"""Tests for the template-creation pipeline (UP-R9, S45).

Two properties carry this module.

**A denial is a decision.** A tool the user refused in a session must never appear in the mined
template's permission signature. A miner that only counted successes would silently re-request it,
and the user would be re-asked for something they already said no to — which reads as the system
overriding them rather than forgetting.

**Over-scrubbing is worse than under-scrubbing.** A template whose prompt reads `{entity_1}
{entity_2} {entity_3}` has scrubbed away the sentence, not the entities. So the scrubber is measured
against the ways it can be too eager: sentence-initial capitals, domain acronyms, short words.
"""

import pytest

from personalclaw.workflows.template_pipeline import (
    NON_ENTITY_TOKENS,
    NUDGE_AFTER,
    NUDGE_COOLDOWN,
    PROMOTE_AFTER,
    SCOPE_LADDER,
    Candidate,
    NudgeState,
    freeze_candidate,
    mine_session,
    mined_goal,
    nudge_text,
    parameterize,
    record_reuse,
    scrub_entities,
    should_nudge,
)


def tool(name: str, **meta) -> dict:
    return {"role": "tool", "content": name, "meta": meta}


# ── session mining ──


def test_mining_counts_the_tools_the_session_actually_used():
    mined = mine_session([tool("knowledge_search"), tool("knowledge_search"), tool("web_fetch")])
    assert {t.name: t.calls for t in mined.tools} == {"knowledge_search": 2, "web_fetch": 1}


def test_tools_are_ordered_by_use():
    """A template declaring every incidentally-touched tool asks for permissions the work does not
    need, which erodes the install-consent surface with noise."""
    mined = mine_session([tool("a"), tool("b"), tool("b"), tool("b")])
    assert [t.name for t in mined.tools] == ["b", "a"]


def test_a_DENIED_tool_is_excluded_from_the_permission_signature():
    """The reason mining is worth doing at all. A miner that only counted successes would silently
    re-request something the user already refused."""
    mined = mine_session([tool("bash", approval="deny"), tool("knowledge_search")])
    assert mined.permission_signature == ["knowledge_search"]
    assert mined.denied == ["bash"]


def test_a_denial_excludes_the_tool_even_if_a_later_call_SUCCEEDED():
    """A denial is a decision about the tool, not about one call. Letting a later success override
    it would make the refusal a speed bump."""
    mined = mine_session([tool("bash", approval="deny"), tool("bash", approval="allow")])
    assert mined.permission_signature == []


@pytest.mark.parametrize("word", ["deny", "denied", "reject", "rejected"])
def test_every_denial_wording_is_caught(word):
    assert mine_session([tool("bash", decision=word)]).denied == ["bash"]


def test_an_approved_tool_is_marked_approved():
    mined = mine_session([tool("web_fetch", approval="allow")])
    assert mined.tools[0].approved is True


def test_the_signature_is_deduplicated_and_sorted():
    """It goes into a manifest. An unstable order would make two identical mines produce two
    different diffs."""
    mined = mine_session([tool("b"), tool("a"), tool("b")])
    assert mined.permission_signature == ["a", "b"]


def test_metadata_supplies_the_title():
    mined = mine_session([{"_type": "metadata", "title": "Corrupt index recovery"}])
    assert mined.title == "Corrupt index recovery"


def test_mining_is_tolerant_of_records_it_does_not_recognize():
    """A transcript is append-only history written by several code paths over time. A miner that
    raised on one unfamiliar record would fail on exactly the long sessions worth mining."""
    mined = mine_session(
        [None, "a string", {"role": "system"}, {"weird": True}, tool("web_fetch")]  # type: ignore
    )
    assert [t.name for t in mined.tools] == ["web_fetch"]


def test_an_empty_transcript_mines_nothing_rather_than_raising():
    assert mine_session([]).permission_signature == []


def test_the_goal_is_the_FIRST_user_turn():
    """Later turns are corrections and follow-ups. A goal assembled from all of them describes the
    conversation rather than the task."""
    mined = mine_session(
        [
            {"role": "user", "content": "summarize the ingest runbook"},
            {"role": "user", "content": "actually make it shorter"},
        ]
    )
    assert mined_goal(mined) == "summarize the ingest runbook"


def test_the_title_is_the_fallback_goal():
    mined = mine_session([{"_type": "metadata", "title": "Runbook summary"}])
    assert mined_goal(mined) == "Runbook summary"


def test_a_tool_record_with_no_name_is_skipped():
    assert mine_session([{"role": "tool", "content": ""}]).tools == []


# ── entity scrubbing ──


def test_a_real_entity_becomes_a_slot():
    scrubbed, mapping = scrub_entities("summarize the Northwind Trading renewal")
    assert "{entity_1}" in scrubbed
    assert mapping["entity_1"] == "Northwind Trading"


