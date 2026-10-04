"""A chat continued on another channel answers there, and the thread it left is told where it went.

A chat that came in on Telegram and was continued in a Slack thread (the chat's "Continue on
Slack", or a link from the dashboard) kept answering on Telegram. Which channel a chat answered on
was its origin tag, the channel it came from, and a handoff set that only for a chat that had
none. So its next answer went to Telegram with the Slack thread's ids, and nothing reached the
Slack thread the owner had moved it to; a restart kept it that way, since the tag is saved with
the chat. A link now names the channel its thread is on, in the one place links are kept
(``session_map.json``), and that is the channel the chat answers on. The thread the chat left
loses it, as a thread another chat takes already did, and is told where the chat went, once.

Driven through the routes the dashboard calls and the inbound door every channel delivers to, over
a real session store and dashboard state; only the model and the two channels' handles stand in.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery
from personalclaw import channel_inbound as ci
from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage, ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.session import SessionManager
from personalclaw.session_map import SessionMap

#: The owner's Telegram DM: the chat id is the conversation, and the owner's own id.
DM = "5550123"
#: Someone else's Telegram DM with the bot.
OTHER_DM = "5550456"
SLACK_OWNER = "U0OWNER1"
SLACK_DM = "D0OWNERDM"
SLACK_CHANNEL = "C0123ABC456"
#: The thread a handoff or a link opens on Slack (the ts of the message that opens it).
THREAD = "1712793600.000200"

_IDS = {"telegram": re.compile(r"-?\d{1,20}"), "slack": re.compile(r"[CDGW][A-Z0-9]+")}


class _Chat(ChannelTransportProvider):
    """A chat channel as the shipped apps declare one: its name, whether its DM is one
    conversation, and which ids are its own."""

    def __init__(self, name: str, display: str, *, dm_is_one_thread: bool) -> None:
        self._name, self._display, self._dm = name, display, dm_is_one_thread

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def capabilities(self):
        from personalclaw.sdk.channel import ChannelCapabilities

        return ChannelCapabilities(inbound=True, threads=True, dm_thread_is_channel=self._dm)

    def validate_target(self, target: str) -> str:
        return "" if _IDS[self._name].fullmatch(target) else f"That isn't a {self._display} id."


def _handle(dm: str, opened: str) -> MagicMock:
    """A channel's delivery handle: it opens *dm* for the owner, and a message it posts is
    *opened*."""
    handle = MagicMock()
    handle.open_dm = AsyncMock(return_value=dm)
    handle.deliver_text = AsyncMock(return_value=opened)
    handle.deliver_chat_mirror = AsyncMock()
    handle.start_stream = AsyncMock(return_value="")
    handle.append_stream_task = AsyncMock()
    handle.stop_stream = AsyncMock()
    return handle


@pytest.fixture
def channels(unset_env):
    """Telegram, whose DM is one conversation, and Slack, whose DM has threads, both connected
    and both knowing the owner. Returns their handles, which hear everything sent there."""
    providers = ("telegram", "slack")
    unset_env(CRED_OWNER_ID, *(owner_id_credential(p) for p in providers))
    channel_transports.register_transport(_Chat("telegram", "Telegram", dm_is_one_thread=True))
    channel_transports.register_transport(_Chat("slack", "Slack", dm_is_one_thread=False))
    telegram, slack = _handle(DM, "901"), _handle(SLACK_DM, THREAD)
    channel_delivery.register(telegram, provider="telegram")
    channel_delivery.register(slack, provider="slack")
    save_credential(owner_id_credential("telegram"), DM)
    save_credential(owner_id_credential("slack"), SLACK_OWNER)
    ci.reset_admissions()
    ct.allow_sender("telegram", DM, name="Ada")
    ct.allow_sender("slack", SLACK_OWNER, name="Ada")
    yield telegram, slack
    ci.reset_admissions()
    for provider in providers:
        channel_transports.unregister_transport(provider)
        channel_delivery.register(None, provider=provider)


def _client(answer: str) -> AsyncMock:
    async def _events():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=answer)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    return client


class _Gateway:
    """One run of the gateway over a home: its session store, its dashboard state, the door its
    channels deliver to (``GatewayServices.deliver_channel_inbound``) and the chat routes."""

    def __init__(self, home: Path) -> None:
        self.sessions: Any = SessionManager(AppConfig())
        self.dashboard_state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "sessions"),
        )
        self.dashboard_state.push_sessions_update = MagicMock()
        self._count = 0

    @property
    def state(self) -> DashboardState:
        return self.dashboard_state

    async def settled(self) -> None:
        """Wait for what the gateway runs on its own: the door's turn, a note to a thread."""
        while self.state._background_tasks:
            await asyncio.wait_for(asyncio.gather(*set(self.state._background_tasks)), timeout=10)

    async def message(
        self, provider: str, text: str, *, channel: str, thread: str, sender: str, answer: str = ""
    ) -> str:
        """The owner sends *text* on *provider*'s *thread*; returns the chat the door ran its turn
        in. With *answer*, the turn runs through the real chat runner and the model answers that;
        without, the turn notes the message as its answer."""
        self._count += 1
        reached: list[str] = []

        async def _notes_it(state: Any, session: Any, message: str) -> None:
            session.append("assistant", f"Noted: {message}", "msg msg-a")

        async def _runs(state: Any, session: Any, message: str) -> None:
            reached.append(session.key)
            if not answer:
                await _notes_it(state, session, message)
                return
            from personalclaw.dashboard.chat_runner import run_chat

            self._can_run_a_turn(answer)
            with (
                patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
                patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
                patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
                patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
            ):
                await run_chat(state, session, message, arrived_from_channel=True)
                while session.task is not None and not session.task.done():
                    await asyncio.wait_for(session.task, timeout=10)

        msg = ChannelMessage(
            channel_id=channel,
            text=text,
            sender=sender,
            thread_id=thread,
            message_id=f"{provider}-{self._count}",
        )
        verdict = await ci.deliver_inbound(self, provider, msg, is_dm=True, turn_runner=_runs)
        assert verdict.allowed, verdict
        await self.settled()
        assert reached, "the door ran no turn"
        return reached[-1]

    def _can_run_a_turn(self, answer: str) -> None:
        """Stand in for the model process only: the turn runs through the real chat runner."""
        self.sessions.get_or_create = AsyncMock(return_value=(_client(answer), True, False))
        self.sessions.record_failure = AsyncMock()
        self.sessions.check_context_usage = MagicMock()
        self.sessions.get_pid = MagicMock(return_value=None)
        cb = MagicMock()
        cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
        cb.build_message.return_value = ("hello", None)
        cb.conversation_log = MagicMock()
        cb.conversation_log.read_messages = MagicMock(return_value=[])
        self.state.context_builder = cb
        hooks = MagicMock()
        hooks.fire_for_ids = AsyncMock(return_value=[])
        self.state._hook_store = hooks

    async def _post(self, route: str, chat: str, body: dict) -> tuple[int, dict]:
        from personalclaw.dashboard.chat_channel import (
            api_chat_session_channel_link,
            api_chat_session_handoff,
        )
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        app["state"] = self.state
        app.router.add_post("/api/chat/sessions/{session}/handoff", api_chat_session_handoff)
        app.router.add_post(
            "/api/chat/sessions/{session}/channel-link", api_chat_session_channel_link
        )
        with patch("personalclaw.dashboard.chat_channel.sel", MagicMock()):
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(f"/api/chat/sessions/{chat}/{route}", json=body)
                answer = resp.status, await resp.json()
        await self.settled()
        return answer

    async def continue_on(self, chat: str, provider: str) -> dict:
        """The chat's "Continue on <channel>" (``POST …/handoff``)."""
        status, body = await self._post("handoff", chat, {"provider": provider})
        assert status == 200, body
        return body

    async def link(self, chat: str, body: dict) -> dict:
        """A link from the dashboard (``POST …/channel-link``)."""
        status, answer = await self._post("channel-link", chat, body)
        assert status == 200, answer
        return answer

    def stop(self) -> None:
        save_all_sessions_to_history(self.state)


