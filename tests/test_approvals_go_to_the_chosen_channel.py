"""An approval asks on the channel the owner chose in "Send approvals to".

The setting is ``agent.approval_channel``.

Every approval that asks on a chat channel tried the connected channels in name order and asked
the first that knew the owner: Discord, then Email, Slack, Telegram. An owner with four channels
paired could not choose, so a Telegram user's approvals went to Discord. The setting names the one
channel that asks. Left empty, the order is what it was. Both askers follow it: the registry's
``channel_dm`` target (``approval_state._approval_on_a_channel``, and its link fallback) and the
gateway's subagent approval (``GatewayOrchestrator._interactive_approval``).

Ledger 283 (decided): a chat that STARTED on a channel is asked on that channel first, in that
chat, since the person asking is there; "Send approvals to" governs the turns with no channel origin
(a chat in PersonalClaw, an unattended run, a trigger) and who is tried after an origin that cannot
ask. It used to send a Telegram chat's approval to whatever the setting named.

Only the channels' outbound halves are fakes. The config file, the owner ids, the rules file and
the approval registry are real, in a scratch home.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as rules
from personalclaw.approval_grants import ToolDecision
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.dashboard import channel_messages

FIRST, LAST = "achat", "zchat"  # name order: FIRST is who asked before the setting existed


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, owner_id_credential(FIRST), owner_id_credential(LAST))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    for provider in (FIRST, LAST):
        channel_delivery.register(None, provider=provider)


class _Channel:
    """A channel's outbound half: what it was sent, and the prompts it is waiting on."""

    def __init__(self, *, can_prompt: bool = True) -> None:
        self.sent: list[tuple[str, str]] = []
        self.prompts: list[Any] = []
        #: where each prompt was asked: the session key and store it was handed, or none
        self.asked_in: list[tuple[str, Any]] = []
        if not can_prompt:
            self.request_approval = None  # type: ignore[assignment]

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        self.sent.append((channel, text))
        return "1"

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape
        pending = SimpleNamespace(future=asyncio.get_running_loop().create_future())
        self.prompts.append(pending)
        self.asked_in.append((parent_session_key, sessions))
        if on_prompted:
            on_prompted(pending)
        return (await pending.future) == "approved"


def _connect(provider: str, *, knows_owner: bool = True, **kw) -> _Channel:
    handle = _Channel(**kw)
    channel_delivery.register(handle, provider=provider)
    if knows_owner:
        save_credential(owner_id_credential(provider), f"owner-on-{provider}")
    return handle


def _send_approvals_to(provider: str) -> None:
    cfg = AppConfig.load()
    cfg.agent.approval_channel = provider
    cfg.save()


