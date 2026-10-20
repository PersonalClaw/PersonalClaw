"""Incident mode holds a workflow run, the way it holds a loop (``loop.watchdog``).

A run is unattended work, so it honours the switch every unattended runner honours
(``guardrails.incident``). While the switch is on, the work in flight is withdrawn as a Pause
withdraws it: a dispatched stage's subagent is stopped and the stage goes back in the queue at the
same epoch, its attempt not counted. Nothing starts until the switch is off. The run's status stays
``running`` — a hold is not a Pause, which waits on its owner — and its views say why
(:func:`held_reason`). On its first step after the switch is off, the run carries on by itself. A
general loop is a workflow run, so its stages are held the same way.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from personalclaw.workflows.models import RunStatus

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)

#: What a running run's views say while incident mode holds it (``held`` on its status).
INCIDENT_HOLD = (
    "Held: incident mode is on, so this run starts no step and makes no model calls. "
    "It carries on by itself once incident mode is turned off."
)
#: What a stage's subagent is told when the hold stops it.
STOP_REASON = "Stopped: incident mode is on"
#: The reason on a stage the switch held as it was dispatched: the step did not run.
DISPATCH_HELD = "held: incident mode is on"


def active() -> bool:
    """Whether incident mode is on — the one read every step of a run makes."""
    from personalclaw.guardrails.incident import incident_active

    return incident_active()


def held_reason(status: RunStatus) -> str:
    """:data:`INCIDENT_HOLD` for a run incident mode is holding right now, else ``""``.

    Only a ``running`` run is held: every other status is already not working, and saying "held"
    there would name a cause it lacks.
    """
    return INCIDENT_HOLD if status == RunStatus.RUNNING and active() else ""


async def hold(ctl: RunController, *, wake_secs: float) -> None:
    """Hold *ctl*'s run for one step. The first step of a hold withdraws the work in flight; every
    step sets the time the tick loop looks again, so a held run sleeps rather than spins."""
    ctl._incident_wake = time.time() + wake_secs
    if ctl._incident_held:
        return
    ctl._incident_held = True
    await ctl._withdraw_inflight(reason=STOP_REASON)
    logger.info("workflow run %s held: incident mode is on", ctl.run.id)
    ctl._publish("workflow_run_update", {"status": ctl.run.status.value, "held": INCIDENT_HOLD})


def carry_on(ctl: RunController) -> None:
    """The switch is off: *ctl*'s run goes on from where the hold stopped it."""
    ctl._incident_held = False
    ctl._incident_wake = 0.0
    logger.info("workflow run %s carries on: incident mode is off", ctl.run.id)
    ctl._publish("workflow_run_update", {"status": ctl.run.status.value, "held": ""})
