"""An owner notification goes to the first channel that can actually reach the owner.

Measured on main: with email and Telegram connected and only the shared ``PERSONALCLAW_OWNER_ID``
set (to a Slack user id), owner routing picked email — first by name, and it "knows" an owner
through the shared fallback. Email's ``open_dm`` rightly refuses an id that is not an address,
the pick returned nothing, and every owner notification was dropped: heartbeat and cron results,
subagent replies, hook results, file sends, ``send-message``. Telegram, which could have
delivered, was never asked.

Now every connected channel is tried in turn (``channel_delivery.reach_owner``): a channel with no
owner id, one that cannot open a conversation with the id it has, and one whose send raises hands
over to the next. When none gets through, the notification goes to the Inbox with a sentence
saying why each could not (``deliver_to_owner``) — never dropped. With no channel connected at all
nothing failed, and the Inbox is left alone.

The fake channels behave as the shipped apps' ``open_dm`` does: email answers ``""`` for anything
that is not an address, a chat channel treats the user id as the DM.
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
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.inbox import InboxStore

SLACK_ID = "U0SLACKOWNER"
_PROVIDERS = ("email", "telegram", "discord", "slack")


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
    """Just enough of a transport to give a channel its display name."""

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


def _delivery(*, email: bool = False, fails: bool = False) -> MagicMock:
    d = MagicMock()
    if email:
        d.open_dm = AsyncMock(side_effect=lambda uid: uid if "@" in uid else "")
    else:
        d.open_dm = AsyncMock(side_effect=lambda uid: str(uid))
    for method in (
        "deliver_notification",
        "deliver_text",
        "deliver_rich",
        "upload_attachment",
        "deliver_subagent_reply",
    ):
        mock = (
            AsyncMock(side_effect=RuntimeError("chat not found"))
            if fails
            else AsyncMock(return_value="1.0")
        )
        setattr(d, method, mock)
    return d


@pytest.fixture
def channels():
    """Connect fake channels: a delivery handle plus the transport that names it."""
    added: list[str] = []

    def _connect(provider: str, display: str, delivery: MagicMock) -> MagicMock:
        channel_transports.register_transport(_Transport(provider, display))
        channel_delivery.register(delivery, provider=provider)
        added.append(provider)
        return delivery

    yield _connect
    for provider in added:
        channel_transports.unregister_transport(provider)


def _state(tmp_path) -> MagicMock:
    """A dashboard state whose Inbox is a real store in this test's directory."""
    state = MagicMock()
    state._inbox_svc = None
    state._inbox_store = InboxStore(path=tmp_path / "inbox.json")
    return state


def _inbox(state: MagicMock) -> list:
    return state._inbox_store.pending()


def _gateway(state: Any):
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.dashboard_state = state
    return orch


def _email_and_telegram(channels, *, telegram_fails: bool = False) -> tuple[MagicMock, MagicMock]:
    """The measured case: both connected, only the SHARED owner id set, to a Slack user id."""
    email = channels("email", "Email", _delivery(email=True))
    telegram = channels("telegram", "Telegram", _delivery(fails=telegram_fails))
    save_credential(CRED_OWNER_ID, SLACK_ID)
    return email, telegram


# ── the routing rule ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_first_hands_over_to_telegram(channels, tmp_path):
    """The exact case the other lane measured, through the gateway's own owner DM (a heartbeat or
    cron result delivered to `channel`)."""
    email, telegram = _email_and_telegram(channels)
    state = _state(tmp_path)

    await _gateway(state)._deliver_result("Nightly", "task", "all green", "channel")

    email.open_dm.assert_awaited_once_with(SLACK_ID)
    email.deliver_notification.assert_not_awaited()
    telegram.deliver_notification.assert_awaited_once()
    assert telegram.deliver_notification.await_args.args[:2] == (SLACK_ID, "Nightly")
    assert _inbox(state) == [], "a delivered notification also went to the Inbox"


