"""A turn that ends without an answer says why on the channel the conversation is linked to.

A conversation that came from a channel got its answer mirrored back there, and nothing else. A turn
that failed wrote its error into the dashboard chat only, and a turn cut short (its runtime reset
under it, the gateway restarting, a queued turn over its time limit) wrote nothing anywhere. The
person on the channel saw "Thinking…", perhaps a tool line or two, and then silence.

Now the channel is told, in one line, how the turn ended: the error the chat shows, that the owner
stopped it from the dashboard, or that it stopped before it finished — and a turn cut short says so
in the chat as well, where it said nothing.

The door, the turn engine, the dashboard state and the session manager are the real ones; only the
model and the channel's outbound half are fakes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery, channel_trust
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_inbound import reset_admissions
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat import api_chat_session_stop
from personalclaw.dashboard.chat_runner import (
    TURN_CUT_SHORT_NOTICE,
    TURN_STOPPED_FROM_DASHBOARD_NOTICE,
    run_chat,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from tests.chat_test_helpers import _api_app

PROVIDER = "fakechat"
OWNER = "4242"


class _Model:
    """Answers "It's 4."; holds each request open while ``hold`` is clear. Aborting the request
    (a stop) lets it go, as dropping an HTTP request ends its stream."""

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
        self.hold.set()
        return "acked"


class _FailingModel(_Model):
    """Every request fails before anything streams."""

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.started.set()
        raise ValueError("the model host refused the request")
        yield  # pragma: no cover - makes this an async generator


class _Channel:
    """The channel's outbound half: what the person on the channel would see."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.answers: list[str] = []

    async def deliver_text(self, channel, text, thread_ts="", **_kw):
        self.texts.append(text)
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


async def _gateway(tmp_path: Path, model: _Model) -> tuple[GatewayOrchestrator, SessionManager]:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    runtime.set_approval_policy("auto")
    sessions = SessionManager(AppConfig(), provider_factory=lambda *_a, **_kw: runtime)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    (tmp_path / "ws").mkdir()
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    return gateway, sessions


async def _from_the_channel(gateway: GatewayOrchestrator, text: str) -> None:
    await gateway.deliver_channel_inbound(
        PROVIDER,
        ChannelMessage(
            channel_id=OWNER, thread_id=OWNER, sender=OWNER, text=text, message_id="m-1"
        ),
        is_dm=True,
    )


async def _settled(chat) -> None:
    for _ in range(500):
        if not chat.running and not chat._queue:
            await asyncio.sleep(0.05)  # what the turn's end sends goes out on its own
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


def _errors(chat) -> list[str]:
    return [m["content"] for m in chat.messages if m.get("role") == "error"]


@pytest.mark.asyncio
async def test_a_turn_cut_short_says_so_on_its_channel_and_in_the_chat(tmp_path, channel):
    model = _Model()
    model.hold.clear()
    gateway, sessions = await _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?")
            (chat,) = gateway.dashboard_state._sessions.values()
            await asyncio.wait_for(model.started.wait(), timeout=5)

            # The runtime is reset under the turn, as a restart or a tripped circuit breaker does.
            await sessions.reset(f"dashboard:{chat.key}")
            model.hold.set()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert channel.answers == [], "vacuity floor: this turn never answered"
    assert channel.texts == [TURN_CUT_SHORT_NOTICE], "the channel heard nothing about its turn"
    assert _errors(chat) == [TURN_CUT_SHORT_NOTICE], "the chat said nothing about it either"


@pytest.mark.asyncio
async def test_a_turn_that_fails_tells_its_channel_what_the_chat_shows(tmp_path, channel):
    gateway, sessions = await _gateway(tmp_path, _FailingModel())
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?")
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
    finally:
        await sessions.close_all()

    (shown,) = _errors(chat)
    assert "refused the request" in shown, "vacuity floor: the chat shows the failure"
    assert channel.texts == [shown], "the channel was not told why its message went unanswered"


@pytest.mark.asyncio
async def test_a_stop_pressed_in_the_dashboard_is_said_on_the_channel(tmp_path, channel):
    """The chat has its stop card already, so nothing more is written there."""
    model = _Model()
    model.hold.clear()
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/stop", api_chat_session_stop)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?")
            (chat,) = state._sessions.values()
            await asyncio.wait_for(model.started.wait(), timeout=5)

            async with TestClient(TestServer(app)) as client:
                resp = await client.post(f"/api/chat/sessions/{chat.key}/stop")
                assert resp.status == 200, await resp.text()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert channel.answers == [], "vacuity floor: the stop landed before the answer"
    assert channel.texts == [TURN_STOPPED_FROM_DASHBOARD_NOTICE]
    assert _errors(chat) == [], "a stop the owner asked for is not reported as a failure"


@pytest.mark.asyncio
async def test_a_turn_that_answers_sends_only_its_answer(tmp_path, channel):
    gateway, sessions = await _gateway(tmp_path, _Model())
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await _from_the_channel(gateway, "What's 2+2?")
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert channel.answers == ["It's 4."]
    assert channel.texts == [] and _errors(chat) == []


@pytest.mark.asyncio
async def test_a_dashboard_chat_cut_short_says_so_in_the_chat_and_sends_nothing(tmp_path, channel):
    model = _Model()
    model.hold.clear()
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            chat.append("user", "What's 2+2?", "msg msg-u")
            chat.task = asyncio.ensure_future(run_chat(state, chat, "What's 2+2?"))
            await asyncio.wait_for(model.started.wait(), timeout=5)

            await sessions.reset(f"dashboard:{chat.key}")
            model.hold.set()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert _errors(chat) == [TURN_CUT_SHORT_NOTICE]
    assert channel.texts == [] and channel.answers == [], "a chat with no channel sent something"