@pytest.mark.parametrize("token", ["API", "REST", "JSON", "CI", "SQL", "LLM"])
def test_a_domain_acronym_SURVIVES(token):
    """A template whose prompt says `{entity_1} endpoint` where the user wrote `REST endpoint` has
    scrubbed away the domain, not the entity."""
    scrubbed, _ = scrub_entities(f"document the {token} endpoint")
    assert token in scrubbed


def test_the_allowlist_is_the_single_point_of_truth():
    """A scrubber and a scorer with two ideas of "not an entity" disagree silently — one path
    parameterizes an acronym and the other does not, and nothing reports the difference."""
    assert "API" in NON_ENTITY_TOKENS
    assert len(set(NON_ENTITY_TOKENS)) == len(NON_ENTITY_TOKENS)


def test_a_sentence_initial_capital_is_not_an_entity():
    """Measured as the single largest source of junk slots: without this every prompt's first word
    becomes a parameter, and the launch form asks the user to name their own verb."""
    scrubbed, mapping = scrub_entities("Summarize the findings.")
    assert scrubbed == "Summarize the findings."
    assert mapping == {}


def test_a_capital_after_a_full_stop_is_not_an_entity():
    scrubbed, _ = scrub_entities("First do this. Then check the output.")
    assert "{" not in scrubbed


def test_a_very_short_capitalized_word_is_left_alone():
    scrubbed, _ = scrub_entities("compare the A run with the B run")
    assert "{" not in scrubbed


def test_the_same_entity_gets_the_SAME_slot():
    """Two mentions of one company must bind one input. Two slots would ask for the same value
    twice, and a user who fills one gets a run using the stale literal in the other."""
    scrubbed, mapping = scrub_entities("compare Northwind revenue with Northwind costs")
    assert scrubbed.count("{entity_1}") == 2
    assert len(mapping) == 1


def test_the_mapping_records_what_was_replaced():
    """It is what lets a review show the user what became a parameter. A scrubber that discarded it
    would make its own decisions unreviewable."""
    _scrubbed, mapping = scrub_entities("email Acme Corp about it")
    assert mapping == {"entity_1": "Acme Corp"}


def test_empty_text_scrubs_to_nothing():
    assert scrub_entities("") == ("", {})


# ── parameterizing a whole spec ──


def spec_with(*prompts) -> dict:
    return {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": f"s{i}", "config": {"prompt": p}}
                for i, p in enumerate(prompts)
            ],
        }
    }


def test_every_slot_is_DECLARED_as_an_input():
    """Session 42 measured both directions of this: a declared input nothing reads is a control
    that silently does nothing, and a binding with no declared input dies at run start."""
    out, mapping = parameterize(spec_with("review the Northwind Trading contract"))
    assert set(mapping) == set(out["inputs"])


def test_a_declared_slot_keeps_the_original_as_its_DEFAULT():
    """The generalized template must still run unchanged out of the box — a template that cannot
    run without being filled in is a form, not a template."""
    out, _ = parameterize(spec_with("review the Northwind Trading contract"))
    assert out["inputs"]["entity_1"]["default"] == "Northwind Trading"


def test_one_entity_across_TWO_stages_binds_one_input():
    out, mapping = parameterize(
        spec_with("research Northwind Trading", "write up Northwind Trading")
    )
    assert len(mapping) == 1
    prompts = [c["config"]["prompt"] for c in out["root"]["children"]]
    assert all("{entity_1}" in p for p in prompts)


def test_two_DIFFERENT_entities_get_two_inputs():
    out, mapping = parameterize(spec_with("compare Acme Corp with Northwind Trading"))
    assert len(mapping) == 2
    assert len(out["inputs"]) == 2


def test_a_spec_with_no_entities_declares_no_inputs():
    out, mapping = parameterize(spec_with("summarize the findings"))
    assert mapping == {}
    assert "inputs" not in out


def test_a_malformed_spec_parameterizes_to_nothing_rather_than_raising():
    assert parameterize({"root": "junk"})[1] == {}


def test_scrubbing_reaches_a_loop_body():
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 2},
            "body": {"kind": "stage", "id": "inner", "config": {"prompt": "check Acme Corp"}},
        }
    }
    out, mapping = parameterize(spec)
    assert mapping
    assert "{entity_1}" in out["root"]["body"]["config"]["prompt"]


# ── discover-then-freeze ──


def test_a_generated_spec_freezes_at_SESSION_scope():
    """A spec that parsed is not yet a spec that worked. Freezing higher would put a one-off in the
    library every future session matches against."""
    candidate = freeze_candidate({"root": {}}, "research cold starts", session_id="s1")
    assert candidate.scope == SCOPE_LADDER[0]
    assert candidate.reuses == 0


def test_the_candidate_name_is_deterministic():
    """The same goal must freeze to the same name, so a second generation updates the candidate
    rather than creating a near-duplicate beside it."""
    first = freeze_candidate({}, "research cold starts in lambda")
    second = freeze_candidate({}, "research cold starts in lambda")
    assert first.name == second.name


