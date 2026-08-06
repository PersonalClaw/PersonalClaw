"""EVALUATION-SUBSTRATE ES-1b — the child-process experiment-matrix RUNNER.

These tests exercise ``run_matrix`` by monkeypatching the child-spawn boundary
(``subprocess.run``) — no real LLM call, no real child process. The load-bearing
assertions are the §1.3 isolation contract (the parent ``os.environ`` is never
mutated; the child env carries the workspace and the parent's does not) and the
three-state outcome mapping (passed/failed vs the THREE ``VERIFIER_ABSENT`` paths:
timeout, non-zero exit / garbage stdout, and budget-exceeded).
"""

from __future__ import annotations

import os
import subprocess
import types

import pytest

from personalclaw.evals import runner as runner_mod
from personalclaw.evals import store
from personalclaw.evals.child import (
    error_result,
    parse_descriptor,
    render_result_line,
    result_from_scenario,
    wrap_factory_for_model,
)
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    VERIFIER_ABSENT,
    MatrixSpec,
    aggregate,
)
from personalclaw.evals.runner import run_matrix


@pytest.fixture()
def eval_home(tmp_path, monkeypatch):
    """Point config_dir() (which the store roots on) at an isolated home — nothing
    touches the real ``~/.personalclaw``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


class _FakeRun:
    """A stand-in for ``subprocess.run`` that records the ``env=`` it was handed and
    returns a canned CompletedProcess (or raises), per-call configurable."""

    def __init__(self, *, behavior):
        self.calls: list[dict] = []
        self._behavior = behavior  # callable(index) -> ("ok"|"fail"|"garbage"|"timeout", payload)

    def __call__(self, args, *, env, timeout, capture_output, text):
        idx = len(self.calls)
        self.calls.append({"args": args, "env": dict(env), "timeout": timeout})
        kind, payload = self._behavior(idx)
        if kind == "timeout":
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
        if kind == "garbage":
            return types.SimpleNamespace(returncode=0, stdout="no sentinel here\n", stderr="")
        if kind == "fail":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        # ok
        return types.SimpleNamespace(returncode=0, stdout=render_result_line(payload), stderr="")


def _patch_spawn(monkeypatch, behavior):
    fake = _FakeRun(behavior=behavior)
    monkeypatch.setattr(runner_mod.subprocess, "run", fake)
    return fake


# ── §1.3 — the parent env is never mutated ────────────────────────────────────


def test_parent_env_never_mutated_child_env_carries_workspace(eval_home, monkeypatch):
    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    before = dict(os.environ)
    fake = _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))

    run_matrix(MatrixSpec(subject="smoke_test", axes={}, trial_count=1), matrix_id="m-env")

    # The parent env is byte-identical — no PERSONALCLAW_WORKSPACE added or changed.
    assert dict(os.environ) == before
    assert "PERSONALCLAW_WORKSPACE" not in os.environ
    # The CHILD env (the copy handed to subprocess.run) DID carry the workspace.
    assert len(fake.calls) == 1
    child_env = fake.calls[0]["env"]
    assert "PERSONALCLAW_WORKSPACE" in child_env
    assert child_env["PERSONALCLAW_WORKSPACE"]  # a real temp path
    # And it is NOT the parent's value (parent had none).
    assert "PERSONALCLAW_WORKSPACE" not in before


# ── three-state outcome mapping ────────────────────────────────────────────────


def test_passing_child_maps_to_passed(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 0.9}))
    result = run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-pass")
    assert [c.outcome for c in result.cells] == [PASSED]
    assert result.cells[0].score == pytest.approx(0.9)


def test_failing_child_maps_to_failed(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": False, "score": 0.1}))
    result = run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-fail")
    assert [c.outcome for c in result.cells] == [FAILED]
    assert result.cells[0].score == pytest.approx(0.1)


def test_timeout_maps_to_verifier_absent(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("timeout", None))
    result = run_matrix(
        MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-timeout", timeout_secs=5.0
    )
    assert [c.outcome for c in result.cells] == [VERIFIER_ABSENT]
    assert result.cells[0].score is None


def test_nonzero_exit_maps_to_verifier_absent(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("fail", None))
    result = run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-nz")
    assert [c.outcome for c in result.cells] == [VERIFIER_ABSENT]


def test_garbage_stdout_maps_to_verifier_absent(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("garbage", None))
    result = run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-garbage")
    assert [c.outcome for c in result.cells] == [VERIFIER_ABSENT]


def test_verifier_absent_is_never_averaged_as_zero(eval_home, monkeypatch):
    """A mixed matrix: one PASSED (0.8), one FAILED (0.2), one VERIFIER_ABSENT
    (a non-zero exit). The mean is over the two SCORED cells (0.5), never /3."""

    def behavior(i):
        if i == 0:
            return "ok", {"ok": True, "passed": True, "score": 0.8}
        if i == 1:
            return "ok", {"ok": True, "passed": False, "score": 0.2}
        return "fail", None

    _patch_spawn(monkeypatch, behavior)
    result = run_matrix(
        MatrixSpec(subject="s", axes={"arm": ["a", "b", "c"]}, trial_count=1),
        matrix_id="m-mixed",
    )
    agg = result.aggregates
    assert agg["mean_score"] == pytest.approx(0.5)
    assert agg["scored_count"] == 2
    assert agg["counts"][VERIFIER_ABSENT] == 1


# ── budget preflight — EXCEEDED skips the spawn ────────────────────────────────


def test_budget_exceeded_makes_cell_absent_without_spawn(eval_home, monkeypatch):
    from personalclaw.guardrails.budgets import BudgetVerdict

    class _FakeMeter:
        def check_day(self, budget):
            return BudgetVerdict.EXCEEDED, "over the cap"

    monkeypatch.setattr("personalclaw.guardrails.budgets.get_meter", lambda: _FakeMeter())
    fake = _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))

    result = run_matrix(
        MatrixSpec(subject="s", axes={}, trial_count=1, budget_usd=1.0), matrix_id="m-budget"
    )
    assert [c.outcome for c in result.cells] == [VERIFIER_ABSENT]
    # No child was spawned for the budget-blocked cell.
    assert fake.calls == []


def test_no_budget_proceeds_and_spawns(eval_home, monkeypatch):
    """budget_usd=0.0 (unlimited) never consults the meter and always spawns."""
    fake = _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))
    result = run_matrix(
        MatrixSpec(subject="s", axes={}, trial_count=1, budget_usd=0.0), matrix_id="m-nobudget"
    )
    assert [c.outcome for c in result.cells] == [PASSED]
    assert len(fake.calls) == 1


# ── sequential + cartesian expansion ───────────────────────────────────────────


def test_cartesian_product_times_trials_spawns_every_cell(eval_home, monkeypatch):
    """A 2×2 axes spec with trial_count=1 spawns exactly 4 cells."""
    fake = _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))
    spec = MatrixSpec(subject="s", axes={"model": ["A:x", "B:y"], "k": [1, 3]}, trial_count=1)
    result = run_matrix(spec, matrix_id="m-grid")
    assert len(fake.calls) == 4
    assert len(result.cells) == 4
    # Each of the 4 coordinate combinations appears once.
    coord_pairs = {(c.coords["model"], c.coords["k"]) for c in result.cells}
    assert coord_pairs == {("A:x", 1), ("A:x", 3), ("B:y", 1), ("B:y", 3)}


def test_trial_count_multiplies_cells(eval_home, monkeypatch):
    fake = _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))
    spec = MatrixSpec(subject="s", axes={"arm": ["a", "b"]}, trial_count=3)
    run_matrix(spec, matrix_id="m-trials")
    assert len(fake.calls) == 6  # 2 arms × 3 trials


# ── artifact retention ─────────────────────────────────────────────────────────


def test_artifacts_are_retained_and_roundtrip(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 0.7}))
    spec = MatrixSpec(subject="wf-x", axes={"model": ["A:x"]}, scorer="assertion", trial_count=1)
    run_matrix(spec, matrix_id="m-artifacts")

    mdir = store.matrix_dir("m-artifacts")
    assert (mdir / "experiment.json").exists()
    assert (mdir / "aggregates.json").exists()
    assert (mdir / "trials.json").exists()

    # experiment.json round-trips back to the spec.
    assert MatrixSpec.from_dict(store.read_matrix_experiment("m-artifacts")) == spec
    # aggregates round-trip.
    agg = store.read_matrix_aggregates("m-artifacts")
    assert agg["scored_count"] == 1 and agg["counts"][PASSED] == 1
    # trials.json carries one per-cell row with its artifact ref.
    trials = store.read_matrix_trials("m-artifacts")
    assert len(trials) == 1
    assert trials[0]["outcome"] == PASSED
    assert trials[0]["artifact_ref"]
    # a results.tsv row was appended.
    rows = store.read_results()
    assert len(rows) == 1
    assert rows[0]["study_id"] == "m-artifacts"
    assert rows[0]["kind"] == "matrix"
    assert rows[0]["verdict"] == "pass"


def test_per_cell_descriptor_and_result_written(eval_home, monkeypatch):
    _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))
    run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-celldir")
    cell_dir = store.matrix_dir("m-celldir") / "cell-0000"
    assert (cell_dir / "descriptor.json").exists()
    assert (cell_dir / "result.json").exists()


# ── runner never raises out on an infra failure ────────────────────────────────


def test_matrix_never_raises_on_cell_spawn_error(eval_home, monkeypatch):
    """Even a non-timeout spawn fault (e.g. OSError) is one VERIFIER_ABSENT cell,
    never an exception that aborts the whole matrix — the never-raise-out contract."""

    def boom(args, *, env, timeout, capture_output, text):
        raise OSError("cannot spawn")

    monkeypatch.setattr(runner_mod.subprocess, "run", boom)
    result = run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-oserr")
    assert [c.outcome for c in result.cells] == [VERIFIER_ABSENT]


# ── child pure helpers (no process/LLM) ────────────────────────────────────────


def test_parse_descriptor_rejects_non_object():
    with pytest.raises(ValueError):
        parse_descriptor("[1, 2, 3]")
    assert parse_descriptor('{"subject": "s"}')["subject"] == "s"


def test_result_from_scenario_scores_by_assertion_rate():
    scenario_result = types.SimpleNamespace(
        total_assertions=4, passed_assertions=3, passed=False, name="s", elapsed_secs=1.2
    )
    out = result_from_scenario(scenario_result)
    assert out["ok"] is True
    assert out["passed"] is False
    assert out["score"] == pytest.approx(0.75)


def test_result_from_scenario_no_assertions_uses_passed_flag():
    passing = types.SimpleNamespace(total_assertions=0, passed_assertions=0, passed=True, name="s")
    assert result_from_scenario(passing)["score"] == 1.0
    failing = types.SimpleNamespace(total_assertions=0, passed_assertions=0, passed=False, name="s")
    assert result_from_scenario(failing)["score"] == 0.0


def test_error_result_is_not_ok():
    assert error_result("kaboom")["ok"] is False
    assert "kaboom" in error_result("kaboom")["error"]


def test_render_result_line_is_sentinel_prefixed():
    line = render_result_line({"ok": True, "score": 1.0})
    assert line.startswith(runner_mod.CELL_RESULT_SENTINEL)
    # and the runner parses its own render back out.
    assert runner_mod._parse_child_stdout("noise\n" + line + "\nmore")["ok"] is True


def test_wrap_factory_binds_model_override():
    seen = {}

    def base(session_key, **kwargs):
        seen.update(kwargs)
        return "provider"

    wrapped = wrap_factory_for_model(base, "Prov:model-x")
    assert wrapped("k") == "provider"
    assert seen["model_override"] == "Prov:model-x"
    # No model → base factory returned unwrapped.
    assert wrap_factory_for_model(base, None) is base


def test_aggregate_matches_runner_result(eval_home, monkeypatch):
    """Sanity: the aggregates the runner persists equal aggregate() over its cells."""
    _patch_spawn(monkeypatch, lambda i: ("ok", {"ok": True, "passed": bool(i % 2), "score": 0.5}))
    result = run_matrix(
        MatrixSpec(subject="s", axes={"arm": ["a", "b"]}, trial_count=1), matrix_id="m-agg"
    )
    assert result.aggregates == aggregate(result.cells)
