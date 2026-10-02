"""A trigger's `launched` run, told how the agent it started went.

A fire whose action starts an agent (`run-prompt`, `invoke-agent`) records its run the moment the
agent starts, as `launched`: started is not succeeded, and nothing is known yet. The agent's
ending reached the trigger's route as a note and nowhere else, so the trigger's history said
"launched" for good, beside a note saying the same run had finished or failed.

The row now names the agent (`ActionResult.work_id`), and when the agent ends
(`gateway._subagent_done`) this says how, on that row (`ScheduleRunStore.settle_sync`):

* `success`, and what the agent said;
* `failure`, and why — a time limit, a crash, a start nobody answered in time;
* `refused`, when the run's own limits refused calls it made (`SubagentInfo.refused`): what its
  Allow said it may not do, or an approval nobody was there to give. It has not done all it was
  asked, so it is not a success, and its limits held, so it is not a failure: the row names the
  calls, then what the agent said;
* `declined`, when the owner answered its start with Deny. Their own decision is not a failure: the
  row says they declined it, and no note calls it one (`SubagentInfo.declined`).

A failure or a refusal is also stamped on the trigger (`last_failure_at`, `last_error_summary`),
and a success moves `last_success_at`, through the stamps a run that ended with its action takes
(`run_record.stamp_run`), so the Triggers list's last run says what the history says, and why.

A run that may do less than the step that started it asks (`SubagentInfo.held_back`: a working
folder its owner has not trusted, an agent CLI no files to change can be held to) leads its row, its
trigger's last error and the note it sends with why, whichever way it ended.

A lifecycle hook (`lifecycle:<id>`) keeps no run rows — only its last status — so there is no row
to settle for one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: What a declined run's row says. The owner is who reads it, and it was their Deny.
DECLINED_LINE = "You declined it, so it did not run."


def refused_line(refused: list[str]) -> str:
    """What the row of a run its limits refused says first: the calls, each tool once."""
    tools = list(dict.fromkeys(name for name in refused if name))
    count = len(refused)
    calls = "1 call" if count == 1 else f"{count} calls"
    return (
        f"Its limits refused {calls} ({', '.join(tools)}), so it may not have done all it was "
        "asked."
    )


def what_it_said(info: Any) -> str:
    """What the agent *info* describes said when it ended, as its run's row and its trigger's note
    say it: a run held back leads with why (:func:`_held_back`), and a run its limits refused with
    the calls they refused (:func:`refused_line`)."""
    result = str(getattr(info, "result", "") or "")
    refused = list(getattr(info, "refused", None) or [])
    said = f"{refused_line(refused)} {result}".strip() if refused else result
    return with_why_it_was_held_back(info, said)


def why_it_failed(info: Any) -> str:
    """Why the agent *info* describes failed, as its run's row and its trigger's note say it: a
    run held back leads with why (:func:`_held_back`). Empty for an agent that did not fail."""
    error = str(getattr(info, "error", "") or "")
    return with_why_it_was_held_back(info, error) if error else ""


def _held_back(info: Any) -> str:
    """Why the run *info* describes may do less than its step asks (`SubagentInfo.held_back`)."""
    held = getattr(info, "held_back", "")
    return held if isinstance(held, str) else ""


def with_why_it_was_held_back(info: Any, said: str) -> str:
    """*said*, about the run *info* describes, led by why it may do less than its step asks."""
    held = _held_back(info)
    return f"{held} {said}".strip() if held else said


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
        refused = list(getattr(info, "refused", None) or [])
        status = (
            "declined"
            if declined
            else ("failure" if error else ("refused" if refused else "success"))
        )
        if declined:
            said = DECLINED_LINE
        else:
            said = why_it_failed(info) if error else what_it_said(info)
        taken = ScheduleRunStore(home).settle_sync(
            trigger_id, agent_work_id(agent_id), status=status, summary=said, error=error
        )
        if taken and not declined:
            why = refused_line(refused) if status == "refused" else error
            _stamp(trigger_id, status=status, why=with_why_it_was_held_back(info, why), home=home)
        return taken
    except Exception:  # noqa: BLE001 - see the docstring: the row is bookkeeping about the run
        logger.warning("could not settle the run of trigger %s", trigger_id, exc_info=True)
        return False


def _stamp(trigger_id: str, *, status: str, why: str, home: Path) -> None:
    """Move the trigger's stamps for a run whose work has now ended as *status*, as a run that
    ended with its action moves them (`run_record.stamp_run`). Its row is still the last one, so
    `last_run_id` stays as it is."""
    from personalclaw.triggers.run_record import stamp_run
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home)
    row = store.get(trigger_id)
    if row is None:
        return
    live = row.trigger
    stamp_run(live, status=status, why=why)
    store.upsert(live)
