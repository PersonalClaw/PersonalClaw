"""Every read the engine makes on behalf of a step inside a fan-out item reads THAT item's steps.

A step's own bindings already resolve against its own item (`BindingContext.scoped_to`). These are
the other reads the engine makes for a step, each of which used to go by node id, and so read
whichever item last wrote that id. Each control sets up two or more items whose values differ, with
another item's value written last, and checks the read made for an item is that item's own:

* `{{nodes.X.artifact}}`, the pointer to an offloaded output;
* a metric gate's `metric_from` (a step let through on another item's score);
* the inputs an approval gate's continuation records;
* the `max_concurrency` count (an item whose case is still running read as finished);
* a loop's progress field and its judge's ruling at the cycle boundary;
* the output a loop's cycle boundary reads off its last leaf, which the handoff is taken from;
* and ``{{item}}`` itself, read by a step in a loop inside the item.

The run-level controls drive the real controller with a fake model; the loop-boundary reads and the
concurrency count are read straight off the functions that make them, over a fixed state, since a
run that reaches those states by timing alone would pass or fail by luck.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeInstance,
    WorkflowRun,
    spec_path,
    walk,
)
from personalclaw.workflows.tick import Limits, frontier

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _runs_in_this_tests_folder(tmp_path, monkeypatch):
    home = tmp_path / "runs"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _fan(items: list[str], body: list[dict]) -> dict:
    return {
        "name": "item-reads",
        "root": {
            "kind": "sequence",
            "id": "main",
            "children": [
                {"kind": "transform", "id": "src", "config": {"expr": {"items": items}}},
                {
                    "kind": "foreach",
                    "id": "fan",
                    "config": {"items": "{{nodes.src.output.items}}", "on_item_error": "skip"},
                    "body": {"kind": "sequence", "id": "one", "children": body},
                },
            ],
        },
    }


def _together(items: list[str], answer):
    """A model whose calls all wait until one call per item has arrived, then all answer at once.
    They settle in one pass, in the order the items were launched, so the LAST item's answer is
    the one left under the node id: no timing decides which."""
    released = asyncio.Event()
    arrived: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        item = prompt.split("::", 1)[1].strip()
        arrived.append(item)
        if len(arrived) == len(items):
            released.set()
        await released.wait()
        return json.dumps(answer(item))

    return fn


def _controller(run: WorkflowRun, spec: dict, fn, *, lanes: int = 8) -> RunController:
    return RunController(
        run,
        spec,
        services=EngineServices(
            completion=fn, lane_limits=Limits(lanes={"llm": lanes, "io": 2, "compute": 64})
        ),
    )


def _run(spec: dict) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    return run


def _per_item(ctl: RunController, node_id: str) -> dict[str, NodeInstance]:
    """item index → the instance of *node_id* in that item."""
    by_path = {path: node.id for path, node in walk(ctl.root)}
    out: dict[str, NodeInstance] = {}
    for path, inst in ctl.instances.items():
        if "body#" in path and by_path.get(spec_path(path)) == node_id:
            out[path.split("body#", 1)[1].split(".", 1)[0]] = inst
    return out


# ── artifacts ────────────────────────────────────────────────────────────────


async def test_an_items_artifact_read_is_its_own_offloaded_output() -> None:
    items = ["I0", "I1", "I2", "I3"]
    big = {"kind": "infer", "id": "big", "config": {"prompt": "BIG :: {{item}}"}}
    read = {
        "kind": "transform",
        "id": "read",
        "config": {"expr": {"artifact": "{{nodes.big.artifact}}"}},
    }
    spec = _fan(items, [big, read])
    run = _run(spec)
    # Over the inline boundary, so each item's output is offloaded and has an artifact ref.
    fn = _together(items, lambda item: {"item": item, "blob": "x" * (70 * 1024)})
    ctl = _controller(run, spec, fn)
    status = await ctl.run_to_completion(timeout=60)
    assert status.value == "complete", store.get(run.id).error_message
    bigs, reads = _per_item(ctl, "big"), _per_item(ctl, "read")
    assert all(not b.output_ref.startswith("outputs/") for b in bigs.values()), "premise: offloaded"
    wrong = {
        index: store.read_output(run.id, reads[index].path)["artifact"]
        for index in reads
        if store.read_output(run.id, reads[index].path)["artifact"] != bigs[index].output_ref
    }
    assert wrong == {}, f"items read another item's artifact: {wrong}"


# ── the metric gate ──────────────────────────────────────────────────────────


async def test_a_metric_gate_scores_the_step_on_its_own_items_metric() -> None:
    """I0 scores 0.1 and I1 0.9, and I1's score is the one left under the node id. Read by node
    id, I0's ship ran on I1's 0.9; on its own 0.1 it is held."""
    items = ["I0", "I1"]
    verify = {
        "kind": "infer",
        "id": "verify",
        "config": {"prompt": "SCORE :: {{item}}", "schema": {"score": "number"}},
    }
    ship = {
        "kind": "transform",
        "id": "ship",
        "config": {
            "expr": {"shipped": "{{item}}"},
            "metric_from": "{{nodes.verify.output.score}}",
            "metric_pass": 0.5,
            "metric_hold": 0.05,
        },
    }
    spec = _fan(items, [verify, ship])
    run = _run(spec)
    fn = _together(items, lambda item: {"score": 0.1 if item == "I0" else 0.9})
    ctl = _controller(run, spec, fn)
    await ctl.run_to_completion(timeout=60)
    ships = _per_item(ctl, "ship")
    assert ships["1"].state == InstanceState.DONE, "premise: I1's own 0.9 passes its gate"
    assert "0" not in ships or ships["0"].state != InstanceState.DONE, "I0's ship ran on I1's score"


# ── an approval gate's continuation ──────────────────────────────────────────


async def test_an_approval_gates_continuation_records_its_own_items_inputs() -> None:
    from personalclaw.workflows.human_input import list_continuations

    items = ["I0", "I1", "I2", "I3"]
    echo = {
        "kind": "infer",
        "id": "echo",
        "config": {"prompt": "ECHO :: {{item}}", "schema": {"echoed": "string"}},
    }
    gate = {
        "kind": "gate",
        "id": "hold",
        "config": {"kind": "approval", "prompt": "ship {{nodes.echo.output.echoed}}?"},
    }
    spec = _fan(items, [echo, gate])
    run = _run(spec)
    ctl = _controller(run, spec, _together(items, lambda item: {"echoed": item}))
    await ctl.start()
    try:
        for _ in range(500):
            if len(list_continuations(run.id)) == len(items):
                break
            await asyncio.sleep(0.01)
        conts = list_continuations(run.id)
        assert len(conts) == len(items), "premise: every item's gate is waiting"
        recorded = {
            c.instance_path.split("body#", 1)[1].split(".", 1)[0]: c.resolved_inputs["echo"]
            for c in conts
        }
        wrong = {i: r for i, r in recorded.items() if r != {"echoed": items[int(i)]}}
        assert wrong == {}, f"a gate recorded another item's inputs: {wrong}"
    finally:
        await ctl.stop()


# ── the concurrency count ────────────────────────────────────────────────────


def test_an_item_whose_case_is_running_counts_against_the_cap() -> None:
    """max_concurrency 2. I0 took its even case, still running; I1 took its odd case and is done,
    and I1's odd echo is the one under the node id. Judged on I1's echo, I0's running case reads
    as the untaken, skipped one, I0 reads as finished, and the cap admits two new items."""
    root = Node.from_dict(
        {
            "kind": "foreach",
            "id": "fan",
            "config": {"items": ["I0", "I1", "I2", "I3"], "max_concurrency": 2},
            "body": {
                "kind": "sequence",
                "id": "one",
                "children": [
                    {"kind": "infer", "id": "echo", "config": {"prompt": "ECHO {{item}}"}},
                    {
                        "kind": "branch",
                        "id": "route",
                        "config": {"on": "{{nodes.echo.output.even}}"},
                        "cases": {
                            "true": {"kind": "infer", "id": "held", "config": {"prompt": "x"}},
                            "false": {"kind": "transform", "id": "odd", "config": {"expr": 1}},
                        },
                    },
                ],
            },
        }
    )
    done, running, skipped = InstanceState.DONE, InstanceState.RUNNING, InstanceState.SKIPPED
    states = {
        "root.body#0.children[0]": done,
        "root.body#0.children[1]": done,
        "root.body#0.children[1].cases[true]": running,
        "root.body#0.children[1].cases[false]": skipped,
        "root.body#1.children[0]": done,
        "root.body#1.children[1]": done,
        "root.body#1.children[1].cases[false]": done,
        "root.body#1.children[1].cases[true]": skipped,
    }
    fr = frontier(
        root,
        states,
        outputs={"echo": {"even": False}},
        instance_outputs={
            "root.body#0.children[0]": ("echo", {"even": True}),
            "root.body#1.children[0]": ("echo", {"even": False}),
        },
    )
    started = sorted(r.path for r in fr.ready)
    assert started == ["root.body#2.children[0]"], f"the cap admitted {started}"


# ── a loop's cycle boundary inside an item ───────────────────────────────────

_LOOP_FAN = {
    "kind": "foreach",
    "id": "fan",
    "config": {"items": ["I0", "I1"]},
    "body": {
        "kind": "loop",
        "id": "lp",
        "config": {"mode": "until_dry", "progress_field": "new", "max_iterations": 3},
        "body": {
            "kind": "sequence",
            "id": "cycle",
            "children": [
                {"kind": "infer", "id": "scan", "config": {"prompt": "x"}},
                {
                    "kind": "stage",
                    "id": "judge",
                    "config": {"prompt": "judge", "judge_contract": True},
                },
            ],
        },
    },
}


def _cycle_ctl(scan: dict[str, dict], judge: dict[str, dict]) -> SimpleNamespace:
    """A run standing at cycle 0 of each item's loop, both steps done, with I1's answers the ones
    under the node ids."""
    root = Node.from_dict(_LOOP_FAN)
    instances, by_instance = {}, {}
    for index, item in enumerate(["I0", "I1"]):
        for child, node_id, value in ((0, "scan", scan[item]), (1, "judge", judge[item])):
            path = f"root.body#{index}.body@0.children[{child}]"
            instances[path] = NodeInstance(path=path, state=InstanceState.DONE)
            by_instance[path] = (node_id, value)
    return SimpleNamespace(
        root=root,
        instances=instances,
        _outputs={"scan": scan["I1"], "judge": judge["I1"]},
        _instance_outputs=by_instance,
        _declined_edges=set(),
        _iterations={},
        run=SimpleNamespace(inputs={}, id=""),
    )


def test_a_loops_progress_field_is_read_from_its_own_items_cycle() -> None:
    from personalclaw.workflows.loop_iteration import _progress_value

    ctl = _cycle_ctl(
        scan={"I0": {"new": 3}, "I1": {"new": 0}},
        judge={"I0": {"verdict": "PASS"}, "I1": {"verdict": "PASS"}},
    )
    loop = ctl.root.body
    assert _progress_value(ctl, loop, "root.body#0", 0, "new") == (True, 3)
    assert _progress_value(ctl, loop, "root.body#1", 0, "new") == (True, 0)


def test_a_loops_judge_ruling_is_its_own_items_judge() -> None:
    from personalclaw.workflows.ending_sentence import judge_ruling

    ctl = _cycle_ctl(
        scan={"I0": {"new": 0}, "I1": {"new": 0}},
        judge={"I0": {"verdict": "PASS"}, "I1": {"verdict": "ESCALATE"}},
    )
    loop = ctl.root.body
    assert judge_ruling(ctl, loop, "root.body#0", 0) == {"verdict": "PASS"}
    assert judge_ruling(ctl, loop, "root.body#1", 0) == {"verdict": "ESCALATE"}


def _loop_per_item(work_config: dict) -> dict:
    """A fan-out over two items, each running a one-cycle counted loop of one ``work`` step."""
    return {
        "name": "loop-in-item",
        "root": {
            "kind": "foreach",
            "id": "fan",
            "config": {"items": ["I0", "I1"], "on_item_error": "skip"},
            "body": {
                "kind": "loop",
                "id": "lp",
                "config": {"mode": "counted", "n": 1},
                "body": {"kind": "infer", "id": "work", "config": work_config},
            },
        },
    }


async def test_a_loop_inside_an_item_hands_its_body_the_item() -> None:
    """A step in a loop inside a fan-out item reads ``{{item}}``. The loop used to hand its body
    no item, so the read failed as unresolved in every item."""
    spec = _loop_per_item({"prompt": "WORK :: {{item}}", "schema": {"seen": "string"}})
    run = _run(spec)
    fn = _together(["I0", "I1"], lambda item: {"seen": item})
    ctl = _controller(run, spec, fn)
    await ctl.run_to_completion(timeout=60)
    works = _per_item(ctl, "work")
    failed = {i: w.failure.cause_plain for i, w in works.items() if w.failure is not None}
    assert failed == {}, f"a loop's step could not read its item: {failed}"
    seen = {i: store.read_output(run.id, w.path)["seen"] for i, w in works.items()}
    assert seen == {"0": "I0", "1": "I1"}, seen


async def test_a_failed_cycle_hands_on_nothing_from_another_items_cycle() -> None:
    """In each item a counted loop runs one cycle of one step that reads ``{{item}}``. I1's step
    succeeds first and hands on notes; I0's then fails. The cycle boundary reads the settled
    leaf's output: by node id that was I1's, and I0's loop journaled I1's notes as its own
    handoff."""
    spec = _loop_per_item({"prompt": "WORK :: {{item}}", "schema": {"handoff": "object"}})
    run = _run(spec)

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        item = prompt.split("::", 1)[1].strip()
        if item == "I0":
            await asyncio.sleep(0.2)
            raise RuntimeError("I0 broke")
        return json.dumps({"handoff": {"verified_state": f"{item} cycle 0 notes"}})

    ctl = _controller(run, spec, fn)
    with contextlib.suppress(Exception):
        await ctl.run_to_completion(timeout=60)
    handoffs = {
        rec.get("instance_path"): rec.get("verified_state")
        for rec in J.journal_records(run.id, kinds={J.HANDOFF})
    }
    i0, i1 = "root.body#0", "root.body#1"
    assert handoffs.get(i1) == "I1 cycle 0 notes", f"premise: {handoffs}"
    assert i0 not in handoffs, f"I0's failed cycle handed on another item's: {handoffs}"
