"""A trigger's `launched` run, told how the work it started went.

A fire whose action starts an agent (`run-prompt`, `invoke-agent`) or a workflow run
(`run-workflow`) records its run the moment the work starts, as `launched` (or `queued`, behind a
run of the same workflow in flight): started is not succeeded, and nothing is known yet. The
work's ending reached the trigger's route as a note and nowhere else, so the trigger's history
said "launched" for good, beside a note saying the same run had finished or failed.

The row names the work (`ActionResult.work_id`: `subagent:<id>`, `workflow:<id>`), and when it
ends this says how, on that row (`ScheduleRunStore.settle_sync`): for an agent from the gateway's
subagent completion (:func:`settle_agent_run`), for a workflow run from its terminal write
(:func:`settle_workflow_run`):

* `success`, and what the agent said, or what the run said it produced;
* `failure`, and why — a time limit, a crash, a start nobody answered in time, a step that failed;
* `refused`, when the run's own limits refused calls it made (`SubagentInfo.refused`): what its
  Allow said it may not do, or an approval nobody was there to give. It has not done all it was
  asked, so it is not a success, and its limits held, so it is not a failure: the row names the
  calls, then what the agent said;
* `declined`, when the owner answered its start with Deny (`SubagentInfo.declined`), or an
  approval its workflow run needed. Their own decision is not a failure: the row says they
  declined it, and no note calls it one;
* `stopped`, when someone stopped the work before it finished: its owner stopped the agent
  (`SubagentInfo.cancelled`) or cancelled the workflow run. Their own decision too: the row says
  what stopped it, and no note goes out, since whoever stopped it knows;
* `interrupted`, when the gateway's stop or restart cut the agent off (`settle_interrupted`). The
  agent's own ending for that is "cancelled", which read as the run failing; the stop closes the
  row first, with what stopped it, and the agent's ending then finds nothing to settle.

**The ending is the run's.** The row takes the exit the work ended with (`ScheduleRun.trigger`),
and its trigger takes the ending as it takes a run that ended with its action
(`run_record.record_ending`): its stamps — `last_success_at`, or `last_failure_at` with why in
`last_error_summary` — and, for a fire, the lifecycle decision (`autopause.ending_decision`). So a
failure counts toward the pause as a failed command does, a success starts the count over, and a
refusal, a Deny, a stop and a restart's cut count for nothing either way. The row kept the fire's
own exit, a clean one, so an automation whose agent failed on every fire never paused, and told
the owner so on every fire. A Deny and a stop move no stamp: nothing went wrong.

A run that may do less than the step that started it asks (`SubagentInfo.held_back`: a working
folder its owner has not trusted, an agent CLI no files to change can be held to) leads its row, its
trigger's last error and the note it sends with why, whichever way it ended.

A lifecycle hook (`lifecycle:<id>`) keeps no run rows — only its last status — so there is no row
to settle for one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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


#: What a stopped run's row says when whatever stopped it gave no reason of its own.
STOPPED_LINE = "It was stopped before it finished."

#: `(trigger, decision) -> None`, handed a trigger an ending stopped (`run_record.record_ending`).
OnAttention = Callable[[Any, Any], None]


def settle_agent_run(
    info: Any, *, base_dir: Path | None = None, on_attention: OnAttention | None = None
) -> bool:
    """Write how the agent *info* describes ended onto the run of the trigger that started it, and
    take that ending onto the trigger (:func:`_took`). *on_attention* is handed the trigger when
    the ending stopped it: the gateway raises the card that says so.

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
        # Its owner stopped it (or a stop of what started it did): `SubagentInfo.cancelled`, which
        # a stop sets and nothing else does. Its error is the stop's own reason, not a failure.
        stopped = not declined and getattr(info, "cancelled", False) is True
        error = "" if declined or stopped else str(getattr(info, "error", "") or "")
        refused = list(getattr(info, "refused", None) or [])
        status = (
            "declined"
            if declined
            else (
                "stopped"
                if stopped
                else "failure" if error else "refused" if refused else "success"
            )
        )
        if declined:
            said = DECLINED_LINE
        elif stopped:
            said = str(getattr(info, "error", "") or "") or STOPPED_LINE
        else:
            said = why_it_failed(info) if error else what_it_said(info)
        work_id = agent_work_id(agent_id)
        taken = ScheduleRunStore(home).settle_sync(
            trigger_id,
            work_id,
            status=status,
            summary=said,
            error=error,
            exit_type=_exit_for(status),
        )
        if taken and not (declined or stopped):
            why = refused_line(refused) if status == "refused" else error
            _took(
                trigger_id,
                work_id,
                status=status,
                why=with_why_it_was_held_back(info, why),
                home=home,
                on_attention=on_attention,
            )
        return taken
    except Exception:  # noqa: BLE001 - see the docstring: the row is bookkeeping about the run
        logger.warning("could not settle the run of trigger %s", trigger_id, exc_info=True)
        return False


