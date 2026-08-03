"""The projected Task WRITE, and the actor asymmetry it depends on (TASKS-SOPS §1 — S61g).

S61f wired the projection call site but wrote nothing — it emitted the event and recorded
the binding.
This session makes the Task real, which is the first point in the whole program where running a
workflow puts a row on the user's board.

The write is the ENGINE actor in §1's three-actor matrix, and the asymmetry is the entire
design: the
engine sets a managed task's status directly, and `materialize.reject_write` refuses that same write
from anyone else, naming the alternative (`workflow_skip`/`workflow_rewind`). Two writers on one
status field produce a board that disagrees with the run it shows — and the user believes
the board.

Driven through REAL runs, because the shape being tested is "does the row appear when a workflow
runs". A unit test on the writer would not have caught the async mismatch below.

Measured while wiring: the settle path (`_apply`) is SYNC but runs inside the async tick, while the
task provider's `create_task` is async. The event now fires from the write's COMPLETION so `task_id`
is the real id — an event with an empty id would tell a board to render a row it cannot open.
"""

import asyncio

import pytest

from personalclaw.workflows.journal import TASK_MATERIALIZED, ledger
from personalclaw.workflows.materialize import reject_write


def _spec(children: list) -> dict:
    return {"name": "t", "root": {"kind": "sequence", "id": "s", "children": children}}


def _action(node_id: str, **config) -> dict:
    return {
        "kind": "action",
        "id": node_id,
        "config": {"provider": "bash", "with": {"command": "true"}, **config},
    }


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    apreg._ensure_default_providers_registered()
    yield


async def _run_and_settle(spec: dict, run_id: str = "r-1"):
    """Execute a spec and AWAIT the projection writes, rather than sleeping.

    The writes are scheduled on the loop, so a test that returned immediately would race them. The
    controller tracks them for exactly this reason.
    """
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id=run_id, workflow_name="t")
    wstore.create(run)
    controller = RunController(run, spec, services=EngineServices())
    await controller.run_to_completion()
    if controller._projection_writes:
        await asyncio.gather(*list(controller._projection_writes))
    return run, controller


async def _tasks() -> list:
    from personalclaw.tasks.registry import list_all_tasks

    rows = await list_all_tasks()
    return rows[0] if isinstance(rows, tuple) else rows


# ── the row appears ──


def test_running_a_workflow_puts_a_ROW_ON_THE_BOARD():
    """The first point in the program where the projection is a product feature rather than a
    mechanism: a user runs a template and sees the steps."""

    async def go():
        await _run_and_settle(_spec([_action("impl")]))
        return await _tasks()

    tasks = asyncio.run(go())
    assert [t.title for t in tasks] == ["impl"]


def test_every_leaf_gets_its_own_row():
    async def go():
        await _run_and_settle(_spec([_action("impl"), _action("verify")]))
        return sorted(t.title for t in await _tasks())

    assert asyncio.run(go()) == ["impl", "verify"]


def test_the_row_is_MANAGED_and_names_its_run_and_node():
    """`managed` is what makes `reject_write` refuse a user's status edit. Without it the task is an
    ordinary manual task that happens to have been created by a run."""

    async def go():
        await _run_and_settle(_spec([_action("impl")]))
        return (await _tasks())[0]

    task = asyncio.run(go())
    binding = task.workflow_binding
    assert binding is not None
    assert binding.managed is True
    assert binding.run_id == "r-1"
    assert binding.node_id == "impl"
    assert binding.fingerprint


def test_the_EVENT_carries_the_real_task_id():
    """Fired from the write's completion, not before it. An event with an empty id would
    tell a board
    to render a row it cannot open."""

    async def go():
        await _run_and_settle(_spec([_action("impl")]))
        return await _tasks()

    tasks = asyncio.run(go())
    row = next(r for r in ledger("r-1") if r["kind"] == TASK_MATERIALIZED)
    assert row["task_id"] == tasks[0].id


def test_a_CONTAINER_gets_no_row():
    async def go():
        await _run_and_settle(
            _spec([{"kind": "parallel", "id": "fan", "children": [_action("one"), _action("two")]}])
        )
        return sorted(t.title for t in await _tasks())

    assert asyncio.run(go()) == ["one", "two"]


def test_an_OPT_OUT_node_gets_no_row():
    async def go():
        await _run_and_settle(_spec([_action("kept"), _action("hidden", materialize_task=False)]))
        return [t.title for t in await _tasks()]

    assert asyncio.run(go()) == ["kept"]


def test_a_FAILED_node_gets_no_row():
    """A row for work that did not happen is one a user would try to close."""

    async def go():
        await _run_and_settle(
            _spec(
                [
                    {
                        "kind": "action",
                        "id": "boom",
                        "config": {"provider": "bash", "with": {"command": "exit 7"}},
                    }
                ]
            )
        )
        return await _tasks()

    assert asyncio.run(go()) == []


# ── the actor asymmetry ──


def test_the_ENGINE_write_lands_while_a_USER_status_write_is_REFUSED():
    """The whole design. Two writers on one status field produce a board that disagrees with the run
    it shows, and the user believes the board."""

    async def go():
        await _run_and_settle(_spec([_action("impl")]))
        return (await _tasks())[0]

    task = asyncio.run(go())
    assert task.workflow_binding.managed is True
    refusal = reject_write(task, {"status": "done"})
    assert refusal
    assert "workflow_skip" in refusal or "workflow_rewind" in refusal


