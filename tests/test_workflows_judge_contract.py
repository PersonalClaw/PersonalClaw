"""Tests for the judge contract — maker/checker with teeth.

Every test here corresponds to a degenerate pass the contract makes impossible rather
than discourages. The distinction matters: prompt doctrine ("be skeptical") is advice,
and advice loses to a worker being scored on completion.
"""

import pytest

from personalclaw.workflows.judge_contract import (
    DEFAULT_FORBIDDEN_MODES,
    GRANULARITY_PRESETS,
    SCORE_MAX,
    FallbackCheck,
    FreedomLevel,
    Isolation,
    JudgeHints,
    JudgeVerdict,
    Ratchet,
    RubricCriterion,
    Verdict,
    aggregate_samples,
    compute_overall,
    detect_forbidden_modes,
    hints_from_dict,
    meets_ratchet,
    validate_verdict,
)

RUBRIC = [
    RubricCriterion("tests pass", 2),
    RubricCriterion("no stubs", 2),
    RubricCriterion("documented", 1),
]
FULL = {"tests pass": 2, "no stubs": 2, "documented": 1}


def hints(**kw) -> JudgeHints:
    kw.setdefault("rubric", list(RUBRIC))
    return JudgeHints(**kw)


def passing(**extra) -> dict:
    base = {"verdict": "PASS", "proof": "pytest: 42 passed", "scores": dict(FULL)}
    base.update(extra)
    return base


# ── the proof precondition ──


def test_a_pass_without_proof_is_invalid():
    """Not "discouraged" — rejected. A completion record without proof is a claim,
    and the point of a checker is to stop accepting claims."""
    verdict = validate_verdict({"verdict": "PASS", "scores": dict(FULL)}, hints())
    assert not verdict.passed
    assert "without cited proof" in verdict.invalid_reason


def test_evidence_refs_satisfy_the_proof_precondition():
    """A citation chain is proof; it need not be a command's stdout."""
    verdict = validate_verdict(
        {"verdict": "PASS", "scores": dict(FULL), "evidence_refs": ["node.audit.output"]}, hints()
    )
    assert verdict.passed


def test_a_pass_with_proof_and_full_scores_passes():
    assert validate_verdict(passing(), hints()).passed


def test_a_reject_needs_no_proof():
    """Only approval carries the burden — demanding proof to reject would make the
    judge's easiest move be approval."""
    verdict = validate_verdict({"verdict": "REJECT", "reasoning": "tests fail"}, hints())
    assert verdict.valid and not verdict.passed


# ── the ratchet ──


def test_a_single_shortfall_fails_under_strict():
    """No averaging. Averaging is how a broken deliverable passes on the strength of
    its documentation."""
    verdict = validate_verdict(passing(scores={**FULL, "tests pass": 0}), hints())
    assert not verdict.passed
    assert "below rubric targets" in verdict.invalid_reason
    assert "tests pass" in verdict.invalid_reason


def test_an_unscored_criterion_is_a_shortfall():
    """Silence is not a pass."""
    ok, shortfalls = meets_ratchet({"tests pass": 2}, hints())
    assert not ok
    assert any("not scored" in s for s in shortfalls)


def test_relaxed_reports_shortfalls_even_when_it_passes():
    """A relaxed pass is never silent about what it let through."""
    ok, shortfalls = meets_ratchet(
        {**FULL, "documented": 0}, hints(ratchet=Ratchet.RELAXED, marginal_threshold=1.0)
    )
    assert shortfalls  # recorded regardless of the verdict
    assert isinstance(ok, bool)


def test_scores_are_clamped_to_the_fixed_scale():
    verdict = validate_verdict(passing(scores={**FULL, "tests pass": 99}), hints())
    assert verdict.scores["tests pass"] == SCORE_MAX


def test_target_scores_are_clamped():
    assert RubricCriterion("x", target_score=99).clamp_target() == SCORE_MAX
    assert RubricCriterion("x", target_score=-5).clamp_target() == 0


# ── engine-computed derivable fields ──


def test_the_overall_is_computed_not_taken_from_the_model():
    """If the engine trusted a self-reported aggregate, a judge could score every
    dimension 0 and still report 5, and nothing downstream would notice."""
    verdict = validate_verdict(passing(overall=5.0), hints())
    assert verdict.overall == pytest.approx(compute_overall(FULL, RUBRIC))
    assert verdict.model_overall == 5.0  # kept as metadata so drift is visible
    assert verdict.overall != verdict.model_overall


def test_weights_shift_the_computed_overall():
    weighted = [RubricCriterion("a", 2, weight=3.0), RubricCriterion("b", 2, weight=1.0)]
    assert compute_overall({"a": 2, "b": 0}, weighted) > compute_overall({"a": 0, "b": 2}, weighted)


def test_an_empty_rubric_falls_back_to_a_plain_mean():
    assert compute_overall({"a": 2, "b": 0}, []) == pytest.approx(1.0)
    assert compute_overall({}, []) == 0.0


# ── the deterministic cross-check ──