def settle_workflow_run(
    trigger_id: str,
    run_id: str,
    ending: Any,
    *,
    error: str,
    summary: str,
    base_dir: Path | None = None,
    on_attention: OnAttention | None = None,
) -> bool:
    """Write how the workflow run *run_id* that trigger *trigger_id* started ended onto that
    trigger's run, and take that ending onto the trigger (:func:`_took`), from the run's terminal
    write (`workflows.run_finish.report_to_its_trigger`).

    *ending* is the run's `RunStatus`: it finished (`success`, with *summary*, what it said it
    produced), its owner cancelled it (`stopped`) or declined an approval it needed (`declined`),
    or it failed or gave up on a step (`failure`); *error* says why, for every ending but the
    first. Returns whether its row took the ending, as :func:`settle_agent_run` does. Never raises.
    """
    if not trigger_id or not run_id:
        return False
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX
        from personalclaw.schedule_history import ScheduleRunStore
        from personalclaw.workflows.models import RunStatus, run_ending, run_work_id

        if trigger_id.startswith(LIFECYCLE_TRIGGER_PREFIX):
            return False
        home = base_dir if base_dir is not None else config_dir()
        status = (
            "success"
            if ending == RunStatus.COMPLETE
            else (
                "stopped"
                if ending == RunStatus.CANCELLED
                else "declined" if ending == RunStatus.DECLINED else "failure"
            )
        )
        finished = summary or f"The workflow run {run_ending(RunStatus.COMPLETE)}."
        said = finished if status == "success" else error
        failure = error if status == "failure" else ""
        work_id = run_work_id(run_id)
        taken = ScheduleRunStore(home).settle_sync(
            trigger_id,
            work_id,
            status=status,
            summary=said,
            error=failure,
            exit_type=_exit_for(status),
        )
        if taken and status in ("success", "failure"):
            _took(
                trigger_id,
                work_id,
                status=status,
                why=failure,
                home=home,
                on_attention=on_attention,
            )
        return taken
    except Exception:  # noqa: BLE001 - see settle_agent_run
        logger.warning("could not settle the workflow run of trigger %s", trigger_id, exc_info=True)
        return False


def settle_interrupted(trigger_id: str, work_id: str, *, why: str, at: float, home: Path) -> bool:
    """Write onto the `launched` run of *trigger_id* that a stop cut its work off, and *why*.

    The row's status is `reaper.RESTART_INTERRUPTED_STATUS`, written as its word here so the status
    rail reads what this module can write. Returns whether the row took it
    (`ScheduleRunStore.settle_sync`). The caller (`reaper.record_stopped_work`) moves the trigger's
    stamps and keeps the card.
    """
    from personalclaw.schedule_history import ScheduleRunStore

    return ScheduleRunStore(home).settle_sync(
        trigger_id,
        work_id,
        status="interrupted",
        error=why,
        finished_at=at,
        exit_type=_exit_for("interrupted"),
    )


def _exit_for(status: str) -> str:
    """The typed exit an ending gives its row (`ScheduleRun.trigger`): a success and a failure end
    as a run that ran its action does (`autopause.ExitType`), so they count toward the pause and
    start it over as that run's would. Every other ending carries its outcome, which is no exit and
    decides nothing (`autopause.ending_decision`)."""
    from personalclaw.triggers.autopause import ExitType
    from personalclaw.triggers.history import SCHEDULE_STATUS_TO_OUTCOME

    if status == "success":
        return ExitType.OK.value
    if status == "failure":
        return ExitType.FAILED.value
    return SCHEDULE_STATUS_TO_OUTCOME.get(status, status)


def _took(
    trigger_id: str,
    work_id: str,
    *,
    status: str,
    why: str,
    home: Path,
    on_attention: OnAttention | None,
) -> None:
    """Take the ending of the work *work_id* onto its trigger: its stamps, and for a fire the
    lifecycle decision (`run_record.record_ending`), on the trigger where it lives (`routed`: a
    trigger an app serves keeps its stamps and its state in that app's store)."""
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers.routing import routed
    from personalclaw.triggers.run_record import record_ending
    from personalclaw.triggers.store import TriggerStore

    record_ending(
        trigger_id,
        work_id,
        status=status,
        why=why,
        store=routed(TriggerStore(base_dir=home)),
        runs=ScheduleRunStore(home),
        on_attention=on_attention,
    )