def _restart(home: Path) -> _Gateway:
    """A new gateway over *home*, in a later second than the one before, as any restart is."""
    started = int(time.time())
    while int(time.time()) == started:
        time.sleep(0.02)
    return _Gateway(home)


def _texts(handle: MagicMock) -> list[tuple[str, str, str]]:
    """What a channel's handle posted, as ``(channel, text, thread)``."""
    return [
        (c.args[0], c.args[1], c.args[2] if len(c.args) > 2 else "")
        for c in handle.deliver_text.await_args_list
    ]


async def _a_telegram_chat(gateway: _Gateway) -> str:
    return await gateway.message(
        "telegram", "Book the 9:10 to the coast", channel=DM, thread=DM, sender=DM
    )


async def _a_reply_in_the_slack_thread(gateway: _Gateway, *, answer: str = "") -> str:
    return await gateway.message(
        "slack",
        "And a return on Sunday",
        channel=SLACK_DM,
        thread=THREAD,
        sender=SLACK_OWNER,
        answer=answer,
    )


# ── the chat goes where it was continued ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_telegram_chat_continued_in_a_slack_thread_answers_in_the_slack_thread(
    tmp_path, channels
):
    """🔴 Red before: the answer went to Telegram's handle with the Slack thread's ids, and the
    Slack thread heard nothing."""
    telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)

    await gateway.continue_on(chat, "slack")
    reached = await _a_reply_in_the_slack_thread(gateway, answer="Booked: Sunday 17:40 back.")

    assert reached == chat, "the reply in the Slack thread opened another chat"
    slack.deliver_chat_mirror.assert_awaited_once_with(
        SLACK_DM, "Booked: Sunday 17:40 back.", THREAD
    )
    telegram.deliver_chat_mirror.assert_not_awaited()
    telegram.start_stream.assert_not_awaited()
    assert gateway.state.channel_provider_for(chat) == "slack"


