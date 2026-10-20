"""A turn's "Turn complete" line is still there after a reload.

Measured before: the line (events, tool calls, context, cost, tokens) went out only as a live
``activity_event``, so reloading a chat took the Telemetry row out of every turn's details, even
though the turn's telemetry record was persisted on its last assistant message. The runner now
composes that sentence ONCE, sends it live, and persists the same string in the record, so the
chat detail a reload reads carries the sentence the user saw. Driven through a real ``run_chat``
turn, read back off disk, and served again after the session was evicted.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_session_map import TURN_TELEMETRY_KEY
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

_COMPLETE = AgentEvent(
    kind=EVENT_COMPLETE,
    stop_reason="end_turn",
    input_tokens=1200,
    output_tokens=340,
    cache_read_tokens=800,
    cache_creation_tokens=64,
    cost_usd=0.0123,
    duration_ms=4321,
    context_usage_pct=37.25,
    event_count=9,
    tool_call_count=2,
)


def _turn_state(tmp_path):
    """A DashboardState whose provider streams one answer, then the terminal event above."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.hooks import ToolHookResult

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    client = AsyncMock()
    client.provider_id = "anthropic"
    client.context_usage_pct = MagicMock(return_value=_COMPLETE.context_usage_pct)
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
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()

    async def _stream(*_a, **_kw):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="the answer")
        yield _COMPLETE

    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return state


def _live_stats_lines(state) -> list[str]:
    return [
        call.args[1]["text"]
        for call in state.broadcast_ws.call_args_list
        if call.args[0] == "activity_event" and call.args[1].get("kind") == "stats"
    ]


@pytest.mark.asyncio
async def test_the_line_a_user_saw_live_is_the_line_a_reload_serves(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _turn_state(tmp_path)
    session = state.get_or_create_session("s38")
    session._trust = True
    session.model = "claude-sonnet-4-5"
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")

    live = _live_stats_lines(state)
    assert len(live) == 1, live
    assert live[0].startswith("Turn complete: 9 events, 2 tool calls, context 37%")

    # The record on the turn's last assistant message carries the same sentence...
    last = [m for m in session.messages if m.get("role") == "assistant"][-1]
    assert last["meta"][TURN_TELEMETRY_KEY]["line"] == live[0]

    # ...and so does the copy on disk, which is what a reload reads.
    log_file = ConversationLog(base_dir=tmp_path)._path("dashboard:s38")
    rows = log_file.read_text(encoding="utf-8").splitlines()
    on_disk = [json.loads(row) for row in rows if row]
    persisted = [e for e in on_disk if e.get("role") == "assistant"][-1]
    assert persisted["meta"][TURN_TELEMETRY_KEY]["line"] == live[0]

    # A reload: the session is gone from memory, and the chat detail rehydrates it from disk.
    state._sessions.pop("s38")
    async with TestClient(TestServer(_make_app(state))) as client:
        r = await client.get("/api/chat/sessions/s38")
        assert r.status == 200
        detail = await r.json()
    served = [m for m in detail["messages"] if m.get("role") == "assistant"][-1]
    assert served["meta"][TURN_TELEMETRY_KEY]["line"] == live[0]


@pytest.mark.asyncio
async def test_a_turn_that_reported_nothing_sends_and_persists_no_line(tmp_path, monkeypatch):
    """The control: the live line and the record keep one gate, so neither says a row of zeros."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _turn_state(tmp_path)
    quiet = AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    async def _stream(*_a, **_kw):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="the answer")
        yield quiet

    client = state.sessions.get_or_create.return_value[0]
    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    client.context_usage_pct = MagicMock(return_value=None)
    session = state.get_or_create_session("s38q")
    session._trust = True
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")
    assert _live_stats_lines(state) == []
    for m in session.messages:
        assert TURN_TELEMETRY_KEY not in (m.get("meta") or {})
