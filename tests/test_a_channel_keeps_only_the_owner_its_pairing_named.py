"""A channel can keep only the owner its pairing named, and have core forget any other.

Core stores a channel's owner under the channel's own key (``owner_id_credential``), and reads
it back with ``owner_id_for``, which falls back to the one key every channel wrote before each had
its own. The owner pairing (a code shown on the channel's Configure page, sent to the bot in a
direct message) is the one way a person proves the account is theirs; an id stored any other way
was never confirmed. Core records who its pairing named (``paired_owner``), and a channel that keeps
no other owner asks core to forget one that came some other way (``forget_owner``): the id leaves
the channel's own key, the shared key stops answering for the channel, the id leaves the channel's
trust list, and the security log says so. Another channel that still reads the shared key keeps
reading it.

Each refusal sits beside its vacuity floor: the same setup, before the forget or with the paired
owner, still names the owner.
"""

from __future__ import annotations

import os

import pytest

from personalclaw import channel_trust as ct
from personalclaw.config.credentials import get_credential, owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.sdk.channel import (
    CANNED_OWNER_PAIRED_REPLY,
    forget_owner,
    owner_id_for,
    paired_owner,
)

PROVIDER = "achat"
OTHER = "bchat"
OWNER = "U0NOOR"
CLAIMED = "U0STRANGER"


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
    keys = (CRED_OWNER_ID, owner_id_credential(PROVIDER), owner_id_credential(OTHER))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)


class _State:
    def notify(self, *_args, **_kwargs) -> None:
        pass


def _pair(sender: str = OWNER) -> None:
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


# ── who the pairing named ─────────────────────────────────────────────────────────────────


def test_the_pairing_records_who_it_made_the_owner():
    assert paired_owner(PROVIDER) == "", "vacuity floor: nothing paired yet"

    _pair()

    assert paired_owner(PROVIDER) == OWNER
    assert owner_id_for(PROVIDER) == OWNER


def test_a_code_found_inside_a_message_records_the_owner_too():
    code = ct.create_owner_pairing_code(PROVIDER)

    assert ct.redeem_owner_pairing_code(PROVIDER, OWNER, code, "Noor") is True

    assert paired_owner(PROVIDER) == OWNER


def test_pairing_another_account_names_that_one():
    _pair()
    _pair("U0ROBIN")

    assert paired_owner(PROVIDER) == "U0ROBIN"
    assert owner_id_for(PROVIDER) == "U0ROBIN"


def test_an_owner_stored_some_other_way_was_not_paired():
    save_credential(owner_id_credential(PROVIDER), CLAIMED)

    assert owner_id_for(PROVIDER) == CLAIMED, "vacuity floor: core reads the stored owner"
    assert paired_owner(PROVIDER) == ""


# ── forgetting an owner nobody paired ─────────────────────────────────────────────────────


def test_a_forgotten_owner_is_no_longer_read_trusted_or_listed():
    save_credential(owner_id_credential(PROVIDER), CLAIMED)
    ct.allow_sender(PROVIDER, CLAIMED, "Someone", via="owner")
    assert ct.is_allowed_sender(PROVIDER, CLAIMED), "vacuity floor: they were trusted"

    assert forget_owner(PROVIDER, CLAIMED) is True

    assert owner_id_for(PROVIDER) == ""
    assert get_credential(owner_id_credential(PROVIDER)) == ""
    assert not os.environ.get(owner_id_credential(PROVIDER))
    assert not ct.is_allowed_sender(PROVIDER, CLAIMED)
    assert ct.owner_ref(PROVIDER) == {"id": "", "source": "", "name": ""}
    assert (
        "owner_forgotten",
        "forgotten",
        f"{PROVIDER}:{CLAIMED}",
    ) in _sel_rows()


def test_the_shared_key_no_longer_answers_for_a_channel_that_forgot_its_owner():
    """An earlier release kept the owner only under the shared key. Forgetting it there leaves the
    key itself alone, for a channel that still reads it, and this channel no longer does."""
    save_credential(CRED_OWNER_ID, CLAIMED)
    assert owner_id_for(PROVIDER) == CLAIMED, "vacuity floor: core falls back to the shared key"
    assert ct.owner_ref(PROVIDER)["source"] == "shared"

    assert forget_owner(PROVIDER, CLAIMED) is True

    assert owner_id_for(PROVIDER) == ""
    assert get_credential(CRED_OWNER_ID) == CLAIMED, "the shared key was deleted for every channel"
    assert owner_id_for(OTHER) == CLAIMED, "another channel lost the owner it reads"


def test_forgetting_an_owner_here_and_in_the_shared_key_forgets_both():
    """The channel's own key and the shared key can hold different ids: an owner typed in after
    an earlier release left a first sender under the shared key. Neither answers afterwards."""
    save_credential(CRED_OWNER_ID, "U0EARLIER")
    save_credential(owner_id_credential(PROVIDER), CLAIMED)

    assert forget_owner(PROVIDER, CLAIMED) is True

    assert (
        owner_id_for(PROVIDER) == ""
    ), "the id under the shared key answered once the own key went"


def test_only_the_owner_core_holds_is_forgotten():
    save_credential(owner_id_credential(PROVIDER), OWNER)

    assert forget_owner(PROVIDER, CLAIMED) is False
    assert forget_owner(PROVIDER, "") is False

    assert owner_id_for(PROVIDER) == OWNER
    assert not [r for r in _sel_rows() if r[0] == "owner_forgotten"]


def test_a_forget_leaves_an_owner_code_on_show_alone():
    """The owner may be pairing while the channel forgets an owner it held: the code she is about
    to send still works."""
    save_credential(owner_id_credential(PROVIDER), CLAIMED)
    code = ct.create_owner_pairing_code(PROVIDER)

    forget_owner(PROVIDER, CLAIMED)

    assert ct.owner_pairing_status(PROVIDER)["active"] is True
    assert ct.redeem_owner_pairing_code(PROVIDER, OWNER, code, "Noor") is True
    assert owner_id_for(PROVIDER) == OWNER
    assert paired_owner(PROVIDER) == OWNER


def test_after_a_forget_pairing_is_the_way_back():
    save_credential(owner_id_credential(PROVIDER), CLAIMED)
    forget_owner(PROVIDER, CLAIMED)

    _pair()

    assert owner_id_for(PROVIDER) == OWNER
    assert paired_owner(PROVIDER) == OWNER
    assert ct.is_allowed_sender(PROVIDER, OWNER)
    assert not ct.is_allowed_sender(PROVIDER, CLAIMED)


def test_a_paired_channel_takes_no_owner_from_the_shared_key():
    """Once its own key is gone (deleted from Settings → Secrets, say), a channel whose owner core
    paired does not fall back to whichever platform's id the shared key holds."""
    save_credential(CRED_OWNER_ID, "424242")
    _pair()
    from personalclaw.config.credentials import delete_credential

    delete_credential(owner_id_credential(PROVIDER))

    assert owner_id_for(PROVIDER) == ""
    assert owner_id_for(OTHER) == "424242", "vacuity floor: a channel never paired still reads it"
