"""Tests for the judge contract — maker/checker with teeth.

Every test here corresponds to a degenerate pass the contract makes impossible rather
than discourages. The distinction matters: prompt doctrine ("be skeptical") is advice,
and advice loses to a worker being scored on completion.
"""

import inspect

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
    adjudicate,
    aggregate_samples,
    compute_overall,
    detect_forbidden_modes,
    hints_from_dict,
    judge_instruction,
    meets_ratchet,
    parse_judge_json,
    score_for,
    validate_verdict,
    verdict_for_cycle,
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


# ── the enforcement rail (WF2LOO-12 measured it unwired; WF2LOO-13 wired it) ──

#: The ENFORCEMENT entry points production must reach DIRECTLY. The TYPES are deliberately
#: absent: `judge_actors` imports `Isolation` and `judge_pretier` imports `FallbackCheck`, and
#: importing a type enforces nothing.
_ENFORCEMENT_ENTRY_POINTS = (
    "validate_verdict",
    "hints_from_dict",
    "aggregate_samples",
    "judge_instruction",
    "parse_judge_json",
)

#: Reached THROUGH `validate_verdict` rather than called from outside — so "no production caller"
#: is the wrong question for them and the right one is "is the chain from the entry point still
#: intact". Asserted by AST below rather than by grep: a rule that stops being reachable from the
#: one function production calls is exactly as inert as one with no caller at all, and it looks
#: fine from every other angle.
_REACHED_THROUGH_VALIDATION = ("meets_ratchet", "compute_overall", "detect_forbidden_modes")

#: The claim the docstring carried while the list had NO caller. WF2LOO-13 gave every entry point
#: one, so this phrase reappearing means someone re-stranded the contract — or copied the old
#: notice back in. Either way the docstring and the call graph have parted company again.
_UNWIRED_MARKER = "enforcement is not wired"

_OWNER = "workflows/judge_contract.py"


def _repo_src():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "src"


def _production_callers() -> dict[str, list[str]]:
    """Production call sites of the enforcement entry points, keyed by name.

    Substring matching on `name(` / `import name` on purpose: it over-reports rather than
    under-reports, and an over-report on THIS rail means a human re-reads the docstring.
    The owning module is excluded — a function calling its own helpers is not wiring.
    """
    found: dict[str, list[str]] = {name: [] for name in _ENFORCEMENT_ENTRY_POINTS}
    for path in sorted(_repo_src().rglob("*.py")):
        rel = path.as_posix()
        if rel.endswith(_OWNER):
            continue
        text = path.read_text(encoding="utf-8")
        for name in _ENFORCEMENT_ENTRY_POINTS:
            if f"{name}(" in text or f"import {name}" in text:
                found[name].append(rel)
    return {name: sites for name, sites in found.items() if sites}


def test_every_enforcement_entry_point_has_a_production_caller():
    """The INVERTED WF2LOO-12 rail: it used to prove the contract was stranded, now it holds the
    wiring in place.

    WF2LOO-12 measured that nothing in `src/` called this module's enforcement and made the
    docstring say so; WF2LOO-13 wired all six through `engine.dispatch_gate`'s judge branch, the
    `apply_judge_contract` seam and the controller's `runtime_hints.judge` threading. The rail was
    inverted rather than deleted, because "the contract is authored but nothing runs it" is a state
    this module has already been in once, and it is invisible from inside the module.
    """
    source = (_repo_src() / "personalclaw/workflows/judge_contract.py").read_text(encoding="utf-8")
    # Vacuity floor: a rail scanning for names that no longer exist passes forever on an
    # empty match set. Every entry point must still be a function in the owning module.
    missing = [name for name in _ENFORCEMENT_ENTRY_POINTS if f"def {name}(" not in source]
    assert not missing, (
        f"this rail scans for enforcement functions judge_contract no longer defines: {missing}"
        " — retarget the list, do not let it match nothing"
    )

    wired = _production_callers()
    stranded = [name for name in _ENFORCEMENT_ENTRY_POINTS if name not in wired]
    assert not stranded, (
        f"{stranded} lost every production caller — the judge contract is authored-and-unrun "
        "again, which is the exact defect WF2LOO-12 measured and WF2LOO-13 fixed. Re-wire it, or "
        "if the mechanism is genuinely gone, delete it rather than leaving a rule nothing applies."
    )


