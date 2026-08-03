"""The executor: drain a session inbox, run the action, classify the outcome (§3 / §1.3 — S90).

§3's fire path ends: "… → create run (full or ledger-only) → engine executes under the
`headless` profile
→ **outcome classification (§1.3)** → delivery contract (decision 13) → health rollup +
failure policy."

S86 built the gate order, S87 the store, S88 the tick, S89 the dispatcher. This is the last
link: it takes
what S89 queued onto a session inbox and turns it into a typed outcome plus a delivery.

**Measured before writing — every dependency shipped, and two honesty contracts already fought
for.**

* `ScheduleService._execute` runs an action through an INJECTED `_on_job` callback, not a hard-coded
  provider. So this module takes a runner too: a trigger executor that imported the action registry
  directly would be untestable without a live provider, and the shipped scheduler already proved the
  injection point works.
* **The `_STATUS_PENDING` sentinel is load-bearing.** `_execute` seeds `last_status =
  "_pending"` and only
  defaults to `"ok"` if the sentinel SURVIVED — its comment says why: "so a failed action's
  'error' is no
  longer CLOBBERED by an unconditional 'ok' (the honest-status bug T7 set out to kill: a failed run
  recorded as success)". This module reproduces that three-state logic exactly.
* **`launched` ≠ succeeded.** `engine.dispatch_action`'s docstring: "'launched' means
  background work
  STARTED, not that it succeeded, so it maps to DEGRADED with a reason rather than a clean DONE.
  Reporting it as success would make a fire-and-forget action look verified." S84 preserved the same
  distinction in the history projection (`launched` → `deferred`). This preserves it a third time.
* `dispatch.classify_handler_outcome` and `autopause.outcome_for_exit` already own the
  exception→outcome
  and exit-type→outcome mappings, so neither is re-derived here.

**What this owns, and the boundary.** It drains one session's inbox, runs each payload through the
supplied runner, classifies the result into `FIRE_OUTCOMES`, and builds S85's delivery. It
does NOT own
the LLM turn itself — that is `SubagentManager.spawn` with its `__wf_depth` cap, which the
caller supplies
as the runner. Keeping the turn injected is what lets the whole chain be driven end to end without a
model.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from personalclaw.triggers.models import FIRE_OUTCOMES, Outcome

logger = logging.getLogger(__name__)

#: The sentinel meaning "the runner has not reported a verdict yet", reproducing
#: `schedule._STATUS_PENDING`. Its whole purpose is that a default of `"ok"` must apply ONLY
#: when nothing
#: else was reported — otherwise an unconditional success clobbers a runner's own `"error"`,
#: which is the
#: honest-status bug T7 was written to kill.
STATUS_PENDING = "_pending"

#: A runner's reported status → a typed fire outcome. Data rather than branches, so the mapping is
#: reviewable and a new status cannot silently fall through to success.
#:
#: `launched` → `DEFERRED`, never `RAN`: the action started background work and nobody has seen the
#: result. `engine.dispatch_action` maps the same status to DEGRADED for the same reason, and S84's
#: history projection maps it to `deferred` too. Three surfaces, one meaning.
STATUS_TO_OUTCOME: dict[str, str] = {
    "ok": Outcome.RAN.value,
    "success": Outcome.RAN.value,
    "launched": Outcome.DEFERRED.value,
    "error": Outcome.FAILED.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "refused": Outcome.REFUSED.value,
    "blocked": Outcome.REFUSED.value,
}

#: Max payloads drained from one session in a single pass. A runaway trigger that queued
#: thousands would
#: otherwise hold the executor for minutes; the cap is REPORTED so a partial drain never looks
#: complete.
MAX_DRAIN = 50


@dataclass
class RunOutcome:
    """What running one queued payload produced."""

    trigger_id: str
    session_key: str
    outcome: str
    reason: str = ""
    duration_secs: float = 0.0
    #: The runner's own reported status, kept verbatim alongside the typed outcome. A user
    #: debugging an
    #: automation wants the provider's word for it, not only this module's translation.
    reported: str = ""
    run_id: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == Outcome.RAN.value

    @property
    def settled(self) -> bool:
        """Whether the work is FINISHED. A `deferred` run started something nobody has seen
        yet, so it
        is neither a success nor a failure — and a health rollup that counted it either way would be
        lying in one direction or the other."""
        return self.outcome not in (Outcome.DEFERRED.value,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "session_key": self.session_key,
            "outcome": self.outcome,
            "reason": self.reason,
            "duration_secs": self.duration_secs,
            "reported": self.reported,
            "run_id": self.run_id,
            "ok": self.ok,
            "settled": self.settled,
        }


@dataclass
class DrainResult:
    """One drain pass over a session's inbox."""

    session_key: str
    outcomes: list[RunOutcome] = field(default_factory=list)
    #: True when the cap stopped the drain. Named, never silent: a partial drain that looked
    #: complete
    #: would make a backed-up queue invisible.
    truncated: bool = False
    #: Payloads skipped because they carried no trigger — a malformed queue row, not a fire.
    skipped: int = 0

    @property
    def ran(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == Outcome.FAILED.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "total": len(self.outcomes),
            "ran": self.ran,
            "failed": self.failed,
            "truncated": self.truncated,
            "skipped": self.skipped,
        }


