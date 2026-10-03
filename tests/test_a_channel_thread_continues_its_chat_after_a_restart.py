"""A channel thread continues the chat it is linked to after the gateway restarts.

A message on a channel thread (a Telegram or Discord DM, a Slack thread, an email thread) continues
the chat that thread is linked to: the inbound door asks ``DashboardState.get_linked_session`` for
it. That answer came from a map the dashboard held in memory and filled only when a link was made,
so after any restart the map was empty and the next message on the thread started a new chat. The
conversation lost everything said before it, and the thread's old chat kept a link nothing read.

The link was on disk all along, in the session store (``session_map.json``), and it is now the one
place a link is kept and read: the door asks it, a chat reads its own link there, and linking a
thread takes it from the chat that had it there. A restart here is what it is on a running gateway:
a new session store and a new dashboard state over the same home.
"""

from __future__ import annotations

import asyncio
import functools
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw import channel_delivery
from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_persistence import (
    restore_recent_sessions,
    save_all_sessions_to_history,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.session import SessionManager
from personalclaw.session_map import SessionMap

PROVIDER = "telegram"
#: A Telegram DM: the chat id is the conversation, and the sender's own id.
DM = "5550123"
OTHER_DM = "5550456"


@pytest.fixture(autouse=True)
def _a_trusted_sender():
    """The owner's DM is let in, and no verdict outlives its test (the door caches one per
    message, keyed by the message, not by the home)."""
    ci.reset_admissions()
    ct.allow_sender(PROVIDER, DM, name="Ada")
    ct.allow_sender(PROVIDER, OTHER_DM, name="Ben")
    yield
    ci.reset_admissions()


class _Gateway:
    """One run of the gateway over a home: its session store, its dashboard state, and the door
    its channels deliver to (``GatewayServices.deliver_channel_inbound``)."""

    def __init__(self, home: Path) -> None:
        self.sessions: Any = SessionManager(AppConfig())
        self.dashboard_state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "sessions"),
        )
        self._count = 0

    @property
    def state(self) -> DashboardState:
        return self.dashboard_state

    async def message(self, text: str, *, thread: str = DM, turn_runner: Any = None) -> str:
        """The owner sends *text* on *thread*; returns the chat the door ran its turn in. The turn
        notes the message as its answer, unless *turn_runner* runs it."""
        self._count += 1
        reached: list[str] = []

        async def _notes_it(state: Any, session: Any, message: str) -> None:
            session.append("assistant", f"Noted: {message}", "msg msg-a")

        async def _runs(state: Any, session: Any, message: str) -> None:
            reached.append(session.key)
            await (turn_runner or _notes_it)(state, session, message)

        msg = ChannelMessage(
            channel_id=thread,
            text=text,
            sender=thread,
            thread_id=thread,
            message_id=f"{thread}-{self._count}",
        )
        before = set(self.state._background_tasks)
        verdict = await ci.deliver_inbound(self, PROVIDER, msg, is_dm=True, turn_runner=_runs)
        assert verdict.allowed, verdict
        await asyncio.wait_for(
            asyncio.gather(*(set(self.state._background_tasks) - before)), timeout=10
        )
        assert reached, "the door ran no turn"
        return reached[-1]

    def stop(self) -> None:
        """What a stopping gateway does with the chats it holds: it saves them."""
        save_all_sessions_to_history(self.state)

    def said(self, chat: str) -> list[tuple[str, str]]:
        return [(m["role"], m["content"]) for m in self.state._sessions[chat].messages]


def _restart(home: Path, *, restore: bool) -> _Gateway:
    """A new gateway over *home*. With *restore*, the start brings back the recent chats as a
    gateway's start does; without, a chat lives only on disk (older than the start's window, or
    ``restore_sessions`` off).

    It starts in a later second than the gateway before it, as any real restart does: a new chat
    is named by its number and its second, so one opened in the second the last run opened its
    first would be given that chat's name."""
    started = int(time.time())
    while int(time.time()) == started:
        time.sleep(0.02)
    gateway = _Gateway(home)
    if restore:
        restore_recent_sessions(gateway.state)
    return gateway


