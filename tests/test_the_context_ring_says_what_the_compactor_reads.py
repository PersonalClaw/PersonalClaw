"""The chat's context ring says what the gateway's compaction reads, and says why when it can't.

The ring is drawn from the ``context_usage`` frame. That frame carried a percentage or ``None``
and nothing else, so an absent measurement had one explanation on screen — "no context window is
declared for this model" — whatever the reason. A chat served a 32,768-token window by its local
runtime showed exactly that after a ``/compact`` on a runtime that had not answered yet, and again
once the session was restarted at the threshold, while the chat said "Auto-compacted at 71% of the
context window". The frame now also names the window the turn was served with (``None`` when
nothing declared or served one), the chat keeps what it was last told, and session detail serves
that, so a page opened later draws the same ring.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.llm.events import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)

SERVED = 32_768


def _state(tmp_path, monkeypatch, client: Any):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    sessions = MagicMock(count=0)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


def _client(stream, *, pct: float | None, window: int | None) -> AsyncMock:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=pct)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    client.stream_command = MagicMock(side_effect=lambda *a, **kw: stream())
    client.compacts_in_process = True
    # The runtime's model provider answers the window it serves, as a local runtime does.
    provider = MagicMock()
    provider.served_context_window = AsyncMock(return_value=window)
    provider.request_only = False
    client.model_provider = provider
    return client


async def _turn(state, session, message: str) -> None:
    from personalclaw.dashboard.chat_runner import run_chat

    session._trust = True
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, message)
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)


def _usage_frames(state) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "context_usage"]


async def _answer():
    yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="The failure pattern is a read timeout.")
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _compact():
    yield AgentEvent(
        kind=EVENT_COMPACTION_STATUS,
        text="completed",
        title="freed 10% of the conversation (74,611 → 67,481 characters)",
    )
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


@pytest.mark.asyncio
async def test_a_measured_turn_names_the_window_it_was_served_with(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _client(_answer, pct=71.0, window=SERVED))
    session = state.get_or_create_session("chat-1")
    await _turn(state, session, "what is the failure pattern?")
    assert _usage_frames(state)[-1] == {"session": "chat-1", "pct": 71.0, "window": SERVED}


@pytest.mark.asyncio
async def test_an_unmeasured_compact_still_names_the_served_window(tmp_path, monkeypatch):
    """A runtime that has not answered yet measured nothing — and its window is still known, so
    the ring must not be told there is none."""
    state = _state(tmp_path, monkeypatch, _client(_compact, pct=None, window=SERVED))
    session = state.get_or_create_session("chat-1")
    await _turn(state, session, "/compact")
    assert _usage_frames(state)[-1] == {"session": "chat-1", "pct": None, "window": SERVED}


@pytest.mark.asyncio
async def test_a_model_with_no_known_window_is_said_to_have_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "personalclaw.context_headroom.binding_declared_window", lambda ref: None, raising=False
    )
    state = _state(tmp_path, monkeypatch, _client(_answer, pct=None, window=None))
    session = state.get_or_create_session("chat-1")
    await _turn(state, session, "hello")
    assert _usage_frames(state)[-1] == {"session": "chat-1", "pct": None, "window": None}


@pytest.mark.asyncio
async def test_a_restart_at_the_threshold_keeps_the_window_and_clears_the_reading(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch, _client(_answer, pct=71.0, window=SERVED))
    state.sessions.set_compact_callback = MagicMock()
    session = state.get_or_create_session("chat-1")
    await _turn(state, session, "what is the failure pattern?")
    state.wire_session_compact_callback()
    restarted = state.sessions.set_compact_callback.call_args[0][0]

    await restarted("dashboard:chat-1", 71.0)

    assert _usage_frames(state)[-1] == {"session": "chat-1", "pct": None, "window": SERVED}
    assert session.context_usage == {"pct": None, "window": SERVED}


@pytest.mark.asyncio
async def test_session_detail_serves_what_the_ring_was_last_told(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from tests.chat_test_helpers import _make_app

    state = _state(tmp_path, monkeypatch, _client(_answer, pct=71.0, window=SERVED))
    session = state.get_or_create_session("chat-1")
    async with TestClient(TestServer(_make_app(state))) as c:
        before = await (await c.get("/api/chat/sessions/chat-1")).json()
        await _turn(state, session, "what is the failure pattern?")
        after = await (await c.get("/api/chat/sessions/chat-1")).json()
    assert before["context_usage"] is None
    assert after["context_usage"] == {"pct": 71.0, "window": SERVED}
