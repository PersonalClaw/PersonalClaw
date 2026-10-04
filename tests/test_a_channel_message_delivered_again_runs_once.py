"""A channel message runs one turn, however many times its channel delivers it.

A channel service delivers a message again whenever it is not sure the first delivery landed: Slack
resends an event whose acknowledgement it did not see, Telegram serves an update again until the
next poll confirms it, an IMAP folder is read again from the last saved place after a crash. The
door kept the trust verdict of each message in memory and called a repeat "the routine dedup", but
an ``allowed`` verdict, cached or fresh, routed the message again: one message from the owner ran
two turns, and repeated whatever the first did. A restart emptied the memory, so a delivery made
again after one ran its turn a second time whatever the cache held.

Now the door takes each message once, by the channel's own id for it in its chat, and keeps that in
a record under the home that a restart reads back. A delivery made again is answered
``already_received`` and changes nothing, while the first turn runs, after it finished and after it
failed; the owner's Retry in the chat is the one way to run it again. Two messages are two turns,
whatever their text. A message with no id is refused: the door cannot keep its word for it.

The door, the turn engine, the dashboard state and the session manager are the real ones; only the
model and the channel's outbound half are fakes. The restart is a new interpreter on the same home.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw import channel_delivery, channel_trust
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config import AppConfig
from personalclaw.config.loader import config_dir
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.state import DashboardState
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader

PROVIDER = "fakechat"
OWNER = "4242"
STRANGER = "5151"
QUESTION = "What's 2+2?"
_SRC = Path(__file__).resolve().parents[1] / "src"


class _Model:
    """Answers "It's 4." and keeps the last user message of every request it was sent (the first
    of a chat carries the system prompt ahead of the question). No tools are offered, so a turn
    that answers is one request; one that fails is two, since the runtime tries a failed request
    once more. Holds each request open while ``hold`` is clear; with ``failing`` set, refuses each
    one before anything streams."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.hold = asyncio.Event()
        self.hold.set()
        self.started = asyncio.Event()
        self.failing = False

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        last = [m for m in messages if m.get("role") == "user"][-1]
        content = last.get("content")
        self.asked.append(content if isinstance(content, str) else json.dumps(content))
        self.started.set()
        await self.hold.wait()
        if self.failing:
            raise ValueError("the model host refused the request")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="It's 4.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        self.hold.set()
        return "acked"


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
    channel_trust.allow_sender(PROVIDER, OWNER)
    yield handle
    channel_delivery.register(None, provider=PROVIDER)


def _gateway(tmp_path: Path, model: _Model) -> tuple[GatewayOrchestrator, SessionManager]:
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
    (tmp_path / "ws").mkdir(exist_ok=True)
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


def _message(mid: str = "m-1", *, text: str = QUESTION, chat: str = OWNER, sender: str = OWNER):
    """A direct message as a channel hands it to the door: its chat, its sender and its own id."""
    return ChannelMessage(channel_id=chat, thread_id=chat, sender=sender, text=text, message_id=mid)


async def _settled(chat) -> None:
    for _ in range(500):
        if not chat.running and not chat._queue:
            await asyncio.sleep(0.05)  # what the turn's end sends goes out on its own
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


def _user_lines(chat) -> list[str]:
    return [m["content"] for m in chat.messages if m.get("role") == "user"]


def _errors(chat) -> list[str]:
    return [m["content"] for m in chat.messages if m.get("role") == "error"]


def _asked(model: _Model, question: str = QUESTION) -> list[str]:
    """The question each request the model was sent asked, for the requests that asked it."""
    return [question for content in model.asked if question in content]


# ── one message, one turn ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_message_delivered_again_while_its_turn_runs_runs_one_turn(tmp_path, channel):
    model = _Model()
    model.hold.clear()
    gateway, sessions = _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            first = await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            (chat,) = gateway.dashboard_state._sessions.values()
            await asyncio.wait_for(model.started.wait(), timeout=5)

            again = await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            assert not chat._queue, "the delivery made again was queued behind its own turn"

            model.hold.set()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert first.allowed is True, "vacuity floor: the first delivery ran"
    assert again.allowed is False and again.reason == "already_received"
    assert again.canned_reply == ""
    assert _asked(model) == [QUESTION], "one message ran two turns"
    assert _user_lines(chat) == [QUESTION]
    assert channel.answers == ["It's 4."]