# ── the thread continues its chat ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [True, False], ids=["restored-at-start", "only-on-disk"])
async def test_a_message_after_a_restart_continues_the_chat_its_thread_is_linked_to(
    tmp_path, restore
):
    """🔴 Red before: the second message started a new chat, with none of the first in it."""
    first = _Gateway(tmp_path)
    chat = await first.message("Book the 9:10 to the coast")
    first.stop()

    second = _restart(tmp_path, restore=restore)
    reached = await second.message("And a return on Sunday")

    assert reached == chat, "the restart started a new chat for the thread"
    assert second.said(chat) == [
        ("user", "Book the 9:10 to the coast"),
        ("assistant", "Noted: Book the 9:10 to the coast"),
        ("user", "And a return on Sunday"),
        ("assistant", "Noted: And a return on Sunday"),
    ]
    assert list(second.state._sessions) == [chat], "a second chat was opened for the thread"


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [True, False], ids=["restored-at-start", "only-on-disk"])
async def test_a_restored_chat_still_answers_on_the_channel_it_came_from(tmp_path, restore):
    """🔴 Red before: a chat came back from a restart with no channel, so even when it was found
    its answers stayed in the dashboard (its origin was saved and never read back)."""
    first = _Gateway(tmp_path)
    chat = await first.message("Book the 9:10 to the coast")
    first.stop()

    second = _restart(tmp_path, restore=restore)
    session = second.state.get_linked_session(DM)

    assert session is not None and session.key == chat
    assert second.state.channel_provider_for(chat) == PROVIDER


def _client(answer: str) -> AsyncMock:
    async def _events():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=answer)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    return client


def _can_run_a_turn(gateway: _Gateway, answer: str) -> None:
    """Stand in for the model process only: the turn runs through the real chat runner."""
    sessions = gateway.sessions
    sessions.get_or_create = AsyncMock(return_value=(_client(answer), True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    gateway.state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    gateway.state._hook_store = hooks


@pytest.fixture
def telegram():
    """A connected Telegram: its delivery handle, which hears every answer a chat sends there."""
    delivery = MagicMock()
    delivery.deliver_text = AsyncMock(return_value="")
    delivery.deliver_chat_mirror = AsyncMock()
    delivery.start_stream = AsyncMock(return_value="")
    delivery.append_stream_task = AsyncMock()
    delivery.stop_stream = AsyncMock()
    channel_delivery.register(delivery, provider=PROVIDER)
    yield delivery
    channel_delivery.register(None, provider=PROVIDER)


@pytest.mark.asyncio
async def test_the_answer_after_a_restart_is_the_linked_chats_and_goes_back_to_the_thread(
    tmp_path, telegram
):
    """🔴 Red before: the answer came from a new chat that knew nothing of the first message."""
    from personalclaw.dashboard.chat_runner import run_chat

    first = _Gateway(tmp_path)
    chat = await first.message("Book the 9:10 to the coast")
    first.stop()

    second = _restart(tmp_path, restore=True)
    _can_run_a_turn(second, "Booked: Sunday 17:40 back.")
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        reached = await second.message(
            "And a return on Sunday",
            turn_runner=functools.partial(run_chat, arrived_from_channel=True),
        )

    assert reached == chat, "the restart started a new chat for the thread"
    contents = [m["content"] for m in second.state._sessions[chat].messages]
    assert contents[:2] == ["Book the 9:10 to the coast", "Noted: Book the 9:10 to the coast"]
    assert contents[-1] == "Booked: Sunday 17:40 back.", contents
    telegram.deliver_chat_mirror.assert_awaited_with(DM, "Booked: Sunday 17:40 back.", DM)


# ── the controls: what starts a new chat still does ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_thread_after_a_restart_starts_its_own_chat(tmp_path):
    first = _Gateway(tmp_path)
    chat = await first.message("Book the 9:10 to the coast")
    first.stop()

    second = _restart(tmp_path, restore=True)
    other = await second.message("Hello from someone else", thread=OTHER_DM)

    assert other != chat
    assert second.said(other)[0] == ("user", "Hello from someone else")
    assert ("user", "Hello from someone else") not in second.said(chat)


async def _delete(gateway: _Gateway, chat: str) -> None:
    """The chat's Delete button: ``DELETE /api/chat/sessions/{session}``."""
    from personalclaw.dashboard.chat import api_chat_session_delete

    app = _api_app(gateway.state)
    app.router.add_delete("/api/chat/sessions/{session}", api_chat_session_delete)
    async with TestClient(TestServer(app)) as client:
        resp = await client.delete(f"/api/chat/sessions/{chat}")
        assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True], ids=["before-a-restart", "after-a-restart"])
async def test_a_thread_whose_chat_was_deleted_starts_a_new_chat(tmp_path, restart):
    first = _Gateway(tmp_path)
    chat = await first.message("Book the 9:10 to the coast")
    await _delete(first, chat)
    gateway = first
    if restart:
        first.stop()
        gateway = _restart(tmp_path, restore=True)

    reached = await gateway.message("Are you there?")

    assert reached != chat, "a deleted chat came back for its thread"
    assert gateway.said(reached) == [
        ("user", "Are you there?"),
        ("assistant", "Noted: Are you there?"),
    ]
    assert chat not in gateway.state._sessions
    assert gateway.state.get_linked_session(DM) is gateway.state._sessions[reached]


