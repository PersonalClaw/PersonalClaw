"""Tests for the pure tick decision core (P6): loop/tick.py evaluate() + collapse().

Every branch is exercised with a hand-built (cfg, state, now) — no store, no I/O."""

from __future__ import annotations

from personalclaw.loop.tick import (
    Action,
    Decision,
    StepConfig,
    TickConfig,
    TickState,
    collapse,
    evaluate,
    step_config_from_phase,
    tick_config_from_plan,
    tick_state_from_snapshot,
    validate_step_phase,
)


def _cfg(*steps: StepConfig, max_cycles: int = 0, rollback_cap: int = 3) -> TickConfig:
    return TickConfig(steps=tuple(steps), max_cycles=max_cycles, rollback_cap=rollback_cap)


def _state(**kw) -> TickState:
    base = dict(step_index=0, step_started_at=0.0)
    base.update(kw)
    return TickState(**base)


# ── budget / completion ─────────────────────────────────────────────────────


def test_budget_exhausted_completes():
    d = evaluate(_cfg(StepConfig(), max_cycles=5), _state(total_cycles=5), now=100.0)
    assert d.action is Action.COMPLETE and "budget" in d.reason


def test_forever_never_budget_completes():
    # max_cycles=0 (forever) → budget never triggers even at high cycle counts
    d = evaluate(_cfg(StepConfig(), max_cycles=0), _state(total_cycles=9999), now=100.0)
    assert d.action is not Action.COMPLETE or "budget" not in d.reason


def test_past_last_step_completes():
    d = evaluate(_cfg(StepConfig(), StepConfig()), _state(step_index=2), now=100.0)
    assert d.action is Action.COMPLETE and "all steps complete" in d.reason


# ── worker-in-flight ──────────────────────────────────────────────────────────


def test_worker_in_flight_waits():
    d = evaluate(_cfg(StepConfig()), _state(worker_in_flight=True), now=100.0)
    assert d.action is Action.WAITING


# ── dwell / bake ───────────────────────────────────────────────────────────────


def test_bake_period_holds_until_elapsed():
    cfg = _cfg(StepConfig(min_dwell_secs=60.0))
    holding = evaluate(cfg, _state(step_started_at=0.0, gate_passed=True), now=30.0)
    assert holding.action is Action.HOLD and "bake" in holding.reason


def test_bake_elapsed_allows_advance():
    cfg = _cfg(StepConfig(min_dwell_secs=60.0), StepConfig())
    d = evaluate(cfg, _state(step_started_at=0.0, gate_passed=True), now=61.0)
    assert d.action in (Action.ADVANCE, Action.COMPLETE)


# ── min_findings ───────────────────────────────────────────────────────────────


def test_min_findings_keeps_executing():
    cfg = _cfg(StepConfig(min_findings=3))
    d = evaluate(cfg, _state(findings_in_step=1, gate_passed=True), now=100.0)
    assert d.action is Action.EXECUTE and "evidence" in d.reason


# ── gate / advance ──────────────────────────────────────────────────────────────


def test_gate_passed_advances():
    cfg = _cfg(StepConfig(), StepConfig())
    d = evaluate(cfg, _state(step_index=0, gate_passed=True), now=100.0)
    assert d.action is Action.ADVANCE and d.step_index == 1


def test_gate_passed_on_last_step_completes():
    cfg = _cfg(StepConfig(), StepConfig())
    d = evaluate(cfg, _state(step_index=1, gate_passed=True), now=100.0)
    assert d.action is Action.COMPLETE and d.step_index == 2


def test_gate_not_passed_executes():
    d = evaluate(_cfg(StepConfig(), StepConfig()), _state(gate_passed=False), now=100.0)
    assert d.action is Action.EXECUTE


# ── metric gate ────────────────────────────────────────────────────────────────


def test_metric_pass_gates_advance():
    cfg = _cfg(StepConfig(metric_pass=4.0, metric_hold=2.0), StepConfig())
    # gate structurally passed but metric below pass → not an advance
    hold = evaluate(cfg, _state(gate_passed=True, metric=3.0), now=100.0)
    assert hold.action is Action.HOLD and "marginal" in hold.reason
    # metric clears pass → advance
    adv = evaluate(cfg, _state(gate_passed=True, metric=4.5), now=100.0)
    assert adv.action is Action.ADVANCE


def test_metric_below_hold_without_prior_floor_executes():
    # No prior floor → can't rollback; a low metric just keeps executing.
    cfg = _cfg(StepConfig(metric_pass=4.0, metric_hold=2.0))
    d = evaluate(cfg, _state(gate_passed=False, metric=1.0, prior_step_floor=None), now=100.0)
    assert d.action is Action.EXECUTE


