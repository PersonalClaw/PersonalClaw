"""S77 — predict-then-verify, auto-filed reverts, the incognito gate (LEARN-R16 / §7).

The last spoke: everything upstream FILES proposals, and this measures what happened after a human
accepted one. §3.1's rule is predict-then-verify rather than measure-after — an accepted proposal
declared which failures it would fix, so the verdict compares prediction against outcome instead of
looking at a delta and inventing a story.

**Measured before writing.** `refiner.canary_verdict` (S73) already returns the five verdicts from
a scalar, so this reuses that vocabulary rather than forking it. And `learning/gate.py`'s permission
half is genuinely CLOSED — probed across all three cadences, a restricted session is refused
whatever its tool count or correction signal. What is NOT closed is COVERAGE:
`test_the_uncovered_cadences_are_pinned` records that `SESSION_END` and `RUN_END` are declared with
zero
live callers, because a gate cannot suppress a path nobody routes through it.
"""

from __future__ import annotations

from personalclaw.learning.accountability import (
    DELTA_EPS,
    MIN_RUNS,
    REVERT_VERDICTS,
    VERDICTS,
    Outcome,
    ProposerTrust,
    Verdict,
    assert_gate_covers_cadences,
    attribute,
    proposer_trust,
    revert_proposal,
)


def _attr(predicted, before, after, runs=5):
    return attribute(
        predicted_fixes=predicted, outcome=Outcome(before=before, after=after, runs_after=runs)
    )


# ── the verdict vocabulary is shared, not forked ──


def test_the_verdict_names_match_the_refiners():
    """S73's `canary_verdict` already returns these from a scalar before/after.

    Two verdict scales would make one proposal's history unreadable when it passed through both
    paths.
    """
    from personalclaw.learning.refiner import canary_verdict

    produced = {
        canary_verdict(before=0.9, after=0.5, runs=5),
        canary_verdict(before=0.5, after=0.9, runs=5),
        canary_verdict(before=0.9, after=0.9, runs=5),
        canary_verdict(before=0.5, after=0.9, runs=1),
    }
    assert produced <= set(VERDICTS)


def test_pending_is_a_distinct_state_not_a_guess():
    assert Verdict.PENDING.value in VERDICTS
    assert _attr(["schema"], {"schema": 0.5}, {"schema": 0.0}, runs=1).verdict == "PENDING"
    assert MIN_RUNS == 3


# ── the five-way ladder ──


def test_every_prediction_landing_and_nothing_regressing_is_EFFECTIVE():
    result = _attr(["schema"], {"schema": 0.5}, {"schema": 0.0})
    assert result.verdict == Verdict.EFFECTIVE.value
    assert result.precision == 1.0


def test_some_predictions_landing_is_PARTIALLY_EFFECTIVE():
    result = _attr(
        ["schema", "timeout"], {"schema": 0.5, "timeout": 0.4}, {"schema": 0.0, "timeout": 0.4}
    )
    assert result.verdict == Verdict.PARTIALLY_EFFECTIVE.value
    assert result.precision == 0.5
    assert result.unfulfilled == ["timeout"]


def test_nothing_moving_is_INEFFECTIVE_not_harmful():
    """Clutter, not damage — and the distinction is what keeps the revert queue readable."""
    result = _attr(["schema"], {"schema": 0.5}, {"schema": 0.5})
    assert result.verdict == Verdict.INEFFECTIVE.value


def test_a_regression_alongside_a_real_fix_is_MIXED_not_harmful():
    """Deliberately not harmful: the change did something the user wanted, so reverting is theirs
    rather than an automatic rollback."""
    result = _attr(["schema"], {"schema": 0.5, "code": 0.1}, {"schema": 0.0, "code": 0.7})
    assert result.verdict == Verdict.MIXED.value
    assert not result.owes_revert


def test_damage_with_no_upside_is_HARMFUL():
    result = _attr(["schema"], {"schema": 0.5, "code": 0.1}, {"schema": 0.5, "code": 0.7})
    assert result.verdict == Verdict.HARMFUL.value
    assert result.owes_revert


def test_a_change_with_no_predictions_can_never_be_EFFECTIVE():
    """Without a prediction there is nothing to have been right about.

    Letting it reach EFFECTIVE would reward filing manifests with empty `predicted_fixes` — the
    shortcut §3.1's lenient validation makes tempting.
    """
    result = _attr([], {"schema": 0.5}, {"schema": 0.0})
    assert result.verdict == Verdict.PARTIALLY_EFFECTIVE.value
    assert result.precision == 0.0


