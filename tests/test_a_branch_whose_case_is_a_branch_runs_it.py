"""A branch is a case like any other step, and the body of a fan-out or a loop like any other.

🔴 The defect (Backlog: "A workflow branch whose taken case is itself a branch stops the run with
no runnable nodes"). A `branch` stores DONE the moment it routes, and its taken case runs after
it; what is finished is derived from the case. The scheduler read three places by the STORED
state instead: the case a branch took (`_visit_branch`), each item of a fan-out
(`_visit_foreach`) and a loop's current cycle (`_visit_loop`). So a case, an item or a cycle that
was itself a branch looked finished as soon as it routed, nothing visited it again to run its own
case, and the run ended "run deadlocked: no runnable nodes and none in flight". The only way
round was to wrap the inner branch in a `sequence`.

And the scheduler found a routed branch's case by resolving its selector again, from a binding
context that does not hold a fan-out's `{{item}}`: a branch routing on its item routed when it
ran, then read as unrouted, and its item's case never ran. It now reads the case the branch
recorded (`{"case": label}`) first.

Driven through the real controller with transform steps only: no model is called.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _runs_in_this_tests_folder(tmp_path, monkeypatch):
    home = tmp_path / "runs"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)


def _leaf(node_id: str, got: str) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"got": got}}}


def _branch(node_id: str, on: str, cases: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "branch", "id": node_id, "config": {"on": on}, "cases": cases}


def _nested(outer_on: str, inner_on: str) -> dict[str, Any]:
    """`outer` routes to `inner` (`go`) or to `halt`; `inner` routes to `leaf_x` or `leaf_y`.
    `inner` is the case itself, not a sequence holding it."""
    inner = _branch("inner", inner_on, {"x": _leaf("leaf_x", "x"), "y": _leaf("leaf_y", "y")})
    return _branch("outer", outer_on, {"go": inner, "stop": _leaf("halt", "stop")})


def _then(first: dict[str, Any], reads: str) -> dict[str, Any]:
    after = {"kind": "transform", "id": "after", "config": {"expr": {"seen": reads}}}
    return {
        "name": "routes",
        "root": {"kind": "sequence", "id": "root", "children": [first, after]},
    }


async def _ran(spec: dict[str, Any], inputs: dict[str, Any]) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices())
    status = await ctl.run_to_completion(timeout=20)
    assert status == RunStatus.COMPLETE, (status, ctl.run.error_message)
    return ctl


@pytest.mark.parametrize("inner_on,got", [("x", "x"), ("y", "y")])
async def test_a_branch_whose_case_is_a_branch_runs_the_inner_case(inner_on, got):
    """🔴 Before: `inner` routed, its case never ran, and the run deadlocked."""
    spec = _then(_nested("{{inputs.a}}", "{{inputs.b}}"), "{{nodes.outer.output}}")
    ctl = await _ran(spec, {"a": "go", "b": inner_on})
    assert ctl._outputs[f"leaf_{got}"] == {"got": got}
    seen = ctl._outputs["after"]["seen"]
    assert seen["case"] == "go" and seen["produced"]["produced"] == {"got": got}, seen
    assert "halt" not in ctl._outputs and f"leaf_{'y' if got == 'x' else 'x'}" not in ctl._outputs


async def test_the_untaken_outer_case_still_routes_around_the_inner_branch():
    """The control: with `stop` taken the inner branch never routes, and the run completes."""
    ctl = await _ran(_then(_nested("{{inputs.a}}", "{{inputs.b}}"), "x"), {"a": "stop", "b": "x"})
    assert ctl._outputs["halt"] == {"got": "stop"} and "inner" not in ctl._outputs


async def test_each_item_of_a_fan_out_runs_its_nested_branchs_case():
    """🔴 Before: both items routed and the run deadlocked. Each item routes on its own item."""
    fan = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": ["x", "y"]},
        "body": _nested("{{inputs.a}}", "{{item}}"),
    }
    ctl = await _ran(_then(fan, "{{nodes.fan.output | map('got')}}"), {"a": "go"})
    assert ctl._outputs["after"] == {"seen": ["x", "y"]}, ctl._outputs["after"]


async def test_a_fan_out_whose_body_is_a_branch_on_its_item_runs_each_items_case():
    """🔴 Before: each item's branch routed on its `{{item}}`, the scheduler could not read it back,
    and no item ran its case. An item no case names takes the default."""
    body = _branch("route", "{{item}}", {"go": _leaf("went", "go"), "stop": _leaf("halt", "stop")})
    body["default"] = _leaf("other", "neither")
    fan = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": ["go", "stop", "maybe", "go"]},
        "body": body,
    }
    ctl = await _ran(_then(fan, "{{nodes.fan.output | map('got')}}"), {})
    assert ctl._outputs["after"] == {"seen": ["go", "stop", "neither", "go"]}, ctl._outputs


async def test_a_loop_whose_body_is_a_branch_runs_its_case_every_cycle():
    """🔴 Before: the first cycle's branch routed, its case never ran, and the run deadlocked."""
    loop = {
        "kind": "loop",
        "id": "cycles",
        "config": {"mode": "counted", "n": 2},
        "body": _nested("{{inputs.a}}", "{{inputs.b}}"),
    }
    ctl = await _ran(_then(loop, "{{nodes.cycles.output.got}}"), {"a": "go", "b": "y"})
    ran = [p for p in ctl.instances if p.endswith(".cases[go].cases[y]")]
    assert sorted(ran) == [
        "root.children[0].body@0.cases[go].cases[y]",
        "root.children[0].body@1.cases[go].cases[y]",
    ], sorted(ctl.instances)
    assert ctl._outputs["after"] == {"seen": "y"}