def test_the_candidate_remembers_the_goal_it_came_from():
    """It is what the matcher matches against. Without it the candidate is unreachable, which makes
    freezing it pointless."""
    assert freeze_candidate({}, "research cold starts").origin_goal == "research cold starts"


def test_one_reuse_does_not_promote():
    """One reuse is the same task done twice, which is not yet a pattern."""
    candidate = record_reuse(freeze_candidate({}, "g"))
    assert candidate.scope == "session"
    assert candidate.reuses == 1


def test_the_promotion_threshold_is_reached_by_use_not_by_opinion():
    candidate = freeze_candidate({}, "g")
    for _ in range(PROMOTE_AFTER):
        candidate = record_reuse(candidate)
    assert candidate.scope == "agent"


def test_promotion_is_ONE_rung_per_threshold_crossing():
    """Jumping a heavily-reused candidate straight to global would put a spec that worked a few
    times in one session into the library every future session matches against. Each rung costs
    its own PROMOTE_AFTER reuses, so the ladder is walked, never skipped."""
    candidate = freeze_candidate({}, "g")
    scopes = []
    for _ in range(PROMOTE_AFTER * 3):
        candidate = record_reuse(candidate)
        scopes.append(candidate.scope)
    # One rung per threshold crossing, in order — not a jump to the top on the first crossing.
    assert scopes == ["session", "agent", "agent", "workspace", "workspace", "global"]


def test_a_single_reuse_never_skips_a_rung():
    """The invariant behind the ladder: whatever the reuse count, one recorded reuse advances at
    most one rung. A candidate arriving with a stale high count must not teleport to global."""
    stale = Candidate(name="c", scope="session", reuses=99)
    assert record_reuse(stale).scope == "agent"


def test_the_top_rung_does_not_promote_further():
    candidate = Candidate(name="c", scope="global", reuses=PROMOTE_AFTER)
    assert candidate.promotable is False
    assert record_reuse(candidate).scope == "global"


def test_recording_a_reuse_returns_a_NEW_candidate():
    """The caller persists it. Mutating in place would make a failed write leave the in-memory
    count ahead of disk, and the candidate would promote on a reuse that was never saved."""
    original = freeze_candidate({}, "g")
    record_reuse(original)
    assert original.reuses == 0


def test_a_promoted_candidate_serializes_with_its_state():
    payload = freeze_candidate({"root": {}}, "g", session_id="s1").to_dict()
    assert payload["scope"] == "session"
    assert payload["promotable"] is False
    assert payload["session_id"] == "s1"


# ── the nudge, and its anti-nag rules ──


def test_the_nudge_waits_for_a_pattern():
    """Three, so it arrives on the repetition that proves a pattern rather than on the coincidence
    of doing something twice."""
    fires, why = should_nudge(NudgeState(shape="weekly digest", occurrences=2), turn=100)
    assert fires is False
    assert "2/3" in why


def test_the_nudge_fires_at_the_threshold():
    fires, why = should_nudge(NudgeState(shape="weekly digest", occurrences=NUDGE_AFTER), turn=100)
    assert fires
    assert "recurred" in why


def test_a_DECLINED_shape_is_settled():
    """ "No, not for this" must not become "ask me again next week about the same thing"."""
    fires, why = should_nudge(NudgeState(shape="s", occurrences=99, declined=True), turn=10_000)
    assert fires is False
    assert "declined" in why


def test_declining_one_shape_does_not_silence_others():
    """ "No, not for this" is not "never again for anything" — and a user who mutes the feature
    loses the useful nudges too."""
    declined = NudgeState(shape="a", occurrences=5, declined=True)
    other = NudgeState(shape="b", occurrences=5)
    assert should_nudge(declined, turn=100)[0] is False
    assert should_nudge(other, turn=100)[0] is True


def test_a_recently_offered_nudge_is_in_cooldown():
    """A nudge re-offered immediately is the definition of nagging."""
    state = NudgeState(shape="s", occurrences=5, last_offered_turn=90)
    fires, why = should_nudge(state, turn=95)
    assert fires is False
    assert "cooldown" in why


def test_the_cooldown_expires():
    state = NudgeState(shape="s", occurrences=5, last_offered_turn=10)
    assert should_nudge(state, turn=10 + NUDGE_COOLDOWN)[0] is True


def test_an_ACCEPTED_shape_is_never_nudged_again():
    state = NudgeState(shape="s", occurrences=99, accepted=True)
    fires, why = should_nudge(state, turn=10_000)
    assert fires is False
    assert "already saved" in why


def test_the_reason_is_returned_even_when_the_nudge_fires():
    """The anti-nag rules have to be inspectable, or a nudge that never appears is indistinguishable
    from a broken one."""
    assert should_nudge(NudgeState(shape="s", occurrences=3), turn=1)[1]


def test_the_nudge_names_the_shape_and_offers_ONE_action():
    """A nudge that oversells is one a user dismisses without reading, which costs the next one."""
    text = nudge_text(NudgeState(shape="weekly digest", occurrences=4))
    assert "weekly digest" in text
    assert text.count("?") == 1
