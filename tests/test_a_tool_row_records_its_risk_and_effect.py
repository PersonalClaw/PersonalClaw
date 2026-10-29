"""Each tool row records how risky its call was and whether it only read, as the runner decided.

The chat runner works out a call's effective risk when the call is announced
(``task_modes.resolve_effective_risk``) and used it for the call's audit row only, so a transcript
said nothing of it, and every later reader (the run-again question, the typed turn's row mapping)
had to work it out again from the tools as they are now. The row now keeps it: ``meta.risk`` is the
risk decided at call time, and ``meta.effect`` is ``read`` exactly when ``task_modes.reads_only``
held then (the risk reached ``safe``), ``write`` for every other call (shared contracts §3.3).

Driven through the real turn engine (``run_chat``) with the real ``NativeAgentRuntime``; only the
model is scripted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


class _Tools(ToolProvider):
    """A tool that declares it only reads, and one that declares a change; neither asks."""

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_note",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="write_note",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="done")


def _model() -> _ScriptedModel:
    calls = [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="call-r", title="read_note", tool_input="{}"),
        AgentEvent(
            kind=EVENT_TOOL_CALL, tool_call_id="call-w", title="write_note", tool_input="{}"
        ),
    ]
    return _ScriptedModel(
        [
            [*calls, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )


def _chat(tmp_path: Path) -> tuple[DashboardState, Any]:
    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(), model_provider=_model(), tool_providers=[_Tools()], cwd=tmp_path
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, state.get_or_create_session()


@pytest.mark.asyncio
async def test_a_read_and_a_write_each_record_their_risk_and_effect(tmp_path):
    state, session = _chat(tmp_path)
    session.append("user", "go", "msg msg-u")
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)

    rows = {
        (m.get("meta") or {}).get("tool_call_id"): m.get("meta") or {}
        for m in session.messages
        if m.get("role") == "tool" and (m.get("meta") or {}).get("tool_call_id")
    }
    assert set(rows) == {"call-r", "call-w"}, [m.get("content") for m in session.messages]
    assert (rows["call-r"].get("risk"), rows["call-r"].get("effect")) == ("safe", "read")
    assert (rows["call-w"].get("risk"), rows["call-w"].get("effect")) == ("caution", "write")
