"""Tests for calibration — the instrument that checks the instrument.

A judge that always passes is worse than no judge: no judge is an absence you can see,
while a 100%-pass judge looks like a working control, reads as evidence in the ledger,
and licenses everything downstream to trust output nobody checked. These tests pin the
detector that catches it, and the lint rules that catch the shape before it ever runs.
"""

import pytest

from personalclaw.workflows.judge_calibration import (
    CANARY_MIN_SEPARATION,
    NODDING_MIN_RUNS,
    STUCK_FAILED_CYCLES,
    STUCK_IDENTICAL_SCORES,
    DivergenceRecord,
    GateHealth,
    VerdictRecord,
    assess_all_gates,
    assess_gate,
    assess_separation,
    calibration_summary,
    detect_stuck,
    divergence_exemplars,
    divergences_from_journal,
    journal_divergence,
    journal_verdict,
    prompt_version,
    verdicts_from_journal,
)


def verdict(v: str, *, status: str = "kept", template: str = "t", node: str = "judge", overall=1.5):
    return VerdictRecord(
        run_id="r", node_id=node, template=template, verdict=v, status=status, overall=overall
    )


def divergence(judge: str, human: str, *, reason: str = "", at: str = "1"):
    return DivergenceRecord(
        run_id="r",
        node_id="judge",
        template="t",
        judge_verdict=judge,
        human_verdict=human,
        reason=reason,
        created_at=at,
    )


# ── the nodding-loop detector ──


def test_a_gate_that_never_rejects_is_flagged():
    """Statistical evidence of a check that does not check."""
    report = assess_gate([verdict("PASS") for _ in range(10)], template="t", node_id="judge")
    assert report.health is GateHealth.NODDING
    assert report.blocks_default


def test_a_gate_that_rejects_sometimes_is_healthy():
    records = [verdict("PASS") for _ in range(8)] + [verdict("REJECT") for _ in range(2)]
    report = assess_gate(records, template="t", node_id="judge")
    assert report.health is GateHealth.DISCRIMINATING
    assert not report.blocks_default
    assert report.pass_rate == pytest.approx(0.8)


def test_a_gate_that_never_passes_is_also_broken():
    """The mirror failure. Broken in the SAFE direction — it fails work that should pass,
    which is visible and annoying rather than invisible and trusted."""
    report = assess_gate([verdict("REJECT") for _ in range(10)], template="t", node_id="judge")
    assert report.health is GateHealth.OBSTRUCTING
    assert not report.blocks_default  # visible, so it does not need to block


def test_a_small_sample_is_unproven_not_nodding():
    """Accusing a new template of nodding on its third run would train authors to
    distrust the detector."""
    report = assess_gate([verdict("PASS") for _ in range(3)], template="t", node_id="judge")
    assert report.health is GateHealth.UNPROVEN
    assert not report.blocks_default
    assert "need" in report.detail


def test_the_minimum_sample_is_generous():
    """A genuinely good template on easy work passes a lot; the threshold has to be
    'never, across enough runs to matter' rather than 'usually'."""
    assert NODDING_MIN_RUNS >= 5


def test_discarded_verdicts_still_count_as_evidence():
    """A rewound iteration's rejection really happened. Excluding it would let a template
    look like a nodder precisely BECAUSE its judge was doing its job and forcing rewinds.
    """
    records = [verdict("PASS") for _ in range(9)] + [verdict("REJECT", status="discard")]
    assert assess_gate(records, template="t", node_id="judge").health is GateHealth.DISCRIMINATING


def test_no_data_yields_none_not_a_zero_rate():
    """0.0 would read as "always fails"."""
    assert assess_gate([], template="t").pass_rate is None


def test_gates_are_assessed_per_template_and_node():
    records = [
        verdict("PASS", template="a", node="judge"),
        verdict("REJECT", template="b", node="judge"),
    ]
    reports = assess_all_gates(records, min_runs=1)
    assert {(r.template, r.node_id) for r in reports} == {("a", "judge"), ("b", "judge")}


def test_one_templates_nodding_does_not_taint_another():
    records = [verdict("PASS", template="nodder") for _ in range(10)]
    records += [verdict("PASS", template="good") for _ in range(9)]
    records += [verdict("REJECT", template="good")]
    by_template = {r.template: r.health for r in assess_all_gates(records)}
    assert by_template["nodder"] is GateHealth.NODDING
    assert by_template["good"] is GateHealth.DISCRIMINATING


