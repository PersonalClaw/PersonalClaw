"""Handing a chat to a channel: the chat's menu names the channel, and the handoff goes there.

``POST /api/chat/sessions/{session}/handoff`` had no caller in the UI. The chat's menu now offers
"Continue on <channel>" for each connected channel, so the request names the channel (``provider``)
and the handoff opens the owner's DM on THAT channel — with the owner id it keeps — rather than on
whichever channel reaches the owner first. A channel id without its channel is refused: an id means
nothing without the channel that issued it (#959).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery, channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID

#: Every channel a test here connects: its owner id, which ``save_credential`` mirrors into the
#: environment, and its transport are given back after the test, whether it passed or not.
_PROVIDERS = ("telegram", "discord", "slackish")


@pytest.fixture(autouse=True)
def _owner_ids_are_this_tests_own(unset_env):
    unset_env(CRED_OWNER_ID, *(owner_id_credential(p) for p in _PROVIDERS))
    yield
    for provider in _PROVIDERS:
        channel_transports.unregister_transport(provider)
        channel_delivery.register(None, provider=provider)


class _Transport(ChannelTransportProvider):
    def __init__(self, name: str, display: str) -> None:
        self._name, self._display = name, display

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


def _connect(provider: str, display: str, owner: str = "") -> MagicMock:
    delivery = MagicMock()
    delivery.open_dm = AsyncMock(side_effect=lambda uid: f"dm-{uid}")
    delivery.deliver_text = AsyncMock(return_value="ts-1")
    channel_transports.register_transport(_Transport(provider, display))
    channel_delivery.register(delivery, provider=provider)
    if owner:
        save_credential(owner_id_credential(provider), owner)
    return delivery


def _state() -> MagicMock:
    state = MagicMock()
    session = MagicMock(key="dashboard:chat-1", title="Plans", _titled=True)
    state.get_session.return_value = session
    state.conversation_log.read_messages.return_value = [{"role": "user", "content": "hi"}]
    state.conversation_log.get_metadata.return_value = {}
    return state


async def _handoff(state: Any, body: dict, session: str = "chat-1") -> tuple[int, dict]:
    from personalclaw.dashboard.chat_channel import api_chat_session_handoff

    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/sessions/{session}/handoff", api_chat_session_handoff)
    with patch("personalclaw.dashboard.chat_channel.save_session_to_history"):
        async with TestClient(TestServer(app)) as c:
            resp = await c.post(f"/api/chat/sessions/{session}/handoff", json=body)
            return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_named_channel_takes_the_handoff_to_its_own_owner():
    telegram = _connect("telegram", "Telegram", owner="4242")
    discord = _connect("discord", "Discord", owner="99887766")
    state = _state()
    state.channel_delivery = telegram  # the first channel — the one an unnamed handoff could pick

    status, body = await _handoff(state, {"provider": "discord"})

    assert status == 200, body
    assert discord.deliver_text.await_args.args[0] == "dm-99887766"
    telegram.deliver_text.assert_not_awaited()
    assert body["provider"] == "discord"


@pytest.mark.asyncio
async def test_a_named_channel_with_no_owner_says_so_and_no_other_channel_is_used():
    telegram = _connect("telegram", "Telegram", owner="4242")
    _connect("discord", "Discord")
    status, body = await _handoff(_state(), {"provider": "discord"})
    assert status == 502
    assert "Discord has no owner id" in body["error"]["message"]
    telegram.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_channel_that_is_not_connected_is_named_in_the_refusal():
    _connect("telegram", "Telegram", owner="4242")
    status, body = await _handoff(_state(), {"provider": "discord"})
    assert status == 404
    assert body["error"]["code"] == "channel_unknown"


@pytest.mark.asyncio
async def test_a_channel_id_without_its_channel_is_refused():
    telegram = _connect("telegram", "Telegram", owner="4242")
    status, body = await _handoff(_state(), {"channel": "C0123"})
    assert status == 400
    telegram.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_channel_id_goes_through_the_channel_that_issued_it():
    telegram = _connect("telegram", "Telegram", owner="4242")
    discord = _connect("discord", "Discord", owner="99887766")
    state = _state()
    state.channel_delivery = telegram
    status, body = await _handoff(state, {"provider": "discord", "channel": "chan-7"})
    assert status == 200, body
    assert discord.deliver_text.await_args.args[0] == "chan-7"
    telegram.deliver_text.assert_not_awaited()


# ── …and a reply there CONTINUES the chat ────────────────────────────────────────────────────
#
# The handoff posted "Reply to continue this session" and then recorded a link the guarded door
# never reads, so the owner's reply opened a NEW chat (measured on a dev gateway against a fake
# Telegram: chat-2, linked to Telegram, beside the handed-off chat-1). Now the handoff links the
# chat where the door looks — by the DM itself on a channel whose DM is one conversation, by the
# thread it opened otherwise — and gives the chat that channel as its origin, so its replies go
# back out there.


class _Capable(_Transport):
    def __init__(self, name: str, display: str, *, dm_thread_is_channel: bool) -> None:
        super().__init__(name, display)
        self._dm = dm_thread_is_channel

    def capabilities(self):
        from personalclaw.sdk.channel import ChannelCapabilities

        return ChannelCapabilities(inbound=True, threads=True, dm_thread_is_channel=self._dm)


@pytest.fixture
def real_state(tmp_path, monkeypatch):
    """A real DashboardState (its channel links are what the door reads), and a trust store here."""
    import personalclaw.providers.entity_routes as er
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    sessions = MagicMock(count=0)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.get_or_create_session("chat-9")
    state.conversation_log.append("dashboard:chat-9", "user", "Plan the launch week.")
    return state


def _connect_capable(provider: str, display: str, *, owner: str, dm_is_thread: bool) -> MagicMock:
    delivery = MagicMock()
    delivery.open_dm = AsyncMock(
        side_effect=lambda uid: str(uid)
    )  # a Telegram DM id IS the user id
    delivery.deliver_text = AsyncMock(return_value="77")  # the posted message's id
    channel_transports.register_transport(
        _Capable(provider, display, dm_thread_is_channel=dm_is_thread)
    )
    channel_delivery.register(delivery, provider=provider)
    save_credential(owner_id_credential(provider), owner)
    return delivery


async def _reply(state, provider: str, *, channel: str, thread: str, sender: str) -> Any:
    """The owner's reply, through the REAL guarded door; returns the chat it reached."""
    from personalclaw import channel_trust
    from personalclaw.channel_inbound import deliver_inbound, reset_admissions
    from personalclaw.channel_transports.base import ChannelMessage

    reset_admissions()
    channel_trust.allow_sender(provider, sender)
    reached: dict = {}

    async def turn_runner(_state, session, _text):
        reached["session"] = session

    services = MagicMock(dashboard_state=state)
    await deliver_inbound(
        services,
        provider,
        ChannelMessage(
            channel_id=channel,
            thread_id=thread,
            sender=sender,
            text="and a budget?",
            message_id="m-1",
        ),
        is_dm=True,
        turn_runner=turn_runner,
    )
    await asyncio.sleep(0)  # the door runs the turn as a task
    return reached.get("session")


