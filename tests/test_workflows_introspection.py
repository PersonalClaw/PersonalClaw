"""Tests for the introspection checklist and RunStats projection (§6.4, R5/R6/R9 — S53).

Everything here is a projection over the REAL journal, so the tests write real journal
events with the
real `Journal` class rather than hand-building dicts. That is what caught the two findings
this module
encodes:

**`GATE_REJECTED` is declared and emitted nowhere.** A said-no metric reading it would report zero
rejections for every gate in the library and flag all of them as fake checks — a warning badge on
everything, which is the same as no badge. Pass/reject comes from `GATE_RESOLVED`'s own `approved`
field, which the controller writes on both the auto-approve and human-resolution paths.

**A `step_attempt` on a non-gate created a gate-table row.** `publish` (an action) appeared with
`total: 0` and a 0.0 pass rate — a row that reads as a gate which has never passed anything, in a
table whose credibility is the only reason anyone reads it.

The third property is the badge's sample gate: "0 rejections in 0 runs" and "0 rejections
in 40 runs"
are different claims, and only the second is evidence.
"""

import pathlib
import tempfile

import pytest

from personalclaw.workflows.introspection import (
    CHECKLIST,
    FAKE_CHECK_MIN_RUNS,
    VERIFICATION_DEBT_WARN,
    GateStats,
    RunStats,
    checklist_gaps,
    gate_stats,
    percentile,
    proof_section,
    run_stats,
    template_card,
)


@pytest.fixture()
def journal_home(monkeypatch):
    """A real journal in an isolated home, via the env var the loader honors."""
    home = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def write_run(run_id: str, events: list[tuple]) -> list[dict]:
    """Write real journal events and read the ledger back.

    Uses the engine's own `Journal`, so the field names and event kinds are whatever the engine
    actually writes — a hand-built fixture would let this module drift from the stream it
    projects.
    """
    from personalclaw.workflows import journal as J

    j = J.Journal(run_id)
    for kind, kwargs in events:
        j.write(kind, **kwargs)
    return J.ledger(run_id)


# ── the projection agrees with the engine's own aggregate ──


def test_run_stats_matches_the_engines_OWN_run_totals(journal_home):
    """Two aggregates over one stream that disagreed would make the cockpit and the run row show
    different numbers for the same run, and there would be no way to tell which was right."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-agree",
        [
            (
                J.STEP_COMPLETED,
                {"instance_path": "a", "node_id": "a", "tokens": 100, "cost_usd": 0.01},
            ),
            (
                J.STEP_COMPLETED,
                {"instance_path": "b", "node_id": "b", "tokens": 250, "cost_usd": 0.02},
            ),
            (J.STEP_FAILED, {"instance_path": "c", "node_id": "c"}),
            (J.STEP_CACHED, {"instance_path": "d", "node_id": "d"}),
        ],
    )
    stats = run_stats("r-agree", events)
    official = J.run_totals("r-agree")
    assert stats.tokens == official["tokens"]
    assert round(stats.cost_usd, 6) == official["cost_usd"]
    assert stats.steps_completed == official["steps_completed"]
    assert stats.steps_failed == official["steps_failed"]
    assert stats.steps_cached == official["steps_cached"]


def test_the_models_a_run_used_are_collected(journal_home):
    """ "What is costing money" is unanswerable without knowing which model spent it."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-models",
        [
            (J.STEP_COMPLETED, {"instance_path": "a", "node_id": "a", "model": "claude-sonnet-5"}),
            (J.STEP_COMPLETED, {"instance_path": "b", "node_id": "b", "model": "claude-opus-5"}),
            (J.STEP_COMPLETED, {"instance_path": "c", "node_id": "c", "model": "claude-sonnet-5"}),
        ],
    )
    assert run_stats("r-models", events).models == ["claude-sonnet-5", "claude-opus-5"]


def test_an_empty_ledger_projects_to_zeros_rather_than_raising():
    stats = run_stats("r-empty", [])
    assert stats.tokens == 0
    assert stats.verification_debt == 0.0
    assert stats.cache_hit_rate == 0.0