def test_a_pass_contradicting_the_deterministic_check_escalates():
    """A judge that passes what `exit 1` failed is either wrong or being gamed, and
    both need a human."""
    verdict = validate_verdict(passing(), hints(), fallback_result=False)
    assert verdict.escalated and not verdict.passed
    assert "contradicts" in verdict.escalation_reason


def test_a_pass_agreeing_with_the_check_stands():
    assert validate_verdict(passing(), hints(), fallback_result=True).passed


def test_an_unavailable_check_does_not_block_a_pass():
    """None means "could not run" — a missing tool must never read as a real failure."""
    assert validate_verdict(passing(), hints(), fallback_result=None).passed


# ── forbidden success modes ──


def test_an_admitted_forbidden_mode_invalidates_a_pass():
    verdict = validate_verdict(
        passing(reasoning="The test was deleted but the implementation looks correct."), hints()
    )
    assert not verdict.passed
    assert "forbidden success mode" in verdict.invalid_reason


@pytest.mark.parametrize(
    "admission",
    [
        "The test was deleted but the code is fine.",
        "Tests were skipped for this run.",
        "The worker modified the gate config to make it pass.",
        "The validation config was changed during the run.",
        "Output is stubbed for now but the shape is right.",
        "The return value is hardcoded to satisfy the assertion.",
    ],
)
def test_every_default_mode_is_detected_on_realistic_phrasing(admission):
    """Measured: an all-words match made the whole denylist INERT on the phrasing a
    judge actually produces — present, plausible, and doing nothing."""
    assert detect_forbidden_modes(admission, JudgeHints())


@pytest.mark.parametrize(
    "innocent",
    [
        "All tests pass and the implementation is complete with real output.",
        "I added a test for the new branch and it passes.",
        "The config is unchanged; the fix was in the parser.",
        "Deleted a stale comment from the docstring.",
    ],
)
def test_innocent_prose_is_not_flagged(innocent):
    """A false positive here blocks legitimate completions, so the matcher needs two
    distinct signals rather than one."""
    assert not detect_forbidden_modes(innocent, JudgeHints())


def test_the_default_modes_each_carry_two_signal_words():
    """The matcher requires two; a one-signal phrase would never fire."""
    for mode in DEFAULT_FORBIDDEN_MODES:
        words = [w for w in mode.replace("/", " ").split() if len(w) > 2 and w != "or"]
        assert len(words) >= 2, mode


# ── the typed escape hatch ──


def test_cannot_judge_becomes_an_escalation_not_a_crash():
    """A parseable refusal. It is not a pass, and it is not an error either."""
    verdict = validate_verdict({"verdict": "PASS", "cannot_judge": "no test output"}, hints())
    assert verdict.verdict is Verdict.ESCALATE
    assert verdict.escalated and not verdict.passed


@pytest.mark.parametrize("garbage", [None, "not a dict", 42, [], {}, {"verdict": "MAYBE"}])
def test_garbage_becomes_a_reject_never_an_exception(garbage):
    """A judge returning malformed JSON has not approved anything, and crashing the
    run turns a judge outage into a lost iteration."""
    verdict = validate_verdict(garbage, hints())
    assert verdict.verdict is Verdict.REJECT
    assert verdict.invalid_reason


def test_the_verdict_enum_is_closed():
    with pytest.raises(ValueError):
        Verdict("APPROVED")


# ── sample aggregation ──


def test_a_majority_pass_carries_a_terminal_gate():
    """Single-run LLM-judge acceptance was measured to be indistinguishable from
    noise, so terminal gates take a median of independent samples."""
    samples = [
        validate_verdict(passing(), hints()),
        validate_verdict(passing(), hints()),
        validate_verdict({"verdict": "REJECT"}, hints()),
    ]
    assert aggregate_samples(samples, hints()).passed


def test_a_minority_pass_does_not():
    samples = [
        validate_verdict(passing(), hints()),
        validate_verdict({"verdict": "REJECT"}, hints()),
        validate_verdict({"verdict": "REJECT"}, hints()),
    ]
    assert not aggregate_samples(samples, hints()).passed


def test_any_forbidden_mode_hit_outweighs_a_passing_majority():
    """One sample spotting a disqualifier beats two that missed it — a disqualifier is
    a fact, not an opinion."""
    samples = [
        validate_verdict(passing(), hints()),
        validate_verdict(passing(), hints()),
        validate_verdict(passing(reasoning="the test was deleted"), hints()),
    ]
    assert not aggregate_samples(samples, hints()).passed


def test_any_escalation_wins():
    samples = [
        validate_verdict(passing(), hints()),
        validate_verdict(passing(), hints()),
        validate_verdict(passing(), hints(), fallback_result=False),
    ]
    result = aggregate_samples(samples, hints())
    assert result.escalated


def test_no_samples_is_a_reject():
    assert aggregate_samples([], hints()).verdict is Verdict.REJECT


def test_a_single_sample_passes_through():
    single = validate_verdict(passing(), hints())
    assert aggregate_samples([single], hints()) is single