# ── stuck detection ──


def test_identical_scores_across_the_window_are_stuck():
    """A loop grinding at the same score has already told you it is stuck; paying a model
    to confirm it is paying to be told what the numbers say."""
    result = detect_stuck([0.5] * STUCK_IDENTICAL_SCORES)
    assert result.stuck and result.reason == "identical_scores"


def test_one_short_of_the_window_is_not_stuck():
    assert not detect_stuck([0.5] * (STUCK_IDENTICAL_SCORES - 1)).stuck


def test_slow_convergence_is_not_stuck():
    """A score moving 0.01 a cycle is converging slowly, which is a different situation
    from one that has not moved at all. Conflating them pauses runs still making
    progress."""
    assert not detect_stuck([0.50, 0.51, 0.52, 0.53, 0.54, 0.55]).stuck


def test_consecutive_failures_are_stuck():
    result = detect_stuck([], failures=STUCK_FAILED_CYCLES)
    assert result.stuck and result.reason == "consecutive_failures"


def test_one_short_of_the_failure_window_is_not_stuck():
    assert not detect_stuck([], failures=STUCK_FAILED_CYCLES - 1).stuck


def test_failures_outrank_scores():
    """Three outright failures is a stronger signal than a flat score, and naming it
    correctly is what routes it to the right escalation arm."""
    result = detect_stuck([0.5] * 9, failures=STUCK_FAILED_CYCLES)
    assert result.reason == "consecutive_failures"


def test_an_empty_history_is_not_stuck():
    assert not detect_stuck([]).stuck


def test_the_windows_are_tunable():
    assert detect_stuck([0.5, 0.5], identical_window=2).stuck


# ── the judge canary ──


def test_a_judge_that_separates_strong_from_null_is_calibrated():
    result = assess_separation(4.5, 1.0)
    assert result.calibrated is True
    assert result.separation == pytest.approx(3.5)
    assert not result.blind


def test_a_judge_that_does_not_separate_is_blind():
    result = assess_separation(3.0, 2.5)
    assert result.calibrated is False
    assert result.blind
    assert "carry no information" in result.detail


def test_a_probe_that_could_not_run_is_not_blind():
    """Declaring a judge untrustworthy because the PROBE broke would halt runs for an
    infrastructure problem — a false accusation with real cost."""
    for strong, null in ((None, 1.0), (4.0, None), (None, None)):
        result = assess_separation(strong, null)
        assert result.calibrated is None
        assert not result.blind


def test_the_separation_threshold_matches_the_existing_canary():
    """A second, different threshold would make the same judge trustworthy to one caller
    and blind to another."""
    from personalclaw.loop.instrument import _CANARY_MIN_SEPARATION

    assert CANARY_MIN_SEPARATION == _CANARY_MIN_SEPARATION


def test_exactly_at_the_threshold_is_calibrated():
    assert assess_separation(CANARY_MIN_SEPARATION, 0.0).calibrated is True


# ── divergence ──


def test_a_judge_pass_the_human_rejects_is_a_false_pass():
    """The dangerous direction: it ships."""
    assert divergence("PASS", "REJECT").direction == "false_pass"


def test_a_judge_reject_the_human_passes_is_a_false_reject():
    assert divergence("REJECT", "PASS").direction == "false_reject"


def test_agreement_is_not_a_divergence():
    assert divergence("PASS", "PASS").direction == "agreement"
    assert divergence("REJECT", "REJECT").direction == "agreement"


def test_direction_is_case_insensitive():
    assert divergence("pass", "reject").direction == "false_pass"


def test_exemplars_put_the_dangerous_direction_first():
    """A bounded exemplar list should spend its budget on the error that ships."""
    divergences = [
        divergence("REJECT", "PASS", reason="it was fine", at="1"),
        divergence("PASS", "REJECT", reason="the test was skipped", at="2"),
    ]
    exemplars = divergence_exemplars(divergences)
    assert exemplars[0]["the_judge_said"] == "PASS"
    assert "Do not approve" in exemplars[0]["lesson"]


def test_agreements_are_not_exemplars():
    assert divergence_exemplars([divergence("PASS", "PASS")]) == []


def test_exemplars_are_bounded():
    many = [divergence("PASS", "REJECT", at=str(i)) for i in range(20)]
    assert len(divergence_exemplars(many, limit=3)) == 3


