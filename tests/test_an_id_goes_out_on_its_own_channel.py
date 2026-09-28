"""A chat, channel or user id goes out on the chat channel that issued it.

A message addressed to an id without naming its channel (`via`) was handed to whichever connected
channel sorted first: with Discord and Slack connected, a Slack channel id went to Discord. And
the id itself had to look like a Slack one, so a Telegram chat id or a Discord channel id was
refused as "invalid channel ID format" before any channel was asked.

Now each chat channel set up here is asked whether the id is one of its own
(`ChannelTransportProvider.validate_target`), and a user id whose owner id it is. Exactly one →
that channel. None, or more than one (an 18-digit number is a Telegram chat and a Discord channel
alike), is refused with the channels to choose from, and nothing is sent. The Triggers page and the
chat's automation tools ask the same question of a Send message action where it is saved.
"""

from __future__ import annotations

import os
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery, channel_transports
from personalclaw.action_providers import send_message_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.channel_delivery import channel_of_id, id_problem
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.dashboard.handlers import api_send_message

SLACK_ID = "C0123ABC456"
TELEGRAM_CHAT = "-1001234567890"
SNOWFLAKE = "123456789012345678"  # a Discord channel, and a Telegram chat, alike

_IDS = {
    "slack": ("Slack", re.compile(r"[CDGW][A-Z0-9]+")),
    "telegram": ("Telegram", re.compile(r"-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")),
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
    d.open_dm = AsyncMock(side_effect=lambda uid: f"dm-{uid}")
    d.deliver_text = AsyncMock(return_value="1.0")
    d.is_tracked_channel = MagicMock(return_value=True)
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


# ── which channel an id belongs to ─────────────────────────────────────────────────────────────


def test_an_id_belongs_to_the_one_channel_that_takes_it(chats):
    chats("discord", "slack", "telegram")
    assert channel_of_id(SLACK_ID) == ("slack", "")
    assert channel_of_id(TELEGRAM_CHAT) == ("telegram", "")


def test_an_id_two_channels_take_is_refused_with_them(chats):
    chats("discord", "slack", "telegram")
    assert channel_of_id(SNOWFLAKE) == (
        "",
        f"{SNOWFLAKE} could be a chat or channel on Discord, Telegram. "
        "Say which one to send it on.",
    )


def test_an_id_no_channel_takes_is_refused_with_the_channels_set_up(chats):
    chats("discord", "slack")
    key, problem = channel_of_id("-42")
    assert key == ""
    assert problem == (
        "No chat channel set up here takes -42 as a chat or channel id. The chat channels set up "
        "here: Discord, Slack. Say which one to send it on."
    )


def test_with_no_chat_channel_set_up_an_id_has_no_channel_and_no_problem(chats):
    assert channel_of_id(SLACK_ID) == ("", "")


def test_a_channel_whose_check_raises_claims_nothing(chats, monkeypatch):
    chats("slack", "telegram")
    monkeypatch.setattr(
        _Chat, "validate_target", lambda self, t: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert channel_of_id(SLACK_ID)[0] == ""


def test_a_user_id_belongs_to_the_channel_whose_owner_it_is(chats, monkeypatch):
    chats("slack", "telegram")
    monkeypatch.setenv(owner_id_credential("telegram"), "4242")
    monkeypatch.setenv(owner_id_credential("slack"), "U0OWNER")
    assert channel_of_id("4242", user=True) == ("telegram", "")
    # Slack spells one user with a U or a W in front.
    assert channel_of_id("W0OWNER", user=True) == ("slack", "")


def test_a_user_id_that_is_the_owner_on_two_channels_is_refused(chats, monkeypatch):
    """Only the shared owner id set: every channel reads it as its owner."""
    chats("slack", "telegram")
    monkeypatch.setenv(CRED_OWNER_ID, "U0OWNER")
    assert channel_of_id("U0OWNER", user=True) == (
        "",
        "U0OWNER is the owner's user id on Slack, Telegram. Say which one to send it on.",
    )


@pytest.mark.parametrize("bad", ["", "C01 23", "C01\n23", "x" * 257, "C01\u200b23"])
def test_what_no_id_can_be(bad):
    assert id_problem(bad)


# ── the send-message route (the notify tool, a schedule script's ctx.notify) ──────────────────


def _post(payload: dict[str, Any], owner_id: str = "") -> Any:
    state = MagicMock()
    state.owner_id = owner_id
    app = web.Application()
    app.router.add_post("/api/send-message", api_send_message)
    app["state"] = state
    return app, state


async def _send(payload: dict[str, Any], *, owner_id: str = "") -> tuple[int, dict, MagicMock]:
    app, state = _post(payload, owner_id)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/send-message", json=payload)
        return resp.status, await resp.json(), state


@pytest.mark.asyncio
async def test_a_channel_id_goes_out_on_its_own_channel(chats):
    """🔴 Before: Discord (first by name) was handed the Slack channel id."""
    sent = chats("discord", "slack")
    status, body, _state = await _send({"text": "Standup in five.", "channel": SLACK_ID})
    assert (status, body["ok"], body["channel"]) == (200, True, True), body
    sent["slack"].deliver_text.assert_awaited_once()
    assert sent["slack"].deliver_text.await_args.args[:2] == (SLACK_ID, "Standup in five.")
    sent["discord"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_another_channels_id_is_sent_too(chats):
    """🔴 Before: 400 "invalid channel ID format": only a Slack-shaped id got past the route."""
    sent = chats("slack", "telegram")
    status, body, _state = await _send({"text": "Bin night.", "channel": TELEGRAM_CHAT})
    assert (status, body["ok"]) == (200, True), body
    assert sent["telegram"].deliver_text.await_args.args[:2] == (TELEGRAM_CHAT, "Bin night.")
    sent["slack"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_id_two_channels_could_have_issued_is_refused_and_nothing_is_sent(chats):
    sent = chats("discord", "telegram")
    status, body, state = await _send({"text": "Bin night.", "channel": SNOWFLAKE})
    assert status == 200 and body["ok"] is False
    assert body["error"] == (
        f"{SNOWFLAKE} could be a chat or channel on Discord, Telegram. "
        "Say which one to send it on."
    )
    for delivery in sent.values():
        delivery.deliver_text.assert_not_awaited()
    state.notify.assert_not_called()


@pytest.mark.asyncio
async def test_naming_the_channel_settles_it(chats):
    sent = chats("discord", "telegram")
    status, body, _state = await _send(
        {"text": "Bin night.", "channel": SNOWFLAKE, "via": "telegram"}
    )
    assert (status, body["ok"]) == (200, True), body
    sent["telegram"].deliver_text.assert_awaited_once()
    sent["discord"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_id_the_named_channel_does_not_take_is_refused_in_its_words(chats):
    sent = chats("slack", "telegram")
    status, body, _state = await _send({"text": "hi", "channel": SLACK_ID, "via": "telegram"})
    assert (status, body["ok"], body["error"]) == (200, False, "That isn't a Telegram id.")
    sent["telegram"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_allowlist_asked_is_the_ids_own_channels(chats):
    """Discord's tracked channels say nothing about a Slack channel."""
    sent = chats("discord", "slack")
    sent["discord"].is_tracked_channel.return_value = True
    sent["slack"].is_tracked_channel.return_value = False
    status, _body, _state = await _send({"text": "hi", "channel": SLACK_ID})
    assert status == 403
    sent["slack"].is_tracked_channel.assert_called_once_with(SLACK_ID)
    sent["discord"].is_tracked_channel.assert_not_called()


@pytest.mark.asyncio
async def test_the_owners_user_id_is_dmed_on_the_channel_that_knows_them_by_it(chats, monkeypatch):
    """🔴 Before: 400 "invalid user ID format" for a Telegram user id."""
    sent = chats("discord", "telegram")
    monkeypatch.setenv(owner_id_credential("telegram"), "4242")
    status, body, _state = await _send({"text": "Your parcel is here.", "user": "4242"})
    assert (status, body["ok"]) == (200, True), body
    sent["telegram"].open_dm.assert_awaited_once_with("4242")
    sent["discord"].open_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_user_who_is_not_the_owner_anywhere_is_refused(chats, monkeypatch):
    sent = chats("telegram")
    monkeypatch.setenv(owner_id_credential("telegram"), "4242")
    status, body, _state = await _send({"text": "hi", "user": "9999"})
    assert status == 403, body
    sent["telegram"].open_dm.assert_not_awaited()


# ── the Send message action, when it fires and where it is saved ──────────────────────────────


@pytest.fixture
def services(monkeypatch):
    from personalclaw.action_providers import services as svc

    state = MagicMock()
    monkeypatch.setattr(svc, "_services", svc.ActionServices(state=state))
    return state


@pytest.mark.asyncio
async def test_the_action_sends_an_id_on_its_own_channel(chats, services):
    """🔴 Before: the first channel by name was handed the id."""
    sent = chats("discord", "slack")
    result = await send_message_provider.SendMessageActionProvider().execute(
        {"text_template": "Standup in five.", "channel": SLACK_ID}, ActionContext(event="clock")
    )
    assert result.success, result.error
    sent["slack"].deliver_text.assert_awaited_once_with(SLACK_ID, "Standup in five.")
    sent["discord"].deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_action_refuses_an_id_two_channels_could_have_issued(chats, services):
    sent = chats("discord", "telegram")
    result = await send_message_provider.SendMessageActionProvider().execute(
        {"text_template": "hi", "channel": SNOWFLAKE}, ActionContext(event="clock")
    )
    assert result.success is False
    assert result.error.startswith(f"send-message: {SNOWFLAKE} could be a chat or channel on")
    for delivery in sent.values():
        delivery.deliver_text.assert_not_awaited()


@pytest.mark.parametrize(
    ("config", "problem"),
    [
        ({"via": "carrier pigeon"}, "carrier pigeon isn't one of the chat channels set up here."),
        ({"via": "telegram", "channel": SLACK_ID}, "That isn't a Telegram id."),
        ({"channel": SNOWFLAKE}, f"{SNOWFLAKE} could be a chat or channel on Discord, Telegram."),
    ],
)
def test_what_a_send_message_action_is_refused_for_where_it_is_saved(chats, config, problem):
    chats("discord", "slack", "telegram")
    assert send_message_provider.config_problem(config).startswith(problem)


def test_a_send_message_action_that_can_send_saves(chats):
    chats("discord", "slack", "telegram")
    for config in ({}, {"via": "Telegram"}, {"channel": SLACK_ID}, {"via": "discord"}):
        assert send_message_provider.config_problem(config) == "", config


@pytest.fixture
def trigger_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    import personalclaw.dashboard.handlers.triggers as h
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(h, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    monkeypatch.setattr(h, "_trigger_store", lambda: store)
    return store


async def _save(action_config: dict[str, Any]) -> tuple[int, dict]:
    import personalclaw.dashboard.handlers.triggers as h

    app = web.Application()
    app["state"] = MagicMock()
    h.register_trigger_routes(app)
    body = {
        "trigger_type": "schedule",
        "name": "Bin night",
        "every": 3600,
        "action": {"provider": "send-message", "config": action_config},
        "confirm": True,
    }
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/triggers", json=body)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_triggers_page_refuses_a_channel_not_set_up_here(chats, trigger_home):
    """🔴 Before: it saved, and every fire failed "carrier pigeon isn't connected"."""
    chats("slack", "telegram")
    status, body = await _save({"text_template": "Bins out tonight.", "via": "carrier pigeon"})
    assert status == 400, body
    assert body["error"]["message"] == (
        "carrier pigeon isn't one of the chat channels set up here. The chat channels set up "
        "here: Slack, Telegram."
    )
    assert trigger_home.load() == []


@pytest.mark.asyncio
async def test_the_triggers_page_saves_a_send_message_that_can_send(chats, trigger_home):
    chats("slack", "telegram")
    status, body = await _save({"text_template": "Bins out tonight.", "via": "Telegram"})
    assert status == 200, body
    (row,) = trigger_home.load()
    assert row.trigger.workflow["inline"]["config"]["via"] == "Telegram"


def test_the_chats_automation_update_asks_the_same(chats, tmp_path):
    from personalclaw.triggers import tools
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    chats("discord", "telegram")
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:bins",
            name="Bin night",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"inline": {"provider": "send-message", "config": {"text_template": "x"}}},
        )
    )
    result = tools.update(
        store,
        trigger_id="clock:bins",
        patch={
            "workflow": {
                "inline": {
                    "provider": "send-message",
                    "config": {"text_template": "x", "channel": SNOWFLAKE},
                }
            }
        },
    )
    assert result.ok is False
    assert result.text.startswith(f"Error: {SNOWFLAKE} could be a chat or channel on")
