"""A chat turn that runs on its own has a time limit, and says so when it runs past it.

A turn started from a chat's queue (a message sent while another turn ran, a retry, a message
from the channel the chat is linked to), from a subagent's report, or from a loop's nudge is
bounded, so that a wedged turn cannot hold its chat forever. The clock stops while the turn waits
on its owner's answer to one of its calls: the approval's own window bounds that wait, and it is
not the turn running long. A turn that runs past its limit is stopped, and ends in an error that
says so (``turn_endings.past_limit_notice``), in the chat and on its linked channel: not in the
notice for a reply cut short by a fault, and not as a Stop nobody pressed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from personalclaw.cancellation import OutOfTime, wait_for_unpaused

logger = logging.getLogger(__name__)

#: The turns being stopped at their limit: the task running the turn → the limit, in seconds.
#: Set just before the turn is cancelled, read by the turn as it ends (:func:`limit_passed`), and
#: cleared once it has ended. Keyed by the turn's own task, never by its chat: the chat's next
#: turn can start while this one is still being stopped, and it must not read this one's ending.
_STOPPING: dict[asyncio.Future[Any], float] = {}


async def run_within(
    state: Any, session: Any, turn: Coroutine[Any, Any, None], limit: float
) -> None:
    """Run *turn* — ``run_chat`` for *session* — for at most *limit* seconds of its own work.

    Out of time, the turn is cancelled and ends saying it ran past its limit; nothing is raised,
    because the turn has already said how it ended. Cancelled itself, it cancels the turn, which
    ends as any stopped turn does.
    """
    key = str(getattr(session, "key", "") or "")
    task = asyncio.ensure_future(turn)

    def _stopping() -> None:
        _STOPPING[task] = limit

    try:
        await wait_for_unpaused(
            task,
            limit,
            paused=lambda: state.waiting_on_owner(key),
            what=f"chat turn {key}",
            on_timeout=_stopping,
        )
    except OutOfTime:
        logger.warning("The turn of %s ran past its %.0f s limit and was stopped", key, limit)
    finally:
        _STOPPING.pop(task, None)


def limit_passed() -> float:
    """The limit the running turn is being stopped at, in seconds; 0.0 for any other turn."""
    task = asyncio.current_task()
    return _STOPPING.get(task, 0.0) if task is not None else 0.0
