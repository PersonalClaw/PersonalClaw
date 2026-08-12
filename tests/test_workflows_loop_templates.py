"""Integration tests for the loop-kind bundled templates.

These templates are the descendants of the five loop kinds, and the property that
matters most is the one the plan calls the platform's oldest rule: **no agent certifies
its own work**. A template whose work stage self-reports `done: true` with no judge
behind it has that rule broken at the spec level, where no runtime check can help.

So the tests here are mostly structural invariants over the shipped specs rather than
behavioural tests of the engine — the engine has its own suites. What cannot be caught
at runtime is a template that was authored wrong.
"""

import json

import pytest

from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.judge_contract import (
    FallbackCheck,
    Isolation,
    Ratchet,
    Verdict,
    hints_from_dict,
)
from personalclaw.workflows.template_lint import lint_template
from personalclaw.workflows.validator import validate_spec

#: The loop-kind families this session authored. Named explicitly rather than derived
#: from a tag, so a template silently losing its tag cannot silently leave this suite.
LOOP_TEMPLATES = (
    "goal-pursuit-open-ended",
    "goal-pursuit-verifiable",
    "general-project",
    "design-project",
    "diagnose-run",
    # The code/SDLC descendant (WF2LOO-10). It joins this suite rather than getting its own
    # weaker one: it is one of the plan's per-kind templates, so the judge contract, the
    # runtime_hints split, the loop bounds and the shipping metadata are the SAME contract
    # for it. Its own R5 structural gates are tested in `test_workflows_code_project.py`.
    "code-project",
)


def _spec(name: str) -> dict:
    wf = read_template(name)
    assert wf is not None, f"{name} did not load"
    return wf.to_dict()


def _nodes(node: dict):
    """Every node in the tree, depth-first — including branch cases and loop bodies."""
    yield node
    for child in node.get("children") or []:
        yield from _nodes(child)
    if node.get("body"):
        yield from _nodes(node["body"])
    for case in (node.get("cases") or {}).values():
        if isinstance(case, dict):
            yield from _nodes(case)
        elif isinstance(case, list):
            for item in case:
                yield from _nodes(item)


def _judges(spec: dict):
    """Nodes that ADJUDICATE.

    Identified by id or gate kind, NOT by `tools_posture: verify` — a stage can be
    read-only because it reads a ledger (diagnose-run's trace stage) without being a
    judge, and treating every read-only stage as one made the judge assertions fire on
    stages that never claimed to adjudicate anything.
    """
    return [
        n
        for n in _nodes(spec["root"])
        if n.get("id", "").startswith("judge")
        # `accept` is a judge gate. `verify` is deliberately NOT here: a
        # verify_command gate is a shell exit code, and demanding a verdict schema or an
        # anti-leniency preamble from a shell command is a category error.
        or n.get("id", "") == "accept" or (n.get("config") or {}).get("kind") == "judge"
    ]


# ── they exist and load ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_template_is_shipped_and_loads(name):
    assert name in template_names()
    assert read_template(name) is not None


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_template_validates_strictly(name):
    result = validate_spec(_spec(name), strict=True)
    assert result.ok, [i.to_dict() for i in result.errors]
    assert not result.warnings, [i.to_dict() for i in result.warnings]


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_template_is_lint_clean_as_bundled(name):
    """Bundled templates are held to warnings-free: a warning that ships propagates to
    every template copied from it."""
    root = read_template(name)
    assert root is not None
    result = lint_template(json.loads(json.dumps(root.to_dict())), bundled=True)
    assert result.clean, [f.to_dict() for f in result.findings]


