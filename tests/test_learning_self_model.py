"""S72 — the capped self-model: reinforcement-promoted, propose-don't-write (LEARN-R21 / §2.6).

The flywheel's ONLY mechanism that learns from what quietly WORKS. Every other cadence learns from
corrections and failures, so this is the one that can notice a habit that keeps succeeding — and
that asymmetry is what makes it worth constraining. §2.6 names three constraints and this suite
asserts each is enforced MECHANICALLY rather than by convention:

* propose, never install — `test_a_refused_plan_yields_no_proposal_at_all`
* bounded by construction — `test_a_full_tier_displaces_rather_than_appending`
* only a compact snapshot injects — `test_the_snapshot_is_bounded_and_drops_whole_entries`

**Measured before writing.** `user.selfmodel.*` was NOT in `vector_memory._NON_FACT_KEY_CLAUSE`, so
a behavioural principle the harness observed about its OWN working patterns would have rendered as a
FACT ABOUT THE USER. `test_the_selfmodel_prefix_is_excluded_from_fact_blocks` is the regression.
`user.*` WAS already in `_BUILTIN_PREFIXES`, so no allowlist change was needed — measuring both is
what kept this session from inventing one.
"""

from __future__ import annotations

import pytest

from personalclaw.learning.self_model import (
    CAPS,
    FACETS,
    KEY_PREFIX,
    MIN_CONFIDENCE,
    MIN_SEEN_COUNT,
    PROPOSAL_KIND,
    PROVENANCE,
    REACTION_WEIGHT,
    SNAPSHOT_FACETS,
    SNAPSHOT_MAX_CHARS,
    Entry,
    Facet,
    Observation,
    Reaction,
    Reinforcement,
    build_proposal,
    over_cap,
    plan_promotion,
    proposal_fingerprint,
    reinforce,
    snapshot,
    trim_ring,
)


def _obs(reaction=Reaction.ACCEPTED.value, *, ok=True, pattern="p", route="direct"):
    return Observation(pattern=pattern, route=route, succeeded=ok, reaction=reaction)


def _reinforced(*reactions, ok=True, pattern="p"):
    record = None
    for reaction in reactions:
        record = reinforce(record, _obs(reaction, ok=ok, pattern=pattern))
    return record or Reinforcement(pattern=pattern)


def _entries(count, *, facet="principle", confidence=0.8, seen=5):
    return [
        Entry(
            facet=facet,
            key=f"p{i}",
            body=f"principle {i}",
            confidence=confidence,
            seen_count=seen,
            created_at=f"2024-01-{i + 1:02d}",
        )
        for i in range(count)
    ]


# ── the measured leak this session closed ──


def test_the_selfmodel_prefix_is_excluded_from_fact_blocks():
    """THE regression.

    Measured before the module was written: `user.selfmodel.*` was absent from the exclusion
    clause, so a principle about the HARNESS's working patterns would render in the user-fact
    block. It is a statement about the system, not the user; only §2.6's snapshot may inject it.
    """
    from personalclaw.vector_memory import _NON_FACT_KEY_CLAUSE

    assert "user.selfmodel.%" in _NON_FACT_KEY_CLAUSE


def test_the_prefix_sits_under_an_already_allowlisted_root():
    """`user.*` was already in `_BUILTIN_PREFIXES`; measuring saved inventing an allowlist entry."""
    from personalclaw.vector_memory import _BUILTIN_PREFIXES

    assert any(KEY_PREFIX.startswith(p.rstrip("*")) for p in _BUILTIN_PREFIXES)


def test_the_prefix_is_adjacent_to_the_existing_persona_seam():
    """§2.6 puts the self-model beside `user.persona.*`, which is the same KIND of thing:
    harness-internal, agent-facing, never a user fact."""
    from personalclaw.vector_memory import _NON_FACT_KEY_CLAUSE

    assert "user.persona.%" in _NON_FACT_KEY_CLAUSE
    assert KEY_PREFIX == "user.selfmodel"


def test_an_entry_renders_a_namespaced_memory_key():
    entry = Entry(facet="principle", key="edit-over-rewrite", body="x")
    assert entry.memory_key == "user.selfmodel.principle.edit-over-rewrite"


# ── reinforcement: both thresholds, as a conjunction ──


