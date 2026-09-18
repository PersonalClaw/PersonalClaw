"""The trigger reaper: force-release runs that blew their deadline (§3.1 / §8 — S106).

**🔴 THE DEFECT THIS REPLACES: the cron reaper has been INERT since S100.**
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
fire path already keeps the same fact durably and cross-process: S97's **claim** carries
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
claim, mark the trigger's health, and write a `timeout` ledger row so the run shows up in history as
reaped rather than as still-running-forever.

**🔴 THE SECOND TERMINALIZER: the BOOT ORPHAN PASS (WF2AUT-16).** Everything above is bounded by a
CLOCK, and that is the wrong first answer after a restart. `overdue` waits 1800s and claim expiry
waits 3600s, so a run whose owning process died one second ago reads as IN FLIGHT for half an hour —
the phantom `guardrails/self_destruct.py` describes. A restart kills the owner of every in-flight
run, and death is observable, so `terminalize_orphans` answers at boot instead: it reads each live
claim's `owner_pid` (S97's claim now carries one) and terminalizes only the claims whose owner is
PROVABLY gone. The deadline sweep stays as the backstop for a run that is alive and merely stuck —
two different questions, two passes, one shared `_mark_degraded` so both look the same to a user.
"""

from __future__ import annotations

import asyncio
import logging
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

#: The `ScheduleRun.status` a restart-interrupted run carries (WF2AUT-16).
#:
#: 🔴 DELIBERATELY NOT A NEW MEMBER. `ScheduleRun.status` is closed at four values
#: (`success|failure|timeout|launched`) and `web/src/pages/schedule/scheduleMeta.ts` switches on
#: exactly those; an unrecognised value falls through `statusMeta` to "never run" in neutral grey —
#: the one label a genuinely-ended run must never render as. `timeout` because that is what the
#: vocabulary already means here ("it did not finish"), and it is what `migrate._HEALTH_FROM_STATUS`
#: maps to DEGRADED. WHY it did not finish rides in the row's `error`, which is the field both
#: `ScheduleDetail`'s `Last run` block and `RunHistory` render.
RESTART_INTERRUPTED_STATUS = "timeout"


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
    """
    from personalclaw.triggers import claims

    now = now or time.time()
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
    record["recorded"] = _mark_degraded(
        store,
        trigger_id,
        f"Reaped after {int(elapsed)}s (exceeded {int(RUN_DEADLINE_SECS)}s deadline)",
    )
    _audit(trigger_id, tool_name="reaper_force_kill", outcome="reaped", elapsed=elapsed)
    return record


def _mark_degraded(store: Any, trigger_id: str, summary: str) -> bool:
    """Record on the TRIGGER that its last run did not finish. True when the row was written.

    DEGRADED, not FAILING: `migrate.py`'s `_HEALTH_FROM_STATUS` maps a legacy `timeout` status to
    DEGRADED, and that reading is the honest one — the trigger is not broken, its last run did not
    finish. The fields are `health_status`/`last_error_summary` (NOT `last_status`/`last_error`,
    which are the LEGACY names `LEGACY_FIELD_MAP` translates FROM — writing those would set two
    attributes nothing reads and leave the health dot green on a reaped run).

    Shared by both terminalizers in this module — the deadline sweep and WF2AUT-16's boot orphan
    pass — because "how a non-finishing run appears to the user" must be one answer. Two copies is
    how a reaped run and an interrupted one start rendering differently for no reason.

    Never raises: one unreadable row must not stop the pass from freeing the others.
    """
    if store is None:
        return False
    try:
        row = store.get(trigger_id)
        if row is None:
            return False
        trigger = row.trigger
        trigger.health_status = TriggerHealth.DEGRADED.value
        trigger.last_error_summary = summary
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
    2. **A terminal run row**, so the run history shows an ending instead of an open row. `status`
       stays inside `ScheduleRun`'s closed four-member set (`success|failure|timeout|launched`) —
       WF2AUT-16 forbids a fifth member, because `scheduleMeta.ts` switches on that vocabulary and
       an unknown value renders as "never run" grey. The interrupted-by-restart distinction rides in
       `error`, which both `ScheduleDetail`'s `Last run` block and `RunHistory` already render.
    3. **The trigger's health**, via the same `_mark_degraded` the deadline sweep uses.

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
        started = claims.running_since(trigger_id, now=now, base_dir=base_dir) or now
        elapsed = max(0.0, now - started)
        released = claims.release_claim(trigger_id, base_dir=base_dir)
        logger.warning(
            "Boot: trigger %s was running under pid %d, which is gone; terminalizing after %.0fs",
            trigger_id,
            owner_pid,
            elapsed,
        )
        reason = (
            f"Interrupted by a gateway restart: the process running this "
            f"(pid {owner_pid}) is gone. It ran {int(elapsed)}s."
        )
        record: dict[str, Any] = {
            "trigger_id": trigger_id,
            "owner_pid": owner_pid,
            "elapsed": int(elapsed),
            "released": bool(released),
            "reason": reason,
            "recorded": False,
        }
        record["recorded"] = _mark_degraded(store, trigger_id, reason)
        _write_interrupted_row(
            trigger_id, started_at=started, now=now, reason=reason, base_dir=base_dir
        )
        _audit(trigger_id, tool_name="boot_orphan_terminalize", outcome="interrupted", elapsed=elapsed)
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


def _write_interrupted_row(
    trigger_id: str,
    *,
    started_at: float,
    now: float,
    reason: str,
    base_dir: Path | str | None = None,
) -> None:
    """The terminal run row for an interrupted run. Never raises.

    Rooted at `base_dir`, never at the ambient `config_dir()`, for the leak `service._run_store`
    documents: a row describing a fire from `<base_dir>/triggers.json` belongs beside it, and a pass
    that reached for the active home instead would deposit rows in the operator's real
    `~/.personalclaw/cron-history/` whenever it ran against another store.
    """
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

        root = Path(base_dir) if base_dir is not None else config_dir()
        ScheduleRunStore(root).append_sync(
            ScheduleRun(
                run_id=f"interrupted-{int(now * 1000)}",
                job_id=trigger_id,
                trigger="scheduled",
                started_at=started_at,
                finished_at=now,
                duration_ms=int(max(0.0, now - started_at) * 1000),
                status=RESTART_INTERRUPTED_STATUS,
                summary="",
                error=reason,
            )
        )
    except Exception:  # noqa: BLE001 - the claim is already freed; losing the row must not undo it
        logger.debug("Boot: could not record the interrupted row for %s", trigger_id, exc_info=True)


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
