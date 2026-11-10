"""A skip of a step inside a loop or a foreach holds for every cycle and item that has not run it.

The agent's `workflow_skip` (a `skip` op) names a step by its id. A step inside a loop or a
foreach runs under paths the scheduler opens later, one per cycle (`…body@<n>`) or item
(`…body#<i>`), and the skip reached none of them: it was answered as applied, the run page showed
a skipped row at the step's definition path, and the step ran in every cycle and for every item.

Most cases park a run at a gate before its loop or foreach opens, skip the step inside it, and
answer the gate (the harness of ``test_workflows_rewind_at_a_gate.py``); others hold a step
mid-call (``_held_running``) or run to the end.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import live_runs
import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

GATE = "root.children[0]"
#: The loop or foreach, and the definition path of the step inside it.
HOLDER = "root.children[1]"
BODY = f"{HOLDER}.body"


@pytest.fixture(autouse=True)
async def _stopped_runs(anyio_backend):
    """Each run a test started, stopped before its loop closes (``live_runs``)."""
    yield
    await live_runs.stop_started()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _spec(holder: dict[str, Any]) -> dict[str, Any]:
    """approve (waits) → *holder*, whose body is the step `work`."""
    return {
        "name": "skip-inside",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": "Go on?", "timeout_secs": 0},
                },
                holder,
            ],
        },
    }


WORK = {"kind": "transform", "id": "work", "config": {"expr": {"done": True}}}
FOREACH = {"kind": "foreach", "id": "fan", "config": {"items": [1, 2]}, "body": WORK}
LOOP = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 2}, "body": WORK}
HOLDERS = {"foreach": (FOREACH, "#"), "counted loop": (LOOP, "@")}


def _completions(run_id: str, node_id: str) -> int:
    return sum(
        1
        for e in J.ledger(run_id)
        if e.get("kind") == J.STEP_COMPLETED and e.get("node_id") == node_id
    )


async def _parked(spec: dict[str, Any], services: EngineServices | None = None) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = live_runs.started(RunController(run, spec, services=services or EngineServices()))
    assert await c.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT
    assert c.instances[GATE].state == InstanceState.WAITING
    return c


def _answer(c: RunController) -> None:
    (pending,) = HI.list_continuations(c.run.id)
    assert c.resume(pending.token, True, by=YOU)["ok"]


async def _until(predicate, *, what: str, timeout: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("holder", sorted(HOLDERS))
async def test_a_skip_holds_for_every_cycle_and_item_not_yet_run(holder: str) -> None:
    spec, mark = HOLDERS[holder]
    c = await _parked(_spec(spec))

    body = c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)
    assert body["ok"], body
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)

    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 0, f"{holder}: the skipped step ran"
    opened = {p: i.state for p, i in c.instances.items() if p.startswith(f"{BODY}{mark}")}
    assert sorted(opened) == [f"{BODY}{mark}0", f"{BODY}{mark}1"], opened
    assert set(opened.values()) == {InstanceState.SKIPPED}, opened
    assert all("skip" in c.instances[p].degraded_reason.lower() for p in opened), {
        p: c.instances[p].degraded_reason for p in opened
    }


@pytest.mark.parametrize("holder", sorted(HOLDERS))
async def test_the_run_has_no_row_at_the_steps_definition_path(holder: str) -> None:
    """The step runs only under the paths its loop or foreach opens: a row at its definition path
    is a step no cycle or item ran, shown skipped beside the ones that did."""
    spec, _mark = HOLDERS[holder]
    c = await _parked(_spec(spec))
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    assert BODY not in c.instances, c.instances[BODY]
    assert BODY not in store.read_state(c.run.id)


async def test_a_run_rebuilt_from_disk_still_skips_the_step() -> None:
    """The skip is kept on the run's spec, so a controller rebuilt after a restart, from the run's
    own record, skips the step in the cycles it had not reached."""
    c = await _parked(_spec(LOOP))
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await live_runs.stop_started()

    again = live_runs.started(
        RunController(store.get(c.run.id), store.read_spec(c.run.id), services=EngineServices())
    )
    assert await again.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT
    _answer(again)
    await asyncio.wait_for(again._terminal.wait(), timeout=10)
    assert again.run.status == RunStatus.COMPLETE, again.run.error_message
    assert _completions(c.run.id, "work") == 0


async def _supervised(
    spec: dict[str, Any], policy_overrides: dict[str, Any] | None = None
) -> tuple[WorkflowWatchdog, RunController]:
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], policy_overrides=policy_overrides or {})
    )
    store.write_spec(run.id, spec)
    sup = WorkflowWatchdog(state=None, services=EngineServices())
    c = live_runs.started(await sup.launch(run, copy.deepcopy(spec)))
    assert await c.wait_for_terminal(timeout=10) == RunStatus.NEEDS_INPUT
    return sup, c


async def test_the_skip_answers_which_steps_it_reaches() -> None:
    sup, c = await _supervised(_spec(FOREACH))
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["node_id"] == "work"
    assert reach["later"] is True, "a step in a foreach not yet opened is reached in every item"
    assert reach["instances"] == [], reach


def _after_the_foreach() -> dict[str, Any]:
    """the foreach `fan` {work} runs first, then approve waits: the step has run in every item."""
    spec = _spec(FOREACH)
    children = spec["root"]["children"]
    children.reverse()
    return spec


async def test_a_skip_that_reaches_no_step_is_refused_and_says_why() -> None:
    """Every item of the foreach has run `work`: a skip of it is refused, in words, rather than
    answered as applied."""
    sup, c = await _supervised(_after_the_foreach())
    assert _completions(c.run.id, "work") == 2
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"] is False, body
    assert body["code"] == "WF_SKIP_REACHES_NOTHING", body
    assert "“work” has already run, or is under way, wherever this run runs it" in body["message"]


async def test_a_skip_through_the_edit_route_that_reaches_nothing_is_refused() -> None:
    """`workflow_skip` over the gateway posts its skip to the edit route: the same refusal."""
    sup, c = await _supervised(_after_the_foreach())
    body = service.edit_run(
        c.run.id, [{"op": "skip", "node_id": "work"}], supervisor=sup, confirm_cascade=True
    )
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


@pytest.mark.parametrize(
    ("also", "said"),
    [
        ({"op": "skip", "node_id": "later"}, "skipped"),
        ({"op": "update_node", "node_id": "later", "fields": {"expr": {"n": 3}}}, "applied"),
    ],
    ids=["another skip", "an update"],
)
async def test_a_refused_batch_says_none_of_its_skips_was_applied(also, said) -> None:
    spec = _after_the_foreach()
    spec["root"]["children"].append(
        {"kind": "transform", "id": "later", "config": {"expr": {"n": 2}}}
    )
    sup, c = await _supervised(spec)
    body = service.edit_run(
        c.run.id, [{"op": "skip", "node_id": "work"}, also], supervisor=sup, confirm_cascade=True
    )
    assert body["code"] == "WF_SKIP_REACHES_NOTHING", body
    assert f"Nothing else in this edit was {said} either." in body["message"], body["message"]
    assert c.instances.get("root.children[2]") is None or (
        c.instances["root.children[2]"].state != InstanceState.SKIPPED
    )
    assert store.read_spec(c.run.id)["root"]["children"][2]["config"]["expr"] == {"n": 2}


async def test_a_skip_of_a_step_running_in_every_item_is_refused() -> None:
    """The scheduler does not skip a step already under way: one held in each item of a foreach
    (waiting on its own approval) is reached by nothing, and both items still finish once
    answered."""
    held = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [1, 2]},
        "body": {
            "kind": "gate",
            "id": "hold",
            "config": {"kind": "approval", "prompt": "Item?", "timeout_secs": 0},
        },
    }
    spec = {"name": "held", "root": {"kind": "sequence", "id": "s", "children": [held]}}
    sup, c = await _supervised(spec)
    assert len(HI.list_continuations(c.run.id)) == 2
    body = service.skip_nodes(c.run.id, ["hold"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body
    for pending in HI.list_continuations(c.run.id):
        assert c.resume(pending.token, True, by=YOU)["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message


async def test_a_rewind_of_a_skipped_step_runs_it() -> None:
    """A rewind resets the step it names, its skip included: the step runs, once."""
    spec = _spec({"kind": "transform", "id": "work", "config": {"expr": {"done": True}}})
    c = await _parked(spec)
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    assert c.instances[HOLDER].state == InstanceState.SKIPPED
    assert c.submit_mutation([{"op": "rewind", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the rewind to apply")
    assert (
        not store.read_spec(c.run.id)["root"]["children"][1].get("config", {}).get("__skipped")
    ), "the rewind left the skip on the spec a rebuilt run reads"
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 1


async def test_a_loop_whose_only_step_was_skipped_ends_its_cycles_without_a_stall() -> None:
    """A cycle with nothing left in it after a skip is not a cycle that produced the same output
    again: a counted loop of four runs out its cycles and completes."""
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 4}, "body": WORK}
    c = await _parked(_spec(loop))
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    cycles = sorted(p for p in c.instances if p.startswith(f"{BODY}@"))
    assert cycles == [f"{BODY}@{n}" for n in range(4)], cycles
    # No cycle ran anything, so the loop produced nothing: it is skipped as asked itself.
    loop = c.instances[HOLDER]
    assert (loop.state, loop.degraded_reason) == (
        InstanceState.SKIPPED,
        "not run: skipped as asked",
    )


async def test_a_step_skipped_at_its_own_path_says_why() -> None:
    spec = _spec({"kind": "transform", "id": "work", "config": {"expr": {"done": True}}})
    c = await _parked(spec)
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    assert c.instances[HOLDER].degraded_reason == "not run: skipped as asked"


def test_a_branch_case_whose_label_holds_body_is_not_a_fan_out() -> None:
    from personalclaw.workflows.mid_flight import fan_out_holders
    from personalclaw.workflows.models import Node

    root = Node.from_dict(
        {
            "kind": "branch",
            "id": "b",
            "config": {"on": "{{inputs.x}}"},
            "cases": {"request.body": {"kind": "transform", "id": "t", "config": {"expr": {}}}},
        }
    )
    assert fan_out_holders(root) == {}


def _gate(node_id: str) -> dict[str, Any]:
    return {
        "kind": "gate",
        "id": node_id,
        "config": {"kind": "approval", "prompt": f"{node_id}?", "timeout_secs": 0},
    }


def _transform(node_id: str, expr: Any) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": expr}}


def _in(*children: dict[str, Any]) -> dict[str, Any]:
    return {"name": "skips", "root": {"kind": "sequence", "id": "s", "children": list(children)}}


async def _answer_all(c: RunController) -> None:
    for pending in HI.list_continuations(c.run.id):
        assert c.resume(pending.token, True, by=YOU)["ok"]
    await asyncio.wait_for(c._terminal.wait(), timeout=10)


async def test_a_skip_of_a_step_in_a_finished_until_loop_is_refused() -> None:
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "until", "condition": "{{last.output.done}}"},
        "body": _transform("work", {"done": True}),
    }
    sup, c = await _supervised(_in(loop, _gate("after")))
    assert _completions(c.run.id, "work") == 1
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


def _fan(body: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "foreach", "id": "fan", "config": {"items": [1, 2]}, "body": body}


def _steps(*children: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "sequence", "id": "steps", "children": list(children)}


ITEM = "root.children[0].body"


async def test_a_skip_of_a_group_holds_its_waiting_steps_and_skips_the_rest() -> None:
    """A skip of a group of steps in a foreach lets the gate waiting in each item go on waiting,
    and skips the step after it, which never started."""
    sup, c = await _supervised(_in(_fan(_steps(_gate("hold"), _transform("after", {"n": 1})))))
    holds = [f"{ITEM}#{i}.children[0]" for i in (0, 1)]
    assert len(HI.list_continuations(c.run.id)) == 2
    body = service.skip_nodes(c.run.id, ["steps"], supervisor=sup)
    assert body["ok"], body
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await asyncio.sleep(0.2)
    assert [c.instances[h].state for h in holds] == [InstanceState.WAITING] * 2
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    afters = [c.instances[f"{ITEM}#{i}.children[1]"] for i in (0, 1)]
    assert [(a.state, a.degraded_reason) for a in afters] == [
        (InstanceState.SKIPPED, "not run: skipped as asked")
    ] * 2
    assert [c.instances[h].state for h in holds] == [InstanceState.DONE] * 2


async def test_a_skip_of_a_group_whose_steps_are_all_run_or_waiting_is_refused() -> None:
    sup, c = await _supervised(_in(_fan(_steps(_transform("work", {"n": 1}), _gate("hold")))))
    assert len(HI.list_continuations(c.run.id)) == 2
    body = service.skip_nodes(c.run.id, ["steps"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert [c.instances[f"{ITEM}#{i}.children[1]"].state for i in (0, 1)] == [
        InstanceState.DONE
    ] * 2


async def test_a_skip_of_a_group_in_a_finished_foreach_is_refused() -> None:
    sup, c = await _supervised(_in(_fan(_steps(_transform("work", {"n": 1}))), _gate("after")))
    assert _completions(c.run.id, "work") == 2
    body = service.skip_nodes(c.run.id, ["steps"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


async def test_a_skip_of_a_finished_group_outside_any_loop_is_refused() -> None:
    sup, c = await _supervised(_in(_steps(_transform("work", {"n": 1})), _gate("after")))
    body = service.skip_nodes(c.run.id, ["steps"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


async def test_a_rewind_of_a_skipped_step_inside_a_loop_runs_it_in_each_cycle() -> None:
    c = await _parked(_spec(LOOP))
    assert c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    assert store.read_spec(c.run.id)["root"]["children"][1]["body"]["config"].get("__skipped")
    assert c.submit_mutation([{"op": "rewind", "node_id": "work"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the rewind to apply")
    assert not store.read_spec(c.run.id)["root"]["children"][1]["body"]["config"].get("__skipped")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 2


async def test_a_skip_of_a_step_a_counted_loops_last_cycle_has_run_is_refused() -> None:
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "counted", "n": 2},
        "body": {
            "kind": "sequence",
            "id": "cyc",
            "children": [WORK, _gate("hold"), _transform("tail", {"t": 1})],
        },
    }
    sup, c = await _supervised(_in(loop))
    _answer(c)
    last = "root.children[0].body@1.children[1]"
    await _until(
        lambda: getattr(c.instances.get(last), "state", None) == InstanceState.WAITING,
        what="the last cycle's gate",
    )
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


@pytest.mark.parametrize(
    ("config", "overrides"),
    [
        ({"mode": "until_dry", "streak": 1, "max_iterations": 1}, {}),
        ({"mode": "counted", "n": 3, "max_iterations": 1}, {}),
        ({"mode": "until_dry", "streak": 1}, {"max_cycles": 1}),
    ],
    ids=["max_iterations", "counted under max_iterations", "the run's Max cycles"],
)
async def test_a_skip_of_a_step_on_a_capped_loops_last_cycle_is_refused(
    config: dict[str, Any], overrides: dict[str, Any]
) -> None:
    """The loop's cycle budget allows one cycle, which has run `work` and waits at its gate: no
    other cycle opens, so the skip reaches nothing. The loop stops at its budget."""
    body = _steps(WORK, _gate("hold"), _transform("tail", {"t": 1}))
    sup, c = await _supervised(
        _in({"kind": "loop", "id": "again", "config": config, "body": body}), overrides
    )
    assert _completions(c.run.id, "work") == 1
    answer = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert answer["ok"] is False and answer["code"] == "WF_SKIP_REACHES_NOTHING", answer
    await _answer_all(c)
    assert c.run.status == RunStatus.ESCALATED, c.run.error_message
    assert _completions(c.run.id, "work") == 1


@pytest.mark.parametrize(
    ("config", "cycle_body", "asked_in"),
    [
        ({"mode": "counted", "n": 1}, _steps(_gate("hold"), WORK), 0),
        ({"mode": "until_dry", "streak": 1, "max_iterations": 1}, _steps(_gate("hold"), WORK), 0),
        (
            {"mode": "counted", "n": 2},
            _steps(_gate("hold"), WORK, _transform("tail", {"t": 1})),
            1,
        ),
    ],
    ids=["counted", "capped", "second of two"],
)
async def test_a_skip_of_a_step_still_to_come_in_a_loops_last_cycle_reaches_it(
    config: dict[str, Any], cycle_body: dict[str, Any], asked_in: int
) -> None:
    """The loop's last cycle waits at its gate before `work`: no cycle opens after it, and the
    skip reaches `work` there."""
    sup, c = await _supervised(
        _in({"kind": "loop", "id": "again", "config": config, "body": cycle_body})
    )
    if asked_in:
        _answer(c)
        await _until(
            lambda: getattr(c.instances.get("root.children[0].body@1.children[0]"), "state", None)
            == InstanceState.WAITING,
            what="the last cycle's gate",
        )
    answer = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert answer["ok"], answer
    (reach,) = answer["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await _answer_all(c)
    work = c.instances[f"root.children[0].body@{asked_in}.children[1]"]
    assert (work.state, work.degraded_reason) == (
        InstanceState.SKIPPED,
        "not run: skipped as asked",
    )
    assert _completions(c.run.id, "work") == asked_in
    if config["mode"] == "counted":
        assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    else:
        assert c.run.is_terminal, c.run.status


async def test_a_skip_of_a_step_in_a_loop_inside_a_foreach_reaches_its_later_cycles() -> None:
    sup, c = await _supervised(_spec(_fan(LOOP)))
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach


async def test_a_skip_in_loops_still_cycling_inside_a_foreach_reaches_their_later_cycles() -> None:
    """Each item's loop has run `work` in its first cycle and waits at its gate: the second cycle
    of each is still to open, so the skip reaches it there."""
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "counted", "n": 2},
        "body": _steps(WORK, _gate("hold"), _transform("tail", {"t": 1})),
    }
    sup, c = await _supervised(_in(_fan(loop)))
    assert _completions(c.run.id, "work") == 2
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await _answer_all(c)
    for i in (0, 1):
        later = c.instances[f"{ITEM}#{i}.body@1.children[0]"]
        assert (later.state, later.degraded_reason) == (
            InstanceState.SKIPPED,
            "not run: skipped as asked",
        )
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 2


RUNNING_WORK = {
    "kind": "infer",
    "id": "work",
    "config": {"prompt": "W {{item}}", "schema": {"n": "string"}},
}


async def _held_running(spec: dict[str, Any], running: list[str]):
    """A run of *spec* whose `work` steps are held mid-call: the controller, once each instance
    in *running* is RUNNING, and the event that lets them answer."""
    release = asyncio.Event()

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        await release.wait()
        return '{"n": "done"}'

    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = live_runs.started(RunController(run, spec, services=EngineServices(completion=fn)))
    await c.start()
    await _until(
        lambda: all(
            getattr(c.instances.get(i), "state", None) == InstanceState.RUNNING for i in running
        ),
        what="each held step to be running",
    )
    return c, release


async def _refused_while_running(spec: dict[str, Any], running: list[str], name: str) -> None:
    c, release = await _held_running(spec, running)
    body = c.submit_mutation([{"op": "skip", "node_id": name}], confirm=True)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body
    release.set()
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 2


async def test_a_skip_of_a_step_running_in_every_item_is_refused_while_it_runs() -> None:
    """Held RUNNING, not waiting: each item's step is mid-call."""
    await _refused_while_running(_in(_fan(RUNNING_WORK)), [f"{ITEM}#{i}" for i in (0, 1)], "work")


