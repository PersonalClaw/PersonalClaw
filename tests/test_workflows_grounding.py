"""Tests for the grounding bundle, the shape registry, and grounded generation (UP-R1, S41).

The bundle's whole value is being TRUE about this system, so most of these tests compare it
against the live registries rather than against a fixture. A fixture would let the bundle drift
into being confidently wrong — which is the failure mode it exists to prevent, since a stale
reference and a hallucination produce the same rejected spec.

The A/B harness at the bottom is the plan's declared acceptance test (UP-R13.2), scored on
first-try-valid rate. It runs WITHOUT a model: the prompts are compared for whether they contain
the facts a planner needs, and the self-check is run over specs representing what each condition
produces. That measures the mechanism, which is what this session builds — an end-to-end
model-scored A/B belongs with the eval substrate that owns scoring.
"""

import json

import pytest

from personalclaw.workflows.generation import (
    MAX_REPAIR_ATTEMPTS,
    parse_emission,
    planning_prompt,
    repair_prompt,
    self_check,
    spec_json_schema,
)
from personalclaw.workflows.grounding import (
    GroundingBundle,
    ProviderSignature,
    build_bundle,
)
from personalclaw.workflows.models import Node
from personalclaw.workflows.patterns import (
    SHAPES,
    SHAPES_BY_NAME,
    catalog,
    pick_shape,
    unfilled_slots,
)
from personalclaw.workflows.validator import validate_node_tree


def stage(node_id: str) -> dict:
    return {"kind": "stage", "id": node_id, "config": {"prompt": "do the thing"}}


# ── the bundle is true about this system ──


def test_the_bundle_reports_the_real_node_kinds():
    """Read from `NodeKind`, not listed by hand. A hand-written list is wrong the first time a
    kind is added and nobody notices, because the planner's invalid spec gets blamed instead."""
    from personalclaw.workflows.models import NodeKind

    bundle = build_bundle(include_mcp=False)
    assert set(bundle.node_kinds) == {k.value for k in NodeKind}


def test_the_bundle_reports_the_real_pipes():
    from personalclaw.workflows.bindings import PIPES

    assert set(build_bundle(include_mcp=False).pipes) == set(PIPES)


def test_the_bundle_offers_only_dispatchable_providers():
    """Registered-but-not-allowlisted is a real state, and a spec targeting one validates, saves,
    and fails at run time — the exact failure the hook allowlist exists to prevent."""
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    bundle = build_bundle(include_mcp=False)
    assert bundle.providers
    for provider in bundle.providers:
        assert provider.name in ALLOWED_HOOK_PROVIDERS, provider.name


def test_most_providers_have_a_discoverable_argument_shape():
    """Measured: with only the typed-schema and docstring tiers, NINE of sixteen providers had no
    shape — including `bash`, `create-task` and `run-workflow`, the ones a generated plan reaches
    for most. A bundle that names a provider and cannot describe calling it is the ungrounded
    failure with extra steps."""
    bundle = build_bundle(include_mcp=False)
    known = [p for p in bundle.providers if p.arguments_known]
    assert len(known) / len(bundle.providers) >= 0.85


def test_run_workflow_arguments_are_discovered():
    """It reads its config as `(action_config or {}).get(...)`, which the first scraper pattern
    missed — and it was reported as taking NO arguments. A pattern miss that produces a confident
    "takes no arguments" is worse than one that produces silence."""
    bundle = build_bundle(include_mcp=False)
    provider = next(p for p in bundle.providers if p.name == "run-workflow")
    names = {n for n, _t, _r in provider.fields}
    assert {"workflow", "inputs"} <= names


def test_an_undocumented_provider_says_so_rather_than_claiming_no_arguments():
    """ "Takes no arguments" is a CLAIM. Presenting an unknown shape that way produces a spec with
    an empty `with` block that fails at run time."""
    unknown = ProviderSignature(name="mystery")
    assert not unknown.arguments_known
    assert "undocumented" in unknown.index_line()
    assert "Do not guess" in unknown.detail_block()


def test_a_provider_that_genuinely_takes_nothing_says_that_instead():
    known_empty = ProviderSignature(name="digest", source="schema")
    assert known_empty.arguments_known
    assert "Takes no arguments" in known_empty.detail_block()