def test_the_verdict_carries_its_reasoning():
    for predicted, before, after in (
        (["s"], {"s": 0.5}, {"s": 0.0}),
        (["s"], {"s": 0.5, "c": 0.1}, {"s": 0.5, "c": 0.7}),
        (["s"], {"s": 0.5}, {"s": 0.5}),
    ):
        assert _attr(predicted, before, after).reason


# ── the scariest class ──


def test_a_regression_nobody_predicted_is_surfaced():
    """§3.1: "the scariest class, surfaced loudly"."""
    result = _attr(["schema"], {"schema": 0.5, "code": 0.1}, {"schema": 0.0, "code": 0.8})
    assert result.unattributed_regressions == ["code"]


def test_a_BRAND_NEW_failure_mode_counts_as_a_regression():
    """A cluster only in `after` is a failure the change INTRODUCED — the most important kind,
    and the one a `before`-keyed loop would miss entirely."""
    result = _attr(["schema"], {"schema": 0.5}, {"schema": 0.0, "brand_new": 0.4})
    assert "brand_new" in result.regressed
    assert "brand_new" in result.unattributed_regressions


def test_a_predicted_regression_is_not_unattributed():
    """If a proposer said a cluster would move and it moved the wrong way, that is anticipated."""
    result = _attr(["code"], {"code": 0.1}, {"code": 0.8})
    assert result.regressed == ["code"]
    assert result.unattributed_regressions == []


def test_noise_below_epsilon_is_neither_fixed_nor_regressed():
    """Treating noise as a regression would file reverts against changes that did nothing at all."""
    result = _attr(["schema"], {"schema": 0.500}, {"schema": 0.495})
    assert result.fixed == [] and result.regressed == []
    assert result.verdict == Verdict.INEFFECTIVE.value
    assert DELTA_EPS == 0.02


def test_rates_are_used_so_a_busier_week_is_not_a_regression():
    """Counts would call five failures in five hundred runs worse than five in ten."""
    outcome = Outcome(before={"s": 0.50}, after={"s": 0.10}, runs_after=500)
    assert outcome.fixed == ["s"] and outcome.regressed == []


# ── the auto-filed revert ──


def test_only_a_HARMFUL_verdict_owes_a_revert():
    """An INEFFECTIVE change is clutter; auto-filing a revert for everything that did not help would
    bury the queue — which is how the one revert that mattered gets skipped."""
    assert REVERT_VERDICTS == {Verdict.HARMFUL.value}
    for predicted, before, after in (
        (["s"], {"s": 0.5}, {"s": 0.0}),
        (["s"], {"s": 0.5}, {"s": 0.5}),
        (["s"], {"s": 0.5, "c": 0.1}, {"s": 0.0, "c": 0.7}),
    ):
        assert revert_proposal(target="t", attribution=_attr(predicted, before, after)) is None


def test_a_harmful_change_files_a_revert_that_NAMES_what_broke():
    """A proposal saying only "this made things worse" is un-reviewable: the user cannot weigh a
    rollback without knowing what broke."""
    result = _attr(["schema"], {"schema": 0.5, "code": 0.1}, {"schema": 0.5, "code": 0.7})
    proposal = revert_proposal(target="nightly-digest", attribution=result, run_ids=["r1", "r2"])
    assert proposal is not None
    assert "nightly-digest" in proposal.title
    assert "code" in proposal.body
    assert "Nobody predicted" in proposal.body


def test_a_revert_names_predictions_that_never_landed():
    result = _attr(
        ["schema", "timeout"], {"schema": 0.5, "code": 0.1}, {"schema": 0.5, "code": 0.7}
    )
    proposal = revert_proposal(target="t", attribution=result)
    assert proposal is not None
    assert "never landed" in proposal.body


def test_revert_evidence_is_deduped_and_bounded():
    result = _attr(["s"], {"s": 0.5, "c": 0.1}, {"s": 0.5, "c": 0.7})
    proposal = revert_proposal(
        target="t", attribution=result, run_ids=["r1"] * 30 + [f"x{i}" for i in range(40)]
    )
    assert proposal is not None
    assert len(proposal.evidence_refs) <= 20
    assert len(proposal.evidence_refs) == len(set(proposal.evidence_refs))


def test_a_revert_is_a_PROPOSAL_never_an_application():
    """§3.1: reverts go through the queue, "making version-pin rollback mechanical instead of
    user vigilance". Mechanical means it appears without anyone noticing — not that it applies
    itself,
    and S75's gate refuses a non-human accept regardless.
    """
    import inspect

    from personalclaw.learning import accountability

    src = inspect.getsource(accountability)
    for forbidden in ("proposals.accept(", "installer(", "atomic_write", "sqlite3"):
        assert forbidden not in src, f"accountability applies changes via {forbidden}"


