"""Tests for the one decay kernel.

The most important test here is the half-life continuity one: this kernel REPLACES
`preference_facets.decay`, so if the numbers don't line up, every existing facet
silently changes meaning on upgrade.
"""

import pytest

from personalclaw.learning import decay as D

# ── the curve ──


def test_the_kernel_reproduces_the_facet_stores_half_life():
    """Continuity, not coincidence.

    The facet store used `0.5 ** (age/30)`. This kernel replaces it, so 30 active
    days must still mean half strength — otherwise upgrading rewrites the meaning of
    every stored facet without touching a single row.
    """
    old = 0.5 ** (30 / 30.0)
    new = D.strength(kind="skill", active_days_since_use=30)
    assert new == pytest.approx(old, abs=1e-6)


def test_strength_starts_at_one_and_decreases_monotonically():
    assert D.strength(kind="skill", active_days_since_use=0) == pytest.approx(1.0)
    values = [D.strength(kind="skill", active_days_since_use=d) for d in (0, 10, 30, 90, 365)]
    assert values == sorted(values, reverse=True)
    assert values[-1] > 0.0  # never reaches zero — decay, not deletion


def test_negative_days_are_clamped():
    """A clock skew must not produce a strength above 1.0."""
    assert D.strength(kind="skill", active_days_since_use=-50) == pytest.approx(1.0)


def test_kind_multipliers_encode_what_endures():
    """A strategy that worked is still worth knowing; a specific failure usually
    describes a world that has moved on."""
    at60 = {
        kind: D.strength(kind=kind, active_days_since_use=60)
        for kind in ("strategy", "preference", "lesson", "skill", "procedural", "failure")
    }
    assert at60["strategy"] > at60["preference"] >= at60["lesson"] > at60["skill"]
    assert at60["skill"] > at60["procedural"] > at60["failure"]


def test_a_speculative_claim_decays_fastest():
    assert D.strength(kind="speculative", active_days_since_use=30) < D.strength(
        kind="failure", active_days_since_use=30
    )


def test_an_unknown_kind_falls_back_to_the_reference_rate():
    assert D.strength(kind="who_knows", active_days_since_use=30) == pytest.approx(
        D.strength(kind="skill", active_days_since_use=30)
    )


# ── importance as a second axis, not an exemption ──


def test_importance_slows_decay_without_stopping_it():
    """Exemption is what produces a library full of things marked important once
    and never revisited."""
    plain = D.strength(kind="skill", active_days_since_use=90)
    important = D.strength(kind="skill", active_days_since_use=90, importance=1.0)
    assert important > plain
    assert important < 1.0  # still decayed


def test_importance_is_clamped():
    assert D.strength(kind="skill", active_days_since_use=30, importance=5.0) == pytest.approx(
        D.strength(kind="skill", active_days_since_use=30, importance=1.0)
    )
    assert D.strength(kind="skill", active_days_since_use=30, importance=-1.0) == pytest.approx(
        D.strength(kind="skill", active_days_since_use=30, importance=0.0)
    )


def test_lambda_never_reaches_zero():
    """Even maximal importance leaves a decay rate — a floor, not an off switch."""
    assert D.effective_lambda("strategy", importance=1.0) > 0.0


# ── pruning needs BOTH signals ──


def test_pruning_requires_low_strength_AND_low_importance():
    cold_trivial = D.evaluate(kind="failure", active_days_since_use=200)
    assert cold_trivial.prune

    cold_important = D.evaluate(kind="failure", active_days_since_use=200, importance=0.9)
    assert not cold_important.prune


def test_a_rarely_consulted_runbook_survives():
    """Low strength alone is a false positive: a critical runbook read twice a year
    is cold by construction and must not be evicted for it."""
    verdict = D.evaluate(kind="strategy", active_days_since_use=180, importance=0.8)
    assert not verdict.prune


def test_a_hot_but_trivial_entry_is_not_pruned_while_hot():
    assert not D.evaluate(kind="failure", active_days_since_use=1).prune


# ── the sparing rules ──


def test_pinned_beats_everything():
    verdict = D.evaluate(kind="speculative", active_days_since_use=9999, pinned=True)
    assert not verdict.prune and not verdict.review
    assert verdict.reason == "pinned"


