"""S71 — per-arm precision, threshold tuning from data, Beta-Binomial trust (LEARN-R4 / §2.5).

§7 criterion 7 is the bar, and it has three clauses: per-arm surfaced-vs-used precision is
REPORTABLE per entity kind, threshold profiles are TUNABLE FROM DATA, and a muted chip visibly
LOWERS an entity's trust posterior. Each has a test named for it below.

**Measured before writing any of it.** `learning/surfacing.py` already carried `THRESHOLD_PROFILES`
(the deliberate 0.55/0.62 split) and `learning/usage.py` already persisted
`surfaced`/`used`/`successes`/`failures`. What was missing was the middle: `Candidate` had no
`arm`, so nothing could attribute a surfacing to the path that produced it. And
`memory_push.ARM_CONFIDENCE` already existed — writing a second table diverged on the first
comparison, which is why `test_the_shipped_arm_table_is_imported_not_restated` exists.
"""

from __future__ import annotations

import pytest

from personalclaw.learning.measure import (
    ARM_CONFIDENCE,
    ARM_SPREAD_ALERT,
    ARMS,
    DEFAULT_ARM,
    LOWER_BOUND_Z,
    MAX_THRESHOLD_STEP,
    MIN_SAMPLES_FOR_TUNING,
    MUTE_WEIGHT,
    POOR_BELOW,
    PRIOR_ALPHA,
    PRIOR_BETA,
    RECENCY_BONUS,
    RICH_ABOVE,
    ArmStats,
    Posterior,
    Precision,
    apply_mute,
    arm_confidence,
    build_report,
    per_arm_precision,
    posterior_from_counts,
    propose_thresholds,
    rank_by_trust,
)


def _events(kind, arm, used, unused):
    return [{"kind": kind, "arm": arm, "used": True}] * used + [
        {"kind": kind, "arm": arm, "used": False}
    ] * unused


# ── the arm vocabulary must not fork ──


def test_the_shipped_arm_table_is_imported_not_restated():
    """THE regression for a divergence I shipped and then measured.

    `memory_push.ARM_CONFIDENCE` already defines alias/exact_name/suffix, and its docstring records
    that "how the name was recognised IS the evidence". My first draft wrote a second table with
    `exact_name` at 0.90 where the shipped one says 0.80 — two confidence scales for one arm name,
    which is exactly the drift this program keeps finding. The shipped values win.
    """
    from personalclaw.memory_push import ARM_CONFIDENCE as shipped
    from personalclaw.memory_push import RECENCY_BONUS as shipped_bonus

    for arm, confidence in shipped.items():
        assert ARM_CONFIDENCE[arm] == confidence, f"{arm} diverged from the shipped table"
    assert RECENCY_BONUS == shipped_bonus


def test_the_retrieval_arms_extend_rather_than_replace():
    """`memory_push` has no notion of embedding/keyword retrieval, so those arms are additive."""
    from personalclaw.memory_push import ARM_CONFIDENCE as shipped

    assert set(ARM_CONFIDENCE) > set(shipped)
    for arm in ("embedding", "keyword", "path", "exact_title"):
        assert arm in ARM_CONFIDENCE


def test_an_embedding_match_is_the_least_trusted_arm():
    """A nearest neighbour is a guess; the plan names it at ~0.6 for that reason."""
    assert ARM_CONFIDENCE["embedding"] == min(ARM_CONFIDENCE.values())


def test_an_unknown_arm_falls_back_to_the_weakest_not_to_zero():
    """Zero would suppress the candidate entirely, making an un-instrumented path invisible rather
    than merely weak."""
    assert arm_confidence("who-knows") == ARM_CONFIDENCE[DEFAULT_ARM]
    assert DEFAULT_ARM == "embedding"


def test_recency_is_a_bonus_on_an_arm_not_an_arm_of_its_own():
    """Recency modifies how much to trust the SAME match; it is not a way of matching."""
    assert arm_confidence("alias", recent=True) == pytest.approx(
        ARM_CONFIDENCE["alias"] + RECENCY_BONUS
    )
    assert "recency" not in ARMS


def test_confidence_is_clamped_to_one():
    """A 0.95 arm plus the bonus must not exceed certainty."""
    assert arm_confidence("alias", recent=True) <= 1.0


# ── clause 1: per-arm precision is reportable ──


def test_precision_is_reported_per_kind_and_arm():
    """ "A single scalar can't be calibrated per-arm" (§2.5)."""
    events = _events("skill", "exact_name", 18, 2) + _events("skill", "embedding", 4, 21)
    stats = {(s.kind, s.arm): s for s in per_arm_precision(events)}
    assert stats[("skill", "exact_name")].precision == pytest.approx(0.9)
    assert stats[("skill", "embedding")].precision == pytest.approx(0.16)


