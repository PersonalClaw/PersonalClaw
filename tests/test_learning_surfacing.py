"""Tests for the ranked slot allocator.

The load-bearing test is `test_a_lesson_is_never_truncated`. Today `context.py`
character-truncates the lesson block when it runs long — lessons are the user's own
corrections, the most authoritative content in the prompt, and they were the block
being cut because whoever appended last won.
"""

import pytest

from personalclaw.learning.surfacing import (
    AUTHORITY_PREAMBLE,
    ENTITY_PRIORS,
    L2_MAX_ITEMS,
    MAX_PER_SOURCE,
    THRESHOLD_PROFILES,
    Candidate,
    Tier,
    allocate,
    classify_intent,
    count_tokens,
    fuse,
    query_overlap,
    score_candidate,
)


def cand(kind: str, key: str, score: float, l0: str, l1: str = "", l2: str = "", **kw) -> Candidate:
    return Candidate(kind=kind, key=key, score=score, l0=l0, l1=l1, l2=l2, **kw)


# ── entry gates stay per-entity ──


def test_the_calibrated_thresholds_are_preserved():
    """The 0.55/0.62 split was calibrated for different text profiles, and the code
    comments document it. A single joint threshold would silently recalibrate both.
    """
    assert THRESHOLD_PROFILES["skill"] == 0.55
    assert THRESHOLD_PROFILES["route"] == 0.62


def test_the_skill_threshold_matches_the_engine_it_replaces():
    """Continuity: if this number drifts, every existing skill's surfacing changes."""
    from personalclaw.skills.surfacing import DEFAULT_SEMANTIC_THRESHOLD

    assert THRESHOLD_PROFILES["skill"] == DEFAULT_SEMANTIC_THRESHOLD


def test_entity_priors_stay_near_one():
    """A prior far from 1.0 lets source identity outrank relevance — which is the
    per-family allocation this replaces, wearing a ranking costume."""
    for kind, prior in ENTITY_PRIORS.items():
        assert 0.9 <= prior <= 1.1, kind


# ── salience ──


def test_query_overlap_is_bounded_and_needs_no_embedder():
    assert query_overlap("parse the config", "parse the config file") == pytest.approx(1.0)
    assert query_overlap("parse config", "unrelated words entirely") == 0.0
    assert query_overlap("", "anything") == 0.0
    assert query_overlap("anything", "") == 0.0


def test_short_tokens_are_ignored_in_overlap():
    """Two-letter tokens match everything and mean nothing."""
    assert query_overlap("do it up", "do it up") == 0.0


def test_a_path_match_outranks_a_higher_similarity_score():
    """A candidate naming a file this turn touched is deterministically relevant; no
    similarity score should be able to argue with that."""
    query = "fix the timeout in engine.py"
    generic = cand("memory", "generic", 0.9, "a high-scoring but unrelated memory")
    pathy = cand("lesson", "fix", 0.4, "engine.py timeouts need heartbeats", path_match=True)
    assert score_candidate(pathy, query) > score_candidate(generic, query)


def test_rank_decay_stops_one_source_filling_the_pool():
    first = cand("skill", "a", 0.8, "parsing helper")
    later = cand("skill", "b", 0.8, "parsing helper")
    later.source_rank = 5
    assert score_candidate(first, "parsing") > score_candidate(later, "parsing")


def test_scores_are_clamped():
    """A provider returning 1.7 must not buy unbounded salience."""
    absurd = cand("memory", "x", 1.7, "text")
    sane = cand("memory", "y", 1.0, "text")
    assert score_candidate(absurd, "text") == score_candidate(sane, "text")


# ── intent ──


@pytest.mark.parametrize(
    "query,expected",
    [
        ("why does the build fail", "debug"),
        ("there is a regression in the parser", "debug"),
        ("what approach should we take", "ideation"),
        ("explore the trade-offs", "ideation"),
        ("add a field to the model", "default"),
        ("", "default"),
    ],
)
def test_intent_is_classified_lexically(query, expected):
    """Cheap on purpose: paying a model call to decide how to weight a model call is
    a cost with no ceiling."""
    assert classify_intent(query) == expected


def test_debug_intent_weights_overlap_more_heavily():
    """Debugging wants the specific; ideation wants the durable."""
    item = cand("memory", "x", 0.2, "the parser fails on empty input")
    query = "why does the parser fail"
    assert score_candidate(item, query, "debug") > score_candidate(item, query, "ideation")


