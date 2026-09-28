"""A channel that speaks as the owner answers no stranger: their message waits in the Inbox.

The email channel sends from the owner's own mailbox. The gate handed it the pairing note for any
stranger ("I don't recognize you yet…"), and it sent that from the owner's address to whoever
wrote: it told them the mailbox is read, and it was a message the owner never agreed to send.

A channel now says it speaks as its owner (``ChannelCapabilities.speaks_as_owner``). The gate
hands it no reply for a stranger, and the door holds the stranger's message in the Inbox as
someone new. The owner answers it there: Reply sends their words back through the channel,
threaded under the message; Pair lets the sender talk to the agent from their next message on;
Ignore dismisses the row. A chat channel, which speaks as a bot, keeps its pairing note.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery
from personalclaw import channel_inbound as ci
from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
)
from personalclaw.inbox import InboxState, InboxStore
from personalclaw.testing.channel_conformance import CapturingState

STRANGER = "pat@example.org"
THREAD = "<root-1@example.org>"
ASKED = "Could I borrow your ladder on Saturday?"
HELD_NOTICE = "Their message is in your Inbox: reply to it, pair them, or ignore it."


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The trust store, the security log and the Inbox in this test's directory."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(channel_transports, "_apps", {})
    ci.reset_admissions()
    yield tmp_path
    ci.reset_admissions()


class _Channel(ChannelTransportProvider):
    def __init__(self, name: str, display: str, *, as_owner: bool) -> None:
        self._name, self._display, self._as_owner = name, display, as_owner

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, threads=True, speaks_as_owner=self._as_owner)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


class _State(CapturingState):
    """The kit's state, with a real Inbox in the test's directory."""

    def __init__(self, tmp_path) -> None:
        super().__init__()
        self._inbox_svc = None
        self._inbox_store = InboxStore(path=tmp_path / "inbox.json")
        self._inbox_state = InboxState(path=tmp_path / "inbox_state.json")
        self.frames: list[tuple[str, dict]] = []

    def broadcast_ws(self, kind: str, payload: dict) -> None:
        self.frames.append((kind, payload))


class _Services:
    def __init__(self, state) -> None:
        self.dashboard_state = state
        self.turns: list[str] = []

    async def deliver_channel_inbound(self, provider, msg, *, is_dm=True):
        async def turn_runner(state, session, text):
            self.turns.append(text)

        return await ci.deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=turn_runner)


def _mail(text=ASKED, mid="<m1@example.org>", thread=THREAD, **meta) -> ChannelMessage:
    return ChannelMessage(
        channel_id=STRANGER,
        text=text,
        sender=STRANGER,
        thread_id=thread,
        message_id=mid,
        ts=1_790_000_000.0,
        metadata={"sender_name": "Pat Example", "subject": "Ladder", **meta},
    )


async def _deliver(state, msg, provider="email"):
    services = _Services(state)
    verdict = await services.deliver_channel_inbound(provider, msg, is_dm=True)
    return verdict, services


def _rows(state) -> list:
    return list(state._inbox_store.items.values())


def _email(*, as_owner: bool = True) -> None:
    channel_transports.register_transport(_Channel("email", "Email", as_owner=as_owner))


# ── the gate: no reply for a stranger, from a channel that speaks as the owner ──────────────────


def _holds(result: bool = True) -> tuple[list[int], Any]:
    """A hold as the door hands the gate one: it records each call, and says whether it held."""
    calls: list[int] = []

    def hold() -> bool:
        calls.append(1)
        return result

    return calls, hold


def test_the_gate_hands_a_channel_that_speaks_as_the_owner_no_reply_for_a_stranger(tmp_path):
    state = _State(tmp_path)
    calls, hold = _holds()

    verdict = ct.guard_inbound(
        state, "email", STRANGER, sender_name="Pat Example", text="hi", hold_for_owner=hold
    )

    assert verdict.allowed is False and verdict.reason == "unknown_sender"
    assert verdict.canned_reply == "", "a reply went to a stranger in the owner's name"
    assert calls == [1] and verdict.fired_notification is True
    [note] = state.notifications
    assert note["title"] == "Someone new wrote to you on email"
    assert "Nothing was sent to them" in note["body"]
    assert HELD_NOTICE in note["body"]


def test_a_message_the_inbox_did_not_take_is_named_in_no_notice(tmp_path):
    """The notice is composed from what the Inbox took. A message it kept out (its thread muted,
    its row dismissed) is named in none, and the once-a-day window stays open for one it takes."""
    state = _State(tmp_path)
    _, kept_out = _holds(False)

    refused = ct.guard_inbound(state, "email", STRANGER, text="hi", hold_for_owner=kept_out)

    assert refused.allowed is False and refused.canned_reply == ""
    assert refused.fired_notification is False and state.notifications == []

    # Nothing was told, so the window is still open: the next message the Inbox takes is.
    _, hold = _holds()
    held = ct.guard_inbound(state, "email", STRANGER, text="hi again", hold_for_owner=hold)

    assert held.fired_notification is True
    [note] = state.notifications
    assert HELD_NOTICE in note["body"]


def test_a_hold_that_fails_holds_nothing_and_tells_nothing(tmp_path):
    state = _State(tmp_path)

    def broken() -> bool:
        raise OSError("the Inbox could not be written")

    verdict = ct.guard_inbound(state, "email", STRANGER, text="hi", hold_for_owner=broken)

    assert verdict.allowed is False and verdict.canned_reply == ""
    assert verdict.fired_notification is False and state.notifications == []


def test_a_chat_channel_still_says_how_to_pair(tmp_path):
    """A bot may answer: the pairing note stays for a channel that does not speak as the owner."""
    verdict = ct.guard_inbound(_State(tmp_path), "telegram", "4242", text="hi")

    assert verdict.canned_reply == ct.CANNED_PAIRING_REPLY


# ── the door: the stranger's message is held in the Inbox ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_strangers_mail_is_held_in_the_inbox_as_someone_new_and_nothing_answers_it(
    tmp_path,
):
    """🔴 Before: the verdict carried the pairing note, which the channel sent from the owner's
    address, and the stranger's message was nowhere the owner could answer it."""
    _email()
    state = _State(tmp_path)

    with patch("personalclaw.event_triggers.emit_event") as event:
        verdict, services = await _deliver(state, _mail())

    assert verdict.canned_reply == ""
    assert services.turns == [] and state.sessions_created == []
    [row] = _rows(state)
    assert row.refs == {"someone_new": "email", "channel_name": "Email"}
    assert row.source == "channel:email"
    assert (row.channel, row.sender_id, row.sender_name) == (STRANGER, STRANGER, "Pat Example")
    assert row.message == f"Ladder\n\n{ASKED}"
    assert row.thread_ts == THREAD and row.reply_target == "<m1@example.org>"
    assert row.can_reply is True and row.status == "pending"
    assert [kind for kind, _ in state.frames] == ["inbox_new_item"]
    event.assert_not_called()  # a sender the gate refused arms no automation