def test_a_revert_proposal_serializes():
    result = _attr(["s"], {"s": 0.5, "c": 0.1}, {"s": 0.5, "c": 0.7})
    payload = revert_proposal(target="t", attribution=result).to_dict()
    assert set(payload) == {"target", "kind", "title", "body", "evidence_refs", "provenance"}
    assert payload["provenance"] == "accountability"


# ── proposer trust ──


def test_trust_is_aggregated_per_source():
    """§3.1: "the flywheel learns which of its own proposers to believe"."""
    trust = {
        t.source: t for t in proposer_trust([("refiner", "HARMFUL"), ("detector", "EFFECTIVE")])
    }
    assert set(trust) == {"refiner", "detector"}
    assert trust["refiner"].harm_rate == 1.0
    assert trust["detector"].effective_rate == 1.0


def test_harm_rate_is_over_DECIDED_not_total():
    """A proposer with many pending changes would otherwise look safer than one whose changes were
    measured — inverting the signal exactly when a new proposer starts filing."""
    records = [("p", "HARMFUL")] + [("p", "PENDING")] * 99
    trust = proposer_trust(records)[0]
    assert trust.total == 100 and trust.decided == 1
    assert trust.harm_rate == 1.0


def test_the_worst_proposer_sorts_first():
    """The useful question is which proposer to trust less, and a name-sorted list buries it."""
    records = (
        [("safe", "EFFECTIVE")] * 9 + [("risky", "HARMFUL")] * 5 + [("risky", "EFFECTIVE")] * 5
    )
    assert [t.source for t in proposer_trust(records)][0] == "risky"


def test_an_unknown_verdict_is_counted_not_dropped():
    """A verdict this module does not recognize is a drift signal; discarding it hides the drift."""
    trust = proposer_trust([("p", "WHO_KNOWS")])[0]
    assert trust.counts == {"WHO_KNOWS": 1}
    assert trust.total == 1


def test_a_proposer_with_no_decided_verdicts_has_no_rates():
    trust = ProposerTrust(source="new", counts={"PENDING": 5})
    assert trust.harm_rate == 0.0 and trust.effective_rate == 0.0


def test_trust_serializes():
    payload = proposer_trust([("refiner", "HARMFUL")])[0].to_dict()
    assert set(payload) == {
        "source",
        "counts",
        "total",
        "decided",
        "harm_rate",
        "effective_rate",
    }


def test_an_empty_history_is_handled():
    assert proposer_trust([]) == []


# ── §7: the incognito capture gate ──


def test_the_permission_gate_refuses_a_restricted_session_on_every_cadence():
    """The half that IS closed, asserted across the whole cadence enum rather than spot-checked.

    Probed with both an idle turn and a busy one with a correction: neither can teach an incognito
    session, which is the property §7 names.
    """
    from personalclaw.learning.gate import Cadence, LearningGate

    gate = LearningGate(enabled=True, is_restricted=True)
    for cadence in Cadence:
        for kwargs in ({"tool_calls": 0}, {"tool_calls": 50, "correction": True}):
            assert not gate.decide(cadence, **kwargs).allowed, f"{cadence} leaked"


def test_an_ephemeral_or_disabled_session_teaches_nothing_either():
    from personalclaw.learning.gate import Cadence, LearningGate

    for gate in (LearningGate(is_ephemeral=True), LearningGate(enabled=False)):
        for cadence in Cadence:
            assert not gate.decide(cadence, tool_calls=50, correction=True).allowed


def test_a_permitted_session_still_gates_on_worthwhileness():
    """Permission and worthwhileness are separate questions; collapsing them is what produced the
    facet-capture carve-out `gate.py` was written to remove."""
    from personalclaw.learning.gate import Cadence, LearningGate

    gate = LearningGate(enabled=True)
    assert not gate.decide(Cadence.PER_TURN, tool_calls=0).allowed
    assert gate.decide(Cadence.PER_TURN, tool_calls=0, correction=True).allowed


def test_the_uncovered_cadences_are_pinned():
    """THE coverage finding. A gate cannot suppress a path nobody routes through it.

    `SESSION_END` and `RUN_END` are declared with ZERO live call sites, so pinning the gap set makes
    wiring one — or adding a fourth cadence — a deliberate edit here rather than a silent hole.
    """
    assert assert_gate_covers_cadences() == ["RUN_END", "SESSION_END"]


def test_the_coverage_checker_does_not_find_ITSELF():
    """A defect measured while writing it.

    The first version matched its own docstring's `Cadence.SESSION_END` mention and reported ZERO
    gaps
    for two cadences that genuinely had no callers — a checker that certifies coverage by finding
    itself. Same self-referential trap S67's fire-site scan fell into.
    """
    assert assert_gate_covers_cadences(), "the checker reports full coverage, which was the bug"