async def test_a_skip_of_a_foreach_whose_items_are_all_running_is_refused() -> None:
    await _refused_while_running(_in(_fan(RUNNING_WORK)), [f"{ITEM}#{i}" for i in (0, 1)], "fan")


ONE_CYCLE = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 1}}


async def test_a_skip_of_a_loop_on_its_last_cycle_in_each_item_is_refused() -> None:
    spec = _in(_fan({**ONE_CYCLE, "body": RUNNING_WORK}))
    await _refused_while_running(spec, [f"{ITEM}#{i}.body@0" for i in (0, 1)], "again")


async def test_a_skip_of_a_group_holding_such_a_loop_is_refused() -> None:
    spec = _in(_fan(_steps({**ONE_CYCLE, "body": RUNNING_WORK})))
    held = [f"{ITEM}#{i}.children[0].body@0" for i in (0, 1)]
    await _refused_while_running(spec, held, "steps")


@pytest.mark.parametrize("cycles", [1, 2], ids=["its only cycle", "the second of two"])
async def test_a_skip_of_a_loop_whose_cycle_waits_skips_the_rest_of_it(cycles: int) -> None:
    """Each item's current cycle waits at its gate with `after` still to run: a skip of the loop
    reaches it there, lets the gate go on waiting, and `after` never runs in that cycle."""
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": cycles}}
    spec = _in(_fan({**loop, "body": _steps(_gate("hold"), _transform("after", {"n": 1}))}))
    sup, c = await _supervised(spec)
    last = cycles - 1
    holds = [f"{ITEM}#{i}.body@{last}.children[0]" for i in (0, 1)]
    if last:
        for pending in HI.list_continuations(c.run.id):
            assert c.resume(pending.token, True, by=YOU)["ok"]
        await _until(
            lambda: all(
                getattr(c.instances.get(h), "state", None) == InstanceState.WAITING for h in holds
            ),
            what="each item's second cycle's gate",
        )
    assert len(HI.list_continuations(c.run.id)) == 2
    body = service.skip_nodes(c.run.id, ["again"], supervisor=sup)
    assert body["ok"], body
    assert body["reaches"][0]["instances"] == [f"{ITEM}#0", f"{ITEM}#1"], body["reaches"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await asyncio.sleep(0.2)
    assert [c.instances[h].state for h in holds] == [InstanceState.WAITING] * 2
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "after") == 2 * last, "each item ran it in its earlier cycle"
    assert [c.instances[h].state for h in holds] == [InstanceState.DONE] * 2


