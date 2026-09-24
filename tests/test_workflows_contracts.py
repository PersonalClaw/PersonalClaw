"""Tests for derived parameters, stage contracts and decision typing (UP-R3/R8/R16, S42).

Every mechanism here replaces a hand-maintained artifact with a derived one, so the tests measure
against the SHIPPED library rather than fixtures — a fixture would let the derivation drift into
being confidently wrong, which is the failure it exists to prevent.

Two findings these tests encode, both measured on the real library:

* THREE of eighteen templates declared an input nothing read. One let a user set `apply: true` on a
  consolidation pass and watch the node run with `apply: false` — no effect, no error.
* FIVE templates ended on a write with nothing establishing the work was right. The contract lint
  found them; four more (from an earlier slice) are recorded as findings rather than fixed here.
"""

import json

import pytest

from personalclaw.workflows import bundled_defs
from personalclaw.workflows.contracts import (
    MACHINE_VERIFIED_GATES,
    ParamSpec,
    apply_extraction,
    contract_issues,
    declared_but_unused,
    derive_contracts,
    open_decisions,
    resolve_unfilled_inputs,
    template_types,
    type_decisions,
)
from personalclaw.workflows.models import Node
from personalclaw.workflows.validator import validate_node_tree

TEMPLATES = sorted(bundled_defs.template_names())


def spec_of(name: str) -> dict:
    """A template as a plain spec dict, the shape these functions take."""
    definition = bundled_defs.read_template(name)
    root = definition.root
    inputs = {}
    for key, value in (definition.inputs or {}).items():
        inputs[key] = value.to_dict() if hasattr(value, "to_dict") else value
    return {
        "inputs": inputs,
        "root": root.to_dict() if hasattr(root, "to_dict") else root,
    }


def stage(node_id: str, **cfg) -> dict:
    return {"kind": "stage", "id": node_id, "config": {"prompt": "x", **cfg}}


# ── derived parameters (UP-R8) ──


def test_the_derived_schema_is_what_the_tree_references():
    spec = {
        "inputs": {"topic": {"required": True}},
        "root": stage("s", prompt="about {{inputs.topic}} for {{inputs.audience}}"),
    }
    names = [p.name for p in resolve_unfilled_inputs(spec)]
    assert names == ["audience", "topic"]


def test_a_declared_input_the_tree_never_reads_is_not_a_parameter():
    """Returning it would put a control on the launch form that changes nothing — worse than
    omitting it, because the user believes they configured something."""
    spec = {"inputs": {"ghost": {"required": True}}, "root": stage("s", prompt="no bindings")}
    assert resolve_unfilled_inputs(spec) == []
    assert declared_but_unused(spec) == ["ghost"]


@pytest.mark.parametrize("name", TEMPLATES)
def test_no_shipped_template_declares_an_input_it_never_reads(name):
    """Measured: THREE of eighteen did. `knowledge-lint` offered `apply` while its node hardcoded
    `false`; `design-project` and `general-project` offered loop caps nothing consulted. A control
    that silently does nothing is worse than a missing one."""
    assert declared_but_unused(spec_of(name)) == []


def test_a_parameter_with_a_default_is_optional():
    spec = {
        "inputs": {"rounds": {"default": 3}},
        "root": stage("s", prompt="{{inputs.rounds}}"),
    }
    assert resolve_unfilled_inputs(spec)[0].required is False


def test_an_undeclared_parameter_defaults_to_REQUIRED():
    """An unasked parameter resolves to nothing and the binding fails mid-run. One question beats
    a run that dies on its first node."""
    spec = {"root": stage("s", prompt="{{inputs.mystery}}")}
    assert resolve_unfilled_inputs(spec)[0].required is True


