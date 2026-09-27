"""A chat's progress line on its channel says how each call ended, not "done" for every one.

A chat linked to a channel thread mirrors its turn there: one progress line per tool call, in
progress while it runs (``ChannelDelivery.append_stream_task``). Each line was marked ``complete``
when the next call started or the turn ended, whatever had happened to its call, so a call the
owner rejected read "✅ bash" on Telegram, and so did one nobody answered, one whose turn was
stopped, and one that ran and failed. Each line now ends the way its call did: ``complete``,
``failed``, ``rejected``, ``expired`` or ``cancelled`` (``channel_delivery.TASK_STATUSES``).

Driven through the real turn engine (``run_chat``) with the real ``NativeAgentRuntime`` and session
manager; only the model and the channel's outbound half are fakes. The approval is answered where
PersonalClaw's own card answers it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw import channel_delivery
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.approval_answer import YOU
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

PROVIDER = "fakechat"
TOOL = "write_note"


class _Tools(ToolProvider):
    """One tool that asks before it runs; ``fails`` makes it run and fail."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=TOOL, description="d", parameters={"type": "object"}, requires_approval=True
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        if self.fails:
            return ToolResult(success=False, output="the disk is full")
        return ToolResult(success=True, output="done")


class _Channel:
    """The channel's outbound half: the progress lines of the one stream a turn opens."""

    def __init__(self) -> None:
        #: every (task_id, title, status) the stream was given, in order
        self.lines: list[tuple[str, str, str]] = []
        self.stopped = False

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel, text, thread_ts="", **_kw):
        return "t-1"

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        return "s-1"

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        self.lines.append((task_id, title, status))

    async def stop_stream(self, channel, stream_ts):
        self.stopped = True

    async def deliver_chat_mirror(self, channel, text, thread_ts=""):
        return None

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape; nobody presses here
        pending = SimpleNamespace(future=asyncio.get_running_loop().create_future())
        if on_prompted:
            on_prompted(pending)
        return (await pending.future) == "approved"

    def statuses(self) -> list[str]:
        """What the call's line went through, in order (the one call each turn makes)."""
        tasks = {task for task, _title, _status in self.lines}
        assert len(tasks) == 1, f"one call, one line: {self.lines}"
        return [status for _task, _title, status in self.lines]


@pytest.fixture
def channel():
    handle = _Channel()
    channel_delivery.register(handle, provider=PROVIDER)
    yield handle
    channel_delivery.register(None, provider=PROVIDER)


def _calls_once() -> _ScriptedModel:
    call = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="call-1", title=TOOL, tool_input='{"text": "hi"}'
    )
    return _ScriptedModel(
        [
            [call, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )


def _chat(tmp_path: Path, tools: _Tools) -> tuple[DashboardState, Any]:
    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(), model_provider=_calls_once(), tool_providers=[tools], cwd=tmp_path
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
    # A chat that came in from the channel, answered in its thread there.
    session = state.get_or_create_session(app=PROVIDER)
    state.link_channel(session.key, "thread-1", "chan-1")
    return state, session


async def _pending(session) -> str:
    for _ in range(400):
        waiting = [k for k, f in session._approval_futures.items() if not f.done()]
        if waiting:
            return waiting[0]
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never asked")


async def _turn(state, session, answer=None) -> None:
    """One turn; *answer* is what happens to its approval once it is asked."""
    session.append("user", "go", "msg msg-u")
    answering = asyncio.ensure_future(answer(state, session)) if answer else None
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    if answering is not None:
        await answering


def _decide(action: str):
    async def answer(state, session) -> None:
        state.decide_session_approval(session, await _pending(session), action, by=YOU)

    return answer


async def _stop_the_turn(state, session) -> None:
    await _pending(session)
    assert state.cancel_turn_approvals(f"dashboard:{session.key}") == 1


@pytest.mark.asyncio
async def test_a_rejected_call_s_line_says_rejected(tmp_path, channel):
    tools = _Tools()
    state, session = _chat(tmp_path, tools)
    await _turn(state, session, _decide("rejected"))
    assert tools.ran == []
    assert channel.statuses() == ["in_progress", "rejected"], "a call that never ran read done"


@pytest.mark.asyncio
async def test_a_call_nobody_answered_says_expired(tmp_path, channel):
    tools = _Tools()
    state, session = _chat(tmp_path, tools)
    state.approval_window_secs = lambda: 0.3  # type: ignore[method-assign]
    await _turn(state, session)
    assert tools.ran == []
    assert channel.statuses() == ["in_progress", "expired"]


@pytest.mark.asyncio
async def test_a_call_whose_turn_was_stopped_says_cancelled(tmp_path, channel):
    tools = _Tools()
    state, session = _chat(tmp_path, tools)
    await _turn(state, session, _stop_the_turn)
    assert tools.ran == []
    assert channel.statuses() == ["in_progress", "cancelled"]


@pytest.mark.asyncio
async def test_an_approved_call_that_failed_says_failed(tmp_path, channel):
    tools = _Tools(fails=True)
    state, session = _chat(tmp_path, tools)
    await _turn(state, session, _decide("approved"))
    assert tools.ran == [TOOL]
    assert channel.statuses() == ["in_progress", "failed"]


@pytest.mark.asyncio
async def test_an_approved_call_that_ran_says_complete(tmp_path, channel):
    """The floor: a call that ran and succeeded is the one line that reads done."""
    tools = _Tools()
    state, session = _chat(tmp_path, tools)
    await _turn(state, session, _decide("approved"))
    assert tools.ran == [TOOL]
    assert channel.statuses() == ["in_progress", "complete"]
    assert channel.stopped


# ── a runtime that asks before it shows the call ────────────────────────────────────────────


def _asks_before_it_shows(tmp_path: Path, monkeypatch) -> tuple[DashboardState, Any, Any]:
    """The same chat over the offline scripted provider, which asks its approval before it
    reports the call, where the native runtime and ACP report the call first."""
    import json

    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV
    from personalclaw.llm.scripted import ScriptedProvider

    call = {"id": "call-1", "name": TOOL, "input": {"text": "hi"}}
    call.update(risk_level="destructive", requires_approval=True)
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"version": 1, "turns": [{"tool_calls": [call], "stop_reason": "end_turn"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    model = ScriptedProvider()
    sessions = SessionManager(AppConfig(), provider_factory=lambda *_a, **_kw: model)
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
    session = state.get_or_create_session(app=PROVIDER)
    state.link_channel(session.key, "thread-1", "chan-1")
    return state, session, model


@pytest.mark.asyncio
async def test_a_call_refused_before_it_was_shown_says_rejected(tmp_path, channel, monkeypatch):
    """A call refused at an approval asked before the runtime reported it had no line to end,
    and the line the report then opened read done at the end of the turn."""
    state, session, model = _asks_before_it_shows(tmp_path, monkeypatch)
    await _turn(state, session, _decide("rejected"))
    assert model.decisions == [("call-1", "rejected")]
    assert channel.statuses() == ["rejected"], "a call that never ran read done"


@pytest.mark.asyncio
async def test_a_call_approved_before_it_was_shown_says_complete(tmp_path, channel, monkeypatch):
    """The floor for that order: an approved call's line opens when it is shown, and reads done."""
    state, session, model = _asks_before_it_shows(tmp_path, monkeypatch)
    await _turn(state, session, _decide("approved"))
    assert model.decisions == [("call-1", "approved")]
    assert channel.statuses() == ["in_progress", "complete"]
