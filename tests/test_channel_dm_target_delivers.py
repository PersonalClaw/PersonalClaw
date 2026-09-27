"""The "Channel DM" notification target sends, and an approval asks on the owner's channel.

``notification_rules.TARGETS`` accepted and persisted ``channel_dm`` with nothing behind it, so
ticking it in Settings → Notifications did nothing and the UI labelled it "not delivered yet".
``DashboardState.notify`` now sends the note to the owner's DM on the first connected channel
that reaches them, and ``approval/requested`` asks there with Approve/Deny when the channel can
prompt, else with a link.

Only the channel's outbound half is a fake. The owner id, the rules file, the approval registry
and the dashboard state are real. Each case that sends nothing has its floor: the same note or
approval, with the target set, does send.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw import channel_delivery
from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as rules
from personalclaw.approval_answer import YOU
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.dashboard import channel_messages

PROVIDER = "fakechat"
OWNER = "4242"


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
    """A channel's outbound half. ``prompts`` is where a request_approval waits for a press."""

    def __init__(self, *, can_prompt: bool = True, prompt_window: float = 30.0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.prompts: list[Any] = []
        self.events: list[Any] = []
        self._window = prompt_window
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
        self.events.append(event)
        self.prompts.append(pending)
        if on_prompted:
            on_prompted(pending)
        try:
            outcome = await asyncio.wait_for(pending.future, timeout=self._window)
        except asyncio.TimeoutError:
            outcome = "rejected"  # what the shipped channels do when their window closes
        return outcome == "approved"


def _connect(**kw) -> _Channel:
    handle = _Channel(**kw)
    channel_delivery.register(handle, provider=PROVIDER)
    save_credential(owner_id_credential(PROVIDER), OWNER)
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
    return state


def _target(flat_kind: str) -> None:
    registered = nk.kind_for_legacy(flat_kind)
    assert rules.ensure_target(registered.source, registered.kind, "channel_dm")


async def _settle(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def _until(check, what: str) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


# ── a note ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_note_with_the_channel_dm_target_reaches_the_owner_on_their_channel(tmp_path):
    channel = _connect()
    state = _state(tmp_path)

    state.notify(nk.WARNING, "Disk nearly full", "92% of /data is used.")
    await _settle()
    assert channel.sent == [], "vacuity floor: no target, no DM"

    _target(nk.WARNING)
    state.notify(nk.WARNING, "Disk nearly full", "92% of /data is used.")
    await _until(lambda: channel.sent, "the note reached the channel")
    where, text = channel.sent[0]
    assert where == f"dm-{OWNER}"
    assert "Disk nearly full" in text and "92% of /data is used." in text


@pytest.mark.asyncio
async def test_a_note_a_channel_route_already_sent_is_not_sent_twice(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.WARNING)

    state.notify(nk.WARNING, "Nightly report", "done", meta={"sent_to_channel": PROVIDER})
    state.notify(nk.WARNING, "Disk nearly full", "92%")
    await _until(lambda: channel.sent, "the unmarked note reached the channel")
    await _settle()
    assert [t for _, t in channel.sent if "Nightly report" in t] == []


@pytest.mark.asyncio
async def test_a_note_carries_its_link_when_the_dashboard_has_an_address(tmp_path, monkeypatch):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.WARNING)
    monkeypatch.setattr(
        channel_messages, "dashboard_link", lambda frag: f"https://claw.example{frag[1:]}"
    )

    state.notify(nk.WARNING, "Run failed", "exit 1", meta={"statusUrl": "#/triggers?open=t1"})
    await _until(lambda: channel.sent, "the note reached the channel")
    assert channel.sent[0][1].endswith("https://claw.example/triggers?open=t1")


# ── an approval ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approval_is_answered_from_the_channel(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.APPROVAL)

    waiter = asyncio.ensure_future(
        state.request_approval("ap-1", "cron:nightly", "bash", tool_input="ls /")
    )
    await _until(lambda: channel.prompts, "the channel asked")
    event = channel.events[0]
    assert event.title == "bash"
    assert event.request_id != "ap-1" and len(event.request_id) <= 12, "a short token, not the id"

    channel.prompts[0].future.set_result("approved")  # the owner pressed Approve
    assert await asyncio.wait_for(waiter, timeout=5) is True
    assert "ap-1" not in state._pending_approvals


@pytest.mark.asyncio
async def test_deny_on_the_channel_denies(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.APPROVAL)

    waiter = asyncio.ensure_future(state.request_approval("ap-2", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    channel.prompts[0].future.set_result("rejected")
    assert await asyncio.wait_for(waiter, timeout=5) is False


@pytest.mark.asyncio
async def test_an_answer_in_the_dashboard_closes_the_channel_prompt(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.APPROVAL)

    waiter = asyncio.ensure_future(state.request_approval("ap-3", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    assert state.resolve_approval("ap-3", True, by=YOU) is True
    assert await asyncio.wait_for(waiter, timeout=5) is True
    await _until(lambda: channel.prompts[0].future.done(), "the channel prompt closed")
    assert channel.prompts[0].future.result() == "approved"


@pytest.mark.asyncio
async def test_a_prompt_that_runs_out_on_the_channel_decides_nothing(tmp_path):
    """Every shipped channel returns False when its own window closes. That is not a Deny."""
    channel = _connect(prompt_window=0.05)
    state = _state(tmp_path)
    _target(nk.APPROVAL)

    waiter = asyncio.ensure_future(state.request_approval("ap-4", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    await _until(lambda: channel.prompts[0].future.cancelled(), "the channel's window closed")
    await _settle()
    assert "ap-4" in state._pending_approvals and not waiter.done(), "still waiting on you"

    assert state.resolve_approval("ap-4", True, by=YOU) is True  # floor: it can still be answered
    assert await asyncio.wait_for(waiter, timeout=5) is True


@pytest.mark.asyncio
async def test_a_channel_with_no_approval_prompt_gets_a_link_instead(tmp_path, monkeypatch):
    channel = _connect(can_prompt=False)
    state = _state(tmp_path)
    _target(nk.APPROVAL)
    monkeypatch.setattr(
        channel_messages, "dashboard_link", lambda frag: f"https://claw.example{frag[1:]}"
    )

    waiter = asyncio.ensure_future(state.request_approval("ap-5", "cron:nightly", "bash"))
    await _until(lambda: channel.sent, "the owner was told")
    text = channel.sent[0][1]
    assert "waiting for your approval: bash" in text
    assert "https://claw.example/companion?approval=ap-5" in text
    state.resolve_approval("ap-5", False, by=YOU)
    assert await asyncio.wait_for(waiter, timeout=5) is False


@pytest.mark.asyncio
async def test_no_second_prompt_when_the_caller_is_already_asking_on_a_channel(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    _target(nk.APPROVAL)

    waiter = asyncio.ensure_future(
        state.request_approval("ap-6", "cron:nightly", "bash", asked_on_channel=True)
    )
    await _settle(50)
    assert channel.prompts == [] and channel.sent == []

    # Floor: the same approval without the flag asks.
    other = asyncio.ensure_future(state.request_approval("ap-7", "cron:nightly", "bash"))
    await _until(lambda: channel.prompts, "the channel asked")
    for approval_id in ("ap-6", "ap-7"):
        state.resolve_approval(approval_id, False, by=YOU)
    await asyncio.wait_for(asyncio.gather(waiter, other), timeout=5)


@pytest.mark.asyncio
async def test_no_channel_dm_target_no_prompt(tmp_path):
    channel = _connect()
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(state.request_approval("ap-8", "cron:nightly", "bash"))
    await _settle(50)
    assert channel.prompts == [] and channel.sent == []
    state.resolve_approval("ap-8", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)
