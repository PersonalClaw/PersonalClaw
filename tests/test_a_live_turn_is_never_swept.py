"""The session sweep never takes a runtime out from under a turn, and knows every chat that exists.

``SessionManager._expire_idle`` reaps a ``dashboard:`` runtime whose chat is gone. It learned
which chats exist from a snapshot that some dashboard routes pushed after creating or deleting a
chat, and a chat made anywhere else went through none of them: the channel door
(``channel_inbound._route_to_session``), a scheduled prompt, Investigate, the OpenAI-compatible
endpoint. Every such chat read as gone, so the first sweep that landed during its turn reset the
runtime and the turn ended with no answer. A Telegram message showed "Thinking…" and its tool
lines, then nothing.

The sweep now asks the dashboard which chats exist when it runs, and it leaves alone any runtime a
turn is holding: a turn is not idle, and it ends by its own bounds, not by the sweep.

The door, the turn engine, the dashboard state and the session manager are all the real ones; only
the model and the channel's outbound half are fakes.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery, channel_trust
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_inbound import reset_admissions
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_persistence import restore_recent_sessions
from personalclaw.dashboard.state import DashboardState
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

PROVIDER = "fakechat"
OWNER = "4242"


class _Model:
    """Answers "It's 4."; holds each request open while ``hold`` is clear."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.hold = asyncio.Event()
        self.hold.set()
        self.started = asyncio.Event()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.started.set()
        await self.hold.wait()
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="It's 4.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Channel:
    """The channel's outbound half: what the person on the channel would see."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    async def deliver_text(self, channel, text, thread_ts="", **_kw):
        return "t-1"

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        return "s-1"

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        return None

    async def stop_stream(self, channel, stream_ts):
        return None

    async def deliver_chat_mirror(self, channel, text, thread_ts=""):
        self.answers.append(text)


@pytest.fixture
def channel():
    handle = _Channel()
    channel_delivery.register(handle, provider=PROVIDER)
    reset_admissions()
    channel_trust.allow_sender(PROVIDER, OWNER)
    yield handle
    channel_delivery.register(None, provider=PROVIDER)
    reset_admissions()


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.session.timeout_secs = 3600
    return cfg


async def _gateway(tmp_path: Path, model: _Model) -> tuple[GatewayOrchestrator, SessionManager]:
    """A gateway whose chats run on the real session manager, booted the way the dashboard
    boots: its recent chats restored first."""
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    runtime.set_approval_policy("auto")
    sessions = SessionManager(_cfg(), provider_factory=lambda *_a, **_kw: runtime)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    restore_recent_sessions(state)

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    return gateway, sessions


async def _from_the_channel(gateway: GatewayOrchestrator, text: str, n: int) -> None:
    await gateway.deliver_channel_inbound(
        PROVIDER,
        ChannelMessage(
            channel_id=OWNER, thread_id=OWNER, sender=OWNER, text=text, message_id=f"m-{n}"
        ),
        is_dm=True,
    )


async def _settled(chat) -> None:
    for _ in range(500):
        if not chat.running and not chat._queue:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


@pytest.mark.asyncio
async def test_a_sweep_during_a_channel_turn_leaves_the_turn_to_answer(tmp_path, channel):
    model = _Model()
    model.hold.clear()
    gateway, sessions = await _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?", 1)
            (chat,) = gateway.dashboard_state._sessions.values()
            await asyncio.wait_for(model.started.wait(), timeout=5)
            key = f"dashboard:{chat.key}"
            assert sessions.has_session(key), "vacuity floor: the turn runs on its own runtime"

            # A sweep lands while the model is still answering. A high idle timeout leaves the
            # orphan rule as the only way it could reap anything.
            await sessions._expire_idle(9999)
            assert sessions.has_session(key), "the sweep reset a runtime a live turn was using"

            model.hold.set()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert channel.answers == ["It's 4."], "the channel's turn ended without an answer"


@pytest.mark.asyncio
async def test_a_chat_the_channel_started_is_not_taken_for_a_closed_one(tmp_path, channel):
    """Between turns too: the chat exists, so its runtime is kept for its next message."""
    gateway, sessions = await _gateway(tmp_path, _Model())
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?", 1)
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
        key = f"dashboard:{chat.key}"
        assert sessions.has_session(key), "vacuity floor: the turn left its runtime warm"

        await sessions._expire_idle(9999)

        assert sessions.has_session(key), "a chat that exists was reaped as a closed one"

        # Vacuity floor: once the chat is gone from the dashboard, its runtime IS reaped.
        gateway.dashboard_state._sessions.pop(chat.key)
        await sessions._expire_idle(9999)
        assert not sessions.has_session(key), "the orphan rule no longer reaps anything"
    finally:
        await sessions.close_all()


def _mock_provider_factory():
    def factory(session_key=None, agent=None, channel_id=None, **kwargs):
        m = AsyncMock()
        m.context_usage_pct = lambda: 0.0
        m.compacts_automatically = False
        return m

    return factory


@pytest.mark.asyncio
async def test_a_turn_holding_its_runtime_is_never_idle():
    """Idle is measured from when the last turn let the runtime go, not from when it began."""
    sessions = SessionManager(_cfg(), provider_factory=_mock_provider_factory())
    try:
        await sessions.get_or_create("subagent:long-task")
        sessions._sessions["subagent:long-task"].last_used = time.monotonic() - 10_000

        await sessions._expire_idle(60)
        assert sessions.has_session("subagent:long-task"), "a turn past the idle window was reaped"

        sessions.release("subagent:long-task")
        await sessions._expire_idle(60)
        assert sessions.has_session(
            "subagent:long-task"
        ), "a runtime a turn let go of a moment ago was reaped as idle"

        # Vacuity floor: a runtime nobody has used for longer than the window IS reaped.
        sessions._sessions["subagent:long-task"].last_used = time.monotonic() - 10_000
        await sessions._expire_idle(60)
        assert not sessions.has_session("subagent:long-task")
    finally:
        await sessions.close_all()


@pytest.mark.asyncio
async def test_an_unreadable_chat_list_reaps_no_runtime_as_an_orphan():
    """Reaping is destructive, so a sweep that cannot tell which chats exist reaps none as closed.
    The idle rule still runs."""

    def _unreadable() -> set[str]:
        raise RuntimeError("the chat registry could not be read")

    sessions = SessionManager(_cfg(), provider_factory=_mock_provider_factory())
    try:
        sessions.register_dashboard_sessions(_unreadable)
        await sessions.get_or_create("dashboard:chat-1-1")
        sessions.release("dashboard:chat-1-1")
        await sessions.get_or_create("cron:nightly")
        sessions.release("cron:nightly")
        sessions._sessions["cron:nightly"].last_used = time.monotonic() - 10_000

        await sessions._expire_idle(60)

        assert sessions.has_session("dashboard:chat-1-1")
        assert not sessions.has_session("cron:nightly"), "the idle rule stopped with the orphan one"
    finally:
        await sessions.close_all()