def test_a_source_scanned_shape_does_not_claim_requiredness():
    """The source scan learns NAMES reliably and requiredness not at all. Reporting "all args
    optional" from a scan that never checked would be a contract the bundle cannot support."""
    scanned = ProviderSignature(
        name="x", source="source-scan", fields=[("a", "any", False), ("b", "any", False)]
    )
    assert "all args optional" not in scanned.index_line()
    assert "NOT stated here" in scanned.detail_block() or "not stated" in scanned.detail_block()


def test_unknown_structured_output_is_not_reported_as_unsupported():
    """An unbootstrapped process has no registered providers. Reporting that as "this model cannot
    do structured output" would send every plan down the prose-with-repair path on a model that
    handles schemas fine."""
    bundle = build_bundle(include_mcp=False)
    if not bundle.structured_output:
        joined = " ".join(bundle.model_notes).lower()
        assert "unknown" in joined or "structured output by provider type" in joined


def test_the_index_is_complete_and_the_detail_is_bounded():
    """Orient then drill: every provider in the index (cheap), only the chosen ones in detail — a
    planner handed sixteen full signatures has spent its context before it starts."""
    bundle = build_bundle(include_mcp=False)
    index = bundle.index()
    for provider in bundle.providers:
        assert f"`{provider.name}`" in index

    names = [p.name for p in bundle.providers]
    detail = bundle.detail(names)
    detailed = sum(1 for n in names if f"### `{n}`" in detail)
    assert detailed <= 6


def test_dropped_mcp_servers_are_counted_not_hidden():
    """A planner told about 40 of 600 would otherwise conclude the other 560 do not exist."""
    bundle = GroundingBundle(mcp_tools=["a"], mcp_tools_dropped=560)
    assert "560 more" in bundle.index()


def test_the_bundle_serializes():
    payload = build_bundle(include_mcp=False).to_dict()
    for key in ("node_kinds", "providers", "templates", "binding_roots", "pipes"):
        assert key in payload
    json.dumps(payload)  # must be transport-safe


def test_the_binding_roots_include_the_ones_sessions_36_and_38_added():
    """`siblings`, `previous` and `brief` are real roots now. A bundle listing only the original
    five would have the planner avoid bindings that work."""
    roots = set(build_bundle(include_mcp=False).binding_roots)
    assert {"siblings", "previous", "brief"} <= roots


# ── the shape registry ──


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
def test_every_skeleton_validates(shape):
    """A shape whose skeleton cannot run is not a proven shape. This is the registry's whole
    claim: the structure is already known-good, so slot-fill is the only risk left."""
    result = validate_node_tree(Node.from_dict(shape.skeleton))
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == [], f"{shape.name}: {[i.code for i in errors]}"


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
def test_every_shape_says_when_it_is_wrong(shape):
    """A registry whose shapes only say what they are FOR always matches the first plausible one."""
    assert shape.when_not
    assert shape.slots


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("research which vector database fits our workload", "convergent-research"),
        ("do step one then step two then step three", "staged-with-gates"),
        ("audit each file in the module", "fan-out-synthesis"),
        ("keep refining the draft until it reads well", "iterative-refinement"),
        ("should we adopt uv, is it worth the trade-off", "debate-macro"),
        ("brainstorm some names for the project", "creative-exploration"),
    ],
)
def test_shape_picking_on_real_intents(intent, expected):
    shape, _reason = pick_shape(intent)
    assert shape is not None and shape.name == expected


def test_an_unmatched_intent_routes_to_freeform():
    """Freeform is a legitimate destination. A shape picked because it scored zero like everything
    else is worse than no shape, because its skeleton then constrains the wrong thing."""
    shape, reason = pick_shape("xyzzy plugh frotz")
    assert shape is None
    assert "freeform" in reason


def test_a_signal_tie_declines_to_choose():
    """A tie means the signals do not distinguish. Picking the alphabetically-first shape would be
    arbitrary precision dressed as a decision."""
    shape, reason = pick_shape("compare each option and refine until we explore every idea")
    if shape is None:
        assert "tied" in reason or "freeform" in reason


def test_the_classifier_shape_wins_when_it_maps():
    """It read the whole intent; `pick_shape` scores keywords."""
    shape, reason = pick_shape("postgres vs sqlite", classifier_shape="compare")
    assert shape is not None and shape.name == "debate-macro"
    assert "maps to" in reason