# ── the platform's oldest rule ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_no_stage_certifies_its_own_work(name):
    """The rule a self-reported `done: boolean` breaks at the spec level.

    A work stage that decides its own completion cannot be fixed by any runtime check —
    the graph gave it that authority. Every one of these templates closes its work with
    a separate judge, or has no work loop at all.
    """
    spec = _spec(name)
    work_nodes = [
        n for n in _nodes(spec["root"]) if n.get("kind") == "stage" and n not in _judges(spec)
    ]
    for node in work_nodes:
        schema = (node.get("config") or {}).get("schema") or {}
        # `done` is the specific field the plan flags. A worker may report PROGRESS
        # (which the loop reads) but never completion.
        assert "done" not in schema, f"{name}:{node.get('id')} self-reports done"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_work_loop_is_closed_by_a_judge(name):
    """A loop whose body has no verifier converges on whatever the worker finds easiest
    to claim."""
    spec = _spec(name)
    loops = [n for n in _nodes(spec["root"]) if n.get("kind") == "loop"]
    if not loops:
        pytest.skip(f"{name} has no work loop")
    judge_ids = {n.get("id") for n in _judges(spec)}
    for loop in loops:
        body_ids = {n.get("id") for n in _nodes(loop.get("body") or {})}
        # Either the loop body contains a judge, or the loop is a refinement loop whose
        # output feeds a downstream judge/gate.
        downstream = judge_ids - body_ids
        assert (body_ids & judge_ids) or downstream, f"{name}: loop {loop.get('id')} unjudged"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_judge_is_isolated_and_read_only(name):
    """`tools_posture: verify` + `isolation: fresh` are the two halves of an independent
    judge: it must be able to read what it verifies, and must not be the session that
    produced it."""
    spec = _spec(name)
    judges = _judges(spec)
    assert judges, f"{name} has no judge at all"
    for judge in judges:
        cfg = judge.get("config") or {}
        assert cfg.get("tools_posture") == "verify", f"{name}:{judge.get('id')}"
        assert cfg.get("isolation") == "fresh", f"{name}:{judge.get('id')}"


def test_cross_model_isolation_is_no_longer_flagged():
    """🔴 `isolation: cross_model` USED TO be flagged `WFL_UNENFORCEABLE_ISOLATION` (S146) because
    the engine had no seam to keep the claim: the gate was never told the worker's model and
    `one_shot_completion` resolved by use-case, not by model.

    WF2LOO-11 built that seam — `dispatch_gate` now takes `worker_model`, resolves the concrete
    judge model, validates its FAMILY against the worker's via `judge_actors.validate_judge_model`,
    and pins it (failing CLOSED when a different family can't be obtained). The claim is now
    ENFORCED at dispatch, so a lint warning that says "the engine cannot enforce this" is FALSE and
    was retired. This test pins that the warning is gone — a stale warning would train an author to
    reach for `isolation: fresh` when `cross_model` is exactly what now works.
    """
    spec = {
        "name": "t",
        "version": "1.0.0",
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {
                    "kind": "gate",
                    "id": "accept",
                    "config": {"kind": "judge", "prompt": "p", "isolation": "cross_model"},
                }
            ],
        },
    }
    result = lint_template(spec)
    codes = [f.code for f in result.findings]
    assert (
        "WFL_UNENFORCEABLE_ISOLATION" not in codes
    ), "the lint was retired once cross_model became enforceable at the gate"
    assert result.ok, "a plain cross_model judge is now a clean, enforceable declaration"


@pytest.mark.parametrize("declared", ["fresh", "cross_model", None, ""])
def test_no_isolation_value_is_flagged_unenforceable(declared):
    """Both isolation levels are real now: `fresh` by construction (a fresh one-shot session) and
    `cross_model` by the dispatch-time family check. The retired lint must fire for neither — a
    rule that warned on an honest value would train an author to ignore it."""
    cfg = {"kind": "judge", "prompt": "p"}
    if declared is not None:
        cfg["isolation"] = declared
    spec = {
        "name": "t",
        "version": "1.0.0",
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [{"kind": "gate", "id": "accept", "config": cfg}],
        },
    }
    codes = [f.code for f in lint_template(spec).findings]
    assert "WFL_UNENFORCEABLE_ISOLATION" not in codes


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_no_judge_can_write(name):
    """A judge with write tools can fix what it was meant to report."""
    for judge in _judges(_spec(name)):
        assert (judge.get("config") or {}).get("tools_posture") != "full"


