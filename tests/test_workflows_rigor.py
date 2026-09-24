"""Tests for the cheap end of the rigor axis (UP-R10, S45).

This module exists to keep spec-driven planning from becoming a waterfall, so its tests are mostly
about restraint: Specify emits exactly one stage, the fast path schedules its refinement gate after
the first OUTPUT rather than at the end, and the acceptance ratchet only ever grows.

The ratchet's direction is the load-bearing property. A criteria list that can shrink is one where a
later revision silently drops the check an earlier failure earned — and nothing in the diff says so,
because the spec still looks like a spec with criteria.
"""

import pytest

from personalclaw.workflows.intent import Intent, Rigor
from personalclaw.workflows.models import Node
from personalclaw.workflows.rigor import (
    CRITERIA_KEY,
    REFINE_GATE_ID,
    ArtifactRevision,
    Defect,
    is_fast,
    ratchet_criteria,
    revise_from_artifact,
    rigor_note,
    schedule_refinement,
    specify_prompt,
    specify_spec,
)
from personalclaw.workflows.validator import validate_node_tree


def seq(*children) -> dict:
    return {"root": {"kind": "sequence", "id": "root", "children": list(children)}}


def stage(node_id: str, **cfg) -> dict:
    return {"kind": "stage", "id": node_id, "config": {"prompt": "x", **cfg}}


def ids(spec: dict) -> list[str]:
    return [c["id"] for c in spec["root"]["children"]]


# ── entering the fast path ──


def test_an_explicit_request_wins_over_the_classifier():
    """`rigor: fast` is the user saying "I know this is a worse spec and I want to start anyway".
    A classifier that overrode it would argue with a decision about the user's own time."""
    assert is_fast(Intent(rigor=Rigor.DEEP), requested="fast") is True


def test_the_classifier_can_route_fast_on_its_own():
    assert is_fast(Intent(rigor=Rigor.FAST)) is True


def test_a_standard_intent_is_not_fast():
    assert is_fast(Intent(rigor=Rigor.STANDARD)) is False


def test_is_fast_survives_no_intent_at_all():
    assert is_fast(None, requested="fast") is True
    assert is_fast(None) is False


# ── the refinement gate ──


def test_the_gate_lands_after_the_first_WORK_node():
    """Refining against something built beats guessing up front. A gate at the END refines
    nothing — the work is already done by then."""
    out = schedule_refinement(seq(stage("first"), stage("second")))
    assert ids(out) == ["first", REFINE_GATE_ID, "second"]


def test_a_single_stage_plan_still_gets_a_gate():
    """This is the shape the fast path produces most often. Refusing to schedule here would make
    the mechanism inert exactly where it matters."""
    out = schedule_refinement({"root": stage("only")})
    assert out["root"]["kind"] == "sequence"
    assert ids(out) == ["only", REFINE_GATE_ID]


def test_scheduling_is_idempotent():
    """A re-planned fast plan would otherwise accumulate one gate per pass, and a plan with four
    identical approval gates is one a user learns to click through."""
    once = schedule_refinement(seq(stage("a")))
    twice = schedule_refinement(once)
    assert ids(twice).count(REFINE_GATE_ID) == 1


def test_the_gate_does_not_follow_another_GATE():
    """A gate produces a decision, not an artifact. Refining after one would refine against
    nothing new."""
    spec = seq({"kind": "gate", "id": "ask", "config": {"kind": "approval"}}, stage("work"))
    assert ids(schedule_refinement(spec)) == ["ask", "work", REFINE_GATE_ID]


def test_the_spec_records_that_it_took_the_fast_path():
    """A thin plan the user cannot attribute to the fast path reads as the planner doing badly."""
    assert schedule_refinement(seq(stage("a")))["rigor"] == Rigor.FAST.value


