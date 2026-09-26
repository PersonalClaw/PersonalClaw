"""What keeps a working node's stall clock running (WF2-R5).

`timeout_stall` is meant to catch a SILENT node, not a slow one, so a node that is working has to
be able to say so. Two things say it, and both live here so one module answers "what counts as
progress":

* a nested run's heartbeat: `wait_with_progress` feeds the parent's stall clock while the child
  works, so a subworkflow that legitimately takes ten minutes is not killed as wedged;
* a model call that is still streaming: the guard stamps every event a provider sends on the call
  (`guardrails.calls.ModelCall.last_event_at`) and `last_heard` reads it. Before that, nothing a
  model-calling step did reached the clock. Measured with the stall window at 4s: a best-of-n
  whose model was streaming an 8s answer failed "no progress for 4s (timeout_stall)" 5s in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.guardrails.calls import CallLog

#: How often a long wait feeds the parent's stall clock. Well under any sane `timeout_stall`, so a
#: working child can never be mistaken for a silent one.
HEARTBEAT_SECS = 0.5


async def wait_with_progress(controller: Any, timeout: float, on_progress: Any) -> Any:
    """Wait for a child run, feeding the parent's stall clock while it works.

    Each tick is one function call, which costs nothing next to a child run.
    """
    if not callable(on_progress):
        return await controller.wait_for_terminal(timeout=timeout)

    task = asyncio.ensure_future(controller.wait_for_terminal(timeout=timeout))
    while not task.done():
        on_progress()
        # `asyncio.wait` rather than a sleep-then-check: it returns as soon as the child settles, so
        # a fast child is not padded by the heartbeat interval.
        await asyncio.wait({task}, timeout=HEARTBEAT_SECS)
    return task.result()


def last_heard(last_progress: float, calls: CallLog) -> float:
    """When a node last showed it was working: its own progress, or its calls' latest event."""
    return max(last_progress, calls.last_activity or 0.0)