@pytest.mark.asyncio
async def test_the_telegram_thread_is_told_once_where_the_chat_went_and_hears_nothing_after(
    tmp_path, channels
):
    """🔴 Red before: Telegram was never told the chat had moved, and kept its answers."""
    telegram, _slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)

    await gateway.continue_on(chat, "slack")
    await _a_reply_in_the_slack_thread(gateway, answer="Booked: Sunday 17:40 back.")

    assert _texts(telegram) == [
        (DM, "This chat continues on Slack now. Messages here no longer reach it.", DM)
    ]
    telegram.deliver_chat_mirror.assert_not_awaited()
    # The DM no longer continues the chat: the owner's next message there is a chat of its own.
    other = await gateway.message("telegram", "Is it booked?", channel=DM, thread=DM, sender=DM)
    assert other != chat
    assert ("user", "Is it booked?") not in [
        (m["role"], m["content"]) for m in gateway.state._sessions[chat].messages
    ]


@pytest.mark.asyncio
async def test_the_chat_still_answers_in_the_slack_thread_after_a_restart(tmp_path, channels):
    """🔴 Red before: the chat's channel was its origin tag, saved with it, so after a restart it
    was Telegram's again."""
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)
    await gateway.continue_on(chat, "slack")
    gateway.stop()

    second = _restart(tmp_path)
    linked = second.state.get_linked_session(THREAD)  # brings the chat back from disk

    assert linked is not None and linked.key == chat
    assert second.state.channel_provider_for(chat) == "slack"
    assert second.state.get_linked_session(DM) is None, "the Telegram DM still holds the chat"


@pytest.mark.asyncio
async def test_a_link_from_the_dashboard_moves_a_chat_that_is_on_another_channel(
    tmp_path, channels
):
    """🔴 Red before: a chat linked anywhere was "already linked", so linking a Telegram chat to a
    Slack channel said so in the Telegram DM and opened nothing on Slack."""
    telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)

    linked = await gateway.link(chat, {"channel": SLACK_CHANNEL})

    assert not linked.get("already_linked"), linked
    assert (linked["thread_ts"], linked["channel"]) == (THREAD, SLACK_CHANNEL)
    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (THREAD, SLACK_CHANNEL)
    assert gateway.state.channel_provider_for(chat) == "slack"
    assert _texts(telegram) == [
        (DM, "This chat continues on Slack now. Messages here no longer reach it.", DM)
    ]
    assert slack.deliver_text.await_args_list[0].args[0] == SLACK_CHANNEL


@pytest.mark.asyncio
async def test_a_chat_continued_in_another_thread_of_the_owners_dm_tells_the_one_it_left(
    tmp_path, channels
):
    """The same channel, another conversation: the owner's DM thread the chat was in is told."""
    _telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)
    await gateway.continue_on(chat, "slack")
    slack.deliver_text.reset_mock()
    slack.deliver_text.return_value = "1712793900.000300"

    await gateway.continue_on(chat, "slack")

    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (
        "1712793900.000300",
        SLACK_DM,
    )
    assert (
        SLACK_DM,
        "This chat continues in another Slack conversation now. Messages here no longer reach it.",
        THREAD,
    ) in _texts(slack)