def test_a_malformed_event_is_skipped(journal_home):
    stats = run_stats("r-junk", [None, "not a dict", {"kind": "step_completed", "tokens": 5}])
    assert stats.steps_completed == 1
    assert stats.tokens == 5


# ── verification debt ──


def test_a_step_a_GATE_verified_is_not_debt(journal_home):
    """Counted by binding, not adjacency: a node whose output a later gate consumed is verified even
    with three nodes between them. Counting "the next node is a gate" would report a
    correctly-verified reviewer as debt, and a debt number that flags correct structure gets
    ignored."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-verified",
        [
            (J.STEP_COMPLETED, {"instance_path": "a", "node_id": "write"}),
            (
                J.GATE_RESOLVED,
                {"instance_path": "g", "node_id": "check", "approved": True, "verifies": ["write"]},
            ),
        ],
    )
    stats = run_stats("r-verified", events)
    assert stats.unverified_steps == 0
    assert stats.verification_debt == 0.0


def test_an_UNVERIFIED_step_is_debt(journal_home):
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-debt",
        [
            (J.STEP_COMPLETED, {"instance_path": "a", "node_id": "write"}),
            (J.STEP_COMPLETED, {"instance_path": "b", "node_id": "publish"}),
        ],
    )
    stats = run_stats("r-debt", events)
    assert stats.unverified_steps == 2
    assert stats.verification_debt == 1.0


def test_a_run_that_completed_NOTHING_has_no_debt():
    """Reporting full debt would put a red number on a run that has not yet done anything wrong."""
    assert RunStats(run_id="r").verification_debt == 0.0


def test_the_debt_threshold_is_not_ZERO():
    """A plan legitimately contains zero-token actions whose output IS the check (S42's
    contract lint
    exempts them), so a 0% target would flag correct structure — and the rule that fires on correct
    work is the rule that gets suppressed wholesale."""
    assert VERIFICATION_DEBT_WARN > 0.0


# ── said-no metrics, from the field the engine actually writes ──


def test_gate_stats_read_the_APPROVED_field(journal_home):
    """NOT `GATE_REJECTED`: that kind is declared in `journal.py` and emitted nowhere, so a metric
    reading it would report zero rejections for every gate and flag the whole library as fake."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-gates",
        [
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": True}),
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": False}),
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": True}),
        ],
    )
    stats = gate_stats(events)["check"]
    assert stats.passes == 2
    assert stats.rejects == 1
    assert stats.pass_rate == round(2 / 3, 4)


def test_GATE_REJECTED_is_still_emitted_NOWHERE():
    """The measurement this module is built around. If a future session starts emitting it,
    this test
    fails and the projection should be revisited to count it — a silent second source of truth for
    rejections would double-count them."""
    import pathlib as _p
    import re as _re

    src = _p.Path("src/personalclaw")
    # A WRITE, not a mention: `journal.py` declares the constant and `introspection.py`
    # documents why
    # it is unused, and matching either would make this test fail on its own explanation.
    # The pattern
    # is the shape a real emitter has — the constant passed to a journal write.
    emit = _re.compile(r"write\s*\(\s*[\w_.]*GATE_REJECTED")
    emitters = [str(f) for f in src.rglob("*.py") if emit.search(f.read_text(encoding="utf-8"))]
    assert emitters == [], f"GATE_REJECTED is now emitted by {emitters} — revisit gate_stats"


