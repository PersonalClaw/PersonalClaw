"""Rewind and Run from on a step inside a loop or foreach run it again, and a loop rewound
starts again from its first cycle.

🔴 The defect (Backlog: "Rewind and Run from on a step inside a loop or foreach do nothing, and
rewinding a loop fails the run"). Re-entry addressed a step by its definition paths, the keys a
step outside every loop runs under. A step inside a loop or foreach runs under the keys each cycle
and item opens (`<loop>.body@<n>`, `<foreach>.body#<i>`), which neither the preview nor the reset
read: Rewind and Run from on such a step answered `ok` with `rerun: []`, asked nothing and ran
nothing again. A rewound counted loop kept its cycle counter, so the scheduler found its cycle
finished, nothing ran, and the run ended "run deadlocked: no runnable nodes and none in flight".

Now a re-entry resets the instances each step ran under (`mutations.reentry_plan`): every item's,
in the latest cycle of each loop that ran the step asked for, which that loop goes back to; a loop
or foreach rewound itself starts again from its first cycle or item, its counter journaled where
it went back (`iteration_context.REWOUND`). A re-entry that would reset nothing is refused in
words.

Driven on `test_workflows_rewind_at_a_gate.py`'s harness: a live controller parked at an approval
gate after the loop or fan-out, as the run page meets one.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import live_runs
import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import mutations, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

GATE = "root.children[1]"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def _stopped_runs(anyio_backend):
    yield
    await live_runs.stop_started()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _body() -> dict[str, Any]:
    """`work`, then `read`, which reads it."""
    return {
        "kind": "sequence",
        "id": "cycle",
        "children": [
            {"kind": "transform", "id": "work", "config": {"expr": {"n": 1}}},
            {"kind": "transform", "id": "read", "config": {"expr": "{{nodes.work.output.n}}"}},
        ],
    }


def _spec(holder: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "inside",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                holder,
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": "Ship it?", "timeout_secs": 0},
                },
                {"kind": "transform", "id": "after", "config": {"expr": {"done": True}}},
            ],
        },
    }


def _fan_out() -> dict[str, Any]:
    return {"kind": "foreach", "id": "fan", "config": {"items": ["a", "b"]}, "body": _body()}


def _counted() -> dict[str, Any]:
    return {"kind": "loop", "id": "cycles", "config": {"mode": "counted", "n": 2}, "body": _body()}


def _completions(run_id: str, node_id: str) -> int:
    return sum(
        1
        for e in J.ledger(run_id)
        if e.get("kind") == J.STEP_COMPLETED and e.get("node_id") == node_id
    )


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _parked(spec: dict[str, Any]) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = live_runs.started(RunController(run, spec, services=EngineServices()))
    assert await c.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT
    assert c.instances[GATE].state == InstanceState.WAITING
    return c


async def _parked_again(c: RunController) -> None:
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT and c._task is not None and c._task.done(),
        what="the run to park at its gate again",
    )
    assert c.run.status != RunStatus.FAILED, c.run.error_message
    assert c.instances[GATE].state == InstanceState.WAITING


@pytest.mark.parametrize("holder", [_fan_out, _counted], ids=["foreach", "counted loop"])
async def test_a_rewind_of_a_step_inside_asks_first_then_runs_it_again(holder):
    """🔴 Before: `ok`, `rerun: []`, no question and no second completion."""
    c = await _parked(_spec(holder()))
    ran = _completions(c.run.id, "work")
    assert ran == 2, "vacuity floor: two items, or two cycles"

    asked = c.submit_mutation([{"op": "rewind", "node_id": "work"}])
    assert asked.get("needs_confirmation") is True, asked
    assert "work" in asked["preview"]["rerun"] and "read" in asked["preview"]["rerun"]

    body = c.submit_mutation([{"op": "rewind", "node_id": "work"}], confirm=True)
    assert body["ok"] and body.get("queued"), body
    # Every item it ran in, or the latest cycle that ran it, with the step that reads it.
    again = 2 if holder is _fan_out else 1
    await _until(
        lambda: _completions(c.run.id, "work") == ran + again
        and _completions(c.run.id, "read") == ran + again,
        what="the rewound step and its reader to run again",
    )
    await _parked_again(c)


@pytest.mark.parametrize("holder", [_fan_out, _counted], ids=["foreach", "counted loop"])
async def test_run_from_a_step_inside_runs_only_what_reads_it(holder):
    c = await _parked(_spec(holder()))
    body = c.submit_mutation([{"op": "run_from", "node_id": "work"}], confirm=True)
    assert body["ok"] and body.get("queued"), body
    again = 2 if holder is _fan_out else 1
    await _until(lambda: _completions(c.run.id, "read") == 2 + again, what="the reader to re-run")
    await _parked_again(c)
    assert _completions(c.run.id, "work") == 2, "the step itself keeps its outputs"


@pytest.mark.parametrize("holder", [_fan_out, _counted], ids=["foreach", "counted loop"])
async def test_a_loop_or_foreach_rewound_runs_again_from_its_first_cycle_or_item(holder):
    """🔴 Before, for the counted loop: the run ended failed, "run deadlocked"; for the foreach,
    nothing ran again."""
    c = await _parked(_spec(holder()))
    holder_id = "fan" if holder is _fan_out else "cycles"
    body = c.submit_mutation([{"op": "rewind", "node_id": holder_id}], confirm=True)
    assert body["ok"] and body.get("queued"), body
    await _until(lambda: _completions(c.run.id, "work") == 4, what="every cycle or item again")
    await _parked_again(c)
    assert _completions(c.run.id, "read") == 4
    if holder is _counted:
        assert c._iterations["root.children[0]"] == 1, "its two cycles, and no third"
        rows = J.ledger(c.run.id, kinds={J.ITERATION})
        assert any(r.get("outcome") == "rewound" and r.get("iteration") == 0 for r in rows), rows


def _reads_from_outside(holder: dict[str, Any]) -> dict[str, Any]:
    """`plan` before the loop or fan-out, and `read` inside it reading `plan`. The gate is still
    the root's second child."""
    spec = _spec(holder)
    holder["body"]["children"][1]["config"]["expr"] = "{{nodes.plan.output.n}}"
    plan = {"kind": "transform", "id": "plan", "config": {"expr": {"n": 2}}}
    spec["root"]["children"] = [
        {"kind": "sequence", "id": "first", "children": [plan, holder]},
        *spec["root"]["children"][1:],
    ]
    return spec


