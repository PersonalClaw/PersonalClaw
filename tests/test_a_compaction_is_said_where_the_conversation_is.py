"""A compaction is said where the conversation is: its dashboard chat, and the channel it is on.

Core said every compaction notice in the dashboard chat alone: a `/compact`'s result, whether the
pass ran in the agent or arrived later, the agent compacting on its own in the middle of a turn,
and the session manager restarting a session at the context threshold. A conversation held on
Telegram, Discord, email or a Slack thread lost its middle there without a word.

Each notice now also goes to the thread the conversation is linked to, where its replies go: a
chat that came from a channel is told on that channel, and a channel's own thread, which is no
chat here (a Slack thread its app runs), on the channel that issued the thread's id, never on one
that merely happens to be connected. One path for every notice
(`DashboardState.tell_linked_channel`), so no channel app keeps a copy of it.
"""

from __future__ import annotations

import os
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery, channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.llm.events import (
    COMPACTION_AUTOMATIC,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)

TELEGRAM_CHAT = "-1001234567890"
SLACK_CHANNEL = "C0123ABC456"
SLACK_THREAD = "1712793600.000100"
SNOWFLAKE = "123456789012345678"  # a Discord channel, and a Telegram chat, alike
SUMMARY = "freed 40% of the conversation (10,000 → 6,000 characters)"

_IDS = {
    "slack": ("Slack", re.compile(r"[CDGW][A-Z0-9]+")),
    "telegram": ("Telegram", re.compile(r"-?\d{1,20}")),
    "discord": ("Discord", re.compile(r"\d{17,20}")),
}


class _Chat(ChannelTransportProvider):
    """A chat channel that knows its own ids, as the shipped channel apps do."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._display, self._ids = _IDS[name]

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def validate_target(self, target: str) -> str:
        return "" if self._ids.fullmatch(target) else f"That isn't a {self._display} id."


def _delivery() -> MagicMock:
    d = MagicMock()
    d.deliver_text = AsyncMock(return_value="2.0")
    d.deliver_chat_mirror = AsyncMock()
    d.start_stream = AsyncMock(return_value="")
    d.append_stream_task = AsyncMock()
    d.stop_stream = AsyncMock()
    return d


@pytest.fixture
def chats(monkeypatch):
    """Connect chat channels by name; each gets a fake delivery, returned by name."""
    for key in (CRED_OWNER_ID, *(owner_id_credential(p) for p in _IDS)):
        monkeypatch.delenv(key, raising=False)
    added: dict[str, MagicMock] = {}

    def _connect(*names: str) -> dict[str, MagicMock]:
        for name in names:
            channel_transports.register_transport(_Chat(name))
            added[name] = _delivery()
            channel_delivery.register(added[name], provider=name)
        return added

    yield _connect
    for name in added:
        channel_transports.unregister_transport(name)
        channel_delivery.register(None, provider=name)
    for key in (CRED_OWNER_ID, *(owner_id_credential(p) for p in _IDS)):
        os.environ.pop(key, None)


def _told(delivery: MagicMock) -> list[tuple[Any, ...]]:
    """Every notice a channel was handed: ``(channel, text, thread)``. A reply goes as a mirror
    (`deliver_chat_mirror`), so what goes as plain text is PersonalClaw's own word."""
    return [tuple(c.args[:3]) for c in delivery.deliver_text.await_args_list]


