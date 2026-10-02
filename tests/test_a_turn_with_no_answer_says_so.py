"""A chat turn that ran its steps and wrote no answer says so, with Retry, wherever it is read.

Measured: a review turn ran fifteen commands, its model then answered with nothing, and the page
said "Response complete." with no reply, no notice and no retry; the transcript held no answer
and the log said nothing. The chat runner took "no text after the tools" for a finished turn.

Now the native loop asks its model once for the reply (`owed_reply.ANSWER_OWED_NOTE`), and a turn
still without one ends in the error that says so: in the chat, where Retry asks again, on the
channel the conversation is linked to, and in the turn's outcome every other reader goes by.

The door, the turn engine, the dashboard state and the session manager are the real ones; only
the model and the channel's outbound half are fakes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery, channel_trust
from personalclaw.agents.native.owed_reply import ANSWER_OWED_NOTE
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_inbound import reset_admissions
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_regenerate import api_chat_session_regenerate
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_session_map import TURN_TELEMETRY_KEY
from personalclaw.dashboard.state import CRON_NOTIFY_END, CRON_NOTIFY_PREFIX, DashboardState
from personalclaw.dashboard.turn_endings import no_answer_notice
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from tests.chat_test_helpers import _api_app

PROVIDER = "fakechat"
OWNER = "4242"
QUESTION = "Review the uncommitted diff."
REPLY = "The change renames one helper, and its tests still cover it."


class _Model:
    """Runs one step on each message it is sent, then answers with nothing. With
    ``answers_when_asked`` it writes the reply when the loop asks for it."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, *, answers_when_asked: bool = False) -> None:
        self.answers_when_asked = answers_when_asked
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(list(messages))
        last = messages[-1] if messages else {}
        if last.get("role") == "user" and last.get("content") == ANSWER_OWED_NOTE:
            if self.answers_when_asked:
                yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=REPLY)
        elif last.get("role") != "tool":
            n = len(self.requests)
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id=f"c{n}", title="look", tool_input="{}"
            )
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Look(ToolProvider):
    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read something.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="notes.md: 3 lines changed")


class _Channel:
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
        tool_providers=[_Look()],
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


async def _settled(chat) -> None:
    for _ in range(500):
        if not chat.running and not chat._queue:
            await asyncio.sleep(0.05)  # what the turn's end sends goes out on its own
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


def _turn_rows(chat) -> list[dict]:
    """The rows after the last user row: what the turn itself wrote."""
    rows = chat.messages
    start = max(i for i, m in enumerate(rows) if m.get("role") == "user")
    return rows[start + 1 :]


def _frames(state, kind: str) -> list[dict]:
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == kind]


async def _ask_in_the_dashboard(state, chat, text: str = QUESTION) -> None:
    chat.append("user", text, "msg msg-u")
    chat.task = asyncio.ensure_future(run_chat(state, chat, text))
    await _settled(chat)


@pytest.mark.asyncio
async def test_a_turn_that_ran_steps_and_wrote_nothing_ends_in_the_notice_with_retry(tmp_path):
    model = _Model()
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            chat.append("assistant", "An earlier answer.", "msg msg-a")
            await _ask_in_the_dashboard(state, chat)
    finally:
        await sessions.close_all()

    # The step ran, the model went quiet, it was asked once, and it went quiet again.
    assert len(model.requests) == 3, "the loop did not ask its quiet model for the reply"
    rows = _turn_rows(chat)
    notice = no_answer_notice(1, asked_by_person=True)
    assert [m["role"] for m in rows if m["role"] in ("assistant", "error")] == ["error"]
    (error,) = [m for m in rows if m["role"] == "error"]
    assert error["content"] == notice
    assert chat._last_turn_outcome == "error", "the turn still reads as a finished answer"
    assert {"session": chat.key, "outcome": "error"} in _frames(state, "chat_done")
    assert {"session": chat.key, "role": "error", "content": notice} in (
        _frames(state, "chat_message")
    )
    assert [m["content"] for m in chat.messages if m["role"] == "user"] == [
        QUESTION
    ], "the message was resent, which runs every step again"
    # The turn's numbers are not stamped on the answer the turn before it gave.
    earlier = next(m for m in chat.messages if m.get("content") == "An earlier answer.")
    assert TURN_TELEMETRY_KEY not in (earlier.get("meta") or {})


