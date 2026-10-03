"""A conversation that comes from a channel is answered on that channel.

The guarded door (``channel_inbound.deliver_inbound``) turns a channel message into a chat turn,
and the turn mirrors the answer back to the channel it came from (``chat_runner.run_chat``). The
mirror asks ``DashboardState.channel_provider_for`` which channel that is, with the chat's history
key (``dashboard:<name>``), and the lookup only knew the chat's name. So since #959 every answer to
a Telegram or Discord message stayed in the dashboard, and the person on the channel heard nothing
back.

These drive the real door and the real turn engine; only the model and the channel's outbound half
are fakes. Each case that says something does NOT reach the channel carries its vacuity floor: in
the same conversation, something else does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_test_helpers import links_kept_in_a_session_map

from personalclaw import channel_delivery, channel_trust
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.channel_inbound import reset_admissions
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

PROVIDER = "fakechat"
OWNER = "4242"


ANSWERS = {"What's 2+2?": "It's 4.", "And 3+3?": "It's 6.", "Is 5 prime?": "Yes, 5 is prime."}


class _Model:
    """Answers the question the request ends with; holds a turn open while ``hold`` is clear.

    Anything else (a chat's title, say) gets a word no test looks for."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.hold = asyncio.Event()
        self.hold.set()
        self.started = asyncio.Event()

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.started.set()
        await self.hold.wait()
        # The question asked last: a request carries the conversation so far.
        sent = "\n".join(str(m.get("content") or "") for m in messages)
        where, answer = max(((sent.rfind(q), a) for q, a in ANSWERS.items()), default=(-1, ""))
        answer = answer if where >= 0 else "ok"
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=answer)
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Channel:
    """The channel's outbound half: what the person on the channel would see."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []  # (what, where, text)

    async def deliver_text(self, channel, text, thread_ts="", **_kw):
        self.seen.append(("text", channel, text))
        return "t-1"

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        self.seen.append(("progress", channel, initial_text))
        return "s-1"

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        return None

    async def stop_stream(self, channel, stream_ts):
        return None

    async def deliver_chat_mirror(self, channel, text, thread_ts=""):
        self.seen.append(("answer", channel, text))

    def answers(self) -> list[str]:
        return [text for what, _where, text in self.seen if what == "answer"]

    def echoes(self) -> list[str]:
        return [text for what, _where, text in self.seen if what == "text"]


@pytest.fixture
def channel():
    handle = _Channel()
    channel_delivery.register(handle, provider=PROVIDER)
    reset_admissions()
    channel_trust.allow_sender(PROVIDER, OWNER)
    yield handle
    channel_delivery.register(None, provider=PROVIDER)
    reset_admissions()


async def _gateway(tmp_path: Path, model: _Model) -> GatewayOrchestrator:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")

    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    links_kept_in_a_session_map(sessions)
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
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

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    return gateway


async def _from_the_channel(gateway: GatewayOrchestrator, text: str, n: int) -> None:
    """The owner sends ``text`` in their DM with the bot; a DM is its own thread."""
    await gateway.deliver_channel_inbound(
        PROVIDER,
        ChannelMessage(
            channel_id=OWNER, thread_id=OWNER, sender=OWNER, text=text, message_id=f"m-{n}"
        ),
        is_dm=True,
    )


def _the_chat(gateway: GatewayOrchestrator):
    chats = list(gateway.dashboard_state._sessions.values())
    assert len(chats) == 1, "the channel's messages belong to one chat"
    return chats[0]


async def _settled(chat) -> None:
    """Wait until the chat has run every turn it has, queued ones included."""
    for _ in range(500):
        if not chat.running and not chat._queue:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chat never finished its turns")


def _users(chat) -> list[str]:
    return [m["content"] for m in chat.messages if m.get("role") == "user"]


@pytest.mark.asyncio
async def test_the_answer_to_a_channel_message_goes_back_to_the_channel(tmp_path, channel):
    gateway = await _gateway(tmp_path, _Model())
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _from_the_channel(gateway, "What's 2+2?", 1)
        chat = _the_chat(gateway)
        await _settled(chat)

    assert channel.answers() == [
        "It's 4."
    ], "the agent answered in the dashboard and the channel heard nothing back"
    assert all(where == OWNER for _what, where, _text in channel.seen)


@pytest.mark.asyncio
async def test_the_channel_is_not_sent_back_what_it_just_said(tmp_path, channel):
    """The mirror shows the channel what was typed in the DASHBOARD. What the owner sent from the
    channel is already there, so repeating it back is noise."""
    gateway = await _gateway(tmp_path, _Model())
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _from_the_channel(gateway, "What's 2+2?", 1)
        chat = _the_chat(gateway)
        await _settled(chat)
        assert channel.echoes() == []

        # Vacuity floor: in the same chat, a message typed in the dashboard IS shown there.
        chat.append("user", "And 3+3?", "msg msg-u")
        await run_chat(gateway.dashboard_state, chat, "And 3+3?")

    assert [e for e in channel.echoes() if "3+3" in e], "the dashboard's message never showed"
    assert not [e for e in channel.echoes() if "2+2" in e]
    assert channel.answers() == ["It's 4.", "It's 6."]


@pytest.mark.asyncio
async def test_a_message_sent_while_the_agent_is_answering_waits_its_turn(tmp_path, channel):
    """The door queues it, as the dashboard queues a message typed mid-turn. It used to append
    it to the chat as well, so the chat showed it twice once the queue ran it."""
    model = _Model()
    model.hold.clear()
    gateway = await _gateway(tmp_path, model)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _from_the_channel(gateway, "What's 2+2?", 1)
        chat = _the_chat(gateway)
        await asyncio.wait_for(model.started.wait(), timeout=5)
        assert chat.running, "vacuity floor: the second message arrives mid-turn"

        await _from_the_channel(gateway, "Is 5 prime?", 2)
        model.hold.set()
        await _settled(chat)

    assert _users(chat) == ["What's 2+2?", "Is 5 prime?"]
    assert channel.answers() == ["It's 4.", "Yes, 5 is prime."]
    assert channel.echoes() == []
