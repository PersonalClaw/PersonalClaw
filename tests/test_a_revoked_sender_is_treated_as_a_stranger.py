"""A sender you revoke is a stranger again, and someone unpaired who keeps writing is never lost.

Settings › Sender trust › Revoke says the sender "will be dropped from your allowlist and their next
message will be treated as a stranger's … They can be let back in with a new pairing code." A
stranger's first message is answered with the pairing note, and the owner is told who wrote, once
per sender per renotify window (``channel_trust.UNKNOWN_SENDER_RENOTIFY_SECS``). That window is
stamped at first contact, and the revoke left it standing: a sender revoked within a day of first
writing got no reply and the owner no notice, so their next message was discarded with nobody told.

A revoke now starts their window over. And inside a window, what someone unpaired writes after the
one reply and the one notice is counted on the Sender trust page (who wrote, how many times, the
last when, never what they wrote), so the limit paces the telling and never hides them. They still
get the one reply at most.

Driven through the inbound door every channel uses (``channel_inbound.deliver_inbound``), with the
core's channel test double for the owner's side (``CapturingState``) and the page's own routes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.dashboard.handlers import channel_trust as h
from personalclaw.testing.channel_conformance import CapturingState

PROVIDER = "telegram"
ROBIN = "700100"
ROBIN_NAME = "Robin Example"
T0 = datetime(2026, 9, 30, 19, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The trust store and the security log in this test's directory, never the real home."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    ct.reset_inbound_reports()
    yield tmp_path


class _Clock:
    """The trust gate's clock, moved by hand, so a window can be measured from inside."""

    def __init__(self, monkeypatch) -> None:
        self.now = T0
        monkeypatch.setattr(ct, "_now", lambda: self.now)

    def advance(self, **delta: float) -> datetime:
        self.now = self.now + timedelta(**delta)
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    return _Clock(monkeypatch)


class _Services:
    """What a channel holds: the gateway handle whose dashboard state the door reaches."""

    def __init__(self, state: CapturingState) -> None:
        self.dashboard_state = state


async def _a_turn(state, session, text) -> None:
    """The agent's turn for a message the door let in. Nothing to run here."""


def _send(state: CapturingState, text: str, mid: str, sender: str = ROBIN) -> ct.TrustVerdict:
    """One direct message from ``sender`` through the door, the way a channel hands it over."""
    msg = ChannelMessage(
        channel_id=f"dm-{sender}",
        text=text,
        sender=sender,
        message_id=mid,
        metadata={"sender_name": ROBIN_NAME if sender == ROBIN else ""},
    )
    return asyncio.run(ci.deliver_inbound(_Services(state), PROVIDER, msg, turn_runner=_a_turn))


def _page() -> dict:
    """``GET /api/channels/trust``, as the Sender trust page reads it."""
    req = make_mocked_request("GET", "/api/channels/trust")
    return json.loads(asyncio.run(h.api_channel_trust(req)).body.decode("utf-8"))


def _listed() -> dict:
    return {p["provider"]: p for p in _page()["providers"]}[PROVIDER]


def _revoke_on_the_page(sender_id: str) -> int:
    """The page's Revoke: ``DELETE /api/channels/trust/{provider}/senders/{sender_id}``."""
    req = make_mocked_request(
        "DELETE",
        f"/api/channels/trust/{PROVIDER}/senders/{sender_id}",
        match_info={"provider": PROVIDER, "sender_id": sender_id},
    )
    return asyncio.run(h.api_channel_trust_revoke(req)).status


def _asked_about(state: CapturingState) -> list[str]:
    """Who the owner was asked about, one entry per notice, in order."""
    return [n["meta"]["sender_id"] for n in state.with_actions()]


def _robin_allowed_within_the_hour(state: CapturingState, clock: _Clock) -> None:
    """Robin writes as a stranger, the owner lets them in from the notice, and they talk."""
    first = _send(state, "hi, it's Robin", "m1")
    assert first.canned_reply == ct.CANNED_PAIRING_REPLY and first.fired_notification
    clock.advance(minutes=10)
    assert ct.apply_trust_action("allow", PROVIDER, ROBIN, ROBIN_NAME) is True
    clock.advance(minutes=10)
    assert _send(state, "can you add bread to the list?", "m2").allowed is True


# ── a revoke starts the window over ──────────────────────────────────────────────────────────


def test_a_sender_revoked_on_the_page_gets_the_pairing_note_and_the_owner_is_told(clock):
    state = CapturingState()
    _robin_allowed_within_the_hour(state, clock)
    clock.advance(minutes=30)
    assert _revoke_on_the_page(ROBIN) == 200
    assert ct.owner_was_asked_about(PROVIDER, ROBIN), "the first notice about them still answers"

    clock.advance(seconds=10)
    after = _send(state, "hello?", "m3")

    assert after.allowed is False and after.reason == "unknown_sender"
    assert after.canned_reply == ct.CANNED_PAIRING_REPLY, "their next message is a stranger's"
    assert after.fired_notification is True
    assert _asked_about(state) == [ROBIN, ROBIN], "the owner is told again, about them"
    assert state.delivered_texts() == ["can you add bread to the list?"], "only the trusted one"


