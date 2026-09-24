"""Tests for plan revision and the review surface (UP-R4/R7, S43).

The property that carries this module is that **an untouched stage cannot change**. A revision that
regenerates the whole spec re-rolls the dice on every stage nobody complained about, and the drift
is silent because the new spec is also plausible. So the tests here are mostly about what a merge
REFUSES to do: reject a patch naming a node that does not exist, refuse to turn a bad replace into
an add, and leave every unmentioned node byte-identical.
"""

import copy
import json

import pytest

from personalclaw.workflows.contracts import derive_contracts, type_decisions
from personalclaw.workflows.intent import classify
from personalclaw.workflows.matcher import MatchResult
from personalclaw.workflows.models import Node
from personalclaw.workflows.revision import (
    MAX_SKETCHES,
    NO_UPDATE,
    Patch,
    SketchStore,
    announce_block,
    estimate_cost,
    inferred_chips,
    merge_patches,
    parse_revision,
    plan_markdown,
)
from personalclaw.workflows.validator import validate_node_tree


def spec() -> dict:
    return {
        "name": "p",
        "inputs": {"topic": {"default": "cold starts"}, "depth": {"default": "exhaustive"}},
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "stage",
                    "id": "research",
                    "config": {"prompt": "research {{inputs.topic}}", "model_tier": "standard"},
                },
                {
                    "kind": "stage",
                    "id": "write",
                    "config": {"prompt": "write it", "model_tier": "standard"},
                },
                {"kind": "gate", "id": "check", "config": {"kind": "judge", "prompt": "good?"}},
            ],
        },
    }


def kids(merged: dict) -> dict:
    return {c["id"]: c for c in merged["root"]["children"]}


# ── the NO_UPDATE fast path ──


@pytest.mark.parametrize(
    "raw", [NO_UPDATE, f"  {NO_UPDATE}  ", {"op": "NO_UPDATE"}, {"no_update": True}]
)
def test_the_sentinel_short_circuits(raw):
    """Checked as a LITERAL before any parse — a sentinel that had to be JSON-decoded would cost
    the thing it exists to avoid."""
    patches, no_update = parse_revision(raw)
    assert no_update
    assert patches == []


def test_a_sentinel_merge_is_explicitly_unchanged():
    """ "Nothing changed" must be distinguishable from "the merge did nothing it meant to"."""
    result = merge_patches(spec(), [])
    assert result.unchanged
    assert result.applied == []


def test_garbage_is_not_mistaken_for_the_sentinel():
    patches, no_update = parse_revision("some prose the model wrote instead")
    assert not no_update
    assert patches == []


# ── merge by id ──


def test_a_replace_changes_only_its_own_node():
    """The property the whole module exists for."""
    original = spec()
    patches, _ = parse_revision(
        {
            "patches": [
                {
                    "op": "replace",
                    "node_id": "write",
                    "node": {
                        "kind": "stage",
                        "id": "write",
                        "config": {"prompt": "write it", "model_tier": "fast"},
                    },
                }
            ]
        }
    )
    result = merge_patches(original, patches)
    merged = kids(result.spec)
    assert merged["write"]["config"]["model_tier"] == "fast"
    assert merged["research"] == original["root"]["children"][0]
    assert merged["check"] == original["root"]["children"][2]


def test_the_original_spec_is_not_mutated():
    """A merge that edited its input would make a rejected revision unrecoverable."""
    original = spec()
    snapshot = copy.deepcopy(original)
    merge_patches(original, [Patch(op="remove", node_id="write")])
    assert original == snapshot


def test_a_replace_naming_a_ghost_is_rejected_not_added():
    """A patch naming a node that does not exist is a MODEL error. Applying it as an add would put
    a stage in the plan the user never asked for."""
    result = merge_patches(
        spec(), [Patch(op="replace", node_id="ghost", node={"kind": "stage", "id": "ghost"})]
    )
    assert result.applied == []
    assert any("does not exist" in r for r in result.rejected)
    assert len(result.spec["root"]["children"]) == 3


def test_a_rejection_lists_the_ids_that_do_exist():
    """A repair note that names the available ids is actionable; one that says "not found" is no"""
    result = merge_patches(
        spec(), [Patch(op="replace", node_id="ghost", node={"kind": "stage", "id": "g"})]
    )
    assert "research" in result.rejected[0]


def test_a_duplicate_add_is_rejected():
    result = merge_patches(
        spec(), [Patch(op="add", node_id="write", node={"kind": "stage", "id": "write"})]
    )
    assert any("use replace" in r for r in result.rejected)