async def test_a_skip_of_a_foreach_whose_items_wait_skips_the_rest_of_them() -> None:
    """Each item waits at its gate with `after` still to run: a skip of the foreach reaches it
    there, lets each gate go on waiting, and `after` never runs."""
    sup, c = await _supervised(_in(_fan(_steps(_gate("hold"), _transform("after", {"n": 1})))))
    assert len(HI.list_continuations(c.run.id)) == 2
    body = service.skip_nodes(c.run.id, ["fan"], supervisor=sup)
    assert body["ok"], body
    assert body["reaches"][0]["instances"] == ["root.children[0]"], body["reaches"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await asyncio.sleep(0.2)
    holds = [f"{ITEM}#{i}.children[0]" for i in (0, 1)]
    assert [c.instances[h].state for h in holds] == [InstanceState.WAITING] * 2
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "after") == 0
    assert [c.instances[h].state for h in holds] == [InstanceState.DONE] * 2


async def test_a_skip_in_a_foreach_whose_items_are_not_known_yet_reaches_every_item() -> None:
    """The step that makes the foreach's items has not run: every item is still to open."""
    fan = {"kind": "foreach", "id": "fan", "config": {"items": "{{nodes.list.output.xs}}"}}
    spec = _in(_gate("approve"), _transform("list", {"xs": [1, 2]}), {**fan, "body": WORK})
    sup, c = await _supervised(spec)
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 0
    for i in (0, 1):
        item = c.instances[f"root.children[2].body#{i}"]
        assert (item.state, item.degraded_reason) == (
            InstanceState.SKIPPED,
            "not run: skipped as asked",
        )