def test_a_parameter_reports_which_stages_use_it():
    """Review shows a user "this affects the synthesize stage"; a path is engine addressing."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("first", prompt="{{inputs.topic}}"),
                stage("second", prompt="also {{inputs.topic}}"),
            ],
        }
    }
    assert resolve_unfilled_inputs(spec)[0].used_by == ["first", "second"]


def test_the_type_string_carries_the_help_text():
    """It goes into a prompt, and the comment is the only thing telling a model what the field
    MEANS."""
    spec = {
        "inputs": {"topic": {"required": True, "help": "what to write about"}},
        "root": stage("s", prompt="{{inputs.topic}}"),
    }
    text = template_types(spec)
    assert "topic:" in text
    assert "what to write about" in text


def test_an_optional_parameter_is_marked_in_the_type_string():
    spec = {"inputs": {"r": {"default": 3}}, "root": stage("s", prompt="{{inputs.r}}")}
    assert "r?:" in template_types(spec)


def test_a_parameterless_template_says_so():
    assert "no parameters" in template_types({"root": stage("s", prompt="nothing bound")})


def test_an_unparseable_spec_yields_no_parameters_rather_than_raising():
    assert resolve_unfilled_inputs({"root": "not a dict"}) == []
    assert resolve_unfilled_inputs({}) == []


# ── the extraction contract ──


def params(*names, **required):
    return [ParamSpec(name=n, required=required.get(n, True)) for n in names]


def test_extraction_recomputes_all_filled_rather_than_trusting_it():
    """A model claiming `all_filled: true` while omitting a required field produces a run that dies
    on its first binding."""
    result = apply_extraction(
        params("topic", "audience"),
        {"extracted": {"topic": "cold starts"}, "all_filled": True},
    )
    assert result.all_filled is False
    assert result.missing == ["audience"]


def test_a_field_the_schema_does_not_have_is_ignored():
    """Whatever it looks like, it is not a parameter — and accepting it would put an unvalidated
    value into the run's inputs."""
    result = apply_extraction(params("topic"), {"extracted": {"topic": "x", "invented": "y"}})
    assert result.extracted == {"topic": "x"}


def test_a_default_counts_as_filled():
    """Asking for something the template already answers is the most common way a launch form
    becomes tedious."""
    specs = [ParamSpec(name="rounds", required=True, default=3)]
    result = apply_extraction(specs, {"extracted": {}})
    assert result.extracted == {"rounds": 3}
    assert result.all_filled


def test_a_declined_optional_is_never_re_asked():
    """Asking twice reads as not listening, and the user already answered."""
    result = apply_extraction(
        params("topic", "audience"), {"extracted": {"topic": "x"}}, declined={"audience"}
    )
    assert result.missing == []
    assert result.all_filled


def test_extraction_failure_marks_every_required_field_missing():
    """A failure that silently produced an empty dict would look like a user who said nothing."""
    result = apply_extraction(params("topic", "audience"), "not a dict")
    assert result.failed
    assert set(result.missing) == {"topic", "audience"}
    assert result.follow_up == "extraction_failed"


def test_a_bare_value_map_is_accepted():
    """The `extracted` wrapper is a model's choice, and rejecting a correct answer over its envelope
    would be a repair loop about nothing."""
    result = apply_extraction(params("topic"), {"topic": "cold starts"})
    assert result.extracted == {"topic": "cold starts"}


def test_one_follow_up_covers_everything_missing():
    """Three sequential questions for three fields is three round-trips for information the user
    would have given in one sentence."""
    result = apply_extraction(params("a", "b", "c"), {"extracted": {}})
    assert result.follow_up.count("?") <= 1
    for name in ("a", "b", "c"):
        assert name in result.follow_up


def test_an_empty_value_does_not_count_as_filled():
    result = apply_extraction(params("topic"), {"extracted": {"topic": ""}})
    assert result.missing == ["topic"]


def test_the_extraction_serializes_with_both_flags():
    payload = apply_extraction(params("t"), {"extracted": {}}).to_dict()
    assert payload["all_filled"] is False
    assert payload["extraction_failed"] is False


