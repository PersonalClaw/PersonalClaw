"""The flywheel observability panel, end to end (LEARN-R14b / WF2LEA-9 parts 3 + 5).

Four metrics, each traced from its live WRITER to the endpoint that renders it:

| Metric | Writer | Reader |
|---|---|---|
| budget utilization | `context._record_ambient_measurements` → `allocation_samples` | composite |
| per-op cost (R19e) | `StagingStore.record_flush(cadence, cost_usd)` | `cost_by_op` |
| judge MAE (R10d) | `controller` → ledger `judge_verdict.evidence.samples` | `judge.mae` |
| attribution (R16) | `attribution.grade_accepted_changes` → resolved records | `attribution` |
| ablation delta | `context._record_ambient_measurements` → `ablation_sweeps` | `ablation` |

The unmeasured-vs-zero tests are the load-bearing ones. Every metric here is absent on a
fresh install, and a panel that renders absence as 0 would tell a new user their
flywheel is broken — so absence has to survive all the way to the wire as null.
"""

from __future__ import annotations

import pytest

from personalclaw.learning import measure
from personalclaw.learning.staging import FlushOutcome, StagingStore
from personalclaw.learning.surfacing import ABLATABLE, Candidate, ablation_deltas
from personalclaw.workflows import judge_calibration as jc


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)


@pytest.fixture
def store(tmp_path):
    s = StagingStore(tmp_path)
    yield s
    s.close()


# ── Budget utilization: the writer that did not exist ──


def test_utilization_is_unmeasured_before_any_render(store):
    """None, not 0.0. `ambient.report()` logged this and persisted nothing."""
    assert store.utilization() == {"samples": 0, "mean": None}


def test_utilization_is_the_mean_of_recorded_renders(store):
    store.record_allocation(used_tokens=2000, budget_tokens=4000)
    store.record_allocation(used_tokens=3000, budget_tokens=4000)
    got = store.utilization()
    assert got == {"samples": 2, "mean": 0.625}


def test_a_zero_budget_is_not_a_zero_percent_sample(store):
    """An allocator that was switched off is an absent measurement, not a starving one."""
    assert store.record_allocation(used_tokens=0, budget_tokens=0) is False
    assert store.utilization()["samples"] == 0


def test_allocation_samples_are_a_rolling_window(store):
    for i in range(store.ALLOCATION_KEEP + 25):
        store.record_allocation(used_tokens=i, budget_tokens=4000)
    assert store.utilization()["samples"] <= store.ALLOCATION_KEEP


# ── Per-op cost (R19e) ──


def test_cost_by_op_splits_by_the_cadence_the_writer_records(store):
    ok = FlushOutcome.FLUSH_OK
    store.record_flush(cadence="session_end", outcome=ok, cost_usd=0.02)
    store.record_flush(cadence="session_end", outcome=ok, cost_usd=0.03)
    store.record_flush(cadence="run_end", outcome=ok, cost_usd=0.01)
    rows = store.cost_by_op()
    assert rows[0] == {"op": "session_end", "passes": 2, "cost_usd": 0.05}
    assert rows[1] == {"op": "run_end", "passes": 1, "cost_usd": 0.01}


def test_cost_by_op_is_empty_not_zero_when_nothing_ran(store):
    assert store.cost_by_op() == []


# ── The composite ──


def test_the_composite_excludes_unmeasured_components_rather_than_scoring_them():
    got = measure.health_composite(
        precision=None,
        capture_passes=0,
        capture_errors=0,
        utilization=None,
        judge_false_pass_rate=None,
    )
    assert got["score"] is None
    assert got["measured"] == 0 and got["of"] == 4
    assert all(c["score"] is None for c in got["components"])
    assert all("unmeasured" in c["detail"] for c in got["components"])


def test_the_composite_reweights_around_a_missing_component():
    """One measured component at 100% must read 100, not 40 (its weight)."""
    got = measure.health_composite(
        precision=1.0,
        capture_passes=0,
        capture_errors=0,
        utilization=None,
        judge_false_pass_rate=None,
    )
    assert got["score"] == 100.0
    assert got["measured"] == 1


