"""The trigger reaper: force-release runs that blew their deadline.

**🔴 THE DEFECT THIS REPLACES: the cron reaper has been INERT.**
`ScheduleService._reaper_loop` sweeps `self._job_start_times`, and that dict has exactly ONE writer
in the whole codebase — `_run_job_isolated`, reachable only from `_on_timer`, i.e. only from the
legacy timer the clock cutover stopped arming. Driven directly (a service with a genuinely hung task
in `_executing` + `_running_tasks`, the reaper interval cut to 50ms, eight sweeps):

    job still in _executing : True
    task still running      : True
    sessions.reset called   : []
    reaped_jobs             : set()

Nothing was reaped, nothing could be. `start_reaper()` still returned successfully and still logged
nothing wrong, so the 30-minute deadline the plan calls "defense-in-depth over ALL trigger-fired
runs" (§ "Unattended LLM turns", risk table "hung run") was a control that was present, reviewed,
and enforcing nothing — the exact failure class this program keeps finding.

**Why a new module instead of repairing the old loop.** The state the old reaper needs
(`_job_start_times`, `_job_jitter`, `_reaped_jobs`, `_active_session_keys`) is all process-local, so
it could only ever describe runs THIS process started through the retired timer. The store-backed
fire path already keeps the same fact durably and cross-process: the **claim** carries
`trigger_id`, `holder`, and `claimed_at`, is written by the tick when a fire is granted, and is
released by the executor's `finally`. So "which runs are in flight, and since when" is answerable
from disk, correctly, after a restart and from any process. Reaping reads that instead.

**What reaping means here, and what it deliberately does NOT mean.** Two things bound a run:

* the CLAIM, which gates the next fire — a stuck claim wedges its trigger until the 1h self-expiry,
  so the reaper releases it and records the outcome; and
* the PROCESS, which is bounded by whoever owns it. `run-prompt`/`invoke-agent` fires are
  fire-and-forget `SubagentManager.spawn` calls, and that manager runs its own live reaper with the
  same 30min/60s/SIGKILL parameters over `_agents` (verified: `spawn` registers the entry and boot
  calls `start_reaper()` unconditionally). Killing a session from here as well would mean two
  reapers racing over one process — so this one owns the claim and lets the subagent reaper own the
  process. That is a narrower job than the cron reaper *claimed* to do, and strictly more than it
  actually did.

The sweep is therefore: read every live claim, and for each one older than its deadline, release the
claim, and close the run (`_close`): a `timeout` row in its history, so the run shows up as reaped
rather than as still-running-forever, and the trigger's stamps and health. The sweep wrote no row
at all, only the health, so a reaped run was in no history and its trigger's last run stayed the
one before it.

**🔴 THE SECOND TERMINALIZER: the BOOT ORPHAN PASS.** Everything above is bounded by a
CLOCK, and that is the wrong first answer after a restart. `overdue` waits 1800s and claim expiry
waits 3600s, so a run whose owning process died one second ago reads as IN FLIGHT for half an hour —
the phantom `guardrails/self_destruct.py` describes. A restart kills the owner of every in-flight
run, and death is observable, so `terminalize_orphans` answers at boot instead: it reads each live
claim's `owner_pid` (the claim now carries one) and terminalizes only the claims whose owner is
PROVABLY gone. The deadline sweep stays as the backstop for a run that is alive and merely stuck —
two different questions, two passes, one shared `_close` so both look the same to a user.

**And the run a stop cuts off.** A stop or a Restart does not leave its runs for the boot pass: it
cancels them, and the cancelled run gives its claim back on the way out. So the gateway records
each one itself as it stops: an action still running (`record_stopped_run`), and the agent a run
started and left working (`record_stopped_work`), whose run was recorded `launched` and would
otherwise have read as failed with the agent's own word for the stop, "cancelled". Each gets the
`interrupted` row, its trigger's stamps and the review card; the stop sends no notice, and the next
start says once what it cut off (`review.take_unannounced`).

Every pass records a run by hand as the hand run it was (`claims.held_by_hand`, or the row's own
``manual`` tag): its row is ``manual``, and it leaves its trigger's health alone, as a hand run's
record does (`run_record.record_run`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from personalclaw.triggers.models import TriggerHealth

logger = logging.getLogger(__name__)

#: Seconds between sweeps. Matches the cron + subagent reapers (`_REAPER_INTERVAL`), so all three
#: deadlines are observed at one cadence rather than three that drift.
REAPER_INTERVAL_SECS = 60.0

#: A run's deadline. Matches `schedule._JOB_TIMEOUT_SECS` and `subagent._TIMEOUT_SECS` (both 1800)
#: — the plan keeps the reaper "as defense-in-depth over ALL trigger-fired runs", so the number a
#: user already reasons about for a cron has to be the number a store-backed trigger gets.
RUN_DEADLINE_SECS = 1800.0

#: The `ScheduleRun.status` a restart-interrupted run carries.
#:
#: Its own word, not `timeout`. A run a restart cut off did not blow a deadline, and recording it
#: as one told the user something false about the automation. `web/src/pages/schedule/scheduleMeta
#: .ts` renders it ("interrupted"), and `triggers/history.SCHEDULE_STATUS_TO_OUTCOME` translates it
#: for the unified feed into its own outcome (`Outcome.INTERRUPTED`), not a failure — both in the
#: same change, because an unrecognised status falls through `statusMeta` to "never run" in neutral
#: grey. It is NOT retried on its own: the run may already have done part of its work, so it goes
#: on the review (`triggers/review.py`), where the user runs it again or dismisses it.
RESTART_INTERRUPTED_STATUS = "interrupted"

#: The `ScheduleRun.status` of a run the deadline sweep reaped (`reap_one`).
DEADLINE_STATUS = "timeout"

#: Every status a row the reaper writes can carry (`_write_row`).
_ROW_STATUSES = (RESTART_INTERRUPTED_STATUS, DEADLINE_STATUS)

#: What every interrupted run's reason ends with: what happens to it now, and where you decide.
_NOT_RUN_AGAIN = (
    "It is not run again on its own, because it may already have done part of its work: run it "
    "again from the review on the Triggers page, or dismiss it there."
)


def overdue(
    *,
    now: float = 0.0,
    deadline_secs: float = RUN_DEADLINE_SECS,
    base_dir: Path | str | None = None,
) -> list[tuple[str, float]]:
    """Every trigger whose in-flight run has blown the deadline, as `(trigger_id, elapsed)`.

    A pure read, separate from the reap so the doctor and a test can ask the question without
    causing an effect. Sorted by id for a stable, reproducible sweep order.

    Deliberately reads through S97's `running_ids`, so an EXPIRED claim is already invisible here:
    the 1h self-expiry is the outer backstop and this deadline is the inner one, and a reaper that
    re-reaped self-expired claims would log a kill for a run nothing is holding.
    """
    from personalclaw.triggers import claims

    now = now or time.time()
    out: list[tuple[str, float]] = []
    for trigger_id in claims.running_ids(now=now, base_dir=base_dir):
        started = claims.running_since(trigger_id, now=now, base_dir=base_dir)
        if started is None:
            continue
        elapsed = now - started
        if elapsed > deadline_secs:
            out.append((trigger_id, elapsed))
    return sorted(out)


def reap_one(
    trigger_id: str,
    elapsed: float,
    *,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Release one overdue run's claim and record it. NEVER raises — returns what it managed to do.

    Never raises because this runs inside a background sweep: one trigger whose store row is
    unreadable must not stop the sweep from freeing the others. The returned dict is the audit
    record, so a caller (the loop, the doctor, a test) can assert on the outcome rather than on
    log text.

    The order is release-then-record. If the process dies between the two, the trigger is FREE with
    no ledger row — noisy but harmless. The reverse order could leave a recorded-as-reaped run whose
    claim still blocks every future fire, which is the wedge the reaper exists to prevent.

    The claim is read before it is released: it says when the run started, and whose it was.
    """
    from personalclaw.triggers import claims

    now = now or time.time()
    held = claims.read_claim(trigger_id, now=now, base_dir=base_dir)
    released = claims.release_claim(trigger_id, base_dir=base_dir)
    record: dict[str, Any] = {
        "trigger_id": trigger_id,
        "elapsed": int(elapsed),
        "released": bool(released),
        "deadline_secs": int(RUN_DEADLINE_SECS),
        "recorded": False,
    }
    logger.warning(
        "Reaper: trigger %s exceeded %ds (ran %.0fs), releasing its claim",
        trigger_id,
        int(RUN_DEADLINE_SECS),
        elapsed,
    )
    record["recorded"] = _close(
        trigger_id,
        status=DEADLINE_STATUS,
        reason=f"Reaped after {int(elapsed)}s (exceeded {int(RUN_DEADLINE_SECS)}s deadline)",
        started_at=held.claimed_at if held is not None else now - elapsed,
        now=now,
        by_hand=held is not None and claims.held_by_hand(held.holder),
        store=store,
        base_dir=base_dir,
    )
    _audit(trigger_id, tool_name="reaper_force_kill", outcome="reaped", elapsed=elapsed)
    return record


