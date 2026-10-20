"""The owner's pairing code works on a channel whose messages carry it inside other text.

The gate redeems the owner's code when a direct message is the code and nothing else. A mail is
never that: the code sits in a body under a greeting, a signature and a quoted reply, so the email
channel could not offer owner pairing at all, and its owner id was a variable set by hand.
``redeem_owner_pairing_code`` redeems the code a channel finds inside its text, with the gate's own
rules: the sender becomes the owner under the channel's own key and is trusted, the code is spent,
and a wrong code-shaped word counts against the cap on wrong guesses.

The Configure page shows, over the code, how it is sent on a channel where that is not a direct
message to the bot (``ChannelTransportProvider.owner_pairing_hint``).

Each refusal has its floor: the same setup, handed the right code, pairs.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID

# The published names a channel app pairs its owner with.
from personalclaw.sdk.channel import (
    ChannelCapabilities,
    is_allowed_sender,
    owner_id_for,
    redeem_owner_pairing_code,
)

PROVIDER = "email"
OWNER = "noor@example.test"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The trust store, the SEL and the credential store in tmp_path, with no owner anywhere."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, owner_id_credential(PROVIDER), owner_id_credential("hinted"))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    channel_transports.unregister_transport("hinted")


def _attempts_left() -> int:
    return int(ct.owner_pairing_status(PROVIDER)["attempts_left"])


def test_the_code_found_in_a_message_makes_its_sender_the_owner():
    code = ct.create_owner_pairing_code(PROVIDER)
    assert redeem_owner_pairing_code(PROVIDER, OWNER, code, "Noor") is True
    assert owner_id_for(PROVIDER) == OWNER
    assert is_allowed_sender(PROVIDER, OWNER)
    assert ct.owner_pairing_status(PROVIDER)["ended"] == "paired"
    # Spent: the same code pairs nobody again.
    assert redeem_owner_pairing_code(PROVIDER, "other@example.test", code) is False
    assert owner_id_for(PROVIDER) == OWNER


def test_a_wrong_code_shaped_word_is_a_wrong_guess_and_the_cap_holds():
    code = ct.create_owner_pairing_code(PROVIDER)
    wrong = "12345678" if code != "12345678" else "87654321"
    before = _attempts_left()
    assert redeem_owner_pairing_code(PROVIDER, "guess@example.test", wrong) is False
    assert _attempts_left() == before - 1
    for _ in range(ct.OWNER_PAIRING_MAX_ATTEMPTS - 1):
        redeem_owner_pairing_code(PROVIDER, "guess@example.test", wrong)
    assert ct.owner_pairing_status(PROVIDER)["ended"] == "too_many_attempts"
    # Cancelled: the right code no longer pairs.
    assert redeem_owner_pairing_code(PROVIDER, OWNER, code) is False
    assert owner_id_for(PROVIDER) == ""


def test_words_that_are_not_code_shaped_cost_no_guess():
    code = ct.create_owner_pairing_code(PROVIDER)
    before = _attempts_left()
    for word in ("1234567", "123456789", "1234567x", "", "１２３４５６７８"):
        assert redeem_owner_pairing_code(PROVIDER, OWNER, word) is False
    assert _attempts_left() == before
    # The floor: the code itself, with the space a mail leaves around it.
    assert redeem_owner_pairing_code(PROVIDER, OWNER, f" {code}\n") is True
    assert owner_id_for(PROVIDER) == OWNER


def test_with_no_code_outstanding_nothing_pairs():
    assert redeem_owner_pairing_code(PROVIDER, OWNER, "12345678") is False
    assert owner_id_for(PROVIDER) == ""
    assert not is_allowed_sender(PROVIDER, OWNER)


# ── the Configure page says how the code is sent ────────────────────────────────────────────────


class _Hinted(ChannelTransportProvider):
    """A channel paired by mailing the code, which says so."""

    name = property(lambda self: "hinted")
    display_name = property(lambda self: "Hinted")

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, owner_pairing=True)

    def owner_pairing_hint(self) -> str:
        return "Mail this code to me@example.test from the address that should be the owner"

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    async def health(self) -> dict[str, Any]:
        return {"state": "ready", "detail": "fake"}


class _Plain(_Hinted):
    """A channel paired by a direct message to the bot: no hint of its own."""

    name = property(lambda self: "plain")

    def owner_pairing_hint(self) -> str:
        return ChannelTransportProvider.owner_pairing_hint(self)


@pytest.mark.asyncio
async def test_the_owner_route_says_how_the_code_is_sent():
    from personalclaw.dashboard.handlers.channel_owner import api_channel_owner

    channel_transports.register_transport(_Hinted())
    channel_transports.register_transport(_Plain())
    app = web.Application()
    app.router.add_get("/api/channels/{name}/owner", api_channel_owner)
    try:
        async with TestClient(TestServer(app)) as client:
            hinted = await (await client.get("/api/channels/hinted/owner")).json()
            plain = await (await client.get("/api/channels/plain/owner")).json()
    finally:
        channel_transports.unregister_transport("plain")
    assert hinted["pairing_hint"].startswith("Mail this code to me@example.test")
    assert plain["pairing_hint"] == ""
    assert hinted["pairing_supported"] is True
