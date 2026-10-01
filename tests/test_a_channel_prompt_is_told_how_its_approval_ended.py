"""A prompt on a chat channel is told how its approval ended, so it can say so.

An approval asked on a channel is answered wherever the owner is: its Approve there, the dashboard,
the phone. It can also end with nobody answering (its window closes) or with the work that asked
stopping first. Core closes the channel's prompt each time by resolving the pending record the
channel handed it (``on_prompted``), and the channel shows the outcome on its message and takes the
buttons off. It was only ever told ``approved`` or ``rejected``: an approval that expired, or whose
turn was stopped, left "Rejected" on the owner's phone, a decision nobody made.

Both askers are driven, each with the real registry: the registry's own ask on a channel (a chat's
approval, and one with the Channel DM target), and the gateway's race for a background origin (a
subagent). Only the channels' outbound halves are fakes.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as rules
from personalclaw.approval_answer import YOU
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig

PROVIDER = "achat"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, owner_id_credential(PROVIDER))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    channel_delivery.register(None, provider=PROVIDER)


class _Channel:
    """A channel's outbound half: each prompt it posted, and what it was told about each."""

    def __init__(self) -> None:
        self.prompts: list[Any] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        return "1"

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape
        pending = SimpleNamespace(future=asyncio.get_running_loop().create_future())
        self.prompts.append(pending)
        if on_prompted:
            on_prompted(pending)
        return (await pending.future) == "approved"

    def told(self) -> str:
        """How the first prompt was told its approval ended."""
        future = self.prompts[0].future
        assert future.done(), "the prompt was never told how its approval ended"
        return future.result()


def _connect() -> _Channel:
    handle = _Channel()
    channel_delivery.register(handle, provider=PROVIDER)
    save_credential(owner_id_credential(PROVIDER), "owner-on-achat")
    return handle


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


async def _until(check, what: str) -> None:
    for _ in range(300):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


# ── the registry's own ask on a channel ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approval_nobody_answered_is_told_it_expired(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    state.approval_window_secs = lambda: 0.3  # type: ignore[method-assign]

    waiter = asyncio.ensure_future(state.request_approval("ap-1", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    assert await asyncio.wait_for(waiter, timeout=5) is False
    await _until(lambda: channel.prompts[0].future.done(), "the prompt was closed")
    assert channel.told() == "expired", "the owner's phone says Rejected: a Deny nobody gave"


@pytest.mark.asyncio
async def test_an_approval_whose_work_stopped_is_told_it_was_cancelled(tmp_path):
    channel = _connect()
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-2", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    assert state.cancel_approval("ap-2", reason="its run was stopped") is True
    assert await asyncio.wait_for(waiter, timeout=5) is False
    assert channel.told() == "cancelled"


@pytest.mark.asyncio
async def test_a_chat_s_stopped_turn_is_told_it_was_cancelled(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=PROVIDER)
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures["r-1"] = future
    await state.hold_session_approval(
        chat,
        "r-1",
        tool="bash",
        tool_input='{"command": "make test"}',
        tool_purpose="",
        agent="PersonalClaw",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
    )
    await _until(lambda: channel.prompts, "the channel asked")

    assert state.cancel_turn_approvals(f"dashboard:{chat.key}") == 1  # the stop button
    assert future.result() == "cancelled"
    assert channel.told() == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(("approved", "told"), [(True, "approved"), (False, "rejected")])
async def test_an_answer_given_in_the_dashboard_is_told_as_given(tmp_path, approved, told):
    """The floor: a person's answer anywhere else closes the prompt with that answer."""
    channel = _connect()
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-3", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    assert state.resolve_approval("ap-3", approved, by=YOU) is True
    assert await asyncio.wait_for(waiter, timeout=5) is approved
    assert channel.told() == told


# ── the gateway's race for a background origin ─────────────────────────────────────────────


def _gateway(tmp_path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.history import ConversationLog

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    orch.dashboard_state = state
    return orch


def _event() -> SimpleNamespace:
    return SimpleNamespace(
        request_id="req-1",
        title="bash",
        tool_kind="",
        tool_input='{"command": "make test"}',
        tool_purpose="run the tests",
        risk_level="",
        tool_meta={},
    )


@pytest.mark.asyncio
async def test_a_subagent_s_approval_nobody_answered_is_told_it_expired(tmp_path):
    channel = _connect()
    orch = _gateway(tmp_path)
    orch.dashboard_state.approval_window_secs = lambda: 0.3  # type: ignore[method-assign]
    ask = orch._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        decision = await asyncio.wait_for(ask(_event(), ""), timeout=5)
    assert (decision.approved, decision.outcome) == (False, "expired")
    assert channel.told() == "expired", "the subagent's prompt said Rejected when nobody answered"


@pytest.mark.asyncio
async def test_a_subagent_s_approval_is_closed_even_when_its_dashboard_copy_breaks(tmp_path):
    """The dashboard's copy of the ask failing must not leave the channel's prompt, and the
    subagent waiting on it, open for good: the prompt is told the approval was cancelled."""
    channel = _connect()
    orch = _gateway(tmp_path)

    async def broken(*_a, **_kw):
        raise RuntimeError("the registry could not list it")

    orch.dashboard_state.request_approval = broken  # type: ignore[method-assign]
    ask = orch._interactive_approval("subagent")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        decision = await asyncio.wait_for(ask(_event(), ""), timeout=5)
    assert decision.approved is False
    assert channel.told() == "cancelled"
