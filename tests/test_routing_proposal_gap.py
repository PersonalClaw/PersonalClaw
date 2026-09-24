"""MRT-5's missing executor: the fold write asks whether the evidence outgrew the table.

``routing/proposals.py`` shipped complete and had **zero production importers** — ``propose`` took a
``current``, a ``proposed`` and an ``evidence`` dict and computed no gap, so the ``done_when``
clause *"a genuine quality gap at n>=5 enqueues a routing proposal"* had no ``n>=5``, no gap
detection and no call anywhere. ``routing/gap.py`` is that caller and ``stats.record_routing_stats``
is its trigger point; these rails exist so that deleting either would be caught.

Three things are asserted here that a green ``test_routing_proposals.py`` could not reach:

* **the wire** — driving the real ``record_routing_stats`` (the per-attempt hook the audit path
  calls) leaves a proposal in the queue. Replace the ``_check_for_gap`` call with ``pass`` and
  :class:`TestTheWire` goes red;
* **propose-don't-write across the WHOLE path**, not just across ``propose``: a model call that
  produces a proposal leaves ``routing_policy.json`` byte-identical, with a floor proving the same
  comparison sees the write that accepting it makes;
* **SC #8** — with both JSON files deleted the path degrades and writes no ``memory.db`` /
  ``knowledge.db``, a negative given a floor so it cannot pass vacuously.

Every test drives an isolated ``tmp_path`` home through ``PERSONALCLAW_HOME`` (read per call, cached
nowhere) and asserts the redirect bound. Nothing here may touch the real ``~/.personalclaw``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from personalclaw.routing import gap, policy, proposals, stats

UC = "chat"
QC = "general"
CLOUDY = "cloudy:big"
LOCAL = "ollama:small"
#: ``detect_gap`` derives its candidate pool as ``sorted(fold rows)``, which is this order.
REFS = [CLOUDY, LOCAL]


def _rec(*, provider: str = "ollama", model: str = "small", audit_id: str = "aud-1") -> dict:
    """One ``AttemptRecord.to_json_line`` dict, the shape the fold hook is handed."""
    return {
        "audit_id": audit_id,
        "use_case": UC,
        "query_class": QC,
        "provider": provider,
        "model": model,
        "passed": True,
        "latency_ms": 120.0,
        "dollars_est": 0.0,
    }


def _fold(
    home: Path,
    rows: dict[str, tuple[int, float]],
    *,
    latency: dict[str, float] | None = None,
    cost: dict[str, float] | None = None,
) -> dict:
    """A real ``routing_stats.json`` in the producer's own shape, saved AND returned.

    Returned because ``detect_gap`` takes the in-memory fold (it is called at the moment the fold is
    written, so it costs no second read); saved because ``route_refs`` reads it off disk.
    """
    fold: dict = {"version": stats.STATS_VERSION, "use_cases": {UC: {QC: {}}}}
    for ref, (n, success_rate) in rows.items():
        fold["use_cases"][UC][QC][ref] = {
            "n": n,
            "success_rate": success_rate,
            "feedback": 0.0,
            "feedback_n": 0,
            "avg_ms": (latency or {}).get(ref, 100.0),
            "avg_cost_usd": (cost or {}).get(ref, 0.001),
            "score": stats._score(success_rate, 0.0, 0),
            "updated_at": "2026-08-24T00:00:00Z",
        }
    stats.save_stats(home, fold)
    return fold


def _decisive(home: Path, **kw) -> dict:
    """A fold that says, well past every floor, that the local ref wins."""
    return _fold(home, {CLOUDY: (20, 0.40), LOCAL: (20, 0.95)}, **kw)


def _table(home: Path, mode: str, **extra) -> None:
    """Set the use case's entry in the TABLE, whose mode key is the plain ``"mode"``.

    ``MODE_KEY`` (``routing_mode``) is the *settings-store* key ``mode_for`` consults first; the
    table's fallback is ``"mode"``. Writing the wrong one leaves every mode reading ``off``, which
    passes a "nothing was proposed" test for entirely the wrong reason — so the fixture asserts what
    it set.
    """
    pol = policy.load_policy(home)
    entry: dict = {"mode": mode}
    entry.update(extra)
    pol.setdefault("use_cases", {})[UC] = entry
    policy.save_policy(home, pol)
    assert policy.mode_for(UC, home=home) == mode, "the fixture did not set the mode it meant to"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home with the routing MASTER switch on and the redirect asserted.

    ``master_enabled()`` gates every mode on ``routing.enabled``, whose default AND failure mode are
    both ``False``: a fixture that only wrote the per-use-case mode would measure the master switch
    rather than the detector. ``test_the_master_switch_is_what_the_fixture_overrides`` pins that
    premise, and ``test_the_detector_is_gated_on_the_master_switch`` proves the gate is live.
    """
    from personalclaw.config.loader import config_dir

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert Path(config_dir()).resolve() == tmp_path.resolve(), "PERSONALCLAW_HOME did not bind"
    monkeypatch.setattr(policy, "master_enabled", lambda: True)
    monkeypatch.setattr(policy, "_default_home", lambda: tmp_path)
    monkeypatch.setattr(proposals, "_default_home", lambda: tmp_path)
    policy.save_policy(tmp_path, policy._empty_policy())
    return tmp_path


