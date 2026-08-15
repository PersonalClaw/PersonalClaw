"""EVALUATION-SUBSTRATE ES-1a — the pure eval substrate: store + matrix types.

Covers the store path helpers (rooted under an isolated home), the append-only
``results.tsv`` ledger, per-matrix JSON round-trips, the MatrixSpec round-trip, and
the three-state aggregation whose whole point is that ``verifier_absent`` is counted
separately and NEVER averaged into the mean as a zero.
"""

from __future__ import annotations

import pytest

from personalclaw.evals import store
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    VERIFIER_ABSENT,
    CellResult,
    MatrixResult,
    MatrixSpec,
    aggregate,
)
from personalclaw.evals.pinning import RunPin


@pytest.fixture()
def eval_home(tmp_path, monkeypatch):
    """Point config_dir() (which store helpers root on) at an isolated home.

    config_dir() re-reads PERSONALCLAW_HOME live on every call, so setting the env
    is enough — no store-internal patching, and nothing touches the real home.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ── MatrixSpec round-trip ─────────────────────────────────────────────────────


def test_matrix_spec_roundtrips_through_dict():
    spec = MatrixSpec(
        subject="wf-triage",
        axes={"model": ["A:x", "B:y"], "iterations": [1, 3]},
        trial_count=4,
        scorer="judge",
        budget_usd=2.5,
    )
    restored = MatrixSpec.from_dict(spec.to_dict())
    assert restored == spec
    # axes is JSON-safe (a dict of lists) and independent of the source mapping.
    d = spec.to_dict()
    d["axes"]["model"].append("mutated")
    assert spec.axes["model"] == ["A:x", "B:y"], "to_dict must copy axes, not alias"


def test_matrix_result_roundtrips():
    spec = MatrixSpec(subject="s", axes={"arm": ["a"]})
    cells = [
        CellResult(coords={"arm": "a"}, outcome=PASSED, score=0.8, artifact_ref="r1"),
        CellResult(coords={"arm": "a"}, outcome=VERIFIER_ABSENT, score=None),
    ]
    result = MatrixResult(spec=spec, cells=cells, aggregates=aggregate(cells))
    restored = MatrixResult.from_dict(result.to_dict())
    assert restored.spec == spec
    assert [c.outcome for c in restored.cells] == [PASSED, VERIFIER_ABSENT]
    assert restored.cells[1].score is None


# ── three-state aggregation ───────────────────────────────────────────────────


def test_aggregate_excludes_verifier_absent_from_the_mean():
    """The load-bearing rule: passed=0.8 / failed=0.2 / verifier_absent=None →
    the mean is over the TWO real scores (0.5), and verifier_absent is a separate
    count, never a zero dragged into the average."""
    cells = [
        CellResult(coords={}, outcome=PASSED, score=0.8),
        CellResult(coords={}, outcome=FAILED, score=0.2),
        CellResult(coords={}, outcome=VERIFIER_ABSENT, score=None),
    ]
    agg = aggregate(cells)
    assert agg["mean_score"] == pytest.approx(0.5)  # (0.8 + 0.2) / 2, NOT / 3
    assert agg["scored_count"] == 2
    assert agg["counts"][VERIFIER_ABSENT] == 1
    assert agg["counts"][PASSED] == 1 and agg["counts"][FAILED] == 1
    assert agg["total"] == 3


def test_aggregate_all_verifier_absent_has_no_mean():
    cells = [CellResult(coords={}, outcome=VERIFIER_ABSENT, score=None) for _ in range(3)]
    agg = aggregate(cells)
    assert agg["mean_score"] is None
    assert agg["scored_count"] == 0
    assert agg["counts"][VERIFIER_ABSENT] == 3


def test_aggregate_empty():
    agg = aggregate([])
    assert agg["mean_score"] is None
    assert agg["total"] == 0
    assert agg["counts"] == {PASSED: 0, FAILED: 0, VERIFIER_ABSENT: 0}


# ── store path helpers ─────────────────────────────────────────────────────────


def test_path_helpers_resolve_under_the_isolated_home(eval_home):
    assert store.evals_root() == eval_home / "evals"
    assert store.matrices_dir() == eval_home / "evals" / "matrices"
    assert store.matrix_dir("m-1") == eval_home / "evals" / "matrices" / "m-1"
    assert store.matrix_dir("m-1").is_dir()
    assert store.results_path() == eval_home / "evals" / "results.tsv"


# ── the append-only results ledger ─────────────────────────────────────────────


def _pin(**over):
    """A complete RunPin — every ledger write needs one (ES-2)."""
    parts = {
        "scenario_id": "s",
        "scenario_sha256": "a" * 64,
        "model_fingerprint": {"chat": "Acme:m1"},
        "prompt_pack_sha256": "b" * 64,
        "config_snapshot_ref": "c" * 64,
    }
    parts.update(over)
    return RunPin(**parts)


def test_append_result_is_append_only(eval_home):
    store.append_result(
        {"study_id": "st-1", "kind": "template_ab", "verdict": "pass", "score_new": "0.8"},
        pin=_pin(),
    )
    store.append_result(
        {"study_id": "st-2", "kind": "template_ab", "verdict": "fail", "score_new": "0.2"},
        pin=_pin(),
    )
    text = store.results_path().read_text(encoding="utf-8")
    lines = text.splitlines()
    # header + two data rows — the second append did NOT rewrite the first.
    assert lines[0].split("\t") == list(store.RESULTS_COLUMNS)
    assert len(lines) == 3
    rows = store.read_results()
    assert [r["study_id"] for r in rows] == ["st-1", "st-2"]
    assert rows[0]["verdict"] == "pass" and rows[1]["verdict"] == "fail"


def test_append_result_neutralizes_tabs_and_newlines(eval_home):
    store.append_result({"study_id": "st\t1\nbad", "kind": "k"}, pin=_pin())
    rows = store.read_results()
    assert rows[0]["study_id"] == "st 1 bad"
    assert rows[0]["kind"] == "k"


def test_append_result_refuses_a_row_without_a_pin(eval_home):
    """ES-2's ledger chokepoint: no pin ⇒ no row, and the file is never created."""
    with pytest.raises(store.PinRequiredError):
        store.append_result({"study_id": "st-1", "kind": "k"}, pin=None)
    assert not store.results_path().exists()


