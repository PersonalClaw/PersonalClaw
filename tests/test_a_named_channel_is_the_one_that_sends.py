"""A message the owner asked for on one chat channel goes out there, and on no other.

"Every Wednesday at 18:00, message me on Telegram: 'Bins out tonight.'" had nowhere to put
"Telegram". Every message for the owner (the agent's ``notify``, a trigger's ``send-message``
action, the automation the chat made for it) went to the first connected channel that reached
them, in name order, so with Discord and Telegram both paired it landed on Discord and nothing
said so.

A message can now name its channel (``via``), and it is tried there alone. When that channel cannot
take it, it goes to the Inbox saying why. A name that is not a chat channel set up here is refused
with the ones that are, so the owner can be asked which. Another channel never stands in.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery, channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.inbox import InboxStore
from personalclaw.nl_to_cron import Schedule
from personalclaw.tool_providers.base import ToolFailure

DISCORD_OWNER = "998877665544332211"
TELEGRAM_OWNER = "4242"
BINS = "Bins out tonight."
_PROVIDERS = ("discord", "telegram")


@pytest.fixture(autouse=True)
def _owner_ids_are_this_tests_own(monkeypatch):
    """`save_credential` mirrors a named key into os.environ: keep each test's owner ids its own."""
    keys = (CRED_OWNER_ID, *(owner_id_credential(p) for p in _PROVIDERS))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield
    for key in keys:
        os.environ.pop(key, None)


class _Transport(ChannelTransportProvider):
    """Just enough of a transport to give a channel its name and the name it is shown under."""

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


def _delivery() -> MagicMock:
    d = MagicMock()
    d.open_dm = AsyncMock(side_effect=lambda uid: f"dm-{uid}")
    d.deliver_text = AsyncMock(return_value="1.0")
    d.deliver_rich = AsyncMock(return_value="1.0")
    return d


@pytest.fixture
def channels(monkeypatch):
    """Set up fake chat channels: the transport that names one, and, when connected, its handle.

    The channels set up here are exactly these: the transport registry is this test's own, since a
    refusal lists every channel it holds."""
    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(channel_transports, "_apps", {})

    def _set_up(provider: str, display: str, *, connected: bool = True) -> MagicMock | None:
        channel_transports.register_transport(_Transport(provider, display))
        if not connected:
            return None
        delivery = _delivery()
        channel_delivery.register(delivery, provider=provider)
        return delivery

    return _set_up


def _discord_and_telegram(channels, *, telegram_knows_you: bool = True):
    """Both paired, each with its own owner id: Discord comes first by name."""
    discord = channels("discord", "Discord")
    telegram = channels("telegram", "Telegram")
    save_credential(owner_id_credential("discord"), DISCORD_OWNER)
    if telegram_knows_you:
        save_credential(owner_id_credential("telegram"), TELEGRAM_OWNER)
    return discord, telegram


def _state(tmp_path, first: Any = None) -> MagicMock:
    """A dashboard state whose Inbox is a real store in this test's directory."""
    state = MagicMock()
    state._inbox_svc = None
    state._inbox_store = InboxStore(path=tmp_path / "inbox.json")
    state.channel_delivery = first
    return state


def _inbox(state: MagicMock) -> list:
    return state._inbox_store.pending()


@pytest.fixture
def quiet_sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield


async def _send(state: MagicMock, body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import api_send_message

    app = web.Application()
    app.router.add_route("POST", "/api/send-message", api_send_message)
    app["state"] = state
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/send-message", json=body)
        return resp.status, await resp.json()


# ── a message for the owner (`notify`) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("named", ["telegram", "Telegram"])
async def test_a_message_asked_for_on_telegram_goes_out_on_telegram(
    channels, tmp_path, quiet_sel, named
):
    """🔴 Before: `via` was read by nothing, and the message went to Discord, first by name."""
    discord, telegram = _discord_and_telegram(channels)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS, "via": named})

    assert status == 200, body
    assert body == {"ok": True, "channel": True, "session": False, "ts": "1.0"}
    telegram.deliver_text.assert_awaited_once()
    assert telegram.deliver_text.await_args.args[:2] == (f"dm-{TELEGRAM_OWNER}", BINS)
    discord.open_dm.assert_not_awaited()
    discord.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_when_telegram_cannot_take_it_the_inbox_says_why_and_discord_is_not_asked(
    channels, tmp_path, quiet_sel
):
    """Telegram is paired but was never told who you are: the message is not lost, and it does not
    go to Discord, which could have delivered it."""
    discord, telegram = _discord_and_telegram(channels, telegram_knows_you=False)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS, "via": "telegram"})

    assert status == 200, body
    sentence = (
        "This was for Telegram only, and it could not go out there: Telegram has no owner id."
    )
    assert body["ok"] is True and body["channel"] is False
    assert body["inbox"] is True and body["detail"] == sentence
    [item] = _inbox(state)
    assert item.message == f"Agent Message\n\n{BINS}\n\n{sentence}"
    discord.open_dm.assert_not_awaited()
    discord.deliver_text.assert_not_awaited()
    telegram.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_channel_set_up_but_not_connected_is_said_so(channels, tmp_path, quiet_sel):
    """Set up, with its connection down: that is the reason, not "no channel at all" (which would
    leave the dashboard's note standing in for the channel asked for)."""
    discord = channels("discord", "Discord")
    channels("telegram", "Telegram", connected=False)
    save_credential(owner_id_credential("discord"), DISCORD_OWNER)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS, "via": "telegram"})

    assert status == 200, body
    assert body["inbox"] is True
    assert body["detail"] == (
        "This was for Telegram only, and it could not go out there: Telegram isn't connected."
    )
    discord.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_name_that_is_no_channel_here_is_refused_with_the_ones_that_are(
    channels, tmp_path, quiet_sel
):
    """So the owner can be asked which. Nothing is sent anywhere: not a channel, not the Inbox, not
    a dashboard note."""
    discord, telegram = _discord_and_telegram(channels)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS, "via": "signal"})

    assert status == 200, body
    assert body == {
        "ok": False,
        "channel": False,
        "error": "signal isn't one of the chat channels set up here. "
        "The chat channels set up here: Discord, Telegram.",
    }
    for delivery in (discord, telegram):
        delivery.open_dm.assert_not_awaited()
        delivery.deliver_text.assert_not_awaited()
    state.notify.assert_not_called()
    assert _inbox(state) == []


@pytest.mark.asyncio
async def test_a_channel_id_named_with_its_channel_goes_through_that_channel(
    channels, tmp_path, quiet_sel
):
    """A chat's id means something only to the channel that issued it, so the named channel's
    handle checks it is tracked and sends it."""
    discord, telegram = _discord_and_telegram(channels)
    telegram.is_tracked_channel = MagicMock(return_value=True)
    discord.is_tracked_channel = MagicMock(return_value=True)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS, "via": "telegram", "channel": "C0123ABC456"})

    assert status == 200, body
    telegram.is_tracked_channel.assert_called_once_with("C0123ABC456")
    assert telegram.deliver_text.await_args.args[:2] == ("C0123ABC456", BINS)
    discord.is_tracked_channel.assert_not_called()
    discord.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_no_channel_named_the_first_that_reaches_you_still_sends(
    channels, tmp_path, quiet_sel
):
    """The order a message without a channel follows is unchanged: the first by name."""
    discord, telegram = _discord_and_telegram(channels)
    state = _state(tmp_path, first=discord)

    status, body = await _send(state, {"text": BINS})

    assert status == 200 and body["channel"] is True, body
    assert discord.deliver_text.await_args.args[:2] == (f"dm-{DISCORD_OWNER}", BINS)
    telegram.deliver_text.assert_not_awaited()