def test_the_thresholds_are_a_conjunction_not_an_either():
    """`seen_count` alone promotes a coincidence that happened twice; confidence alone promotes one
    strongly-felt observation. Neither is evidence of a habit."""
    once = _reinforced(Reaction.ACCEPTED.value)
    assert once.confidence == 1.0 and once.seen_count == 1
    assert not once.promotable, "one perfect observation must not promote"

    twice = _reinforced(Reaction.ACCEPTED.value, Reaction.ACCEPTED.value)
    assert twice.promotable

    mixed = _reinforced(Reaction.ACCEPTED.value, Reaction.CORRECTED.value)
    assert mixed.seen_count >= MIN_SEEN_COUNT
    assert mixed.confidence < MIN_CONFIDENCE
    assert not mixed.promotable, "repetition without reliability must not promote"


def test_a_correction_outweighs_an_acceptance():
    """Being told you were wrong is stronger evidence than not being told you were wrong.

    A symmetric scale would let a habit that fails a third of the time still promote.
    """
    assert abs(REACTION_WEIGHT[Reaction.CORRECTED.value]) > REACTION_WEIGHT[Reaction.ACCEPTED.value]


def test_an_acceptance_after_a_FAILED_turn_is_not_reinforcement():
    """The user may have accepted a partial answer and moved on.

    Reading acceptance-after-failure as reinforcement is how a broken habit gets promoted.
    """
    record = _reinforced(Reaction.ACCEPTED.value, Reaction.ACCEPTED.value, ok=False)
    assert record.seen_count == 2
    assert record.confidence == 0.0
    assert not record.promotable


def test_a_neutral_observation_counts_toward_repetition_but_not_confidence():
    """The pattern DID recur; pretending otherwise would let an unobservable outcome erase the
    repetition half of the threshold."""
    record = _reinforced(Reaction.NEUTRAL.value, Reaction.NEUTRAL.value)
    assert record.seen_count == 2
    assert record.confidence == 0.0


def test_an_abandoned_thread_is_weak_negative_evidence():
    """Silence is ambiguous, so it counts against but less than a correction."""
    weight = REACTION_WEIGHT[Reaction.ABANDONED.value]
    assert weight < 0
    assert abs(weight) < abs(REACTION_WEIGHT[Reaction.CORRECTED.value])


def test_reinforce_returns_a_new_record_rather_than_mutating():
    first = _reinforced(Reaction.ACCEPTED.value)
    before = first.seen_count
    reinforce(first, _obs())
    assert first.seen_count == before


def test_a_pattern_with_no_graded_observations_has_zero_confidence():
    assert Reinforcement(pattern="p").confidence == 0.0


def test_the_documented_thresholds_match_the_plan():
    assert MIN_SEEN_COUNT == 2
    assert MIN_CONFIDENCE == pytest.approx(0.72)


# ── bounded by construction ──


def test_every_facet_has_a_cap():
    """Structurally-impossible bloat only holds if every tier is actually bounded."""
    for facet in FACETS:
        assert CAPS.get(facet, 0) > 0, f"{facet} is uncapped"


def test_the_caps_match_the_plans_numbers():
    assert CAPS["principle"] == 6
    assert CAPS["theory"] == 4
    assert CAPS["focus"] == 4


def test_promotion_under_the_cap_needs_no_displacement():
    plan = plan_promotion(
        facet="principle",
        reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 4),
        current=_entries(2),
    )
    assert plan.allowed and plan.displaces == ""


def test_a_full_tier_displaces_rather_than_appending():
    """The cap is enforced BEFORE anything is written, and names the victim.

    Refusing outright would freeze the self-model at its first six principles — the cap would
    prevent bloat by preventing learning.
    """
    plan = plan_promotion(
        facet="principle",
        reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 4),
        current=_entries(CAPS["principle"], confidence=0.80),
    )
    assert plan.allowed
    assert plan.displaces == "p0"
    assert "displace" in plan.reason


def test_a_newcomer_must_BEAT_the_weakest_not_tie_it():
    """An equal-confidence newcomer evicting a proven entry is how a tier churns between two similar
    principles forever."""
    plan = plan_promotion(
        facet="principle",
        reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 4),  # confidence 1.0
        current=_entries(CAPS["principle"], confidence=1.0),
    )
    assert not plan.allowed
    assert "must BEAT it" in plan.reason


def test_the_weakest_entry_is_chosen_by_confidence_then_uses_then_age():
    current = [
        Entry(
            facet="principle",
            key="strong",
            body="x",
            confidence=0.9,
            seen_count=9,
            created_at="2024-01-01",
        ),
        Entry(
            facet="principle",
            key="weak",
            body="x",
            confidence=0.5,
            seen_count=9,
            created_at="2024-06-01",
        ),
    ] + _entries(4, confidence=0.85)
    plan = plan_promotion(
        facet="principle",
        reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 3),
        current=current,
    )
    assert plan.displaces == "weak"