#: A foreach whose second item has no `v` for its step to read: it fails, and under
#: `on_item_error: halt` the third item never opens.
HALTED_FAN = {
    "kind": "foreach",
    "id": "fan",
    "config": {"items": [{"v": 1}, {}, {"v": 3}], "on_item_error": "halt", "max_concurrency": 1},
    "body": _transform("work", "{{item.v}}"),
}


async def test_a_skip_of_a_step_in_a_halted_foreach_is_refused() -> None:
    """Under `on_item_error: halt` the second item fails and the foreach opens no more items: its
    third is never started, so a skip of `work` reaches nothing."""
    sup, c = await _supervised(
        _in({"kind": "parallel", "id": "both", "children": [HALTED_FAN, _gate("hold")]})
    )
    assert _completions(c.run.id, "work") == 1
    assert c.instances["root.children[0].children[0].body#1"].state == InstanceState.FAILED
    assert "root.children[0].children[0].body#2" not in c.instances
    answer = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert answer["ok"] is False and answer["code"] == "WF_SKIP_REACHES_NOTHING", answer
    await _answer_all(c)
    assert c.run.status == RunStatus.FAILED, c.run.error_message
    assert _completions(c.run.id, "work") == 1


async def test_a_loop_whose_cycle_holds_a_halted_foreach_opens_no_other_cycle() -> None:
    """The cycle's foreach halts, so the cycle never ends and the loop opens no other: a skip of
    `work` reaches nothing though the loop has a cycle left, and a skip of `after`, still to come
    in that cycle, reaches it there."""
    cycle = {
        "kind": "parallel",
        "id": "cycle",
        "children": [HALTED_FAN, _steps(_gate("hold"), _transform("after", {"n": 1}))],
    }
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 2}, "body": cycle}
    sup, c = await _supervised(_in(loop))
    fan = "root.children[0].body@0.children[0]"
    assert c.instances[f"{fan}.body#1"].state == InstanceState.FAILED
    assert f"{fan}.body#2" not in c.instances
    refused = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert refused["ok"] is False and refused["code"] == "WF_SKIP_REACHES_NOTHING", refused
    answer = service.skip_nodes(c.run.id, ["after"], supervisor=sup)
    assert answer["ok"], answer
    (reach,) = answer["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await _answer_all(c)
    assert c.run.status == RunStatus.FAILED, c.run.error_message
    after = c.instances["root.children[0].body@0.children[1].children[1]"]
    assert (after.state, after.degraded_reason) == (
        InstanceState.SKIPPED,
        "not run: skipped as asked",
    )
    assert _completions(c.run.id, "work") == 1
    assert _completions(c.run.id, "after") == 0


async def test_a_loop_whose_halted_foreach_has_only_running_items_opens_its_next_cycle() -> None:
    """The first item is still running when the second fails, so the halt leaves nothing to
    start: the foreach ends once the first settles, and so does the cycle. A skip of `work`
    reaches the loop's second cycle."""
    work = {**RUNNING_WORK, "config": {"prompt": "W {{item.v}}", "schema": {"n": "string"}}}
    fan = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [{"v": 1}, {}], "on_item_error": "halt"},
        "body": work,
    }
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 2}, "body": fan}
    c, release = await _held_running(_in(loop), ["root.children[0].body@0.body#0"])
    await _until(
        lambda: getattr(c.instances.get("root.children[0].body@0.body#1"), "state", None)
        == InstanceState.FAILED,
        what="the second item to fail",
    )
    body = c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    release.set()
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    for i in (0, 1):
        item = c.instances[f"root.children[0].body@1.body#{i}"]
        assert (item.state, item.degraded_reason) == (
            InstanceState.SKIPPED,
            "not run: skipped as asked",
        )
    assert _completions(c.run.id, "work") == 1