def test_an_unattributed_event_is_charged_to_the_weakest_arm_not_dropped():
    """Dropping would make the report describe only the instrumented paths while claiming to
    describe surfacing as a whole — the more misleading option."""
    stats = per_arm_precision([{"kind": "skill", "used": True}])
    assert len(stats) == 1
    assert stats[0].arm == DEFAULT_ARM


def test_insufficient_evidence_is_a_distinct_verdict_from_poor():
    """The two demand OPPOSITE responses: poor means tighten, insufficient means collect more.

    Collapsing them is how a threshold gets tuned on noise.
    """
    thin = ArmStats(kind="skill", arm="embedding", surfaced=3, used=0)
    assert thin.verdict == Precision.INSUFFICIENT.value
    real = ArmStats(kind="skill", arm="embedding", surfaced=40, used=4)
    assert real.verdict == Precision.POOR.value
    good = ArmStats(kind="skill", arm="alias", surfaced=40, used=36)
    assert good.verdict == Precision.GOOD.value


def test_precision_of_an_unsurfaced_arm_is_zero_not_a_division_error():
    assert ArmStats(kind="skill", arm="alias").precision == 0.0


def test_per_arm_precision_ignores_malformed_events():
    assert per_arm_precision([None, "nope", 7, {}]) is not None  # type: ignore[list-item]


def test_report_rows_are_stably_ordered():
    """An unstable order would make two runs of the same corpus render differently, which reads as
    the flywheel having learned something."""
    events = _events("skill", "embedding", 1, 1) + _events("lesson", "alias", 1, 1)
    first = [(s.kind, s.arm) for s in per_arm_precision(events)]
    second = [(s.kind, s.arm) for s in per_arm_precision(list(reversed(events)))]
    assert first == second == sorted(first)


# ── clause 2: thresholds tunable from data ──


def test_current_thresholds_are_read_from_the_live_profile_table():
    """A second copy would let a proposal recommend a move away from a value nobody uses."""
    from personalclaw.learning.surfacing import THRESHOLD_PROFILES

    proposals = {p.kind: p for p in propose_thresholds([])}
    for kind, value in THRESHOLD_PROFILES.items():
        assert proposals[kind].current == value


def test_the_deliberate_055_062_split_is_preserved():
    """The comments in `surfacing.py` record that this split was calibrated for different text
    profiles. A joint recalibration would silently discard a real decision."""
    from personalclaw.learning.surfacing import THRESHOLD_PROFILES

    assert THRESHOLD_PROFILES["skill"] == 0.55
    assert THRESHOLD_PROFILES["template"] == 0.62


def test_a_noisy_arm_proposes_a_TIGHTER_threshold():
    events = _events("skill", "embedding", 5, 45)
    # With no stats at all, nothing moves — the baseline this test contrasts against.
    proposal = next(p for p in propose_thresholds([]) if p.kind == "skill")
    assert proposal.proposed == proposal.current
    stats = per_arm_precision(events)
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert proposal.proposed > proposal.current
    assert proposal.changed


def test_an_over_strict_threshold_proposes_LOOSENING():
    stats = per_arm_precision(_events("skill", "alias", 48, 2))
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert proposal.proposed < proposal.current


def test_a_healthy_band_proposes_no_change():
    stats = per_arm_precision(_events("skill", "keyword", 30, 30))
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert not proposal.changed
    assert "healthy band" in proposal.reason


def test_a_proposal_never_moves_a_threshold_far():
    """A threshold is a calibration; a report that could swing it 0.2 per step would oscillate."""
    stats = per_arm_precision(_events("skill", "embedding", 0, 60))
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert abs(proposal.proposed - proposal.current) <= MAX_THRESHOLD_STEP + 1e-9


def test_thin_evidence_yields_a_no_change_proposal_WITH_a_reason():
    """Omission would read as "nothing to say about this kind" when the finding is "not enough data
    yet" — those lead to different decisions."""
    stats = per_arm_precision(_events("skill", "alias", 2, 1))
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert not proposal.changed
    assert str(MIN_SAMPLES_FOR_TUNING) in proposal.reason


def test_the_bands_leave_a_wide_healthy_gap():
    """A threshold that chases every fluctuation is worse than a fixed one."""
    assert RICH_ABOVE - POOR_BELOW >= 0.4