def test_a_scheduled_plan_still_validates():
    out = schedule_refinement(seq(stage("a")))
    errors = [
        i for i in validate_node_tree(Node.from_dict(out["root"])).issues if i.severity == "error"
    ]
    assert errors == []


def test_a_malformed_spec_is_returned_untouched():
    assert schedule_refinement({"root": "junk"}) == {"root": "junk"}


# ── Specify ──


def test_the_specify_prompt_demands_exactly_one_stage():
    """Specify exists for the case where planning costs more than doing. One that emitted a
    five-node graph would have quietly become the planner it was meant to bypass."""
    prompt = specify_prompt("look into the retry thing")
    assert "Exactly one stage" in prompt
    assert "do not add review" in prompt.lower()


def test_the_specify_prompt_forbids_broadening_the_task():
    assert "Do not broaden" in specify_prompt("x")


def test_a_specify_result_is_one_runnable_stage():
    spec = specify_spec("Summarize the retry behaviour in the ingest path as a short note.")
    assert spec["root"]["kind"] == "stage"
    assert spec["rigor"] == Rigor.FAST.value


def test_the_too_vague_sentinel_produces_NO_spec():
    """A spec built from "TOO_VAGUE" would run a stage whose prompt is the model's own refusal."""
    assert specify_spec("TOO_VAGUE") is None


def test_an_empty_instruction_produces_no_spec():
    assert specify_spec("   ") is None


def test_a_specified_spec_validates():
    spec = specify_spec("Write a short note about cold starts.")
    errors = [
        i for i in validate_node_tree(Node.from_dict(spec["root"])).issues if i.severity == "error"
    ]
    assert errors == []


# ── the append-only ratchet ──


def test_a_defects_fix_becomes_the_criterion():
    """Phrased as a requirement a judge can check, not as a bug report about one past run."""
    out = ratchet_criteria({}, [Defect(observed="no sources", fix="every claim cites a source")])
    assert out[CRITERIA_KEY] == ["every claim cites a source"]


def test_a_defect_with_no_fix_still_earns_a_criterion():
    out = ratchet_criteria({}, [Defect(observed="rambled for six paragraphs")])
    assert "does not repeat" in out[CRITERIA_KEY][0]


def test_criteria_only_GROW():
    """The whole mechanism. A spec whose criteria can shrink is one where a later revision silently
    drops the check an earlier failure earned."""
    first = ratchet_criteria({}, [Defect(observed="a", fix="check a")])
    second = ratchet_criteria(first, [Defect(observed="b", fix="check b")])
    assert second[CRITERIA_KEY] == ["check a", "check b"]


def test_the_same_defect_twice_does_not_double_the_list():
    first = ratchet_criteria({}, [Defect(observed="a", fix="check a")])
    second = ratchet_criteria(first, [Defect(observed="a", fix="check a")])
    assert second[CRITERIA_KEY] == ["check a"]


def test_two_SIMILAR_criteria_are_both_kept():
    """Two similar criteria are two checks. Collapsing them would be the planner deciding they are
    the same, which is a judgement it has no evidence for."""
    out = ratchet_criteria(
        {},
        [
            Defect(fix="cites a source", observed=""),
            Defect(fix="cites a PRIMARY source", observed=""),
        ],
    )
    assert len(out[CRITERIA_KEY]) == 2


def test_no_defects_adds_no_criteria_key():
    """An empty criteria list reads as "nothing is required", which is a claim about the plan."""
    assert CRITERIA_KEY not in ratchet_criteria({}, [])


def test_the_original_spec_is_not_mutated():
    spec = {}
    ratchet_criteria(spec, [Defect(fix="check a", observed="")])
    assert spec == {}


# ── revise-spec-from-artifact ──


def test_a_revision_ratchets_and_records_its_provenance():
    out = revise_from_artifact(
        {}, ArtifactRevision(from_run="run-1", defects=[Defect(fix="check a", observed="")])
    )
    assert out[CRITERIA_KEY] == ["check a"]
    assert out["extra"]["revised_from_runs"] == ["run-1"]


