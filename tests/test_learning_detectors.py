"""S74 — ad-hoc work → templates (§3.2) and failed stages → typed lessons (§3.3).

Two spokes, one discipline: a DETERMINISTIC chain decides and a model is consulted only at the score
boundary. §3.2 replaced "pure LLM-prompt branches" for a reason — a model asked "is this
template-worthy" costs a call per candidate and answers differently daily.

**Measured before writing.** `is_environment_failure_claim` — §3.3's deny-filter, the guardrail
keeping a flaky network from becoming a durable lesson — caught **1 of 4** real environment
failures: "connection refused", `ECONNRESET`, and rate-limit noise all passed through, and §3.3
routes EVERY `step_failed` through it. Widened here to 12/12 with 0 false positives on real lessons;
`test_the_widened_env_filter_catches_real_transport_failures` and
`test_the_env_filter_does_not_eat_real_lessons` are the two halves of that regression.
"""

from __future__ import annotations

import pytest

from personalclaw.after_turn_review import is_environment_failure_claim
from personalclaw.learning.detectors import (
    AUTO_FILE_SCORE,
    DROP_SCORE,
    FAILURE_MODES,
    MAX_BUDGET_BURN,
    MIN_PLAN_STEPS,
    NON_LESSON_MODES,
    SIMILARITY_MIN_PRIORS,
    SIMILARITY_THRESHOLD,
    SIMILARITY_WINDOW_DAYS,
    Action,
    Candidate,
    FailureMode,
    LessonKey,
    Skip,
    classify_failure,
    dedupe_signature,
    dominant_mode,
    failure_distribution,
    gate,
    lesson_worthy,
    similarity_verdict,
    structural_score,
)

_STRONG = [
    "fetch {{report_url}}",
    "extract the tables from the above",
    "summarize that result into {{format}}",
    "publish it to {{channel}}",
]


# ── the measured guardrail gap this session closed ──


@pytest.mark.parametrize(
    "text",
    [
        "connection refused by the proxy",
        "ECONNRESET reading from the socket",
        "rate limited: 429 from the provider",
        "we are being rate limited",
        "503 from api.example.com",
        "bad gateway",
        "certificate verify failed",
        "quota exceeded for this key",
        "no such host: api.internal",
        "ETIMEDOUT",
        "too many requests",
        "the network timed out after 30s",
    ],
)
def test_the_widened_env_filter_catches_real_transport_failures(text):
    """THE regression. The shipped list caught 1 of 4 of these.

    §3.3 routes every `step_failed` through this filter, so each miss was a flaky network becoming a
    DURABLE lesson — which teaches the agent to refuse a valid action later.
    """
    assert is_environment_failure_claim(text), f"env failure would become a lesson: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "always run make lint before claiming done",
        "the deploy script needs the venv activated first",
        "prefer editing over rewriting whole files",
        "use --force-with-lease, never bare --force",
        "the 429 rate limiter config lives in settings.py",
        "our rate limiter allows 100 requests per minute",
        "reset the counter when re-enabling an exhausted trigger",
        "connection pooling belongs in the client, not the handler",
        "the timeout constant should be 30, not 300",
    ],
)
def test_the_env_filter_does_not_eat_real_lessons(text):
    """The other half, and the reason the patterns are narrow.

    A bare `429` filtered "the 429 rate limiter config lives in settings.py" — measured. A status
    code
    only counts with failure context; `rate limited` (past tense) is a report while "rate limiter"
    is ordinary vocabulary.
    """
    assert not is_environment_failure_claim(text), f"real lesson filtered as env: {text!r}"


# ── §3.2: hard pre-gates ──


def test_a_one_step_plan_is_not_a_procedure():
    decision = gate(Candidate(run_id="r", steps=["do the thing {{x}}"]))
    assert decision.action == Action.SKIP.value
    assert decision.skip_reason == Skip.TOO_FEW_STEPS.value
    assert MIN_PLAN_STEPS == 2


def test_an_existing_template_means_no_library_gap():
    decision = gate(Candidate(run_id="r", steps=_STRONG, template_surfaced=True))
    assert decision.skip_reason == Skip.TEMPLATE_EXISTS.value


