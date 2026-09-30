"""The model requests a provider has open, so its ``cancel()`` can close them.

An HTTP model client streams its answer from inside ``complete()`` / ``stream()``, which the caller
iterates; a stop arrives on ``cancel()``, from another task. With nothing between the two, the
request was out of reach: ``cancel()`` could only say ``"no_turn"``, and Stop, Pause and an
incident hold ended a turn when the model's request returned — up to the instance's Request
Timeout, 300 s on a slow local model — while the model kept generating an answer nobody would read.

:meth:`InFlightRequests.relay` runs the provider's own stream in a task of its own and hands its
events on; :meth:`InFlightRequests.cancel` cancels that task, which ends the request where it is
(waiting to connect, waiting for the first byte, or mid-answer): the HTTP client's context closes
the connection, and a server that stops on a closed connection (Ollama does) stops generating. The
caller's stream then ends with a terminal event that says it was cancelled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from personalclaw.acp.types import STOP_REASON_CANCELLED
from personalclaw.llm.base import EVENT_COMPLETE, CancelOutcome, LLMEvent

_END = object()


class InFlightRequests:
    """The requests one provider instance has open: each is relayed, and each can be closed."""

    def __init__(self) -> None:
        self._pumps: set[asyncio.Task[None]] = set()
        self._closed: set[asyncio.Task[None]] = set()

    async def relay(self, source: AsyncIterator[LLMEvent]) -> AsyncIterator[LLMEvent]:
        """Yield what *source* yields, running it in a task :meth:`cancel` can stop.

        What *source* raises is raised here. When :meth:`cancel` closed it, the stream ends with
        ``EVENT_COMPLETE`` carrying ``stop_reason="cancelled"`` and no usage, since a request
        closed mid-answer never reports what it used. A caller that stops reading (a ``break``,
        a timeout, ``aclose()``) closes the request too.
        """
        queue: asyncio.Queue[object] = asyncio.Queue()

        async def pump() -> None:
            async for event in source:
                queue.put_nowait(event)

        def ended(done: asyncio.Task[None]) -> None:
            # Read here, whatever happens next: a caller that stopped reading never reads it, and
            # an exception nobody retrieves is logged as one that was lost.
            if not done.cancelled():
                done.exception()
            queue.put_nowait(_END)

        task = asyncio.create_task(pump())
        task.add_done_callback(ended)
        self._pumps.add(task)
        try:
            while True:
                item = await queue.get()
                if item is not _END:
                    yield item  # type: ignore[misc]
                    continue
                if task.cancelled():
                    if task in self._closed:
                        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED)
                    return
                failure = task.exception()
                if failure is not None:
                    raise failure
                return
        finally:
            self._pumps.discard(task)
            self._closed.discard(task)
            if not task.done():
                task.cancel()

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Close every open request. ``"no_turn"`` when none is open.

        With a positive *wait_ack_timeout* it waits that long for each to finish closing, and
        answers ``"timeout"`` if one has not; without one, the close is under way when this
        returns (the request's task is cancelled, which takes effect at its next await).
        """
        open_now = [task for task in self._pumps if not task.done()]
        if not open_now:
            return "no_turn"
        for task in open_now:
            self._closed.add(task)
            task.cancel()
        if wait_ack_timeout > 0:
            _, still_open = await asyncio.wait(open_now, timeout=wait_ack_timeout)
            if still_open:
                return "timeout"
        return "acked"