def test_the_users_reason_is_kept_verbatim():
    """The user's own words are what a few-shot exemplar needs; a dropdown would collapse
    exactly the detail that makes the example teach anything."""
    reason = "the fixture had no findings, so 3 was impossible"
    exemplar = divergence_exemplars([divergence("PASS", "REJECT", reason=reason)])[0]
    assert exemplar["why_the_user_was_right"] == reason


# ── the summary ──


def test_the_summary_reports_the_dangerous_rate_separately():
    """An instrument 90% accurate overall but wrong in the dangerous direction every time
    is not 90% good, and one averaged figure would hide that."""
    verdicts = [verdict("PASS") for _ in range(10)]
    summary = calibration_summary(verdicts, [divergence("PASS", "REJECT")])
    assert summary["false_passes"] == 1
    assert summary["false_rejects"] == 0
    assert summary["false_pass_rate"] == pytest.approx(0.1)


def test_the_summary_names_nodding_gates():
    summary = calibration_summary([verdict("PASS") for _ in range(10)], [])
    assert len(summary["nodding_gates"]) == 1
    assert summary["nodding_gates"][0]["template"] == "t"


def test_the_summary_separates_kept_from_discarded():
    verdicts = [verdict("PASS") for _ in range(3)] + [verdict("REJECT", status="discard")]
    summary = calibration_summary(verdicts, [])
    assert summary["kept"] == 3 and summary["discarded"] == 1


def test_an_empty_summary_reports_none_not_zero():
    summary = calibration_summary([], [])
    assert summary["pass_rate"] is None
    assert summary["median_overall"] is None


# ── the ledger ──


def test_a_verdict_journals_with_its_kind():
    entry = journal_verdict(verdict("PASS"))
    assert entry["kind"] == "judge_verdict"
    assert entry["verdict"] == "PASS"


def test_a_divergence_journals_with_its_kind_and_direction():
    entry = journal_divergence(divergence("PASS", "REJECT"))
    assert entry["kind"] == "judge_divergence"
    assert entry["direction"] == "false_pass"


def test_the_ledger_kinds_are_registered():
    """Without registration these events exist in code and nowhere else."""
    from personalclaw.workflows.journal import LEDGER_KINDS

    assert "judge_verdict" in LEDGER_KINDS
    assert "judge_divergence" in LEDGER_KINDS


def test_verdicts_round_trip_through_the_journal():
    original = verdict("REJECT", overall=0.5)
    parsed = verdicts_from_journal([journal_verdict(original)])
    assert len(parsed) == 1
    assert parsed[0].verdict == "REJECT"
    assert parsed[0].overall == pytest.approx(0.5)


def test_divergences_round_trip_through_the_journal():
    parsed = divergences_from_journal([journal_divergence(divergence("PASS", "REJECT"))])
    assert len(parsed) == 1
    assert parsed[0].direction == "false_pass"


@pytest.mark.parametrize(
    "unusable",
    [
        {"kind": "judge_verdict", "overall": "not a number"},
        "not even a dict",
        None,
        {"kind": "something_else"},
    ],
)
def test_an_unusable_row_is_skipped_not_fatal(unusable):
    """One unreadable row must not make the whole calibration unreadable — refusing to
    report at all because of a single bad line is the worse failure."""
    good = journal_verdict(verdict("PASS"))
    parsed = verdicts_from_journal([unusable, good])
    assert len(parsed) == 1


@pytest.mark.parametrize("bad_scores", ["not a dict", [], 42])
def test_a_row_with_only_a_malformed_scores_field_is_KEPT(bad_scores):
    """Deliberately kept, with empty scores, rather than dropped.

    Measured while writing this: a `scores` holding a string raised AttributeError, which
    the original except clause did not name. Fixing it raised the real question — is such
    a row unusable? No: its VERDICT is what the nodding detector counts, and that is
    intact. Dropping the row would lose a real rejection because a secondary field was
    malformed, which is exactly the direction that makes a judge look like a nodder.
    """
    entry = journal_verdict(verdict("REJECT"))
    entry["scores"] = bad_scores
    parsed = verdicts_from_journal([entry])
    assert len(parsed) == 1
    assert parsed[0].verdict == "REJECT"
    assert parsed[0].scores == {}


def test_a_journal_of_only_unusable_rows_yields_nothing_rather_than_raising():
    assert divergences_from_journal(["nope"]) == []
    assert verdicts_from_journal([]) == []
    assert verdicts_from_journal([None, "x", {"kind": "other"}]) == []


