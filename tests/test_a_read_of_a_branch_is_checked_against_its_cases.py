"""Validation refuses a read of a branch's output that the branch, or its case, never produces.

A branch's output is `{"case": <the case it took>, "produced": <what that case produced>}`, so a
step reads which case ran as `{{nodes.<branch>.output.case}}` and what it produced as
`{{nodes.<branch>.output.produced…}}`. 🔴 Before: validation accepted a read of any field of a
branch, and the run failed "unresolved reference" at the step after the branch, after everything
before it had spent its tokens.

* Any field beside those two is never there, and the refusal names the read that works.
* Under `produced`, where a case's output is known when the spec is saved (a transform's object, a
  stage that declares no schema and so answers `{"result": …}`), a field that case does not carry
  is a read that fails every time that case is taken.

Refused only where it is certain: a case whose output a model or a provider shapes is not held to
any key list, and every bundled template still validates.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bindings import refs_in
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun, spec_path, walk
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio

REFUSED = "WF_UNSATISFIABLE_OUTPUT_REF"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _shape(node_id: str, expr: Any) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": expr}}


SHAPES = {
    "lookup": _shape("shape_lookup", {"tier": "lookup", "angles": ["by-content"], "depth": 1}),
    "survey": _shape("shape_survey", {"tier": "survey", "angles": ["by-entity"], "depth": 2}),
}


def _spec(read: str, cases: dict[str, Any] | None = None, **branch: Any) -> dict[str, Any]:
    return {
        "name": "reads-a-branch",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                _shape("triage", {"tier": "{{inputs.tier}}"}),
                {
                    "kind": "branch",
                    "id": "entry",
                    "config": {"on": "{{nodes.triage.output.tier}}"},
                    "cases": cases if cases is not None else SHAPES,
                    **branch,
                },
                _shape("sweep", read),
            ],
        },
    }


def _refusals(spec: dict[str, Any]) -> list[str]:
    return [i.message for i in validate_spec(spec).errors if i.code == REFUSED]


@pytest.mark.parametrize(
    "read",
    [
        "{{nodes.entry.output}}",
        "{{nodes.entry.output.case}}",
        "{{nodes.entry.output.produced}}",
        "{{nodes.entry.output.produced.angles}}",
        "depth {{nodes.entry.output.produced.depth}} for {{nodes.entry.output.case}}",
    ],
)
def test_which_case_ran_and_a_field_every_case_produces_validate(read: str) -> None:
    assert validate_spec(_spec(read), strict=True).ok


def test_a_field_the_branch_never_has_is_refused_with_the_read_that_works() -> None:
    """The slip both shipped templates made: reading the case's field straight off the branch.
    🔴 Red before: the spec validated, and the read failed at run time on every path."""
    assert _refusals(_spec("{{nodes.entry.output.angles}}")) == [
        "'sweep' reads {{nodes.entry.output.angles}}, but 'entry' is a branch, whose output is "
        '{"case": <the case it took>, "produced": <what that case produced>}, so it never has '
        "'angles'. Read what the case produced as {{nodes.entry.output.produced.angles}}"
    ]


def test_a_field_no_case_produces_is_refused_with_what_each_case_does_produce() -> None:
    refused = _refusals(_spec("{{nodes.entry.output.produced.breadth}}"))

    assert refused == [
        "'sweep' reads {{nodes.entry.output.produced.breadth}}, but 'entry' is a branch, whose "
        "`produced` is what the case it took produced, and no case produces 'breadth' (lookup: "
        "angles, depth, tier; survey: angles, depth, tier) — so the read can never resolve. Give "
        "the cases that field, or read one they produce"
    ]


def test_a_field_one_case_lacks_is_refused_naming_that_case() -> None:
    """The read resolves when `survey` runs and fails whenever `lookup` does."""
    cases = {
        "lookup": _shape("shape_lookup", {"angles": ["by-content"]}),
        "survey": _shape("shape_survey", {"angles": ["by-entity"], "breadth": 3}),
    }
    refused = _refusals(_spec("{{nodes.entry.output.produced.breadth}}", cases))

    assert refused == [
        "'sweep' reads {{nodes.entry.output.produced.breadth}}, but 'entry' is a branch, whose "
        "`produced` is what the case it took produced, and case 'lookup' produces no 'breadth' "
        "(it produces angles) — so the read fails whenever that case is taken. Give that case the "
        "field too, or read it inside the case that produces it"
    ]


@pytest.mark.parametrize(
    "read",
    [
        "{{nodes.entry.output.produced.breadth | default(1)}}",
        "{{nodes.entry.output.angles | default('by-content')}}",
    ],
)
def test_a_default_pipe_does_not_rescue_a_missing_field(read: str) -> None:
    """A `| default(…)` runs only once the reference resolves, so it cannot save it."""
    assert _refusals(_spec(read))


def test_a_schemaless_stage_case_is_known_to_produce_only_its_result() -> None:
    """A stage that declares no schema answers `{"result": <its text>}`, so that is all a step
    after the branch can read of it on that path."""
    look = {"kind": "stage", "id": "look", "config": {"prompt": "Look around."}}
    spec = _spec("{{nodes.entry.output.produced.result}}", {"lookup": look})
    assert validate_spec(spec, strict=True).ok

    refused = _refusals(
        _spec(
            "{{nodes.entry.output.produced.angles}}",
            {"lookup": look, **{"survey": SHAPES["survey"]}},
        )
    )
    assert len(refused) == 1, refused
    assert "case 'lookup' produces no 'angles' (it produces result)" in refused[0]


def test_a_case_whose_output_a_model_decides_is_not_held_to_a_key_list() -> None:
    """A stage with a schema, an infer, an action: what comes back is the model's or the
    provider's, so validation accepts the read rather than guess."""
    cases = {
        "lookup": {
            "kind": "stage",
            "id": "look",
            "config": {"prompt": "Look around.", "schema": {"findings": "array"}},
        },
        "survey": {"kind": "infer", "id": "ask", "config": {"prompt": "Answer."}},
        "investigation": {"kind": "action", "id": "fetch", "config": {"provider": "http"}},
    }
    result = validate_spec(_spec("{{nodes.entry.output.produced.anything}}", cases))
    assert REFUSED not in [i.code for i in result.errors], result.to_dict()