def test_a_near_death_plan_makes_a_bad_template():
    """§3.2: a run that spent its budget flailing is not a procedure — the flail teaches the
    expensive path."""
    decision = gate(Candidate(run_id="r", steps=_STRONG, budget_burn=0.95))
    assert decision.skip_reason == Skip.BUDGET_BURN.value
    assert MAX_BUDGET_BURN == 0.80


def test_a_plan_at_the_budget_line_still_qualifies():
    """The gate is `>`, not `>=` — a run exactly at the line has not exceeded it."""
    assert gate(Candidate(run_id="r", steps=_STRONG, budget_burn=MAX_BUDGET_BURN)).action != (
        Action.SKIP.value
    )


def test_pre_gates_run_before_scoring():
    """A one-step plan cannot be a useful template however it scores, so scoring is waste."""
    decision = gate(Candidate(run_id="r", steps=["x"]))
    assert decision.score.total == 0.0


# ── §3.2: the deterministic score ──


def test_a_plan_with_no_slot_is_a_recording_not_a_template():
    decision = gate(Candidate(run_id="r", steps=["fetch the report", "summarize the above"]))
    assert decision.skip_reason == Skip.NO_SLOTS.value


def test_hardcoded_entities_are_penalized():
    """A "template" full of one project's paths and ids will match nothing else."""
    clean = structural_score(Candidate(run_id="r", steps=_STRONG))
    messy = structural_score(
        Candidate(
            run_id="r",
            steps=[
                "fetch https://acme.internal/x",
                "write /Users/bob/out.md from the above",
                "email bob@acme.com {{note}}",
            ],
        )
    )
    assert messy.hardcoded >= 3
    assert messy.total < clean.total


def test_verb_diversity_measures_against_step_count():
    """Three distinct verbs across three steps is structure; three across twelve is repetition."""
    tight = structural_score(
        Candidate(run_id="r", steps=["build {{a}}", "test the above", "deploy it"])
    )
    repetitive = structural_score(
        Candidate(run_id="r", steps=["build {{a}}"] + ["build the above"] * 8)
    )
    assert tight.verb_diversity > repetitive.verb_diversity


def test_dependencies_come_from_back_references():
    linked = structural_score(Candidate(run_id="r", steps=_STRONG))
    independent = structural_score(
        Candidate(run_id="r", steps=["fetch {{a}}", "build {{b}}", "publish {{c}}"])
    )
    assert linked.dependencies > independent.dependencies


def test_the_score_is_reproducible():
    """The whole reason §3.2 replaced LLM-prompt branches."""
    candidate = Candidate(run_id="r", steps=_STRONG)
    assert structural_score(candidate).total == structural_score(candidate).total


def test_the_score_components_are_visible():
    """A scalar cannot say WHICH signal was weak, and the thresholds are tuned from data."""
    payload = structural_score(Candidate(run_id="r", steps=_STRONG)).to_dict()
    assert set(payload) == {"verb_diversity", "dependencies", "slots", "hardcoded", "total"}


def test_the_score_is_bounded():
    huge = Candidate(run_id="r", steps=["build {{a}} {{b}} {{c}} {{d}} from the above"] * 6)
    assert 0.0 <= structural_score(huge).total <= 1.0


def test_an_empty_plan_scores_zero():
    assert structural_score(Candidate(run_id="r", steps=[])).total == 0.0
    assert structural_score(Candidate(run_id="r", steps=["  ", ""])).total == 0.0


# ── §3.2: the LLM runs only at the boundary ──


def test_a_high_score_auto_files_with_ZERO_model_calls():
    """Filing, not installing — the human-accept invariant is what makes a free auto-file safe."""
    decision = gate(Candidate(run_id="r", steps=_STRONG))
    assert decision.action == Action.AUTO_FILE.value
    assert not decision.costs_a_model_call


def test_a_low_score_is_dropped_with_no_model_call():
    decision = gate(
        Candidate(
            run_id="r",
            steps=["poke https://x.internal/a/b/c {{q}}", "poke /Users/x/y/z again", "poke it"],
        )
    )
    assert decision.action == Action.SKIP.value
    assert not decision.costs_a_model_call


def test_only_the_ambiguous_middle_costs_anything():
    """Free at both extremes, paid in the band between."""
    assert DROP_SCORE < AUTO_FILE_SCORE
    middling = Candidate(run_id="r", steps=["fetch {{url}}", "look at it"])
    decision = gate(middling)
    if DROP_SCORE <= decision.score.total < AUTO_FILE_SCORE:
        assert decision.action == Action.CONSULT.value
        assert decision.costs_a_model_call