def test_a_NON_ENGINE_field_is_still_writable():
    """The refusal is scoped to the engine-owned fields. Refusing everything would make a projected
    task read-only, and a user who cannot even leave a note on their own board row would rightly
    call that broken."""

    async def go():
        await _run_and_settle(_spec([_action("impl")]))
        return (await _tasks())[0]

    task = asyncio.run(go())
    assert reject_write(task, {"notes": ["looked at this"]}) == ""


def test_an_UNMANAGED_task_is_not_governed_by_the_matrix():
    """A standalone manual task must stay fully editable — the plan is explicit that they remain
    independent."""

    async def go():
        from personalclaw.tasks.registry import create_task

        return await create_task("native", title="mine")

    task = asyncio.run(go())
    assert reject_write(task, {"status": "done"}) == ""


# ── failure containment ──


def test_a_WRITE_FAILURE_does_not_fail_the_run(monkeypatch):
    """The node has already succeeded and its output is journaled. Losing real work over a board row
    would be the wrong trade."""
    import personalclaw.tasks.registry as treg

    async def boom(*_a, **_kw):
        raise RuntimeError("task store unavailable")

    monkeypatch.setattr(treg, "create_task", boom)

    async def go():
        run, _ctl = await _run_and_settle(_spec([_action("impl")]))
        return run

    run = asyncio.run(go())
    assert run.status.value == "complete"


def test_a_write_failure_still_EMITS_with_an_empty_id(monkeypatch):
    """Honest rather than silent: the projection was attempted and did not land, and the next
    rebuild recovers it. Suppressing the event entirely would hide that anything was meant to
    happen."""
    import personalclaw.tasks.registry as treg

    async def boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(treg, "create_task", boom)

    async def go():
        await _run_and_settle(_spec([_action("impl")]))

    asyncio.run(go())
    row = next(r for r in ledger("r-1") if r["kind"] == TASK_MATERIALIZED)
    assert row["task_id"] == ""


def test_the_step_is_journaled_even_when_the_write_fails(monkeypatch):
    import personalclaw.tasks.registry as treg

    async def boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(treg, "create_task", boom)

    async def go():
        await _run_and_settle(_spec([_action("impl")]))

    asyncio.run(go())
    assert any(r["kind"] == "step_completed" for r in ledger("r-1"))


# ── the in-flight bookkeeping ──


def test_in_flight_writes_are_TRACKED_then_cleared():
    """Tracked so a teardown does not orphan them and a test can await settlement rather than
    sleeping — a sleep-based test of a scheduled write is a flake generator."""

    async def go():
        _run, ctl = await _run_and_settle(_spec([_action("impl")]))
        return ctl

    ctl = asyncio.run(go())
    assert ctl._projection_writes == set()


def test_the_dedup_set_is_recorded_BEFORE_the_write_is_scheduled():
    """Or a second settle in the same tick would plan the same task again while the first write is
    still in flight — and the board would grow two rows for one node."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController._project_task)
    appended = source.index("self._projected.append(")
    scheduled = source.index("self._schedule_task_write(")
    assert appended < scheduled


# ── the drain (a measured defect) ──


def test_completion_DRAINS_the_projection_write():
    """The defect this session found and fixed. A projected Task write is scheduled on the loop from
    the SYNC settle path, and `run_to_completion` used to return with it still pending — so
    a caller
    that awaited the run and then closed its loop LOST the board row entirely, with the run
    reporting
    `complete` and the ledger showing no `task_materialized`. The row is the user-visible half of
    running a workflow.

    Deliberately does NOT drain manually: that is the whole point. If a caller has to know to drain,
    every caller that does not is silently broken.
    """
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    async def go():
        run = WorkflowRun(id="r-1", workflow_name="t")
        wstore.create(run)
        controller = RunController(run, _spec([_action("impl")]), services=EngineServices())
        await controller.run_to_completion()
        return controller

    controller = asyncio.run(go())
    assert controller._projection_writes == set()
    assert [r["kind"] for r in ledger("r-1") if r["kind"] == TASK_MATERIALIZED]


def test_the_drain_is_BOUNDED(monkeypatch):
    """A hung task store must not hold a finished run open forever.

    Exercises `drain_projection_writes` DIRECTLY with a short timeout rather than through
    `run_to_completion`: that path applies the default 10s bound, so a hung-store test
    routed through
    it takes ten real seconds. Measured — it was the slowest test in the suite by two orders of
    magnitude, and on a shared xdist worker it pushed an unrelated aiohttp test past ITS 120s
    timeout. A slow test is not just slow; it makes its neighbours flaky.

    The write is left running rather than cancelled: a cancelled write may already have created the
    task, and cancelling would lose the id without undoing the row.
    """
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    async def go():
        run = WorkflowRun(id="r-1", workflow_name="t")
        wstore.create(run)
        controller = RunController(run, _spec([]), services=EngineServices())

        async def never() -> None:
            await asyncio.sleep(30)

        handle = asyncio.get_running_loop().create_task(never())
        controller._projection_writes.add(handle)
        await controller.drain_projection_writes(timeout=0.05)
        still_running = not handle.done()
        handle.cancel()
        return still_running

    assert asyncio.run(go()) is True, "the drain must give up, not cancel the write"


def test_draining_with_nothing_in_flight_is_a_no_op():
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    async def go():
        run = WorkflowRun(id="r-1", workflow_name="t")
        wstore.create(run)
        controller = RunController(run, _spec([]), services=EngineServices())
        await controller.drain_projection_writes()

    asyncio.run(go())