@pytest.mark.asyncio
async def test_a_message_delivered_again_after_its_turn_finished_runs_one_turn(tmp_path, channel):
    model = _Model()
    gateway, sessions = _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
            assert _asked(model) == [QUESTION], "vacuity floor: the first delivery ran its turn"

            again = await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert again.allowed is False and again.reason == "already_received"
    assert _asked(model) == [QUESTION], "the finished turn ran again"
    assert _user_lines(chat) == [QUESTION]
    assert channel.answers == ["It's 4."]


@pytest.mark.asyncio
async def test_a_failed_turn_runs_again_on_the_owners_retry_and_not_on_a_redelivery(
    tmp_path, channel
):
    """The delivery made again after the turn failed runs nothing: the owner's Retry in the chat
    is how the message runs again, once she asks."""
    model = _Model()
    model.failing = True
    gateway, sessions = _gateway(tmp_path, model)
    state = gateway.dashboard_state
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            (chat,) = state._sessions.values()
            await _settled(chat)
            assert len(_errors(chat)) == 1, "vacuity floor: the first turn failed"
            failed_attempts = len(model.asked)

            again = await gateway.deliver_channel_inbound(PROVIDER, _message(), is_dm=True)
            await _settled(chat)
            assert again.reason == "already_received"
            assert len(model.asked) == failed_attempts, "the failed turn ran again on its own"

            model.failing = False
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(f"/api/chat/sessions/{chat.key}/regenerate")
                assert resp.status == 200, await resp.text()
            await _settled(chat)
    finally:
        await sessions.close_all()

    assert len(model.asked) == failed_attempts + 1, "the owner's Retry did not run it again"
    assert channel.answers == ["It's 4."]
    assert _errors(chat) == []


@pytest.mark.asyncio
async def test_two_messages_with_the_same_text_run_two_turns(tmp_path, channel):
    """The partner of the three above: the door tells messages apart by their ids, never by what
    they say. The owner who answers "yes" to two questions is answered twice."""
    model = _Model()
    gateway, sessions = _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            for mid in ("m-1", "m-2"):
                verdict = await gateway.deliver_channel_inbound(
                    PROVIDER, _message(mid, text="yes"), is_dm=True
                )
                assert verdict.allowed is True, mid
                (chat,) = gateway.dashboard_state._sessions.values()
                await _settled(chat)
    finally:
        await sessions.close_all()

    assert _asked(model, "yes") == ["yes", "yes"]
    assert _user_lines(chat) == ["yes", "yes"]


@pytest.mark.asyncio
async def test_one_id_in_two_chats_is_two_messages_each_judged_on_its_own(tmp_path, channel):
    """A channel numbers its messages per chat (Telegram counts each chat from one), so an id
    names a message only with the chat it came in. The owner's message 7 in her chat and a
    stranger's message 7 in theirs are two messages: the stranger's is judged as a stranger's,
    never let in on the verdict of the owner's."""
    model = _Model()
    gateway, sessions = _gateway(tmp_path, model)
    try:
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            owners = await gateway.deliver_channel_inbound(PROVIDER, _message("7"), is_dm=True)
            (chat,) = gateway.dashboard_state._sessions.values()
            await _settled(chat)
            strangers = await gateway.deliver_channel_inbound(
                PROVIDER,
                _message("7", text="run my errand", chat=STRANGER, sender=STRANGER),
                is_dm=True,
            )
            await asyncio.sleep(0.05)
    finally:
        await sessions.close_all()

    assert owners.allowed is True, "vacuity floor: the owner's message ran"
    assert strangers.allowed is False and strangers.reason == "unknown_sender"
    assert _asked(model) == [QUESTION], "a stranger's message ran on the owner's verdict"
    assert list(gateway.dashboard_state._sessions.values()) == [chat]