# ── the typed verdict contract ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_judge_returns_the_typed_verdict_shape(name):
    """Loop nodes route on data, not prose — so the verdict field has to be there."""
    for judge in _judges(_spec(name)):
        cfg = judge.get("config") or {}
        schema = cfg.get("schema") or {}
        prompt = cfg.get("prompt") or ""
        if not schema:
            # A gate-kind judge carries its shape in the prompt instead.
            assert "verdict" in prompt, f"{name}:{judge.get('id')}"
            continue
        assert "verdict" in schema, f"{name}:{judge.get('id')}"
        assert "cannot_judge" in schema, f"{name}:{judge.get('id')} has no refusal channel"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_judge_prompt_names_the_closed_enum(name):
    """A judge told to return a verdict without being told the options invents them."""
    for judge in _judges(_spec(name)):
        prompt = (judge.get("config") or {}).get("prompt") or ""
        assert Verdict.PASS.value in prompt and Verdict.REJECT.value in prompt


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_judge_prompt_demands_evidence(name):
    """A verdict with no cited proof is invalid by contract, so the prompt has to ask
    for it — otherwise every verdict fails validation and the loop cannot finish."""
    for judge in _judges(_spec(name)):
        prompt = ((judge.get("config") or {}).get("prompt") or "").lower()
        assert "evidence" in prompt or "proof" in prompt


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_judge_prompt_carries_the_anti_leniency_doctrine(name):
    """ "Do not talk yourself into approving" is doing real work: a judge asked "is this
    good?" tends to agree, because agreeing is the locally plausible answer."""
    for judge in _judges(_spec(name)):
        prompt = ((judge.get("config") or {}).get("prompt") or "").lower()
        assert "do not talk yourself into approving" in prompt or "skeptic" in prompt


# ── runtime_hints ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_template_declares_runtime_hints(name):
    spec = _spec(name)
    assert spec.get("runtime_hints"), f"{name} has no runtime_hints"
    assert "judge" in spec["runtime_hints"]
    assert "execution" in spec["runtime_hints"]


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_hints_parse_into_the_judge_contract(name):
    """The hints are only worth declaring if the contract can read them."""
    hints = hints_from_dict(_spec(name)["runtime_hints"]["judge"])
    assert hints.rubric, f"{name} declares no rubric"
    assert hints.ratchet is Ratchet.STRICT
    assert hints.judge_isolation is Isolation.FRESH
    assert isinstance(hints.fallback_check, FallbackCheck)
    assert hints.consecutive_clean >= 1


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_rubric_criterion_appears_in_a_judge_prompt(name):
    """A rubric the judge never sees is a rubric that scores nothing — the binding form
    `{{defaults.runtime_hints...}}` does NOT exist in this engine (valid roots are
    inputs/nodes/item/iter/last), so the criteria have to be inlined."""
    spec = _spec(name)
    hints = hints_from_dict(spec["runtime_hints"]["judge"])
    prompts = " ".join(((n.get("config") or {}).get("prompt") or "") for n in _judges(spec)).lower()
    for criterion in hints.rubric:
        assert criterion.criterion.lower() in prompts, f"{name}: {criterion.criterion}"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_forbidden_mode_appears_in_a_judge_prompt(name):
    """A denylist the judge never reads cannot be checked against."""
    spec = _spec(name)
    hints = hints_from_dict(spec["runtime_hints"]["judge"])
    prompts = " ".join(((n.get("config") or {}).get("prompt") or "") for n in _judges(spec)).lower()
    for mode in hints.forbidden_success_modes:
        assert mode.lower() in prompts, f"{name}: {mode}"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_escalation_ladder_ends_at_surface(name):
    """A ladder with no terminal rung loops at its top forever."""
    ladder = _spec(name)["runtime_hints"]["execution"]["escalation"]["ladder"]
    assert ladder[-1] == "surface"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_breaker_is_parameterized(name):
    breaker = _spec(name)["runtime_hints"]["execution"].get("breaker") or {}
    assert breaker.get("fingerprint_window", 0) >= 1
    assert breaker.get("no_progress_stop", 0) >= 1