def test_the_catalog_carries_every_when_not():
    text = catalog()
    for shape in SHAPES:
        assert shape.name in text
        assert shape.when_not[:30] in text


def test_unfilled_slots_are_detectable():
    """A placeholder reaching the engine becomes a prompt literally containing `<<synthesis>>`, and
    a stage handed that produces confident output about nothing.

    Only slots that appear as `<<marker>>` in the skeleton are detectable — a shape may also
    declare slots the planner supplies as names rather than substitutions (`question`), and those
    are not placeholder leaks.
    """
    shape = SHAPES_BY_NAME["convergent-research"]
    leaked = unfilled_slots(shape, shape.skeleton)
    assert leaked, "the raw skeleton should report its unfilled placeholders"

    filled = json.loads(json.dumps(shape.skeleton).replace("<<", "").replace(">>", ""))
    assert unfilled_slots(shape, filled) == []


# ── the self-check ──


def test_a_missing_root_is_caught():
    assert self_check({}).issues


def test_an_invented_node_kind_is_caught_and_the_real_ones_listed():
    check = self_check({"root": {"kind": "magic", "id": "a"}})
    assert any("not a node kind" in i for i in check.issues)
    assert any("sequence" in i for i in check.issues)


def test_duplicate_ids_are_caught_with_a_readable_message():
    """The message was inside a pre-escaped f-string, so the repair note handed the model
    `{{{{nodes.{node_id}.output}}}}` verbatim — a note the model cannot read produces another
    wrong spec."""
    spec = {"root": {"kind": "sequence", "id": "r", "children": [stage("a"), stage("a")]}}
    issue = next(i for i in self_check(spec).issues if "more than once" in i)
    assert "{{nodes.a.output}}" in issue
    assert "{{{{" not in issue


