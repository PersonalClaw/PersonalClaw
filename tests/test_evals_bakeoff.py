"""Tests for the model bake-off (EVALUATION-SUBSTRATE §7, ES-10)."""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.evals import bakeoff as bo
from personalclaw.evals import store
from personalclaw.evals.matrix import FAILED, PASSED, VERIFIER_ABSENT, CellResult

# ── helpers ──────────────────────────────────────────────────────────────────


class StubCaller:
    """A counting caller. ``reply_for(model, prompt)`` decides each output + cost, so a
    test can make the reply depend on the candidate model (does the axis reach it?)."""

    def __init__(self, reply_for):
        self.calls: list[dict] = []
        self._reply_for = reply_for

    async def __call__(self, prompt: str, *, model: str, use_case: str) -> bo.BakeoffCall:
        self.calls.append({"prompt": prompt, "model": model, "use_case": use_case})
        text, cost = self._reply_for(model, prompt)
        return bo.BakeoffCall(text=text, elapsed_secs=0.01, cost_usd=cost, model=model)


class _CaptureOn:
    bakeoff_capture_enabled = True


class _CaptureOff:
    bakeoff_capture_enabled = False


def _inp(i: int, *, assertions=("ok",)) -> bo.SampledInput:
    return bo.SampledInput(id=f"reasoning-{i:04d}", prompt=f"prompt {i}", assertions=assertions)