async def test_a_skip_in_a_foreach_a_failed_item_has_not_halted_reaches_its_later_items() -> None:
    """Under the default `on_item_error: skip` a failed item halts nothing: the third item is
    still to open, and a skip of `work` reaches it there."""
    work = {**RUNNING_WORK, "config": {"prompt": "W {{item.v}}", "schema": {"n": "string"}}}
    fan = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [{}, {"v": 2}, {"v": 3}], "max_concurrency": 1},
        "body": work,
    }
    c, release = await _held_running(_in(fan), [f"{ITEM}#1"])
    assert c.instances[f"{ITEM}#0"].state == InstanceState.FAILED
    assert f"{ITEM}#2" not in c.instances
    body = c.submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)
    assert body["ok"], body
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    release.set()
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    item = c.instances[f"{ITEM}#2"]
    assert (item.state, item.degraded_reason) == (
        InstanceState.SKIPPED,
        "not run: skipped as asked",
    )
    assert _completions(c.run.id, "work") == 1


async def test_a_failed_item_that_halts_nothing_leaves_its_loop_cycling() -> None:
    """Under the default `on_item_error: skip` a failed item halts nothing: the foreach's third
    item is still to open, the cycle ends after it, and a skip of `x`, run in the first cycle,
    reaches the second."""
    work = {**RUNNING_WORK, "config": {"prompt": "W {{item.v}}", "schema": {"n": "string"}}}
    fan = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [{}, {"v": 2}, {"v": 3}], "max_concurrency": 1},
        "body": work,
    }
    cycle = _steps(_transform("x", {"n": 1}), fan)
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 2}, "body": cycle}
    first = "root.children[0].body@0.children[1].body"
    c, release = await _held_running(_in(loop), [f"{first}#1"])
    assert c.instances[f"{first}#0"].state == InstanceState.FAILED
    body = c.submit_mutation([{"op": "skip", "node_id": "x"}], confirm=True)
    assert body["ok"], body
    (reach,) = body["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    release.set()
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    x = c.instances["root.children[0].body@1.children[0]"]
    assert (x.state, x.degraded_reason) == (InstanceState.SKIPPED, "not run: skipped as asked")
    assert _completions(c.run.id, "x") == 1


async def test_a_halted_foreach_beside_a_loop_leaves_it_cycling() -> None:
    """The halted foreach is not in the loop's cycle, so the loop still opens its second: a skip
    of `x`, run in the first, reaches it there."""
    body = _steps(_transform("x", {"n": 1}), _gate("hold"), _transform("y", {"n": 2}))
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 2}, "body": body}
    sup, c = await _supervised(
        _in({"kind": "parallel", "id": "both", "children": [HALTED_FAN, loop]})
    )
    assert c.instances["root.children[0].children[0].body#1"].state == InstanceState.FAILED
    answer = service.skip_nodes(c.run.id, ["x"], supervisor=sup)
    assert answer["ok"], answer
    (reach,) = answer["reaches"]
    assert reach["later"] is True and reach["instances"] == [], reach
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    second = "root.children[0].children[1].body@1"
    await _until(
        lambda: getattr(c.instances.get(f"{second}.children[1]"), "state", None)
        == InstanceState.WAITING,
        what="the second cycle's gate",
    )
    x = c.instances[f"{second}.children[0]"]
    assert (x.state, x.degraded_reason) == (InstanceState.SKIPPED, "not run: skipped as asked")
    assert _completions(c.run.id, "x") == 1


