"""A chat's turn records the model that answered it, where the turn is priced (#3808).

The ask before a mid-chat switch to a dearer model prices the next reply on the model the chat runs
on now (``dashboard/chat_switch_price.py``). A chat on Auto names no model: its turns run on its
use-case chain, so what it runs on is the model its latest turn was answered by. The chat runner
knows it when it prices the turn (``answered_model``/``answered_provider`` of the served ref), and
keeps it on the chat as ``answered_by``. The turn here runs through the real runner and the native
runtime, over a model that answers whatever it is sent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

pytestmark = pytest.mark.asyncio

ANSWER = "The notes are tidy."


class _Answers:
    """The model the runtime asks, built through the resolution seam for the entry ``work``."""

    supports_tools = True
    served_ref = "work:large-1"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages, **_kw: Any):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=1)

    async def stream(self, message: str):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="A title")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=2)


@pytest_asyncio.fixture
async def gateway(tmp_path):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.config import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult
    from personalclaw.session import SessionManager

    def factory(session_key=None, **kwargs: Any):
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model=""),
            model_provider=_Answers(),
            tool_providers=[],
            cwd=tmp_path,
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "sessions"),
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.side_effect = lambda message, *_a, **_k: (message, None)
    state.context_builder = builder
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    try:
        yield state
    finally:
        await sessions.close_all()


async def test_a_turn_on_auto_keeps_the_model_that_answered_it(gateway):
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.chat_switch_price import current_model

    chat = gateway.get_or_create_session()
    chat._trust = True
    assert chat.answered_by is None
    chat.append("user", "Tidy the notes.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(gateway, chat, "Tidy the notes.")
    assert [m for m in chat.messages if m.get("role") == "assistant"][-1]["content"] == ANSWER
    assert chat.answered_by == ("work", "large-1")
    assert current_model(chat) == ("work", "large-1")