@pytest.mark.parametrize(
    "utilization,expected",
    [
        (0.50, 100.0),  # the band's floor is ideal, not marginal
        (0.65, 100.0),
        (0.80, 100.0),  # …and so is its ceiling
        (0.25, 50.0),  # halfway to the floor
        (0.90, 50.0),  # halfway past the ceiling
        (0.0, 0.0),
        (1.0, 0.0),  # a fully-consumed budget crowds out on every turn
    ],
)
def test_the_ideal_utilization_band_is_50_to_80_percent(utilization, expected):
    got = measure.health_composite(
        precision=None,
        capture_passes=0,
        capture_errors=0,
        utilization=utilization,
        judge_false_pass_rate=None,
    )
    band = next(c for c in got["components"] if c["name"] == "utilization")
    assert band["score"] == expected
    assert got["ideal_band"] == [0.50, 0.80]


def test_the_composite_weights_sum_to_one():
    """Otherwise "of 100" is a lie in whichever direction the weights drift."""
    assert sum(measure.HEALTH_WEIGHTS.values()) == pytest.approx(1.0)


# ── Judge MAE (R10d) ──


def _verdict(node: str, verdict: str, samples: list[str], run: str = "r1"):
    return jc.VerdictRecord(
        run_id=run, node_id=node, template="t", verdict=verdict, samples=samples
    )


def test_predicted_confidence_comes_from_the_samples_the_writer_records():
    """`overall` is never journaled — an MAE built on it would average parse defaults."""
    rec = _verdict("g", "pass", ["pass", "pass", "fail"])
    assert rec.agreement == pytest.approx(2 / 3)
    unanimous = _verdict("g", "pass", ["pass", "pass", "pass"])
    assert unanimous.agreement == 1.0


def test_a_verdict_with_no_samples_has_no_confidence():
    """None, not 1.0 — a verdict with no recorded panel has nothing to report."""
    assert _verdict("g", "pass", []).agreement is None
    got = jc.mae_buckets([_verdict("g", "pass", [])], [])
    assert got["no_confidence"] == 1
    assert all(row["mae"] is None for row in got["buckets"])


def test_an_unlabelled_verdict_never_counts_as_correct():
    """Silence is not agreement. Otherwise MAE improves as the user stops looking."""
    got = jc.mae_buckets([_verdict("g", "pass", ["pass", "pass", "pass"])], [])
    assert got["labelled"] == 0 and got["unlabelled"] == 1
    top = next(row for row in got["buckets"] if row["bucket"] == "0.75-1.00")
    assert top["n"] == 1 and top["mae"] is None


def test_a_human_override_labels_the_bucket_it_lands_in():
    verdicts = [_verdict("g", "pass", ["pass", "pass", "pass"])]
    divergences = [
        jc.DivergenceRecord(
            run_id="r1", node_id="g", template="t", judge_verdict="pass", human_verdict="fail"
        )
    ]
    got = jc.mae_buckets(verdicts, divergences)
    top = next(row for row in got["buckets"] if row["bucket"] == "0.75-1.00")
    # Predicted P(pass)=1.0, actual 0.0 — a confidently wrong judge, error 1.0.
    assert top["mae"] == 1.0
    assert got["labelled"] == 1


def test_a_confident_reject_lands_at_the_low_end_of_one_axis():
    """Confidence is expressed as P(pass), so a sure REJECT is near 0, not near 1."""
    got = jc.mae_buckets([_verdict("g", "fail", ["fail", "fail", "fail"])], [])
    low = next(row for row in got["buckets"] if row["bucket"] == "0.00-0.25")
    assert low["n"] == 1