def _close(
    trigger_id: str,
    *,
    status: str,
    reason: str,
    started_at: float,
    now: float,
    by_hand: bool,
    store: Any,
    base_dir: Path | str | None,
) -> bool:
    """Close a run the reaper ended: its row in the history, and its trigger's stamps.

    Shared by every pass in this module — the deadline sweep, the boot orphan pass and the run a
    stop cut off — because "how a non-finishing run appears to the user" must be one answer. Two
    copies is how a reaped run and an interrupted one start rendering differently for no reason.

    Returns whether the trigger took its stamps (`_stamp`); the row is written either way.
    """
    run_id = _write_row(
        trigger_id,
        status=status,
        reason=reason,
        name=_name(store, trigger_id),
        started_at=started_at,
        now=now,
        by_hand=by_hand,
        base_dir=base_dir,
    )
    return _stamp(
        store, trigger_id, status=status, reason=reason, run_id=run_id, now=now, by_hand=by_hand
    )


def _name(store: Any, trigger_id: str) -> str:
    """The trigger's name, for its row; "" when the store has no row for it. Never raises."""
    if store is None:
        return ""
    try:
        row = store.get(trigger_id)
    except Exception:  # noqa: BLE001 - the row is named by its trigger as it is called now anyway
        logger.debug("Reaper: could not read the name of %s", trigger_id, exc_info=True)
        return ""
    return str(getattr(row.trigger, "name", "") or "") if row is not None else ""