def test_a_NON_GATE_retry_does_not_create_a_gate_row(journal_home):
    """Measured: attributing every `step_attempt` made `publish` (an action) appear in the
    gate table
    with `total: 0` and a 0.0 pass rate — a row that reads as a gate which has never
    passed anything,
    in a table whose credibility is the only reason anyone reads it."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-nongate",
        [
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": True}),
            (J.STEP_ATTEMPT, {"instance_path": "p", "node_id": "publish", "attempt": 2}),
        ],
    )
    stats = gate_stats(events)
    assert "publish" not in stats
    assert "check" in stats


def test_a_GATE_retry_is_counted(journal_home):
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-gateretry",
        [
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": True}),
            (J.STEP_ATTEMPT, {"instance_path": "g", "node_id": "check", "attempt": 3}),
        ],
    )
    assert gate_stats(events)["check"].retries_consumed == 1


def test_ATTEMPT_ONE_is_not_a_retry(journal_home):
    """Counting it would report a retry on every node that ever ran, which makes the retry column
    meaningless."""
    from personalclaw.workflows import journal as J

    events = write_run(
        "r-firsttry",
        [
            (J.GATE_RESOLVED, {"instance_path": "g", "node_id": "check", "approved": True}),
            (J.STEP_ATTEMPT, {"instance_path": "g", "node_id": "check", "attempt": 1}),
        ],
    )
    assert gate_stats(events)["check"].retries_consumed == 0


def test_an_event_with_no_node_id_is_skipped():
    assert gate_stats([{"kind": "gate_resolved", "approved": True}]) == {}


# ── the fake-check badge needs a SAMPLE ──


@pytest.mark.parametrize("passes", [1, 5, FAKE_CHECK_MIN_RUNS - 1])
def test_a_small_sample_does_NOT_earn_the_badge(passes):
    """ "0 rejections in 3 runs" is a sample-size artifact. A badge that fired there would teach the
    user to ignore badges before the metric had ever been right."""
    assert GateStats(node_id="check", passes=passes, rejects=0).fake_check_warning() == ""


@pytest.mark.parametrize("passes", [FAKE_CHECK_MIN_RUNS, 40])
def test_a_real_sample_with_zero_rejections_EARNS_the_badge(passes):
    """A 100% pass rate over a real sample is statistical evidence the check is not checking
    — worse
    than no gate, because a reviewer counts it."""
    warning = GateStats(node_id="check", passes=passes, rejects=0).fake_check_warning()
    assert "never rejected" in warning
    assert str(passes) in warning


def test_ONE_real_rejection_clears_the_badge():
    """The gate has demonstrated it can say no, which is the only thing the badge was
    testing for."""
    assert GateStats(node_id="check", passes=39, rejects=1).fake_check_warning() == ""


def test_the_badge_threshold_is_configurable_per_call():
    """A caller with a different confidence bar should not have to re-derive the rule."""
    stats = GateStats(node_id="check", passes=3, rejects=0)
    assert stats.fake_check_warning(min_runs=3) != ""


def test_a_gate_that_never_ran_has_no_pass_rate():
    """0/0 must not report 0% — that reads as a gate that rejects everything."""
    assert GateStats(node_id="check").pass_rate == 0.0
    assert GateStats(node_id="check").total == 0


# ── percentiles ──


def test_percentiles_are_REAL_observed_values():
    """Nearest-rank rather than interpolated: with the handful of runs a personal instance
    accumulates, an interpolated p95 invents a value between two real runs, and "the bad case cost
    $0.37" is more useful when $0.37 is a run that actually happened."""
    values = [0.01, 0.02, 0.03, 0.50]
    assert percentile(values, 95) in values
    assert percentile(values, 50) in values


def test_a_single_run_makes_p50_equal_p95():
    assert percentile([0.07], 50) == percentile([0.07], 95) == 0.07


def test_an_empty_series_is_zero_not_an_error():
    assert percentile([], 50) == 0.0


def test_p95_is_at_least_p50():
    values = [1.0, 2.0, 3.0, 100.0]
    assert percentile(values, 95) >= percentile(values, 50)


# ── template cards ──


def test_a_card_reports_BOTH_percentiles():
    """A mean would hide both the typical case and the bad one — one runaway run moves it,
    and nothing
    tells you whether the usual run is cheap."""
    runs = [RunStats(run_id=f"r{i}", cost_usd=c) for i, c in enumerate([0.01, 0.02, 0.03, 0.90])]
    card = template_card("deep-research", runs)
    assert card.cost_p50 < card.cost_p95
    assert card.runs == 4


def test_a_template_with_ONE_run_still_gets_a_card():
    """Withholding it until a sample accumulated would leave the newest template — the one
    most likely
    to be surprising — invisible on the surface that answers "what is costing money"."""
    card = template_card("brand-new", [RunStats(run_id="r1", cost_usd=0.05)])
    assert card.runs == 1
    assert card.cost_p50 == card.cost_p95 == 0.05