def test_the_rules_under_validate_verdict_are_still_reached_from_it():
    """The chain, not just the entry point.

    `meets_ratchet`, `compute_overall` and `detect_forbidden_modes` have no caller outside this
    module by design — `validate_verdict` is the one door. That makes them the shape a
    caller-count audit cannot see: still defined, still tested, and unreachable the moment one
    call is dropped from the door.
    """
    import ast

    tree = ast.parse(
        (_repo_src() / "personalclaw/workflows/judge_contract.py").read_text(encoding="utf-8")
    )
    reached: set[str] = set()
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        if func.name not in ("validate_verdict", "meets_ratchet"):
            continue
        for call in ast.walk(func):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                reached.add(call.func.id)
    unreachable = [name for name in _REACHED_THROUGH_VALIDATION if name not in reached]
    assert not unreachable, (
        f"{unreachable} is no longer called from validate_verdict/meets_ratchet, so nothing on the "
        "live path applies it — a rubric ratchet or a forbidden-mode denylist that runs nowhere is "
        "present, plausible and doing nothing"
    )


def test_the_docstring_describes_the_live_path_rather_than_disclaiming_it():
    """The docstring is a claim about the code, so it rots the same way the code does.

    Held in both directions by construction: the "not wired" disclaimer must be gone, and the
    docstring must NAME the seams that enforce — a reader who is told enforcement is live and not
    told where goes looking for a caller they cannot find.
    """
    from personalclaw.workflows import engine, judge_contract

    doc = (judge_contract.__doc__ or "").lower()
    assert _UNWIRED_MARKER not in doc, (
        "judge_contract's enforcement has production callers, but its docstring still says "
        "enforcement is not wired — describe what enforces what, and on which path"
    )
    for named in ("dispatch_gate", "validate_verdict", "apply_judge_contract"):
        assert named in doc, (
            f"the docstring no longer names {named!r} as part of the live enforcement path — an "
            "enforcement notice that does not say what runs it is the same dead end as the "
            "unwired notice it replaced"
        )

    gate = inspect.getsource(engine.dispatch_gate)
    # Vacuity floor #2: confirm we are reading the function that owns the judge branch before
    # drawing a conclusion from what its text does contain.
    assert "GateKind.JUDGE" in gate, (
        "dispatch_gate no longer contains the judge branch — this rail is reading the wrong "
        "function and the assertions below prove nothing"
    )
    assert "EXACTLY ONE word" not in gate, (
        "the judge gate demands one bare word again. A bare word cannot carry the proof a PASS is "
        "required to cite, so the contract becomes inexpressible on the live path — that is the "
        "regression WF2LOO-13 removed, not a simplification"
    )
    for named in ("judge_instruction", "parse_judge_json", "validate_verdict"):
        assert named in gate, f"the judge gate no longer calls {named!r}"


def test_the_posture_measurement_stays_with_the_code():
    """done_when: "write down the reasoning where the code lives".

    Enforcement on a live population is only defensible with the population written down. The
    numbers are the argument, so they live in the module that enforces — not only in a plan log
    nobody reads from a traceback.
    """
    from personalclaw.workflows import judge_contract

    doc = judge_contract.__doc__ or ""
    for marker in ("7 judge GATES", "13 rubric criteria", "not an outage"):
        assert marker in doc, f"the WF2LOO-13 posture measurement lost {marker!r}"


# ── the wire shape and the anti-outage rules (WF2LOO-13) ──


class TestParseJudgeJson:
    def test_a_bare_object(self):
        assert parse_judge_json('{"verdict": "PASS"}') == {"verdict": "PASS"}

    def test_a_fenced_block(self):
        assert parse_judge_json('```json\n{"verdict": "REJECT"}\n```') == {"verdict": "REJECT"}

    def test_prose_either_side(self):
        raw = 'Here is my assessment:\n{"verdict": "PASS", "proof": "ok"}\nHope that helps.'
        assert parse_judge_json(raw)["verdict"] == "PASS"

    def test_braces_inside_the_reasoning_do_not_truncate_it(self):
        """Brace-counted rather than regex-matched: `reasoning` routinely contains braces, and a
        greedy or lazy regex either swallows the rest of the answer or stops at the first `}`."""
        raw = '{"reasoning": "the dict {a: 1} was empty", "verdict": "REJECT"}'
        assert parse_judge_json(raw)["verdict"] == "REJECT"

    def test_a_bare_verdict_word_is_NOT_a_verdict(self):
        """The old protocol, refused on purpose: a bare word cannot carry the proof a PASS must
        cite, so accepting it would reopen the hole the contract exists to close."""
        assert parse_judge_json("PASS") is None

    def test_prose_and_emptiness_are_None(self):
        for raw in ("", None, "Well, it depends.", "[1, 2, 3]"):
            assert parse_judge_json(raw) is None, raw


