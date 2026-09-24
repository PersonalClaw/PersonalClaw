"""Per-scope trust record (EVALUATION-SUBSTRATE §4.2, atom ES-13).

The behaviours the atom is defined by:

1. an accepted grant writes ONE durable record per scope, capturing the inputs
   ``resolve_rung`` consumes, and the record round-trips from disk (restart-safe);
2. a demotion flips the record to ``revoked`` with the triggering cause, and
   ``resolve_rung`` clamps a revoked scope to its floor even when the flat rung
   store diverges (record outlives the store);
3. the rung dialect rail: this ledger accepts ONLY ``autonomy.RUNGS`` names — the
   plan's L3/observed/unattended vocabulary is refused on write and treated as
   absent on read, so guardrails/autonomy.py stays the only rung dialect;
4. every failure direction is fail-safe: unreadable/malformed/mismatched records
   license nothing and revoke nothing.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import trust_record as tr


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home so records and the rung store land in tmp."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    yield home


KEY = "inbox.reply_draft"


@pytest.fixture(autouse=True)
def _registered_type():
    au.reset_action_types()
    au.register_action_type(
        au.ActionTypeSpec(
            key=KEY,
            floor=au.RUNG_DRAFT_ONLY,
            ceiling=au.RUNG_AUTONOMOUS,
        )
    )
    yield
    au.reset_action_types()


# ── 1. the record round-trips and survives restart ──────────────────────────


def test_grant_writes_record_and_round_trips():
    granted = au.grant_rung(KEY, au.RUNG_ONE_TAP, evidence_window="10 approvals / 7d")
    assert granted == au.RUNG_ONE_TAP

    record = tr.load_record(KEY)
    assert record is not None
    assert record.key == KEY
    assert record.rung == au.RUNG_ONE_TAP
    assert record.granted_at  # timestamp captured
    assert record.granted_by == "user"
    assert record.evidence.evidence_window == "10 approvals / 7d"
    assert record.revoked is False

    # Restart survival: a second load is a fresh disk read of the same file —
    # nothing lives in module state.
    again = tr.load_record(KEY)
    assert again == record
    assert tr._record_path(KEY).exists()


def test_record_is_one_file_per_scope():
    au.register_action_type(au.ActionTypeSpec(key="mail.send", ceiling=au.RUNG_ONE_TAP))
    au.grant_rung(KEY, au.RUNG_ONE_TAP)
    au.grant_rung("mail.send", au.RUNG_ONE_TAP)
    files = sorted(p.name for p in tr.trust_dir().glob("*.json"))
    assert len(files) == 2


# ── 2. demotion revokes; resolve_rung reads the record ──────────────────────


def test_demotion_marks_record_revoked_with_cause():
    au.grant_rung(KEY, au.RUNG_AUTO_WITH_UNDO)
    au.demote(KEY, "user rejected the draft")

    record = tr.load_record(KEY)
    assert record is not None
    assert record.revoked is True
    assert record.revoked_cause == "user rejected the draft"
    assert record.demotion_count == 1
    assert record.rung == au.RUNG_DRAFT_ONLY  # dropped to floor
    assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY


def test_revoked_record_clamps_even_when_store_diverges():
    """The durable record outlives the flat store — the fail-safe direction."""
    au.grant_rung(KEY, au.RUNG_AUTO_WITH_UNDO)
    assert au.resolve_rung(KEY) == au.RUNG_AUTO_WITH_UNDO

    # Simulate divergence: the record says revoked while the store still carries
    # the high grant (e.g. the store file was restored from a backup).
    tr.record_demotion(KEY, floor=au.RUNG_DRAFT_ONLY, cause="harmful verdict", at="2026-01-01")
    assert au.granted_rung(KEY) == au.RUNG_AUTO_WITH_UNDO  # store untouched
    assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY  # record wins downward


def test_fresh_grant_clears_revocation():
    au.grant_rung(KEY, au.RUNG_ONE_TAP)
    tr.record_demotion(KEY, floor=au.RUNG_DRAFT_ONLY, cause="failed study", at="2026-01-01")
    assert tr.is_revoked(KEY)

    tr.record_grant(KEY, au.RUNG_ONE_TAP, granted_at="2026-02-01")
    record = tr.load_record(KEY)
    assert record is not None
    assert record.revoked is False
    assert record.demotion_count == 1  # history is kept, flag is cleared


# ── 3. the single-dialect rail ───────────────────────────────────────────────


def test_unknown_rung_is_refused_on_write():
    tr.record_grant(KEY, "unattended", granted_at="2026-01-01")  # plan §4.2 dialect
    assert tr.load_record(KEY) is None
    tr.record_demotion(KEY, floor="L3", cause="x", at="2026-01-01")
    assert tr.load_record(KEY) is None


def test_unknown_rung_on_disk_reads_as_absent():
    tr.record_grant(KEY, au.RUNG_ONE_TAP, granted_at="2026-01-01")
    path = tr._record_path(KEY)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rung"] = "verified"  # a second dialect creeping in via the file
    raw["revoked"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert tr.load_record(KEY) is None
    assert tr.is_revoked(KEY) is False  # an unvouched record licenses AND revokes nothing


def test_module_mints_no_rung_vocabulary():
    """The rail as a rail: trust_record defines no rung-name constants of its own."""
    import inspect

    source = inspect.getsource(tr)
    for foreign in ("observed", "gated", "verified", "unattended", "L3"):
        assert f'"{foreign}"' not in source.replace("granted_by", "")
    # And the names it validates against ARE the autonomy ladder:
    for rung in au.RUNGS:
        assert tr._valid_rung(rung)
    assert not tr._valid_rung("unattended")


# ── 4. fail-safe reads ───────────────────────────────────────────────────────


def test_corrupt_record_reads_as_absent():
    tr.record_grant(KEY, au.RUNG_ONE_TAP, granted_at="2026-01-01")
    tr._record_path(KEY).write_text("{not json", encoding="utf-8")
    assert tr.load_record(KEY) is None
    assert tr.is_revoked(KEY) is False
    # And resolve_rung falls back to exactly the pre-record behavior.
    assert au.resolve_rung(KEY) in au.RUNGS


def test_key_mismatch_reads_as_absent():
    tr.record_grant(KEY, au.RUNG_ONE_TAP, granted_at="2026-01-01")
    path = tr._record_path(KEY)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["key"] = "someone.else"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert tr.load_record(KEY) is None


def test_absent_record_changes_nothing():
    assert tr.load_record(KEY) is None
    assert tr.is_revoked(KEY) is False
    assert au.resolve_rung(KEY) == au.RUNG_DRAFT_ONLY  # floor, as before ES-13