def _state(tmp_path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    registered = nk.kind_for_legacy(nk.APPROVAL)
    assert rules.ensure_target(registered.source, registered.kind, "channel_dm")
    return state


async def _settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def _until(check, what: str) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


# ── the registry's Channel DM target ────────────────────────────────────────────────────────


def test_the_setting_round_trips_and_defaults_to_empty():
    assert AppConfig().agent.approval_channel == ""
    _send_approvals_to(LAST)
    assert AppConfig.load().agent.approval_channel == LAST


@pytest.mark.parametrize(("raw", "read"), [(" zchat ", "zchat"), (7, ""), (None, ""), ("", "")])
def test_a_hand_edited_value_reads_as_a_name_or_the_default(tmp_path, raw, read):
    import json

    (tmp_path / "config.json").write_text(
        json.dumps({"agent": {"approval_channel": raw}}), encoding="utf-8"
    )
    assert AppConfig.load().agent.approval_channel == read


@pytest.mark.asyncio
async def test_the_settings_write_path_saves_a_channel_and_refuses_one_that_is_not_here(tmp_path):
    """The PATCH Settings uses. A channel is settable once its app has registered it; a name no
    channel app answers to is refused with the names that can be chosen."""
    import json

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.channel_transports import register_transport, unregister_transport
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    register_transport(SimpleNamespace(name=LAST))  # type: ignore[arg-type]
    try:
        async with TestClient(TestServer(app)) as client:
            ok = await client.patch(
                "/api/config/personalclaw", json={"path": "agent.approval_channel", "value": LAST}
            )
            assert ok.status == 200, await ok.text()
            stored = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
            assert stored["agent"]["approval_channel"] == LAST

            refused = await client.patch(
                "/api/config/personalclaw",
                json={"path": "agent.approval_channel", "value": "telegram"},
            )
            assert refused.status == 400
            body = await refused.text()
            assert "'telegram' is not a chat channel here" in body and LAST in body

            back = await client.patch(
                "/api/config/personalclaw", json={"path": "agent.approval_channel", "value": ""}
            )
            assert back.status == 200, await back.text()
    finally:
        unregister_transport(LAST)
    assert AppConfig.load().agent.approval_channel == ""


@pytest.mark.asyncio
async def test_by_default_the_first_channel_that_knows_you_asks(tmp_path):
    """The default is unchanged: name order, first channel with an owner id."""
    first, last = _connect(FIRST), _connect(LAST)
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-1", "cron:nightly", "bash"))
    await _until(lambda: first.prompts, f"{FIRST} asked")
    assert last.prompts == [] and last.sent == []
    state.resolve_approval("ap-1", False)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_send_approvals_to_asks_only_that_channel(tmp_path):
    first, last = _connect(FIRST), _connect(LAST)
    _send_approvals_to(LAST)
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-2", "cron:nightly", "bash"))
    await _until(lambda: last.prompts, f"{LAST} asked")
    assert first.prompts == [] and first.sent == [], "the channel you did not choose stays quiet"

    last.prompts[0].future.set_result("approved")  # pressed Approve on the chosen channel
    assert await asyncio.wait_for(waiter, timeout=5) is True


@pytest.mark.asyncio
async def test_a_chosen_channel_that_is_not_connected_asks_nobody_else(tmp_path):
    """No other channel stands in: the approval waits where every approval is listed."""
    first, last = _connect(FIRST), _connect(LAST)
    _send_approvals_to("telegram")  # chosen, then its app was turned off
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-3", "cron:nightly", "bash"))
    await _settle()
    assert first.prompts == [] and first.sent == []
    assert last.prompts == [] and last.sent == []
    assert "ap-3" in state._pending_approvals and not waiter.done(), "still waiting on you"

    assert state.resolve_approval("ap-3", True) is True  # floor: answered in the dashboard
    assert await asyncio.wait_for(waiter, timeout=5) is True


@pytest.mark.asyncio
async def test_a_chosen_channel_without_buttons_gets_the_link_not_another_channel(
    tmp_path, monkeypatch
):
    first, last = _connect(FIRST), _connect(LAST, can_prompt=False)
    _send_approvals_to(LAST)
    monkeypatch.setattr(
        channel_messages, "dashboard_link", lambda frag: f"https://claw.example{frag[1:]}"
    )
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-4", "cron:nightly", "bash"))
    await _until(lambda: last.sent, f"{LAST} was told")
    assert "https://claw.example/companion?approval=ap-4" in last.sent[0][1]
    assert first.prompts == [] and first.sent == []
    state.resolve_approval("ap-4", False)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_a_chat_that_started_on_a_channel_is_asked_there_first(tmp_path):
    """Ledger 283: the person asking is in that chat, so that is where the approval asks —
    whatever "Send approvals to" names, which governs the approvals with no channel origin."""
    first, last = _connect(FIRST), _connect(LAST)
    _send_approvals_to(LAST)
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=FIRST)  # what FIRST's inbound door creates

    waiter = asyncio.ensure_future(state.request_approval("ap-5", "chat", "bash", session=chat.key))
    await _until(lambda: first.prompts, f"{FIRST} asked")
    assert last.prompts == [] and last.sent == [], "the setting stood in for the chat's channel"
    key, sessions = first.asked_in[0]
    assert key == f"dashboard:{chat.key}", "asked in the owner's DM, not in the chat"
    assert sessions is state.sessions
    first.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiter, timeout=5) is True