async def test_a_skip_of_an_inner_foreach_whose_items_are_all_running_is_refused() -> None:
    """The inner foreach's items are read in each outer item's own scope: item 0 opened one, item
    1 two, and all three are running, so the skip reaches nothing."""
    inner = {
        "kind": "foreach",
        "id": "inner",
        "config": {"items": "{{nodes.list.output.xs}}"},
        "body": RUNNING_WORK,
    }
    outer = {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [[1], [2, 3]]},
        "body": _steps(_transform("list", {"xs": "{{item}}"}), inner),
    }
    held = [f"{ITEM}#0.children[1].body#0", f"{ITEM}#1.children[1].body#0"]
    held.append(f"{ITEM}#1.children[1].body#1")
    c, release = await _held_running(_in(outer), held)
    assert c._outputs["list"] == {"xs": [2, 3]}, "item 1 wrote last"
    body = c.submit_mutation([{"op": "skip", "node_id": "inner"}], confirm=True)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body
    release.set()
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 3


def _counter(*answers: str):
    """A completion that answers each call with the next of *answers*, and the last after that."""
    calls = {"n": 0}

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        calls["n"] += 1
        return answers[min(calls["n"], len(answers)) - 1]

    return fn


def _outcomes(run_id: str) -> list[str]:
    return [str(r.get("outcome", "")) for r in J.ledger(run_id) if r.get("kind") == J.ITERATION]