class TestScoreLookupTolerance:
    """🔴 The single measure that keeps the ratchet from being an outage.

    Under STRICT an unscored criterion is a shortfall, so a REJECT. Byte-exact lookup would have
    made a model that wrote "verify command passes" for the declared "the verify command passes"
    fail every PASS in the six templates that declare a rubric.
    """

    def test_an_exact_key(self):
        assert score_for("the tests pass", {"the tests pass": 2}) == 2

    def test_case_and_punctuation_are_collapsed(self):
        assert score_for("the tests pass", {"The tests pass!": 1}) == 1

    def test_a_restated_key_still_scores(self):
        assert score_for("the verify command passes", {"verify command passes": 2}) == 2

    def test_an_AMBIGUOUS_partial_match_stays_unscored(self):
        """Guessing which of two keys the judge meant is a routing decision made on noise, and
        "not scored" is the auditable answer."""
        scores = {"the tests pass quickly": 2, "the tests pass slowly": 0}
        assert score_for("the tests pass", scores) is None

    def test_a_criterion_nobody_scored(self):
        assert score_for("coverage held", {"the tests pass": 2}) is None


class TestTheRatchetIsScopedToDeclaredRubrics:
    def test_no_rubric_means_no_shortfall(self):
        """The rule that protects 6 of the 7 live judge gates: they declare no rubric, so there is
        nothing to compare and the ratchet is a no-op. A template that never described convergence
        must not be REJECTed into a dead loop for it."""
        ok, shortfalls = meets_ratchet({}, JudgeHints())
        assert ok is True and shortfalls == []

    def test_a_pass_with_no_rubric_and_cited_proof_is_valid(self):
        v = validate_verdict({"verdict": "PASS", "proof": "exit 0"}, JudgeHints())
        assert v.passed is True

    def test_a_declared_rubric_scored_below_target_refuses_the_PASS(self):
        hints = JudgeHints(rubric=[RubricCriterion("the tests pass", target_score=2)])
        v = validate_verdict(
            {"verdict": "PASS", "proof": "exit 1", "scores": {"the tests pass": 1}}, hints
        )
        assert v.passed is False
        assert "below rubric targets" in v.invalid_reason
        assert v.protocol_error is False, "a shortfall is about the WORK, not the answer's shape"
        assert v.shortfalls == ["the tests pass: 1 < 2"]

    def test_a_PASS_that_scored_NOTHING_is_a_protocol_error(self):
        """Distinct from a shortfall, because the remediation is different: this one needs the
        judge's answer fixed, not the deliverable. Reporting both as "below rubric targets" sends
        an operator to read a deliverable that was never measured."""
        hints = JudgeHints(rubric=[RubricCriterion("the tests pass")])
        v = validate_verdict({"verdict": "PASS", "proof": "exit 0"}, hints)
        assert v.protocol_error is True
        assert "scored none of the 1 declared rubric criteria" in v.invalid_reason


class TestTheInstructionStatesWhatIsEnforced:
    """A contract enforced against a prompt that never mentioned it is a trap, not a gate."""

    def test_the_exact_rubric_keys_are_named(self):
        hints = JudgeHints(rubric=[RubricCriterion("the layer is identified", target_score=1)])
        text = judge_instruction("judge it", hints)
        assert '"the layer is identified" (target 1)' in text
        assert "unscored criterion counts as a shortfall" in text

    def test_the_proof_requirement_is_stated(self):
        text = judge_instruction("judge it", JudgeHints())
        assert "neither `proof` nor `evidence_refs` is REJECTED" in text

    def test_every_member_of_the_closed_set_is_offered(self):
        text = judge_instruction("judge it", JudgeHints())
        for member in Verdict:
            assert f'"{member.value}"' in text, member

    def test_the_forbidden_modes_and_hidden_checks_are_rendered(self):
        hints = JudgeHints(
            forbidden_success_modes=["test deleted or skipped"],
            hidden_validation_commands=["pytest -q tests/test_secret.py"],
        )
        text = judge_instruction("judge it", hints)
        assert "test deleted or skipped" in text
        # Rendered ONLY into judge prompts: a worker that can read the hidden checks satisfies
        # them specifically, which is the same as not having them.
        assert "pytest -q tests/test_secret.py" in text


