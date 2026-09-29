"""A trigger's `launched` run, told how the agent it started went.

A fire whose action starts an agent (`run-prompt`, `invoke-agent`) records its run the moment the
agent starts, as `launched`: started is not succeeded, and nothing is known yet. The agent's
ending reached the trigger's route as a note and nowhere else, so the trigger's history said
"launched" for good, beside a note saying the same run had finished or failed.

The row now names the agent (`ActionResult.work_id`), and when the agent ends
(`gateway._subagent_done`) this says how, on that row (`ScheduleRunStore.settle_sync`):

* `success`, and what the agent said;
* `failure`, and why — a time limit, a crash, a start nobody answered in time;
* `declined`, when the owner answered its start with Deny. Their own decision is not a failure: the
  row says they declined it, and no note calls it one (`SubagentInfo.declined`).

A failure is also stamped on the trigger (`last_failure_at`, `last_error_summary`), and a success
moves `last_success_at`, as the fire's own recorders stamp a run that ended when its action did, so
the Triggers list's last run says what the history says.

A lifecycle hook (`lifecycle:<id>`) keeps no run rows — only its last status — so there is no row
to settle for one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: What a declined run's row says. The owner is who reads it, and it was their Deny.
DECLINED_LINE = "You declined it, so it did not run."


def settle_agent_run(info: Any, *, base_dir: Path | None = None) -> bool:
    """Write how the agent *info* describes ended onto the run of the trigger that started it.

    Returns whether its row took the ending (`ScheduleRunStore.settle_sync`): False for an agent
    no stored trigger started, or one whose row already says how it went. Never raises: the agent
    has ended whether or not its row says so, and the notes about it still go out.
    """
    trigger_id = getattr(info, "trigger_id", "")
    agent_id = getattr(info, "id", "")
    if not (isinstance(trigger_id, str) and trigger_id and isinstance(agent_id, str) and agent_id):
        return False
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX
        from personalclaw.schedule_history import ScheduleRunStore
        from personalclaw.subagent import agent_work_id

        if trigger_id.startswith(LIFECYCLE_TRIGGER_PREFIX):
            return False
        home = base_dir if base_dir is not None else config_dir()
        declined = getattr(info, "declined", False) is True
        error = "" if declined else str(getattr(info, "error", "") or "")
        status = "declined" if declined else ("failure" if error else "success")
        said = DECLINED_LINE if declined else (error or str(getattr(info, "result", "") or ""))
        taken = ScheduleRunStore(home).settle_sync(
            trigger_id, agent_work_id(agent_id), status=status, summary=said, error=error
        )
        if taken and not declined:
            _stamp(trigger_id, failed=bool(error), error=error, home=home)
        return taken
    except Exception:  # noqa: BLE001 - see the docstring: the row is bookkeeping about the run
        logger.warning("could not settle the run of trigger %s", trigger_id, exc_info=True)
        return False


def _stamp(trigger_id: str, *, failed: bool, error: str, home: Path) -> None:
    """Move the trigger's last-run stamps for a run that has now ended, as the recorders do for a
    run that ended with its action (`gateway._record_fire_outcome`, `_record_manual_run`)."""
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home)
    row = store.get(trigger_id)
    if row is None:
        return
    live = row.trigger
    stamp = datetime.now(timezone.utc).isoformat()
    if failed:
        live.last_failure_at = stamp
        # Serializers redact it on the way out (`_serialize_store`), as they do the recorders'.
        live.last_error_summary = (error or "the agent failed")[:200]
    else:
        live.last_success_at = stamp
    store.upsert(live)
