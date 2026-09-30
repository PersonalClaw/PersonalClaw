"""A chat that came in on a chat channel lists as that channel's, not as one you opened here.

A message on Telegram starts a chat through the one inbound door (``channel_inbound``): the chat
is created with the channel as its origin tag and linked to the channel's thread
(``DashboardState.link_channel``), which keeps the link under the chat's history key. The chat
list asked for the link under the chat's bare name instead, found none, and so listed every
channel chat as an ordinary chat: no Channels scope, no mark saying where it came from. The chat
list now reads the link the way it is kept, counts a chat whose origin tag is a chat channel as
that channel's, and names the channel as the owner knows it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider

PROVIDER = "fakechat"


class _Channel(ChannelTransportProvider):
    name = property(lambda self: PROVIDER)
    display_name = property(lambda self: "FakeChat")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    channel_transports.register_transport(_Channel())
    yield tmp_path
    channel_transports.unregister_transport(PROVIDER)


def _state(tmp_path):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.session import SessionManager

    state = DashboardState(
        sessions=SessionManager(AppConfig()),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    state.broadcast_ws = MagicMock()
    return state


async def _rows(state) -> dict[str, dict]:
    from personalclaw.dashboard.chat import api_chat_sessions

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/chat/sessions")
        assert resp.status == 200
        return {r["key"]: r for r in await resp.json()}


@pytest.mark.asyncio
async def test_a_chat_the_channel_door_opened_lists_under_its_channel(home):
    state = _state(home)
    # What the inbound door does for a channel's first message
    # (`channel_inbound._route_to_session`).
    chat = state.get_or_create_session(app=PROVIDER)
    state.link_channel(chat.key, "thread-1", "chat-42")
    mine = state.get_or_create_session()

    rows = await _rows(state)

    assert rows[mine.key]["origin"] == "manual", "the control: a chat opened here stays yours"
    row = rows[chat.key]
    assert row["origin"] == "channel"
    assert row["source_label"] == "FakeChat", "it names the channel, not an opaque chat id"
    assert row["source_id"] == "chat-42"


@pytest.mark.asyncio
async def test_a_channel_chat_read_from_disk_lists_under_its_channel(home):
    """The same chat after a restart, when it is on disk and not in memory."""
    state = _state(home)
    log = state.conversation_log
    log.append("dashboard:chat-7-1700000000", "user", "what is on my plate today?")
    log.update_metadata("dashboard:chat-7-1700000000", {"app": PROVIDER})
    state.sessions.set_channel_link("dashboard:chat-7-1700000000", "thread-7", "chat-42")
    log.append("dashboard:chat-8-1700000000", "user", "an ordinary chat")

    rows = await _rows(state)

    assert rows["chat-8-1700000000"]["origin"] == "manual"
    row = rows["chat-7-1700000000"]
    assert row["origin"] == "channel"
    assert row["source_label"] == "FakeChat"


@pytest.mark.asyncio
async def test_a_channel_chat_whose_link_is_gone_is_still_that_channels(home):
    """The origin tag alone says where a chat came from: the door stamps it on every chat."""
    state = _state(home)
    log = state.conversation_log
    log.append("dashboard:chat-9-1700000000", "user", "hello from the phone")
    log.update_metadata("dashboard:chat-9-1700000000", {"app": PROVIDER})

    row = (await _rows(state))["chat-9-1700000000"]
    assert row["origin"] == "channel"
    assert row["source_label"] == "FakeChat"


@pytest.mark.asyncio
async def test_an_apps_conversation_is_not_a_channel_chat(home):
    """The origin tag also carries an app that started a conversation; that is not a channel."""
    state = _state(home)
    chat = state.get_or_create_session(app="some-app")

    assert (await _rows(state))[chat.key]["origin"] == "manual"