@pytest.mark.asyncio
async def test_someone_elses_conversation_hears_nothing_when_the_chat_moves(tmp_path, channels):
    """A chat that came from another person's DM, continued on the owner's Slack: their DM is not
    told where the owner went on with it. The chat still answers on Slack."""
    telegram, slack = channels
    ct.allow_sender("telegram", OTHER_DM, name="Ben")
    gateway = _Gateway(tmp_path)
    chat = await gateway.message(
        "telegram", "Can you book for two?", channel=OTHER_DM, thread=OTHER_DM, sender=OTHER_DM
    )

    await gateway.continue_on(chat, "slack")

    assert _texts(telegram) == [], "the other person was told where the owner went on with it"
    assert gateway.state.channel_provider_for(chat) == "slack"
    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (THREAD, SLACK_DM)


@pytest.mark.asyncio
async def test_a_shared_channel_thread_hears_nothing_when_the_chat_moves(tmp_path, channels):
    """A chat in a thread of a shared Slack channel, continued on Telegram: the channel is not
    told where the owner went on with it."""
    telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)
    await gateway.link(chat, {"channel": SLACK_CHANNEL})
    slack.deliver_text.reset_mock()

    await gateway.continue_on(chat, "telegram")

    assert [text for chan, text, _ in _texts(slack) if chan == SLACK_CHANNEL] == []
    assert gateway.state.channel_provider_for(chat) == "telegram"
    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (DM, DM)


# ── the controls: continuing a chat where it already is changes nothing ────────────────────────


@pytest.mark.asyncio
async def test_continuing_a_telegram_chat_on_telegram_changes_nothing(tmp_path, channels):
    telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)

    await gateway.continue_on(chat, "telegram")
    reached = await gateway.message(
        "telegram", "And back?", channel=DM, thread=DM, sender=DM, answer="The 17:40."
    )

    assert reached == chat
    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (DM, DM)
    assert gateway.state.channel_provider_for(chat) == "telegram"
    told = [text for _chan, text, _thread in _texts(telegram)]
    assert len(told) == 1 and "Reply to continue this session" in told[0], told
    telegram.deliver_chat_mirror.assert_awaited_once_with(DM, "The 17:40.", DM)
    slack.deliver_text.assert_not_awaited()
    slack.deliver_chat_mirror.assert_not_awaited()


@pytest.mark.asyncio
async def test_linking_a_chat_on_the_channel_it_is_on_says_so_and_moves_nothing(tmp_path, channels):
    telegram, slack = channels
    gateway = _Gateway(tmp_path)
    chat = await _a_telegram_chat(gateway)

    linked = await gateway.link(chat, {"provider": "telegram"})

    assert linked["already_linked"] is True and (linked["thread_ts"], linked["channel"]) == (DM, DM)
    assert gateway.sessions.get_channel_link(f"dashboard:{chat}") == (DM, DM)
    assert _texts(telegram) == [(DM, "Session linked from the dashboard. Continuing here.", DM)]
    slack.deliver_text.assert_not_awaited()


# ── the store: a link names its channel, and keeps it ──────────────────────────────────────────


def test_a_link_names_its_channel_once_the_store_is_read_again():
    """🔴 Red before: a link said which thread and which channel id, not which channel."""
    store = SessionMap()
    store.set_channel_link("dashboard:chat-a", THREAD, SLACK_DM, channel_provider="slack")

    again = SessionMap()
    assert again.get_channel_link("dashboard:chat-a") == (THREAD, SLACK_DM)
    assert again.get_channel_provider("dashboard:chat-a") == "slack"


@pytest.mark.asyncio
async def test_a_session_keeps_its_channel_when_its_agent_session_ends_or_its_thread_moves():
    store = SessionMap()
    store.set("dashboard:chat-a", "sid-1")
    store.set_channel_link("dashboard:chat-a", DM, DM, channel_provider="telegram")

    store.forget_session_id("dashboard:chat-a")
    assert store.get_channel_provider("dashboard:chat-a") == "telegram"

    manager = SessionManager(AppConfig())
    manager.set_channel_link("dashboard:chat-b", THREAD, SLACK_DM, channel_provider="slack")
    await manager.set_thread("dashboard:chat-b", "1712793900.000300")
    await manager.set_channel("dashboard:chat-b", SLACK_CHANNEL)
    assert manager.get_channel_link("dashboard:chat-b") == ("1712793900.000300", SLACK_CHANNEL)
    assert manager.get_channel_provider("dashboard:chat-b") == "slack"


def test_an_unlinked_session_is_on_no_channel():
    store = SessionMap()
    store.set_channel_link("dashboard:chat-a", DM, DM, channel_provider="telegram")

    store.set_channel_link("dashboard:chat-a", "", "")

    assert store.get_channel_provider("dashboard:chat-a") == ""
    assert SessionMap().get_channel_provider("dashboard:chat-a") == ""
