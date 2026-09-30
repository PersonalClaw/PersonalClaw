"""A sender's pairing code that ran out is not "outstanding" on the Sender trust page.

``personalclaw pair <channel>`` prints a code that "works once, then expires" after ten minutes.
The gate refuses it after that (``redeem_pairing_code``), but the page's read said a code was
outstanding for as long as the record sat in the store, so Settings › Sender trust went on saying
"A pairing code is outstanding … Anyone who sends it becomes a trusted sender" about a code nobody
could redeem. The owner's own code already read as ended the moment its time was up
(``owner_pairing_status``); a sender's code reads the same way now: past its time, no code is
outstanding.
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


def _at(monkeypatch, when):
    monkeypatch.setattr(ct, "_now", lambda: when)


def test_a_code_past_its_time_is_not_outstanding(monkeypatch):
    minted = ct._now()
    _at(monkeypatch, minted)
    ct.create_pairing_code("telegram")

    # Floor: while it can still be redeemed, the page says a code is out there, and until when.
    _at(monkeypatch, minted + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS - 1))
    live = ct.provider_trust("telegram")
    assert live["pairing_active"] is True
    assert live["pairing_expires_at"]

    _at(monkeypatch, minted + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS + 1))
    after = ct.provider_trust("telegram")
    assert after["pairing_active"] is False
    assert after["pairing_expires_at"] == ""


def test_the_read_says_what_the_gate_does(monkeypatch):
    """The page and the gate agree: the code the page no longer shows, the gate refuses."""
    minted = ct._now()
    _at(monkeypatch, minted)
    code = ct.create_pairing_code("telegram")
    _at(monkeypatch, minted + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS + 1))

    assert ct.provider_trust("telegram")["pairing_active"] is False
    assert ct.redeem_pairing_code("telegram", "u-late", code) is False
    assert ct.is_allowed_sender("telegram", "u-late") is False


def test_a_record_whose_time_cannot_be_read_is_not_outstanding(monkeypatch):
    """A stored code with no readable expiry is refused at the gate, so it is not shown as live."""
    ct.create_pairing_code("telegram")
    store = ct._read_store()
    store["telegram"]["pairing"]["expires_at"] = "not a time"
    ct._write_store(store)

    assert ct.provider_trust("telegram")["pairing_active"] is False


def test_the_page_read_carries_the_same_answer(monkeypatch):
    """``GET /api/channels/trust`` projects this read: an expired code is not outstanding there."""
    import asyncio

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import channel_trust as h

    minted = ct._now()
    _at(monkeypatch, minted)
    ct.create_pairing_code("telegram")
    _at(monkeypatch, minted + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS + 60))

    resp = asyncio.run(h.api_channel_trust(make_mocked_request("GET", "/api/channels/trust")))
    import json

    (telegram,) = [p for p in json.loads(resp.text)["providers"] if p["provider"] == "telegram"]
    assert telegram["pairing_active"] is False
