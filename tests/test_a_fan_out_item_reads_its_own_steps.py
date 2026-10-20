"""A step inside a fan-out item reads the steps of ITS item, never another item's.

Every item of a ``foreach`` runs the same body, so its steps share node ids, and the run keeps
outputs by node id: before this, a step in item 1 that bound its sibling (``{{nodes.echo.output}}``)
read whichever item's ``echo`` had finished last. Nothing failed — the run completed with one item's
data inside another — which is why each control here compares every item's own value with what its
step was handed, rather than looking for an error.

Driven through the real controller; the model call is the only fake. It answers in an order fixed
per test (no randomness at run time), so a run that passes did not pass by luck.

* bindings: each item pairs its ``{{item}}`` with its sibling's answer;
* nesting: the same read from inside containers nested within the item;
* routing: a branch inside each item takes the case ITS item's answer selects;
* completion: a fan-out is not done while an item's case is still running, whatever case another
  item's answer selects;
* branch record: a step after a branch in each item reads what ITS item's case produced;
* restart: the read after a controller is rebuilt from disk part-way through the fan-out;
* outside: a read from after the fan-out keeps its meaning, the last item to finish;
* fail closed: a context scoped to an item that has not produced a node does not hand it another
  item's output (a unit check on ``BindingContext.scoped_to``; the scheduler does not let a step
  read a sibling before it has produced in its own item, so no run reaches this today).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun, spec_path, walk
from personalclaw.workflows.tick import Limits

pytestmark = pytest.mark.anyio

N = 8
ITEMS = [f"I{i}" for i in range(N)]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _runs_in_this_tests_folder(tmp_path, monkeypatch):
    home = tmp_path / "runs"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec(
    body_after_echo: list[dict], *, after: list[dict] | None = None, items: list[str] = ITEMS
) -> dict:
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
                    "body": {
                        "kind": "sequence",
                        "id": "one",
                        "children": [
                            {
                                "kind": "infer",
                                "id": "echo",
                                "config": {
                                    "prompt": "ECHO {{item}}",
                                    "schema": {"echoed": "string", "even": "boolean"},
                                },
                            },
                            *body_after_echo,
                        ],
                    },
                },
                *(after or []),
            ],
        },
    }


_PAIR = {
    "kind": "transform",
    "id": "pair",
    "config": {"expr": {"item": "{{item}}", "seen": "{{nodes.echo.output.echoed}}"}},
}


def _echo(delays: dict[str, float]):
    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        item = prompt.split("ECHO", 1)[1].strip()
        await asyncio.sleep(delays.get(item, 0.0))
        return json.dumps({"echoed": item, "even": int(item[1:]) % 2 == 0})

    return fn


def _delays(seed: int) -> dict[str, float]:
    """Each item's model call takes a fixed, distinct time for this seed: items finish in an order
    unlike the order they started."""
    rng = random.Random(seed)
    steps = rng.sample(range(N), N)
    return {item: 0.01 * step for item, step in zip(ITEMS, steps)}


def _together(seed: int):
    """A model whose N calls all wait until every item's call has arrived, then all answer at once,
    released in an order fixed by *seed*. They finish in one batch, before the next step of any
    item is started: the shape in which every item's next step used to read the one answer
    applied last. No timing decides it, so a run cannot pass by luck."""
    order = random.Random(seed).sample(range(N), N)
    released = {item: asyncio.Event() for item in ITEMS}
    arrived: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        item = prompt.split("ECHO", 1)[1].strip()
        arrived.append(item)
        if len(arrived) == N:
            for index in order:
                released[ITEMS[index]].set()
        await released[item].wait()
        return json.dumps({"echoed": item, "even": int(item[1:]) % 2 == 0})

    return fn


def _controller(run: WorkflowRun, spec: dict, fn) -> RunController:
    # Every item's model call in flight at once, so they overlap as a busy run's do.
    return RunController(
        run,
        spec,
        services=EngineServices(
            completion=fn, lane_limits=Limits(lanes={"llm": N, "io": 2, "compute": 64})
        ),
    )


def _run(spec: dict) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    return run


def _outputs_of(ctl: RunController, run_id: str, node_id: str) -> list[dict]:
    """Every item's output of *node_id*, read from disk by instance path."""
    by_id = {path: node.id for path, node in walk(ctl.root)}
    found = []
    for path, inst in ctl.instances.items():
        if "body#" in path and by_id.get(spec_path(path)) == node_id and inst.output_ref:
            found.append(store.read_output(run_id, path))
    return found


def _mismatched(pairs: list[dict]) -> list[dict]:
    return [p for p in pairs if p.get("item") != p.get("seen")]


@pytest.mark.parametrize("seed", range(5))
async def test_each_item_pairs_its_item_with_its_own_siblings_answer(seed: int) -> None:
    spec = _spec([_PAIR])
    run = _run(spec)
    ctl = _controller(run, spec, _together(seed))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    pairs = _outputs_of(ctl, run.id, "pair")
    assert len(pairs) == N, pairs
    assert _mismatched(pairs) == [], f"items read another item's step: {_mismatched(pairs)}"