def _state(tmp_path, monkeypatch, client: Any = None):
    """The dashboard, over a session manager whose channel links are a dict."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    links: dict[str, tuple[str, str]] = {}
    on: dict[str, str] = {}  # the channel each link names

    def _link(key: str, ts: str, channel: str, *, channel_provider: str = "") -> None:
        links[key] = (ts, channel)
        on[key] = channel_provider if ts else ""

    sessions = MagicMock(count=0)
    sessions.get_channel_link = MagicMock(side_effect=lambda key: links.get(key, (None, None)))
    sessions.get_channel_provider = MagicMock(side_effect=lambda key: on.get(key, ""))
    sessions.set_channel_link = MagicMock(side_effect=_link)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
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
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    state._links = links  # type: ignore[attr-defined]
    return state


def _from_telegram(state):
    """A chat someone started on Telegram, linked to their chat there as the inbound door does."""
    session = state.get_or_create_session(app="telegram")
    state.link_channel(session.key, TELEGRAM_CHAT, TELEGRAM_CHAT, provider="telegram")
    session._titled = True
    return session


def _client(stream) -> AsyncMock:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    return client


async def _turn(state, session, message: str) -> None:
    import asyncio

    from personalclaw.dashboard.chat_runner import run_chat

    session._trust = True
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, message, arrived_from_channel=True)
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)


def _said(session) -> list[str]:
    return [m["content"] for m in session.messages if m.get("role") == "assistant"]


# ── a compaction's outcome, said in the chat and on its channel ───────────────────────────────


@pytest.mark.asyncio
async def test_the_agents_own_pass_is_said_on_the_channel_the_chat_is_on(
    tmp_path, monkeypatch, chats
):
    """🔴 Red before: Telegram heard the answer and never that the conversation lost its middle."""
    telegram = chats("telegram")["telegram"]

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Looking through the logs.")
        yield AgentEvent(kind=EVENT_COMPACTION_STATUS, text=COMPACTION_AUTOMATIC, title=SUMMARY)
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Here is what I found.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    state = _state(tmp_path, monkeypatch, _client(stream))
    session = _from_telegram(state)

    await _turn(state, session, "why did the job fail?")

    notice = f"Conversation compacted: {SUMMARY}"
    assert _told(telegram) == [(TELEGRAM_CHAT, notice, TELEGRAM_CHAT)]
    assert notice in _said(session), "the dashboard chat says it as well"


def _compacting(*, deferred: bool):
    """A client whose `/compact` reports its result in the command's stream, or later, as an agent
    that compacts asynchronously does (`wait_for_compaction`)."""

    async def stream_command(command):
        if not deferred:
            yield AgentEvent(kind=EVENT_COMPACTION_STATUS, text="completed", title=SUMMARY)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = _client(lambda: stream_command("/compact"))
    client.compacts_in_process = not deferred
    client.supports_native_commands = deferred
    client.stream_command = stream_command
    client.wait_for_compaction = AsyncMock(return_value={"type": "completed", "summary": SUMMARY})
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True], ids=["in-the-command", "later"])
async def test_a_compact_typed_on_a_channel_is_answered_there(
    tmp_path, monkeypatch, chats, deferred
):
    """🔴 Red before: `/compact` sent from Telegram was answered in the dashboard alone."""
    telegram = chats("telegram")["telegram"]
    state = _state(tmp_path, monkeypatch, _compacting(deferred=deferred))
    session = _from_telegram(state)

    await _turn(state, session, "/compact")

    notice = f"Conversation compacted: {SUMMARY}"
    assert _told(telegram) == [(TELEGRAM_CHAT, notice, TELEGRAM_CHAT)]
    assert notice in _said(session)


@pytest.mark.asyncio
async def test_a_chat_on_no_channel_is_told_in_the_dashboard_alone(tmp_path, monkeypatch, chats):
    """The control: a chat no channel is linked to says it where it is, and nothing is sent."""
    telegram = chats("telegram")["telegram"]
    state = _state(tmp_path, monkeypatch, _compacting(deferred=False))
    session = state.get_or_create_session("s1")
    session._titled = True

    await _turn(state, session, "/compact")

    assert f"Conversation compacted: {SUMMARY}" in _said(session)
    assert _told(telegram) == []


# ── the session manager's restart at the threshold, said on every conversation's channel ─────


#: Why the session manager restarted an agent CLI's session (`session._why_restarted`).
REASON = "PersonalClaw cannot compact this agent's context"


def _restart_notice(state):
    """The one notice the session manager raises when it restarts a session at the threshold
    (`SessionManager.set_restart_callback`), as the dashboard registers it."""
    state.wire_session_restart_callback()
    return state.sessions.set_restart_callback.call_args[0][0]


def _restarted(pct: int) -> str:
    return (
        f"Restarted the agent's session at {pct}% of its context window: {REASON}. It continues "
        "from what was said here, without the results of its earlier tool calls."
    )


@pytest.mark.asyncio
async def test_a_restart_is_said_in_the_chat_and_on_its_channel(tmp_path, monkeypatch, chats):
    """🔴 Red before: the notice went to the dashboard chat and never to Telegram."""
    telegram = chats("telegram")["telegram"]
    state = _state(tmp_path, monkeypatch)
    session = _from_telegram(state)

    await _restart_notice(state)(f"dashboard:{session.key}", 92.0, REASON)

    notice = _restarted(92)
    assert _told(telegram) == [(TELEGRAM_CHAT, notice, TELEGRAM_CHAT)]
    assert _said(session)[-1] == notice


@pytest.mark.asyncio
async def test_a_restart_is_said_in_a_channels_own_thread(tmp_path, monkeypatch, chats):
    """🔴 Red before: a Slack thread its app runs is no dashboard chat, so the one notice skipped
    it and the thread was told nothing. It is told on the channel that issued its id."""
    connected = chats("slack", "telegram")
    state = _state(tmp_path, monkeypatch)
    state._links[SLACK_THREAD] = (SLACK_THREAD, SLACK_CHANNEL)

    await _restart_notice(state)(SLACK_THREAD, 88.0, REASON)

    notice = _restarted(88)
    assert _told(connected["slack"]) == [(SLACK_CHANNEL, notice, SLACK_THREAD)]
    assert _told(connected["telegram"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "link"),
    [
        ("cron:daily-digest", None),
        (SLACK_THREAD, (SLACK_THREAD, SNOWFLAKE)),
    ],
    ids=["no-thread", "an-id-two-channels-take"],
)
async def test_a_restart_goes_to_no_channel_it_cannot_place(
    tmp_path, monkeypatch, chats, key, link
):
    """The controls: a session no thread is linked to tells no channel, and neither does one
    whose thread id two connected channels could have issued; no channel stands in for another."""
    connected = chats("discord", "slack", "telegram")
    state = _state(tmp_path, monkeypatch)
    if link is not None:
        state._links[key] = link

    await _restart_notice(state)(key, 90.0, REASON)

    assert all(_told(d) == [] for d in connected.values())