# ── prompt versioning ──


def test_prompt_versions_ignore_whitespace():
    assert prompt_version("Be skeptical.  Assume broken.") == prompt_version(
        "Be skeptical. Assume broken."
    )


def test_prompt_versions_change_on_real_edits():
    """Verdicts must be attributable to the wording that produced them, or the hardening
    loop's own improvements look like judge drift."""
    assert prompt_version("Be skeptical.") != prompt_version("Be lenient.")


def test_an_empty_prompt_still_versions():
    assert prompt_version("")


# ── the five anti-pattern lint rules ──


def _loop_spec(**body_cfg):
    """A minimal healthy loop template, for mutating into each anti-pattern."""
    return {
        "name": "t",
        "description": "x" * 60,
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {
                    "kind": "loop",
                    "id": "l",
                    "config": {"mode": "counted", "n": 3, "max_iterations": 3},
                    "body": {
                        "kind": "stage",
                        "id": "w",
                        "config": {"prompt": "work {{last.output}}", **body_cfg},
                    },
                },
                {
                    "kind": "stage",
                    "id": "judge",
                    "config": {
                        "prompt": "verify it",
                        "tools_posture": "verify",
                        "schema": {"verdict": "string"},
                    },
                },
            ],
        },
    }


def _codes(spec):
    from personalclaw.workflows.template_lint import lint_template

    return [f.code for f in lint_template(spec).findings]


def test_the_healthy_shape_triggers_nothing():
    """The baseline every anti-pattern test mutates away from — without this, a rule that
    fires on everything would look like it works."""
    assert _codes(_loop_spec()) == []


def test_a_judge_that_cannot_read_is_nodding():
    """`tools_posture: full` on a judge means it can fix what it was meant to report;
    anything other than `verify` means it cannot independently read what it judges."""
    spec = _loop_spec()
    spec["root"]["children"][1]["config"]["tools_posture"] = "full"
    assert "WFL_NODDING_JUDGE" in _codes(spec)


def test_a_judge_with_no_verdict_field_is_nodding():
    """A loop cannot route on prose."""
    spec = _loop_spec()
    spec["root"]["children"][1]["config"]["schema"] = {"notes": "string"}
    spec["root"]["children"][1]["config"]["prompt"] = "look at it"
    assert "WFL_NODDING_JUDGE" in _codes(spec)


def test_a_stage_reporting_its_own_done_is_flagged():
    spec = _loop_spec(schema={"done": "boolean"})
    assert "WFL_SELF_JUDGED" in _codes(spec)


def test_the_self_judged_opt_out_is_explicit():
    """A template author may genuinely want it, but the platform's oldest rule should not
    be waived silently."""
    spec = _loop_spec(schema={"done": "boolean"})
    spec["self_judged"] = True
    assert "WFL_SELF_JUDGED" not in _codes(spec)


def test_a_loop_that_never_reads_its_last_iteration_is_amnesiac():
    """It will redo the same first step forever, and each iteration will look productive."""
    spec = _loop_spec()
    spec["root"]["children"][0]["body"]["config"]["prompt"] = "do a step"
    assert "WFL_AMNESIAC_LOOP" in _codes(spec)


def test_a_loop_with_no_judge_at_all_is_blind():
    spec = _loop_spec()
    del spec["root"]["children"][1]
    assert "WFL_BLIND_LOOP" in _codes(spec)


def test_a_loop_with_no_cap_is_tangled():
    spec = _loop_spec()
    del spec["root"]["children"][0]["config"]["max_iterations"]
    assert "WFL_TANGLED_LOOP" in _codes(spec)


def test_a_loop_with_only_a_cap_is_tangled():
    """A cap alone makes the limit the behaviour rather than the guard."""
    spec = _loop_spec()
    spec["root"]["children"][0]["config"] = {"mode": "until", "max_iterations": 3}
    assert "WFL_TANGLED_LOOP" in _codes(spec)


def test_a_model_doing_deterministic_reshaping_is_flagged():
    """A `transform` does it for zero tokens and cannot hallucinate the answer."""
    spec = _loop_spec()
    spec["root"]["children"][0]["body"]["config"]["prompt"] = "Reformat this JSON {{last.output}}"
    assert "WFL_MANUAL_WORK" in _codes(spec)


