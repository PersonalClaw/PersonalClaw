"""A chat turn nobody is watching is stopped when it goes quiet, and says so when it is.

A turn the owner asked for in a surface they are at runs until it is done, wherever it started:
a message typed into an idle chat, one typed while another turn ran and queued behind it, a reply
they typed in the Inbox, and the retry of any of them. Nothing here bounds it; their Stop does.

Every other turn is bounded, so that a wedged one cannot hold its chat forever: a subagent's
report, a loop's nudge, an automation's or a heartbeat's result, a message from the channel the
chat is linked to, and the retry of any of them. The bound is on silence, not on how long the turn
runs (:func:`run_within`): every event that shows the turn moving (:data:`PROGRESS`) starts its
clock again, and it is stopped only once it has shown none for the whole limit. The clock also
stops while the turn waits on its owner's answer to one of its calls (the approval's own window
bounds that wait) and while one of its calls is running (that tool's own timeout bounds it): a
long test run or a ``wait`` is the turn working, not stuck. A turn stopped at its limit ends in an
error that says so (``turn_endings.past_limit_notice``), in the chat and on its linked channel:
not in the notice for a reply cut short by a fault, and not as a Stop nobody pressed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from personalclaw.cancellation import PAUSED_CLOCK_TICK_SECS, cancel_and_wait
from personalclaw.llm.events import (
    EVENT_CARRIED_ON,
    EVENT_COMPACTION_STATUS,
    EVENT_PERMISSION_REQUEST,
    EVENT_STEER,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)

logger = logging.getLogger(__name__)

#: The turn events that show a bounded turn moving, each of which starts its quiet clock again:
#: what it writes and thinks, the calls it makes and what they return, an approval it asks for, its
#: history compacted between two steps, a message of the owner's it took, and its going on past a
#: call they refused. Bookkeeping (what the turn spent, the model it fell over to) shows nothing
#: of the work.
PROGRESS = frozenset(
    {
        EVENT_TEXT_CHUNK,
        EVENT_THINKING_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_CALL_UPDATE,
        EVENT_TOOL_RESULT,
        EVENT_PERMISSION_REQUEST,
        EVENT_COMPACTION_STATUS,
        EVENT_STEER,
        EVENT_CARRIED_ON,
    }
)


@dataclass
class _Watch:
    """What a bounded turn has shown of its work: how many progress events, and its calls that
    were made and have not returned yet."""

    beats: int = 0
    open_calls: set[str] = field(default_factory=set)


#: The bounded turns running now: the task running the turn → what it has shown. Keyed by the
#: turn's own task, which every event of it is read on (``run_chat``'s stream), never by its chat.
_WATCHED: dict[asyncio.Future[Any], _Watch] = {}

#: The turns being stopped at their limit: the task running the turn → the limit, in seconds.
#: Set just before the turn is cancelled, read by the turn as it ends (:func:`limit_passed`), and
#: cleared once it has ended. Keyed by the turn's own task, never by its chat: the chat's next
#: turn can start while this one is still being stopped, and it must not read this one's ending.
_STOPPING: dict[asyncio.Future[Any], float] = {}


async def run_within(
    state: Any, session: Any, turn: Coroutine[Any, Any, None], idle_limit: float
) -> None:
    """Run *turn* — ``run_chat`` for *session* — until it shows no work for *idle_limit* seconds.

    The quiet clock starts again on every event in :data:`PROGRESS` the turn's stream carries
    (:func:`saw`), and does not run while one of its calls is open or waits on the owner's answer
    (``state.waiting_on_owner``). It is read every ``PAUSED_CLOCK_TICK_SECS``, and a tick that
    wakes late counts as one tick: time the event loop could not run is not the turn's silence.

    Out of time, the turn is cancelled and ends saying it was stopped; nothing is raised, because
    the turn has already said how it ended. Cancelled itself, it cancels the turn and waits for it
    to end before the cancel goes on, so a caller that acts on how the turn ended finds it said.
    """
    key = str(getattr(session, "key", "") or "")
    what = f"chat turn {key}"
    task = asyncio.ensure_future(turn)
    watch = _Watch()
    _WATCHED[task] = watch
    clock = asyncio.get_running_loop()
    quiet = 0.0
    seen = watch.beats
    try:
        try:
            while quiet < idle_limit:
                tick = min(PAUSED_CLOCK_TICK_SECS, idle_limit - quiet)
                began = clock.time()
                done, _pending = await asyncio.wait({task}, timeout=tick)
                if task in done:
                    task.result()
                    return
                if watch.beats != seen:
                    seen = watch.beats
                    quiet = 0.0
                elif not watch.open_calls and not state.waiting_on_owner(key):
                    quiet += min(clock.time() - began, tick)
        except asyncio.CancelledError:
            await cancel_and_wait([task], what=what)
            raise
        _STOPPING[task] = idle_limit
        await cancel_and_wait([task], what=what)
        logger.warning("The turn of %s showed no work for %.0f s and was stopped", key, idle_limit)
    finally:
        _STOPPING.pop(task, None)
        _WATCHED.pop(task, None)


def _watch() -> _Watch | None:
    task = asyncio.current_task()
    return _WATCHED.get(task) if task is not None else None


def saw(kind: str, call_id: str = "") -> None:
    """The running turn's stream carried an event of *kind* (for a call, of the call *call_id*).

    A no-op for a turn nothing bounds. A call is open from its ``tool_call`` to its
    ``tool_result``; a call with no id cannot be matched to its result, so it counts as progress
    only."""
    watch = _watch()
    if watch is None or kind not in PROGRESS:
        return
    watch.beats += 1
    if call_id and kind == EVENT_TOOL_CALL:
        watch.open_calls.add(call_id)
    elif call_id and kind == EVENT_TOOL_RESULT:
        watch.open_calls.discard(call_id)


def call_ended(call_id: str) -> None:
    """The running turn's call *call_id* ended with no result to come: it was refused."""
    watch = _watch()
    if watch is not None and call_id:
        watch.beats += 1
        watch.open_calls.discard(call_id)


def bounded() -> bool:
    """Whether the running turn is bounded (:func:`run_within`): its retry is bounded the same."""
    return _watch() is not None


def limit_passed() -> float:
    """The limit the running turn is being stopped at, in seconds; 0.0 for any other turn."""
    task = asyncio.current_task()
    return _STOPPING.get(task, 0.0) if task is not None else 0.0