def _stamp(
    store: Any,
    trigger_id: str,
    *,
    status: str,
    reason: str,
    run_id: str,
    now: float,
    by_hand: bool,
) -> bool:
    """Move the TRIGGER's stamps for a run the reaper closed. True when the trigger was written.

    The stamps a run's record moves (`run_record.stamp_run`): ``last_run_id`` names the row just
    written, so the trigger's last result opens it, and ``last_failure_at`` with the reason in
    ``last_error_summary`` dates the trigger's last run by it and says what stopped it. These rows
    moved neither, so after one the trigger's last run read as the run before it, at that run's
    time, beside the reason of whatever had gone wrong last.

    A fire's trigger also reads DEGRADED, not FAILING: `migrate.py`'s `_HEALTH_FROM_STATUS` maps a
    legacy `timeout` status to DEGRADED, and that reading is the honest one — the trigger is not
    broken, its last run did not finish. A run *by_hand* leaves the health alone.

    The row is read again just before it is written, so an edit saved meanwhile is not written
    over. Never raises: one unreadable row must not stop the pass from freeing the others.
    """
    if store is None:
        return False
    try:
        from personalclaw.triggers.run_record import stamp_run

        row = store.get(trigger_id)
        if row is None:
            return False
        trigger = row.trigger
        stamp_run(trigger, status=status, why=reason, run_id=run_id, at=now)
        if not by_hand:
            trigger.health_status = TriggerHealth.DEGRADED.value
        store.upsert(trigger)
        return True
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("Reaper: could not record the outcome for %s", trigger_id, exc_info=True)
        return False