def test_the_notice_s_deny_on_someone_let_in_by_a_code_is_a_revoke_too(clock):
    """The notice raised at their first contact still offers Deny after a code let them in. That
    answer drops them from the list, so it starts their window over as the page's Revoke does."""
    state = CapturingState()
    _send(state, "hi", "m1")
    code = ct.create_pairing_code(PROVIDER)
    clock.advance(minutes=2)
    assert _send(state, code, "m2").reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, ROBIN)

    clock.advance(minutes=5)
    assert ct.apply_trust_action("deny", PROVIDER, ROBIN) is False
    clock.advance(minutes=1)
    after = _send(state, "still there?", "m3")

    assert after.canned_reply == ct.CANNED_PAIRING_REPLY and after.fired_notification
    assert _asked_about(state) == [ROBIN, ROBIN]


# ── inside a window: one reply, one notice, and every message counted for the owner ──────────


def test_a_second_message_inside_the_window_is_not_answered_and_is_counted_for_the_owner(clock):
    state = CapturingState()
    _robin_allowed_within_the_hour(state, clock)
    clock.advance(minutes=30)
    _revoke_on_the_page(ROBIN)
    first_after = clock.advance(seconds=10)
    _send(state, "hello?", "m3")

    last = clock.advance(minutes=4)
    second = _send(state, "are you there? it's Robin", "m4")

    assert second.allowed is False
    assert second.canned_reply == "", "at most the one reply a window"
    assert second.fired_notification is False and _asked_about(state) == [ROBIN, ROBIN]
    assert _listed()["seen_senders"] == [
        {
            "sender_id": ROBIN,
            "name": ROBIN_NAME,
            "since": first_after.isoformat(),
            "last_seen": last.isoformat(),
            "count": 2,
        }
    ], "counted from their first message after the revoke: before it, they were let in"
    page = json.dumps(_page())
    assert "are you there" not in page and "hello?" not in page, "never what they wrote"


def test_denying_a_stranger_from_the_notice_keeps_the_window_and_still_counts_them(clock):
    """A Deny on someone who was never let in is an answer, not a revoke: they are not asked about
    again inside the window, and what they keep sending is counted, not lost."""
    state = CapturingState()
    _send(state, "hi", "m1")
    clock.advance(minutes=3)
    ct.apply_trust_action("deny", PROVIDER, ROBIN)

    clock.advance(minutes=3)
    again = _send(state, "hi again", "m2")

    assert again.canned_reply == "" and again.fired_notification is False
    assert _asked_about(state) == [ROBIN]
    assert [(s["sender_id"], s["count"]) for s in _listed()["seen_senders"]] == [(ROBIN, 2)]


def test_once_the_window_has_passed_they_are_answered_and_the_owner_told_again(clock):
    state = CapturingState()
    _send(state, "hi", "m1")
    clock.advance(seconds=ct.UNKNOWN_SENDER_RENOTIFY_SECS + 1)
    later = _send(state, "hi, a day later", "m2")

    assert later.canned_reply == ct.CANNED_PAIRING_REPLY and later.fired_notification
    assert [(s["sender_id"], s["count"]) for s in _listed()["seen_senders"]] == [(ROBIN, 2)]


# ── the list is of people who are NOT paired ─────────────────────────────────────────────────


@pytest.mark.parametrize("way_in", ["pairing code", "the notice's Allow"])
def test_letting_someone_in_takes_them_off_the_list(clock, way_in):
    state = CapturingState()
    _send(state, "hi", "m1")
    assert [s["sender_id"] for s in _listed()["seen_senders"]] == [ROBIN]

    clock.advance(minutes=1)
    if way_in == "pairing code":
        assert _send(state, ct.create_pairing_code(PROVIDER), "m2").reason == "paired"
    else:
        ct.apply_trust_action("allow", PROVIDER, ROBIN, ROBIN_NAME)

    entry = _listed()
    assert entry["seen_senders"] == []
    assert [s["sender_id"] for s in entry["allowed_senders"]] == [ROBIN]


def test_the_list_keeps_the_newest_and_says_nothing_of_the_window(clock):
    state = CapturingState()
    for n in range(ct.SEEN_SENDERS_MAX + 5):
        clock.advance(seconds=1)
        _send(state, "hello", f"m{n}", sender=f"80{n:03d}")

    listed = _listed()["seen_senders"]
    assert len(listed) == ct.SEEN_SENDERS_MAX
    assert listed[0]["sender_id"] == f"80{ct.SEEN_SENDERS_MAX + 4:03d}", "newest first"
    page = json.dumps(_page())
    assert '"rate"' not in page, "the renotify stamps stay off the page"


def test_a_message_held_for_the_owner_is_not_listed_again(clock):
    """A channel that writes as the owner holds a stranger's message in the Inbox, where the owner
    answers it; the page does not list it a second time."""
    verdict = ct.guard_inbound(
        CapturingState(),
        PROVIDER,
        "someone@example.com",
        is_dm=True,
        text="hello",
        hold_for_owner=lambda: True,
    )
    assert verdict.allowed is False and verdict.fired_notification is True
    assert _listed()["seen_senders"] == []