async def test_a_read_from_containers_nested_in_the_item_is_its_own() -> None:
    nested = {
        "kind": "parallel",
        "id": "legs",
        "children": [{"kind": "sequence", "id": "leg", "children": [_PAIR]}],
    }
    spec = _spec([nested])
    run = _run(spec)
    ctl = _controller(run, spec, _together(0))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    pairs = _outputs_of(ctl, run.id, "pair")
    assert len(pairs) == N, pairs
    assert _mismatched(pairs) == [], f"items read another item's step: {_mismatched(pairs)}"


async def test_a_branch_in_each_item_takes_the_case_its_own_item_selects() -> None:
    route = {
        "kind": "branch",
        "id": "route",
        "config": {"on": "{{nodes.echo.output.even}}"},
        "cases": {
            "true": {
                "kind": "transform",
                "id": "took_even",
                "config": {"expr": {"item": "{{item}}", "case": "even"}},
            },
            "false": {
                "kind": "transform",
                "id": "took_odd",
                "config": {"expr": {"item": "{{item}}", "case": "odd"}},
            },
        },
    }
    spec = _spec([route])
    run = _run(spec)
    ctl = _controller(run, spec, _together(1))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    taken = _outputs_of(ctl, run.id, "took_even") + _outputs_of(ctl, run.id, "took_odd")
    assert sorted(t["item"] for t in taken) == sorted(ITEMS), taken
    wrong = [t for t in taken if t["case"] != ("even" if int(t["item"][1:]) % 2 == 0 else "odd")]
    assert wrong == [], f"an item took the case another item's answer selected: {wrong}"


async def test_a_fan_out_is_not_done_while_an_items_case_is_still_running() -> None:
    """Item I0 (even) takes a case that is held; item I1 (odd) answers after it and takes the
    other. Whether the fan-out has ended is judged per item: judged on I1's answer, I0's held case
    reads as the untaken, skipped one, the fan-out reads as done, and the step after it starts
    while I0 is still working."""
    route = {
        "kind": "branch",
        "id": "route",
        "config": {"on": "{{nodes.echo.output.even}}"},
        "cases": {
            "true": {
                "kind": "infer",
                "id": "held_even",
                "config": {"prompt": "SLOW {{item}}", "schema": {"echoed": "string"}},
            },
            "false": {"kind": "transform", "id": "took_odd", "config": {"expr": {"odd": True}}},
        },
    }
    after = [{"kind": "transform", "id": "after", "config": {"expr": {"after": True}}}]
    spec = _spec([route], after=after, items=["I0", "I1"])
    run = _run(spec)
    release = asyncio.Event()
    slow: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        if prompt.startswith("SLOW"):
            slow.append(prompt)
            await release.wait()
            return json.dumps({"echoed": "slow"})
        # I1 answers after I0, so the last answer under the node id is the odd one.
        return await _echo({"I0": 0.0, "I1": 0.05})(prompt)

    ctl = _controller(run, spec, fn)
    task = asyncio.create_task(ctl.run_to_completion(timeout=60))
    for _ in range(500):
        if slow and "took_odd" in ctl._outputs:
            break
        await asyncio.sleep(0.01)
    assert slow and "took_odd" in ctl._outputs, "premise: I0's case is held and I1's has run"
    for _ in range(30):
        await asyncio.sleep(0.01)
        assert "after" not in ctl._outputs, "the step after the fan-out ran while I0 worked"
    release.set()
    assert await task == RunStatus.COMPLETE
    assert ctl._outputs["after"] == {"after": True}


async def test_a_step_after_a_branch_reads_what_its_own_items_case_produced() -> None:
    route = {
        "kind": "branch",
        "id": "route",
        "config": {"on": "{{nodes.echo.output.even}}"},
        "cases": {
            "true": {"kind": "transform", "id": "took_even", "config": {"expr": {"case": "even"}}},
            "false": {"kind": "transform", "id": "took_odd", "config": {"expr": {"case": "odd"}}},
        },
    }
    read = {
        "kind": "transform",
        "id": "read",
        "config": {
            "expr": {
                "item": "{{item}}",
                "case": "{{nodes.route.output.case}}",
                "produced": "{{nodes.route.output.produced.case}}",
            }
        },
    }
    spec = _spec([route, read])
    run = _run(spec)
    ctl = _controller(run, spec, _together(3))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    reads = _outputs_of(ctl, run.id, "read")
    assert sorted(r["item"] for r in reads) == sorted(ITEMS), reads

    def own(item: str) -> dict:
        even = int(item[1:]) % 2 == 0
        return {
            "item": item,
            "case": "true" if even else "false",
            "produced": "even" if even else "odd",
        }

    wrong = [r for r in reads if r != own(r["item"])]
    assert wrong == [], f"a step read the branch record of another item: {wrong}"