def test_the_sample_count_is_forced_odd():
    """An even count has no median."""
    assert JudgeHints(judge_samples=4).sample_count() == 5
    assert JudgeHints(judge_samples=3).sample_count() == 3
    assert JudgeHints(judge_samples=0).sample_count() == 1


# ── hint parsing ──


def test_hints_parse_leniently_and_default_strict():
    """A template with a typo'd hint should run with defaults, not fail to start —
    and the defaults are the strict ones, so a malformed hint cannot LOOSEN the
    contract."""
    parsed = hints_from_dict({"ratchet": "nonsense", "judge_isolation": "whatever"})
    assert parsed.ratchet is Ratchet.STRICT
    assert parsed.judge_isolation is Isolation.FRESH


def test_absent_hints_yield_the_strict_defaults():
    for raw in (None, "", [], 42):
        parsed = hints_from_dict(raw)
        assert parsed.ratchet is Ratchet.STRICT
        assert parsed.fallback_check is FallbackCheck.ARTIFACT_EXISTS
        assert parsed.freedom_level is FreedomLevel.HIGH


def test_a_full_hint_block_round_trips():
    parsed = hints_from_dict(
        {
            "rubric": [{"criterion": "correct", "target_score": 2, "weight": 2.0}],
            "ratchet": "relaxed",
            "stop_condition": {"consecutive_clean": 3},
            "marginal_threshold": 1.0,
            "forbidden_success_modes": ["custom mode here"],
            "proof_command": "make test",
            "validator_script": "./validate.sh",
            "hidden_validation_commands": ["secret-check"],
            "ground_truth_sources": ["node.measure"],
            "judge_isolation": "cross_model",
            "judge_samples": 5,
            "fallback_check": "command_exit_code",
            "freedom_level": "low",
        }
    )
    assert parsed.rubric[0].weight == 2.0
    assert parsed.ratchet is Ratchet.RELAXED
    assert parsed.consecutive_clean == 3
    assert parsed.judge_isolation is Isolation.CROSS_MODEL
    assert parsed.fallback_check is FallbackCheck.COMMAND_EXIT_CODE
    assert parsed.freedom_level is FreedomLevel.LOW
    assert parsed.hidden_validation_commands == ["secret-check"]


def test_a_malformed_rubric_entry_is_dropped_not_fatal():
    parsed = hints_from_dict({"rubric": [{"no_criterion": 1}, {"criterion": "ok"}]})
    assert [c.criterion for c in parsed.rubric] == ["ok"]


def test_the_double_clean_rule_defaults_to_two():
    """One clean pass is a sample; two is a signal. This is what stops a loop exiting
    on the judge's first good mood."""
    assert JudgeHints().consecutive_clean == 2


def test_an_absent_stop_condition_keeps_the_double_clean_default():
    """An empty dict is "unspecified", which means the default applies — not "zero"."""
    assert JudgeHints(stop_condition={}).consecutive_clean == 2


def test_an_explicit_zero_is_floored_to_one():
    """A zero would let a loop exit having never passed a clean judge at all."""
    assert JudgeHints(stop_condition={"consecutive_clean": 0}).consecutive_clean == 1
    assert JudgeHints(stop_condition={"consecutive_clean": -5}).consecutive_clean == 1


def test_the_granularity_presets_match_the_real_dial():
    """Seeded from the existing UI's scale so the numbers mean what it already means."""
    assert GRANULARITY_PRESETS["balanced"]["marginal_threshold"] == 2.0
    assert GRANULARITY_PRESETS["quick"]["consecutive_clean"] == 1
    assert GRANULARITY_PRESETS["exhaustive"]["consecutive_clean"] == 3


# ── the record ──


def test_a_verdict_reports_validity_and_passing_separately():
    """ "Well-formed" and "approved" are different questions."""
    rejected = JudgeVerdict(verdict=Verdict.REJECT)
    assert rejected.valid and not rejected.passed
    invalid = JudgeVerdict(verdict=Verdict.PASS, invalid_reason="no proof")
    assert not invalid.valid and not invalid.passed


def test_runtime_hints_reach_the_workflow_def():
    """The hints have to survive the def round-trip or none of this is reachable."""
    from personalclaw.workflows.models import WorkflowDef

    spec = {
        "name": "t",
        "root": {"kind": "sequence", "id": "r", "children": []},
        "runtime_hints": {"judge": {"ratchet": "relaxed"}},
    }
    wf = WorkflowDef.from_dict(spec)
    assert wf.runtime_hints["judge"]["ratchet"] == "relaxed"
    assert wf.to_dict()["runtime_hints"] == spec["runtime_hints"]
    assert hints_from_dict(wf.runtime_hints["judge"]).ratchet is Ratchet.RELAXED


def test_malformed_runtime_hints_do_not_break_a_def():
    from personalclaw.workflows.models import WorkflowDef

    wf = WorkflowDef.from_dict(
        {
            "name": "t",
            "root": {"kind": "sequence", "id": "r", "children": []},
            "runtime_hints": "no",
        }
    )
    assert wf.runtime_hints == {}