def test_the_master_switch_is_what_the_fixture_overrides() -> None:
    from personalclaw.config.loader import RoutingConfig

    assert RoutingConfig().enabled is False


def _policy_bytes(home: Path) -> bytes:
    return (home / "routing_policy.json").read_bytes()


# ── the wire: the fold write is the trigger point ───────────────────────────────


class TestTheWire:
    """Would deleting the caller be caught? These are the tests that answer yes."""

    def test_the_fold_write_enqueues_a_proposal(self, home: Path) -> None:
        """The whole atom's missing half: a real attempt fold produces a real proposal."""
        _table(home, "heuristic")
        _fold(home, {CLOUDY: (20, 0.40), LOCAL: (19, 0.95)})
        assert proposals.pending(home=home) == []

        stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")

        props = proposals.pending(home=home)
        assert len(props) == 1, "the fold write did not reach the gap detector"
        assert props[0].current == REFS
        assert props[0].proposed == [LOCAL, CLOUDY]

    def test_a_cell_awaiting_a_decision_does_not_repropose(self, home: Path) -> None:
        """Every subsequent call re-derives the same finding; the queue must not grow per call."""
        _table(home, "heuristic")
        _fold(home, {CLOUDY: (20, 0.40), LOCAL: (19, 0.95)})
        for i in range(3):
            stats.record_routing_stats(_rec(audit_id=f"aud-{i}"), home=home, now=f"2026-08-25T0{i}")
        assert len(proposals.pending(home=home)) == 1

    def test_an_unclassified_attempt_proposes_nothing(self, home: Path) -> None:
        """A row with no ``query_class`` is not attributable to a cell — the fold skips it, and so
        must the detector (it would otherwise propose against a bucket that does not exist)."""
        _table(home, "heuristic")
        _decisive(home)
        rec = _rec()
        rec.pop("query_class")
        stats.record_routing_stats(rec, home=home)
        assert proposals.pending(home=home) == []

    def test_a_detector_failure_never_breaks_the_fold(
        self, home: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """The fold write is best-effort observability that must not break a model call — and a
        detector failure must be attributable to the DETECTOR, not read as a lost fold."""

        def boom(*a, **kw):
            raise RuntimeError("detector exploded")

        monkeypatch.setattr(gap, "detect_gap", boom)
        _table(home, "heuristic")
        _decisive(home)
        with caplog.at_level("WARNING"):
            stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")
        assert "routing proposal check failed" in caplog.text
        assert "routing stats fold failed" not in caplog.text
        folded = stats.load_stats(home)["use_cases"][UC][QC]
        assert folded[LOCAL]["n"] == 21, "the fold itself must still have landed"


# ── propose-don't-write, end to end ─────────────────────────────────────────────


class TestProposeDoesNotWriteTheTable:
    def test_the_fold_write_leaves_routing_policy_byte_identical(self, home: Path) -> None:
        """The atom's central negative, asserted over the whole path rather than over ``propose``.

        A proposal is enqueued in the same call, so this is not the trivially-true "nothing
        happened": the vacuity floor below drives the write that these same bytes DO see.
        """
        _table(home, "heuristic")
        _decisive(home)
        before = _policy_bytes(home)

        stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")

        assert proposals.pending(home=home), "nothing was proposed — this would pass vacuously"
        assert _policy_bytes(home) == before

    def test_the_byte_harness_can_see_the_write_that_accepting_makes(self, home: Path) -> None:
        """The floor for the test above, and the ``accept`` half of the clause in one drive:
        the table updates, carries the ``proposal_id`` basis, and the bytes move."""
        _table(home, "heuristic")
        _decisive(home)
        before = _policy_bytes(home)
        stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")
        prop = proposals.pending(home=home)[0]

        assert proposals.accept(prop.id, home=home) is True

        assert _policy_bytes(home) != before
        assert policy.table_order(UC, QC, home=home) == [LOCAL, CLOUDY]
        assert policy.order_basis(UC, QC, home=home)["proposal_id"] == prop.id

    def test_rejecting_writes_no_table_and_suppresses_the_repropose(self, home: Path) -> None:
        """Reject means "the table was right": no write, and the finding is silenced for the
        cooldown even though the fold still says the same thing on the next call."""
        _table(home, "heuristic")
        _decisive(home)
        stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")
        prop = proposals.pending(home=home)[0]
        before = _policy_bytes(home)

        assert proposals.reject(prop.id, home=home) is True
        assert _policy_bytes(home) == before
        assert proposals.pending(home=home) == []

        stats.record_routing_stats(_rec(audit_id="aud-2"), home=home, now="2026-08-25T01:00:00Z")
        assert proposals.pending(home=home) == [], "the cooldown did not suppress the re-propose"


# ── the floors: n >= min_samples, and the hysteresis band ───────────────────────


class TestTheConfidenceFloor:
    """``n >= min_samples`` is ``learned._opinion``'s floor, reached through the proposal path."""

    def test_exactly_min_samples_is_enough_to_propose(self, home: Path) -> None:
        _table(home, "heuristic")
        fold = _fold(home, {CLOUDY: (5, 0.40), LOCAL: (5, 0.95)})
        assert gap.detect_gap(fold, UC, QC, home=home) is not None

    def test_one_below_min_samples_proposes_nothing(self, home: Path) -> None:
        """The decisive 0.40-vs-0.95 fold is held fixed and only ``n`` moves 5 → 4, so the
        boundary is the single variable — a ``>= 4`` floor would fail this and pass the test
        above."""
        _table(home, "heuristic")
        fold = _fold(home, {CLOUDY: (4, 0.40), LOCAL: (5, 0.95)})
        assert gap.detect_gap(fold, UC, QC, home=home) is None

    def test_the_floor_read_is_the_configs(self, home: Path, monkeypatch) -> None:
        """The floor is not a literal in this module: raise ``min_samples`` and the same fold that
        proposed at n=5 stops proposing."""
        _table(home, "heuristic")
        monkeypatch.setattr(
            policy,
            "_routing_knobs",
            lambda: {"hysteresis": 0.05, "cloud_quality_margin": 0.10, "min_samples": 6},
        )
        fold = _fold(home, {CLOUDY: (5, 0.40), LOCAL: (5, 0.95)})
        assert gap.detect_gap(fold, UC, QC, home=home) is None


class TestTheQualityGap:
    def test_a_within_band_difference_is_not_a_gap(self, home: Path) -> None:
        """Cost is the only thing allowed to reorder near-equals (§5.2), so a within-hysteresis
        difference must not nag the user about their table."""
        _table(home, "heuristic")
        fold = _fold(home, {CLOUDY: (20, 0.94), LOCAL: (20, 0.96)})
        assert gap.detect_gap(fold, UC, QC, home=home) is None

    def test_a_gap_wider_than_the_band_is_one(self, home: Path) -> None:
        """The floor for the test above: same refs, same n, only the spread widens."""
        _table(home, "heuristic")
        fold = _fold(home, {CLOUDY: (20, 0.40), LOCAL: (20, 0.96)})
        assert gap.detect_gap(fold, UC, QC, home=home) is not None


# ── what "a gap" means: the finding is not what routing does ────────────────────


class TestOnlyWhenTheFindingIsNotInEffect:
    def test_no_proposal_when_the_learned_stage_is_already_live(self, home: Path) -> None:
        """Under ``learned`` with no recorded order the stage reorders on every call, so there is
        nothing to decide — the machine does not ask permission for what it already does."""
        _table(home, "learned")
        fold = _decisive(home)
        assert policy.route_refs(UC, QC, REFS, home=home) == [LOCAL, CLOUDY]  # premise
        assert gap.detect_gap(fold, UC, QC, home=home) is None

    def test_the_learned_stage_is_idempotent(self, home: Path) -> None:
        """Load-bearing for the test above: ``current`` is ``route_refs``' answer and ``proposed``
        is the learned stage applied on top of it, so a stage that permuted its own output would
        propose against itself forever."""
        _table(home, "learned")
        _decisive(home)
        keys = policy._local_provider_keys()
        once = policy._learned_order(REFS, UC, QC, keys, home=home)
        assert policy._learned_order(once, UC, QC, keys, home=home) == once

    def test_a_recorded_order_the_evidence_outgrew_is_a_gap(self, home: Path) -> None:
        """A recorded order wins over the learned stage (lever 3 short-circuits lever 4), so once
        the table has an order a proposal is the ONLY route to changing it."""
        _table(home, "learned")
        policy.set_order(
            UC, QC, REFS, home=home, basis={"source": "proposal", "proposal_id": "rp-older"}
        )
        fold = _decisive(home)
        assert policy.route_refs(UC, QC, REFS, home=home) == REFS  # premise: the table wins

        prop = gap.detect_gap(fold, UC, QC, home=home)
        assert prop is not None
        assert prop.current == REFS and prop.proposed == [LOCAL, CLOUDY]

    def test_a_hand_set_order_is_refused_rather_than_overwritten(self, home: Path) -> None:
        """A user-set order is theirs. The proposal is still enqueued (the finding is real), but
        accepting it refuses and says why, instead of the machine overwriting the decision."""
        _table(home, "learned")
        policy.set_order(UC, QC, REFS, home=home, basis={"source": "user"})
        fold = _decisive(home)
        prop = gap.detect_gap(fold, UC, QC, home=home)
        assert prop is not None
        before = _policy_bytes(home)

        assert proposals.accept(prop.id, home=home) is False
        assert _policy_bytes(home) == before
        assert proposals.find(prop.id, home=home).status == "refused"


class TestTheGatesThatSilenceIt:
    def test_off_mode_proposes_nothing(self, home: Path) -> None:
        _table(home, "off")
        assert gap.detect_gap(_decisive(home), UC, QC, home=home) is None

    def test_the_detector_is_gated_on_the_master_switch(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _table(home, "heuristic")
        fold = _decisive(home)
        monkeypatch.setattr(policy, "master_enabled", lambda: False)
        assert gap.detect_gap(fold, UC, QC, home=home) is None

    def test_a_pinned_use_case_proposes_nothing(self, home: Path) -> None:
        """A pin short-circuits ordering entirely, so a recorded order under one would be dead."""
        _table(home, "heuristic", pin="cloud")
        assert policy.pin_for(UC, home=home) == "cloud"  # premise
        assert gap.detect_gap(_decisive(home), UC, QC, home=home) is None

    @pytest.mark.parametrize(
        "fold",
        [
            None,
            {},
            {"use_cases": "not-a-dict"},
            {"use_cases": {UC: {QC: {CLOUDY: "not-a-row"}}}},
            {"use_cases": {UC: {QC: {CLOUDY: {"n": "seven"}, LOCAL: {"n": None}}}}},
            {"use_cases": {UC: {QC: {CLOUDY: {"n": 20, "success_rate": 0.4}}}}},
        ],
    )
    def test_a_corrupt_or_thin_fold_proposes_nothing_and_does_not_raise(
        self, home: Path, fold
    ) -> None:
        _table(home, "heuristic")
        assert gap.detect_gap(fold, UC, QC, home=home) is None


# ── the evidence (§6.3): reviewable without re-running anything ─────────────────


class TestEvidence:
    def _audit(self, home: Path, rows: list[dict]) -> None:
        (home / "model_calls.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    def _proposal(self, home: Path):
        _table(home, "heuristic")
        fold = _decisive(
            home, latency={CLOUDY: 900.0, LOCAL: 120.0}, cost={CLOUDY: 0.004, LOCAL: 0.0}
        )
        self._audit(
            home,
            [
                {**_rec(provider="cloudy", model="big", audit_id="aud-c"), "latency_ms": 900.0},
                {**_rec(audit_id="aud-l"), "latency_ms": 120.0},
                # A different cell, and a different ref — neither may be sampled.
                {**_rec(audit_id="aud-other"), "query_class": "summarize"},
                {**_rec(provider="third", model="party", audit_id="aud-third")},
            ],
        )
        prop = gap.detect_gap(fold, UC, QC, home=home)
        assert prop is not None
        return prop

    def test_the_payload_carries_every_axis_the_clause_names(self, home: Path) -> None:
        ev = self._proposal(home).evidence
        assert ev["n"] == {CLOUDY: 20, LOCAL: 20}
        assert ev["scores"] == {CLOUDY: 0.4, LOCAL: 0.95}
        assert (ev["min_samples"], ev["hysteresis"], ev["cloud_quality_margin"]) == (5, 0.05, 0.10)

    def test_the_deltas_are_promoted_minus_demoted(self, home: Path) -> None:
        """Negative is better on both axes: the promoted ref is faster and cheaper here. p50 comes
        from the JSONL tail (the fold keeps only an EMA), cost from the fold."""
        ev = self._proposal(home).evidence
        assert ev["p50_delta_ms"] == 120.0 - 900.0
        assert ev["cost_delta_usd"] == 0.0 - 0.004

    def test_sample_audit_ids_correlate_to_this_cell_only(self, home: Path) -> None:
        """§6.4's correlation handle: a reviewer pastes one into the audit reader. Ids from another
        query_class or another ref would send them to a call the proposal was not built from."""
        ev = self._proposal(home).evidence
        assert ev["sample_audit_ids"] == ["aud-l", "aud-c"]  # newest first

    def test_a_missing_audit_tail_thins_the_evidence_but_still_proposes(self, home: Path) -> None:
        """The tail is a forensic convenience; the fold is the durable record. No tail must not
        cost the user the proposal."""
        _table(home, "heuristic")
        fold = _decisive(home)
        assert not (home / "model_calls.jsonl").exists()
        prop = gap.detect_gap(fold, UC, QC, home=home)
        assert prop is not None
        assert prop.evidence["sample_audit_ids"] == []
        assert prop.evidence["p50_delta_ms"] == 0.0


# ── SC #8: deleting the state files degrades, and touches no database ───────────


class TestDegradation:
    def _dbs(self, home: Path) -> list[str]:
        return sorted(str(p.relative_to(home)) for p in home.rglob("*.db"))

    def test_deleting_both_files_degrades_and_writes_no_database(self, home: Path) -> None:
        """The clause in one drive: with the fold and the table gone, routing keeps the bound order,
        the detector proposes nothing, and the whole path writes no ``memory.db``/``knowledge.db``.
        """
        _table(home, "learned")
        _decisive(home)
        assert policy.route_refs(UC, QC, REFS, home=home) == [LOCAL, CLOUDY]  # premise: deciding

        (home / "routing_stats.json").unlink()
        (home / "routing_policy.json").unlink()

        assert policy.route_refs(UC, QC, REFS, home=home) == REFS
        assert policy.mode_for(UC, home=home) == "off"
        assert gap.detect_gap(stats.load_stats(home), UC, QC, home=home) is None
        stats.record_routing_stats(_rec(), home=home, now="2026-08-25T00:00:00Z")
        assert proposals.pending(home=home) == []
        assert self._dbs(home) == []

    def test_the_database_check_can_see_a_database(self, home: Path) -> None:
        """The floor for the negative above. A recursive ``*.db`` glob over the home is only
        evidence if it would find one — so this creates a real sqlite file at the inventoried
        ``memory.db`` path and proves the same predicate flips."""
        assert self._dbs(home) == []
        sqlite3.connect(home / "memory.db").close()
        assert self._dbs(home) == ["memory.db"]
