"""A trigger's run, recorded once: the row its history shows, and what the trigger keeps of it.

Every run of a trigger's action is recorded by :func:`record_run`, whatever started it: the clock,
an event, a watched file or page, the end of other work, a quiet session, or a run by hand (Run
now, the restart review's Run now, a webhook, a view's refresh). One recorder, so the row and the
trigger cannot tell two stories about one run:

* **The row** (`ScheduleRun`): when the action started and when it returned, how long that took,
  what the run recorded (`schedule_history.status_for_result`), the line a person reads, the
  trace, and the error. A scheduled fire's row used to be written at record time as
  ``started_at = finished_at``, so every fire read 0 ms, a seven-minute digest included, and its
  start read as late as its end, while a Run now of the same trigger, which had a recorder of its
  own, recorded its real duration.
* **The trigger's stamps**, moved in the same write (:func:`stamp_run`): ``last_run_id``, the row
  its last result opens, and the outcome's own stamp: ``last_success_at``, ``last_failure_at``
  with why in ``last_error_summary``, or ``last_waiting_at`` for a run that stopped for you. Only a
  Run now ever set ``last_run_id``, so a trigger that ran on its own offered no last result to
  open.
* **A fire's lifecycle** (`triggers.autopause`): its health, its state and its park cooldown. A run
  by hand never drives these: testing a broken automation by hand must neither pause it nor clear
  a real failure streak.

A run whose action only started its work — an agent (Invoke Agent, Run prompt) or a workflow run —
has not ended when its action returns: its row reads `launched` (or `queued`), and decides nothing
about how the automation is going. When that work ends, its row says how
(`triggers.settle`), and :func:`record_ending` takes that ending onto the trigger as this recorder
takes any other: its stamps, and for a fire the lifecycle decision, so a failed agent counts toward
the pause exactly as a failed command does. Both go through one decision
(`autopause.ending_decision`), read from the history the run's row is in.

A run whose action never returned, because a stop or a restart cut it off or it ran past its
deadline, has nothing to hand this recorder: `triggers.reaper` closes it, and moves the same stamps
(:func:`stamp_run`), so its row is the trigger's last run too.

The fire METERS, ``run_count`` (what ``max_fires`` spends) and ``last_fired_at`` (what spacing
measures from), move where a fire is DECIDED, before its action runs (:func:`count_fire`):
`service.admit_fire` for the clock and events, and :func:`note_fire` for the fires no admission
walks, which the watched-file and watched-page polls, the chain and the quiet-session poll decide.
Counting at the end instead would let fires in flight all pass a budget of one. A fire nothing
counted used to leave its trigger at ``run_count: 0``, so its panel read "never run" beside the
runs its history listed. A run by hand spends neither.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from personalclaw.triggers.models import Outcome

if TYPE_CHECKING:
    from personalclaw.schedule_history import ScheduleRun

logger = logging.getLogger(__name__)

#: How much of a run's error its row and its trigger keep. Sized to fit a rendered WHAT/WHY/FIX
#: envelope (about 250 characters), so its FIX line, the remedy, survives into the row and into
#: ``last_error_summary`` instead of being cut mid-word.
ERROR_MAX = 512

#: The statuses a run records as having gone wrong (`triggers.history.SCHEDULE_STATUS_TO_OUTCOME`):
#: they stamp ``last_failure_at`` and keep why in ``last_error_summary``. A run a stop or a restart
#: cut off is one: it is not a failure, and it did not do what it was for, so the trigger's last run
#: is dated by it and says what stopped it.
_WENT_WRONG = frozenset({Outcome.FAILED.value, Outcome.REFUSED.value, Outcome.INTERRUPTED.value})

#: How many of a trigger's newest runs its failure count is read from: the most a declared
#: `failure_policy.autopause_after` can count.
STREAK_WINDOW = 20


def count_fire(trigger: Any, *, at: float) -> None:
    """Count one fire of *trigger* that goes ahead at *at*: its ``run_count`` and ``last_fired_at``.

    The one writer of both, called where a fire is decided and before its action runs: by
    `service.admit_fire` on the trigger it is about to persist, and through :func:`note_fire` by
    the decisions no admission walks.
    """
    from personalclaw.triggers.service import to_iso

    trigger.run_count = int(getattr(trigger, "run_count", 0) or 0) + 1
    trigger.last_fired_at = to_iso(at)


def note_fire(store: Any, trigger_id: str, *, at: float) -> None:
    """Count a fire that a poll, the chain or a quiet session decided, on the stored row.

    The row is read again just before it is written, so an edit saved since the caller loaded it
    is not written over. Never raises: the fire goes ahead whether or not its count lands.
    """
    try:
        row = store.get(trigger_id)
        if row is None:
            return
        live = row.trigger
        count_fire(live, at=at)
        store.upsert(live)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not count the fire of %s", trigger_id, exc_info=True)


def stamp_run(
    trigger: Any, *, status: str, why: str = "", run_id: str = "", at: float = 0.0
) -> None:
    """Move *trigger*'s stamps for a run its history recorded as *status*, finished at *at*.

    A run that stopped for you stamps ``last_waiting_at``: it did nothing it was asked yet. One that
    went wrong (`Outcome.FAILED`, `Outcome.REFUSED`, `Outcome.INTERRUPTED`) stamps
    ``last_failure_at`` and keeps *why* as ``last_error_summary``. Every other run stamps
    ``last_success_at``, and a degraded one keeps *why*, what it ran without, as
    ``last_error_summary``, so the trigger's row says why it reads degraded. ``last_run_id``
    becomes *run_id* when one is given: the ending of work a launched run started is written onto
    that run's row, which stays the last one.

    The serializers redact ``last_error_summary`` on the way out (`_serialize_store`,
    `_schedule_row_for`).
    """
    from personalclaw.triggers.history import SCHEDULE_STATUS_TO_OUTCOME
    from personalclaw.triggers.service import to_iso

    if run_id:
        trigger.last_run_id = run_id
    stamp = to_iso(at or time.time())
    outcome = SCHEDULE_STATUS_TO_OUTCOME.get(status, Outcome.FAILED.value)
    if status == "waiting":
        trigger.last_waiting_at = stamp
    elif outcome in _WENT_WRONG:
        trigger.last_failure_at = stamp
        trigger.last_error_summary = (why or "the run failed")[:ERROR_MAX]
    else:
        trigger.last_success_at = stamp
        if outcome == Outcome.DEGRADED.value and why:
            trigger.last_error_summary = why[:ERROR_MAX]


def _row(
    trigger: Any,
    *,
    run_id: str,
    tag: str,
    began: float,
    finished: float,
    result: Any,
    exc: BaseException | None,
    error: str,
    late: str,
) -> ScheduleRun:
    """The row one run records: a `ScheduleRun`, timed from *began* to *finished*.

    A failure's line, trace and error are why it failed: *error* when the dispatch rendered one
    (the WHAT/WHY/FIX envelope of a raise), else the exception, else why the result says it failed
    (`schedule_history.failure_for_result`). Anything else records what the action reported
    (`status_for_result`): its sentence for a person as the line, and what it printed as the trace;
    a park's line says it waits on you, and on what. A *late* run that did its work records
    ``ran_late``, and every row of one that did not fail says first why it was late.
    """
    from personalclaw.schedule_history import (
        UNSETTLED_STATUSES,
        ScheduleRun,
        failure_for_result,
        late_summary,
        status_for_result,
        summary_for_result,
    )
    from personalclaw.triggers import parks

    failure = ""
    work_id = ""
    if _failed(result, exc):
        status = "failure"
        why = error or (f"{type(exc).__name__}: {exc}" if exc is not None else "")
        failure = why or failure_for_result(result)
        summary = trace = failure
    else:
        status = status_for_result(result)
        summary = summary_for_result(result)
        trace = str(getattr(result, "stdout", "") or "") if result is not None else ""
        if status == "waiting":
            summary = trace = parks.waiting_line(result)
        if late:
            if status == "success":
                status = "ran_late"
            summary = late_summary(late, summary)
        if status in UNSETTLED_STATUSES:
            # The agent or the workflow run a launched (or queued) run started, so its row says
            # how it went when it ends (`triggers.settle`).
            work_id = str(getattr(result, "work_id", "") or "")
    return ScheduleRun(
        run_id=run_id,
        job_id=str(getattr(trigger, "id", "") or ""),
        job_name=str(getattr(trigger, "name", "") or ""),
        trigger=tag,
        started_at=began,
        finished_at=finished,
        duration_ms=int((finished - began) * 1000),
        status=status,
        summary=summary,
        trace=trace or summary,
        error=failure[:ERROR_MAX],
        work_id=work_id,
    )


def _failed(result: Any, exc: BaseException | None) -> bool:
    """Whether the action raised, or returned a result that says it failed."""
    return exc is not None or (result is not None and not bool(getattr(result, "success", True)))


async def record_run(
    trigger: Any,
    *,
    started_at: float,
    result: Any = None,
    exc: BaseException | None = None,
    error: str = "",
    late: str = "",
    by_hand: bool = False,
    store: Any = None,
    runs: Any = None,
    state: Any = None,
    on_attention: Callable[[Any, Any], None] | None = None,
) -> str:
    """Record one run of *trigger*'s action. Returns the row's id, ``""`` when none was written.

    *started_at* is when the action started; it has just finished. *result* is what it returned,
    or *exc* what it raised, with *error* the envelope the dispatch rendered for it: *error* is the
    evidence the row and the trigger keep, and *exc* what the run's exit is classified by
    (`autopause.classify_exception`), so a credential outage parks instead of spending the failure
    budget. *late* is why the run stands in for a slot that did not run on time (the tick's
    `missed.late_outcome`, or the review's Run now).

    *by_hand* is a run a person or an outside caller started
    (`trigger_runs._dispatch_store_action`). Its row is tagged ``manual``, which the hourly cap
    (`ScheduleRunStore.count_since`) and the failure streak (`autopause.consecutive_failures_from`)
    both pass over, and it leaves the trigger's health, state and switch alone. A fire's row is
    tagged with its exit type, and the fire walks the autopause decision
    (`autopause.ending_decision`): the streak is read from the history, row first, so it is DERIVED
    from the runs it summarises rather than kept as a second count beside them. A fire that only
    started its work (`launched`, `queued`) decides nothing yet: its work's ending does, when it
    comes (:func:`record_ending`), unless that work ended before this row was written, which is
    then written as the work ended, and decides here.

    *store* and *runs* are the trigger store and the run history to write, by default the active
    home's, the trigger store routed so that a row an app serves keeps its stamps where it lives.
    *state* is the dashboard a park's question is raised through (`parks.settle`). *on_attention*
    is handed the trigger and the lifecycle decision when a fire stops it, once (`_take`): the
    gateway raises the attention card for a trigger that stopped itself from it.

    A one-shot that retires after its run leaves the list last, once its run is written, so no
    write above can put it back (`service.retire_after_run`).

    Never raises: the run already happened, and losing its record is strictly better than turning a
    completed run into a crashed one.
    """
    try:
        return await _record(
            trigger,
            started_at=started_at,
            result=result,
            exc=exc,
            error=error,
            late=late,
            by_hand=by_hand,
            store=store,
            runs=runs,
            state=state,
            on_attention=on_attention,
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not record the run of %s", trigger, exc_info=True)
        return ""


async def _record(
    trigger: Any,
    *,
    started_at: float,
    result: Any,
    exc: BaseException | None,
    error: str,
    late: str,
    by_hand: bool,
    store: Any,
    runs: Any,
    state: Any,
    on_attention: Callable[[Any, Any], None] | None,
) -> str:
    from personalclaw.config.loader import config_dir
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers import autopause, parks
    from personalclaw.triggers.routing import routed
    from personalclaw.triggers.service import retire_after_run
    from personalclaw.triggers.store import TriggerStore

    trigger_id = str(getattr(trigger, "id", "") or "")
    if not trigger_id:
        return ""
    finished = time.time()
    if by_hand:
        tag = "manual"
    elif exc is not None:
        # A RAISING provider is classified by exception type: auth, transport, config, failed.
        tag = autopause.classify_exception(exc)
    elif _failed(result, None):
        # A returned `success=False` carries no exception, so it reads as a plain failure: the
        # fail-safe direction `classify_exception(None)` takes for an unrecognised error.
        tag = autopause.ExitType.FAILED.value
    else:
        tag = autopause.ExitType.OK.value

    run_id = f"{'manual' if by_hand else 'fire'}-{int(finished * 1000)}"
    run = _row(
        trigger,
        run_id=run_id,
        tag=tag,
        began=min(started_at or finished, finished),
        finished=finished,
        result=result,
        exc=exc,
        error=error,
        late=late,
    )
    # Read before the append, which redacts the row for the disk: the stamps keep what the run
    # said, and the serializers redact them on the way out. A failure keeps its error; a degraded
    # run its line, what it went without.
    status = run.status
    why = run.error if status == "failure" else (run.summary if status == "degraded" else "")
    runs = runs if runs is not None else ScheduleRunStore(config_dir())
    await runs.append(run)
    if run.status != status:
        # The work it started ended before its row was written, and the row was written as that
        # work ended (`ScheduleRunStore.append_sync`): that ending is this run's.
        status = run.status
        why = run.error if status == "failure" else ""
    # A park asks you, once, with the action's own card; a run that went through withdraws the
    # question an earlier one asked.
    parks.settle(trigger, result, state=state)

    rows: list[dict[str, Any]] = []
    if not by_hand:
        rows, _total = await runs.list_for_job(trigger_id, 0, STREAK_WINDOW)
    store = store if store is not None else routed(TriggerStore(base_dir=config_dir()))
    # Read just before it is written, with nothing awaited in between: the work this run started
    # can end meanwhile, and what its ending wrote (`record_ending`) must not be written over.
    loaded = store.get(trigger_id)
    if loaded is None:
        return run_id
    live = loaded.trigger
    decision = None if by_hand else autopause_decision(live, rows, run_id)
    _take(
        store,
        live,
        decision,
        status=status,
        why=why,
        run_id=run_id,
        at=finished,
        on_attention=on_attention,
    )
    retire_after_run(store, live, status=status, from_review=by_hand and bool(late))
    return run_id


def record_ending(
    trigger_id: str,
    work_id: str,
    *,
    status: str,
    why: str,
    store: Any,
    runs: Any,
    on_attention: Callable[[Any, Any], None] | None = None,
) -> Any:
    """What the work *work_id* a run only started did to its trigger, now that it has ended.
    Returns the trigger as written, or None when the store has no row for it. Never raises.

    The run's row already says how the work ended (`ScheduleRunStore.settle_sync`): *status*, *why*
    for a failure, the exit it ended with and when. Its trigger takes that ending as it takes a run
    that ended with its action (`record_run`): the lifecycle decision, from the history that row is
    in (`autopause.ending_decision`), so a failed agent counts toward the pause as a failed command
    does, and the stamps. Its row is still the one its last run opens, so ``last_run_id`` is left
    as it is. An ending that came before its row was written decides nothing here: the recorder
    writes that row as the work ended, and decides from it.

    A trigger switched off while its work ran takes no decision: an ending that comes in after its
    owner switched it off, or after it paused itself, neither resumes it nor stops it again.
    """
    try:
        loaded = store.get(trigger_id)
        if loaded is None:
            return None
        live = loaded.trigger
        rows, _total = runs._list_for_job_sync(trigger_id, 0, STREAK_WINDOW)
        row = next((r for r in rows if r.get("work_id") == work_id), None)
        decision = None
        if row is not None and live.enabled:
            decision = autopause_decision(live, rows, str(row.get("run_id") or ""))
        _take(
            store,
            live,
            decision,
            status=status,
            why=why,
            run_id="",
            at=float(row.get("finished_at") or 0.0) if row is not None else 0.0,
            on_attention=on_attention,
        )
        return live
    except Exception:  # noqa: BLE001 - the work has ended whether or not its trigger says so
        logger.warning("could not record how the work of %s ended", trigger_id, exc_info=True)
        return None


def autopause_decision(trigger: Any, rows: list[dict[str, Any]], run_id: str) -> Any:
    """The lifecycle decision run *run_id*'s ending takes for *trigger*, from its newest-first
    history *rows* (`autopause.ending_decision`)."""
    from personalclaw.triggers import autopause
    from personalclaw.triggers.models import TriggerState

    return autopause.ending_decision(
        rows,
        run_id,
        now=time.time(),
        # The trigger's own `failure_policy.autopause_after`, which a budget left out would
        # silently widen to the default.
        budget=autopause.budget_for(trigger),
        quarantined=str(getattr(trigger, "state", "")) == TriggerState.QUARANTINED.value,
    )


def _take(
    store: Any,
    live: Any,
    decision: Any,
    *,
    status: str,
    why: str,
    run_id: str,
    at: float,
    on_attention: Callable[[Any, Any], None] | None,
) -> None:
    """Write what a run's ending did onto its trigger *live*, the stored row: the lifecycle
    *decision* when it took one, and the stamps (:func:`stamp_run`).

    A trigger already stopped by an earlier decision (autopaused, quarantined) keeps that: a run
    that ends after it stopped neither resumes it nor stops it again. *on_attention* is handed the
    trigger and the decision when the decision moves it INTO a state that needs attention, once
    per episode (`autopause.AttentionCard`): the gateway raises the card that says it stopped.
    """
    from personalclaw.triggers import autopause
    from personalclaw.triggers.models import TriggerState

    if decision is not None and autopause.needs_attention(str(getattr(live, "state", ""))):
        decision = None
    if decision is not None:
        live.health_status = decision.health
        live.state = decision.state
        if autopause.needs_attention(decision.state):
            # The pause itself: a state that needs attention must stop firing.
            live.enabled = False
            logger.warning("trigger %s autopaused: %s", live.id, decision.reason or decision.state)
        # The park cooldown `autopause.unpark_due` reads, cleared on any outcome but a park so a
        # recovered trigger carries no stale one into its next outage.
        live.park_retry_after = (
            float(decision.retry_after) if decision.state == TriggerState.PARKED.value else 0.0
        )
    # A failure keeps its error; one with none says the lifecycle's reason rather than nothing.
    reason = decision.reason if decision is not None else ""
    stamp_run(live, status=status, why=why or reason, run_id=run_id, at=at)
    store.upsert(live)
    if decision is not None and autopause.needs_attention(decision.state):
        # A report's automation the clock paused is its report paused: the Reports page must
        # not say it runs (`knowledge.report_schedules.adopt`).
        from personalclaw.knowledge import report_schedules

        report_schedules.adopt(live)
        if on_attention is not None:
            on_attention(live, decision)