@pytest.mark.parametrize("holder", [_fan_out, _counted], ids=["foreach", "counted loop"])
async def test_a_rewind_of_a_step_a_loop_reads_from_outside_runs_every_cycle_again(holder):
    """A step inside read the rewound step in every cycle and item: it runs again in each, and
    the loop goes back to its first cycle rather than leaving a reset step in a cycle it closed."""
    c = await _parked(_reads_from_outside(holder()))
    assert _completions(c.run.id, "read") == 2, "vacuity floor: two items, or two cycles"
    body = c.submit_mutation([{"op": "rewind", "node_id": "plan"}], confirm=True)
    assert body["ok"] and body.get("queued"), body
    await _until(
        lambda: _completions(c.run.id, "plan") == 2 and _completions(c.run.id, "read") == 4,
        what="the rewound step, then its reader in every cycle or item",
    )
    await _parked_again(c)
    assert _completions(c.run.id, "read") == 4, "each cycle or item once more, and no third"


def test_a_reader_inside_a_loop_restarts_the_loop_it_is_in():
    """Pure: the reader's loop is sent back to cycle 0, all it ran dropped, nothing left reset
    under it."""
    spec = _reads_from_outside(_counted())
    loop = "root.children[0].children[1]"
    done = [
        "root.children[0].children[0]",
        *(f"{loop}.body@{n}.children[{i}]" for n in (0, 1) for i in (0, 1)),
        loop,
        "root.children[0]",
    ]
    instances = {p: NodeInstance(path=p, state=InstanceState.DONE) for p in done}
    plan = mutations.reentry_plan(
        mutations.Node.from_dict(spec["root"]), instances, {"plan", "read"}, seeds={"plan"}
    )
    assert plan.cycles == {loop: 0}, plan.cycles
    assert set(plan.dropped) == {p for p in done if ".body@" in p}, plan.dropped
    assert plan.reset == ["root.children[0].children[0]", loop], plan.reset


async def test_a_restart_after_a_loop_was_rewound_resumes_its_cycle_from_the_journal():
    """The counter's durable form is the journal: a controller started again over the run reads
    the cycle the rewind sent the loop back to, not the one before it."""
    from personalclaw.workflows import iteration_context

    c = await _parked(_spec(_counted()))
    rows_before = len(J.ledger(c.run.id, kinds={J.ITERATION}))
    c.journal.iteration("root.children[0]", "cycles", iteration=0, outcome="rewound")
    fresh = RunController(c.run, copy.deepcopy(c.spec), services=EngineServices())
    iteration_context.rehydrate_loop_progress(fresh)
    assert rows_before >= 1, "vacuity floor: the loop journaled its cycles"
    assert fresh._iterations["root.children[0]"] == 0


async def test_a_rewind_that_would_reset_nothing_is_refused_in_words():
    """🔴 Before: answered `ok` with an empty `rerun`."""
    c = await _parked(_spec(_counted()))
    body = c.submit_mutation([{"op": "rewind", "node_id": "after"}], confirm=True)
    assert body["ok"] is False, body
    (issue,) = body["issues"]
    assert issue["code"] == "WF_MUT_NOTHING_TO_RERUN"
    assert "'after' has not run in this run yet" in issue["message"], issue


def test_the_preview_reads_a_steps_cycles():
    """Pure: `work` done in `body@0` and `body@1` is in the re-run set, and it is asked about."""
    spec = _spec(_counted())
    done = [
        "root.children[0].body@0.children[0]",
        "root.children[0].body@0.children[1]",
        "root.children[0].body@1.children[0]",
        "root.children[0].body@1.children[1]",
        "root.children[0]",
    ]
    instances = {p: NodeInstance(path=p, state=InstanceState.DONE) for p in done}
    instances[GATE] = NodeInstance(path=GATE, state=InstanceState.WAITING)
    result = mutations.prepare_batch([{"op": "rewind", "node_id": "work"}], spec, instances)
    assert result.ok, result.issues
    assert {"work", "read"} <= set(result.preview.rerun) and result.preview.needs_confirmation
    plan = mutations.reentry_plan(
        mutations.Node.from_dict(spec["root"]), instances, {"work", "read"}, seeds={"work"}
    )
    assert plan.reset == [
        "root.children[0].body@1.children[0]",
        "root.children[0].body@1.children[1]",
        "root.children[0]",
    ], plan.reset
    assert plan.dropped == [] and plan.cycles == {"root.children[0]": 1}