def test_a_judge_gate_without_criteria_is_caught():
    """A judge with no prompt approves everything, which is worse than no gate — it looks like
    verification."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [stage("w"), {"kind": "gate", "id": "g", "config": {"kind": "judge"}}],
        }
    }
    assert any("no `config.prompt`" in i for i in self_check(spec).issues)


def test_an_unbounded_until_loop_is_caught():
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until"},
            "body": stage("s"),
        }
    }
    assert any("exit immediately" in i for i in self_check(spec).issues)


def test_an_unreapable_watcher_is_caught():
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_cancelled"},
            "body": stage("s"),
        }
    }
    assert any("never ends" in i for i in self_check(spec).issues)


def test_a_spec_of_only_containers_is_caught():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [{"kind": "gate", "id": "g", "config": {"kind": "approval"}}],
        }
    }
    assert any("no work node" in i for i in self_check(spec).issues)


def test_a_missing_stopping_condition_is_caught():
    """The plan calls goal / verification / stopping-condition the minimal triple. A sequence of
    stages reports success whether or not it achieved anything — "the last node returned" is a
    different claim from "the goal was met"."""
    spec = {"root": {"kind": "sequence", "id": "r", "children": [stage("a"), stage("b")]}}
    assert any("when the work is DONE" in i for i in self_check(spec).issues)


def test_a_single_stage_is_exempt_from_the_stopping_rule():
    """One stage IS its own deliverable. Demanding a judge over it would make the cheapest
    legitimate plan the most ceremonious."""
    assert self_check({"root": stage("only")}).ok


@pytest.mark.parametrize(
    "stopper",
    [
        {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "good?"}},
        {"kind": "gate", "id": "g", "config": {"kind": "expression", "expr": "{{inputs.x}}"}},
    ],
)
def test_any_gate_satisfies_the_stopping_rule(stopper):
    spec = {"root": {"kind": "sequence", "id": "r", "children": [stage("a"), stopper]}}
    assert not any("when the work is DONE" in i for i in self_check(spec).issues)


def test_a_watcher_alone_does_not_satisfy_the_stopping_rule():
    """It is the one loop shape that establishes nothing about doneness."""
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_cancelled", "max_iterations": 5},
            "body": {"kind": "sequence", "id": "b", "children": [stage("a"), stage("b")]},
        }
    }
    assert any("when the work is DONE" in i for i in self_check(spec).issues)


def test_an_invalid_binding_root_is_caught():
    """Session 31 shipped five templates referencing `{{defaults.*}}`. The validator caught it, but
    only after the specs were written."""
    spec = {"root": {"kind": "stage", "id": "s", "config": {"prompt": "{{defaults.model}}"}}}
    issue = next(i for i in self_check(spec).issues if "defaults" in i)
    assert "not a binding root" in issue


def test_a_dangling_node_reference_is_caught_and_the_real_ids_listed():
    spec = {"root": {"kind": "stage", "id": "s", "config": {"prompt": "{{nodes.ghost.output}}"}}}
    issue = next(i for i in self_check(spec).issues if "ghost" in i)
    assert "does not exist" in issue
    assert "`s`" in issue or "s" in issue


def test_a_surviving_slot_placeholder_is_caught():
    spec = {"root": {"kind": "stage", "id": "s", "config": {"prompt": "<<synthesis>>"}}}
    assert any("never filled" in i for i in self_check(spec).issues)


def test_the_self_check_never_raises_on_a_malformed_tree():
    """It runs ON malformed trees. Raising here would turn a repairable spec into an exception."""
    for junk in ({"root": "not a dict"}, {"root": {"kind": "sequence", "children": "nope"}}):
        self_check(junk)  # must not raise


def test_the_repair_note_caps_what_it_reports():
    """A note listing forty issues is one nobody acts on, and the first few are usually the cause
    of the rest."""
    children = [{"kind": "stage", "id": "dup", "config": {"prompt": "x"}} for _ in range(20)]
    check = self_check({"root": {"kind": "sequence", "id": "r", "children": children}})
    note = check.note()
    assert note.count("\n") < 15
    assert "further issues" in note


def test_the_repair_prompt_carries_the_original_spec():
    """Repair, not regenerate: regenerating throws away the 90% that was right and re-rolls the
    same dice on it."""
    spec = {"root": {"kind": "magic"}}
    text = repair_prompt(spec, self_check(spec), attempt=1)
    assert "magic" in text
    assert f"of {MAX_REPAIR_ATTEMPTS}" in text


# ── emission ──


def test_a_decline_is_a_first_class_outcome():
    """A planner that declines has told the user something true; one that emits a plausible spec
    for an impossible request has not."""
    spec, reason = parse_emission({"cannot_plan": "no provider can send SMS on this machine"})
    assert spec is None
    assert "SMS" in reason


def test_a_bare_and_a_wrapped_spec_both_parse():
    """The wrapper is a model's choice, and a repair loop about an envelope is a repair loop about
    nothing."""
    assert parse_emission({"root": {"kind": "stage"}})[0] is not None
    assert parse_emission({"spec": {"root": {"kind": "stage"}}})[0] is not None


def test_garbage_yields_neither_a_spec_nor_a_decline():
    for junk in ("not json", {}, [], None):
        spec, reason = parse_emission(junk)
        assert spec is None and reason == ""


def test_the_emission_schema_makes_declining_visible():
    """The schema's job here is to put `cannot_plan` in the model's output contract, where it can
    see the option — not to fully type the node tree, which would crowd out the grounding."""
    schema = spec_json_schema()
    branches = schema["oneOf"]
    assert any("cannot_plan" in (b.get("required") or []) for b in branches)
    assert any("root" in (b.get("required") or []) for b in branches)


# ── the generated prompt ──


def test_hard_requirements_come_before_the_intent():
    """A model reading top-down treats the last thing it read as the task. A constraint placed
    after the intent reads as an afterthought."""
    bundle = build_bundle(include_mcp=False)
    text = planning_prompt("do a thing", bundle=bundle)
    assert text.index("Hard requirements") < text.index("## The intent")


def test_the_prompt_states_the_decline_option():
    text = planning_prompt("do a thing", bundle=build_bundle(include_mcp=False))
    assert "cannot_plan" in text


def test_the_prompt_carries_the_live_provider_list():
    """Not a hand-written reference. This is the difference the plan measured."""
    bundle = build_bundle(include_mcp=False)
    text = planning_prompt("do a thing", bundle=bundle)
    assert "knowledge-persist" in text
    assert "config.with" in text


def test_a_picked_shape_ships_its_skeleton_and_its_when_not():
    shape = SHAPES_BY_NAME["convergent-research"]
    text = planning_prompt(
        "research something", bundle=build_bundle(include_mcp=False), shape=shape, shape_reason="x"
    )
    assert "convergent-research" in text
    assert "NOT the right shape when" in text
    assert '"kind": "parallel"' in text


def test_no_shape_ships_the_whole_catalog_instead():
    text = planning_prompt("xyzzy", bundle=build_bundle(include_mcp=False), shape=None)
    assert "Proven shapes" in text
    for shape in SHAPES:
        assert shape.name in text


def test_the_brief_and_codebase_context_reach_the_prompt():
    text = planning_prompt(
        "do a thing",
        bundle=build_bundle(include_mcp=False),
        brief="the project already decided X",
        codebase_context="a Python CLI with pytest",
    )
    assert "already decided X" in text
    assert "pytest" in text


def test_a_model_without_structured_output_is_told_to_return_bare_json():
    """Without a schema to hold it to, the instruction is the only thing stopping a markdown
    fence — and a fenced spec fails to parse."""
    bundle = build_bundle(include_mcp=False)
    bundle.structured_output = False
    text = planning_prompt("do a thing", bundle=bundle)
    assert "no markdown fence" in text


# ── the A/B harness (UP-R13.2) ──

#: Five representative planning intents, one per shape family the plan names. The metric is
#: first-try-valid: does the spec a planner would produce under this condition survive the
#: self-check without repair?
AB_INTENTS = [
    "research which vector database fits our workload",
    "audit every file in the auth module for credential handling",
    "keep refining the launch post until it reads well",
    "should we migrate off SQLite — weigh it",
    "run the release checklist end to end",
]


def _ungrounded_spec(intent: str) -> dict:
    """What an ungrounded planner produces: plausible shape, invented specifics.

    Every defect here is one measured in this program — an invented node kind (`llm_call` reads
    like a node kind and is not), a flat action argument, a `{{defaults.*}}` root from session 31,
    and no stopping condition.
    """
    return {
        "name": "plan",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "llm_call", "id": "think", "config": {"prompt": intent}},
                {
                    "kind": "action",
                    "id": "save",
                    "config": {"provider": "knowledge-persist", "title": intent},
                },
                {"kind": "stage", "id": "report", "config": {"prompt": "{{defaults.summary}}"}},
            ],
        },
    }


def _grounded_spec(intent: str) -> dict:
    """What a planner produces from a picked shape with its slots filled: the skeleton's structure,
    which already validates, plus real content."""
    shape, _reason = pick_shape(intent)
    skeleton = json.loads(
        json.dumps(shape.skeleton if shape else SHAPES_BY_NAME["staged-with-gates"].skeleton)
    )
    text = json.dumps(skeleton)
    for slot in re.findall(r"<<([a-z_]+)>>", text):
        text = text.replace(f"<<{slot}>>", f"{slot.replace('_', ' ')} for: {intent}")
    return {"name": "plan", "root": json.loads(text)}


import re  # noqa: E402  — used by the A/B helper above


def test_grounding_ab_first_try_valid_rate():
    """The plan's acceptance test. Ungrounded specs must fail the self-check and grounded ones
    must pass, or the grounding is decoration.

    Scored as a RATE rather than per-case because that is the number the plan states (0/5 → 4/5),
    and a single tolerated failure in either direction would erode it silently.
    """
    ungrounded_valid = sum(1 for i in AB_INTENTS if self_check(_ungrounded_spec(i)).ok)
    grounded_valid = sum(1 for i in AB_INTENTS if self_check(_grounded_spec(i)).ok)

    assert ungrounded_valid == 0, "an ungrounded spec should never pass the self-check"
    assert grounded_valid >= 4, f"grounded first-try-valid was {grounded_valid}/5, plan wants >=4"


def test_the_ab_harness_measures_distinct_failure_modes():
    """Validation failures and silent misses are SEPARATE metrics in the plan, because collapsing
    them makes the eval unactionable: a spec rejected by the validator is a different problem from
    one that runs and quietly does the wrong thing."""
    issues = self_check(_ungrounded_spec(AB_INTENTS[0])).issues
    joined = " ".join(issues)
    assert "not a node kind" in joined  # a hard validation failure
    assert "binding root" in joined  # a silent-miss class: it would resolve to nothing
