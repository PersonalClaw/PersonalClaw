"""A chat that started on a chat channel is asked its approvals in that chat, with nothing set up.

The person asking is in that chat, so that is where the approval asks, the way PersonalClaw shows a
chat's own approval card whatever the notification rules say. It used to be asked there only when
the owner had added the Channel DM target to the Approval needed row: the default rule notifies the
dashboard alone, so a chat started on Telegram waited on an approval the person on Telegram never
saw, although the changelog said it was asked there.

The Approval needed row still decides the rest. Its Channel DM target asks an approval with no
channel origin on "Send approvals to", and tries that channel after a chat's own channel that cannot
ask; without it, no other channel stands in and the approval waits in PersonalClaw, where every
approval is listed. Its mode is about pings, so a chat's own channel asks even under ``never``, as
the chat's card still shows.

Only the channels' outbound halves are fakes. The rules file, the owner ids and the approval
registry are real, in a scratch home, and the chat is created by the door a channel's message
comes in through (``get_or_create_session(app=…)``).
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw import channel_delivery
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.dashboard import channel_messages

ORIGIN, OTHER = "achat", "zchat"  # name order: ORIGIN is also who the old default order asked


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, owner_id_credential(ORIGIN), owner_id_credential(OTHER))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    for provider in (ORIGIN, OTHER):
        channel_delivery.register(None, provider=provider)


class _Channel:
    """A channel's outbound half: what it was sent, and the prompts it is waiting on."""

    def __init__(self, *, can_prompt: bool = True) -> None:
        self.sent: list[tuple[str, str]] = []
        self.prompts: list[Any] = []
        #: where each prompt was asked: the session key and the store it was handed
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

    def quiet(self) -> bool:
        return not self.prompts and not self.sent


def _connect(provider: str, *, knows_owner: bool = True, **kw) -> _Channel:
    handle = _Channel(**kw)
    channel_delivery.register(handle, provider=provider)
    if knows_owner:
        save_credential(owner_id_credential(provider), f"owner-on-{provider}")
    return handle


def _approvals_go_to(provider: str) -> None:
    cfg = AppConfig.load()
    cfg.agent.approval_channel = provider
    cfg.save()


def _approval_rule(tmp_path, **rule: Any) -> None:
    """The owner's own Approval needed row (Settings → Notifications)."""
    path = tmp_path / "entity_settings" / "notification_rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rules": {"approval/requested": rule}}), encoding="utf-8")


def _state(tmp_path):
    """The registry with NO rules file: the rules every install starts with."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    return state


async def _ask(state, chat, request_id: str = "r-1") -> asyncio.Future:
    """What the chat's runner does for a call that needs approval: park its future, then publish
    it (``chat_runner`` → ``hold_session_approval``). Returns the future the runner waits on."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures[request_id] = future
    await state.hold_session_approval(
        chat,
        request_id,
        tool="bash",
        tool_input='{"command": "make test"}',
        tool_purpose="run the tests",
        agent="PersonalClaw",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
    )
    return future


async def _until(check, what: str) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


async def _settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_with_no_rule_set_a_chat_from_a_channel_is_asked_in_that_chat(tmp_path):
    origin, other = _connect(ORIGIN), _connect(OTHER)
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)  # what ORIGIN's inbound door creates

    waiting = await _ask(state, chat)
    await _until(lambda: origin.prompts, f"{ORIGIN} asked")
    assert other.quiet(), "a channel the chat did not start on was asked"
    key, sessions = origin.asked_in[0]
    assert key == f"dashboard:{chat.key}", "asked in the owner's DM, not in the chat"
    assert sessions is state.sessions

    origin.prompts[0].future.set_result("approved")  # Approve, pressed in that chat
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"