# ── loop termination ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_loop_has_a_real_exit_and_a_hard_cap(name):
    """A loop with only a cap is a busy-loop that always burns its budget; a loop with
    only a condition never ends when the condition is unreachable. Both are needed."""
    for loop in (n for n in _nodes(_spec(name)["root"]) if n.get("kind") == "loop"):
        cfg = loop.get("config") or {}
        mode = cfg.get("mode")
        if mode == "until":
            assert cfg.get("condition"), f"{name}:{loop.get('id')} has no condition"
        elif mode == "until_dry":
            assert cfg.get("progress_field"), f"{name}:{loop.get('id')} has no progress field"
            assert cfg.get("streak", 0) >= 1
        assert cfg.get("max_iterations", 0) >= 1, f"{name}:{loop.get('id')} has no cap"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_loop_declares_a_stall_timeout(name):
    """Without one, a wedged iteration holds the run open indefinitely."""
    for loop in (n for n in _nodes(_spec(name)["root"]) if n.get("kind") == "loop"):
        cfg = loop.get("config") or {}
        assert cfg.get("timeout_stall_secs", 0) > 0, f"{name}:{loop.get('id')}"


# ── shipping metadata ──


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_every_input_is_documented(name):
    """The run dialog builds its fields from these; an input with no help shows a bare
    snake_case name and the user has to read the spec to learn what it wants."""
    for key, param in (_spec(name).get("inputs") or {}).items():
        assert param.get("help"), f"{name}:{key} has no help"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_no_input_is_both_required_and_defaulted(name):
    """They contradict each other: a default means it can be omitted."""
    for key, param in (_spec(name).get("inputs") or {}).items():
        if param.get("required"):
            # `default: null` is what the LOADER normalizes an absent default to, so
            # asserting the key is missing tests the loader rather than the template.
            # A MEANINGFUL default alongside `required` is the actual contradiction.
            assert param.get("default") in (None, ""), f"{name}:{key}"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_template_carries_both_steering_examples(name):
    """The mutation example is what teaches a model that editing a RUNNING workflow is
    a normal thing to do."""
    events = {
        e.get("event") for e in ((_spec(name).get("metadata") or {}).get("steering_examples") or [])
    }
    assert {"kickoff", "mutation"} <= events, f"{name}: {events}"


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_the_risk_and_capabilities_are_declared(name):
    """The Store shows these as the install-consent surface."""
    metadata = _spec(name).get("metadata") or {}
    assert metadata.get("risk") in {"low", "medium", "high"}
    assert metadata.get("capabilities")


@pytest.mark.parametrize("name", LOOP_TEMPLATES)
def test_a_writing_template_declares_write_capability(name):
    """A template whose stages have full tools but claims read-only understates what
    installing it permits."""
    spec = _spec(name)
    has_full_tools = any(
        (n.get("config") or {}).get("tools_posture") == "full" for n in _nodes(spec["root"])
    )
    if has_full_tools:
        assert "write" in ((spec.get("metadata") or {}).get("capabilities") or [])


# ── the verifiable variant's own contract ──


def test_the_verifiable_variant_captures_a_baseline_before_editing():
    """Otherwise a failure afterwards cannot be told apart from one that was already
    there, and someone debugs the wrong commit."""
    spec = _spec("goal-pursuit-verifiable")
    ids = [n.get("id") for n in spec["root"]["children"]]
    assert ids[0] == "baseline"
    assert ids.index("baseline") < ids.index("work")