# ── fusion and diversification ──


def test_one_source_cannot_exceed_the_diversification_cap():
    """Applied BEFORE trimming: trimming first lets a rich source fill every slot and
    the cap then has nothing left to spread."""
    sources = {
        "skills": [cand("skill", f"s{i}", 0.9, f"skill {i} parsing") for i in range(10)],
        "lessons": [cand("lesson", "l1", 0.5, "one lesson about parsing")],
    }
    for items in sources.values():
        for item in items:
            item.salience = score_candidate(item, "parsing")
    fused = fuse(sources)
    skills = [c for c in fused if c.kind == "skill"]
    assert len(skills) == MAX_PER_SOURCE
    assert any(c.kind == "lesson" for c in fused)


def test_lessons_are_exempt_from_the_diversification_cap():
    """Found by driving the real dev home: the cap silently dropped a 4th lesson
    while 3588 of 4000 tokens sat unused.

    Diversification exists to stop a RICH source crowding out a sparse one. It was
    never meant to ration the user's own corrections — the thing the whole slot
    policy exists to protect. Lessons are bounded by the budget, not by a quota.
    """
    lessons = [cand("lesson", f"l{i}", 0.9, f"lesson {i} about the gate") for i in range(6)]
    skills = [cand("skill", f"s{i}", 0.9, f"skill {i} about the gate") for i in range(6)]
    for items in (lessons, skills):
        for item in items:
            item.salience = score_candidate(item, "gate")
    fused = fuse({"lessons": lessons, "skills": skills})
    assert len([c for c in fused if c.kind == "lesson"]) == 6
    assert len([c for c in fused if c.kind == "skill"]) == MAX_PER_SOURCE


def test_every_lesson_reaches_the_prompt_when_the_budget_allows():
    lessons = [cand("lesson", f"l{i}", 0.9, f"- lesson {i}: run the gate") for i in range(5)]
    context = [cand("context", f"c{i}", 0.95, f"ctx {i} " * 8) for i in range(6)]
    alloc = allocate({"l": lessons, "c": context}, query="gate", budget_tokens=4000)
    included = [key for kind, key, _tier in alloc.included if kind == "lesson"]
    assert len(included) == 5
    assert alloc.headroom > 0  # and there was room to spare


def test_fusion_deduplicates_the_same_entity_from_two_sources():
    shared = cand("skill", "dup", 0.8, "shared skill")
    other = cand("skill", "dup", 0.8, "shared skill")
    fused = fuse({"a": [shared], "b": [other]})
    assert len(fused) == 1


def test_fusion_of_nothing_is_empty():
    assert fuse({}) == []
    assert fuse({"a": []}) == []


# ── token counting ──


def test_the_char_fallback_is_the_live_path():
    """tiktoken is not a dependency, so the fallback is what actually runs — and it
    over-estimates slightly, which is the safe direction for a budget."""
    assert count_tokens("") == 0
    assert count_tokens("a" * 400) == 100
    assert count_tokens("x") == 1


# ── the crowd-out bug this replaces ──


def test_a_lesson_is_never_truncated():
    """The bug: `context.py` cuts the lesson block mid-text when it runs long.

    Lessons are the user's own corrections — the most authoritative content in the
    prompt — and they were the block being cut, because whoever appended last won.
    """
    lesson = cand("lesson", "must-survive", 0.95, "LESSON: always run make lint before pushing")
    context = [
        cand("context", f"ctx{i}", 0.9, "a context one-liner that is fairly long " * 3)
        for i in range(5)
    ]
    alloc = allocate({"l": [lesson], "c": context}, query="lesson lint", budget_tokens=120)
    included = {key for _kind, key, _tier in alloc.included}
    assert "must-survive" in included
    assert alloc.truncated_slot == "retrieved_context"  # the sacrifice went elsewhere


def test_only_the_sacrificial_slot_is_trimmed():
    context = [cand("context", f"c{i}", 0.9, "context " * 20) for i in range(8)]
    alloc = allocate({"c": context}, query="context", budget_tokens=150)
    assert alloc.truncated_slot == "retrieved_context"


def test_an_oversized_item_skips_rather_than_truncates():
    """Half a lesson is worse than no lesson: the reader cannot tell it is half."""
    huge = cand("lesson", "huge", 0.99, "X " * 400)
    alloc = allocate({"l": [huge]}, query="x", budget_tokens=60, include_preamble=False)
    assert alloc.included == []
    assert alloc.skipped_oversized == ["huge"]
    assert alloc.text == ""  # nothing partial was emitted