@pytest.mark.asyncio
async def test_each_message_is_one_row_and_a_redelivery_is_not_a_second(tmp_path):
    _email()
    state = _State(tmp_path)

    await _deliver(state, _mail())
    await _deliver(state, _mail())
    await _deliver(state, _mail(text="Also: bring the form.", mid="<m2@example.org>"))

    assert sorted(r.reply_target for r in _rows(state)) == ["<m1@example.org>", "<m2@example.org>"]


@pytest.mark.asyncio
async def test_a_dismissed_row_stays_dismissed_and_a_muted_thread_holds_no_more(tmp_path):
    _email()
    state = _State(tmp_path)
    await _deliver(state, _mail())
    [row] = _rows(state)
    state._inbox_state.dismissed.add(row.id)
    del state._inbox_store.items[row.id]
    state._inbox_state.muted_threads.add(THREAD)
    ci.reset_admissions()

    await _deliver(state, _mail())
    await _deliver(state, _mail(text="again", mid="<m3@example.org>"))

    assert _rows(state) == []


@pytest.mark.asyncio
async def test_a_muted_thread_a_day_later_raises_no_notice_and_the_next_held_message_does(
    tmp_path, monkeypatch
):
    """The owner muted a stranger's thread, and the stranger writes there again more than a day
    later: the message stays out of the Inbox, and no notice says it is there. The notice's
    window was not spent on it, so their next message, in a new thread, is held and told."""
    _email()
    state = _State(tmp_path)
    await _deliver(state, _mail())
    assert len(state.notifications) == 1 and len(_rows(state)) == 1
    state._inbox_state.muted_threads.add(THREAD)
    later = ct._now() + timedelta(seconds=ct.UNKNOWN_SENDER_RENOTIFY_SECS + 60)
    monkeypatch.setattr(ct, "_now", lambda: later)

    await _deliver(state, _mail(text="Are you there?", mid="<m5@example.org>"))

    assert len(_rows(state)) == 1, "a message in a muted thread was held"
    assert len(state.notifications) == 1, "a notice named a message that is not in the Inbox"

    new_thread = "<m6@example.org>"
    await _deliver(state, _mail(text="New question.", mid=new_thread, thread=new_thread))

    assert len(_rows(state)) == 2
    [_, note] = state.notifications
    assert note["title"] == "Someone new wrote to you on Email" and HELD_NOTICE in note["body"]


