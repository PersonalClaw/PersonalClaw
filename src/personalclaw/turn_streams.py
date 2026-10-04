"""Reading a turn's stream so that it is closed when its reader stops.

A turn reaches its reader as a stream of events: an async generator, and usually several, each
passing on the one beneath it (the chat runner's usage rows over the provider's metering over
the agent CLI's session). A reader often stops before the stream has ended: at its terminal
``EVENT_COMPLETE``, at a refusal, on an error or a cancel. A stream left like that is closed only
when the interpreter collects it, which may be at once or much later, and until then it keeps
what it holds: an agent CLI's session, which sends no other prompt meanwhile, an open request, a
local model's turn, a spend hold.

So whatever stops reading a stream closes it, and a stream that passes another one on reads that
one with :func:`closing_stream`, so that closing the outer stream closes every stream beneath it,
at once. A reader whose reading is too long to sit in a block of its own closes the stream with
:func:`close_stream` from the cleanup it runs on every way out. Standard library only, so every
layer can import it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeVar

_E = TypeVar("_E")


async def close_stream(events: AsyncIterator[object]) -> None:
    """Close *events* now, when it is an async generator, as every provider's stream is; an
    iterator of another kind has nothing of its own to close."""
    if isinstance(events, AsyncGenerator):
        await events.aclose()


@asynccontextmanager
async def closing_stream(events: AsyncIterator[_E]) -> AsyncIterator[AsyncIterator[_E]]:
    """Read *events* inside the block; leaving the block, by any way out, closes it
    (:func:`close_stream`)."""
    try:
        yield events
    finally:
        await close_stream(events)