def test_an_add_with_an_anchor_lands_after_it():
    """Insertion-only semantics: a reviewer's suggestion becomes an attributed step rather than
    a rewrite of someone else's."""
    result = merge_patches(
        spec(),
        [
            Patch(
                op="add",
                node_id="verify",
                after="research",
                node={"kind": "stage", "id": "verify", "config": {"prompt": "check sources"}},
            )
        ],
    )
    assert [c["id"] for c in result.spec["root"]["children"]] == [
        "research",
        "verify",
        "write",
        "check",
    ]


def test_an_add_naming_a_missing_anchor_is_rejected():
    """Appending it instead would put the step somewhere the user did not ask for, which is the
    kind of silent relocation nobody reviews."""
    result = merge_patches(
        spec(), [Patch(op="add", node_id="x", after="nowhere", node={"kind": "stage", "id": "x"})]
    )
    assert result.applied == []
    assert any("nowhere" in r for r in result.rejected)


def test_a_replace_cannot_rename_its_node():
    """A model that renamed the node while replacing it would break every binding pointing at it —
    and the user asked to change the stage, not to re-address it."""
    result = merge_patches(
        spec(),
        [
            Patch(
                op="replace", node_id="write", node={"kind": "stage", "id": "RENAMED", "config": {}}
            )
        ],
    )
    assert "write" in kids(result.spec)
    assert "RENAMED" not in kids(result.spec)


def test_a_revision_carries_its_provenance():
    """A revised plan has to show who asked for what, or a review cannot tell an original step from
    a requested one."""
    result = merge_patches(
        spec(),
        [
            Patch(
                op="replace",
                node_id="write",
                node={"kind": "stage", "id": "write", "config": {}},
                reason="too vague",
                requested_by="reviewer",
            )
        ],
    )
    extra = kids(result.spec)["write"]["extra"]
    assert extra["revised_because"] == "too vague"
    assert extra["revised_by"] == "reviewer"


def test_an_annotation_records_a_comment_without_changing_the_spec():
    """ "I do not like stage 3" is useful even when the user has not said what to do about it."""
    original = spec()
    result = merge_patches(original, [Patch(op="annotate", node_id="write", reason="too vague")])
    merged = kids(result.spec)
    assert merged["write"]["config"] == original["root"]["children"][1]["config"]
    assert merged["write"]["extra"]["review_notes"][0]["comment"] == "too vague"


def test_an_annotation_on_a_ghost_is_rejected():
    result = merge_patches(spec(), [Patch(op="annotate", node_id="ghost", reason="x")])
    assert result.applied == []


def test_a_remove_drops_only_its_node():
    result = merge_patches(spec(), [Patch(op="remove", node_id="write")])
    assert set(kids(result.spec)) == {"research", "check"}


def test_a_replace_reaches_a_loop_body():
    """A node hanging off `body` rather than `children` still has to be addressable, or a revision
    cannot touch the inside of a loop."""
    looped = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 2},
            "body": {"kind": "stage", "id": "inner", "config": {"prompt": "old"}},
        }
    }
    result = merge_patches(
        looped,
        [
            Patch(
                op="replace",
                node_id="inner",
                node={"kind": "stage", "id": "inner", "config": {"prompt": "new"}},
            )
        ],
    )
    assert result.applied == ["inner"]
    assert result.spec["root"]["body"]["config"]["prompt"] == "new"


def test_an_invalid_op_is_dropped_at_parse_time():
    patches, _ = parse_revision({"patches": [{"op": "obliterate", "node_id": "write"}]})
    assert patches == []


def test_a_patch_with_no_node_id_is_dropped():
    patches, _ = parse_revision({"patches": [{"op": "replace", "node": {"kind": "stage"}}]})
    assert patches == []


def test_a_merged_spec_still_validates():
    """A revision that produced an unrunnable spec would be worse than one that refused."""
    result = merge_patches(
        spec(),
        [
            Patch(
                op="add",
                node_id="extra",
                after="research",
                node={"kind": "stage", "id": "extra", "config": {"prompt": "more"}},
            )
        ],
    )
    errors = [
        i
        for i in validate_node_tree(Node.from_dict(result.spec["root"])).issues
        if i.severity == "error"
    ]
    assert errors == []


# ── TTL'd sketches ──


def test_a_fresh_sketch_is_readable():
    store = SketchStore(ttl=100)
    store.put("a", spec(), now=1000.0)
    sketch, reason = store.get("a", now=1050.0)
    assert sketch is not None and reason == ""