def test_a_case_of_several_steps_produces_what_each_of_them_does() -> None:
    cases = {
        "lookup": {
            "kind": "sequence",
            "id": "gather",
            "children": [_shape("look", {"found": "x"}), _shape("check", {"checked": True})],
        },
        "survey": _shape("shape_survey", {"found": "y", "checked": False}),
    }
    assert validate_spec(_spec("{{nodes.entry.output.produced.checked}}", cases), strict=True).ok
    refused = _refusals(_spec("{{nodes.entry.output.produced.angles}}", cases))
    assert len(refused) == 1, refused
    assert (
        "no case produces 'angles' (lookup: checked, found; survey: checked, found)" in refused[0]
    )


def test_a_default_case_is_a_case_too() -> None:
    spec = _spec(
        "{{nodes.entry.output.produced.angles}}",
        {"lookup": SHAPES["lookup"]},
        default=_shape("otherwise", {"note": "nothing to shape"}),
    )
    refused = _refusals(spec)
    assert len(refused) == 1, refused
    assert "the default case produces no 'angles' (it produces note)" in refused[0]


async def test_what_validation_refuses_is_what_a_run_cannot_resolve() -> None:
    """Held to the engine: each refused read fails on a path validation named, and each accepted
    one resolves on every path."""
    cases = {
        "lookup": _shape("shape_lookup", {"angles": ["by-content"]}),
        "survey": _shape("shape_survey", {"angles": ["by-entity"], "breadth": 3}),
    }
    table: dict[tuple[str, str], tuple[bool, bool]] = {}
    for read in ("case", "produced.angles", "produced.breadth", "angles"):
        spec = _spec(f"{{{{nodes.entry.output.{read}}}}}", cases)
        accepted = not _refusals(spec)
        for tier in ("lookup", "survey"):
            run = store.create(
                WorkflowRun(id="", workflow_name=spec["name"], inputs={"tier": tier})
            )
            store.write_spec(run.id, spec)
            ctl = RunController(run, spec, services=EngineServices())
            status = await ctl.run_to_completion(timeout=20)
            nodes = dict(walk(ctl.root))
            sweep = [
                inst.state
                for path, inst in ctl.instances.items()
                if nodes[spec_path(path)].id == "sweep"
            ]
            resolved = status == RunStatus.COMPLETE and sweep == [InstanceState.DONE]
            table[(read, tier)] = (accepted, resolved)

    assert table == {
        ("case", "lookup"): (True, True),
        ("case", "survey"): (True, True),
        ("produced.angles", "lookup"): (True, True),
        ("produced.angles", "survey"): (True, True),
        ("produced.breadth", "lookup"): (False, False),
        ("produced.breadth", "survey"): (False, True),
        ("angles", "lookup"): (False, False),
        ("angles", "survey"): (False, False),
    }, table


def test_every_bundled_template_still_validates_and_its_branch_reads_were_checked() -> None:
    """The shipped library validates strictly, and the rule really examined its reads:
    deep-research reads its shaping branch's fields, each of which every one of its cases
    produces."""
    for name in template_names():
        template = read_template(name)
        assert template is not None, name
        result = validate_spec(template.to_dict(), strict=True)
        assert result.ok and not result.warnings, (name, result.summary())

    deep = read_template("deep-research")
    assert deep is not None
    reads = {
        expr
        for _path, node in walk(deep.root)
        for expr in refs_in(node.config or {})
        if expr.startswith("nodes.entry.output.")
    }
    assert reads >= {
        "nodes.entry.output.produced.angles",
        "nodes.entry.output.produced.breadth",
        "nodes.entry.output.produced.depth",
    }, reads
    # Positive controls: the same template reading a field its cases lack, or reading a field
    # straight off the branch, is refused.
    for slip, said in (
        ("{{nodes.entry.output.produced.width}}", "no case produces 'width'"),
        ("{{nodes.entry.output.width}}", "so it never has 'width'"),
    ):
        spec = deep.to_dict()
        spec["root"]["children"][2]["body"]["children"][0]["config"]["prompt"] += " " + slip
        assert any(said in message for message in _refusals(spec)), slip