def _audit(trigger_id: str, *, tool_name: str, outcome: str, elapsed: float) -> None:
    """SEL audit for a terminalized run. Never raises — an audit failure must not mask the reap.

    `reaper_force_kill` matches what the cron reaper logged, so an operator's existing query still
    finds reaps after the cutover; the boot pass logs its own name so the two are distinguishable
    in one query rather than being conflated as one control.
    """
    try:
        from personalclaw.sel import sel

        sel().log_tool_invocation(
            session_key=f"cron:{trigger_id}",
            source="cron",
            tool_name=tool_name,
            tool_input=None,
            outcome=outcome,
            metadata={"job_id": trigger_id, "elapsed": int(elapsed)},
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("Reaper: SEL audit failed for %s", trigger_id, exc_info=True)


def sweep_once(
    *,
    store: Any = None,
    now: float = 0.0,
    deadline_secs: float = RUN_DEADLINE_SECS,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """One sweep: reap every overdue run and return the audit records. NEVER raises.

    Separate from `run_forever` for the reason `loop.tick_once` is separate from `loop.run_forever`:
    a test (and `automation doctor`) can drive exactly one sweep at an exact instant instead of
    racing a background task with a real clock.
    """
    records: list[dict[str, Any]] = []
    for trigger_id, elapsed in overdue(now=now, deadline_secs=deadline_secs, base_dir=base_dir):
        records.append(reap_one(trigger_id, elapsed, store=store, now=now, base_dir=base_dir))
    return records


def terminalize_orphans_sync(
    *,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Terminalize every live claim whose OWNING PROCESS is gone. NEVER raises (WF2AUT-16).

    🔴 THE DEFECT THIS CLOSES: an orphaned run read as HUNG until a DEADLINE elapsed.
    `guardrails/self_destruct.py` states it in its own words — *"the ScheduleRunStore row never
    reaches a terminal state and the fire reads afterwards as a HUNG run rather than as a
    self-inflicted stop. The user is left debugging a phantom."* Everything in this module above
    this line is bounded by a clock: `overdue` compares `now - claimed_at` against 1800s, and
    read-time claim expiry against 3600s. Both are the right BACKSTOP and the wrong FIRST ANSWER. A
    gateway restart kills the owner of every in-flight run, and after one the owner's death is an
    observable fact — so the honest answer is available at boot, not half an hour later.

    Measured before this existed, on a claim one second old whose pid was gone: `overdue()` → `[]`,
    `claims.is_running()` → `True`. Thirty minutes of "still running" about a process that did not
    exist.

    Three writes per orphan, and each one is a surface a user reads:

    1. **Release the claim**, so `is_running` goes false — the schedule row stops rendering as in
       flight, and the next tick's `overlap: skip` gate stops suppressing the fire it should grant.
    2. **A terminal run row**, so the run history shows an ending instead of an open row, with
       `status: interrupted` (`RESTART_INTERRUPTED_STATUS`) and the reason in `error`, which both
       `ScheduleDetail`'s `Last run` block and `RunHistory` render.
    3. **The trigger's stamps and health**, through the same `_close` the deadline sweep uses.

    A claim a run by hand held (`claims.held_by_hand`) is closed as that hand run: a Run now a
    crash cut off was recorded as a scheduled fire, and marked its trigger degraded.

    The run is not retried here. The gateway puts each record on the review
    (`triggers/review.cards_from_orphans`), where the user runs it again or dismisses it.

    Release-then-record, for the reason `reap_one` gives: a crash between the two leaves the trigger
    FREE with no row (noisy, harmless), where the reverse could leave a recorded-as-ended run whose
    claim still blocks every future fire.

    Sync, matching `sweep_once` and `service.boot`, so a test can drive it at an exact instant; see
    `terminalize_orphans` for the async face the gateway awaits.
    """
    from personalclaw.triggers import claims

    now = now or time.time()
    records: list[dict[str, Any]] = []
    for trigger_id, owner_pid in claims.orphaned_ids(now=now, base_dir=base_dir):
        held = claims.read_claim(trigger_id, now=now, base_dir=base_dir)
        started = held.claimed_at if held is not None else now
        by_hand = held is not None and claims.held_by_hand(held.holder)
        elapsed = max(0.0, now - started)
        released = claims.release_claim(trigger_id, base_dir=base_dir)
        logger.warning(
            "Boot: trigger %s was running under pid %d, which is gone; terminalizing after %.0fs",
            trigger_id,
            owner_pid,
            elapsed,
        )
        # A claim naming this very process was left by the image a restart replaced
        # (`claims.orphaned_ids`): its pid is not gone, the program that ran it is.
        gone = (
            "the gateway restarted while this was running"
            if owner_pid == os.getpid()
            else f"the process running this (pid {owner_pid}) is gone"
        )
        reason = (
            f"Interrupted by a gateway restart: {gone}. It ran {int(elapsed)}s. {_NOT_RUN_AGAIN}"
        )
        record: dict[str, Any] = {
            "trigger_id": trigger_id,
            "owner_pid": owner_pid,
            "elapsed": int(elapsed),
            "released": bool(released),
            "reason": reason,
            "by_hand": by_hand,
            "recorded": False,
        }
        record["recorded"] = _close(
            trigger_id,
            status=RESTART_INTERRUPTED_STATUS,
            reason=reason,
            started_at=started,
            now=now,
            by_hand=by_hand,
            store=store,
            base_dir=base_dir,
        )
        _audit(
            trigger_id, tool_name="boot_orphan_terminalize", outcome="interrupted", elapsed=elapsed
        )
        records.append(record)
    return records


async def terminalize_orphans(
    *,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """`terminalize_orphans_sync` off the event loop. What the gateway boot path awaits.

    Async for the reason `ScheduleRunStore.append` is: the pass writes a JSONL row per orphan under
    a file lock, and boot runs on the loop that is about to start serving.
    """
    return await asyncio.to_thread(
        terminalize_orphans_sync, store=store, now=now, base_dir=base_dir
    )


def record_stopped_run(
    trigger_id: str,
    *,
    started_at: float,
    restarting: bool,
    by_hand: bool = False,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Close a run this gateway cut off as it stopped or restarted. NEVER raises.

    🔴 THE DEFECT THIS CLOSES: a stop or a Restart cancelled a run in flight and recorded nothing.
    A stop cancels the loops a fire runs in, and `asyncio.CancelledError` is not an `Exception`, so
    the dispatch's `except Exception`, which records a failed fire, never saw it — and the
    executor's `finally` gave the claim back, so the boot pass above, which closes a run whose
    owner died, found no claim to close. The run just ended: no row in its history, no card on the
    review, and a last run that read as whatever ran before it.

    Closed here the way that pass closes a run whose owner died, with a reason naming what stopped
    it (`_close`): the `interrupted` row, the trigger's stamps and health, the audit, and the card
    on the review (`triggers/review.py`), where the user runs it again or dismisses it. A run
    started *by_hand* (Run now, the review's Run now) is recorded as the hand run it was — its row
    is `manual`, and it leaves the trigger's health alone, as a hand run's record does
    (`run_record.record_run`). The stop sends no notice: the next start says once what it cut off.

    Returns the record in the boot pass's shape. Synchronous, awaiting nothing: it runs inside the
    cancellation it records, where any await can be cancelled again.
    """
    now = now or time.time()
    elapsed = max(0.0, now - (started_at or now))
    reason = _stop_reason(restarting=restarting, elapsed=elapsed)
    record: dict[str, Any] = {
        "trigger_id": trigger_id,
        "elapsed": int(elapsed),
        "reason": reason,
        "restarting": bool(restarting),
        "recorded": False,
    }
    logger.warning(
        "trigger %s was running when the gateway %s; recording it as interrupted after %.0fs",
        trigger_id,
        "restarted" if restarting else "stopped",
        elapsed,
    )
    record["recorded"] = _close(
        trigger_id,
        status=RESTART_INTERRUPTED_STATUS,
        reason=reason,
        started_at=started_at or now,
        now=now,
        by_hand=by_hand,
        store=store,
        base_dir=base_dir,
    )
    _audit(trigger_id, tool_name="stop_interrupt", outcome="interrupted", elapsed=elapsed)
    _keep_for_review(record, now=now, base_dir=base_dir)
    return record


def record_stopped_work(
    trigger_id: str,
    *,
    work_id: str,
    restarting: bool,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Close the run whose work a stop is about to cut off: the agent it started. NEVER raises.

    🔴 THE DEFECT THIS CLOSES: a Run now cut by a Restart read "failed · cancelled". An action that
    starts an agent (`invoke-agent`, `run-prompt`) returns as soon as the agent starts, and its run
    is recorded `launched`, naming the agent in `work_id`. The stop cancels the agent, which ends
    with its own word for that, "cancelled", and that ending was written onto the run's row
    (`triggers.settle`) as a failure: history "failure · cancelled", the bell "<name> failed ·
    cancelled". Nothing named the restart, nothing went on the review, and the next start said
    nothing.

    So the gateway closes each such run before it cuts the agent off (`gateway._shutdown`): the
    `launched` row says it was interrupted, and why (`settle.settle_interrupted`); the trigger's
    stamps move (`_stamp`), and a fire's trigger reads degraded while a run by hand (the row's own
    ``manual`` tag) leaves the health alone; and the card waits on the review. The agent's own
    ending then finds its row already closed.

    Returns the record in `record_stopped_run`'s shape, or None when no `launched` row of
    *trigger_id* waits on *work_id*. Synchronous, for the reason `record_stopped_run` is.
    """
    try:
        from personalclaw.schedule_history import ScheduleRunStore
        from personalclaw.triggers import settle

        now = now or time.time()
        root = _root(base_dir)
        row = ScheduleRunStore(root).unsettled_row(trigger_id, work_id)
        if row is None:
            return None
        elapsed = max(0.0, now - float(row.get("started_at") or now))
        reason = _stop_reason(restarting=restarting, elapsed=elapsed)
        if not settle.settle_interrupted(trigger_id, work_id, why=reason, at=now, home=root):
            return None
    except Exception:  # noqa: BLE001 - the stop goes on whether or not this lands
        logger.warning("could not close the run of %s a stop cut off", trigger_id, exc_info=True)
        return None
    logger.warning(
        "trigger %s's agent was working when the gateway %s; recording its run as interrupted",
        trigger_id,
        "restarted" if restarting else "stopped",
    )
    record: dict[str, Any] = {
        "trigger_id": trigger_id,
        "elapsed": int(elapsed),
        "reason": reason,
        "restarting": bool(restarting),
        "recorded": False,
    }
    record["recorded"] = _stamp(
        store,
        trigger_id,
        status=RESTART_INTERRUPTED_STATUS,
        reason=reason,
        run_id=str(row.get("run_id") or ""),
        now=now,
        by_hand=str(row.get("trigger") or "") == "manual",
    )
    _audit(trigger_id, tool_name="stop_interrupt", outcome="interrupted", elapsed=elapsed)
    _keep_for_review(record, now=now, base_dir=base_dir)
    return record


def _stop_reason(*, restarting: bool, elapsed: float) -> str:
    """Why a run a stop cut off did not finish, and what happens to it now."""
    if restarting:
        why = "Interrupted by a gateway restart: the gateway restarted while this was running."
    else:
        why = "Interrupted when the gateway stopped: it stopped while this was running."
    return f"{why} It ran {int(elapsed)}s. {_NOT_RUN_AGAIN}"


def _keep_for_review(record: dict[str, Any], *, now: float, base_dir: Path | str | None) -> None:
    """Put a run a stop cut off on the review, to be announced by the next start. Never raises."""
    try:
        from personalclaw.triggers import review

        review.record(
            review.cards_from_orphans([record], now=now, unannounced=True), base_dir=base_dir
        )
    except Exception:  # noqa: BLE001 - the row is written; losing the card must not undo it
        logger.warning(
            "could not put interrupted run %s on the review",
            record.get("trigger_id"),
            exc_info=True,
        )


def _root(base_dir: Path | str | None) -> Path:
    """The home a pass's rows belong to: the store's own, else the active one."""
    if base_dir is not None:
        return Path(base_dir)
    from personalclaw.config.loader import config_dir

    return config_dir()


def _write_row(
    trigger_id: str,
    *,
    status: str,
    reason: str,
    name: str,
    started_at: float,
    now: float,
    by_hand: bool,
    base_dir: Path | str | None = None,
) -> str:
    """The terminal run row for a run the reaper closed; its id, or "" when none was written.

    Only the statuses this module owns (`_ROW_STATUSES`). Rooted at `base_dir`, never at the
    ambient `config_dir()`, for the leak `service._run_store` documents: a row describing a fire
    from `<base_dir>/triggers.json` belongs beside it, and a pass that reached for the active home
    instead would deposit rows in the operator's real `~/.personalclaw/cron-history/` whenever it
    ran against another store. Never raises.
    """
    if status not in _ROW_STATUSES:
        return ""
    try:
        from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

        run_id = f"{status}-{int(now * 1000)}"
        ScheduleRunStore(_root(base_dir)).append_sync(
            ScheduleRun(
                run_id=run_id,
                job_id=trigger_id,
                job_name=name,
                # `manual` for a hand run, the tag its record gives one (`run_record.record_run`):
                # the hourly cap and the failure streak both pass over it.
                trigger="manual" if by_hand else "scheduled",
                started_at=started_at,
                finished_at=now,
                duration_ms=int(max(0.0, now - started_at) * 1000),
                status=status,
                summary="",
                error=reason,
            )
        )
        return run_id
    except Exception:  # noqa: BLE001 - the claim is already freed; losing the row must not undo it
        logger.debug(
            "Reaper: could not record the %s row for %s", status, trigger_id, exc_info=True
        )
        return ""


async def run_forever(
    *,
    store: Any = None,
    base_dir: Path | str | None = None,
    interval_secs: float = REAPER_INTERVAL_SECS,
) -> None:
    """Sweep forever. NEVER returns normally.

    Cancellation-safe in the same shape as the clock loop: `CancelledError` propagates so shutdown
    can stop it, and every other exception is logged and the loop continues. A reaper that died on
    one bad sweep would silently stop bounding every run on the machine, and it would look exactly
    like a healthy one — which is how the loop it replaces stayed inert for six sessions.
    """
    while True:
        await asyncio.sleep(interval_secs)
        try:
            await asyncio.to_thread(sweep_once, store=store, base_dir=base_dir)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sweep must outlive any single failure
            logger.warning("trigger reaper sweep failed; continuing", exc_info=True)