def classify(reported: str, exception: BaseException | None = None) -> tuple[str, str]:
    """A runner's result → `(typed_outcome, reason)`.

    The three-state logic `schedule._execute` established, reproduced deliberately:

    1. An EXCEPTION wins. `dispatch.classify_handler_outcome` owns the exception→outcome
    mapping, so a
       transport error and a genuine failure stay distinguishable (which is what lets
       `autopause` count
       only TRUE failures toward its threshold).
    2. A reported status is honoured verbatim through `STATUS_TO_OUTCOME`.
    3. Only the SURVIVING sentinel defaults to success. This is the T7 rule: an unconditional `"ok"`
       would clobber a runner's own `"error"` and record a failed run as a success.

    An unrecognized status becomes `failed`, not `ran`: a status this build cannot classify
    must not be
    counted as a success, because a success is what a health rollup treats as nothing to look at.
    """
    if exception is not None:
        from personalclaw.triggers.dispatch import classify_handler_outcome

        classified = classify_handler_outcome(exception, reported or "")
        outcome = classified if classified in FIRE_OUTCOMES else Outcome.FAILED.value
        return outcome, f"{type(exception).__name__}: {exception}"[:200]

    status = (reported or "").strip().lower()
    if not status or status == STATUS_PENDING:
        # The sentinel survived: nothing reported a verdict, so the run completed without complaint.
        return Outcome.RAN.value, ""
    if status in STATUS_TO_OUTCOME:
        outcome = STATUS_TO_OUTCOME[status]
        reason = "" if outcome == Outcome.RAN.value else f"runner reported {status}"
        if outcome == Outcome.DEFERRED.value:
            reason = "action launched background work; outcome not yet known"
        return outcome, reason
    return Outcome.FAILED.value, f"unrecognized runner status {status!r}"


def _payload_of(row: Any) -> dict[str, Any]:
    """The wakeup payload out of one queue row.

    S89 queues `(msg_ts, text, kwargs)` with the structured payload under `kwargs['wakeup']` —
    that is
    how it survives without widening the queue's tuple shape. Read defensively: a row queued by some
    other emitter (a chat nudge) has no wakeup, and this must skip it rather than raise.
    """
    if not isinstance(row, tuple) or len(row) < 3:
        return {}
    kwargs = row[2] if isinstance(row[2], dict) else {}
    wakeup = kwargs.get("wakeup")
    return wakeup if isinstance(wakeup, dict) else {}


async def run_one(
    payload: dict[str, Any],
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    session_key: str = "",
    now: float = 0.0,
) -> RunOutcome:
    """Run one queued payload through `runner`, classify, and time it. NEVER raises.

    `runner` is injected, exactly as `ScheduleService` injects `_on_job`: a trigger executor
    that imported
    the action registry directly would be untestable without a live provider, and the shipped
    scheduler
    already proved this seam works. The caller supplies `SubagentManager.spawn` (with its
    `__wf_depth`
    cap) or the action registry.

    A runner may report its verdict two ways, and both are honoured: by RETURNING a dict with
    a `status`
    (or a `.last_status` attribute, matching the shipped `ScheduleJob` shape) or by RAISING.
    """
    started = now or time.time()
    trigger_id = str(payload.get("trigger_id") or "")
    reported = ""
    exception: BaseException | None = None
    run_id = ""
    try:
        result = await runner(payload)
        if isinstance(result, dict):
            reported = str(result.get("status") or "")
            run_id = str(result.get("run_id") or "")
        else:
            reported = str(getattr(result, "last_status", "") or "")
            run_id = str(getattr(result, "run_id", "") or "")
    except (
        Exception
    ) as exc:  # noqa: BLE001 - the outcome IS the error; re-raising would lose the row
        exception = exc

    outcome, reason = classify(reported, exception)
    return RunOutcome(
        trigger_id=trigger_id,
        session_key=session_key or str(payload.get("session_key") or ""),
        outcome=outcome,
        reason=reason,
        duration_secs=round(max(0.0, time.time() - started), 3),
        reported=reported,
        run_id=run_id,
    )