def test_the_lesson_slot_outranks_the_context_slot():
    lesson = cand("lesson", "lsn", 0.5, "a lesson line")
    context = cand("context", "ctx", 0.99, "a context line")
    alloc = allocate({"a": [lesson], "b": [context]}, query="line", budget_tokens=4000)
    order = [key for _kind, key, _tier in alloc.included]
    assert order.index("lsn") < order.index("ctx")


# ── tiered rendering ──


def test_l2_is_rationed_to_the_very_top():
    """L2 is expensive: only for items close to the top, and never many."""
    peers = [cand("memory", f"m{i}", 0.95, f"m{i} l0", f"m{i} l1", f"m{i} FULL") for i in range(6)]
    alloc = allocate({"m": peers}, query="m0 m1 m2 m3 m4 m5", budget_tokens=8000)
    l2s = [key for _kind, key, tier in alloc.included if tier is Tier.L2]
    assert len(l2s) <= L2_MAX_ITEMS


def test_an_item_far_from_the_top_gets_no_l2():
    top = cand("memory", "top", 1.0, "top l0", "top l1", "top FULL")
    weak = cand("memory", "weak", 0.05, "weak l0", "weak l1", "weak FULL")
    alloc = allocate({"m": [top, weak]}, query="top", budget_tokens=8000)
    tiers = {key: tier for _kind, key, tier in alloc.included}
    assert tiers["weak"] is not Tier.L2


def test_degradation_happens_before_dropping():
    """Lower the tier before removing an item — a shorter form may still fit."""
    items = [
        cand("memory", f"m{i}", 0.9, f"m{i} short", "a much longer l1 body " * 8) for i in range(4)
    ]
    alloc = allocate({"m": items}, query="m0 m1 m2 m3", budget_tokens=200)
    assert alloc.degraded  # something was shortened rather than dropped


def test_a_candidate_falls_back_through_the_tiers():
    item = cand("skill", "s", 0.9, "just an l0")
    assert item.text(Tier.L2) == "just an l0"  # no l2/l1 → l0
    assert item.text(Tier.L1) == "just an l0"


# ── the near-miss catalogue ──


def test_dropped_items_are_catalogued_rather_than_vanishing():
    """A dropped item the model never hears about is a silent gap it cannot ask
    about.

    The budget has to be tight enough that even L0 doesn't fit for the tail — the
    diversification cap already limits each source to `MAX_PER_SOURCE`, so a loose
    budget simply admits everything and nothing is dropped.
    """
    lesson = cand("lesson", "keep", 0.99, "keep this lesson")
    context = [cand("context", f"c{i}", 0.9, f"context item {i} " * 12) for i in range(6)]
    alloc = allocate(
        {"l": [lesson], "c": context}, query="keep", budget_tokens=60, include_preamble=False
    )
    assert alloc.near_misses
    # The lesson still survived — the sacrifice came from the sacrificial slot.
    assert "keep" in {key for _kind, key, _tier in alloc.included}


def test_the_catalogue_only_renders_when_it_fits():
    """Precedence: the catalogue is a courtesy, not a reason to blow the budget."""
    context = [cand("context", f"c{i}", 0.9, "x " * 60) for i in range(10)]
    alloc = allocate({"c": context}, query="x", budget_tokens=90)
    assert alloc.used_tokens <= alloc.budget_tokens


# ── the authority preamble ──


def test_the_authority_preamble_is_rendered():
    """Counters a measured failure: perfect injection, and the agent re-searches
    everything anyway."""
    alloc = allocate({"m": [cand("memory", "m", 0.9, "a memory")]}, query="memory")
    assert "AUTHORITATIVE" in alloc.text
    assert "do NOT re-derive" in alloc.text


def test_the_preamble_states_the_conflict_order():
    assert "lessons win" in AUTHORITY_PREAMBLE
    assert "training is last" in AUTHORITY_PREAMBLE


def test_the_preamble_can_be_omitted():
    alloc = allocate(
        {"m": [cand("memory", "m", 0.9, "a memory")]}, query="memory", include_preamble=False
    )
    assert "AUTHORITATIVE" not in alloc.text


# ── budget accounting ──