@pytest.fixture()
def bench_home(tmp_path, monkeypatch):
    """An isolated home the bake-off can PIN + persist against — never the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "Acme"}]}), encoding="utf-8"
    )
    (tmp_path / "active_models.json").write_text(
        json.dumps({"reasoning": ["Acme:big"], "background": ["Acme:small"]}), encoding="utf-8"
    )
    return tmp_path


# ── the recommendation verdict (the user-facing output) ───────────────────────


def test_recommend_names_a_better_model_only_when_it_clears_the_margin():
    # Model B lands every assertion; A lands none. B wins by well over the margin.
    cells = [
        CellResult(coords={"model": "Acme:A", "input": "i0"}, outcome=FAILED, score=0.0),
        CellResult(coords={"model": "Acme:A", "input": "i1"}, outcome=FAILED, score=0.0),
        CellResult(coords={"model": "Acme:B", "input": "i0"}, outcome=PASSED, score=1.0),
        CellResult(coords={"model": "Acme:B", "input": "i1"}, outcome=PASSED, score=1.0),
    ]
    rec = bo.recommend("reasoning", cells, current_model="Acme:A")
    assert rec.verdict == bo.REC_RECOMMENDED
    assert rec.recommended_model == "Acme:B"
    assert rec.current_model == "Acme:A"


def test_recommend_holds_when_the_win_is_within_noise():
    # A 0.02 edge is below the 0.05 margin — telling the user to re-pin on that is noise.
    cells = [
        CellResult(coords={"model": "Acme:A", "input": "i0"}, outcome=PASSED, score=0.80),
        CellResult(coords={"model": "Acme:B", "input": "i0"}, outcome=PASSED, score=0.82),
    ]
    rec = bo.recommend("reasoning", cells, current_model="Acme:A")
    assert rec.verdict == bo.REC_HOLD
    assert rec.recommended_model == "Acme:A"


def test_recommend_is_insufficient_when_nothing_scored():
    cells = [
        CellResult(coords={"model": "Acme:A", "input": "i0"}, outcome=VERIFIER_ABSENT),
        CellResult(coords={"model": "Acme:B", "input": "i0"}, outcome=VERIFIER_ABSENT),
    ]
    rec = bo.recommend("reasoning", cells, current_model="Acme:A")
    assert rec.verdict == bo.REC_INSUFFICIENT
    assert rec.recommended_model == ""


# ── the run: the model × input product is crossed and every artifact persists ──


def test_run_crosses_model_by_input_and_persists_every_artifact(bench_home):
    models = ["Acme:A", "Acme:B"]
    inputs = [_inp(0), _inp(1), _inp(2)]
    # B always contains "ok" (score 1.0), A never does (score 0.0).
    stub = StubCaller(lambda model, prompt: (("ok yes" if model == "Acme:B" else "no"), 0.001))
    result = asyncio.run(
        bo.run_bakeoff(
            "reasoning",
            models=models,
            inputs=inputs,
            caller=stub,
            current_model="Acme:A",
            bench_id="bakeoff-test",
        )
    )
    # 2 models × 3 inputs × 1 trial = 6 cells, and the caller was hit once per cell.
    assert len(result.cells) == 6
    assert len(stub.calls) == 6
    assert result.recommendation.verdict == bo.REC_RECOMMENDED
    assert result.recommendation.recommended_model == "Acme:B"

    d = store.matrix_dir("bakeoff-test")
    for name in ("experiment.json", "aggregates.json", "trials.json", "recommendation.json"):
        assert (d / name).exists(), f"missing {name}"
    md = (d / "recommendation.md").read_text(encoding="utf-8")
    assert "Acme:B" in md and "active_models.json" in md
    # The run appended a ledger row.
    assert store.results_path().exists()
    assert "bakeoff" in store.results_path().read_text(encoding="utf-8")


def test_budget_cap_stops_spending_midway(bench_home):
    # Each call costs 0.03; a 0.05 budget affords ~2 cells, the rest are absent-not-spawned.
    stub = StubCaller(lambda model, prompt: ("ok", 0.03))
    result = asyncio.run(
        bo.run_bakeoff(
            "reasoning",
            models=["Acme:A"],
            inputs=[_inp(0), _inp(1), _inp(2), _inp(3)],
            caller=stub,
            budget_usd=0.05,
            bench_id="bakeoff-budget",
        )
    )
    absent = [c for c in result.cells if c.outcome == VERIFIER_ABSENT]
    assert absent, "budget cap should have marked later cells VERIFIER_ABSENT"
    # The caller was NOT invoked for the capped cells.
    assert len(stub.calls) == len(result.cells) - len(absent)


def test_a_provider_fault_is_an_absent_sample_not_a_crash(bench_home):
    def boom(model, prompt):
        raise RuntimeError("provider down")

    stub = StubCaller(boom)
    result = asyncio.run(
        bo.run_bakeoff(
            "reasoning",
            models=["Acme:A"],
            inputs=[_inp(0), _inp(1)],
            caller=stub,
            bench_id="bakeoff-fault",
        )
    )
    assert all(c.outcome == VERIFIER_ABSENT for c in result.cells)
    assert result.recommendation.verdict == bo.REC_INSUFFICIENT


def test_run_refuses_without_models_or_inputs(bench_home):
    with pytest.raises(ValueError):
        asyncio.run(bo.run_bakeoff("reasoning", models=[], inputs=[_inp(0)]))
    with pytest.raises(ValueError):
        asyncio.run(bo.run_bakeoff("reasoning", models=["Acme:A"], inputs=[]))


# ── the scorer ─────────────────────────────────────────────────────────────────


def test_assertion_scorer_is_none_without_assertions_and_a_fraction_with_them():
    assert bo.assertion_scorer(bo.SampledInput(id="x", prompt="p"), "anything") is None
    inp = bo.SampledInput(id="x", prompt="p", assertions=("alpha", "beta"))
    assert bo.assertion_scorer(inp, "alpha only") == 0.5
    assert bo.assertion_scorer(inp, "alpha and beta") == 1.0


def test_inputs_without_a_verifier_score_nothing(bench_home):
    # No assertions and the default scorer ⇒ every cell VERIFIER_ABSENT, not a false 1.0.
    stub = StubCaller(lambda model, prompt: ("whatever", 0.0))
    result = asyncio.run(
        bo.run_bakeoff(
            "reasoning",
            models=["Acme:A"],
            inputs=[bo.SampledInput(id="i0", prompt="p", assertions=())],
            caller=stub,
            bench_id="bakeoff-noverifier",
        )
    )
    assert all(c.outcome == VERIFIER_ABSENT for c in result.cells)


# ── capture: off by default, redacted, capped, expiring ──────────────────────


def test_capture_is_off_by_default_and_writes_nothing(bench_home):
    assert bo.capture_input("reasoning", "some real prompt") is False
    assert bo.load_captured_inputs("reasoning") == []


def test_capture_redacts_before_writing(bench_home, monkeypatch):
    seen = {}

    def fake_redact(text: str) -> str:
        seen["input"] = text
        return "REDACTED"

    monkeypatch.setattr("personalclaw.security.redact", fake_redact)
    assert bo.capture_input("reasoning", "secret token abc", config=_CaptureOn()) is True
    loaded = bo.load_captured_inputs("reasoning")
    assert len(loaded) == 1
    assert loaded[0].prompt == "REDACTED"
    assert seen["input"] == "secret token abc"


def test_capture_keeps_a_rolling_window(bench_home):
    monkey_cap = 3
    import personalclaw.evals.bakeoff as mod

    original = mod.CAPTURE_MAX_PER_USE_CASE
    mod.CAPTURE_MAX_PER_USE_CASE = monkey_cap
    try:
        for i in range(5):
            bo.capture_input("reasoning", f"prompt {i}", config=_CaptureOn())
        loaded = bo.load_captured_inputs("reasoning")
        assert len(loaded) == monkey_cap
        # The oldest two were dropped; the newest three remain in order.
        assert [i.prompt for i in loaded] == ["prompt 2", "prompt 3", "prompt 4"]
    finally:
        mod.CAPTURE_MAX_PER_USE_CASE = original


def test_captured_inputs_past_the_ttl_are_dropped_on_read(bench_home):
    bo.capture_input("reasoning", "fresh", config=_CaptureOn())
    # Read as if far in the future — the record is now past its TTL.
    future = __import__("time").time() + bo.CAPTURE_TTL_SECS + 1.0
    assert bo.load_captured_inputs("reasoning", now=future) == []


def test_capture_file_is_owner_only(bench_home):
    bo.capture_input("reasoning", "p", config=_CaptureOn())
    path = bo._capture_path("reasoning")
    assert path.exists()
    assert (path.stat().st_mode & 0o077) == 0, "capture file must be 0600 (owner-only)"


def test_capture_reads_the_live_config_flag_when_none_is_injected(bench_home, monkeypatch):
    # The production path (config=None) must read AppConfig.load().evals — prove the whole
    # wiring, not just the injected-config shortcut. Redact is stubbed to avoid its cost.
    monkeypatch.setattr("personalclaw.security.redact", lambda t: t)
    # Default config.json (from the fixture) has no evals section ⇒ flag defaults OFF.
    assert bo.capture_input("reasoning", "p") is False
    # Turn the real flag on in the on-disk config and the production path captures.
    (bench_home / "config.json").write_text(
        json.dumps({"evals": {"bakeoff_capture_enabled": True}}), encoding="utf-8"
    )
    assert bo.capture_input("reasoning", "p") is True
    assert len(bo.load_captured_inputs("reasoning")) == 1


# ── the audit sampler (path A: metadata, no bodies) ──────────────────────────


def test_observed_models_reads_distinct_refs_from_the_audit(bench_home):
    from personalclaw.config.loader import config_dir

    rows = [
        {"ts": 1.0, "use_case": "reasoning", "provider": "Acme", "model": "big"},
        {"ts": 2.0, "use_case": "reasoning", "provider": "Acme", "model": "big"},
        {"ts": 3.0, "use_case": "reasoning", "provider": "Other", "model": "small"},
        {"ts": 4.0, "use_case": "background", "provider": "Acme", "model": "tiny"},
    ]
    (config_dir() / "model_calls.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    refs = bo.observed_models("reasoning")
    # Distinct, most-recent first, scoped to the use case — background's tiny is excluded.
    assert refs == ["Other:small", "Acme:big"]


# ── the capture directory never leaves the machine ──────────────────────────


def test_bakeoff_captures_are_excluded_from_export_and_snapshot():
    from personalclaw.durability.inventory import all_entries

    evals = next(e for e in all_entries() if e.id == "evals")
    assert "benchmarks/bakeoff" in evals.derived_within