def test_append_result_refuses_an_incomplete_pin(eval_home):
    """Each of the four amendment-named parts is individually load-bearing."""
    for missing in (
        "scenario_sha256",
        "prompt_pack_sha256",
        "config_snapshot_ref",
    ):
        with pytest.raises(store.PinRequiredError) as excinfo:
            store.append_result({"study_id": "st-1"}, pin=_pin(**{missing: ""}))
        assert missing in str(excinfo.value)
    with pytest.raises(store.PinRequiredError):
        store.append_result({"study_id": "st-1"}, pin=_pin(model_fingerprint={}))
    assert not store.results_path().exists()


def test_pin_columns_come_from_the_pin_not_the_caller(eval_home):
    """A caller cannot label its score with someone else's scenario hash."""
    store.append_result(
        {"study_id": "st-1", "scenario_sha256": "f" * 64, "model_fp": "spoofed"},
        pin=_pin(),
    )
    row = store.read_results()[0]
    assert row["scenario_sha256"] == "a" * 64
    assert row["model_fp"] == _pin().model_fp() != "spoofed"


def test_read_results_empty_when_absent(eval_home):
    assert store.read_results() == []


# ── per-matrix JSON artifacts ──────────────────────────────────────────────────


def test_matrix_experiment_and_aggregates_roundtrip(eval_home):
    spec = MatrixSpec(subject="wf-x", axes={"model": ["A:x"]}, scorer="judge")
    store.write_matrix_experiment("m-9", spec.to_dict())
    assert MatrixSpec.from_dict(store.read_matrix_experiment("m-9")) == spec

    cells = [CellResult(coords={"model": "A:x"}, outcome=PASSED, score=0.9)]
    agg = aggregate(cells)
    store.write_matrix_aggregates("m-9", agg)
    assert store.read_matrix_aggregates("m-9") == agg

    # Absent reads are None, not an error.
    assert store.read_matrix_experiment("nope") is None
    assert store.read_matrix_aggregates("nope") is None
