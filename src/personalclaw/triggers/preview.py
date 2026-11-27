"""A clock automation's next runs, from the clock's own stepping, before it is saved.

A schedule used to show one next run, so a cadence that landed on a clock change, a skip date or
quiet hours surprised its owner only after it fired. :func:`next_runs` walks the same steps the
clock arms with: the cadence (`arm.cadence_next_fire`, and for a cron `cron_clock.upcoming`, the
stepper `arm` itself calls), skip dates (`arm._skipped_dates`, read on the automation's own
calendar), the spread (`arm.apply_jitter`) and quiet hours (`calendar.evaluate_quiet`). A second
calculation would drift from what fires, so there is none.

Pure: it reads no store and no clock, so the route, the agent's answer and a test all get the same
walk for the same ``now``. A skipped slot is an item with ``runs`` false and the reason in words,
never a hole in the list.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any, Literal

from croniter import CroniterBadDateError  # type: ignore[import-untyped]

NoteKind = Literal["", "clock_forward", "clock_back", "skip_date", "quiet_hours", "spread"]
Outcome = Literal["ok", "never", "refused"]

#: How many runs the route answers by default, and the most it answers.
DEFAULT_COUNT = 5
MAX_COUNT = 10

ELAPSED = "That time has passed."
ALL_SKIPPED = "Every time it would run in the next 400 is one of its skip dates."


@dataclass(frozen=True)
class PreviewItem:
    """One run: ``at`` the UTC instant, ``wall`` the time in the automation's zone, ``runs`` false
    for a slot that is skipped, and the note that explains it (``note_kind``, ``note``)."""

    at: str
    wall: str
    runs: bool
    note_kind: NoteKind = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "wall": self.wall,
            "runs": self.runs,
            "note_kind": self.note_kind,
            "note": self.note,
        }


@dataclass(frozen=True)
class Preview:
    """The next runs: ``outcome`` ``ok``, ``never`` (it runs no more, and ``problem`` says why) or
    ``refused`` (the save would refuse it, in the save's words). ``zone`` the zone its times are
    in, ``cadence`` the schedule in words."""

    outcome: Outcome
    problem: str = ""
    zone: str = ""
    cadence: str = ""
    items: tuple[PreviewItem, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "problem": self.problem,
            "zone": self.zone,
            "cadence": self.cadence,
            "items": [i.to_dict() for i in self.items],
        }


def next_runs(trigger: Any, *, now: float, count: int = DEFAULT_COUNT) -> Preview:
    """The next *count* runs of the clock automation *trigger* after *now*, with every skipped slot
    between them. See the module docstring."""
    from personalclaw.timezones import UnknownTimeZone, resolve_zone, resolve_zone_name
    from personalclaw.triggers.tools import spec_error_refusal

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = str(spec.get("kind") or "").strip().lower()
    expr = str(spec.get("expr") or "").strip()
    # A valid cron that matches no time on any calendar never runs, said in plain words: the save
    # refuses one now, and one saved before it did previews the same answer.
    never = _cron_never_runs(expr) if kind == "cron" else ""
    if never:
        return Preview("never", problem=never, zone=str(spec.get("timezone") or ""))
    refusal = spec_error_refusal("clock", spec)
    if refusal is not None:
        return Preview("refused", problem=_sentence(refusal.text))
    try:
        zone_name, _source = resolve_zone_name(str(spec.get("timezone") or ""))
        zone = resolve_zone(str(spec.get("timezone") or ""))
    except UnknownTimeZone as exc:
        return Preview("refused", problem=_sentence(str(exc)))

    from personalclaw.triggers.schedule_view import describe_cadence

    # The cadence as if it were on: one that is off answers no next fire, and the form previews
    # what it will do once it runs.
    on = copy.copy(trigger)
    on.enabled = True
    cadence = describe_cadence(on, now=now)
    if kind == "adaptive":
        return Preview("ok", zone=zone_name, cadence=cadence)
    try:
        items = _walk(on, spec, kind, zone, now=now, count=max(1, min(int(count), MAX_COUNT)))
    except CroniterBadDateError:
        # The stepper found no fire within its reach: a schedule no calendar reaches.
        from personalclaw.cron_clock import NO_DATE

        return Preview("never", problem=NO_DATE, zone=zone_name, cadence=cadence)
    if not any(i.runs for i in items):
        return Preview(
            "never", problem=ALL_SKIPPED if items else ELAPSED, zone=zone_name, cadence=cadence
        )
    return Preview("ok", zone=zone_name, cadence=cadence, items=tuple(items))


def _cron_never_runs(expr: str) -> str:
    """Why the cron *expr* never runs, or '' (`cron_clock.never_runs`); '' for one ``croniter``
    refuses outright, which the save's refusal says instead."""
    from croniter import croniter  # type: ignore[import-untyped]

    from personalclaw.cron_clock import never_runs

    return never_runs(expr) if expr and croniter.is_valid(expr) else ""


def _walk(
    trigger: Any, spec: dict[str, Any], kind: str, zone: tzinfo, *, now: float, count: int
) -> list[PreviewItem]:
    """The cadence's slots after *now*, each a run or a skipped slot, until *count* runs or
    `arm.MAX_SKIP_ADVANCE` slots. At most *count* skipped slots are listed, the first ones, so a
    night of quiet hours over an every-minute schedule reads as a few struck rows and its runs,
    not hundreds of rows; the walk goes on past them to the runs."""
    from personalclaw.cron_clock import upcoming
    from personalclaw.triggers import arm

    skips = arm._skipped_dates(trigger)
    expr = str(spec.get("expr") or "").strip()
    spread = _spread_note(trigger, spec)
    items: list[PreviewItem] = []
    after, last = now, 0.0
    for _ in range(arm.MAX_SKIP_ADVANCE):
        placed = "exact"
        if kind == "cron":
            step = upcoming(expr, after, zone, 1)
            slot = step[0].at if step else 0.0
            wall = step[0].wall if step else None
            placed = step[0].placed if step else "exact"
        else:
            # `last_fire` keeps an interval on the grid its first slot set, as `arm.next_fire` does
            # when it steps past a skipped one.
            slot = arm.cadence_next_fire(trigger, now=after, last_fire=last)
            wall = None
        if slot <= 0 or slot <= after:
            break
        item = _item(trigger, slot, wall, placed, zone, skips, spread)
        if item.runs or sum(1 for i in items if not i.runs) < count:
            items.append(item)
        if sum(1 for i in items if i.runs) >= count:
            break
        after, last = slot, slot
    return items


def _item(
    trigger: Any,
    slot: float,
    wall: datetime | None,
    placed: str,
    zone: tzinfo,
    skips: set[str],
    spread: str,
) -> PreviewItem:
    from personalclaw.instants import utc_iso
    from personalclaw.triggers import arm
    from personalclaw.triggers.calendar import evaluate_quiet
    from personalclaw.triggers.standing import when_said

    local = datetime.fromtimestamp(slot, tz=zone)
    if local.strftime("%Y-%m-%d") in skips:
        return PreviewItem(
            at=utc_iso(slot),
            wall=when_said(slot, zone),
            runs=False,
            note_kind="skip_date",
            note=f"Skipped: {local.day} {local:%b %Y} is one of its skip dates.",
        )
    fire = arm.apply_jitter(trigger, slot)
    gates = trigger.gates if isinstance(getattr(trigger, "gates", None), dict) else {}
    # The moment the quiet hours are read at, as the fire path reads it (`TriggerService`): this
    # machine's local time.
    quiet, _issues = evaluate_quiet(gates, datetime.fromtimestamp(fire))
    if not quiet.allowed and quiet.window is not None:
        hours = f"{quiet.window.start}–{quiet.window.end}"
        if quiet.catch_up_at:
            return PreviewItem(
                at=utc_iso(quiet.catch_up_at),
                wall=when_said(quiet.catch_up_at, zone),
                runs=True,
                note_kind="quiet_hours",
                note=(
                    f"In its quiet hours ({hours}), so it runs when they end, "
                    f"at {quiet.window.end}."
                ),
            )
        return PreviewItem(
            at=utc_iso(fire),
            wall=when_said(fire, zone),
            runs=False,
            note_kind="quiet_hours",
            note=f"Skipped: {datetime.fromtimestamp(fire):%H:%M} is in its quiet hours ({hours}).",
        )
    kind, note = _clock_note(wall, placed, local)
    if not kind and spread:
        kind, note = "spread", spread
    return PreviewItem(
        at=utc_iso(fire), wall=when_said(fire, zone), runs=True, note_kind=kind, note=note
    )


def _clock_note(wall: datetime | None, placed: str, local: datetime) -> tuple[NoteKind, str]:
    """The words for a cron fire on a night the clocks change: one they jump past, or one they show
    twice."""
    if wall is None or placed == "exact":
        return "", ""
    day = f"{wall:%a} {wall.day} {wall:%b %Y}"
    if placed == "jumped":
        return (
            "clock_forward",
            f"{wall:%H:%M} does not happen on {day} (the clocks go forward), so it runs at "
            f"{local:%H:%M}.",
        )
    return (
        "clock_back",
        f"{wall:%H:%M} happens twice on {day} (the clocks go back); it runs once, at the first.",
    )


def _spread_note(trigger: Any, spec: dict[str, Any]) -> str:
    """A draft's spread, said once per run: the offset is picked from the automation's id, which a
    draft does not have yet. An automation with an id is shown at its own offset instead."""
    if getattr(trigger, "id", "") or bool(spec.get("strict")):
        return ""
    try:
        secs = float(spec.get("jitter_secs") or 0)
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    minutes = round(secs / 60)
    window = (
        f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes >= 1 else f"{int(secs)} seconds"
    )
    return (
        f"It runs up to {window} after this, at a time picked for this automation when it is saved."
    )


def _sentence(text: str) -> str:
    """A refusal as the save says it, without the tools' ``Error:`` lead."""
    text = str(text or "").strip()
    return text[len("Error:") :].strip() if text.startswith("Error:") else text
