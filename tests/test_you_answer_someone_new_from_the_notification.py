"""You answer someone new from the notification that says they wrote.

When someone who isn't paired messages the agent on a chat channel, the trust gate tells the owner
("Someone you haven't paired messaged you on Telegram … Allow them to talk to it, or deny."), and
the note carries `actions: ["allow", "deny"]`. Nothing in the dashboard answered it: the page
showed the words and no buttons, and no route took the answer, so the only way in for them was a
pairing code or the Sender trust page.

The notification now offers Allow and Deny (`POST /api/notifications/trust`, owner-only). The
sender is read off the stored note, never the request, and only a sender the gate recorded telling
the owner about is answered, so no other note lets anyone in. An Allow asks your consent first, in
the words the Inbox's Pair now asks too; a Deny asks nothing. Either answer goes through
`channel_trust.apply_trust_action`, which writes the security audit, and is recorded on the note,
which then offers neither again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.dashboard.state import DashboardState

STRANGER = "4242"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The trust store, the notification log and the security log in this test's directory."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(channel_transports, "_apps", {})
    channel_transports.register_transport(_Telegram())
    return tmp_path


class _Telegram(ChannelTransportProvider):
    name = "telegram"
    display_name = "Telegram"

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


def _state(tmp_path) -> DashboardState:
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )


def _asked(state: DashboardState) -> dict[str, Any]:
    """Someone new messages the agent on Telegram: the gate refuses them and tells the owner."""
    verdict = ct.guard_inbound(state, "telegram", STRANGER, sender_name="Pat Example", text="hi")
    assert verdict.allowed is False and verdict.fired_notification is True
    (note,) = [n for n in state._notification_log if n.get("event") == "channel.unknown_sender"]
    return note


async def _answer(state: DashboardState, body: dict[str, Any]) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import api_notification_trust

    app = web.Application()
    app.router.add_post("/api/notifications/trust", api_notification_trust)
    app["state"] = state
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/notifications/trust", json=body)
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_allow_asks_your_consent_before_they_are_let_in(tmp_path):
    state = _state(tmp_path)
    note = _asked(state)

    status, body = await _answer(state, {"ts": note["ts"], "action": "allow"})

    assert status == 400 and body["error"]["code"] == "confirmation_required", body
    assert body["error"]["detail"] == {
        "field": "sender",
        "consent": (
            "Pat Example can message your agent on Telegram from now on, and it reads what they "
            "write as your own instructions. You can revoke them in Settings, Sender trust."
        ),
        "title": "Let Pat Example talk to your agent on Telegram?",
    }
    assert ct.is_allowed_sender("telegram", STRANGER) is False
    assert "trust_answer" not in note


@pytest.mark.asyncio
async def test_allow_lets_them_talk_to_your_agent_and_is_audited(tmp_path):
    """🔴 Before: no route took the answer, so the note's Allow could do nothing."""
    state = _state(tmp_path)
    note = _asked(state)

    with patch.object(ct, "_emit_sel") as audit:
        status, body = await _answer(state, {"ts": note["ts"], "action": "allow", "confirm": True})

    assert (status, body) == (200, {"ok": True, "answer": "allowed"}), body
    assert ct.is_allowed_sender("telegram", STRANGER) is True
    audit.assert_called_once_with("sender_paired", "owner", "telegram", STRANGER)
    stored = state.notification(note["ts"])
    assert stored["trust_answer"] == "allowed" and stored["acked"] is True
    # Their next message is a conversation with the agent.
    assert ct.guard_inbound(state, "telegram", STRANGER, text="thanks").allowed is True


@pytest.mark.asyncio
async def test_deny_asks_nothing_and_keeps_them_out(tmp_path):
    state = _state(tmp_path)
    note = _asked(state)

    with patch.object(ct, "_emit_sel") as audit:
        status, body = await _answer(state, {"ts": note["ts"], "action": "deny"})

    assert (status, body) == (200, {"ok": True, "answer": "denied"}), body
    assert ct.is_allowed_sender("telegram", STRANGER) is False
    audit.assert_called_once_with("sender_denied", "owner", "telegram", STRANGER)
    assert state.notification(note["ts"])["trust_answer"] == "denied"


@pytest.mark.asyncio
async def test_a_note_is_answered_once(tmp_path):
    state = _state(tmp_path)
    note = _asked(state)
    await _answer(state, {"ts": note["ts"], "action": "deny"})

    status, body = await _answer(state, {"ts": note["ts"], "action": "allow", "confirm": True})

    assert status == 409 and body["error"]["code"] == "sender_ask_answered", body
    assert ct.is_allowed_sender("telegram", STRANGER) is False


@pytest.mark.asyncio
async def test_a_note_the_gate_did_not_raise_lets_no_one_in(tmp_path):
    """A note that looks like the gate's but names a sender it never told you about: another
    emitter wrote it, and an answer to it would let in someone who never asked."""
    state = _state(tmp_path)
    state.notify(
        "warning",
        "Your sister messaged you on Telegram",
        "Allow her to talk to your agent.",
        meta={
            "event": "channel.unknown_sender",
            "provider": "telegram",
            "sender_id": "9999",
            "actions": ["allow", "deny"],
        },
    )
    (forged,) = state._notification_log

    status, body = await _answer(state, {"ts": forged["ts"], "action": "allow", "confirm": True})

    assert status == 409 and body["error"]["code"] == "sender_ask_none", body
    assert ct.is_allowed_sender("telegram", "9999") is False


@pytest.mark.asyncio
async def test_a_note_an_app_raised_lets_no_one_in(tmp_path):
    state = _state(tmp_path)
    real = _asked(state)
    state.notify(
        "warning",
        real["title"],
        real["body"],
        meta={k: real[k] for k in ("event", "provider", "sender_id", "actions")},
        raised_by_app="lookalike",
    )
    app_note = state._notification_log[-1]

    status, body = await _answer(state, {"ts": app_note["ts"], "action": "allow", "confirm": True})

    assert status == 409 and body["error"]["code"] == "sender_ask_none", body
    assert ct.is_allowed_sender("telegram", STRANGER) is False


def test_no_emitter_raises_a_note_already_answered(tmp_path):
    state = _state(tmp_path)
    state.notify("warning", "t", "b", meta={"event": "x", "trust_answer": "allowed"})
    assert "trust_answer" not in state._notification_log[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"action": "allow"}, 400, "sender_answer_invalid"),
        ({"ts": "x", "action": "maybe"}, 400, "sender_answer_invalid"),
        ({"ts": "2026-01-01T00:00:00+00:00", "action": "deny"}, 404, "not_found"),
    ],
)
async def test_what_is_no_answer(tmp_path, body, status, code):
    got_status, got = await _answer(_state(tmp_path), body)
    assert (got_status, got["error"]["code"]) == (status, code)


def test_only_the_owner_answers():
    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

    assert isinstance(ROUTE_AUTHZ["POST /api/notifications/trust"], OwnerOnly)