@pytest.mark.asyncio
async def test_a_model_that_answers_when_asked_ends_the_turn_with_its_answer(tmp_path):
    model = _Model(answers_when_asked=True)
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            await _ask_in_the_dashboard(state, chat)
    finally:
        await sessions.close_all()

    rows = _turn_rows(chat)
    assert [m["content"] for m in rows if m["role"] == "assistant"] == [REPLY]
    assert not [m for m in rows if m["role"] == "error"]
    assert chat._last_turn_outcome == "complete"


@pytest.mark.asyncio
async def test_retry_on_the_notice_asks_the_same_question_again(tmp_path):
    model = _Model()
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            await _ask_in_the_dashboard(state, chat)
            model.answers_when_asked = True
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(f"/api/chat/sessions/{chat.key}/regenerate")
                assert resp.status == 200, await resp.text()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert [m["content"] for m in chat.messages if m["role"] == "user"] == [QUESTION]
    assert [m["role"] for m in chat.messages if m["role"] in ("assistant", "error")] == [
        "assistant"
    ]
    assert chat._last_turn_outcome == "complete"


@pytest.mark.asyncio
async def test_the_linked_channel_hears_that_the_turn_has_no_answer(tmp_path, channel):
    gateway, sessions = await _gateway(tmp_path, _Model())
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await gateway.deliver_channel_inbound(
                PROVIDER,
                ChannelMessage(
                    channel_id=OWNER, thread_id=OWNER, sender=OWNER, text=QUESTION, message_id="m-1"
                ),
                is_dm=True,
            )
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert channel.answers == [], "vacuity floor: this turn never answered"
    assert channel.texts == [
        no_answer_notice(1, asked_by_person=True)
    ], "the channel was left with silence"


@pytest.mark.asyncio
async def test_a_turn_an_automation_started_is_retried_as_that_turn(tmp_path):
    # An automation's message starts a turn in a chat that already has an answered question.
    # Nobody typed it, so the notice does not ask for a message to be sent again; Retry runs the
    # automation's turn again, and the question and answer before it stay as they were.
    model = _Model()
    gateway, sessions = await _gateway(tmp_path, model)
    state = gateway.dashboard_state
    app = _api_app(state)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    earlier = ("What changed since Monday?", "One helper was renamed.")
    automation = (
        f'{CRON_NOTIFY_PREFIX}"Morning check"]\nThe backup folder has three new files.\n'
        f"{CRON_NOTIFY_END}"
    )
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            chat = state.get_or_create_session()
            chat.append("user", earlier[0], "msg msg-u")
            chat.append("assistant", earlier[1], "msg msg-a")
            chat.append("inject", automation, json.dumps({"cronLabel": "Morning check"}))
            chat.task = asyncio.ensure_future(run_chat(state, chat, automation))
            await _settled(chat)
            (error,) = [m for m in chat.messages if m["role"] == "error"]
            model.answers_when_asked = True
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(f"/api/chat/sessions/{chat.key}/regenerate")
                assert resp.status == 200, await resp.text()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert error["content"] == no_answer_notice(1, asked_by_person=False)
    assert error["content"].endswith("Retry it from the chat."), error["content"]
    assert [(m["role"], m["content"]) for m in chat.messages if m["role"] != "tool"] == [
        ("user", earlier[0]),
        ("assistant", earlier[1]),
        ("inject", automation),
        ("assistant", REPLY),
    ], "Retry ran an earlier turn, or lost the one before it"
    assert chat._last_turn_outcome == "complete"