def test_the_budget_is_never_exceeded():
    items = [cand("memory", f"m{i}", 0.9, f"item {i} " * 10) for i in range(30)]
    for budget in (50, 200, 1000):
        alloc = allocate({"m": items}, query="item", budget_tokens=budget)
        assert alloc.used_tokens <= budget, budget


def test_headroom_is_reported():
    alloc = allocate({"m": [cand("memory", "m", 0.9, "short")]}, query="short", budget_tokens=500)
    assert alloc.headroom == 500 - alloc.used_tokens


def test_an_empty_pool_is_not_an_error():
    alloc = allocate({}, query="anything", budget_tokens=100)
    assert alloc.text == "" and alloc.used_tokens == 0


def test_a_zero_budget_emits_nothing_rather_than_raising():
    alloc = allocate(
        {"m": [cand("memory", "m", 0.9, "text")]},
        query="text",
        budget_tokens=0,
        include_preamble=False,
    )
    assert alloc.used_tokens == 0


# ── slot ordering matches the render it takes over ──


def test_the_slot_order_matches_the_existing_ambient_render():
    """The order `context.py build_session_context` already assembles becomes the
    contract, so four families can no longer independently accrete weight."""
    from personalclaw.learning.surfacing import SLOT_ORDER

    names = [name for name, _priority, _sacrificial in SLOT_ORDER]
    assert names.index("lessons") < names.index("retrieved_context")
    assert names.index("system") == 0


def test_exactly_one_slot_is_sacrificial():
    from personalclaw.learning.surfacing import SLOT_ORDER

    sacrificial = [name for name, _p, sac in SLOT_ORDER if sac]
    assert sacrificial == ["retrieved_context"]


# ── the live lesson block: the policy, now enforced by the ONE budget (S80) ──
#
# These five tests originally covered `context._fit_lessons`, the char cap S71 added to stop the
# lesson block being sliced mid-sentence. S80 replaced that cap with the real budget
# (`learning.ambient`), so the cap is gone — but the PROPERTIES it protected are exactly what the
# budget must still guarantee, so they are migrated rather than deleted. The two that asserted the
# cap's own withheld-count WORDING are re-expressed against the allocator's near-miss catalogue,
# which is the same idea in the mechanism that survived.


def _lesson_block(count: int = 20) -> str:
    return "\n".join(
        f"- Lesson {i}: never deploy without running the full test suite" for i in range(count)
    )


def test_the_lesson_block_drops_whole_lessons_not_characters():
    """The bug this fixed: `lessons_ctx[:cap]` cut the final lesson mid-sentence.

    "Never deploy without" reads as an instruction, and it is not the one the user
    gave. A half-rendered correction is worse than an absent one because the reader
    cannot tell it is half.
    """
    from personalclaw.learning import ambient

    block = _lesson_block()
    alloc = ambient.render(lessons=block, budget_tokens=80)
    kept = [line for line in alloc.text.split("\n") if line.startswith("- Lesson")]
    assert kept  # something survived
    assert all(line.endswith("full test suite") for line in kept)  # none are partial


def test_the_dropped_lessons_are_still_counted():
    """Silent omission of the user's own corrections is the worst version of this.

    The char cap said so in prose ("…[N more lesson(s) withheld]"); the budget says so in the
    allocation's near-miss list, which `ambient.report` surfaces. Same guarantee, one mechanism.
    """
    from personalclaw.learning import ambient

    alloc = ambient.render(lessons=_lesson_block(20), budget_tokens=80)
    assert alloc.near_misses


def test_the_dropped_count_is_accurate():
    from personalclaw.learning import ambient

    alloc = ambient.render(lessons=_lesson_block(20), budget_tokens=80)
    kept = len([k for kind, k, _t in alloc.included if kind == "lesson"])
    assert kept + len(alloc.near_misses) == 20


def test_a_block_under_the_budget_keeps_every_lesson():
    from personalclaw.learning import ambient

    alloc = ambient.render(lessons=_lesson_block(3), budget_tokens=100_000)
    for i in range(3):
        assert f"- Lesson {i}:" in alloc.text
    assert not alloc.near_misses


def test_even_an_impossible_budget_emits_no_partial_lesson():
    from personalclaw.learning import ambient

    alloc = ambient.render(lessons=_lesson_block(20), budget_tokens=6)
    assert not [line for line in alloc.text.split("\n") if line.startswith("- Lesson")]
    assert alloc.text == "" or "Lesson" not in alloc.text