def test_an_unpromotable_pattern_is_refused_with_its_numbers():
    plan = plan_promotion(
        facet="principle", reinforcement=_reinforced(Reaction.ACCEPTED.value), current=[]
    )
    assert not plan.allowed
    assert "needs" in plan.reason and "2" in plan.reason


def test_an_unknown_facet_is_refused():
    plan = plan_promotion(
        facet="vibes", reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 4), current=[]
    )
    assert not plan.allowed
    assert "unknown facet" in plan.reason


def test_caps_are_per_facet_so_tiers_do_not_compete():
    """A full principle tier must not block a theory."""
    current = _entries(CAPS["principle"], facet="principle")
    plan = plan_promotion(
        facet="theory", reinforcement=_reinforced(*[Reaction.ACCEPTED.value] * 3), current=current
    )
    assert plan.allowed and plan.displaces == ""


def test_over_cap_catches_hand_edited_data():
    """The caps are structural for anything via `plan_promotion`, but a file on disk can say
    anything — and a self-model holding twelve principles would blow the injection budget."""
    assert over_cap(_entries(9)) == {"principle": 3}
    assert over_cap(_entries(CAPS["principle"])) == {}
    assert over_cap([]) == {}


def test_the_retrospection_ring_trims_to_cap_keeping_the_newest():
    ring = [
        Entry(
            facet="retrospection", key=f"r{i}", body=f"obs {i}", last_seen_at=f"2024-01-{i + 1:02d}"
        )
        for i in range(12)
    ]
    kept = trim_ring(ring)
    assert len(kept) == CAPS["retrospection"]
    assert kept[0].key == "r11"


def test_trimming_the_ring_leaves_other_facets_alone():
    mixed = _entries(3) + [Entry(facet="retrospection", key=f"r{i}", body="x") for i in range(20)]
    kept = trim_ring(mixed)
    assert len([e for e in kept if e.facet == "principle"]) == 3
    assert len([e for e in kept if e.facet == "retrospection"]) == CAPS["retrospection"]


# ── propose, never install ──


def test_a_refused_plan_yields_no_proposal_at_all():
    """The cap and the thresholds are enforced ON THE PATH to the queue, not after it.

    Returning an un-promotable proposal would let a caller file one by ignoring the plan.
    """
    refused = plan_promotion(
        facet="principle", reinforcement=_reinforced(Reaction.ACCEPTED.value), current=[]
    )
    assert build_proposal(facet="principle", reinforcement=_reinforced(), plan=refused) is None


def test_a_promoted_pattern_becomes_a_lesson_shaped_proposal():
    """§2.6: an accepted principle is "lessons-shaped (constraint-like, always-on)".

    Reusing `lesson_batch` rather than minting a kind means it inherits the review UI, the
    fingerprint dedup, and the decision store — a new kind would be a second review surface for
    one shape of thing.
    """
    from personalclaw.learning.proposals import Kind

    record = _reinforced(*[Reaction.ACCEPTED.value] * 4)
    plan = plan_promotion(facet="principle", reinforcement=record, current=[])
    proposal = build_proposal(facet="principle", reinforcement=record, plan=plan)
    assert proposal is not None
    assert PROPOSAL_KIND in {k.value for k in Kind}
    assert proposal.to_dict()["kind"] == PROPOSAL_KIND


def test_a_proposal_is_marked_as_observed_rather_than_corrected():
    """A reviewer must tell an OBSERVED principle from a correction-derived lesson: they are
    shaped alike and earned very differently."""
    record = _reinforced(*[Reaction.ACCEPTED.value] * 3)
    plan = plan_promotion(facet="principle", reinforcement=record, current=[])
    proposal = build_proposal(facet="principle", reinforcement=record, plan=plan)
    assert proposal is not None
    assert proposal.to_dict()["provenance"] == PROVENANCE == "observed-reinforcement"


def test_a_proposal_carries_its_reinforcement_evidence():
    """ "The system decided this about itself" is not an auditable explanation."""
    record = _reinforced(Reaction.ACCEPTED.value, Reaction.ACCEPTED.value, Reaction.ACCEPTED.value)
    plan = plan_promotion(facet="principle", reinforcement=record, current=[])
    proposal = build_proposal(facet="principle", reinforcement=record, plan=plan)
    assert proposal is not None
    assert proposal.evidence and all("after success" in line for line in proposal.evidence)
    assert proposal.seen_count == 3


def test_the_evidence_is_bounded():
    """A proposal a reviewer will not read is not evidence, it is volume."""
    record = _reinforced(*[Reaction.ACCEPTED.value] * 40)
    plan = plan_promotion(facet="principle", reinforcement=record, current=[])
    proposal = build_proposal(facet="principle", reinforcement=record, plan=plan)
    assert proposal is not None and len(proposal.evidence) <= 3