# ── one thread, one chat, wherever the link was made ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_linking_a_thread_after_a_restart_takes_it_from_the_chat_that_had_it(tmp_path):
    """🔴 Red before: the chat that had the thread was known only to the map the restart emptied,
    so it kept its link: its notices and the messages typed into it still went to the thread, and
    which chat the thread continued after the next restart depended on file order."""
    first = _Gateway(tmp_path)
    old = await first.message("Book the 9:10 to the coast")
    first.stop()

    second = _restart(tmp_path, restore=True)
    other = second.state.get_or_create_session(app=PROVIDER)
    other.append("user", "Plan the trip", "msg msg-u")
    second.state.link_channel(other.key, DM, DM)  # "Continue on Telegram": a DM is one thread

    assert second.sessions.get_channel_link(f"dashboard:{old}") == ("", "")
    assert second.state._sessions[old].to_dict()["channel_linked"] is False
    assert second.state.get_linked_session(DM) is other
    second.stop()

    third = _restart(tmp_path, restore=False)
    assert await third.message("Is it booked?") == other.key


def test_a_chat_reads_its_link_where_the_link_is_kept(tmp_path):
    """🔴 Red before: a chat held its own copy of its link, read when it was made, so a link a
    channel app wrote through the session store never reached the chat's frame or its questions."""
    gateway = _Gateway(tmp_path)
    session = gateway.state.get_or_create_session("chat-trip")

    gateway.sessions.set_channel_link("dashboard:chat-trip", "1712793600.000200", "C0123ABC456")

    frame = session.to_dict()
    assert (frame["channel_linked"], frame["channel_thread_ts"], frame["channel_id"]) == (
        True,
        "1712793600.000200",
        "C0123ABC456",
    )
    assert "chat channel" in gateway.state.owner_questions.cannot_ask("dashboard:chat-trip")

    gateway.sessions.set_channel_link("dashboard:chat-trip", "", "")

    assert session.to_dict()["channel_linked"] is False
    assert gateway.state.owner_questions.cannot_ask("dashboard:chat-trip") == ""


@pytest.mark.asyncio
async def test_a_chat_linked_with_no_channel_is_not_continued_from_the_thread(tmp_path):
    """🔴 Red before: the door continued a chat that carried no channel to answer on (one a channel
    app linked without naming its channel), so the turn ran and its answer reached nobody on the
    thread. The message starts a chat on its own channel instead, and the old chat is left alone."""
    gateway = _Gateway(tmp_path)
    nowhere = gateway.state.get_or_create_session("chat-nowhere")
    gateway.state.link_channel(nowhere.key, DM, DM)

    reached = await gateway.message("Are you there?")

    assert reached != nowhere.key
    assert gateway.state.channel_provider_for(reached) == PROVIDER
    assert nowhere.messages == []


# ── the session store gives one answer, before it is read again and after ─────────────────────


def test_a_thread_continues_the_session_that_linked_it_last_once_the_store_is_read_again():
    """🔴 Red before: reading the store again rebuilt the thread index in file order, so a session
    that linked the thread last but was written to the file first lost it to the one before."""
    store = SessionMap()
    store.set("dashboard:chat-b", "sid-b")  # chat-b's entry is the older one in the file
    store.set_channel_link("dashboard:chat-a", "4242", "4242")
    store.set_channel_link("dashboard:chat-b", "4242", "4242")
    assert store.get_session_for_thread("4242") == "dashboard:chat-b"

    assert SessionMap().get_session_for_thread("4242") == "dashboard:chat-b"


def test_moving_one_session_to_another_thread_leaves_the_thread_another_session_took():
    """🔴 Red before: moving a session off its thread dropped the thread from the index even when
    another session had linked it since, so that session's thread continued nothing."""
    store = SessionMap()
    store.set_channel_link("dashboard:chat-a", "4242", "4242")
    store.set_channel_link("dashboard:chat-b", "4242", "4242")

    store.set_channel_link("dashboard:chat-a", "7777", "7777")

    assert store.get_session_for_thread("4242") == "dashboard:chat-b"
    assert store.get_session_for_thread("7777") == "dashboard:chat-a"


def test_an_unlinked_session_holds_no_thread():
    """🔴 Red before: unlinking indexed the empty thread, so asking for a thread with no id named
    the session that had just been unlinked."""
    store = SessionMap()
    store.set_channel_link("dashboard:chat-a", "4242", "4242")

    store.set_channel_link("dashboard:chat-a", "", "")

    assert store.get_session_for_thread("") is None
    assert store.get_session_for_thread("4242") is None
    assert store.get_channel_link("dashboard:chat-a") == ("", "")
