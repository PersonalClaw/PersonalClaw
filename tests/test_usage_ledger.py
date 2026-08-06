"""COST-AND-TOKEN-OBSERVABILITY §2.4 / C1 — the per-turn cost/token ledger.

Covers the store only (the write sites C2 + surfaces S2 are later atoms):
round-trip, the five rollup group keys, the tainted-total `priced` rule, the
fail-open write contract, and the inventory registration (audit_home passes WITH
the `usage` entry and fails WITHOUT it — both proven, per the done-when).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw import usage_ledger as ul
from personalclaw.usage_ledger import TurnUsage


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Isolated config_dir so the ledger writes under tmp, never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _u(**over) -> TurnUsage:
    base = dict(
        ts="2026-08-06T12:00:00+00:00",
        session_key="s1",
        source="chat",
        agent="",
        provider="anthropic",
        model="claude-opus-5",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.5,
        priced=True,
    )
    base.update(over)
    return TurnUsage(**base)


def test_round_trip(_home):
    ul.record_turn(_u(input_tokens=100, output_tokens=20, cost_usd=0.5))
    rows = ul._iter_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "claude-opus-5" and r["input_tokens"] == 100
    assert r["cost_usd"] == 0.5 and r["priced"] is True


def test_storage_path_is_usage_turns_jsonl(_home):
    ul.record_turn(_u())
    assert ul._path() == _home / "usage" / "turns.jsonl"
    assert (_home / "usage" / "turns.jsonl").is_file()


def test_rollup_groups_by_each_key(_home):
    ul.record_turn(_u(model="m-a", source="chat", cost_usd=1.0))
    ul.record_turn(_u(model="m-a", source="loop", cost_usd=2.0))
    ul.record_turn(_u(model="m-b", source="chat", cost_usd=0.5))
    for key in ("model", "source", "agent", "provider", "day"):
        rows = ul.rollup(group_by=key)
        assert rows, f"rollup by {key} returned nothing"
        # Every group's cost is the sum of its members; the grand total is invariant.
        assert round(sum(r["cost_usd"] for r in rows), 6) == 3.5
    by_model = {r["model"]: r for r in ul.rollup(group_by="model")}
    assert by_model["m-a"]["cost_usd"] == 3.0 and by_model["m-a"]["turns"] == 2
    assert by_model["m-b"]["cost_usd"] == 0.5


def test_rollup_rejects_unknown_group_key(_home):
    with pytest.raises(ValueError, match="group_by must be one of"):
        ul.rollup(group_by="nonsense")


def test_unpriced_row_taints_group_and_total(_home):
    ul.record_turn(_u(model="m-a", cost_usd=1.0, priced=True))
    ul.record_turn(_u(model="m-a", cost_usd=0.0, priced=False))  # unpriced constituent
    grp = {r["model"]: r for r in ul.rollup(group_by="model")}["m-a"]
    assert grp["priced"] is False, "a group with any unpriced row must report priced=False"
    assert ul.totals()["priced"] is False


def test_priced_total_stays_true_when_all_priced(_home):
    ul.record_turn(_u(cost_usd=1.0, priced=True))
    ul.record_turn(_u(cost_usd=2.0, priced=True))
    t = ul.totals()
    assert t["priced"] is True and t["cost_usd"] == 3.0 and t["turns"] == 2


def test_rollup_window_filters_by_ts(_home):
    ul.record_turn(_u(ts="2026-08-01T00:00:00+00:00", cost_usd=1.0))
    ul.record_turn(_u(ts="2026-08-05T00:00:00+00:00", cost_usd=2.0))
    # [since, until) half-open window.
    rows = ul.rollup(since="2026-08-03T00:00:00+00:00", group_by="day")
    assert len(rows) == 1 and rows[0]["day"] == "2026-08-05"


def test_record_turn_is_fail_open(monkeypatch, _home):
    """A write failure degrades to a DEBUG log, never raises into the turn (§2.7)."""

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    # Must not raise despite the write blowing up.
    ul.record_turn(_u())


def test_iter_rows_tolerates_a_corrupt_line(_home):
    p = ul._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    ul.record_turn(_u(model="good"))
    with open(p, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    ul.record_turn(_u(model="good2"))
    rows = ul._iter_rows()
    assert [r["model"] for r in rows] == ["good", "good2"]  # bad line skipped, rest kept


# ── inventory registration (done-when: audit passes WITH, fails WITHOUT) ──


def test_usage_path_is_registered_in_inventory():
    from personalclaw.durability.inventory import by_id

    e = by_id("usage_ledger")
    assert e is not None and e.path == "usage"
    assert e.derived is True and e.secret is False  # telemetry-of-self, disposable


def test_audit_home_passes_with_registration(_home):
    from personalclaw.durability.inventory import audit_home

    ul.record_turn(_u())  # creates usage/turns.jsonl under the home
    result = audit_home(_home)
    assert "usage/" not in result.unclaimed, f"usage should be claimed, got {result.unclaimed}"


def test_audit_home_fails_without_registration(_home, monkeypatch):
    """Prove the registration is load-bearing: drop the usage entry and the audit
    reports usage/ as unclaimed."""
    import personalclaw.durability.inventory as inv

    ul.record_turn(_u())
    stripped = tuple(e for e in inv.INVENTORY if e.id != "usage_ledger")
    monkeypatch.setattr(inv, "INVENTORY", stripped)
    result = inv.audit_home(_home)
    assert "usage/" in result.unclaimed


# ── CATO-2: the chat write-site (_record_turn_usage) ──────────────────────────


class TestChatWriteSite:
    """`_record_turn_usage` lands exactly one row with the right priced/cost logic."""

    def _event(self, **over):
        base = dict(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            duration_ms=1234,
        )
        base.update(over)
        return SimpleNamespace(**base)

    def _call(self, event, model="claude-opus-4.5"):
        from personalclaw.dashboard.chat_runner import _record_turn_usage

        _record_turn_usage(
            event,
            session_key="dashboard:s1",
            source="chat",
            agent="",
            provider="anthropic",
            model=model,
        )

    def test_one_row_with_provider_reported_cost_is_priced(self, _home):
        self._call(self._event(cost_usd=0.42))
        rows = ul._iter_rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "chat" and r["provider"] == "anthropic"
        assert r["model"] == "claude-opus-4.5" and r["input_tokens"] == 100
        assert r["cost_usd"] == 0.42 and r["priced"] is True

    def test_priced_model_with_zero_cost_still_priced(self, _home):
        # No provider cost, but the model HAS a price row → priced=True (cost may be
        # a real estimate the caller already set, or 0.0 for a tiny turn).
        self._call(self._event(cost_usd=0.0), model="claude-opus-4.5")
        r = ul._iter_rows()[0]
        assert r["priced"] is True

    def test_unpriced_model_writes_priced_false_and_zero(self, _home):
        self._call(self._event(cost_usd=0.0), model="some-unknown-local-model")
        r = ul._iter_rows()[0]
        assert r["priced"] is False and r["cost_usd"] == 0.0

    def test_write_site_is_fail_open(self, monkeypatch, _home):
        """A ledger write failure must not raise into the turn."""
        import personalclaw.usage_ledger as _ul

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(_ul, "_path", _boom)
        self._call(self._event(cost_usd=0.1))  # must not raise