def test_an_expired_sketch_says_EXPIRED_not_missing():
    """The user's revision was reasonable and the draft aged out — a different thing from a wrong
    id, and the message is what tells them to re-plan rather than retry."""
    store = SketchStore(ttl=100)
    store.put("a", spec(), now=1000.0)
    _sketch, reason = store.get("a", now=2000.0)
    assert "expired" in reason


def test_the_expired_reason_survives_a_second_read():
    """Dropping on read is right — a sweep needs a clock nobody owns — but without a tombstone the
    reason was one-shot, and the second attempt reported "unknown sketch"."""
    store = SketchStore(ttl=100)
    store.put("a", spec(), now=1000.0)
    store.get("a", now=2000.0)
    _sketch, reason = store.get("a", now=2000.0)
    assert "expired" in reason


def test_an_id_that_never_existed_is_distinguishable():
    store = SketchStore(ttl=100)
    assert store.get("nope", now=1.0)[1] == "unknown sketch"


def test_eviction_is_oldest_first_at_the_cap():
    """Every held sketch is a spec a stale reference could launch."""
    store = SketchStore(ttl=10_000, cap=3)
    for index in range(5):
        store.put(f"s{index}", spec(), now=1000.0 + index)
    assert len(store) == 3
    assert store.get("s0", now=1010.0)[0] is None
    assert store.get("s4", now=1010.0)[0] is not None


def test_the_tombstone_set_is_bounded():
    """It is a courtesy, not a log — an unbounded id set would outlive the process it was helpin"""
    store = SketchStore(ttl=1, cap=2)
    for index in range(60):
        store.put(f"s{index}", spec(), now=float(index))
    assert len(store._expired) <= MAX_SKETCHES * 4 + 1


def test_revising_through_the_store_updates_the_sketch():
    store = SketchStore(ttl=10_000)
    store.put("a", spec(), now=1.0)
    result, reason = store.revise("a", [Patch(op="remove", node_id="write")], now=2.0)
    assert reason == ""
    assert result.applied == ["write"]
    sketch, _ = store.get("a", now=3.0)
    assert sketch.revisions == 1
    assert "write" not in {c["id"] for c in sketch.spec["root"]["children"]}


def test_revising_an_expired_sketch_reports_why():
    store = SketchStore(ttl=10)
    store.put("a", spec(), now=1.0)
    result, reason = store.revise("a", [Patch(op="remove", node_id="write")], now=100.0)
    assert result is None
    assert "expired" in reason


def test_a_rejected_revision_does_not_bump_the_counter():
    """A revision counter that counted failures would make a plan look more reviewed than it is."""
    store = SketchStore(ttl=10_000)
    store.put("a", spec(), now=1.0)
    store.revise(
        "a", [Patch(op="replace", node_id="ghost", node={"kind": "stage", "id": "g"})], now=2.0
    )
    assert store.get("a", now=3.0)[0].revisions == 0


# ── the cost estimate ──


def test_the_estimate_counts_model_calls_not_nodes():
    """A gate and a transform cost nothing; counting nodes would tell the user a zero-token plan is
    expensive."""
    assert estimate_cost(spec())["model calls"] == 2


def test_a_fan_out_multiplies_the_estimate():
    """A stage inside a foreach is not one call, and presenting it as one understates exactly the
    topology that runs away."""
    fanned = {
        "root": {
            "kind": "foreach",
            "id": "f",
            "config": {"items": "{{inputs.x}}"},
            "body": {"kind": "stage", "id": "s", "config": {"prompt": "x"}},
        }
    }
    assert estimate_cost(fanned)["model calls"] > 1


def test_an_unbounded_loop_is_named_rather_than_estimated():
    """An unbounded loop makes the number a FLOOR, and presenting a floor as an estimate
    understates the one case that runs away."""
    unbounded = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_dry", "streak": 2},
            "body": {"kind": "stage", "id": "s", "config": {"prompt": "x"}},
        }
    }
    assert "UNBOUNDED loops" in estimate_cost(unbounded)


def test_the_estimate_returns_counts_not_a_price():
    """A dollar figure derived from a node count is a confident number built on an unknown
    per-call cost, and a user who sees "$0.42" believes it."""
    payload = estimate_cost(spec())
    assert not any("$" in str(k) or "$" in str(v) for k, v in payload.items())


def test_a_malformed_spec_estimates_nothing_rather_than_raising():
    assert estimate_cost({"root": "junk"}) == {}


# ── the announce block ──