@pytest.mark.asyncio
async def test_a_channel_whose_send_fails_hands_over_to_the_next(channels, tmp_path):
    discord = channels("discord", "Discord", _delivery(fails=True))
    telegram = channels("telegram", "Telegram", _delivery())
    save_credential(owner_id_credential("discord"), "998877665544332211")
    save_credential(owner_id_credential("telegram"), "4242")

    await _gateway(_state(tmp_path))._deliver_result("Nightly", "task", "done", "channel")

    discord.deliver_notification.assert_awaited_once()
    telegram.open_dm.assert_awaited_once_with("4242")
    telegram.deliver_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_when_no_channel_reaches_the_owner_it_goes_to_the_inbox_saying_why(
    channels, tmp_path
):
    email, telegram = _email_and_telegram(channels, telegram_fails=True)
    state = _state(tmp_path)

    await _gateway(state)._deliver_result("Nightly", "task", "all green", "channel")

    telegram.deliver_notification.assert_awaited_once()
    [item] = _inbox(state)
    assert item.message == (
        "Nightly\n\nall green\n\nNo channel could deliver this to you: Email could not open a "
        "conversation with the owner id it has; Telegram could not send it. The gateway log has "
        "the errors."
    )


@pytest.mark.asyncio
async def test_a_channel_with_no_owner_id_says_so(channels, tmp_path):
    channels("telegram", "Telegram", _delivery())  # connected, but no owner id anywhere
    state = _state(tmp_path)

    await _gateway(state)._deliver_result("Nightly", "task", "done", "channel")

    [item] = _inbox(state)
    assert item.message.endswith("No channel could deliver this to you: Telegram has no owner id.")


@pytest.mark.asyncio
async def test_with_no_channel_connected_the_inbox_is_left_alone(tmp_path):
    """The guard for the fallback: a dashboard-only install is not a failed delivery, so a
    heartbeat must not put an item in the Inbox every time it runs."""
    state = _state(tmp_path)
    await _gateway(state)._deliver_result("Nightly", "task", "done", "channel")
    assert _inbox(state) == []


# ── every owner notification goes through it ────────────────────────────────────────────────


def _app(state: Any, *routes: tuple[str, str, Any]) -> web.Application:
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    app["state"] = state
    return app


@pytest.fixture
def quiet_sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield


@pytest.mark.asyncio
async def test_send_message_reaches_the_owner_on_telegram(channels, tmp_path, quiet_sel):
    """A cron result's `send-message` with no session to go back to: the owner's DM."""
    from personalclaw.dashboard.handlers import api_send_message

    email, telegram = _email_and_telegram(channels)
    state = _state(tmp_path)
    state.channel_delivery = email
    async with TestClient(
        TestServer(_app(state, ("POST", "/api/send-message", api_send_message)))
    ) as c:
        resp = await c.post("/api/send-message", json={"text": "build is green"})
        assert resp.status == 200, await resp.text()
        assert await resp.json() == {"ok": True, "channel": True, "session": False, "ts": "1.0"}
    assert telegram.deliver_text.await_args.args[:2] == (SLACK_ID, "build is green")
    email.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_that_reaches_no_channel_goes_to_the_inbox(
    channels, tmp_path, quiet_sel
):
    from personalclaw.dashboard.handlers import api_send_message

    email, _telegram = _email_and_telegram(channels, telegram_fails=True)
    state = _state(tmp_path)
    state.channel_delivery = email
    async with TestClient(
        TestServer(_app(state, ("POST", "/api/send-message", api_send_message)))
    ) as c:
        resp = await c.post("/api/send-message", json={"text": "build is green"})
        assert resp.status == 200, await resp.text()
        body = await resp.json()
    assert body["ok"] is True and body["channel"] is False and body["inbox"] is True
    assert body["detail"].startswith("No channel could deliver this to you: Email could not")
    [item] = _inbox(state)
    assert "build is green" in item.message