async def test_a_loop_whose_last_step_was_skipped_reads_what_its_other_steps_did() -> None:
    """The cycle ends on the skipped step, which produced nothing: what `work` produced is the
    cycle's output, so new work each cycle is no stall."""
    work = {"kind": "infer", "id": "work", "config": {"prompt": "W", "schema": {"n": "string"}}}
    body = {"kind": "sequence", "id": "cyc", "children": [work, _transform("notify", {"sent": 1})]}
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 4}, "body": body}
    fn = _counter(*(f'{{"n": "c{k}"}}' for k in range(4)))
    c = await _parked(_spec(loop), EngineServices(completion=fn))
    assert c.submit_mutation([{"op": "skip", "node_id": "notify"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 4
    assert not any(o.startswith("breaker:") for o in _outcomes(c.run.id)), _outcomes(c.run.id)


async def test_an_until_dry_loop_whose_last_step_was_skipped_is_not_dry_while_it_finds() -> None:
    body = {
        "kind": "sequence",
        "id": "cyc",
        "children": [_transform("find", {"found": ["x"]}), _transform("notify", {"sent": 1})],
    }
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "until_dry", "streak": 1, "max_iterations": 2},
        "body": body,
    }
    c = await _parked(_spec(loop))
    assert c.submit_mutation([{"op": "skip", "node_id": "notify"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert _completions(c.run.id, "find") == 2, _outcomes(c.run.id)
    assert _completions(c.run.id, "notify") == 0, _outcomes(c.run.id)
    assert _outcomes(c.run.id)[0] == "continue", _outcomes(c.run.id)


async def test_a_loop_skipped_after_its_first_cycle_ends_on_what_that_cycle_produced() -> None:
    """An until loop whose step is skipped once cycle 0 has run: cycle 1 runs nothing, the loop
    ends there with no stall, and the step after it reads cycle 0's output."""
    holder: dict[str, RunController] = {}

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        holder["c"].submit_mutation([{"op": "skip", "node_id": "work"}], confirm=True)
        return '{"v": "first", "done": false}'

    work = {
        "kind": "infer",
        "id": "work",
        "config": {"prompt": "W", "schema": {"v": "string", "done": "boolean"}},
    }
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "until", "condition": "{{last.output.done}}"},
        "body": work,
    }
    spec = _in(loop, _transform("after", {"saw": "{{nodes.again.output.v}}"}))
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = live_runs.started(RunController(run, spec, services=EngineServices(completion=fn)))
    holder["c"] = c
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 1
    assert not any(o.startswith("breaker:") for o in _outcomes(c.run.id)), _outcomes(c.run.id)
    assert c._outputs["after"] == {"saw": "first"}, c._outputs.get("after")


async def test_a_skip_of_a_group_outside_any_loop_holds_its_waiting_step() -> None:
    """Outside a loop or foreach too: the gate waiting in the group goes on waiting, and the step
    after it is skipped once it is answered."""
    sup, c = await _supervised(_in(_steps(_gate("hold"), _transform("after", {"n": 1}))))
    body = service.skip_nodes(c.run.id, ["steps"], supervisor=sup)
    assert body["ok"], body
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    await asyncio.sleep(0.2)
    assert c.instances["root.children[0].children[0]"].state == InstanceState.WAITING
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    after = c.instances["root.children[0].children[1]"]
    assert (after.state, after.degraded_reason) == (
        InstanceState.SKIPPED,
        "not run: skipped as asked",
    )


async def test_a_cycle_that_ends_on_a_skipped_steps_reader_reads_what_its_other_steps_did() -> None:
    """`log` reads the skipped `notify`, so the frontier skips it too and the cycle ends on it:
    the cycle is still read by what `work` produced, new each cycle, and is no stall."""
    work = {"kind": "infer", "id": "work", "config": {"prompt": "W", "schema": {"n": "string"}}}
    body = {
        "kind": "sequence",
        "id": "cyc",
        "children": [
            work,
            _transform("notify", {"sent": 1}),
            _transform("log", {"saw": "{{nodes.notify.output.sent}}"}),
        ],
    }
    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 4}, "body": body}
    fn = _counter(*(f'{{"n": "c{k}"}}' for k in range(4)))
    c = await _parked(_spec(loop), EngineServices(completion=fn))
    assert c.submit_mutation([{"op": "skip", "node_id": "notify"}], confirm=True)["ok"]
    await _until(lambda: c._pending_mutations == [], what="the skip to apply")
    _answer(c)
    await asyncio.wait_for(c._terminal.wait(), timeout=10)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert _completions(c.run.id, "work") == 4
    logs = [c.instances[f"{BODY}@{n}.children[2]"].state for n in range(4)]
    assert logs == [InstanceState.SKIPPED] * 4, logs
    assert not any(o.startswith("breaker:") for o in _outcomes(c.run.id)), _outcomes(c.run.id)