def test_a_label_from_one_run_does_not_label_another_run():
    verdicts = [_verdict("g", "pass", ["pass"], run="r1")]
    divergences = [
        jc.DivergenceRecord(
            run_id="r2", node_id="g", template="t", judge_verdict="pass", human_verdict="fail"
        )
    ]
    assert jc.mae_buckets(verdicts, divergences)["labelled"] == 0


def test_samples_are_parsed_from_where_the_controller_writes_them():
    """The ledger row nests them under `evidence`, not at the top level."""
    parsed = jc.verdicts_from_journal(
        [
            {
                "kind": "judge_verdict",
                "node_id": "g",
                "verdict": "pass",
                "evidence": {"samples": ["pass", "fail"], "sample_count": 2},
            }
        ]
    )
    assert parsed[0].samples == ["pass", "fail"]


# ── Ablation-delta sweep (§2.5) ──


def _pool() -> dict[str, list[Candidate]]:
    """Enough candidates per source that the diversification cap can bite."""
    return {
        "skills": [
            Candidate(
                kind="skill", key=f"s{i}", score=0.9 - i * 0.05, l0=f"skill {i} deploy", l1=""
            )
            for i in range(5)
        ],
        "memory": [
            Candidate(kind="memory", key=f"m{i}", score=0.8, l0=f"memory {i} deploy", l1="")
            for i in range(4)
        ],
    }


def test_the_sweep_reports_every_heuristic():
    rows = ablation_deltas(_pool(), query="deploy the thing", budget_tokens=200)
    assert {r["heuristic"] for r in rows} == set(ABLATABLE)
    assert all(0.0 <= r["delta"] <= 1.0 for r in rows)
    # Weakest first, so the removal candidates are what a reader sees at the top.
    deltas = [r["delta"] for r in rows]
    assert deltas == sorted(deltas)


def test_a_heuristic_that_changes_nothing_reports_no_effect():
    """The null result is the point — it is the evidence for deleting the heuristic."""
    rows = ablation_deltas(_pool(), query="deploy", budget_tokens=200)
    for row in rows:
        expected = "no_effect" if row["delta"] <= 0.02 else "earns_its_place"
        assert row["verdict"] == expected


def test_diversification_measurably_changes_what_is_injected():
    """A rail against a vacuous sweep: at least one heuristic must earn its place."""
    rows = ablation_deltas(_pool(), query="deploy the thing", budget_tokens=400)
    assert any(row["verdict"] == "earns_its_place" for row in rows), rows


def test_an_unknown_ablation_raises_rather_than_ablating_nothing():
    """A typo'd name would report delta 0.0 — the same reading as a useless heuristic."""
    with pytest.raises(ValueError, match="unknown ablation"):
        from personalclaw.learning.surfacing import allocate

        allocate(_pool(), query="x", budget_tokens=100, ablate="intnet")


def test_the_live_path_is_unchanged_by_the_parameters_existence():
    from personalclaw.learning.surfacing import allocate

    plain = allocate(_pool(), query="deploy", budget_tokens=400)
    explicit = allocate(_pool(), query="deploy", budget_tokens=400, ablate="")
    assert plain.included == explicit.included


def test_a_sweep_round_trips_through_the_store(store):
    rows = ablation_deltas(_pool(), query="deploy the thing", budget_tokens=400)
    assert store.record_ablation(rows) == len(ABLATABLE)
    latest = store.latest_ablation()
    assert {r["heuristic"] for r in latest["rows"]} == set(ABLATABLE)
    assert latest["at"]


def test_no_sweep_yet_reads_as_empty_not_as_all_zero(store):
    assert store.latest_ablation() == {}


def test_the_sweep_cadence_gates_itself(store):
    import time

    now = time.time()
    assert store.ablation_due(now=now) is True, "never run — due"
    store.record_ablation(
        [{"heuristic": "intent", "delta": 0.5, "verdict": "earns_its_place"}], now=now
    )
    assert store.ablation_due(now=now) is False, "just ran — not due"
    # …and due again a day later. The cadence is what makes the sweep affordable at all.
    assert store.ablation_due(now=now + 86401) is True