async def test_a_router_inside_the_taken_case_routes_on_its_own_items_step() -> None:
    """The outer branch takes the same case in every item; inside that case (a sequence) a second
    branch routes on the item's own ``echo``. Whether the outer case has ended is judged on the
    item's own outputs too: judged on the item whose ``echo`` was written last, the other parity's
    inner case reads as the untaken one, the outer record is never written for that item, and its
    read of ``produced`` fails as unresolved (the run still completes, since items are skipped).

    The case is a sequence holding the router, not the router itself: a branch whose case is
    directly another branch deadlocks with or without a fan-out, which is a separate defect."""
    inner = {
        "kind": "branch",
        "id": "inner",
        "config": {"on": "{{nodes.echo.output.even}}"},
        "cases": {
            "true": {"kind": "transform", "id": "in_even", "config": {"expr": {"took": "even"}}},
            "false": {"kind": "transform", "id": "in_odd", "config": {"expr": {"took": "odd"}}},
        },
    }
    outer = {
        "kind": "branch",
        "id": "outer",
        "config": {"on": "{{inputs.go}}"},
        "cases": {"yes": {"kind": "sequence", "id": "case_body", "children": [inner]}},
    }
    read = {
        "kind": "transform",
        "id": "read",
        "config": {"expr": {"item": "{{item}}", "took": "{{nodes.outer.output.produced.took}}"}},
    }
    spec = _spec([outer, read])
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={"go": "yes"}))
    store.write_spec(run.id, spec)
    ctl = _controller(run, spec, _together(4))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    reads = _outputs_of(ctl, run.id, "read")
    assert sorted(r["item"] for r in reads) == sorted(ITEMS), f"items whose read failed: {reads}"
    wrong = [r for r in reads if r["took"] != ("even" if int(r["item"][1:]) % 2 == 0 else "odd")]
    assert wrong == [], f"an item read another item's routing: {wrong}"


def test_an_item_that_has_not_produced_a_node_is_not_handed_another_items() -> None:
    """Fail closed. Item 1 has produced ``x``; item 0 has not. Scoped to item 0, ``x`` is not
    there, so a read of it fails as unresolved instead of answering with item 1's value."""
    from personalclaw.workflows.bindings import BindingContext, BindingError, resolve

    item0 = "root.children[1].body#0.children[1]"
    item1_x = "root.children[1].body#1.children[0]"
    ctx = BindingContext(
        node_outputs={"x": "from item 1", "src": "outside"},
        instance_outputs={item1_x: ("x", "from item 1")},
    )
    scoped = ctx.scoped_to(item0)
    assert "x" not in (scoped.node_outputs or {})
    assert scoped.node_outputs["src"] == "outside", "a node outside the fan-out keeps its value"
    with pytest.raises(BindingError):
        resolve("{{nodes.x.output}}", scoped)
    assert resolve("{{nodes.x.output}}", ctx.scoped_to(item1_x)) == "from item 1"


async def test_a_controller_rebuilt_mid_fan_out_reads_each_items_own_step() -> None:
    """What a gateway restart is: every item's ``echo`` is done and its ``hold`` is in flight when
    the controller goes; a new one rebuilt from disk runs ``pair``. The outputs it read back by
    node id hold one item's answer, so this is where every item read the same one."""
    hold = {
        "kind": "infer",
        "id": "hold",
        "config": {"prompt": "HOLD {{item}}", "schema": {"echoed": "string", "even": "boolean"}},
    }
    spec = _spec([hold, _PAIR])
    run = _run(spec)
    released = asyncio.Event()
    holding: set[str] = set()

    async def first(prompt, *, use_case="background", output_type=None, model=""):
        if prompt.startswith("HOLD"):
            holding.add(prompt)
            await released.wait()
        return await _echo(_delays(2))(prompt.replace("HOLD", "ECHO"))

    ctl = _controller(run, spec, first)
    task = asyncio.create_task(ctl.run_to_completion(timeout=60))
    for _ in range(500):
        if len(holding) == N:
            break
        await asyncio.sleep(0.01)
    assert len(holding) == N, "premise: every item's echo finished and its hold started"
    await ctl.stop()
    task.cancel()
    with contextlib.suppress(BaseException):
        await task

    revived = _controller(store.get(run.id), store.read_spec(run.id), _echo({}))
    assert await revived.run_to_completion(timeout=60) == RunStatus.COMPLETE
    pairs = _outputs_of(revived, run.id, "pair")
    assert len(pairs) == N, pairs
    assert _mismatched(pairs) == [], f"items read another item's step: {_mismatched(pairs)}"


async def test_a_read_from_after_the_fan_out_still_gives_the_last_item() -> None:
    """Outside every item the node id keeps its meaning: the item that wrote it last. One model
    call at a time, so the last item to finish is the last item."""
    after = [
        {
            "kind": "transform",
            "id": "after",
            "config": {"expr": {"seen": "{{nodes.echo.output.echoed}}"}},
        }
    ]
    spec = _spec([_PAIR], after=after)
    run = _run(spec)
    ctl = RunController(
        run,
        spec,
        services=EngineServices(
            completion=_echo({}), lane_limits=Limits(lanes={"llm": 1, "io": 2, "compute": 64})
        ),
    )
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE
    assert ctl._outputs["after"] == {"seen": ITEMS[-1]}