# ── stage contracts (UP-R3) ──


def test_a_gate_after_a_stage_verifies_it():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("work"),
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "good?"}},
            ],
        }
    }
    contract = next(c for c in derive_contracts(spec) if c.node_id == "work")
    assert contract.verification == "gate"
    assert contract.verifiable


def test_an_approval_gate_is_not_a_MACHINE_check():
    """A human saying yes is a decision, not a check. Counting it would let a plan satisfy the
    minimal triple with nothing but "ask the user"."""
    assert "approval" not in MACHINE_VERIFIED_GATES
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("work"),
                {"kind": "gate", "id": "g", "config": {"kind": "approval"}},
            ],
        }
    }
    contract = next(c for c in derive_contracts(spec) if c.node_id == "work")
    assert contract.verification == "approval"
    assert any("not a machine check" in i for i in contract_issues(derive_contracts(spec)))


def test_a_bounded_enclosing_loop_verifies_its_body():
    """The loop's exit condition IS the check."""
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_dry", "streak": 2},
            "body": {"kind": "sequence", "id": "b", "children": [stage("a"), stage("b")]},
        }
    }
    assert all(c.verification == "loop-condition" for c in derive_contracts(spec))


def test_required_artifacts_verify_a_stage():
    spec = {"root": stage("w", required_artifacts=["report.md"])}
    assert derive_contracts(spec)[0].verification == "artifact"


def test_a_stage_feeding_a_verified_stage_is_not_flagged():
    """The reviewer's findings are exactly what the downstream judge reads. Flagging it would demand
    a gate per stage, turning a three-stage plan into a six-node ceremony."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("review"),
                stage("revise", prompt="fix {{nodes.review.output}}"),
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
            ],
        }
    }
    contracts = derive_contracts(spec)
    reviewer = next(c for c in contracts if c.node_id == "review")
    assert reviewer.feeds_verified
    assert not any("`review`" in i for i in contract_issues(contracts))


def test_a_zero_token_node_is_not_required_to_have_a_judge():
    """An action either succeeded or returned a typed error the engine surfaced. Demanding a model
    call after every write would put an opinion where there is a fact."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("w"),
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
                {"kind": "action", "id": "save", "config": {"provider": "knowledge-persist"}},
            ],
        }
    }
    assert not any("`save`" in i for i in contract_issues(derive_contracts(spec)))


def test_a_plan_with_no_machine_check_is_rejected():
    """The minimal triple: goal, verification, stopping condition."""
    spec = {"root": {"kind": "sequence", "id": "r", "children": [stage("a"), stage("b")]}}
    assert any(i.startswith("no stage") for i in contract_issues(derive_contracts(spec)))


def test_an_all_deterministic_plan_is_exempt():
    """Measured on `knowledge-health`: every node is a zero-token action, so its output already IS
    the check. Demanding a model judge over a deterministic scan spends a call to form an opinion
    about arithmetic — and a rule that fires on correct structure gets suppressed wholesale."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "action", "id": "scan", "config": {"provider": "knowledge-health"}},
                {"kind": "transform", "id": "out", "config": {"expr": "{{nodes.scan.output}}"}},
            ],
        }
    }
    assert not any(i.startswith("no stage") for i in contract_issues(derive_contracts(spec)))


def test_done_means_is_derived_when_the_author_left_it_blank():
    """An empty `done_means` reads as "nobody decided", when in fact the gate decided."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("w"),
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
            ],
        }
    }
    contract = next(c for c in derive_contracts(spec) if c.node_id == "w")
    assert "gate" in contract.done_means


def test_an_unverifiable_contract_carries_its_review_note():
    spec = {"root": {"kind": "sequence", "id": "r", "children": [stage("a"), stage("b")]}}
    payload = derive_contracts(spec)[0].to_dict()
    assert payload["verifiable"] is False
    assert "unverifiable" in payload["review_note"]


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_shipped_template_still_validates(name):
    """The gates added by this session must not break any template."""
    root = spec_of(name)["root"]
    errors = [i for i in validate_node_tree(Node.from_dict(root)).issues if i.severity == "error"]
    assert errors == [], f"{name}: {[i.code for i in errors]}"