def test_a_template_with_no_runs_reports_zeros():
    card = template_card("unused", [])
    assert card.runs == 0
    assert card.cost_p95 == 0.0


def test_the_failure_rate_counts_RUNS_not_steps():
    """A run with four failed steps is one bad run, not four. Counting steps would make a
    single messy
    run look like a systemic problem."""
    runs = [
        RunStats(run_id="r1", steps_failed=4),
        RunStats(run_id="r2", steps_failed=0),
        RunStats(run_id="r3", steps_failed=0),
        RunStats(run_id="r4", steps_failed=0),
    ]
    assert template_card("t", runs).failure_rate == 0.25


def test_warnings_ride_on_the_card():
    card = template_card("t", [RunStats(run_id="r1")], warnings=["`check` has never rejected"])
    assert card.to_dict()["warnings"] == ["`check` has never rejected"]


# ── the nine-question checklist ──


def test_the_checklist_has_all_NINE_questions():
    """Named in code rather than a UI comment so the checklist is checkable — which is what turns
    "glanceable" from a taste claim into a contract."""
    assert len(CHECKLIST) == 9
    keys = {key for key, _ in CHECKLIST}
    assert {"running", "blocked", "approval", "failed", "cost", "risky", "next", "proof"} <= keys


def test_a_MISSING_answer_is_reported_as_a_gap():
    """A surface that renders eight of nine has a specific hole, and naming it is what makes this a
    validation script rather than an aspiration."""
    answers = {key: [] for key, _ in CHECKLIST[:8]}
    gaps = checklist_gaps(answers)
    assert len(gaps) == 1
    assert "proof" in gaps[0]


def test_an_EMPTY_answer_counts_as_answered():
    """ "Nothing is blocked" is an answer. Treating it as a gap would make an idle instance look
    broken."""
    assert checklist_gaps({key: [] for key, _ in CHECKLIST}) == []


def test_no_answers_reports_every_question():
    assert len(checklist_gaps({})) == 9


# ── the Proof section ──


def test_a_proof_section_with_no_evidence_SAYS_SO():
    """A Proof section with no evidence and no warning is the worst possible surface: it looks like
    proof."""
    section = proof_section(RunStats(run_id="r", steps_completed=3))
    assert any("claim about the run rather than proof" in w for w in section.warnings)
    assert section.honest is True


def test_a_section_with_evidence_needs_no_caveat():
    section = proof_section(
        RunStats(run_id="r", steps_completed=3), evidence_files=["before.png", "after.png"]
    )
    assert section.evidence_files == ["before.png", "after.png"]
    assert section.honest is True


def test_HIGH_verification_debt_earns_a_warning():
    """A run with high debt and a confident summary is exactly the shape that makes unattended work
    untrustworthy — the output looks finished, and nothing says how much of it was checked."""
    section = proof_section(
        RunStats(run_id="r", steps_completed=4, unverified_steps=4), evidence_files=["x.png"]
    )
    assert any("nothing verifying them" in w for w in section.warnings)


def test_LOW_debt_earns_no_debt_warning():
    section = proof_section(
        RunStats(run_id="r", steps_completed=4, unverified_steps=0), evidence_files=["x.png"]
    )
    assert not any("nothing verifying" in w for w in section.warnings)


def test_a_FAILED_step_is_called_out():
    section = proof_section(
        RunStats(run_id="r", steps_completed=2, steps_failed=1), evidence_files=["x.png"]
    )
    assert any("failed" in w for w in section.warnings)


def test_coverage_is_verified_over_total():
    section = proof_section(RunStats(run_id="r", steps_completed=4, unverified_steps=1))
    assert section.verified_steps == 3
    assert section.coverage == 0.75


def test_a_section_over_a_run_that_did_nothing_reports_zero_coverage_not_an_error():
    assert proof_section(RunStats(run_id="r")).coverage == 0.0


def test_the_summary_states_the_counts_rather_than_a_verdict():
    """A summary that said "succeeded" would be the run grading itself; the counts let the reader
    grade it."""
    summary = proof_section(RunStats(run_id="r", steps_completed=3, steps_failed=1)).summary
    assert "3 step(s) completed" in summary
    assert "1 failed" in summary