def test_every_negative_decision_names_a_typed_reason():
    """§3.2: "the flywheel's negative space is how thresholds get tuned".

    A detector that silently declines is one nobody can calibrate — and prose reasons are
    unfilterable, so the counts per reason are what say which gate earns its place.
    """
    candidates = [
        Candidate(run_id="a", steps=["one {{x}}"]),
        Candidate(run_id="b", steps=_STRONG, template_surfaced=True),
        Candidate(run_id="c", steps=_STRONG, budget_burn=0.99),
        Candidate(run_id="d", steps=["fetch the report", "summarize the above"]),
    ]
    reasons = {gate(c).skip_reason for c in candidates}
    assert reasons == {
        Skip.TOO_FEW_STEPS.value,
        Skip.TEMPLATE_EXISTS.value,
        Skip.BUDGET_BURN.value,
        Skip.NO_SLOTS.value,
    }
    assert all(r in {s.value for s in Skip} for r in reasons)


def test_every_decision_carries_a_human_reason_too():
    for candidate in (
        Candidate(run_id="a", steps=["one {{x}}"]),
        Candidate(run_id="b", steps=_STRONG),
    ):
        assert gate(candidate).reason


# ── §3.2: the plan-similarity detector ──


def test_repeated_similar_plans_suggest_a_template():
    decision = similarity_verdict(matches=[("a", 0.90, 3.0), ("b", 0.88, 10.0)], now=0.0)
    assert decision.action == Action.AUTO_FILE.value


def test_one_prior_is_not_a_pattern():
    decision = similarity_verdict(matches=[("a", 0.95, 1.0)], now=0.0)
    assert decision.skip_reason == Skip.TOO_FEW_PRIORS.value
    assert SIMILARITY_MIN_PRIORS == 2


def test_stale_priors_are_reported_DIFFERENTLY_from_too_few():
    """The same plan from a project that ended is a different finding from a plan built once.

    Counting either would propose a template for work nobody does any more.
    """
    decision = similarity_verdict(matches=[("a", 0.9, 300.0), ("b", 0.9, 400.0)], now=0.0)
    assert decision.skip_reason == Skip.STALE_PRIORS.value
    assert SIMILARITY_WINDOW_DAYS == 30


def test_below_threshold_matches_are_different_plans():
    decision = similarity_verdict(matches=[("a", 0.5, 1.0), ("b", 0.6, 1.0)], now=0.0)
    assert decision.action == Action.SKIP.value
    assert SIMILARITY_THRESHOLD == 0.85


def test_no_matches_at_all_is_handled():
    assert similarity_verdict(matches=[], now=0.0).action == Action.SKIP.value


# ── §3.3: typed failure modes ──


@pytest.mark.parametrize(
    "text,expected",
    [
        ("json decode error: unexpected field 'x'", FailureMode.SCHEMA_VIOLATION.value),
        ("UNIQUE constraint failed on users.email", FailureMode.CONSTRAINT_VIOLATION.value),
        ("deadline exceeded after 30s", FailureMode.TIMEOUT.value),
        ("no module named requests", FailureMode.DEPENDENCY.value),
        ("missing env var AWS_REGION", FailureMode.CONFIG.value),
        ("expected a list but got a dict", FailureMode.SPEC_MISMATCH.value),
        ("malformed csv record on line 9", FailureMode.DATA.value),
        ("Traceback ... AttributeError: no attribute 'foo'", FailureMode.CODE.value),
        ("connection refused by the proxy", FailureMode.ENVIRONMENT.value),
    ],
)
def test_failures_classify_onto_the_closed_enum(text, expected):
    assert classify_failure(text) == expected


def test_the_environment_check_wins_outright():
    """A message that is both — `ECONNRESET` inside a traceback — is still the world's fault.

    Classifying it as `code` would route it to a lesson, which is the outcome the guardrail forbids.
    """
    assert classify_failure("Traceback ... ECONNRESET reading from socket") == (
        FailureMode.ENVIRONMENT.value
    )