def test_the_verifiable_variant_ends_on_the_command_not_a_model():
    """The command IS the goal in executable form; a model verdict is the tiebreaker,
    not the authority."""
    spec = _spec("goal-pursuit-verifiable")
    final = spec["root"]["children"][-1]
    assert final.get("kind") == "gate"
    assert (final.get("config") or {}).get("kind") == "verify_command"


def test_the_verifiable_variant_forbids_weakening_the_check():
    """Deleting the test makes the command pass without making the goal true."""
    hints = hints_from_dict(_spec("goal-pursuit-verifiable")["runtime_hints"]["judge"])
    joined = " ".join(hints.forbidden_success_modes).lower()
    assert "test deleted" in joined
    assert "verify command weakened" in joined


def test_the_verifiable_variants_fallback_is_the_command():
    hints = hints_from_dict(_spec("goal-pursuit-verifiable")["runtime_hints"]["judge"])
    assert hints.fallback_check is FallbackCheck.COMMAND_EXIT_CODE


# ── the design template's own contract ──


def test_the_design_template_diverges_before_committing():
    """The second option is only useful if it could not have been reached from the
    first."""
    ids = [n.get("id") for n in _spec("design-project")["root"]["children"]]
    assert ids[0] == "diverge"
    assert ids.index("diverge") < ids.index("explore")


def test_the_design_evaluator_is_not_either_generator():
    evaluate = next(n for n in _nodes(_spec("design-project")["root"]) if n.get("id") == "evaluate")
    assert (evaluate.get("config") or {}).get("isolation") == "fresh"


def test_the_design_refinement_loop_has_a_reachable_exit():
    """A `remaining_issues | length == 0` style condition over a field the body never
    sets is an infinite loop with extra steps."""
    spec = _spec("design-project")
    refine = next(n for n in _nodes(spec["root"]) if n.get("id") == "refine")
    condition = (refine.get("config") or {}).get("condition") or ""
    body_schema = ((refine.get("body") or {}).get("config") or {}).get("schema") or {}
    field = condition.replace("{{", "").replace("}}", "").strip().split(".")[-1]
    assert field in body_schema, f"condition reads {field!r}, body sets {sorted(body_schema)}"


# ── the diagnose template's own contract ──


def test_the_diagnose_template_localizes_before_explaining():
    ids = [n.get("id") for n in _spec("diagnose-run")["root"]["children"]]
    assert ids.index("trace") < ids.index("classify")


def test_the_diagnose_template_names_all_four_layers():
    """A classification that would fit any failure explains none of them."""
    spec = _spec("diagnose-run")
    prompts = " ".join(
        ((n.get("config") or {}).get("prompt") or "") for n in _nodes(spec["root"])
    ).lower()
    for layer in ("routing", "execution", "verification", "governance"):
        assert layer in prompts


def test_the_diagnose_template_requires_ruling_out_the_others():
    classify = next(n for n in _nodes(_spec("diagnose-run")["root"]) if n.get("id") == "classify")
    assert "ruled_out" in ((classify.get("config") or {}).get("schema") or {})


def test_the_diagnose_template_is_read_only():
    """A post-mortem that can edit the thing it is diagnosing changes the evidence."""
    spec = _spec("diagnose-run")
    assert "write" not in ((spec.get("metadata") or {}).get("capabilities") or [])
    for node in _nodes(spec["root"]):
        assert (node.get("config") or {}).get("tools_posture") != "full"


# ── the whole library still holds ──


def test_the_new_templates_did_not_break_the_shipped_library():
    """Every bundled template, old and new, still loads and validates."""
    for name in template_names():
        wf = read_template(name)
        assert wf is not None, name
        assert validate_spec(wf.to_dict(), strict=True).ok, name


def test_the_loop_families_are_all_present():
    """The five loop kinds the plan replaces each have a descendant."""
    shipped = set(template_names())
    assert set(LOOP_TEMPLATES) <= shipped
    # deep-research is the research-loop descendant and pre-dates this session.
    assert "deep-research" in shipped