async def test_a_cycle_that_ends_on_an_untaken_cases_reader_reads_as_before() -> None:
    """No skip is asked: `tell` reads the case the branch did not take, so the frontier skips it
    and the cycle ends on it. The cycle reads as it always has, empty, so the loop ends dry on its
    first cycle."""
    route = {
        "kind": "branch",
        "id": "route",
        "config": {"on": "{{nodes.find.output.tier}}"},
        "cases": {"small": _transform("quiet", {"q": 1}), "large": _transform("report", {"r": 1})},
    }
    body = _steps(
        _transform("find", {"tier": "small"}),
        route,
        _transform("tell", {"saw": "{{nodes.report.output.r}}"}),
    )
    loop = {
        "kind": "loop",
        "id": "again",
        "config": {"mode": "until_dry", "streak": 1, "max_iterations": 3},
        "body": body,
    }
    spec = _in(loop)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = live_runs.started(RunController(run, spec, services=EngineServices()))
    assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE, c.run.error_message
    assert c.instances["root.children[0].body@0.children[2]"].state == InstanceState.SKIPPED
    assert _completions(c.run.id, "find") == 1, _outcomes(c.run.id)
    assert _outcomes(c.run.id) == ["dry_streak"], _outcomes(c.run.id)


async def test_a_skip_of_a_step_in_finished_loops_inside_a_live_foreach_is_refused() -> None:
    """Each item's loop has run its cycles and the item waits at a gate after it: no cycle of
    `work` is still to open, though the foreach has not ended."""
    sup, c = await _supervised(_in(_fan(_steps(LOOP, _gate("hold")))))
    assert _completions(c.run.id, "work") == 4
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body
    await _answer_all(c)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message


async def test_a_skip_of_a_step_in_a_loop_inside_a_finished_foreach_is_refused() -> None:
    sup, c = await _supervised(_in(_fan(LOOP), _gate("after")))
    assert _completions(c.run.id, "work") == 4
    body = service.skip_nodes(c.run.id, ["work"], supervisor=sup)
    assert body["ok"] is False and body["code"] == "WF_SKIP_REACHES_NOTHING", body


SKIP_FAN = {"op": "skip", "node_id": "fan"}
REWIND_WORK = {"op": "rewind", "node_id": "work"}


@pytest.mark.parametrize(
    ("edits", "says"),
    [([[SKIP_FAN], [REWIND_WORK]], True), ([[SKIP_FAN, REWIND_WORK]], False)],
    ids=["a later edit", "the edit that skips it"],
)
async def test_a_rewind_of_a_step_inside_a_skipped_foreach_says_it_stays_skipped(
    edits: list[list[dict[str, str]]], says: bool
) -> None:
    """The rewind is applied, and the foreach's own skip is left for the owner to undo. A rewind
    in the edit that skips the foreach asked for that, so its answer says nothing more."""
    c = await _parked(_spec(FOREACH))
    for ops in edits:
        body = c.submit_mutation(ops, confirm=True)
        assert body["ok"], body
        await _until(lambda: c._pending_mutations == [], what="the edit to apply")
    issues = [i for i in body["issues"] if i["code"] == "WF_MUT_REWOUND_UNDER_A_SKIP"]
    if says:
        (issue,) = issues
        assert issue["node_id"] == "fan" and "“fan”" in issue["message"], issue
    else:
        assert issues == [], issues
    assert store.read_spec(c.run.id)["root"]["children"][1]["config"].get("__skipped")