def test_a_kind_whose_arms_disagree_sharply_says_so():
    """Found while probing.

    `skill` had a 90%-precision exact_name arm and a 16% embedding arm, which average to a
    healthy-looking 49% and move nothing. The aggregate is the right input for a kind threshold (it
    gates the fused score) but it hides that — so the reason names the spread, and points at the
    own confidence as the actual fix.
    """
    stats = per_arm_precision(
        _events("skill", "exact_name", 18, 2) + _events("skill", "embedding", 4, 21)
    )
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert "disagree sharply" in proposal.reason
    assert "exact_name" in proposal.reason and "embedding" in proposal.reason


def test_a_uniform_kind_gets_no_spread_note():
    stats = per_arm_precision(_events("lesson", "keyword", 15, 10))
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "lesson")
    assert "disagree" not in proposal.reason


def test_a_one_sample_arm_does_not_trigger_a_spread_alert():
    """A 1-of-1 arm always looks like 100%, and would spread-alert against every real arm."""
    stats = per_arm_precision(
        _events("skill", "alias", 1, 0) + _events("skill", "embedding", 5, 25)
    )
    proposal = next(p for p in propose_thresholds(stats) if p.kind == "skill")
    assert "disagree" not in proposal.reason


def test_the_spread_alert_threshold_is_documented():
    assert ARM_SPREAD_ALERT == 0.4


def test_proposals_are_proposals_and_apply_nothing():
    """§2.5: recalibration happens "empirically, not by taste" — and also not automatically."""
    from personalclaw.learning.surfacing import THRESHOLD_PROFILES

    before = dict(THRESHOLD_PROFILES)
    propose_thresholds(per_arm_precision(_events("skill", "embedding", 0, 80)))
    assert THRESHOLD_PROFILES == before


# ── clause 3: trust posteriors, and a mute that visibly lowers one ──


def test_a_new_entity_starts_at_even_odds():
    """§2.5 says "start 0.50" — neither trusted nor suspected."""
    fresh = Posterior(kind="skill", entity="brand-new")
    assert fresh.mean == pytest.approx(0.5)
    assert fresh.observations == 0
    assert PRIOR_ALPHA == PRIOR_BETA == 1.0


def test_a_mute_visibly_lowers_the_posterior():
    """§7 criterion 7's third clause, asserted directly."""
    before = posterior_from_counts(kind="lesson", entity="lint", surfaced=20, used=15)
    after = apply_mute(before)
    assert after.lower_bound < before.lower_bound
    assert after.mean < before.mean


def test_a_mute_counts_as_a_full_negative_observation():
    """Anything less would make muting a gesture the numbers ignore."""
    assert MUTE_WEIGHT == 1.0
    muted = posterior_from_counts(kind="skill", entity="x", surfaced=10, used=10, mutes=1)
    unmuted = posterior_from_counts(kind="skill", entity="x", surfaced=10, used=10)
    assert muted.beta == unmuted.beta + 1.0


def test_apply_mute_returns_a_new_posterior_rather_than_mutating():
    """A mutation would let a caller drop the result and silently lose the trust change."""
    original = posterior_from_counts(kind="skill", entity="x", surfaced=10, used=8)
    beta_before = original.beta
    apply_mute(original)
    assert original.beta == beta_before


def test_a_lucky_single_hit_does_not_outrank_a_proven_entity():
    """THE reason this is Beta-Binomial and not a ratio.

    A raw used/surfaced ratio says 1.0 after one lucky hit, so a brand-new entity would outrank a
    proven one and one bad turn would condemn a good lesson.
    """
    lucky = posterior_from_counts(kind="skill", entity="lucky", surfaced=1, used=1)
    proven = posterior_from_counts(kind="skill", entity="proven", surfaced=30, used=27)
    assert lucky.precision_ratio() == 1.0  # the naive view
    assert proven.precision_ratio() < 1.0
    assert lucky.lower_bound < proven.lower_bound  # the honest one
    assert [p.entity for p in rank_by_trust([lucky, proven])] == ["proven", "lucky"]


def test_ranking_uses_the_lower_bound_so_ignorance_costs_something():
    assert LOWER_BOUND_Z > 0
    thin = posterior_from_counts(kind="skill", entity="thin", surfaced=2, used=2)
    assert thin.lower_bound < thin.mean


def test_the_ranking_tie_break_is_stable():
    a = posterior_from_counts(kind="skill", entity="aaa", surfaced=10, used=5)
    b = posterior_from_counts(kind="skill", entity="bbb", surfaced=10, used=5)
    assert [p.entity for p in rank_by_trust([b, a])] == ["aaa", "bbb"]


