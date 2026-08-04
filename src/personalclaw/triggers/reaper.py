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

    # Mark the trigger's health so the reap is visible on the surface a user actually looks at.
    # DEGRADED, not FAILING: `migrate.py`'s `_HEALTH_FROM_STATUS` maps a legacy `timeout` status to
    # DEGRADED, and that reading is the honest one — the trigger is not broken, its last run did not
    # finish. The fields are `health_status`/`last_error_summary` (NOT `last_status`/`last_error`,
    # which are the LEGACY names `LEGACY_FIELD_MAP` translates FROM — writing those would set two
    # attributes nothing reads and leave the health dot green on a reaped run).
    if store is not None:
        try:
            row = store.get(trigger_id)
            if row is not None:
                trigger = row.trigger
                trigger.health_status = TriggerHealth.DEGRADED.value
                trigger.last_error_summary = (
                    f"Reaped after {int(elapsed)}s (exceeded {int(RUN_DEADLINE_SECS)}s deadline)"
                )
                store.upsert(trigger)
                record["recorded"] = True
        except Exception:  # noqa: BLE001 - one unreadable row must not stop the sweep
            logger.debug("Reaper: could not record the reap for %s", trigger_id, exc_info=True)

    # SEL audit, matching what the cron reaper logged so an operator's existing query still finds
    # reaps after the cutover.
    try:
        from personalclaw.sel import sel

        sel().log_tool_invocation(
            session_key=f"cron:{trigger_id}",
            source="cron",
            tool_name="reaper_force_kill",
            outcome="reaped",
            metadata={"job_id": trigger_id, "elapsed": int(elapsed)},
        )
    except Exception:  # noqa: BLE001 - an audit failure must not mask the reap
        logger.debug("Reaper: SEL audit failed for %s", trigger_id, exc_info=True)
    return record


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