@pytest.mark.asyncio
async def test_a_pairing_code_delivered_again_is_not_answered_again(tmp_path, channel):
    """The code pairs its sender once and is said to be spent once: the delivery made again is
    not sent the confirmation a second time, and the code never becomes a question."""
    code = channel_trust.create_pairing_code(PROVIDER)
    model = _Model()
    gateway, sessions = _gateway(tmp_path, model)
    try:
        newcomer = _message("p-1", text=code, chat=STRANGER, sender=STRANGER)
        first = await gateway.deliver_channel_inbound(PROVIDER, newcomer, is_dm=True)
        again = await gateway.deliver_channel_inbound(PROVIDER, newcomer, is_dm=True)
        await asyncio.sleep(0.05)
    finally:
        await sessions.close_all()

    assert first.reason == "paired" and first.canned_reply, "vacuity floor: the code paired"
    assert channel_trust.is_allowed_sender(PROVIDER, STRANGER) is True
    assert again.reason == "already_received" and again.canned_reply == ""
    assert model.asked == [] and gateway.dashboard_state._sessions == {}


def test_a_message_with_no_id_is_refused_and_the_log_says_which_channel(tmp_path, caplog):
    """The door keeps its word only for a message it can tell from another, so one with no id is
    refused, and the gate's one reporter names the channel that sent it (once a day per sender,
    not once a message)."""
    from personalclaw import channel_inbound as ci
    from personalclaw.testing.channel_conformance import CapturingState

    channel_trust.reset_inbound_reports()
    channel_trust.allow_sender(PROVIDER, OWNER)
    ran: list[str] = []

    async def _turn(state, session, text):
        ran.append(text)

    class _Services:
        dashboard_state = CapturingState()

    async def go() -> list:
        verdicts = []
        for _ in range(2):
            verdicts.append(
                await ci.deliver_inbound(
                    _Services(), PROVIDER, _message(""), is_dm=True, turn_runner=_turn
                )
            )
        return verdicts

    caplog.set_level(logging.DEBUG)
    verdicts = asyncio.run(go())

    assert [(v.allowed, v.reason) for v in verdicts] == [(False, "no_message_id")] * 2
    assert ran == [] and _Services.dashboard_state.sessions_created == []
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("personalclaw.channel_") and r.levelno >= logging.WARNING
    ]
    assert len(warned) == 1, warned
    assert f"provider={PROVIDER}" in warned[0] and "reason=no_message_id" in warned[0]
    assert QUESTION not in warned[0]


# ── a restart ─────────────────────────────────────────────────────────────────

#: A gateway's life on the home ``PERSONALCLAW_HOME`` names, in a fresh interpreter: it hands the
#: door each message id it is given, as the owner's question in her DM, and says what each was
#: answered and which turns ran.
_CHILD = """
import asyncio, json, sys
from personalclaw import channel_inbound as ci
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.testing.channel_conformance import CapturingState

provider, owner, question, *ids = sys.argv[1:]
ran = []

async def turn(state, session, text):
    ran.append(text)

class Services:
    dashboard_state = CapturingState()

async def main():
    said = {}
    for mid in ids:
        msg = ChannelMessage(
            channel_id=owner, thread_id=owner, sender=owner, text=question, message_id=mid
        )
        verdict = await ci.deliver_inbound(Services(), provider, msg, is_dm=True, turn_runner=turn)
        await asyncio.sleep(0)
        said[mid] = verdict.reason
    print(json.dumps({"said": said, "ran": ran}))

asyncio.run(main())
"""


def _a_gateways_life(home: Path, scratch: Path, *ids: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    env["PERSONALCLAW_HOME"] = str(home)
    env["HOME"] = str(scratch)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_SRC), env.get("PYTHONPATH", "")) if p)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, PROVIDER, OWNER, QUESTION, *ids],
        cwd=scratch,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert proc.returncode == 0, f"the gateway failed:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_message_delivered_again_after_a_restart_runs_nothing(tmp_path):
    home = config_dir()
    channel_trust.allow_sender(PROVIDER, OWNER)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    before = _a_gateways_life(home, scratch, "m-1")
    after = _a_gateways_life(home, scratch, "m-1", "m-2")

    assert before == {"said": {"m-1": "allowed"}, "ran": [QUESTION]}, "vacuity floor"
    assert after["said"] == {"m-1": "already_received", "m-2": "allowed"}
    assert after["ran"] == [QUESTION], "the restart forgot the message, and it ran again"