def test_counts_are_clamped_so_bad_data_cannot_produce_a_nonsense_posterior():
    """A `used` larger than `surfaced` is possible when a use is recorded for an entity surfaced
    before events existed."""
    weird = posterior_from_counts(kind="skill", entity="x", surfaced=5, used=99)
    assert weird.beta >= PRIOR_BETA
    assert 0.0 <= weird.lower_bound <= 1.0
    negative = posterior_from_counts(kind="skill", entity="x", surfaced=-4, used=-2, mutes=-1)
    assert negative.mean == pytest.approx(0.5)


def test_the_posterior_reads_the_counts_usage_already_persists():
    """No migration and no second store: `UsageRecord` already has surfaced/used."""
    from personalclaw.learning.usage import UsageRecord

    fields = set(UsageRecord.__dataclass_fields__)
    assert {"surfaced", "used"} <= fields


# ── the assembled report ──


def test_the_report_answers_is_the_flywheel_working():
    events = _events("skill", "alias", 18, 2) + _events("template", "embedding", 5, 45)
    usage = [
        {"kind": "skill", "entity": "deploy", "surfaced": 20, "used": 18},
        {"kind": "template", "entity": "triage", "surfaced": 50, "used": 5},
    ]
    report = build_report(events, usage=usage)
    assert report.overall_precision == pytest.approx(23 / 70)
    assert report.arms and report.proposals
    assert [p.entity for p in report.trusted] == ["deploy", "triage"]
    assert any(p.kind == "template" and p.changed for p in report.actionable)


def test_the_report_serializes_for_an_api():
    report = build_report(_events("skill", "alias", 3, 1))
    payload = report.to_dict()
    assert set(payload) >= {"overall_precision", "surfaced", "used", "arms", "proposals", "trusted"}


def test_the_trust_list_is_bounded():
    """The report is a page; every entity ever surfaced is not one."""
    usage = [
        {"kind": "skill", "entity": f"e{i}", "surfaced": 10, "used": i % 10} for i in range(50)
    ]
    assert len(build_report([], usage=usage, top=5).trusted) == 5


def test_an_empty_corpus_reports_zero_rather_than_dividing_by_it():
    report = build_report([])
    assert report.overall_precision == 0.0
    assert report.actionable == []


# ── arm attribution through the surfacing pipeline ──


def test_candidate_carries_an_arm():
    from personalclaw.learning.surfacing import Candidate

    assert "arm" in Candidate.__dataclass_fields__
    assert Candidate(kind="skill", key="k", score=0.5, l0="x").arm == ""


def test_arm_attribution_is_order_independent_through_fusion():
    """A defect found by measuring, not reading.

    With two sources finding the same entity at the same rank, RRF ties and the survivor was
    whichever source dict was iterated FIRST — so an entity matched by both exact-name and
    embedding was attributed by insertion order, so the report would credit the wrong path.
    """
    from personalclaw.learning.surfacing import Candidate, fuse

    def survivor(order):
        return fuse(
            {
                name: [Candidate(kind="skill", key="deploy", score=0.8, l0="x", arm=arm)]
                for name, arm in order
            }
        )[0].arm

    names_first = survivor([("names", "exact_name"), ("vectors", "embedding")])
    vectors_first = survivor([("vectors", "embedding"), ("names", "exact_name")])
    assert names_first == vectors_first == "exact_name"


def test_the_stronger_arm_wins_a_dedup():
    """Same rule `memory_push` already applies: being named explicitly once is not undone by also
    being a vector neighbour."""
    from personalclaw.learning.surfacing import Candidate, fuse

    out = fuse(
        {
            "weak": [Candidate(kind="skill", key="k", score=0.9, l0="x", arm="embedding")],
            "strong": [Candidate(kind="skill", key="k", score=0.9, l0="x", arm="alias")],
        }
    )
    assert out[0].arm == "alias"


def test_an_empty_arm_loses_to_a_real_one_in_either_direction():
    from personalclaw.learning.surfacing import Candidate, fuse

    for order in (("", "alias"), ("alias", "")):
        out = fuse(
            {
                f"s{i}": [Candidate(kind="skill", key="k", score=0.9, l0="x", arm=arm)]
                for i, arm in enumerate(order)
            }
        )
        assert out[0].arm == "alias"


def test_distinct_entities_keep_their_own_arms():
    from personalclaw.learning.surfacing import Candidate, fuse

    out = fuse(
        {
            "s": [
                Candidate(kind="skill", key="a", score=0.9, l0="x", arm="alias"),
                Candidate(kind="skill", key="b", score=0.8, l0="y", arm="embedding"),
            ]
        }
    )
    assert {c.key: c.arm for c in out} == {"a": "alias", "b": "embedding"}