def test_the_notify_tool_carries_the_channel_and_it_outranks_the_cron_default():
    from personalclaw.mcp_core import _call_tool

    with (
        patch("personalclaw.mcp_core._post", return_value={"ok": True}) as post,
        patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "cron:abc123"}),
    ):
        _call_tool("notify", {"text": BINS, "via": "telegram"})

    path, payload = post.call_args.args
    assert path == "/api/send-message"
    assert payload["via"] == "telegram"
    assert "session" not in payload, "a cron's reply went back to its chat, not to Telegram"


def test_a_refused_channel_reaches_the_agent_as_a_failure_with_its_sentence():
    """🔴 Before: any refusal came back as the text "Failed: {…}", which reads as a success."""
    from personalclaw.mcp_core import _call_tool

    sentence = (
        "signal isn't one of the chat channels set up here. "
        "The chat channels set up here: Discord, Telegram."
    )
    with patch("personalclaw.mcp_core._post", return_value={"ok": False, "error": sentence}):
        out = _call_tool("notify", {"text": BINS, "via": "signal"})

    assert isinstance(out, ToolFailure)
    assert out == f"Error: {sentence}"


# ── the automation the chat makes ────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _create(home, **args: Any):
    """`automation_create` as the chat calls it, the cadence read without a model."""
    from personalclaw import mcp_automation
    from personalclaw.triggers import tools as T

    real = T.create

    def _create_without_a_model(*a: Any, **kw: Any):
        return real(*a, cadence_to_cron=lambda _c: Schedule(expr="0 18 * * 3"), **kw)

    with patch.object(T, "create", _create_without_a_model):
        return mcp_automation._call_tool("automation_create", args)


def _action(home, trigger_id: str) -> dict:
    from personalclaw.triggers.schedule_view import _inline_action
    from personalclaw.triggers.store import TriggerStore

    row = TriggerStore(base_dir=home).get(trigger_id)
    assert row is not None, "nothing was saved"
    return _inline_action(row.trigger)