def test_risk_comes_before_the_pipeline():
    """Putting the pipeline first would bury "this touches payments" under twelve stage names."""
    intent = classify("delete every production customer record")
    header = announce_block(intent=intent, cost={"model calls": 4})
    assert header.index("Risk:") < header.index("Cost:")


def test_an_irreversible_intent_says_so_in_the_header():
    header = announce_block(intent=classify("purge the production billing table"))
    assert "IRREVERSIBLE" in header


def test_a_matched_template_is_named_with_its_confidence():
    match = MatchResult(primary="audit-sweep", confidence=0.79, reason="T1: keywords[audit]")
    header = announce_block(match=match)
    assert "audit-sweep" in header
    assert "79%" in header


def test_no_match_says_it_is_generating():
    header = announce_block(intent=classify("do something novel"))
    assert "generating" in header


def test_blocking_decisions_are_surfaced_as_pause_points():
    decisions = type_decisions(
        {"root": {"kind": "gate", "id": "ask", "config": {"kind": "approval"}}}
    )
    assert "ask" in announce_block(decisions=decisions)


def test_the_header_agrees_with_the_contract_lint_about_what_is_unchecked():
    """Measured: the header flagged four stages the lint deliberately exempts. Two views of one plan
    disagreeing is worse than either alone — the user believes the scarier one, and the lint they
    might have trusted looks wrong."""
    reviewed = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "review", "config": {"prompt": "review"}},
                {
                    "kind": "stage",
                    "id": "revise",
                    "config": {"prompt": "fix {{nodes.review.output}}"},
                },
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
                {"kind": "action", "id": "save", "config": {"provider": "knowledge-persist"}},
            ],
        }
    }
    contracts = derive_contracts(reviewed)
    header = announce_block(contracts=contracts)
    assert "Unchecked:" not in header


def test_an_empty_header_is_empty_rather_than_a_shell():
    """A header with labels and no values reads as "nothing was detected", which is a claim."""
    assert announce_block() == ""


# ── the markdown artifact ──


def test_the_markdown_lists_the_steps_in_order():
    # The goal string must not contain a step id: `.index()` would match the title line instead of
    # the step, which is a test that passes for the wrong reason as easily as it fails.
    text = plan_markdown(spec(), goal="an article about latency")
    steps = [line for line in text.splitlines() if line.strip().startswith(("1.", "2.", "3."))]
    assert [s.split("**")[1] for s in steps] == ["research", "write", "check"]


def test_the_markdown_marks_unchecked_steps():
    """A user reviewing a plan is deciding whether the WORK is right, and an unverified step that
    looks like the others is the one they approve without noticing."""
    bare = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "x"}},
                {"kind": "stage", "id": "b", "config": {"prompt": "y"}},
            ],
        }
    }
    text = plan_markdown(bare, goal="g", contracts=derive_contracts(bare))
    assert "nothing checks this step" in text


def test_the_markdown_omits_containers():
    """A sequence is a scheduling policy, not a step. Listing it makes the user count structure."""
    text = plan_markdown(spec(), goal="g")
    assert "**root**" not in text


# ── inferred chips ──


def test_a_value_the_user_said_is_marked_stated():
    chips = {c["name"]: c for c in inferred_chips(spec(), "look into cold starts")}
    assert chips["topic"]["source"] == "stated"
    assert chips["topic"]["confirm"] is False


def test_a_value_the_user_never_said_is_marked_inferred():
    """A user re-reads what they said and skims what the system assumed, so an inferred value
    presented identically to a stated one is the one that ships wrong."""
    chips = {c["name"]: c for c in inferred_chips(spec(), "look into cold starts")}
    assert chips["depth"]["source"] == "inferred"
    assert chips["depth"]["confirm"] is True


def test_a_spec_with_no_inputs_has_no_chips():
    assert inferred_chips({"root": {}}, "anything") == []


# ── the wired plan tool ──


def test_the_plan_tool_ships_the_review_surface():
    """The end-to-end claim: a plan arrives with its header, its cost shape, its markdown, and the
    revision grammar a caller needs to patch it."""
    from personalclaw.workflows import bundled_defs

    bundled_defs.register_bundled_provider()
    from personalclaw.mcp_workflows import _plan

    out = _plan({"goal": "publish an article about cold starts"})
    body = json.loads(out[out.find("{") :])
    assert body.get("announce")
    assert "model calls" in (body.get("cost_estimate") or {})
    assert body.get("plan_markdown", "").startswith("# Plan:")
    grammar = body.get("revision_grammar") or {}
    assert grammar.get("no_update_sentinel") == NO_UPDATE
    assert set(grammar.get("ops") or []) == {"replace", "add", "remove", "annotate"}
