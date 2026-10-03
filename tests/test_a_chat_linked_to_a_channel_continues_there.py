"""A chat linked to a channel thread continues there: its answers, its notices and replies to it.

`POST /api/chat/sessions/{session}/channel-link` opened a thread on a channel and wrote the thread
into the session manager, and no more. It never said which channel the chat was on, which is what
a reply is sent by (`DashboardState.channel_provider_for`), and it never told the inbound door, so
the chat's next answer, its compaction notices and a reply someone sent in the thread all missed
it. It also opened the thread through whichever channel reaches the owner first, so a Slack
channel's id went to Discord.

The link now goes through the one function the handoff uses (`chat_channel._continue_there`), on
the channel that issued the thread's id (`channel_delivery.channel_of_id`), or the one named.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import links_kept_in_a_session_map

from personalclaw import channel_delivery, channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.llm.events import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)

SLACK_CHANNEL = "C0123ABC456"
SLACK_OWNER = "U0OWNER1"
SLACK_DM = "D0OWNERDM"
THREAD = "1712793600.000200"
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
    d.open_dm = AsyncMock(return_value=SLACK_DM)
    d.deliver_text = AsyncMock(return_value=THREAD)
    d.deliver_chat_mirror = AsyncMock()
    d.start_stream = AsyncMock(return_value="")
    d.append_stream_task = AsyncMock()
    d.stop_stream = AsyncMock()
    return d


@pytest.fixture
def chats(monkeypatch):
    """Connect chat channels by name; each gets a fake delivery, returned by name. Slack knows the
    owner, so the owner's DM opens there."""
    for key in (CRED_OWNER_ID, *(owner_id_credential(p) for p in _IDS)):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(owner_id_credential("slack"), SLACK_OWNER)
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


def _state(tmp_path, monkeypatch, client: Any = None):
    """The dashboard, over a session manager whose channel links are kept in a session map."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    sessions = MagicMock(count=0)
    links_kept_in_a_session_map(sessions)
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
    return state


def _chat(state):
    session = state.get_or_create_session("s1")
    session._titled = True
    session.title = "Trip planning"
    session.append("user", "Which train do we take?", "msg msg-u")
    session.append("assistant", "The 9:10 to the coast.", "msg msg-a")
    return session


async def _link(state, body: dict[str, Any]) -> tuple[int, dict]:
    from personalclaw.dashboard.chat_channel import api_chat_session_channel_link
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/chat/sessions/{session}/channel-link", api_chat_session_channel_link)
    app["state"] = state
    with patch("personalclaw.dashboard.chat_channel.sel", MagicMock()):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/channel-link", json=body)
            return resp.status, await resp.json()


def _client(stream) -> AsyncMock:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    return client


async def _answering(text: str):
    yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _turn(state, session, message: str) -> None:
    from personalclaw.dashboard.chat_runner import run_chat

    session._trust = True
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, message)
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)


# ── the link: the chat continues in the thread ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "where"),
    [({"channel": SLACK_CHANNEL}, SLACK_CHANNEL), ({}, SLACK_DM)],
    ids=["a-channel", "the-owners-dm"],
)
async def test_a_linked_chat_is_answered_told_and_continued_in_its_thread(
    tmp_path, monkeypatch, chats, body, where
):
    """🔴 Red before: the link named no channel, so the next answer and the compaction notice
    stayed in the dashboard, and a reply in the thread found no chat to continue."""
    from personalclaw.dashboard.chat_utils import _broadcast_compaction_result

    slack = chats("slack")["slack"]
    state = _state(tmp_path, monkeypatch, _client(lambda: _answering("Take the 9:10.")))
    session = _chat(state)

    status, linked = await _link(state, body)
    assert status == 200 and linked["thread_ts"] == THREAD, linked

    await _turn(state, session, "and back?")
    slack.deliver_chat_mirror.assert_awaited_with(where, "Take the 9:10.", THREAD)

    notice = await _broadcast_compaction_result(
        state, session, AgentEvent(kind=EVENT_COMPACTION_STATUS, text="completed", title=SUMMARY)
    )
    assert slack.deliver_text.await_args.args[:3] == (where, notice, THREAD)

    assert state.get_linked_session(THREAD) is session, "a reply in the thread opens a new chat"


# ── the thread opens on the channel that issued its id ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_channels_id_opens_the_thread_on_that_channel(tmp_path, monkeypatch, chats):
    """🔴 Red before: the thread opened through whichever channel reaches the owner first, so a
    Slack channel's id, and the chat's last messages, went to Discord."""
    connected = chats("discord", "slack")
    state = _state(tmp_path, monkeypatch)
    _chat(state)

    status, linked = await _link(state, {"channel": SLACK_CHANNEL})

    assert status == 200, linked
    posted = [c.args[:1] for c in connected["slack"].deliver_text.await_args_list]
    assert posted and set(posted) == {(SLACK_CHANNEL,)}, posted
    connected["discord"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_id_no_one_channel_issued_is_refused_and_nothing_is_posted(
    tmp_path, monkeypatch, chats
):
    """🔴 Red before: an id two channels could take was posted to by the first one, with the
    chat's last messages. It is refused with the channels to choose from."""
    connected = chats("discord", "telegram")
    state = _state(tmp_path, monkeypatch)
    _chat(state)

    status, refused = await _link(state, {"channel": SNOWFLAKE})

    assert status == 400 and refused["error"]["code"] == "invalid_request", refused
    assert "Discord, Telegram" in refused["error"]["message"]
    for delivery in connected.values():
        delivery.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_named_channel_takes_an_id_two_channels_could_take(tmp_path, monkeypatch, chats):
    """🔴 Red before: the channel named was not read, and the first channel that reaches the owner
    opened the thread instead. With ``provider`` named, as the handoff takes it, that channel does.
    """
    connected = chats("discord", "telegram")
    state = _state(tmp_path, monkeypatch)
    _chat(state)

    status, linked = await _link(state, {"channel": SNOWFLAKE, "provider": "telegram"})

    assert status == 200, linked
    assert connected["telegram"].deliver_text.await_args_list[0].args[0] == SNOWFLAKE
    connected["discord"].deliver_text.assert_not_awaited()
