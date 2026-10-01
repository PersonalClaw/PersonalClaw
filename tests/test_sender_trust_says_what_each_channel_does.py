"""Settings › Sender trust words each channel by what that channel says it can do.

Every channel's section used to be one chat-bot template: "Strangers must redeem a pairing code.
Only tracked groups are read.", a Group chats rule, "Add the bot to a group…" and a code to send
"to your bot in a direct message". A channel that sends as you, from your own mailbox, has no bot
and no groups, and a stranger there is sent nothing. So the page read what is true of a bot as true
of every channel, beside its own intro saying otherwise.

The read now carries what each channel declares (``ChannelCapabilities.groups`` and
``speaks_as_owner``, and ``sender_pairing_hint()``), keyed by nothing but the channel's own
declarations: core names no channel.

A sender's pairing code lets someone in only while the channel's rule for strangers asks for one
(``pairing``). The gate held that rule; a channel that finds the code inside a longer message calls
``redeem_pairing_code`` itself and skipped it, so under "only you let them in" a code still did.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelCapabilities, ChannelTransportProvider
from personalclaw.dashboard.handlers import channel_trust as h

BOT = "fakechat"
MAILBOX = "fakemail"
BROKEN = "fakebroken"

MAILBOX_HINT = (
    "Have them mail this code from the address you want to let in, to noor@example.com. "
    "It can be anywhere in the message"
)


class _Channel(ChannelTransportProvider):
    def __init__(self, key: str, shown: str, caps: ChannelCapabilities, hint: str = "") -> None:
        self._key, self._shown, self._caps, self._hint = key, shown, caps, hint

    name = property(lambda self: self._key)
    display_name = property(lambda self: self._shown)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def capabilities(self) -> ChannelCapabilities:
        return self._caps

    def sender_pairing_hint(self) -> str:
        return self._hint


class _Unreadable(_Channel):
    def capabilities(self) -> ChannelCapabilities:
        raise RuntimeError("capabilities unavailable")

    def sender_pairing_hint(self) -> str:
        raise RuntimeError("hint unavailable")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    channels = [
        _Channel(BOT, "FakeChat", ChannelCapabilities(inbound=True, groups=True)),
        _Channel(
            MAILBOX,
            "FakeMail",
            ChannelCapabilities(inbound=True, speaks_as_owner=True),
            hint=MAILBOX_HINT,
        ),
        _Unreadable(BROKEN, "FakeBroken", ChannelCapabilities()),
    ]
    for channel in channels:
        channel_transports.register_transport(channel)
    yield tmp_path
    for channel in channels:
        channel_transports.unregister_transport(channel.name)


def _app() -> web.Application:
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_get("/api/channels/trust", h.api_channel_trust)
    app.router.add_put("/api/channels/trust/{provider}/policies", h.api_channel_trust_policies)
    return app


def _run(method: str, path: str, body: Any = None) -> tuple[int, dict]:
    async def _go():
        async with TestClient(TestServer(_app())) as client:
            resp = await client.request(method, path, json=body)
            return resp.status, await resp.json()

    return asyncio.run(_go())


def _listed() -> dict[str, dict]:
    status, body = _run("GET", "/api/channels/trust")
    assert status == 200, body
    return {p["provider"]: p for p in body["providers"]}


def _does(entry: dict) -> tuple[bool, bool, str]:
    return entry["groups"], entry["speaks_as_owner"], entry["pairing_hint"]


# ── the read carries what each channel declares ─────────────────────────────────────────────


def test_each_channel_is_listed_with_what_it_declares_it_can_do():
    listed = _listed()
    assert _does(listed[BOT]) == (True, False, "")
    assert _does(listed[MAILBOX]) == (False, True, MAILBOX_HINT)


def test_a_channel_that_is_no_longer_set_up_claims_nothing():
    """Its trust state outlives it, so it is still listed; nothing reaches the agent through it,
    and there is no channel left to say what it can do."""
    ct.allow_sender("gone", "u1", "Rin")
    entry = _listed()["gone"]
    assert entry["registered"] is False
    assert _does(entry) == (False, False, "")


def test_unreadable_declarations_keep_the_group_rule_and_say_nothing_as_a_bot():
    """The group rule stays on the page, so the owner can still turn groups off; and the channel
    is worded as one that sends as you, the side on which the page promises no reply to a
    stranger (the gate reads it the same way)."""
    assert _does(_listed()[BROKEN]) == (True, True, "")


# ── the consent to open a channel to anyone says what that channel does ──────────────────────


def _consent(provider: str) -> str:
    status, body = _run("PUT", f"/api/channels/trust/{provider}/policies", {"dm": "open"})
    assert status == 400 and body["error"]["code"] == "confirmation_required", body
    return body["error"]["detail"]["consent"]


def test_opening_a_channel_that_sends_as_you_says_the_agent_answers_as_you():
    consent = _consent(MAILBOX)
    assert "bot" not in consent
    assert "anyone who writes to you on FakeMail can talk to your agent" in consent
    assert "answers them as you" in consent
    assert "your own instructions" in consent


def test_opening_a_bot_channel_still_names_the_bot():
    consent = _consent(BOT)
    assert consent.startswith("anyone who messages your bot on FakeChat")
    assert "answers them as you" not in consent


# ── a sender's code lets someone in only while the rule asks for one ────────────────────────


@pytest.mark.parametrize("policy", ["owner_only", "open"])
def test_a_code_is_not_redeemed_while_the_rule_does_not_ask_for_one(policy):
    code = ct.create_pairing_code(MAILBOX)
    ct.set_trust_policies(MAILBOX, dm=policy)
    assert ct.redeem_pairing_code(MAILBOX, "rin@example.com", code) is False
    assert not ct.is_allowed_sender(MAILBOX, "rin@example.com")


def test_a_code_is_redeemed_while_the_rule_asks_for_one():
    """The floor for the two refusals above: the same code, under ``pairing``, lets them in."""
    code = ct.create_pairing_code(MAILBOX)
    assert ct.redeem_pairing_code(MAILBOX, "rin@example.com", code) is True
    assert ct.is_allowed_sender(MAILBOX, "rin@example.com")


def test_leaving_the_rule_that_asks_for_a_code_ends_the_code_outstanding():
    """A code the page lists as outstanding says "anyone who sends it becomes a trusted sender";
    once the rule no longer takes codes that would be untrue, so the code ends with the rule."""
    ct.create_pairing_code(BOT)
    assert ct.provider_trust(BOT)["pairing_active"] is True
    ct.set_trust_policies(BOT, dm="owner_only")
    assert ct.provider_trust(BOT)["pairing_active"] is False

    ct.set_trust_policies(BOT, dm="pairing")
    ct.create_pairing_code(BOT)
    ct.set_trust_policies(BOT, group="off")
    assert ct.provider_trust(BOT)["pairing_active"] is True, "a group change leaves the code alone"


# ── the terminal's code says the same ───────────────────────────────────────────────────────


def _pair_in_the_terminal(provider: str, capsys) -> str:
    import argparse

    from personalclaw.cli_commands import _pair

    _pair(argparse.Namespace(provider=provider))
    return capsys.readouterr().out


def test_a_code_from_the_terminal_claims_no_bot(capsys):
    """``personalclaw pair email`` said to send the code "to the email bot". No channel is loaded
    in the terminal, so the sentence claims nothing a channel does."""
    out = _pair_in_the_terminal(MAILBOX, capsys)
    assert "bot" not in out
    assert f"send it to your agent on {MAILBOX} within 10 minutes" in out
    assert "lets nobody in" not in out


def test_a_code_from_the_terminal_says_when_the_rule_takes_none(capsys):
    ct.set_trust_policies(MAILBOX, dm="owner_only")
    out = _pair_in_the_terminal(MAILBOX, capsys)
    assert f"It lets nobody in until {MAILBOX}'s rule for strangers asks for a code again" in out