@pytest.mark.asyncio
async def test_a_chat_channels_stranger_is_not_held(tmp_path):
    _email(as_owner=False)
    state = _State(tmp_path)

    verdict, _ = await _deliver(state, _mail())

    assert verdict.canned_reply == ct.CANNED_PAIRING_REPLY
    assert _rows(state) == []


# ── the owner's answers: Reply, Pair ────────────────────────────────────────────────────────────


def _app(state) -> web.Application:
    from personalclaw.dashboard import handlers_inbox as H

    app = web.Application()
    app.router.add_post("/api/inbox/send", H.api_inbox_send)
    app.router.add_post("/api/inbox/{id}/pair", H.api_inbox_pair)
    app["state"] = state
    return app


async def _post(state, path: str, body: dict | None = None) -> tuple[int, dict]:
    async with TestClient(TestServer(_app(state))) as client:
        resp = await client.post(path, json=body or {})
        return resp.status, await resp.json()


async def _held(tmp_path) -> tuple[_State, Any]:
    _email()
    state = _State(tmp_path)
    await _deliver(state, _mail())
    [row] = _rows(state)
    return state, row


@pytest.mark.asyncio
async def test_a_reply_goes_to_them_through_the_channel_threaded_under_their_message(tmp_path):
    state, row = await _held(tmp_path)
    handle = MagicMock()
    handle.deliver_text = AsyncMock(return_value="<sent-1@example.test>")
    channel_delivery.register(handle, provider="email")

    reply = {"id": row.id, "text": "Yes, any time after ten."}
    status, body = await _post(state, "/api/inbox/send", reply)

    assert status == 200 and body == {"ok": True, "sent": True}, body
    handle.deliver_text.assert_awaited_once_with(STRANGER, "Yes, any time after ten.", THREAD)
    stored = state._inbox_store.items[row.id]
    assert stored.status == "handled" and stored.replied_at > 0


@pytest.mark.asyncio
async def test_a_reply_while_the_channel_is_down_is_kept_and_says_why(tmp_path):
    state, row = await _held(tmp_path)

    status, body = await _post(state, "/api/inbox/send", {"id": row.id, "text": "Yes."})

    assert status == 503
    assert body == {"error": "Email isn't connected, so the reply was not sent.", "sent": False}
    assert state._inbox_store.items[row.id].status == "pending"


@pytest.mark.asyncio
async def test_pair_asks_your_consent_before_anyone_new_is_let_in(tmp_path):
    """🔴 Before: one click let them in, with none of the consent the Sender trust page asks for
    opening a channel to strangers, or the unknown-sender notification's Allow asks."""
    state, row = await _held(tmp_path)

    status, body = await _post(state, f"/api/inbox/{row.id}/pair")

    assert status == 400, body
    assert body["error"]["code"] == "confirmation_required"
    assert body["error"]["detail"]["title"] == "Let Pat Example talk to your agent on Email?"
    assert ct.is_allowed_sender("email", STRANGER) is False
    assert state._inbox_store.items[row.id].status == "pending"


@pytest.mark.asyncio
async def test_pair_lets_them_talk_to_the_agent_from_their_next_message_on(tmp_path):
    state, row = await _held(tmp_path)
    assert ct.is_allowed_sender("email", STRANGER) is False

    status, body = await _post(state, f"/api/inbox/{row.id}/pair", {"confirm": True})

    assert status == 200 and body == {"ok": True, "paired": True}, body
    assert ct.is_allowed_sender("email", STRANGER) is True
    stored = state._inbox_store.items[row.id]
    assert stored.status == "handled" and stored.refs["paired"] is True

    verdict, services = await _deliver(state, _mail(text="Thanks!", mid="<m4@example.org>"))
    await asyncio.sleep(0)
    assert verdict.allowed is True
    assert services.turns == ["Thanks!"], "the paired sender's next message was not a turn"


@pytest.mark.asyncio
async def test_pair_on_an_ordinary_row_is_refused(tmp_path):
    from personalclaw.inbox import InboxItem

    state = _State(tmp_path)
    state._inbox_store.add(
        InboxItem(
            id="mail_0123456789abcdef_1790000000.0",
            channel="me@example.test",
            channel_name="me",
            thread_ts=None,
            message="hi",
            sender_id="friend@example.test",
            sender_name="Friend",
            source="mail",
            can_reply=True,
        )
    )
    state._inbox_store.flush()

    status, body = await _post(state, "/api/inbox/mail_0123456789abcdef_1790000000.0/pair")

    assert status == 409
    assert body == {"error": "Only a message from someone new can pair its sender."}
    assert ct.is_allowed_sender("email", "friend@example.test") is False
