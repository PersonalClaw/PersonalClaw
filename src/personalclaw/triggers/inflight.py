"""The actions this process's runs have under way, so the deadline reaper can end one.

🔴 **THE DEFECT THIS CLOSES: a reaped run kept running.** The reaper (`reaper.reap_one`) found a
run past its deadline, released its claim and wrote its `timeout` row, and the run went on. Its
action was still running in the gateway that started it, and with the claim gone the next fire
started a second one beside it. When the first ended, its own record wrote a second row for the
one run.

The reaper could not end it, because nothing it could reach was the run alone: a scheduled fire
runs inside the tick loop's own task (`loop._execute_delivery` awaits it), and a Run now inside
its request's, so cancelling the task the action ran in would have cancelled the scheduler, or
the request.

So a run's action runs as a task of its own (:func:`acting`), kept here by trigger id while it
runs, and the reaper ends it (:func:`cut`): only the action's task is cancelled, and the run that
awaited it sees :class:`CutAtDeadline` where a stop would be a cancellation, and goes on. The
reaper writes the run's one row; the run itself records nothing more. A stop that cancels the
run's own task still reaches the action as a cancellation, and is recorded as a stop.

Process-local on purpose: only this process can end what this process runs. A claim another live
process holds is left to that claim's expiry (`reaper.reap_one`).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CutAtDeadline(Exception):
    """The action a run awaited was ended at the run's deadline (`reaper.reap_one`).

    An `Exception`, not a cancellation: the task that awaited the action was not stopped, and must
    go on (the tick loop to its next fire, a Run now's request to its answer).
    """


@dataclass(eq=False)
class _Action:
    loop: asyncio.AbstractEventLoop
    task: asyncio.Future[Any]
    started: float = field(default_factory=time.time)
    cut: bool = False


_LOCK = threading.Lock()
#: Each trigger's actions under way, oldest first: a run beside it (`overlap: allow`, an answer's
#: run beside a tick's) has its own.
_RUNNING: dict[str, list[_Action]] = {}


async def acting(trigger_id: str, action: Awaitable[T]) -> T:
    """Await *action* as a task of its own, kept under *trigger_id* while it runs.

    Raises :class:`CutAtDeadline` when :func:`cut` ended it, and a cancellation when the task
    awaiting it was itself cancelled (a stop, a restart), which cancels the action too.
    """
    task = asyncio.ensure_future(action)
    entry = _Action(loop=asyncio.get_running_loop(), task=task)
    with _LOCK:
        _RUNNING.setdefault(trigger_id, []).append(entry)
    try:
        result = await task
    except asyncio.CancelledError:
        here = asyncio.current_task()
        # Cut, and only cut: a stop of the awaiting task while the cut lands is still a stop.
        if entry.cut and task.cancelled() and not (here is not None and here.cancelling()):
            raise CutAtDeadline(trigger_id) from None
        raise
    finally:
        with _LOCK:
            kept = [e for e in _RUNNING.get(trigger_id, ()) if e is not entry]
            if kept:
                _RUNNING[trigger_id] = kept
            else:
                _RUNNING.pop(trigger_id, None)
    if entry.cut:
        # It ended as the cut landed: the reaper has already written the run as cut off, and a
        # row of its own beside that one would be two rows for the one run.
        raise CutAtDeadline(trigger_id)
    return result


def cut(trigger_id: str, *, older_than: float) -> int:
    """End each action *trigger_id* has under way in this process that started before
    *older_than* (a wall-clock time). Returns how many it ended. Never raises.

    Safe from any thread: the reaper's sweep runs in one (`reaper.run_forever`), so each is
    cancelled on its own loop. A run beside the reaped one that started since is left running.
    """
    with _LOCK:
        due = [
            e
            for e in _RUNNING.get(trigger_id, ())
            if e.started < older_than and not e.task.done() and not e.cut
        ]
        for entry in due:
            entry.cut = True
    ended = 0
    for entry in due:
        try:
            entry.loop.call_soon_threadsafe(entry.task.cancel)
            ended += 1
        except RuntimeError:
            # Its loop has closed: nothing of it is still running to end.
            logger.debug("the action of %s has no loop left to end it on", trigger_id)
    return ended
