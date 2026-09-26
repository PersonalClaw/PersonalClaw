"""The owner pairs a channel from the dashboard: a code sent in a DM makes the sender its OWNER.

A channel's owner id — who core DMs the heartbeat, cron results, subagent replies, approvals and a
chat's handoff to — was written only by ``personalclaw setup``, and pairing codes came only from
``personalclaw pair``, which allow-lists a sender and leaves the owner unset. After a setup done in
the UI, the owner could talk to the bot and nothing core sent the owner reached anybody.

``create_owner_pairing_code`` mints a code the Configure page shows. Whoever sends it to the bot in
a direct message becomes the channel's owner: :func:`guard_inbound` — the gate every channel's
inbound crosses — stores the sender's id under ``owner_id_credential(<provider>)`` and trusts them.

Each negative case carries its vacuity floor: the same setup, sent the right code, pairs.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from personalclaw import channel_trust as ct
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID

# The published names a channel app reads its owner with.
from personalclaw.sdk.channel import owner_id_for

PROVIDER = "telegram"
OWNER = "424242"
STRANGER = "999"


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
    keys = (CRED_OWNER_ID, owner_id_credential(PROVIDER), owner_id_credential("discord"))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)


class _State:
    def __init__(self) -> None:
        self.notes: list[dict] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append({"kind": kind, "title": title, "meta": meta or {}})


def _dm(text: str, sender: str = OWNER, provider: str = PROVIDER):
    return ct.guard_inbound(
        _State(), provider, sender, sender_name="Dana", channel_id=sender, is_dm=True, text=text
    )


def _set_dm_policy(policy: str) -> None:
    store = ct._read_store()
    rec = ct._provider_record(store, PROVIDER)
    rec["policies"]["dm"] = policy
    store[PROVIDER] = rec
    ct._write_store(store)


def _sel_rows():
    from personalclaw.sel import sel

    return [
        (e.get("operation"), e.get("outcome"), e.get("caller_identity")) for e in sel().recent(200)
    ]


# ── the round trip ──────────────────────────────────────────────────────────────────────────


def test_the_code_sent_in_a_dm_makes_the_sender_this_channels_owner():
    assert owner_id_for(PROVIDER) == "", "vacuity floor: no owner before pairing"

    code = ct.create_owner_pairing_code(PROVIDER)
    verdict = _dm(code)

    assert verdict.allowed is False, "the code is spent on pairing, not answered by the agent"
    assert verdict.reason == "owner_paired"
    assert verdict.canned_reply == ct.CANNED_OWNER_PAIRED_REPLY
    # Stored under THIS channel's own key — not the shared one another platform could hold.
    assert owner_id_for(PROVIDER) == OWNER
    assert os.environ.get(owner_id_credential(PROVIDER)) == OWNER
    assert not os.environ.get(CRED_OWNER_ID)
    # And trusted, so their next message is a turn.
    assert ct.is_allowed_sender(PROVIDER, OWNER)
    assert _dm("hello").allowed is True
    status = ct.owner_pairing_status(PROVIDER)
    assert status["active"] is False and status["ended"] == "paired"


def test_the_code_works_once():
    code = ct.create_owner_pairing_code(PROVIDER)
    assert _dm(code).reason == "owner_paired"
    again = _dm(code, sender=STRANGER)
    assert again.reason != "owner_paired"
    assert owner_id_for(PROVIDER) == OWNER, "a spent code cannot hand the channel to someone else"


def test_only_the_hash_is_stored_and_nothing_projects_the_code(isolated):
    code = ct.create_owner_pairing_code(PROVIDER)
    raw = (isolated / "entity_settings" / "channel_trust.json").read_text(encoding="utf-8")
    assert code not in raw
    assert code not in str(ct.provider_trust(PROVIDER))
    assert code not in str(ct.owner_pairing_status(PROVIDER))
    created = [r for r in _sel_rows() if r[0] == "owner_pairing_code_created"]
    assert created and all(code not in str(r) for r in created)


@pytest.mark.parametrize("policy", ["pairing", "owner_only", "open"])
def test_the_owner_pairs_under_every_dm_policy(policy):
    """`owner_only` refuses a SENDER's code (the owner's Allow is the only door) — the owner's own
    code is that door, minted from the dashboard. Under `open` the code must not become a turn."""
    _set_dm_policy(policy)
    code = ct.create_owner_pairing_code(PROVIDER)
    verdict = _dm(code)
    assert verdict.reason == "owner_paired"
    assert owner_id_for(PROVIDER) == OWNER


def test_a_sender_already_trusted_can_pair_as_the_owner():
    ct.allow_sender(PROVIDER, OWNER, via="pairing")
    assert _dm("hi").allowed is True, "vacuity floor: this sender is already trusted"
    code = ct.create_owner_pairing_code(PROVIDER)
    assert _dm(code).reason == "owner_paired"
    assert owner_id_for(PROVIDER) == OWNER


def test_pairing_replaces_the_previous_owner():
    from personalclaw.config.credentials import save_credential

    save_credential(owner_id_credential(PROVIDER), "111")
    code = ct.create_owner_pairing_code(PROVIDER)
    assert _dm(code, sender="222").reason == "owner_paired"
    assert owner_id_for(PROVIDER) == "222"


def test_a_group_message_never_pairs_an_owner():
    code = ct.create_owner_pairing_code(PROVIDER)
    ct.track(PROVIDER, "grp", name="Standup")
    verdict = ct.guard_inbound(
        _State(), PROVIDER, STRANGER, channel_id="grp", is_dm=False, text=code
    )
    assert verdict.reason != "owner_paired"
    assert owner_id_for(PROVIDER) == ""
    assert _dm(code).reason == "owner_paired", "vacuity floor: the same code pairs in a DM"


def test_an_expired_code_does_not_pair(monkeypatch):
    code = ct.create_owner_pairing_code(PROVIDER)
    later = ct._now() + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS + 1)
    monkeypatch.setattr(ct, "_now", lambda: later)
    assert _dm(code).reason != "owner_paired"
    assert owner_id_for(PROVIDER) == ""
    assert ct.owner_pairing_status(PROVIDER)["ended"] == "expired"


def test_a_cancelled_code_does_not_pair():
    code = ct.create_owner_pairing_code(PROVIDER)
    assert ct.cancel_owner_pairing(PROVIDER) is True
    assert _dm(code).reason != "owner_paired"
    assert ct.owner_pairing_status(PROVIDER)["ended"] == "cancelled"


def test_wrong_codes_cancel_the_code_at_the_limit():
    """An owner code grants the owner's approvals, so guessing is capped: after the limit the code
    is cancelled and the status says why."""
    code = ct.create_owner_pairing_code(PROVIDER)
    wrong = "00000000" if code != "00000000" else "11111111"
    for n in range(ct.OWNER_PAIRING_MAX_ATTEMPTS - 1):
        _dm(wrong, sender=STRANGER)
        assert ct.owner_pairing_status(PROVIDER)["attempts_left"] == (
            ct.OWNER_PAIRING_MAX_ATTEMPTS - n - 1
        )
    assert ct.owner_pairing_status(PROVIDER)["active"] is True
    _dm(wrong, sender=STRANGER)
    status = ct.owner_pairing_status(PROVIDER)
    assert status["active"] is False and status["ended"] == "too_many_attempts"
    assert _dm(code).reason != "owner_paired"
    assert owner_id_for(PROVIDER) == ""


def test_a_trusted_senders_eight_digit_message_still_reaches_the_agent():
    ct.allow_sender(PROVIDER, STRANGER, via="owner")
    ct.create_owner_pairing_code(PROVIDER)
    verdict = _dm("12345678", sender=STRANGER)
    assert verdict.allowed is True, "a wrong code from a trusted sender is an ordinary message"


def test_a_sender_code_and_the_owner_code_are_independent():
    owner_code = ct.create_owner_pairing_code(PROVIDER)
    sender_code = ct.create_pairing_code(PROVIDER)

    friend = _dm(sender_code, sender=STRANGER)
    assert friend.reason == "paired" and ct.is_allowed_sender(PROVIDER, STRANGER)
    assert owner_id_for(PROVIDER) == "", "a sender's code makes nobody the owner"
    status = ct.owner_pairing_status(PROVIDER)
    assert status["active"] is True
    assert (
        status["attempts_left"] == ct.OWNER_PAIRING_MAX_ATTEMPTS
    ), "redeeming the sender's code is not a wrong guess at the owner's"
    assert _dm(owner_code).reason == "owner_paired"
    assert owner_id_for(PROVIDER) == OWNER


def test_pairing_is_audited_without_the_code():
    code = ct.create_owner_pairing_code(PROVIDER)
    _dm(code)
    rows = _sel_rows()
    assert ("owner_paired", "paired", f"{PROVIDER}:{OWNER}") in rows
    assert all(code not in str(r) for r in rows)


def test_the_status_before_any_code():
    status = ct.owner_pairing_status(PROVIDER)
    assert status == {
        "active": False,
        "expires_at": "",
        "attempts_left": 0,
        "ended": "",
        "ended_at": "",
    }