class TestAggregationAbsorbedTheGateRules:
    """The engine used to restate these over a second verdict enum. The merge removed the reason,
    so the rules have to live in the one aggregator — or they are silently gone."""

    def _v(self, verdict: str, **kw) -> JudgeVerdict:
        return validate_verdict({"verdict": verdict, "proof": "cited", **kw}, JudgeHints())

    def test_a_bare_ESCALATE_verdict_outweighs_a_pass_majority(self):
        samples = [self._v("PASS"), self._v("PASS"), self._v("ESCALATE")]
        assert aggregate_samples(samples, JudgeHints()).verdict is Verdict.ESCALATE

    def test_a_split_prefers_the_terminal_REJECT_over_the_spinning_RETRY(self):
        samples = [self._v("RETRY"), self._v("REJECT")]
        assert aggregate_samples(samples, JudgeHints()).verdict is Verdict.REJECT

    def test_unanimous_RETRY_stays_RETRY(self):
        samples = [self._v("RETRY"), self._v("RETRY"), self._v("RETRY")]
        assert aggregate_samples(samples, JudgeHints()).verdict is Verdict.RETRY


class TestThirdVocabularyAbsorbed:
    """WF2LOO-16: `loop/judge.CycleVerdict` was a THIRD dialect over one decision. It is deleted
    and its fields live here. These tests hold the two halves that made the merge additive rather
    than a rename — the 0-5 signals the contract lacked, and the projection from the loop's
    boolean done-ness onto the closed enum.
    """

    def test_cycle_verdict_is_deleted_not_bridged(self):
        """No compat shim, no re-export, no alias. A surviving `CycleVerdict` symbol anywhere in
        `loop.judge` would mean the third vocabulary is still constructible, which is the whole
        thing this atom removed."""
        from personalclaw.loop import judge as loop_judge

        assert not hasattr(loop_judge, "CycleVerdict")
        assert not hasattr(loop_judge, "adjudicate")  # the rule moved WITH its fields
        assert not hasattr(loop_judge, "_clamp")  # the contract owns the 0-5 clamp now

    def test_verdict_for_cycle_maps_every_corner(self):
        """All four corners, including the overlap. `done` DOMINATES a regression because that is
        what the supervisor does (`goal._assess_open_ended` completes on `done` without reading
        `regressed`); a projection stricter than the routing it labels would be a lie."""
        assert verdict_for_cycle(True, False) is Verdict.PASS
        assert verdict_for_cycle(True, True) is Verdict.PASS
        assert verdict_for_cycle(False, True) is Verdict.REJECT
        assert verdict_for_cycle(False, False) is Verdict.RETRY

    def test_done_is_derived_from_the_enum_never_stored(self):
        """One fact, one source. A stored `done` beside a `verdict` is two places to disagree."""
        assert JudgeVerdict(verdict=Verdict.PASS).done is True
        assert JudgeVerdict(verdict=Verdict.RETRY).done is False
        with pytest.raises(TypeError):
            JudgeVerdict(verdict=Verdict.RETRY, done=True)  # type: ignore[call-arg]

    def test_done_and_passed_are_not_the_same_claim(self):
        """`done` is "the judge said complete"; `passed` adds "and it survived validation". The
        loop routes on the first because its prompt was never given the preconditions."""
        invalid = validate_verdict({"verdict": "PASS"}, JudgeHints())  # no proof cited
        assert invalid.done is True and invalid.passed is False

    @pytest.mark.parametrize(
        "raw,expected",
        [(99, 5.0), (-4, 0.0), (2.5, 2.5), (5, 5.0), (0, 0.0), ("high", 0.0), (None, 0.0)],
    )
    def test_the_0_5_clamp_is_structural(self, raw, expected):
        """Clamped in `__post_init__`, so EVERY producer clamps — the loop parser, `adjudicate`
        and `validate_verdict` cannot disagree about the range. Unclamped, a model answering `9`
        would clear `GRANULARITY_PRESETS`' exhaustive threshold of 1.0 forever and a loop would
        never call diminishing returns."""
        v = JudgeVerdict(verdict=Verdict.RETRY, marginal_value=raw, quality_score=raw)
        assert v.marginal_value == expected and v.quality_score == expected

    def test_clamp_holds_on_the_contract_parse_path_too(self):
        v = validate_verdict(
            {"verdict": "RETRY", "marginal_value": 42, "quality_score": -1}, JudgeHints()
        )
        assert v.marginal_value == 5.0 and v.quality_score == 0.0

    def test_validate_verdict_parses_the_absorbed_fields(self):
        """Six bundled judge STAGES already declare `marginal_value` in their schema; before the
        merge this record could not hold it, so it was dropped on the floor."""
        v = validate_verdict(
            {
                "verdict": "RETRY",
                "marginal_value": 3.5,
                "quality_score": 4.25,
                "regressed": True,
                "done_reason": "two criteria still open",
            },
            JudgeHints(),
        )
        assert v.marginal_value == 3.5 and v.quality_score == 4.25
        assert v.regressed is True and v.done_reason == "two criteria still open"

    def test_to_dict_carries_every_key_the_cockpit_reads(self):
        """The cockpit's ROI rail and verdict chips read these off the PERSISTED shape. Dropping
        one is a silent blank in the UI, not a test failure, so the wire contract is asserted."""
        d = JudgeVerdict(
            verdict=Verdict.PASS, done_reason="met", marginal_value=3.0, quality_score=4.0
        ).to_dict()
        for key in (
            "done",
            "done_reason",
            "marginal_value",
            "quality_score",
            "regressed",
            "adversarial",
            "band_used",
            "verdict",
            "evidence_refs",
        ):
            assert key in d, f"the cockpit reads {key} and it is not on the wire"
        assert d["done"] is True and d["marginal_value"] == 3.0