def test_a_displacing_proposal_names_who_it_would_displace():
    """Displacing an existing principle is a real loss; the user should see it named before it
    happens."""
    record = _reinforced(*[Reaction.ACCEPTED.value] * 4)
    plan = plan_promotion(
        facet="principle", reinforcement=record, current=_entries(CAPS["principle"], confidence=0.5)
    )
    proposal = build_proposal(facet="principle", reinforcement=record, plan=plan)
    assert proposal is not None and proposal.displaces == "p0"


def test_the_fingerprint_uses_the_shared_proposal_hash():
    """A second hashing scheme would make the self-model the one proposer that can re-file something
    the user already declined."""
    from personalclaw.learning.proposals import content_fingerprint

    record = _reinforced(*[Reaction.ACCEPTED.value] * 3)
    plan = plan_promotion(facet="principle", reinforcement=record, current=[])
    proposal = build_proposal(
        facet="principle", reinforcement=record, plan=plan, body="do the thing"
    )
    assert proposal is not None
    assert proposal_fingerprint(proposal) == content_fingerprint(
        PROPOSAL_KIND, f"{KEY_PREFIX}.principle", "do the thing"
    )


def test_nothing_in_this_module_writes_memory():
    """ "Never self-installed". Asserted against the SOURCE, because the property is an ABSENCE."""
    import inspect

    from personalclaw.learning import self_model

    src = inspect.getsource(self_model)
    for forbidden in ("MemoryService(", ".remember(", ".save(", "atomic_write", "sqlite3"):
        assert forbidden not in src, f"self_model writes via {forbidden}"


# ── only a compact snapshot injects ──


def test_the_snapshot_is_bounded_and_drops_whole_entries():
    """Half a principle is an instruction whose reader cannot tell it is half — the same rule
    `learning/surfacing.py` applies to lessons."""
    big = [
        Entry(facet="principle", key=f"p{i}", body="x" * 120, confidence=0.9 - i * 0.01)
        for i in range(20)
    ]
    out = snapshot(big, limit=300)
    assert len(out) <= 300
    for line in out.split("\n"):
        assert line.endswith("x") or line.endswith(":")


def test_the_default_snapshot_ceiling_is_documented():
    assert SNAPSHOT_MAX_CHARS == 700


def test_theories_render_as_explicitly_unproven():
    """A working theory that reads like a principle is worse than no theory: the model would treat a
    guess as a constraint."""
    out = snapshot(
        [Entry(facet="theory", key="t", body="You prefer terse commits.", confidence=0.6)]
    )
    assert "Unproven" in out


def test_retrospections_never_inject():
    """They are evidence for promotion, not guidance for a turn."""
    assert Facet.RETROSPECTION.value not in SNAPSHOT_FACETS
    out = snapshot([Entry(facet="retrospection", key="r", body="SECRET-HISTORY", confidence=0.9)])
    assert out == ""


def test_the_snapshot_orders_principles_by_confidence():
    entries = [
        Entry(facet="principle", key="low", body="LOW", confidence=0.5),
        Entry(facet="principle", key="high", body="HIGH", confidence=0.95),
    ]
    out = snapshot(entries)
    assert out.index("HIGH") < out.index("LOW")


def test_an_empty_self_model_injects_nothing():
    assert snapshot([]) == ""


def test_a_heading_never_renders_without_entries_under_it():
    """A dangling heading reads as a bug."""
    entries = [Entry(facet="principle", key="p", body="x" * 40, confidence=0.9)] + [
        Entry(facet="theory", key="t", body="y" * 200, confidence=0.5)
    ]
    out = snapshot(entries, limit=70)
    assert not out.endswith(":")


# ── the config gate (four-point wiring) ──


def test_the_gate_exists_and_defaults_on():
    from personalclaw.config.loader import LearningConfig

    assert "self_model_enabled" in LearningConfig.__dataclass_fields__
    assert LearningConfig().self_model_enabled is True


def test_the_gate_is_read_by_load():
    """Point (b): omission from the field-by-field mapping means the value is silently dropped."""
    import inspect

    from personalclaw.config import loader

    assert "self_model_enabled=bool(learning_data.get(" in inspect.getsource(loader)


def test_the_gate_is_runtime_editable():
    """Point (d). Live-editable because it is the one path that acts on what WORKED — a user
    who finds that presumptuous should be able to stop it without a restart."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["learning.self_model_enabled"] == {"type": "bool"}