async def drain(
    sessions: Any,
    session_key: str,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    limit: int = MAX_DRAIN,
    now: float = 0.0,
) -> DrainResult:
    """Drain one session's inbox, running each payload. Returns every outcome.

    Uses the shipped `SessionManager.dequeue`, which already skips CANCELLED rows —
    reimplementing the
    drain would lose that, and a cancelled fire running anyway is the worst kind of surprise.

    The cap is reported rather than silent (`truncated`), because a partial drain that looked
    complete
    would make a backed-up queue invisible — the S65 rule this program keeps re-learning on
    new surfaces.
    """
    result = DrainResult(session_key=session_key)
    if sessions is None:
        return result

    for _ in range(max(1, limit)):
        try:
            row = sessions.dequeue(session_key)
        except (
            Exception
        ):  # noqa: BLE001 - a broken queue must not lose the outcomes already collected
            logger.debug("dequeue failed for %s", session_key, exc_info=True)
            break
        if row is None:
            return result
        payload = _payload_of(row)
        if not payload.get("trigger_id"):
            # Not a trigger fire (a chat nudge, a malformed row). Counted, not run: executing an
            # unrecognized payload as if it were a fire is how one subsystem's message becomes
            # another's action.
            result.skipped += 1
            continue
        result.outcomes.append(
            await run_one(
                payload.get("payload") or payload, runner, session_key=session_key, now=now
            )
        )

    # The loop ended without hitting `row is None`, so there may be more.
    try:
        result.truncated = sessions.dequeue(session_key) is not None
    except Exception:  # noqa: BLE001
        result.truncated = False
    return result


def delivery_for(outcome: RunOutcome, *, trigger_name: str = "", destination: str = "") -> Any:
    """S85's completion notification for one settled run, or None while it is deferred.

    Returns None for `DEFERRED` deliberately: the action launched background work and nobody
    has seen the
    result, so a "finished" notification would be the fire-and-forget lie `dispatch_action`'s
    docstring
    warns about. The delivery goes out when the background turn reports.
    """
    if not outcome.settled:
        return None
    from personalclaw.triggers.delivery import build_delivery

    return build_delivery(
        trigger_id=outcome.trigger_id,
        trigger_name=trigger_name,
        ok=outcome.ok,
        summary=outcome.reason,
        run_id=outcome.run_id,
        destination=destination,
        duration_secs=outcome.duration_secs,
    )


def ledger_rows(result: DrainResult) -> list[dict[str, Any]]:
    """One typed row per executed payload — §7 crit 8's "zero silent drops", at the execution end.

    S86's fire path writes a row for every fire it evaluated; this writes one for every fire that
    actually ran. Both halves are needed: a fire that passed every gate and then died in the
    executor
    would otherwise leave a `ran` row from the gate walk and nothing else.
    """
    return [
        {
            "trigger_id": o.trigger_id,
            "outcome": o.outcome,
            "reason": o.reason,
            "duration_secs": o.duration_secs,
            "reported": o.reported,
            "run_id": o.run_id,
            "phase": "execute",
        }
        for o in result.outcomes
    ]


def health_delta(result: DrainResult) -> dict[str, Any]:
    """What this drain does to a trigger's health rollup (§3.7).

    `deferred` counts toward NEITHER success nor failure. A rollup that counted a
    launched-but-unverified
    run as a success would mark a broken automation healthy; counting it as a failure would
    autopause one
    that is working. Excluding it is the only honest option, and `consecutive_failures` is what
    `autopause` thresholds on.
    """
    settled = [o for o in result.outcomes if o.settled]
    failures = [o for o in settled if o.outcome == Outcome.FAILED.value]
    return {
        "settled": len(settled),
        "succeeded": sum(1 for o in settled if o.ok),
        "failed": len(failures),
        "deferred": len(result.outcomes) - len(settled),
        # Only TRUE failures advance the autopause counter — `classify` keeps a transport error
        # distinguishable precisely so this count stays honest (S68's finding).
        "consecutive_failures": len(failures),
    }