# ── rollback ───────────────────────────────────────────────────────────────────


def test_metric_regression_rolls_back():
    cfg = _cfg(StepConfig(metric_pass=4.0), StepConfig(metric_pass=4.0))
    d = evaluate(cfg, _state(step_index=1, metric=1.0, prior_step_floor=3.0), now=100.0)
    assert d.action is Action.ROLLBACK and d.step_index == 0
    assert "regressed" in d.reason


def test_rollback_cap_blocks():
    cfg = _cfg(StepConfig(metric_pass=4.0), StepConfig(metric_pass=4.0), rollback_cap=2)
    d = evaluate(
        cfg, _state(step_index=1, metric=1.0, prior_step_floor=3.0, rollbacks_on_step=2), now=100.0
    )
    assert d.action is Action.COMPLETE and "cap" in d.reason


def test_no_rollback_when_metric_at_or_above_floor():
    cfg = _cfg(StepConfig(metric_pass=4.0), StepConfig(metric_pass=4.0))
    d = evaluate(
        cfg, _state(step_index=1, metric=3.5, prior_step_floor=3.0, gate_passed=False), now=100.0
    )
    assert d.action is not Action.ROLLBACK


# ── default / no-steps ───────────────────────────────────────────────────────────


def test_no_steps_executes():
    # A point-in-time loop (no steps) just executes; completion comes from an external signal.
    d = evaluate(_cfg(), _state(), now=100.0)
    assert d.action is Action.EXECUTE


# ── zero-wait collapse ─────────────────────────────────────────────────────────


def test_collapse_folds_instant_steps():
    # 3 instant, already-gated steps → collapse straight to COMPLETE in one call.
    cfg = _cfg(StepConfig(), StepConfig(), StepConfig())
    d = collapse(cfg, _state(step_index=0, gate_passed=True), now=100.0)
    assert d.action is Action.COMPLETE


def test_collapse_stops_at_a_dwell_step():
    # step 0 instant+gated → advance to step 1 which has a dwell → collapse stops (HOLD).
    cfg = _cfg(StepConfig(), StepConfig(min_dwell_secs=60.0), StepConfig())
    d = collapse(cfg, _state(step_index=0, gate_passed=True, step_started_at=100.0), now=100.0)
    assert d.action in (Action.ADVANCE, Action.HOLD)
    # it must NOT have collapsed past the dwell step to COMPLETE
    assert d.action is not Action.COMPLETE


# ── Decision serialization ───────────────────────────────────────────────────────


def test_decision_to_dict_lean_and_metric():
    assert "metric" not in Decision(Action.EXECUTE, 0).to_dict()
    d = Decision(Action.ADVANCE, 1, "why", metric=3.14159).to_dict()
    assert d["action"] == "advance" and d["step_index"] == 1 and d["metric"] == 3.142


# ── pure adapters: phase dict → StepConfig / TickConfig ────────────────────────


def test_step_config_from_bare_phase_is_neutral():
    # A phase with no P6 keys → today's no-dwell / no-metric behavior.
    sc = step_config_from_phase({"title": "Understand", "objective": "x"})
    assert sc.min_dwell_secs == 0.0 and sc.min_findings == 0
    assert sc.metric_pass is None and sc.metric_hold is None


def test_step_config_from_phase_parses_keys():
    sc = step_config_from_phase(
        {"min_dwell_secs": 30, "min_findings": "2", "metric_pass": 4.0, "metric_hold": 2.0}
    )
    assert sc.min_dwell_secs == 30.0 and sc.min_findings == 2
    assert sc.metric_pass == 4.0 and sc.metric_hold == 2.0


def test_step_config_ignores_garbage():
    sc = step_config_from_phase(
        {"min_dwell_secs": "not-a-number", "min_findings": "oops", "metric_pass": ""}
    )
    assert sc.min_dwell_secs == 0.0 and sc.min_findings == 0 and sc.metric_pass is None


def test_tick_config_from_plan_builds_steps():
    cfg = tick_config_from_plan([{"title": "a"}, {"min_dwell_secs": 10}], max_cycles=20)
    assert len(cfg.steps) == 2 and cfg.max_cycles == 20
    assert cfg.steps[1].min_dwell_secs == 10.0


# ── phase validation ────────────────────────────────────────────────────────────


def test_validate_step_phase_ok():
    assert (
        validate_step_phase({"title": "a", "min_dwell_secs": 5, "metric_pass": 4, "metric_hold": 2})
        == []
    )
    assert validate_step_phase({"title": "bare"}) == []