def test_the_same_run_is_recorded_once():
    once = revise_from_artifact({}, ArtifactRevision(from_run="run-1"))
    twice = revise_from_artifact(once, ArtifactRevision(from_run="run-1"))
    assert twice["extra"]["revised_from_runs"] == ["run-1"]


def test_revision_from_an_artifact_does_NOT_edit_nodes():
    """Node edits go through `revision.merge_patches`, whose merge-by-id is what guarantees an
    untouched stage cannot drift. A second edit path here would be a second chance to silently
    rewrite a stage nobody complained about."""
    spec = seq(stage("a"), stage("b"))
    out = revise_from_artifact(
        spec,
        ArtifactRevision(from_run="r", defects=[Defect(fix="check", observed="", node_id="a")]),
    )
    assert out["root"] == spec["root"]


def test_the_revision_payload_separates_facts_from_judgements():
    """The run's output is a fact, the user's reaction is a judgement, and the criteria are the
    durable residue. Merged, a defect the user reported would be indistinguishable from one the
    system inferred."""
    payload = ArtifactRevision(
        from_run="r", reaction="too shallow", defects=[Defect(observed="thin", fix="go deeper")]
    ).to_dict()
    assert payload["reaction"] == "too shallow"
    assert payload["criteria"] == ["go deeper"]
    assert payload["defects"][0]["observed"] == "thin"


# ── the rigor note ──


@pytest.mark.parametrize(
    "intent,requested,expected",
    [
        (Intent(rigor=Rigor.FAST, reason="deadline"), "", "Fast path"),
        (Intent(rigor=Rigor.STANDARD), "fast", "you asked for it"),
        (Intent(rigor=Rigor.DEEP, reason="complex"), "", "Deep path"),
        (Intent(rigor=Rigor.TRIVIAL), "", "No workflow needed"),
        (Intent(rigor=Rigor.STANDARD, reason="default"), "", "Standard path"),
    ],
)
def test_the_note_says_which_path_ran_and_why(intent, requested, expected):
    """A user who got a thin plan needs to know it was the fast path; one who got interrogated
    needs to know what earned it. Unexplained rigor is the same legibility failure either way."""
    assert expected in rigor_note(intent, requested=requested)


def test_the_fast_note_explains_where_refinement_HAPPENS():
    """Otherwise "fast path" reads as "we skipped the questions" with nothing said about what
    replaces them."""
    note = rigor_note(Intent(rigor=Rigor.FAST, reason="deadline"))
    assert "refinement gate after the first output" in note


# ── the tool's own vocabulary ──


@pytest.mark.parametrize("word", ["fast", "minimal", "FAST", " Minimal "])
def test_the_tools_published_rigor_WORD_enters_the_fast_path(word):
    """Measured: `workflow_plan`'s vocabulary is `minimal`/`standard`/`deep`, and matching only the
    literal `fast` made the whole fast path inert from the one surface that can request it — a
    caller asking for `minimal` got the standard path plus a note saying so."""
    assert is_fast(Intent(rigor=Rigor.STANDARD), requested=word) is True


def test_an_explicit_request_does_not_quote_the_CLASSIFIERS_contradicting_reason():
    """Measured: an explicit `minimal` printed "Fast path (rigor=standard …)", so the note
    contradicted its own headline and read as a router bug rather than as an honored request."""
    note = rigor_note(Intent(rigor=Rigor.STANDARD, reason="rigor=standard"), requested="minimal")
    assert "you asked for it" in note
    assert "rigor=standard" not in note


def test_an_unrecognized_rigor_word_does_not_enter_the_fast_path():
    """Substituting the fast path for an invalid value would give a caller something they did not
    ask for with no indication — the same silent-substitution failure the plan tool rejects."""
    assert is_fast(Intent(rigor=Rigor.STANDARD), requested="thorough") is False
