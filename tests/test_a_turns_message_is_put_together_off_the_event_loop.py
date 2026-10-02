"""A chat turn puts its message together off the event loop, so the gateway answers meanwhile.

Putting a turn's message together waits on the embedding model: the turn's memory, its skill match
and active recall each embed the message. ``run_chat`` did it inline, on the event loop that serves
every request, so with a model answering in 3 s a health check sent as a turn began waited 6 s.

Driven through the real ``run_chat`` with a mocked client; the assembly is stood in for by one that
takes as long as those round trips do.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.context_engine import AssembledContext
from personalclaw.context_headroom import Headroom, HeadroomState, Window
from personalclaw.dashboard import chat_runner
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

#: How long the stand-in assembly waits, as the embedding round trips of a slow model do.
ASSEMBLY_SECS = 0.8

_WINDOW = Window(
    tokens=32768,
    output_reserve_tokens=1024,
    input_tokens=31744,
    source="served",
    ref="local:test-model",
)


def _client() -> AsyncMock:
    client = AsyncMock()
    client.context_usage_pct = MagicMock(return_value=10.0)

    async def _stream(_msg):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The dishwasher goes left of the sink.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn", output_tokens=9)

    client.stream = _stream
    client.stream_command = _stream
    return client


@pytest.fixture
def turn(tmp_path, monkeypatch):
    """``run() -> (start, end) of the assembly`` — one real turn, its assembly slow."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = MagicMock()
    state.consolidator = None
    state._hook_store = None
    import personalclaw.trust_mode as _tm

    _tm.disable_yolo()
    session = state.get_or_create_session("s-assembly")
    assembled: list[tuple[float, float]] = []

    async def _resolve(model_ref="", *, serving=None):  # noqa: ANN001, ANN202
        return _WINDOW

    def _assemble(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        start = time.monotonic()
        time.sleep(ASSEMBLY_SECS)
        assembled.append((start, time.monotonic()))
        return AssembledContext(message="Where does the dishwasher go?")

    def _check(_assembled, **kw):  # noqa: ANN001, ANN003, ANN202
        return Headroom(
            state=HeadroomState.FITS,
            window=kw["window"],
            assembled_tokens=1,
            raw_tokens=1,
            text="Where does the dishwasher go?",
        )

    monkeypatch.setattr(chat_runner, "resolve_window", _resolve)
    monkeypatch.setattr(chat_runner, "assemble_context", _assemble)
    monkeypatch.setattr(chat_runner, "check_headroom", _check)

    async def run() -> list[tuple[float, float]]:
        state.sessions.get_or_create = AsyncMock(return_value=(_client(), True, False))
        session.append("user", "Where does the dishwasher go?", "msg msg-u")
        await chat_runner.run_chat(state, session, "Where does the dishwasher go?")
        return assembled

    return run, session


async def _ticks_while(task: asyncio.Future) -> list[tuple[float, float]]:
    """Each 20 ms pause this coroutine took while ``task`` ran: when it began, when it ended."""
    ticks: list[tuple[float, float]] = []
    while not task.done():
        start = time.monotonic()
        await asyncio.sleep(0.02)
        ticks.append((start, time.monotonic()))
    return ticks


@pytest.mark.asyncio
async def test_the_event_loop_runs_while_a_turns_message_is_put_together(turn):
    """🔴 Red before: the assembly ran on the event loop, which stopped for all of it."""
    run, session = turn
    task = asyncio.ensure_future(run())

    ticks = await _ticks_while(task)
    assembled = await task

    assert assembled, "premise: the message was put together"
    began, ended = assembled[0]
    # How much of each pause fell inside the assembly: on a stopped loop, one pause spans it all.
    inside = [min(end, ended) - max(start, began) for start, end in ticks]
    worst = max(inside, default=0.0) - 0.02
    assert worst < 0.3, f"the event loop stopped for {worst:.2f}s while the message was assembled"
    replies = [m for m in session.messages if m.get("role") == "assistant"]
    assert replies and "left of the sink" in replies[-1]["content"], "and the turn was answered"
