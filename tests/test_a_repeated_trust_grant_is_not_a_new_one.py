"""Letting in a sender who is already in, or tracking a group already tracked, is not a new grant.

A channel app keeps its own list of who it lets in and which groups it reads, and writes that list
through to core's store every time it starts, so the two agree. That write had to skip every entry
the store already held: a repeat ``allow_sender`` stamped a fresh "added" date and wrote another
``sender_paired`` audit row, and a repeat ``track`` stamped a fresh date too. So a name the owner
gave a sender or a group after it was first let in never reached Settings › Sender trust, which
listed the bare id. A repeat grant by the same route now keeps its date, is not audited again,
and takes the name it is given; a grant by another route is recorded as the new provenance it is.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from personalclaw import channel_trust as ct


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the entity-settings store + SEL at tmp_path (the real home is never touched)."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


def _sender(provider: str, sender_id: str) -> dict:
    (row,) = [
        s for s in ct.provider_trust(provider)["allowed_senders"] if s["sender_id"] == sender_id
    ]
    return row


def _group(provider: str, channel_id: str) -> dict:
    (row,) = [
        c for c in ct.provider_trust(provider)["tracked_channels"] if c["channel_id"] == channel_id
    ]
    return row


def _paired_rows() -> int:
    from personalclaw.sel import sel

    return sum(1 for e in sel().recent(200) if e.get("operation") == "sender_paired")


def _later(monkeypatch, seconds: int = 3600) -> None:
    then = ct._now() + timedelta(seconds=seconds)
    monkeypatch.setattr(ct, "_now", lambda: then)


def test_a_repeat_allow_takes_the_name_and_keeps_the_date(monkeypatch):
    ct.allow_sender("slack", "U0ROBIN", via="owner")
    first = _sender("slack", "U0ROBIN")
    audited = _paired_rows()
    assert first["name"] == "" and audited == 1

    _later(monkeypatch)
    ct.allow_sender("slack", "U0ROBIN", "Robin", via="owner")

    again = _sender("slack", "U0ROBIN")
    assert again["name"] == "Robin"
    assert again["added_at"] == first["added_at"], "she was let in when she was first let in"
    assert again["via"] == "owner"
    assert _paired_rows() == audited, "naming someone already in is not a new grant"


def test_a_repeat_allow_with_no_name_keeps_the_name_it_has(monkeypatch):
    ct.allow_sender("slack", "U0ROBIN", "Robin", via="owner")
    _later(monkeypatch)
    ct.allow_sender("slack", "U0ROBIN", via="owner")
    assert _sender("slack", "U0ROBIN")["name"] == "Robin"


def test_a_grant_by_another_route_is_recorded_and_audited(monkeypatch):
    """The floor: a sender who pairs as the owner after being let in by a code is a new grant."""
    ct.allow_sender("telegram", "5550", "Robin", via="pairing")
    audited = _paired_rows()
    _later(monkeypatch)
    ct.allow_sender("telegram", "5550", "Robin Ash", via="owner_pairing")

    row = _sender("telegram", "5550")
    assert row["via"] == "owner_pairing"
    assert row["name"] == "Robin Ash"
    assert _paired_rows() == audited + 1


def test_a_revoked_sender_let_in_again_is_a_new_grant(monkeypatch):
    ct.allow_sender("slack", "U0SAM", "Sam", via="owner")
    first = _sender("slack", "U0SAM")["added_at"]
    ct.deny_sender("slack", "U0SAM")
    _later(monkeypatch)
    audited = _paired_rows()
    ct.allow_sender("slack", "U0SAM", "Sam", via="owner")

    assert _sender("slack", "U0SAM")["added_at"] != first
    assert _paired_rows() == audited + 1


def test_a_repeat_track_takes_the_name_and_keeps_the_date(monkeypatch):
    ct.track("slack", "C0ALERTS")
    first = _group("slack", "C0ALERTS")
    assert first["name"] == ""

    _later(monkeypatch)
    ct.track("slack", "C0ALERTS", "alerts")
    again = _group("slack", "C0ALERTS")
    assert again["name"] == "alerts"
    assert again["added_at"] == first["added_at"]

    ct.track("slack", "C0ALERTS")
    assert _group("slack", "C0ALERTS")["name"] == "alerts", "no name given keeps the one it has"