@pytest.mark.asyncio
async def test_a_file_send_reaches_the_owner_on_telegram(
    channels, tmp_path, quiet_sel, monkeypatch
):
    from personalclaw.config import loader
    from personalclaw.dashboard.handlers.files import api_channel_upload_file

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "report.txt").write_text("all green\n", encoding="utf-8")
    monkeypatch.setattr(loader, "outbox_dir", lambda: outbox)
    email, telegram = _email_and_telegram(channels)
    state = _state(tmp_path)
    state.channel_delivery = email
    app = _app(state, ("POST", "/api/channel/upload-file", api_channel_upload_file))
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(
            "/api/channel/upload-file",
            json={"file_path": str(outbox / "report.txt"), "filename": "report.txt"},
        )
        assert resp.status == 200, await resp.text()
        assert await resp.json() == {"ok": True}
    assert telegram.upload_attachment.await_args.args[0] == SLACK_ID
    email.upload_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_file_send_that_reaches_no_channel_says_why(
    channels, tmp_path, quiet_sel, monkeypatch
):
    """The MCP tool reads this body (a 5xx would reach it only as an HTTP status line)."""
    from personalclaw.config import loader
    from personalclaw.dashboard.handlers.files import api_channel_upload_file

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "report.txt").write_text("all green\n", encoding="utf-8")
    monkeypatch.setattr(loader, "outbox_dir", lambda: outbox)
    email, _telegram = _email_and_telegram(channels, telegram_fails=True)
    state = _state(tmp_path)
    state.channel_delivery = email
    app = _app(state, ("POST", "/api/channel/upload-file", api_channel_upload_file))
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(
            "/api/channel/upload-file",
            json={"file_path": str(outbox / "report.txt"), "filename": "report.txt"},
        )
        body = await resp.json()
    assert resp.status == 200 and body["ok"] is False and body["inbox"] is True
    assert body["error"].startswith("No channel could deliver this to you:")
    [item] = _inbox(state)
    assert item.message.startswith("File: report.txt")


@pytest.mark.asyncio
async def test_the_send_message_action_reaches_the_owner_on_telegram(channels, tmp_path):
    """A trigger's send-message action with no channel or user: the owner's DM. It used to DM
    the shared id through whichever channel sorted first."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.send_message_provider import SendMessageActionProvider

    email, telegram = _email_and_telegram(channels)
    state = _state(tmp_path)
    state.channel_delivery = email
    with patch(
        "personalclaw.action_providers.send_message_provider.get_action_services",
        return_value=MagicMock(state=state),
    ):
        result = await SendMessageActionProvider().execute(
            {"text_template": "deploy finished"}, ActionContext(event="Stop", context="")
        )
    assert result.success, result.error
    assert telegram.deliver_text.await_args.args == (SLACK_ID, "deploy finished")
    email.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_handoff_to_the_owners_dm_uses_the_channel_that_reaches_them(
    channels, tmp_path, quiet_sel
):
    from personalclaw.dashboard.chat_channel import api_chat_session_handoff

    email, telegram = _email_and_telegram(channels)
    state = _state(tmp_path)
    state.channel_delivery = email
    session = MagicMock(key="dashboard:chat-1", title="Plans", _titled=True)
    state.get_session.return_value = session
    state.conversation_log.read_messages.return_value = [{"role": "user", "content": "hi"}]
    state.conversation_log.get_metadata.return_value = {}
    app = _app(state, ("POST", "/api/chat/sessions/{session}/handoff", api_chat_session_handoff))
    with patch("personalclaw.dashboard.chat_channel.save_session_to_history"):
        async with TestClient(TestServer(app)) as c:
            resp = await c.post("/api/chat/sessions/chat-1/handoff", json={})
            assert resp.status == 200, await resp.text()
    assert telegram.deliver_text.await_args.args[0] == SLACK_ID
    email.deliver_text.assert_not_awaited()