async def _fire(action: dict, state: MagicMock):
    """Run the saved action through the provider the fire path dispatches it to."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    provider = get_action_provider(action["provider"])
    with patch(
        "personalclaw.action_providers.send_message_provider.get_action_services",
        return_value=MagicMock(state=state),
    ):
        return await provider.execute(action["config"], ActionContext(event="clock", context=""))


_BIN_NIGHT = {
    "name": "Bin night",
    "when": "every Wednesday at 18:00",
    "say": BINS,
    "via": "telegram",
}


@pytest.mark.asyncio
async def test_message_me_on_telegram_is_an_automation_that_sends_on_telegram(
    channels, home, tmp_path
):
    """🔴 Before: the chat could make only an agent task, with nowhere to say "Telegram"; what
    reached the owner went out on Discord, first by name."""
    discord, telegram = _discord_and_telegram(channels)

    out = _create(home, **_BIN_NIGHT)

    assert not isinstance(out, ToolFailure), out
    assert f"sends you “{BINS}” on Telegram, and on no other channel" in out
    action = _action(home, "clock:bin-night")
    assert action == {
        "provider": "send-message",
        "config": {"text_template": BINS, "via": "telegram"},
    }

    result = await _fire(action, _state(tmp_path, first=discord))

    assert result.success, result.error
    assert telegram.deliver_text.await_args.args == (f"dm-{TELEGRAM_OWNER}", BINS)
    discord.open_dm.assert_not_awaited()
    discord.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fire_telegram_cannot_take_goes_to_the_inbox_saying_why(channels, home, tmp_path):
    """The owner id was never set on Telegram: it says so, loudly, and Discord is not used."""
    discord, telegram = _discord_and_telegram(channels, telegram_knows_you=False)
    _create(home, **_BIN_NIGHT)
    state = _state(tmp_path, first=discord)

    result = await _fire(_action(home, "clock:bin-night"), state)

    [item] = _inbox(state)
    assert item.message.endswith(
        "This was for Telegram only, and it could not go out there: Telegram has no owner id."
    )
    assert BINS in item.message
    assert "Telegram has no owner id" in result.stdout
    discord.deliver_text.assert_not_awaited()
    telegram.deliver_text.assert_not_awaited()


def test_a_channel_not_set_up_here_is_refused_so_the_owner_can_be_asked_which(channels, home):
    _discord_and_telegram(channels)

    out = _create(home, **{**_BIN_NIGHT, "via": "signal"})

    assert isinstance(out, ToolFailure)
    sentence, data = out.split("\n\n", 1)
    assert sentence == (
        "Error: nothing was saved: signal isn't one of the chat channels set up here. The chat "
        "channels set up here: Discord, Telegram. Ask the owner which one to use."
    )
    assert data == '<automation-data>{"via": "signal"}</automation-data>'
    from personalclaw.triggers.store import TriggerStore

    assert TriggerStore(base_dir=home).load() == []


def _delivery_of(home, trigger_id: str) -> str:
    from personalclaw.triggers.store import TriggerStore

    row = TriggerStore(base_dir=home).get(trigger_id)
    assert row is not None, "nothing was saved"
    return row.trigger.delivery


def test_a_channel_for_a_task_delivers_its_result_there(channels, home):
    """🔴 Before: refused ("a task in `message` reports its result in PersonalClaw, not on a chat
    channel"), though a trigger made on the Triggers page sends its result to the Notify channel it
    names. A task's result now takes that same route."""
    _discord_and_telegram(channels)

    out = _create(
        home,
        name="Bin night",
        when="every Wednesday at 18:00",
        message="summarise the week's bins",
        via="telegram",
    )

    assert not isinstance(out, ToolFailure), out
    assert "sends you what it produced on Telegram, and on no other channel" in out
    assert _delivery_of(home, "clock:bin-night") == "channel:telegram"


class _Ids(_Transport):
    """A channel whose chats have ids it checks, as a real one does."""

    def validate_target(self, target: str) -> str:
        return "" if target.startswith("C") else "A chat id here starts with C, like C0123456789."


def test_a_chat_on_the_channel_is_where_the_result_goes(channels, home, monkeypatch):
    """`to` is a chat on the `via` channel: the result goes there, by the channel's own id."""
    monkeypatch.setattr(channel_transports, "_transports", {})
    channel_transports.register_transport(_Ids("chatty", "Chatty"))

    out = _create(
        home, name="Digest", when="every Monday at 9", message="digest", via="chatty", to="C0123"
    )

    assert not isinstance(out, ToolFailure), out
    assert "on Chatty to C0123, and on no other channel" in out
    assert _delivery_of(home, "clock:digest") == "channel:chatty:C0123"


def test_a_chat_the_channel_does_not_take_is_refused_in_its_words(channels, home, monkeypatch):
    monkeypatch.setattr(channel_transports, "_transports", {})
    channel_transports.register_transport(_Ids("chatty", "Chatty"))

    out = _create(
        home, name="Digest", when="every Monday at 9", message="digest", via="chatty", to="#agent"
    )

    assert isinstance(out, ToolFailure)
    assert "A chat id here starts with C" in out
    from personalclaw.triggers.store import TriggerStore

    assert TriggerStore(base_dir=home).load() == []


def test_a_chat_with_no_channel_is_refused(home):
    out = _create(home, name="Digest", when="every Monday at 9", message="digest", to="C0123")

    assert isinstance(out, ToolFailure)
    assert "give `via` too" in out


def test_words_and_a_task_together_are_refused(home):
    out = _create(home, name="Bin night", when="every Wednesday", say=BINS, message="also this")

    assert isinstance(out, ToolFailure)
    assert "not both" in out


@pytest.mark.asyncio
async def test_the_words_go_out_as_written(channels, home, tmp_path):
    """A `$` the action's template would read as a placeholder is sent as the owner wrote it."""
    discord, telegram = _discord_and_telegram(channels)
    words = "Bring $EVENT tickets and $5 for $CONTEXT."
    _create(home, **{**_BIN_NIGHT, "say": words})

    result = await _fire(_action(home, "clock:bin-night"), _state(tmp_path, first=discord))

    assert result.success, result.error
    assert telegram.deliver_text.await_args.args == (f"dm-{TELEGRAM_OWNER}", words)