def test_user_authored_content_is_never_aged_out():
    """Deleting what the user wrote is not curation."""
    verdict = D.evaluate(kind="failure", active_days_since_use=9999, source_type="user")
    assert not verdict.prune
    assert verdict.reason == "user_authored"


def test_a_linked_neighbor_spares_an_entity():
    """Evicting one end of a chain leaves the other pointing at nothing."""
    alone = D.evaluate(kind="failure", active_days_since_use=500)
    linked = D.evaluate(kind="failure", active_days_since_use=500, linked_neighbors=2)
    assert alone.prune
    assert not linked.prune and linked.reason == "chain_spared"


def test_decayed_but_stable_becomes_a_review_not_an_archival():
    """Confident about something nobody uses is a question for the user — and the
    confidence is itself evidence they may want it back."""
    verdict = D.evaluate(kind="skill", active_days_since_use=200, stability=0.8)
    assert verdict.review and not verdict.prune
    assert verdict.reason == "decayed_but_stable"


def test_review_takes_precedence_over_pruning():
    verdict = D.evaluate(kind="failure", active_days_since_use=500, stability=0.95, importance=0.0)
    assert verdict.review and not verdict.prune


def test_a_healthy_entity_is_truthy():
    assert D.evaluate(kind="skill", active_days_since_use=1)
    assert not D.evaluate(kind="failure", active_days_since_use=500)


# ── reinforcement damping ──


def test_a_burst_of_reinforcements_counts_half():
    """Ten retrievals in one minute is one act of attention. Counting it ten times
    inflates the heat that drives eviction, so it ends up deleting the wrong things.
    """
    assert D.reinforcement_weight(10) == D.REINFORCE_DAMPED_WEIGHT
    assert D.reinforcement_weight(D.REINFORCE_WINDOW_SECS + 1) == 1.0


def test_a_first_reinforcement_is_full_weight():
    assert D.reinforcement_weight(-1) == 1.0


# ── the active-days clock ──


def test_the_clock_counts_active_days_not_wall_clock():
    """Vacation-proof: for a single-user system, "time passed" and "the user moved
    on" are different claims and only the second should age anything."""
    dates = ["2026-07-01", "2026-07-02", "2026-07-03"]
    counted = D.active_days_between(dates, "2026-06-25T00:00:00Z", "2026-07-25T00:00:00Z")
    assert counted == 3.0  # not 30


def test_no_active_days_means_no_decay():
    assert D.active_days_between([], "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z") == 0.0


def test_active_days_outside_the_window_are_excluded():
    dates = ["2026-01-01", "2026-07-02", "2026-12-01"]
    assert D.active_days_between(dates, "2026-06-01T00:00:00Z", "2026-08-01T00:00:00Z") == 1.0


def test_the_start_boundary_is_exclusive_and_the_end_inclusive():
    """The day an entity was last used is not an idle day."""
    dates = ["2026-07-01", "2026-07-02"]
    assert D.active_days_between(dates, "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z") == 1.0


def test_a_malformed_timestamp_degrades_to_zero_rather_than_raising():
    assert D.active_days_between(["2026-07-01"], "not-a-date") == 0.0
    assert D.active_days_between(["also-not-a-date"], "2026-06-01T00:00:00Z") == 0.0


def test_an_end_before_the_start_yields_zero():
    assert (
        D.active_days_between(["2026-07-01"], "2026-08-01T00:00:00Z", "2026-06-01T00:00:00Z") == 0.0
    )


def test_naive_timestamps_are_treated_as_utc():
    """Half the stored timestamps in this system carry no offset."""
    assert (
        D.active_days_between(["2026-07-02"], "2026-07-01T00:00:00", "2026-07-03T00:00:00") == 1.0
    )


# ── doctrine ──


def test_the_kernel_is_pure():
    """No clock, no store, no I/O — the caller owns the calendar. That is what makes
    it testable without freezing time and what lets the active-days clock exist."""
    first = D.strength(kind="skill", active_days_since_use=17, importance=0.3)
    second = D.strength(kind="skill", active_days_since_use=17, importance=0.3)
    assert first == second