def test_unmatched_text_is_UNKNOWN_never_a_guess():
    """An unclassified failure must be visible as such, or `failure_distribution` attributes it
    to whichever mode the pattern list leans toward."""
    assert classify_failure("something inexplicable happened") == FailureMode.UNKNOWN.value
    assert classify_failure("") == FailureMode.UNKNOWN.value


def test_the_mode_enum_is_closed_and_covers_the_rca_seed():
    for seed in ("code", "config", "data", "infra", "dependency", "process"):
        assert seed in FAILURE_MODES


# ── §3.3: distribution and dominant-mode targeting ──


def test_the_distribution_reports_only_nonzero_modes():
    """A table of twelve zeros hides what is happening, and lets a reader mistake absence for a
    measured zero."""
    counts = failure_distribution(["json decode error"] * 3 + ["connection refused"] * 2)
    assert counts == {FailureMode.SCHEMA_VIOLATION.value: 3, FailureMode.ENVIRONMENT.value: 2}


def test_the_distribution_is_ordered_worst_first():
    counts = failure_distribution(["no module named x"] * 2 + ["json decode error"] * 7)
    assert list(counts)[0] == FailureMode.SCHEMA_VIOLATION.value


def test_the_dominant_mode_excludes_what_a_refiner_cannot_FIX():
    """Environment is the biggest bucket here and is still not the target.

    A refiner cannot fix the network; proposing against it would be a diff that cannot work.
    """
    failures = ["connection refused"] * 8 + ["json decode error"] * 5 + ["deadline exceeded"] * 3
    assert failure_distribution(failures)[FailureMode.ENVIRONMENT.value] == 8
    assert dominant_mode(failures) == FailureMode.SCHEMA_VIOLATION.value


def test_an_all_environment_corpus_has_no_dominant_mode():
    """Returning "" rather than the raw top mode is what stops a proposal against an uninfluenceable
    target."""
    assert dominant_mode(["connection refused"] * 20) == ""


def test_unknown_is_never_dominant():
    assert dominant_mode(["inexplicable"] * 30 + ["json decode error"]) == (
        FailureMode.SCHEMA_VIOLATION.value
    )


def test_an_empty_corpus_yields_no_dominant_mode():
    assert dominant_mode([]) == ""
    assert failure_distribution([]) == {}


# ── §3.3: lesson worthiness and keys ──


@pytest.mark.parametrize("mode", sorted(NON_LESSON_MODES))
def test_environment_class_failures_never_become_lessons(mode):
    assert mode in FAILURE_MODES


def test_a_transport_failure_is_refused_with_a_reason():
    worthy, reason = lesson_worthy("connection refused by the proxy")
    assert not worthy
    assert "refuse a valid action" in reason


def test_an_unclassified_failure_is_refused():
    worthy, reason = lesson_worthy("something inexplicable")
    assert not worthy and "could not be classified" in reason


def test_a_real_failure_is_lesson_worthy():
    worthy, reason = lesson_worthy("json decode error: unexpected field 'x'")
    assert worthy and reason == ""


def test_lessons_are_keyed_by_template_and_mode_for_re_injection():
    """§3.3 calls a lesson "a persistent mutation hint" — a hint nobody can look up by template is a
    note in a drawer."""
    key = LessonKey(template="nightly-digest", mode=FailureMode.SCHEMA_VIOLATION.value)
    assert key.key == "lesson.nightly-digest:schema_violation"


def test_a_key_survives_missing_parts():
    assert LessonKey(template="", mode="").key == "lesson.unknown:unknown"


def test_the_dedupe_signature_is_SHARED_with_the_refiner():
    """Two signature schemes would make a clustered failure and its lesson un-joinable — the refiner
    would target a cluster whose lesson it could not find."""
    from personalclaw.learning.refiner import failure_signature

    text = "json decode error at line 42 of /tmp/x.json (trace a3f9c8d1)"
    assert dedupe_signature(text) in failure_signature(text)


def test_the_signature_collapses_run_specific_noise():
    a = dedupe_signature("json decode error at line 42 of /tmp/x.json")
    b = dedupe_signature("json decode error at line 9 of /var/y.json")
    assert a == b and a


def test_a_keyed_lesson_serializes():
    payload = LessonKey(template="t", mode="code", signature="sig").to_dict()
    assert set(payload) == {"template", "mode", "signature", "key"}