class TestAsymmetricAdjudication:
    """The skeptic merge, moved here with the fields it merges. The asymmetry is the mechanism:
    a completion needs two independent yeses, a regression needs only one flag.
    """

    def _v(self, done=False, regressed=False, **kw) -> JudgeVerdict:
        return JudgeVerdict(verdict=verdict_for_cycle(done, regressed), regressed=regressed, **kw)

    def test_a_done_does_NOT_survive_a_disagreeing_skeptic(self):
        merged = adjudicate(self._v(done=True, done_reason="met"), self._v(done=False))
        assert merged.done is False, "a claimed completion survived on ONE yes"
        assert merged.verdict is Verdict.RETRY  # re-projected, not left saying PASS
        assert "overturned" in merged.done_reason

    def test_a_done_survives_only_when_the_skeptic_agrees(self):
        merged = adjudicate(self._v(done=True, done_reason="met"), self._v(done=True))
        assert merged.done is True and merged.verdict is Verdict.PASS

    def test_a_regressed_survives_EITHER_judge_flagging_it(self):
        assert adjudicate(self._v(), self._v(regressed=True)).regressed is True
        assert adjudicate(self._v(regressed=True), self._v()).regressed is True

    def test_a_regression_flagged_by_one_judge_is_not_downgraded_to_RETRY(self):
        merged = adjudicate(self._v(), self._v(regressed=True))
        assert merged.verdict is Verdict.REJECT

    def test_an_unavailable_skeptic_never_manufactures_a_refutation(self):
        primary = self._v(done=True, marginal_value=2.0, quality_score=3.0)
        result = adjudicate(primary, None)
        assert result.done is True and result.adversarial is False

    def test_the_merge_carries_the_primarys_scores_and_band(self):
        primary = self._v(done=True, marginal_value=3.5, quality_score=4.2, band_used=1.7)
        merged = adjudicate(primary, self._v(done=True))
        assert merged.marginal_value == 3.5
        assert merged.quality_score == 4.2
        assert merged.band_used == 1.7
        assert merged.adversarial is True

    def test_the_merge_keeps_the_reasoning_and_the_observed_evidence(self):
        """WF2LOO-16 DISCOVERY: the loop-local version rebuilt the verdict WITHOUT `reasoning`,
        so the bounded chain-of-thought (AUTONOMY-GUARDRAILS §2.4) was discarded on exactly the
        high-stakes verdicts that earned a second judge — and after this atom it would have
        discarded the supervisor's observed `evidence_refs` with it."""
        primary = self._v(
            done=True,
            reasoning="ran the verify command, exit 0; REPORT.md has the section",
            evidence_refs=["command:pytest -q → PASSED (exit 0)", "file:REPORT.md"],
        )
        merged = adjudicate(primary, self._v(done=True))
        assert merged.reasoning == primary.reasoning
        assert merged.evidence_refs == primary.evidence_refs
