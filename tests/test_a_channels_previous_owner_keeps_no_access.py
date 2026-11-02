"""A channel's previous owner, or one you revoked, keeps no access.

Two records say who may reach your agent on a channel: the owner core keeps (``owner_id_for``,
paired with a code from the channel's Configure page) and the channel's trusted senders. Pairing a
new owner trusted the new account and left the previous one trusted, and that is the account an
owner moves away from: lost, shared or taken over. Revoke on the owner's own row in Settings ›
Sender trust took the account off the trusted senders only, so it stayed the channel's owner and
kept receiving its approval prompts.

Now pairing a new account takes the previous owner off the trusted senders in the same write, and
the security log says who was replaced. Revoke on the owner's row has core forget the owner
(``forget_owner``), so nothing core reads names them as the channel's owner. Sender trust marks
which row is the owner's. Each refusal sits beside its floor: pairing the same account again keeps
it, and Revoke on anyone else is what it was.

Driven through the pairing itself (a code minted for the Configure page, sent in a DM through
``guard_inbound``) and the real revoke route, over a scratch home.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_trust as ct
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.sdk.channel import CANNED_OWNER_PAIRED_REPLY, owner_id_for, paired_owner

pytestmark = pytest.mark.security

PROVIDER = "achat"
BEFORE = "U0BEFORE"
AFTER = "U0AFTER"
FRIEND = "U0FRIEND"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the entity-settings store, the SEL and the credential store at tmp_path."""
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


class _State:
    def notify(self, *_args, **_kwargs) -> None:
        pass


def _pair(sender: str) -> None:
    """The owner pairs the channel: a code minted for the Configure page, sent in a DM."""
    code = ct.create_owner_pairing_code(PROVIDER)
    verdict = ct.guard_inbound(
        _State(), PROVIDER, sender, sender_name="Noor", channel_id=sender, is_dm=True, text=code
    )
    assert verdict.reason == "owner_paired"
    assert verdict.canned_reply == CANNED_OWNER_PAIRED_REPLY


def _sel_rows() -> list[tuple]:
    from personalclaw.sel import sel

    return [
        (e.get("operation"), e.get("outcome"), e.get("caller_identity")) for e in sel().recent(200)
    ]


async def _revoke(sender_id: str) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers.channel_trust import api_channel_trust_revoke

    app = web.Application()
    app.router.add_delete(
        "/api/channels/trust/{provider}/senders/{sender_id}", api_channel_trust_revoke
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.delete(
            f"/api/channels/trust/{PROVIDER}/senders/{quote(sender_id, safe='')}"
        )
        return resp.status, await resp.json()


def test_pairing_a_new_owner_ends_the_previous_owners_trust() -> None:
    _pair(BEFORE)
    _pair(AFTER)

    assert owner_id_for(PROVIDER) == AFTER
    assert ct.is_allowed_sender(PROVIDER, AFTER)
    assert not ct.is_allowed_sender(PROVIDER, BEFORE), "the account you moved away from is out"
    assert ("owner_replaced", "paired", f"{PROVIDER}:{BEFORE}") in _sel_rows()


def test_pairing_the_same_account_again_keeps_it() -> None:
    _pair(BEFORE)
    _pair(BEFORE)

    assert owner_id_for(PROVIDER) == BEFORE and ct.is_allowed_sender(PROVIDER, BEFORE)
    assert not [r for r in _sel_rows() if r[0] == "owner_replaced"]


@pytest.mark.asyncio
async def test_revoking_the_owners_row_unpairs_them() -> None:
    _pair(BEFORE)

    status, body = await _revoke(BEFORE)

    assert status == 200 and body.get("owner") is True, body
    assert owner_id_for(PROVIDER) == "", "nothing core reads names them the owner"
    assert paired_owner(PROVIDER) == ""
    assert not ct.is_allowed_sender(PROVIDER, BEFORE)
    assert ("owner_forgotten", "forgotten", f"{PROVIDER}:{BEFORE}") in _sel_rows()


@pytest.mark.asyncio
async def test_revoking_anyone_else_is_what_it_was() -> None:
    _pair(BEFORE)
    ct.allow_sender(PROVIDER, FRIEND, "Sam", via="owner")

    status, body = await _revoke(FRIEND)

    assert status == 200 and body.get("owner") is False, body
    assert not ct.is_allowed_sender(PROVIDER, FRIEND)
    assert owner_id_for(PROVIDER) == BEFORE and ct.is_allowed_sender(PROVIDER, BEFORE)


@pytest.mark.asyncio
async def test_an_owner_who_changes_during_the_revoke_is_still_revoked(monkeypatch) -> None:
    """Who the owner is is read once, by the unpairing itself: one paired anew after the page
    showed the row (the unpairing finds them no longer the owner) is revoked as a sender, and the
    answer says they were not the owner."""
    _pair(BEFORE)
    monkeypatch.setattr(ct, "forget_owner", lambda _provider, _sender: False)

    status, body = await _revoke(BEFORE)

    assert status == 200 and body.get("owner") is False, body
    assert not ct.is_allowed_sender(PROVIDER, BEFORE)


def test_sender_trust_says_which_row_is_the_owners() -> None:
    _pair(BEFORE)
    ct.allow_sender(PROVIDER, FRIEND, "Sam", via="owner")

    rows = {s["sender_id"]: s for s in ct.provider_trust(PROVIDER)["allowed_senders"]}

    assert rows[BEFORE]["owner"] is True
    assert rows[FRIEND]["owner"] is False