@pytest.mark.asyncio
async def test_the_setting_does_not_move_a_channel_chat_off_its_own_channel(tmp_path):
    origin, other = _connect(ORIGIN), _connect(OTHER)
    _approvals_go_to(OTHER)
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)

    waiting = await _ask(state, chat)
    await _until(lambda: origin.prompts, f"{ORIGIN} asked")
    assert other.quiet(), "Send approvals to stood in for the chat's own channel"
    origin.prompts[0].future.set_result("rejected")
    assert await asyncio.wait_for(waiting, timeout=5) == "rejected"


@pytest.mark.asyncio
async def test_a_rule_that_says_never_still_asks_the_chat_s_own_channel(tmp_path):
    """``never`` stops the pings (push, Channel DM). The chat's own channel is where the
    conversation is, like the chat's card in PersonalClaw, which ``never`` does not hide."""
    origin = _connect(ORIGIN)
    _approval_rule(tmp_path, mode="never", targets=["dashboard", "channel_dm"])
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)

    waiting = await _ask(state, chat)
    await _until(lambda: origin.prompts, f"{ORIGIN} asked")
    origin.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"


@pytest.mark.asyncio
async def test_a_channel_chat_whose_channel_has_no_buttons_is_sent_the_link_there(
    tmp_path, monkeypatch
):
    origin, other = _connect(ORIGIN, can_prompt=False), _connect(OTHER)
    monkeypatch.setattr(
        channel_messages, "dashboard_link", lambda frag: f"https://claw.example{frag[1:]}"
    )
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)

    waiting = await _ask(state, chat)
    await _until(lambda: origin.sent, f"{ORIGIN} was told")
    assert "https://claw.example/companion?approval=" in origin.sent[0][1]
    assert other.quiet()
    waiting.cancel()


# ── what the rule still decides ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_in_personalclaw_is_asked_on_no_channel_unless_the_rule_says_so(tmp_path):
    """No channel origin and no Channel DM target: the approval is PersonalClaw's alone. The floor
    is the next case, where the same chat with the target is asked."""
    origin, other = _connect(ORIGIN), _connect(OTHER)
    state = _state(tmp_path)
    chat = state.get_or_create_session("here")  # a chat started in PersonalClaw

    waiting = await _ask(state, chat)
    await _settle()
    assert origin.quiet() and other.quiet()
    assert not waiting.done(), "still waiting on you"
    waiting.cancel()


@pytest.mark.asyncio
async def test_with_the_channel_dm_target_a_chat_in_personalclaw_asks_send_approvals_to(tmp_path):
    origin, other = _connect(ORIGIN), _connect(OTHER)
    _approvals_go_to(OTHER)
    _approval_rule(tmp_path, mode="immediate", targets=["dashboard", "channel_dm"])
    state = _state(tmp_path)
    chat = state.get_or_create_session("here")

    waiting = await _ask(state, chat)
    await _until(lambda: other.prompts, f"{OTHER} asked")
    assert origin.quiet()
    other.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"


@pytest.mark.asyncio
async def test_without_the_target_no_other_channel_stands_in_for_the_chat_s_own(tmp_path):
    """The chat's channel cannot reach its owner: the approval waits in PersonalClaw. With the
    Channel DM target it hands over to "Send approvals to" (the next case, the floor)."""
    origin, other = _connect(ORIGIN, knows_owner=False), _connect(OTHER)
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)

    waiting = await _ask(state, chat)
    await _settle()
    assert origin.quiet() and other.quiet()
    assert not waiting.done()
    waiting.cancel()


@pytest.mark.asyncio
async def test_with_the_target_an_origin_that_cannot_ask_hands_over(tmp_path):
    origin, other = _connect(ORIGIN, knows_owner=False), _connect(OTHER)
    _approval_rule(tmp_path, mode="immediate", targets=["dashboard", "channel_dm"])
    state = _state(tmp_path)
    chat = state.get_or_create_session(app=ORIGIN)

    waiting = await _ask(state, chat)
    await _until(lambda: other.prompts, f"{OTHER} asked")
    assert other.asked_in[0] == ("", None), "the next channel asks in the owner's DM"
    assert origin.quiet(), "a channel that knows no owner cannot ask anyone"
    other.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"
