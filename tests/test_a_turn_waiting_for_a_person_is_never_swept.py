"""A chat turn waiting for a person is never reaped by the session sweep, whichever door made it.

The Artifacts page's Iterate panel is a chat staged by Investigate and embedded beside the
artifact. Its turn asked for an Allow; two minutes later the idle sweep logged "Expiring orphaned
dashboard session (session gone)" and shut the runtime down, and the user's Allow, ten seconds
after that, got "Error: cancelled". The sweep learned which chats exist from a list only some routes
pushed, and a chat made by Investigate was never on it.

The sweep now asks the dashboard which chats exist when it runs, and leaves alone any runtime a
turn is holding — and a turn waiting on an approval holds its runtime until the answer comes. This
drives that exact shape: the chat is staged the way Investigate stages it, its turn parks on an
approval, the sweep runs with both of its rules armed, and then the Allow is answered.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.config import AppConfig
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)
from personalclaw.session import SessionManager


async def _events(items):
    for item in items:
        yield item


def _provider(*_a, **_kw):
    """The chat's runtime: it asks for one approval, then answers."""
    client = AsyncMock()
    client.context_usage_pct = lambda: 0.0
    client.compacts_automatically = False
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _events(
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="image_generate",
                    tool_kind="edit",
                    request_id="req-1",
                ),
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="The bubbles are blue now."),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )
    return client


def _context_builder() -> MagicMock:
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("Make the bubbles blue.", None)
    return cb


@pytest.mark.asyncio
async def test_an_iterate_chat_waiting_on_its_allow_outlives_the_sweep(tmp_path) -> None:
    cfg = AppConfig()
    cfg.session.timeout_secs = 3600
    sessions = SessionManager(cfg, provider_factory=_provider)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.context_builder = _context_builder()
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()

    # Staged as `POST /api/investigate` stages it: a new dashboard chat, in agent mode.
    chat = state.get_or_create_session()
    chat._task_mode = "agent"
    key = f"dashboard:{chat.key}"

    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            turn = asyncio.create_task(run_chat(state, chat, "Make the bubbles blue."))
            for _ in range(400):
                await asyncio.sleep(0.005)
                if "req-1" in chat._approval_futures:
                    break
            assert "req-1" in chat._approval_futures, "vacuity floor: the turn never asked"
            assert sessions.has_session(key), "vacuity floor: the turn runs on its own runtime"

            # The orphan rule alone (a high idle timeout), then the idle rule too: the runtime has
            # been waiting "idle" far longer than the window.
            await sessions._expire_idle(9999)
            sessions._sessions[key].last_used = time.monotonic() - 10_000
            await sessions._expire_idle(60)
            assert sessions.has_session(key), "the sweep took the runtime of a turn waiting on you"

            chat._approval_futures["req-1"].set_result("approved")
            await asyncio.wait_for(turn, timeout=5)
    finally:
        await sessions.close_all()

    said = [m.get("content", "") for m in chat.messages if m.get("role") == "assistant"]
    assert any("The bubbles are blue now." in s for s in said), said
    assert not any("cancelled" in str(m.get("content", "")).lower() for m in chat.messages)


@pytest.mark.asyncio
async def test_once_the_turn_is_answered_a_chat_that_is_gone_is_reaped() -> None:
    """The floor: the orphan rule still reaps a runtime nobody holds whose chat was closed."""
    cfg = AppConfig()
    cfg.session.timeout_secs = 3600
    sessions = SessionManager(cfg, provider_factory=_provider)
    sessions.register_dashboard_sessions(lambda: frozenset())
    try:
        await sessions.get_or_create("dashboard:chat-2-1")
        sessions.release("dashboard:chat-2-1")
        await sessions._expire_idle(9999)
        assert not sessions.has_session("dashboard:chat-2-1")
    finally:
        await sessions.close_all()
