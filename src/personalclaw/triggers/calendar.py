"""Calendar-aware scheduling: quiet windows + the duty gate (AUTOMATION-SUBSTRATE AUTO-A1/A2 — S70).

`gates.quiet_hours` has been a RESERVED key with no semantics since S62 — declared in `GATE_KEYS`,
accepted by validation, and consulted by nothing. This module gives it meaning, and adds the
duty-gate seam beside it.

**Measured before writing.** `providers/entity_routes._in_quiet_window` already exists and gets the
hard part right (a window may wrap midnight; a zero-length window never matches). But it is
notification-scoped and cannot express what AUTO-A1 requires: no day-of-week, one window per call,
server-local minutes with no timezone, and a bare bool with no catch-up-or-skip resolution. So the
wrap SEMANTICS are preserved deliberately — two different answers to "is 23:00 inside
22:00→08:00" on one machine would be a bug nobody could explain — while the shape grows the three
missing dimensions.

The rules, each with the failure it prevents:

* **A quiet window SUPPRESSES, it does not cancel.** Whether the missed fire runs afterwards is the
  trigger's `quiet_resolution`: `skip` drops it, `catch_up` runs it once when the window closes. A
  single hard-coded choice is wrong for half of all automations — a nightly backup wants catch-up, a
  "post to the team channel" wants skip, and guessing produces either a 3am Slack message or a
  backup
  that silently never ran.
* **The duty gate FAILS OPEN, with a time-box.** It is the one gate here that calls out to a
provider
  (a calendar app), and §1.4's classification is explicit: a broken calendar app must not silence
  every automation on the machine. A quiet window is local arithmetic and cannot hang, so it stays
  fail-closed in the sense that it always produces an answer.
* **The duty gate is LLM-free by contract.** A gate consulted on every fire cannot cost a model
call;
  that is how a 5-minute trigger becomes a budget incident.

Pure functions plus one provider registry. Nothing here fires or writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable

#: How long a duty-gate provider gets before its verdict is abandoned and the fire proceeds. Short
#: on purpose: this runs on EVERY fire, so a slow gate is a latency tax on the whole automation
#: surface, and the fail-open default means a timeout costs nothing but an unfiltered fire.
DUTY_GATE_TIMEOUT_SECS = 2.0

#: Day-of-week tokens, Monday-first to match `datetime.weekday()`. Named rather than positional so a
#: window reads as `{"days": ["sat", "sun"]}` — a list of integers in a config file is the kind of
#: thing someone gets off by one.
DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_DAY_INDEX = {day: i for i, day in enumerate(DAYS)}

#: Convenience groups, because "weekends" is what a person actually means. Expanded at parse time so
#: the stored window stays explicit and a reader never has to know what the alias meant.
DAY_ALIASES: dict[str, tuple[str, ...]] = {
    "weekdays": ("mon", "tue", "wed", "thu", "fri"),
    "weekends": ("sat", "sun"),
    "daily": DAYS,
    "all": DAYS,
}

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class QuietResolution(str, Enum):
    """What happens to a fire a quiet window suppressed.

    Both members are real user intents and neither is a safe default for the other: a nightly backup
    suppressed at 3am should run when the window closes, and a "good morning" post should not arrive
    at 08:01 having been queued since 22:00.
    """

    SKIP = "skip"
    CATCH_UP = "catch_up"


def parse_hhmm(value: str) -> int | None:
    """`"22:30"` → minutes since midnight, or None when unparseable.

    Returns None rather than raising or defaulting to 0. A malformed window that silently became
    midnight would suppress a band nobody configured — and the caller treats None as "no window",
    which is the only safe reading of an unparseable time.
    """
    match = _HHMM_RE.match((value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


@dataclass
class QuietWindow:
    """One suppression band: a time range plus the days it applies to.

    `days` defaults to every day, because a window with no days would match nothing — a config that
    silently does nothing is worse than one that does the obvious thing.
    """

    start: str
    end: str
    days: tuple[str, ...] = DAYS

    @property
    def valid(self) -> bool:
        start, end = parse_hhmm(self.start), parse_hhmm(self.end)
        return start is not None and end is not None and start != end and bool(self.days)

    @property
    def wraps(self) -> bool:
        """Whether this window crosses midnight (22:00 → 08:00)."""
        start, end = parse_hhmm(self.start), parse_hhmm(self.end)
        if start is None or end is None:
            return False
        return start > end

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "days": list(self.days)}


def parse_windows(raw: Any) -> tuple[list[QuietWindow], list[str]]:
    """Read `gates.quiet_hours` into windows. Returns `(windows, issues)`. Never raises.

    Accepts three shapes, because the reserved key has no established form and all three are things
    a
    person plausibly writes:

    * `{"start": "22:00", "end": "08:00"}` — one window, the common case.
    * `[{...}, {...}]` — several windows (a lunch break plus overnight).
    * `{"windows": [...], "resolution": "catch_up"}` — the full form.

    An invalid window is DROPPED with an issue rather than silently ignored or promoted to
    "suppress everything": a malformed quiet band that accidentally matched all day would look
    exactly like a broken scheduler.
    """
    issues: list[str] = []
    if not raw:
        return [], issues

    candidates: list[Any]
    if isinstance(raw, dict):
        if isinstance(raw.get("windows"), (list, tuple)):
            candidates = list(raw["windows"])
        elif not raw.get("start") and not raw.get("end"):
            # A resolution-only block (`{"resolution": "catch_up"}`) declares no window at all.
            # Treating it as one malformed window reported a spurious `invalid_quiet_window` on top
            # of the real `catch_up_without_quiet_hours` finding — two complaints for one mistake,
            # and the wrong one first. Measured while probing the doctor.
            return [], issues
        else:
            candidates = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        return [], [f"quiet_hours must be an object or a list, got {type(raw).__name__}"]

    windows: list[QuietWindow] = []
    for index, entry in enumerate(candidates):
        if not isinstance(entry, dict):
            issues.append(f"quiet window {index} must be an object")
            continue
        days, day_issues = _parse_days(entry.get("days"))
        issues.extend(f"quiet window {index}: {issue}" for issue in day_issues)
        window = QuietWindow(
            start=str(entry.get("start", "") or ""),
            end=str(entry.get("end", "") or ""),
            days=days,
        )
        if not window.valid:
            issues.append(
                f"quiet window {index} is invalid (start={window.start!r} end={window.end!r}); "
                "expected HH:MM values that differ — dropped rather than applied"
            )
            continue
        windows.append(window)
    return windows, issues


def _parse_days(raw: Any) -> tuple[tuple[str, ...], list[str]]:
    """Day tokens, with aliases expanded. Unknown tokens are dropped with an issue."""
    if raw is None:
        return DAYS, []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return DAYS, [f"days must be a list, got {type(raw).__name__}; treated as every day"]
    out: list[str] = []
    issues: list[str] = []
    for token in raw:
        key = str(token).strip().lower()[:9]
        if key in DAY_ALIASES:
            out.extend(DAY_ALIASES[key])
        elif key[:3] in _DAY_INDEX:
            out.append(key[:3])
        else:
            issues.append(f"unknown day {token!r}")
    if not out:
        # Every day, not no days: a window that matches nothing is a config that silently does
        # nothing, which is the harder failure to notice.
        return DAYS, issues + ["no valid days; treated as every day"]
    return tuple(sorted(set(out), key=lambda d: _DAY_INDEX[d])), issues


def resolution_of(raw: Any) -> str:
    """The `quiet_resolution` for a gates block, defaulting to `skip`.

    `skip` is the default because it is the reversible one: a skipped fire is one missing run the
    user can trigger by hand, while an unwanted catch-up is an action already taken. When the
    plan says "per-trigger resolution", the safe end of that choice is the one that does less.
    """
    if isinstance(raw, dict):
        value = str(raw.get("resolution", "") or "").strip().lower()
        if value in {r.value for r in QuietResolution}:
            return value
    return QuietResolution.SKIP.value


def in_quiet_window(windows: list[QuietWindow], moment: datetime) -> QuietWindow | None:
    """The window suppressing `moment`, or None.

    Midnight-wrap semantics are DELIBERATELY identical to
    `providers/entity_routes._in_quiet_window`: `[start, end)`, and a wrapping window matches
    `now >= start or now < end`. Two different answers to "is 23:00 inside 22:00→08:00" on one
    machine
    would be a bug nobody could explain.

    The day check applies to the day the window STARTED on, not the day `moment` falls in. For a
    Friday-night 22:00→08:00 window, 02:00 Saturday is still inside the Friday band — reading the
    Saturday date instead would end the suppression at midnight, which is not what "Friday night"
    means to anyone.
    """
    minutes = moment.hour * 60 + moment.minute
    today = DAYS[moment.weekday()]
    yesterday = DAYS[(moment.weekday() - 1) % 7]
    for window in windows:
        start, end = parse_hhmm(window.start), parse_hhmm(window.end)
        if start is None or end is None or start == end:
            continue
        if start < end:
            if today in window.days and start <= minutes < end:
                return window
        else:
            # Wrapping: the evening half belongs to today, the morning half to yesterday's band.
            if today in window.days and minutes >= start:
                return window
            if yesterday in window.days and minutes < end:
                return window
    return None


def window_closes_at(window: QuietWindow, moment: datetime) -> datetime:
    """When `window` stops suppressing, at or after `moment`.

    What a `catch_up` fire is scheduled for. Computed from the window rather than "now + an hour",
    because a catch-up that lands back inside the same window is a fire that never happens — the
    single most likely bug in this function, and the reason it returns a concrete instant.
    """
    end = parse_hhmm(window.end)
    if end is None:
        return moment
    close = moment.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if close <= moment:
        close += timedelta(days=1)
    return close


class GateOutcome(str, Enum):
    """Why a calendar gate allowed or suppressed a fire."""

    ALLOWED = "allowed"
    QUIET = "quiet"
    OFF_DUTY = "off_duty"
    #: A date the job's own `skip_dates` excludes (§AUTO-A3's "struck columns"). A distinct outcome
    #: from QUIET because they are different promises: a quiet window suppresses a TIME OF DAY and
    #: may catch up, while a skip date removes a WHOLE DAY and never does. Collapsing them would
    #: render a struck column as a shaded band, which reads as "delayed" rather than "cancelled".
    SKIPPED = "skipped"


@dataclass
class CalendarDecision:
    """The calendar gates' verdict for one fire.

    `catch_up_at` is set only for a suppressed fire whose resolution is `catch_up`, so a caller that
    ignores it drops the fire — the same direction as `skip`, which is the safe one.
    """

    outcome: str
    reason: str = ""
    catch_up_at: float = 0.0
    window: QuietWindow | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == GateOutcome.ALLOWED.value

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"outcome": self.outcome, "reason": self.reason}
        if self.catch_up_at:
            out["catch_up_at"] = self.catch_up_at
        if self.window is not None:
            out["window"] = self.window.to_dict()
        return out


def evaluate_quiet(
    gates: dict[str, Any] | None, moment: datetime
) -> tuple[CalendarDecision, list[str]]:
    """The quiet-hours gate. Returns `(decision, parse_issues)`. Pure, and cannot hang.

    A suppression records `skipped_gate` at the caller with THIS reason — S62's `require_reason`
    rule
    means "skipped_gate" alone is not enough, because it does not say which gate or which window.
    """
    windows, issues = parse_windows((gates or {}).get("quiet_hours"))
    if not windows:
        return CalendarDecision(outcome=GateOutcome.ALLOWED.value), issues
    window = in_quiet_window(windows, moment)
    if window is None:
        return CalendarDecision(outcome=GateOutcome.ALLOWED.value), issues
    resolution = resolution_of((gates or {}).get("quiet_hours"))
    catch_up_at = 0.0
    if resolution == QuietResolution.CATCH_UP.value:
        catch_up_at = window_closes_at(window, moment).timestamp()
    return (
        CalendarDecision(
            outcome=GateOutcome.QUIET.value,
            reason=(
                f"quiet hours {window.start}–{window.end} "
                f"({'every day' if len(window.days) == 7 else ', '.join(window.days)}); "
                + (
                    "the fire will run once the window closes"
                    if catch_up_at
                    else "the fire is dropped (resolution: skip)"
                )
            ),
            catch_up_at=catch_up_at,
            window=window,
        ),
        issues,
    )


# ── the duty-gate provider seam (AUTO-A2) ──


@dataclass
class DutyVerdict:
    """A duty-gate provider's answer.

    `reason` is mandatory in spirit: an off-duty verdict with no reason tells the user their
    automation was suppressed and nothing about why, which is indistinguishable from a bug.
    """

    on_duty: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"on_duty": self.on_duty, "reason": self.reason}


#: The registry. A flat dict, matching the `action_providers` shape rather than inventing a second
#: registry idiom — the plan is explicit that this seam is `action_providers`-shaped.
_DUTY_GATES: dict[str, Callable[[datetime, dict[str, Any]], Awaitable[DutyVerdict]]] = {}


def register_duty_gate(
    name: str, fn: Callable[[datetime, dict[str, Any]], Awaitable[DutyVerdict]]
) -> None:
    """Register a duty-gate provider. Last registration wins, as with action providers."""
    _DUTY_GATES[name] = fn


def duty_gate_names() -> list[str]:
    return sorted(_DUTY_GATES)


def clear_duty_gates() -> None:
    """Test hook. The registry is process-global, so a test that registers must restore."""
    _DUTY_GATES.clear()


async def _manual_gate(now: datetime, config: dict[str, Any]) -> DutyVerdict:
    """The built-in `manual` gate: a user on/off-duty toggle.

    Reads `on_duty` from its own config, defaulting to TRUE. Default-on because this gate ships
    enabled-by-name in core: defaulting to off-duty would silence every automation of anyone who
    named the gate without setting the flag.
    """
    on_duty = bool(config.get("on_duty", True))
    return DutyVerdict(
        on_duty=on_duty,
        reason="" if on_duty else "the manual duty toggle is set to off-duty",
    )


register_duty_gate("manual", _manual_gate)


async def evaluate_duty(
    gates: dict[str, Any] | None, moment: datetime, *, timeout: float = DUTY_GATE_TIMEOUT_SECS
) -> CalendarDecision:
    """The duty gate. FAILS OPEN, time-boxed.

    Every failure path returns ALLOWED, and each one is deliberate:

    * **No gate configured** — nothing to consult.
    * **Unknown provider name** — an app that supplied the gate may be disabled or uninstalled. §1.4
      classifies this gate fail-open, and refusing here would mean uninstalling a calendar app
      silently stops every automation that referenced it.
    * **The provider raised** — a broken third-party gate must not become a global kill switch.
    * **The provider timed out** — this runs on EVERY fire, so a hanging gate would otherwise stall
      the whole automation surface.

    Only an explicit `on_duty=False` suppresses. That asymmetry is the whole point of a fail-open
    classification: the gate can refine WHEN automations run, but it cannot become the reason they
    all stopped.
    """
    import asyncio

    spec = (gates or {}).get("duty_gate")
    if not isinstance(spec, dict):
        return CalendarDecision(outcome=GateOutcome.ALLOWED.value)
    name = str(spec.get("provider", "") or "").strip()
    if not name:
        return CalendarDecision(outcome=GateOutcome.ALLOWED.value)
    gate = _DUTY_GATES.get(name)
    if gate is None:
        return CalendarDecision(
            outcome=GateOutcome.ALLOWED.value,
            reason=f"duty gate {name!r} is not registered; the fire proceeds "
            "(this gate fails open)",
        )
    raw_config = spec.get("config")
    config: dict[str, Any] = dict(raw_config) if isinstance(raw_config, dict) else {}
    try:
        verdict = await asyncio.wait_for(gate(moment, config), timeout=max(0.1, timeout))
    except asyncio.TimeoutError:
        return CalendarDecision(
            outcome=GateOutcome.ALLOWED.value,
            reason=f"duty gate {name!r} did not answer within {timeout:g}s; the fire proceeds",
        )
    except Exception as exc:  # noqa: BLE001 - a broken gate must not become a kill switch
        return CalendarDecision(
            outcome=GateOutcome.ALLOWED.value,
            reason=f"duty gate {name!r} failed ({type(exc).__name__}); the fire proceeds",
        )
    if verdict.on_duty:
        return CalendarDecision(outcome=GateOutcome.ALLOWED.value)
    return CalendarDecision(
        outcome=GateOutcome.OFF_DUTY.value,
        reason=verdict.reason or f"the {name!r} duty gate reports off-duty",
    )


# ── the week grid (AUTO-A1's read-only view) ──


@dataclass
class Occurrence:
    """One projected fire in the week grid.

    Carries WHY a slot is dimmed, so the grid explains itself: a shaded band the user cannot account
    for is worse than no grid, because they will assume the schedule is wrong.
    """

    trigger_id: str
    trigger_name: str
    at: float
    suppressed_by: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "trigger_name": self.trigger_name,
            "at": self.at,
            "suppressed_by": self.suppressed_by,
            "reason": self.reason,
        }


#: Hard ceiling on occurrences the grid computes per trigger per week. A 1-minute trigger generates
#: 10,080 in a week; rendering them is meaningless and computing them makes the endpoint slow. The
#: response reports truncation rather than silently showing a partial week — the S65 rule applied
#: to a different surface.
MAX_OCCURRENCES_PER_TRIGGER = 200


def _resolve_zone(tz_name: str):
    """The zone a projected fire's calendar date is read in.

    Mirrors `schedule._job_tz`'s resolution order — the job's own zone, then the app config's, then
    the server's — because the grid has to strike the same column the scheduler will skip. It is NOT
    a call into that function: importing the scheduler here would pull the whole cron service into a
    pure-decision module, and the ORDER is the contract, not the code.

    An unparseable name falls back to server-local rather than raising. A trigger with a typo'd
    timezone still has real fires, and a grid that 500s on one bad row shows nothing at all.
    """
    from zoneinfo import ZoneInfo

    names = [tz_name]
    try:
        from personalclaw.config.loader import AppConfig

        names.append(AppConfig.load().timezone or "")
    except Exception:
        # Silent by design: this module is pure decisions and carries no logger (adding one for a
        # fallback path would be the first import of logging into it). An unreadable config timezone
        # is not an error — it just means the next candidate applies.
        pass
    for name in names:
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return None  # `.astimezone(None)` is server-local — the documented fallback.


def project_occurrences(
    *,
    trigger_id: str,
    trigger_name: str,
    interval_secs: float,
    first_fire_at: float,
    start: datetime,
    days: int = 7,
    gates: dict[str, Any] | None = None,
    cap: int = MAX_OCCURRENCES_PER_TRIGGER,
    skip_dates: list[str] | None = None,
    tz_name: str = "",
    next_after: Any = None,
) -> tuple[list[Occurrence], bool]:
    """Project one trigger's fires across the window. Returns `(occurrences, truncated)`.

    Quiet windows and skip dates are applied as ANNOTATIONS, not filters: a suppressed slot is still
    returned, marked with why. Dropping them would make the grid show a schedule the user does not
    have, and the whole point of the view is to explain why a trigger is not firing when they expect
    it to.

    **`skip_dates` and `tz_name` were measured as a gap, together.** AUTO-A3 requires skip dates to
    render as struck columns, and this function did not read them at all — driven with a daily
    trigger and tomorrow-plus-one declared a skip date, the projection returned that fire completely
    unannotated while `SchedulerService` would refuse it. Worse, the two halves have to arrive
    together: the scheduler resolves the calendar date through `_job_tz(job)` (the JOB's timezone),
    while this function converted with `.astimezone()` (the SERVER's). For a `Asia/Tokyo` job on a
    UTC host the same instant is a different calendar date, so honouring `skip_dates` against server
    time would have struck the WRONG column — a grid that is confidently wrong, which is worse than
    the one that was merely silent.

    The duty gate is deliberately NOT evaluated here. It is async, provider-backed, and answers
    about
    a moment in time — calling it 200 times for a projected week would be both slow and wrong (a
    calendar's answer for next Thursday is not knowable now).

    **🔴 `next_after` closes the CRON GAP (S103).** This function took only `interval_secs`, so the
    week grid OMITTED every cron trigger — its caller's own comment admitted it ("a cron trigger is
    omitted rather than mis-plotted"). At the time that was right: nothing could iterate a cron's
    fires. S96's `arm.next_fire` can, so a caller now passes a stepper — `next_after(t) -> float` —
    and a cron plots on the same annotated grid as an interval. Omitting them made the week view a
    forecast of only half the user's automations, silently.
    """
    stepping = callable(next_after)
    if not stepping and (interval_secs <= 0 or first_fire_at <= 0):
        return [], False

    from datetime import timezone as _tz

    windows, _issues = parse_windows((gates or {}).get("quiet_hours"))
    # `gates.skip_dates` is accepted as well as the explicit argument: §1.1 reserves the key on the
    # unified Trigger entity, while a legacy `ScheduleJob` carries the list as a top-level field.
    # Accepting only one of the two would have quietly ignored half the triggers.
    skips = {str(d).strip() for d in (skip_dates or (gates or {}).get("skip_dates") or []) if d}
    job_zone = _resolve_zone(tz_name)
    end_ts = (start + timedelta(days=days)).timestamp()
    start_ts = start.timestamp()

    # Advance to the first fire inside the window rather than iterating from `first_fire_at`, which
    # for an old trigger with a 60s interval would be millions of no-op steps before the first slot.
    if stepping:
        # A stepper answers "the next fire strictly after t", so the window entry is one call
        # rather than arithmetic — and it is the ONLY correct way to advance a cron: a cron's
        # spacing is not constant, so adding an interval would drift across months and DST.
        at = float(next_after(start_ts - 1))
        if at <= 0:
            return [], False
    else:
        at = first_fire_at
        if at < start_ts:
            skipped = int((start_ts - at) // interval_secs)
            at += skipped * interval_secs
            if at < start_ts:
                at += interval_secs

    out: list[Occurrence] = []
    while at < end_ts:
        if len(out) >= max(1, cap):
            return out, True
        # Rendered in the JOB's zone when it declares one, so the grid's columns are the same
        # calendar days the scheduler will compare `skip_dates` against.
        moment = datetime.fromtimestamp(at, tz=_tz.utc).astimezone(job_zone)
        window = in_quiet_window(windows, moment) if windows else None
        local_date = moment.strftime("%Y-%m-%d")
        if local_date in skips:
            # SKIP wins over QUIET. Both suppress the fire, and reporting the quiet window on a day
            # that is struck anyway would send the user to fix the wrong setting.
            suppressed, reason = GateOutcome.SKIPPED.value, f"skip date {local_date}"
        elif window:
            suppressed, reason = GateOutcome.QUIET.value, f"quiet hours {window.start}–{window.end}"
        else:
            suppressed, reason = "", ""
        out.append(
            Occurrence(
                trigger_id=trigger_id,
                trigger_name=trigger_name,
                at=at,
                suppressed_by=suppressed,
                reason=reason,
            )
        )
        if stepping:
            nxt = float(next_after(at))
            if nxt <= at:
                # A stepper that does not advance would spin forever. Stopping is the honest
                # response: the grid shows what it could project rather than hanging the request.
                return out, False
            at = nxt
        else:
            at += interval_secs
    return out, False


# ── `automation doctor` (§7 criterion 12) ──

#: A file-watch glob is "broad" when fewer than this many path segments precede the first wildcard.
#: `~/**` and `/**` are the cases that matter: they match every file the user owns, so a watch
#: trigger on one fires constantly and reads as a runaway automation. Measured at 2 first, which
#: flagged `~/projects/**` — a perfectly reasonable scope for someone who keeps all their work in
#: one directory. One segment is the honest line: it catches the roots and leaves any named
#: directory alone.
BROAD_GLOB_SEGMENTS = 1


@dataclass
class Finding:
    """One `automation doctor` finding.

    `fix` is not optional in spirit: a doctor that reports problems without saying what to do is a
    list of complaints, and users learn to ignore it.
    """

    trigger_id: str
    code: str
    detail: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "code": self.code,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass
class DoctorReport:
    """Everything the doctor found, grouped for a single render."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "findings": [f.to_dict() for f in self.findings],
            "count": len(self.findings),
        }


def _is_broad_glob(pattern: str) -> bool:
    """Whether a watch glob is broad enough to fire on nearly everything.

    Measured by the depth at which the first wildcard appears: `~/**` and `/**` match a whole tree,
    while `~/projects/acme/**/*.py` is scoped. Counting segments before the wildcard is what
    distinguishes them — a naive "contains `**`" check would flag every legitimate recursive watch.
    """
    if not pattern:
        return False
    head = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
    segments = [s for s in head.replace("\\", "/").split("/") if s and s not in {"~", "."}]
    has_wildcard = any(c in pattern for c in "*?[")
    return has_wildcard and len(segments) < BROAD_GLOB_SEGMENTS


def diagnose(
    triggers: list[dict[str, Any]],
    *,
    known_workflows: set[str] | frozenset[str] | None = None,
    known_duty_gates: set[str] | frozenset[str] | None = None,
) -> DoctorReport:
    """Structural problems across every trigger. Pure; never raises.

    The two findings §7's criterion 12 names by hand — an orphaned workflow ref and a broad
    file-watch
    glob — plus the ones that fell out of the entity work and are equally silent:

    * a trigger referencing a workflow def that does not exist fires and fails forever,
    * a broad watch glob fires on everything the user owns,
    * a quiet window covering all 168 hours means the trigger can never run,
    * an unparseable quiet window is DROPPED at parse time, so the user believes they are protected,
    * a duty gate naming an unregistered provider fails open — the automation runs unfiltered, which
      is the opposite of what its author asked for,
    * `catch_up` with no quiet window is a setting with nothing to resolve.

    Every one of these is invisible at runtime: the trigger looks configured and behaves differently
    than its author intended, which is precisely what a doctor is for.
    """
    report = DoctorReport()
    workflows = set(known_workflows or ())
    gates_available = set(known_duty_gates) if known_duty_gates is not None else set(_DUTY_GATES)

    for entry in triggers or []:
        tid = str(entry.get("id", "") or "")
        # Annotated two-step rather than a ternary: the conditional form types as
        # `Any | dict | None`, which mypy correctly refuses at every `.get` below. Third time this
        # pattern has come up in this program (S62, S66) — the two-step is the fix.
        raw_gates = entry.get("gates")
        gates: dict[str, Any] = dict(raw_gates) if isinstance(raw_gates, dict) else {}
        raw_workflow = entry.get("workflow")
        workflow: dict[str, Any] = dict(raw_workflow) if isinstance(raw_workflow, dict) else {}

        ref = str(workflow.get("def") or workflow.get("name") or "")
        if ref and known_workflows is not None and ref not in workflows:
            report.findings.append(
                Finding(
                    trigger_id=tid,
                    code="orphaned_workflow_ref",
                    detail=f"references the workflow {ref!r}, which does not exist",
                    fix="point the trigger at an existing workflow, or re-create the missing one",
                )
            )

        raw_spec = entry.get("spec")
        spec: dict[str, Any] = dict(raw_spec) if isinstance(raw_spec, dict) else {}
        for key in ("glob", "pattern", "path"):
            value = str(spec.get(key, "") or "")
            if value and _is_broad_glob(value):
                report.findings.append(
                    Finding(
                        trigger_id=tid,
                        code="broad_watch_glob",
                        detail=f"watches {value!r}, which matches nearly every file",
                        fix="scope the glob to a project directory, e.g. ~/projects/<name>/**",
                    )
                )

        windows, issues = parse_windows(gates.get("quiet_hours"))
        for issue in issues:
            report.findings.append(
                Finding(
                    trigger_id=tid,
                    code="invalid_quiet_window",
                    detail=issue,
                    fix="use HH:MM start/end values; an invalid window is dropped, so the trigger "
                    "is NOT protected by it",
                )
            )
        if windows and _covers_whole_week(windows):
            report.findings.append(
                Finding(
                    trigger_id=tid,
                    code="quiet_hours_cover_everything",
                    detail="the quiet windows cover every hour of every day, so this trigger can "
                    "never fire",
                    fix="narrow the windows, or disable the trigger if that was the intent",
                )
            )
        if (
            not windows
            and resolution_of(gates.get("quiet_hours")) == QuietResolution.CATCH_UP.value
        ):
            report.findings.append(
                Finding(
                    trigger_id=tid,
                    code="catch_up_without_quiet_hours",
                    detail="quiet_resolution is catch_up but no quiet window is configured",
                    fix="add a quiet window, or drop the resolution setting",
                )
            )

        # 🔴 An UNFENCED write-capable action (decision 7 — S116). The fence is wired as of S116 and
        # denies on an empty block, so a trigger authored before that ships carries
        # `capabilities: {}`, requests a write-capable provider, and REFUSES on its next fire. The
        # refusal is in the ledger, but the user's question is "why did my automation stop", and
        # the doctor is where that gets answered. Re-saving the trigger freezes the grant.
        raw_caps = entry.get("capabilities")
        caps: dict[str, Any] = dict(raw_caps) if isinstance(raw_caps, dict) else {}
        wf = entry.get("workflow")
        if isinstance(wf, dict):
            from personalclaw.triggers.screen import provider_is_read_only

            inline = wf.get("inline") if isinstance(wf.get("inline"), dict) else None
            action = str((inline or wf).get("provider") or "").strip()
            granted = caps.get("providers") or []
            if action and not provider_is_read_only(action) and action not in granted:
                report.findings.append(
                    Finding(
                        trigger_id=tid,
                        code="unfenced_write_action",
                        detail=f"runs the write-capable action {action!r} with no capability "
                        f"grant, so the frozen-capability fence refuses it",
                        fix="re-save the automation to freeze its capability set, or switch it "
                        "to a read-only action",
                    )
                )

        duty = gates.get("duty_gate")
        if isinstance(duty, dict):
            name = str(duty.get("provider", "") or "")
            if name and name not in gates_available:
                report.findings.append(
                    Finding(
                        trigger_id=tid,
                        code="unknown_duty_gate",
                        detail=f"names the duty gate {name!r}, which is not registered; the gate "
                        "fails open so this trigger runs UNFILTERED",
                        fix="install the app that provides the gate, or remove the duty_gate block",
                    )
                )
    return report


def _covers_whole_week(windows: list[QuietWindow]) -> bool:
    """Whether the windows leave no minute of the week un-suppressed.

    Checked by sampling every hour of a synthetic week rather than by interval algebra: the wrap +
    day-of-week interaction makes the algebraic version easy to get subtly wrong, and 168 membership
    tests cost nothing. A cheap exact check beats a clever approximate one for a warning that tells
    someone their automation can never run.
    """
    base = datetime(2024, 1, 1)  # a Monday
    for hour in range(24 * 7):
        moment = base + timedelta(hours=hour)
        if in_quiet_window(windows, moment) is None:
            return False
    return True


# ── config defaults (the fifth config point — S61k's lesson) ──


def parse_default_window(value: str) -> QuietWindow | None:
    """`"22:00-08:00"` → a `QuietWindow`, or None when unparseable.

    The config form is a single compact range rather than the full JSON shape, because this is a
    Settings text field and a user typing JSON into one is a worse experience than a narrower
    format.

    An unparseable value reads as NO default — the fail-safe direction. A malformed window that
    accidentally matched all day would suppress every automation and look exactly like a broken
    scheduler, which is the hardest failure in this file to diagnose from the outside.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    for separator in ("-", "–", "to"):
        if separator in raw:
            start, _, end = raw.partition(separator)
            window = QuietWindow(start=start.strip(), end=end.strip())
            return window if window.valid else None
    return None


def _workflows_config() -> object | None:
    """The live `WorkflowsConfig`, or None when it cannot be read.

    Loaded per call rather than cached, matching `workflows/settings.py`: both knobs are in the
    PATCH allowlist, so a cached value would keep applying the old setting until the gateway
    restarted — which defeats the point of making them live-editable.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().workflows
    except Exception:
        return None


def default_quiet_window() -> QuietWindow | None:
    """The configured default quiet window, or None.

    Read HERE rather than at each call site, for the reason S61k documented: a knob wired through
    all four config points is still inert if the module keeps its own constant. Best-effort — a
    malformed `config.json` must not stop an automation from firing.
    """
    config = _workflows_config()
    if config is None:
        return None
    return parse_default_window(str(getattr(config, "default_quiet_windows", "") or ""))


def default_duty_gate() -> str:
    """The configured default duty-gate provider name, or `""`.

    Returns the raw name even when it is not registered: `evaluate_duty` already fails open on an
    unknown name with a reason, and `automation doctor` reports it. Filtering it to `""` here would
    hide a typo the user needs to see.
    """
    config = _workflows_config()
    if config is None:
        return ""
    return str(getattr(config, "duty_gate_default", "") or "").strip()


def apply_defaults(gates: dict[str, Any] | None) -> dict[str, Any]:
    """Fill a gates block's quiet/duty settings from config where the trigger set none.

    A trigger's OWN settings always win, and an explicitly empty value counts as a setting: someone
    who cleared their quiet hours meant to clear them, so re-applying the global default would
    override a deliberate choice with a fallback. Only an ABSENT key is filled.
    """
    out = dict(gates or {})
    if "quiet_hours" not in out:
        window = default_quiet_window()
        if window is not None:
            out["quiet_hours"] = window.to_dict()
    if "duty_gate" not in out:
        name = default_duty_gate()
        if name:
            out["duty_gate"] = {"provider": name, "config": {}}
    return out