def test_validate_step_phase_catches_bad_dwell_and_inverted_band():
    assert any("min_dwell_secs" in e for e in validate_step_phase({"min_dwell_secs": "x"}))
    inv = validate_step_phase({"metric_pass": 2.0, "metric_hold": 4.0})
    assert any("metric_hold" in e and "≤" in e for e in inv)  # hold must be ≤ pass


def test_goal_validate_config_surfaces_bad_phase(monkeypatch, tmp_path):
    # The malformed phase must surface through the kind's validate_config (intake-time).
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    cfg = {
        "kind_config": {
            "goal_type": "open_ended",
            "granularity": "balanced",
            "execution_plan": [{"title": "bad", "metric_pass": 1.0, "metric_hold": 5.0}],
        }
    }
    errors, _warnings = kinds.get("goal").validate_config(cfg)
    assert any("metric_hold" in e for e in errors)


# ── snapshot builder + end-to-end pipeline ────────────────────────────────────


def test_snapshot_derives_findings_in_step():
    st = tick_state_from_snapshot(
        step_index=1, step_started_at=10.0, findings_total=7, findings_at_step_start=5
    )
    assert st.findings_in_step == 2 and st.step_index == 1
    # never negative even if counts look inconsistent
    st2 = tick_state_from_snapshot(
        step_index=0, step_started_at=0.0, findings_total=3, findings_at_step_start=9
    )
    assert st2.findings_in_step == 0


def test_end_to_end_snapshot_to_decision():
    # Realistic adapter pipeline: plan → cfg, raw reads → state, evaluate → Decision.
    cfg = tick_config_from_plan(
        [{"title": "gather", "min_findings": 2}, {"title": "finalize"}], max_cycles=30
    )
    # On step 0 with only 1 finding since it began → keep executing (evidence not met).
    executing = evaluate(
        cfg,
        tick_state_from_snapshot(
            step_index=0,
            step_started_at=0.0,
            findings_total=1,
            findings_at_step_start=0,
            gate_passed=True,
        ),
        now=100.0,
    )
    assert executing.action is Action.EXECUTE and "evidence" in executing.reason
    # Enough findings + gate passed → advance to step 1.
    adv = evaluate(
        cfg,
        tick_state_from_snapshot(
            step_index=0,
            step_started_at=0.0,
            findings_total=3,
            findings_at_step_start=0,
            gate_passed=True,
        ),
        now=100.0,
    )
    assert adv.action is Action.ADVANCE and adv.step_index == 1


def test_sdlc_producer_emits_step_keys():
    """P6 producer: the SDLC classifier attaches sensible tick step-keys per stage kind,
    a planner override survives, and every emitted phase passes intake validation."""
    from personalclaw.loop.code_classify import _normalize_plan

    raw = [
        {"stage": "implementation", "title": "Build", "objective": "x"},
        {"stage": "verification", "title": "Verify", "objective": "y"},
        {
            "stage": "review",
            "title": "Review",
            "objective": "z",
            "metric_pass": 4.9,
            "metric_hold": 3.0,
        },
    ]
    plan = _normalize_plan(raw, set(), set())
    by = {p["title"]: step_config_from_phase(p) for p in plan}
    # universal floor: never advance a stage on zero evidence
    assert all(sc.min_findings >= 1 for sc in by.values())
    # implementation is not metric-gated; verification is; review honors the planner override
    assert by["Build"].metric_pass is None
    assert by["Verify"].metric_pass == 3.5 and by["Verify"].metric_hold == 2.0
    assert by["Review"].metric_pass == 4.9 and by["Review"].metric_hold == 3.0
    # every produced phase is valid at intake (no inverted band, etc.)
    assert [e for p in plan for e in validate_step_phase(p)] == []


def test_end_to_end_dwell_then_advance():
    cfg = tick_config_from_plan([{"title": "bake", "min_dwell_secs": 60}, {"title": "next"}])
    hold = evaluate(
        cfg,
        tick_state_from_snapshot(
            step_index=0,
            step_started_at=0.0,
            findings_total=1,
            findings_at_step_start=0,
            gate_passed=True,
        ),
        now=30.0,
    )
    assert hold.action is Action.HOLD
    adv = evaluate(
        cfg,
        tick_state_from_snapshot(
            step_index=0,
            step_started_at=0.0,
            findings_total=1,
            findings_at_step_start=0,
            gate_passed=True,
        ),
        now=61.0,
    )
    assert adv.action is Action.ADVANCE