def test_a_malformed_spec_does_not_crash_the_lint():
    """A lint that crashed on a bad spec would hide every finding it had already found."""
    from personalclaw.workflows.template_lint import lint_template

    for bad in ({"root": "not a dict"}, {}, {"root": None}):
        assert lint_template(bad) is not None


# ── the five-moves audit ──


def test_the_audit_locates_each_move_in_a_real_template():
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.template_lint import five_moves_audit

    moves = five_moves_audit(read_template("goal-pursuit-open-ended").to_dict())
    assert moves["discovery"] and moves["verification"] and moves["handoff"]


def test_the_audit_reports_an_absence_rather_than_inventing_one():
    """An absence has to be VISIBLE. A long-running template that schedules nothing never
    wakes up again, and that is the gap nobody notices in a 200-line spec."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.template_lint import audit_report

    report = audit_report(read_template("goal-pursuit-open-ended").to_dict())
    # No shipped template self-schedules yet: the monitor variant needs
    # AUTOMATION-SUBSTRATE's trigger tools, which do not exist.
    assert "scheduling" in report["absent_moves"]


def test_a_read_only_template_legitimately_persists_nothing():
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.template_lint import audit_report

    report = audit_report(read_template("diagnose-run").to_dict())
    assert "persistence" in report["absent_moves"]
    assert report["clean"]  # an absence is not a defect


def test_the_audit_survives_a_malformed_spec():
    from personalclaw.workflows.template_lint import five_moves_audit

    moves = five_moves_audit({"root": "nope"})
    assert set(moves) and not any(moves.values())


ANTI_PATTERN_CODES = {
    "WFL_NODDING_JUDGE",
    "WFL_SELF_JUDGED",
    "WFL_AMNESIAC_LOOP",
    "WFL_BLIND_LOOP",
    "WFL_TANGLED_LOOP",
    "WFL_MANUAL_WORK",
}

#: Empty, and that is the point: every finding my first draft of these rules produced on
#: the shipped library turned out to be a FALSE POSITIVE, not a template defect.
#:
#: In order: the amnesiac rule only accepted `{{last.}}`/`{{iter.}}` when `{{nodes.}}`
#: inside a loop body is equally cross-iteration state; the nodding rule demanded a field
#: literally named `verdict` when `refuted: boolean` routes just as well; it demanded
#: `tools_posture: verify` from `infer` nodes, which have no tools by definition; the
#: tangled rule required `progress_field` when `streak` alone is a valid `until_dry` exit;
#: and the blind rule only recognised verifiers with "judge" in the name, missing
#: `verify_refute`, `completeness_critic` and `round_gaps`.
#:
#: Five false positives from one afternoon's rules on six templates. A lint that cries
#: wolf on the library it ships with is a lint authors learn to ignore, which is worse
#: than no lint — so each was fixed at the rule rather than exempted at the call site.
KNOWN_ANTI_PATTERNS: set[tuple[str, str]] = set()


def test_no_shipped_template_has_an_UNKNOWN_anti_pattern():
    """The library is the example every user template gets copied from.

    Known findings are enumerated above with their reasoning; anything else is a
    regression this test exists to catch.
    """
    from personalclaw.workflows.bundled_defs import read_template, template_names
    from personalclaw.workflows.template_lint import lint_template

    unexpected = []
    for name in template_names():
        spec = read_template(name).to_dict()
        for finding in lint_template(spec).findings:
            if (
                finding.code in ANTI_PATTERN_CODES
                and (name, finding.code) not in KNOWN_ANTI_PATTERNS
            ):
                unexpected.append((name, finding.code, finding.message))
    assert not unexpected, unexpected


def test_the_templates_this_session_authored_are_anti_pattern_free():
    """No exemptions for the new ones."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.template_lint import lint_template

    for name in (
        "goal-pursuit-open-ended",
        "goal-pursuit-verifiable",
        "general-project",
        "design-project",
        "diagnose-run",
    ):
        spec = read_template(name).to_dict()
        findings = [f for f in lint_template(spec).findings if f.code in ANTI_PATTERN_CODES]
        assert not findings, (name, [f.to_dict() for f in findings])


def test_there_are_no_exemptions_to_forget():
    """An exemption nobody revisits becomes a permanent blind spot, so the set is empty
    and this test is what keeps it that way: adding one requires deleting this."""
    assert KNOWN_ANTI_PATTERNS == set()