@pytest.mark.asyncio
async def test_a_reply_in_the_dm_continues_the_handed_off_chat(real_state):
    _connect_capable("telegram", "Telegram", owner="4242", dm_is_thread=True)
    status, body = await _handoff(real_state, {"provider": "telegram"}, session="chat-9")
    assert status == 200, body

    # Telegram marks every message in a DM with the DM itself as its thread.
    reached = await _reply(real_state, "telegram", channel="4242", thread="4242", sender="4242")
    assert (
        reached is not None and reached.key == "chat-9"
    ), "the owner's reply opened a new chat instead of continuing the one handed off"
    # …and the chat's replies go back out on Telegram.
    assert real_state.channel_provider_for("chat-9") == "telegram"


@pytest.mark.asyncio
async def test_a_reply_in_the_thread_continues_it_where_dms_have_threads(real_state):
    _connect_capable("slackish", "Slackish", owner="U0OWNER", dm_is_thread=False)
    status, body = await _handoff(real_state, {"provider": "slackish"}, session="chat-9")
    assert status == 200, body

    reached = await _reply(real_state, "slackish", channel="U0OWNER", thread="77", sender="U0OWNER")
    assert reached is not None and reached.key == "chat-9"
    # The link is persisted where a channel with its own routing (and the next restart) reads it.
    real_state.sessions.set_channel_link.assert_called_with("dashboard:chat-9", "77", "U0OWNER")


@pytest.mark.asyncio
async def test_a_channel_thread_hands_off_the_transcript_it_keeps_under_its_own_key():
    """A chat that started on a channel keeps its transcript under its BARE key, as the channel
    wrote it. The handoff read the `dashboard:`-prefixed form instead, found nothing there, and
    refused a thread holding a whole conversation as having "no messages to hand off yet"."""
    discord = _connect("discord", "Discord", owner="99887766")
    thread = "telegram:4242:9001"
    transcript = {thread: [{"role": "user", "content": "the deploy is stuck again"}]}
    state = MagicMock()
    state.get_session.return_value = MagicMock(key=thread, title="Deploy", _titled=True)
    state.conversation_log.read_messages.side_effect = lambda key: transcript.get(key, [])
    state.conversation_log.get_metadata.side_effect = lambda key: (
        {"title": "Deploy"} if key in transcript else {}
    )

    status, body = await _handoff(state, {"provider": "discord"}, session=thread)

    assert status == 200, body
    assert discord.deliver_text.await_count == 1
    read = {c.args[0] for c in state.conversation_log.read_messages.call_args_list}
    assert read == {thread}, f"the handoff read {read}, not the thread's own file"