@pytest.mark.parametrize(
    "name",
    [
        "knowledge-synthesis",
        "publish-article",
        "thesis-tracker",
        "rich-ingest",
        "gap-healing",
        "knowledge-lint",
    ],
)
def test_the_templates_this_program_authored_have_a_machine_check(name):
    """Measured by the contract lint: all six ended on a write with nothing establishing the work
    was right. `publish-article` had only a human approval — nobody verified the revision addressed
    the accuracy findings before it was stored as reference."""
    issues = contract_issues(derive_contracts(spec_of(name)))
    assert not any(i.startswith("no stage") for i in issues), name


# ── decision typing (UP-R16) ──


def test_a_gate_whose_output_is_consumed_is_blocking():
    """The run cannot proceed correctly without the answer."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {
                    "kind": "gate",
                    "id": "decide",
                    "config": {"kind": "expression", "expr": "{{inputs.x}}"},
                },
                stage("act", prompt="given {{nodes.decide.output}}"),
            ],
        }
    }
    decision = type_decisions(spec)[0]
    assert decision.blocking
    assert "downstream binding" in decision.reason


def test_a_gate_nothing_binds_to_is_an_open_decision():
    """Ambiguity that changes no execution path is answerable after the run."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                stage("work"),
                {"kind": "gate", "id": "note", "config": {"kind": "judge", "prompt": "worth it?"}},
            ],
        }
    }
    decision = type_decisions(spec)[0]
    assert not decision.blocking
    assert open_decisions(type_decisions(spec))


def test_an_approval_gate_is_always_blocking():
    """It exists to pause for a person."""
    spec = {"root": {"kind": "gate", "id": "ask", "config": {"kind": "approval"}}}
    assert type_decisions(spec)[0].blocking


def test_a_destructive_gate_is_always_blocking_whatever_binds_to_it():
    """Auto-proceeding past "may I delete this?" because nothing consumed the answer is the one
    classification error with an unrecoverable cost."""
    spec = {
        "root": {
            "kind": "gate",
            "id": "danger",
            "config": {"kind": "judge", "prompt": "ok?", "risk": "destructive"},
        }
    }
    decision = type_decisions(spec)[0]
    assert decision.blocking
    assert "destructive" in decision.reason


def test_decision_typing_reports_a_severity_for_review():
    spec = {"root": {"kind": "gate", "id": "g", "config": {"kind": "approval"}}}
    assert type_decisions(spec)[0].to_dict()["severity"] == "blocking"


def test_a_spec_with_no_gates_has_no_decisions():
    assert type_decisions({"root": stage("only")}) == []


def test_decision_typing_survives_a_malformed_spec():
    assert type_decisions({"root": "junk"}) == []
    assert type_decisions({}) == []


# ── the real library, end to end ──


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_template_derives_contracts_without_raising(name):
    """These run over author-written specs, so robustness is the contract."""
    spec = spec_of(name)
    derive_contracts(spec)
    type_decisions(spec)
    resolve_unfilled_inputs(spec)


def test_publish_article_records_its_approval_as_a_blocking_decision():
    decisions = {d.node_id: d for d in type_decisions(spec_of("publish-article"))}
    assert decisions["approve"].blocking


def test_the_derived_form_for_a_real_template_is_askable():
    """The end-to-end claim: the launch form comes from the tree, so it cannot drift from it."""
    params_list = resolve_unfilled_inputs(spec_of("publish-article"))
    names = {p.name for p in params_list}
    assert "topic" in names
    required = {p.name for p in params_list if p.required}
    assert "topic" in required
    assert json.dumps([p.to_dict() for p in params_list])