@pytest.mark.asyncio
async def test_an_origin_that_cannot_ask_hands_over_to_send_approvals_to(tmp_path):
    first, last = _connect(FIRST, knows_owner=False), _connect(LAST)
    _send_approvals_to(LAST)
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=FIRST)

    waiter = asyncio.ensure_future(state.request_approval("ap-6", "chat", "bash", session=chat.key))
    await _until(lambda: last.prompts, f"{LAST} asked")
    assert first.prompts == []
    assert last.asked_in[0] == ("", None), "the setting's channel asks in the owner's DM"
    state.resolve_approval("ap-6", False)
    await asyncio.wait_for(waiter, timeout=5)


# ── the gateway's subagent approval ─────────────────────────────────────────────────────────


def _orchestrator():
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    ds = MagicMock()
    ds.is_yolo_active.return_value = False
    ds._sessions = {}
    ds.request_approval = AsyncMock(return_value=False)
    orch.dashboard_state = ds
    return orch


def _asker(provider: str, *, knows_owner: bool = True) -> MagicMock:
    handle = MagicMock()
    handle.request_approval = AsyncMock(return_value=True)
    channel_delivery.register(handle, provider=provider)
    if knows_owner:
        save_credential(owner_id_credential(provider), f"owner-on-{provider}")
    return handle


def _event() -> MagicMock:
    event = MagicMock()
    event.request_id = "req-1"
    event.title = "bash: ls"
    event.tool_input = ""
    event.tool_purpose = ""
    return event


@pytest.mark.asyncio
async def test_a_subagent_approval_asks_the_chosen_channel():
    first, last = _asker(FIRST), _asker(LAST)
    _send_approvals_to(LAST)
    callback = _orchestrator()._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        assert await callback(_event(), "") == ToolDecision(True, "approved", "you")
    last.request_approval.assert_awaited_once()
    first.request_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_subagent_approval_passes_over_a_channel_that_does_not_know_you():
    """The default the Settings label states — the first connected channel that knows you — is
    true of this asker too. It asked the first channel in name order whether or not that channel
    had an owner id, and a channel with none cannot ask anyone."""
    first, last = _asker(FIRST, knows_owner=False), _asker(LAST)
    callback = _orchestrator()._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        assert await callback(_event(), "") == ToolDecision(True, "approved", "you")
    last.request_approval.assert_awaited_once()
    first.request_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_subagent_approval_with_the_chosen_channel_gone_waits_in_the_dashboard():
    first = _asker(FIRST)
    _send_approvals_to(LAST)  # not connected
    orch = _orchestrator()
    callback = orch._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        # The dashboard's answer (the mock's), a person's.
        assert await callback(_event(), "") == ToolDecision(False, "rejected", "you")
    first.request_approval.assert_not_awaited()
    orch.dashboard_state.request_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_subagent_of_a_channel_chat_asks_that_channel_first():
    first, last = _asker(FIRST), _asker(LAST)
    _send_approvals_to(LAST)
    orch = _orchestrator()
    orch.dashboard_state.channel_provider_for = lambda key: (
        FIRST if key == "dashboard:chat-7" else ""
    )
    callback = orch._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        assert await callback(_event(), "dashboard:chat-7") == ToolDecision(True, "approved", "you")
    first.request_approval.assert_awaited_once()
    last.request_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_subagent_of_a_dashboard_chat_follows_the_setting():
    first, last = _asker(FIRST), _asker(LAST)
    _send_approvals_to(LAST)
    orch = _orchestrator()
    orch.dashboard_state.channel_provider_for = lambda key: ""
    callback = orch._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        assert await callback(_event(), "dashboard:chat-8") == ToolDecision(True, "approved", "you")
    last.request_approval.assert_awaited_once()
    first.request_approval.assert_not_awaited()
