"""What a step declares is read the way it is written, and validation says so when it cannot be.

* A branch's `on`, a fan-out's `items` and a condition's operand are read with the one scan that
  splits a value into its references (`bindings.resolve`), so a value holding two references is
  the text they spell. 🔴 Before: each read anything that began with `{{` and ended with `}}` as
  ONE reference, so `{{a}}-{{b}}` looked up a path named `a}}-{{b`; validation, which splits
  values properly, had accepted it.
* A branch whose value cannot be read says that, rather than "matched no case and the node has
  no default" for a branch that has a default.
* `on_error` is `null_continue` or `fail_run`, and anything else is refused when the spec is
  saved. 🔴 Before: `"on_error": "stop"` validated and behaved as `null_continue`, so a step its
  author meant to end the run let the steps after it run.
* An action's own step keys (`on_error`, `retry`, …) are not provider arguments. 🔴 Before: an
  action with no `config.with` was refused for declaring one, told to move it into `with`,
  where the engine would no longer read it.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.conditions import evaluate
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _sequence(*children: dict[str, Any], name: str = "as-written") -> dict[str, Any]:
    return {"name": name, "root": {"kind": "sequence", "id": "s", "children": list(children)}}


def _transform(node_id: str, expr: Any) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": expr}}


async def _run(spec: dict[str, Any], inputs: dict[str, Any]) -> tuple[RunController, RunStatus]:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=dict(inputs)))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices())
    return ctl, await ctl.run_to_completion(timeout=20)


# ── a value holding two references is the text they spell ────────────────────


async def test_a_branch_routes_on_the_text_two_references_spell() -> None:
    """🔴 Red before: the selector looked up `inputs.size}}-{{inputs.speed` and the branch failed
    "matched no case"."""
    spec = _sequence(
        {
            "kind": "branch",
            "id": "route",
            "config": {"on": "{{inputs.size}}-{{inputs.speed}}"},
            "cases": {
                "big-fast": _transform("express", "the express path"),
                "big-slow": _transform("freight", "the freight path"),
            },
        },
    )
    assert validate_spec(spec, strict=True).ok
    ctl, status = await _run(spec, {"size": "big", "speed": "fast"})

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl.instances["root.children[0].cases[big-fast]"].state == InstanceState.DONE
    assert ctl.instances["root.children[0].cases[big-slow]"].state == InstanceState.SKIPPED


async def test_a_fan_out_over_two_references_runs_over_the_text_they_spell() -> None:
    """🔴 Red before: the items never resolved, so the fan-out never started and the run failed
    deadlocked."""
    spec = _sequence(
        {
            "kind": "foreach",
            "id": "each",
            "config": {"items": "{{inputs.first}} and {{inputs.second}}"},
            "body": _transform("say", "item: {{item}}"),
        },
        _transform("after", "done"),
    )
    assert validate_spec(spec, strict=True).ok
    ctl, status = await _run(spec, {"first": "one", "second": "two"})

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert store.read_output(ctl.run.id, "root.children[0].body#0") == "item: one and two"


def test_a_condition_operand_of_two_references_compares_the_text_they_spell() -> None:
    """🔴 Red before: BindingError "unresolved reference at 'a}}{{inputs'"."""
    ctx = BindingContext(inputs={"a": "x", "b": "y"})
    assert evaluate("{{inputs.a}}{{inputs.b}} == 'xy'", ctx) is True
    assert evaluate("{{inputs.a}}-{{inputs.b}} != 'x-y'", ctx) is False


def test_one_reference_and_a_bare_path_still_read_as_before() -> None:
    """Positive control: a single reference keeps its type, a bare path is a reference, and a
    quoted operand is text even when it holds braces."""
    ctx = BindingContext(inputs={"n": 3, "flag": True})
    assert evaluate("{{inputs.n}} == 3", ctx) is True
    assert evaluate("inputs.flag", ctx) is True
    assert evaluate("'{{inputs.n}}' == '{{inputs.n}}'", ctx) is True


# ── a branch that cannot read its value says so ──────────────────────────────


async def test_a_branch_that_cannot_read_its_value_says_so() -> None:
    """The value it routes on is missing, and the branch HAS a default. 🔴 Red before: it failed
    "branch selector matched no case and the node has no default", false on both counts."""
    spec = _sequence(
        _transform("triage", {"why": "the classifier left out the tier"}),
        {
            "kind": "branch",
            "id": "route",
            "config": {"on": "{{nodes.triage.output.tier}}"},
            "cases": {"deep": _transform("dig", "dig")},
            "default": _transform("skim", "skim"),
        },
    )
    ctl, status = await _run(spec, {})

    assert status == RunStatus.FAILED
    failure = ctl.instances["root.children[1]"].failure
    assert failure is not None
    assert failure.cause_plain == (
        "branch could not read the value it routes on: unresolved reference at 'tier' "
        "(in {{nodes.triage.output.tier}})"
    )
    assert "no default" not in failure.cause_plain


async def test_a_branch_whose_value_matches_no_case_names_the_value() -> None:
    spec = _sequence(
        _transform("triage", {"tier": "medium"}),
        {
            "kind": "branch",
            "id": "route",
            "config": {"on": "{{nodes.triage.output.tier}}"},
            "cases": {"deep": _transform("dig", "dig"), "light": _transform("skim", "skim")},
        },
    )
    ctl, status = await _run(spec, {})

    assert status == RunStatus.FAILED
    failure = ctl.instances["root.children[1]"].failure
    assert failure is not None
    assert failure.cause_plain == (
        "the value this branch routes on is 'medium', which matches none of its cases "
        "(deep, light), and it has no default"
    )


# ── on_error is one of the two the engine reads ──────────────────────────────


@pytest.mark.parametrize("value", ["null_continue", "fail_run"])
def test_the_two_on_error_values_validate(value: str) -> None:
    spec = _sequence({"kind": "transform", "id": "t", "config": {"expr": 1, "on_error": value}})
    assert validate_spec(spec, strict=True).ok


@pytest.mark.parametrize("value", ["stop", "continue", "Fail_Run", True])
def test_any_other_on_error_is_refused_when_saved(value: Any) -> None:
    spec = _sequence({"kind": "transform", "id": "t", "config": {"expr": 1, "on_error": value}})
    result = validate_spec(spec)

    refused = [i for i in result.errors if i.code == "WF_BAD_ON_ERROR"]
    assert [i.path for i in refused] == ["root.children[0]"], result.to_dict()
    assert refused[0].message == (
        f"unknown on_error {value!r} — a step's on_error is null_continue (the steps after it "
        "still run) or fail_run (its failure ends the run)"
    )


# ── an action's own step keys are not arguments ──────────────────────────────


@pytest.mark.parametrize(
    "keys",
    [
        {"on_error": "fail_run"},
        {"retry": {"max_attempts": 2}},
        {"success_when": "{{output.ok}}"},
        {"allow_failure": True, "persists_memory": False},
    ],
)
def test_an_action_without_arguments_may_declare_its_own_step_keys(keys: dict[str, Any]) -> None:
    spec = _sequence({"kind": "action", "id": "ping", "config": {"provider": "notify", **keys}})
    codes = [i.code for i in validate_spec(spec).errors]
    assert "WF_ACTION_ARGS_NOT_NESTED" not in codes, codes


def test_an_argument_written_flat_is_still_refused_and_only_it_is_named() -> None:
    spec = _sequence(
        {
            "kind": "action",
            "id": "build",
            "config": {"provider": "bash", "command": "make test", "on_error": "fail_run"},
        }
    )
    refused = [i for i in validate_spec(spec).errors if i.code == "WF_ACTION_ARGS_NOT_NESTED"]

    assert [i.message for i in refused] == [
        "action arguments go under `config.with` — move command into it"
    ]
