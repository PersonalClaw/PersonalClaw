"""A workflow run's end ends what its steps started: a batch one of its steps' subagents started
from its own session, and that batch's steps and what they were waiting on.

A step of a workflow run runs as a subagent, whose own session is ``subagent:<id>``; a batch it
hands to ``subagent_run`` records that session as the one that started it (``origin.session_key``).
When the run ended (stopped, failed, deleted), its own steps' subagents were stopped, but such a
batch went on: its steps kept running and spending, and kept their approvals answerable, for a run
that was over.

The rule is the one a loop's end and a turn's Stop follow (``started_work``): what the run's steps
started ends with the run, saying how the run ended. These drive a real ``RunController`` for the
run and another for the batch, both over one real ``SubagentManager`` whose spawns ask the real
approval registry, and end the run through the real cancel, a real failing step, and the real
delete.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    _open_rows,
    _real_manager,
    _resolved,
    _until,
)

from personalclaw.approval_answer import YOU
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

RUN_NAME = "release-review"


def _review_spec(*, fail_run: bool = False) -> dict[str, Any]:
    """The run: one step, whose subagent waits on its start's approval."""
    config: dict[str, Any] = {"prompt": "review the release"}
    if fail_run:
        config["on_error"] = "fail_run"
    return {
        "name": RUN_NAME,
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "review", "label": "Review", "config": config}],
        },
    }


def _batch_spec() -> dict[str, Any]:
    """A compiled batch's shape: a quorum parallel of one labelled step."""
    return {
        "name": "subagent-batch-1",
        "root": {
            "kind": "parallel",
            "id": "batch",
            "config": {"join": "quorum", "quorum": 1},
            "children": [
                {
                    "kind": "stage",
                    "id": "check_the_links_0",
                    "label": "Check the links",
                    "config": {"prompt": "check the links"},
                }
            ],
        },
    }


class _Supervisor:
    """The controller registry a cancel wakes a live run through."""

    def __init__(self) -> None:
        self.controllers: dict[str, RunController] = {}

    def controller(self, run_id: str) -> RunController | None:
        return self.controllers.get(run_id)


def _drive(w, run: WorkflowRun, spec: dict[str, Any]) -> asyncio.Task:
    controller = RunController(
        run,
        spec,
        services=EngineServices(
            subagents=w.manager, cwd="", attention_state=w.state, supervisor=w.state.workflows
        ),
    )
    w.state.workflows.controllers[run.id] = controller
    return asyncio.create_task(controller.run_to_completion(timeout=30.0))


def _run(name: str, spec: dict[str, Any], *, session_key: str = "") -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            origin=RunOrigin(kind=OriginKind.API, session_key=session_key),
        )
    )
    store.write_spec(run.id, spec)
    return run


@pytest.fixture
def runs(world, monkeypatch):  # noqa: F811 - the imported fixture
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    world.manager = _real_manager(world.state)
    world.state.workflows = _Supervisor()
    return world


def _spawns(w) -> set[str]:
    return {a for a in w.state._pending_approvals if a.startswith("spawn:")}


async def _a_run_whose_step_started_a_batch(w, *, fail_run: bool = False):
    """The run's step is waiting to start; its subagent started a batch from its own session,
    whose step is waiting to start too."""
    parent = _run(RUN_NAME, _review_spec(fail_run=fail_run))
    parent_driving = _drive(w, parent, _review_spec(fail_run=fail_run))
    await _until(lambda: len(_spawns(w)) == 1, "the run's step never asked to start")
    (step_ask,) = _spawns(w)
    step_agent = step_ask.removeprefix("spawn:")
    batch = _run("subagent-batch-1", _batch_spec(), session_key=f"subagent:{step_agent}")
    batch_driving = _drive(w, batch, _batch_spec())
    await _until(lambda: len(_spawns(w)) == 2, "the batch's step never asked to start")
    (batch_ask,) = _spawns(w) - {step_ask}
    return SimpleNamespace(
        parent=parent,
        parent_driving=parent_driving,
        step_ask=step_ask,
        batch=batch,
        batch_driving=batch_driving,
        batch_ask=batch_ask,
    )


async def _assert_the_batch_ended(w, t, ending: str) -> None:
    status = await asyncio.wait_for(t.batch_driving, timeout=10)
    assert status is RunStatus.CANCELLED, "the batch the run's step started went on"
    run = store.get(t.batch.id)
    assert run.error_message == f"Stopped because {ending}.", run.error_message
    (inst,) = store.read_state(t.batch.id).values()
    assert inst.state is InstanceState.CANCELLED, "the batch's step still reads running"
    info = w.manager.get(t.batch_ask.removeprefix("spawn:"))
    assert info.cancelled and info.done, vars(info)
    assert info.error == f"Cancelled because {ending}", info.error
    assert t.batch_ask not in w.state._pending_approvals, "Home and To triage still list it"
    (frame,) = _resolved(w, t.batch_ask)
    assert frame["outcome"] == "cancelled", frame
    assert frame["ended"] == (
        f"the workflow run that asked for it was cancelled because {ending}"
    ), frame
    assert not _open_rows(w, t.batch_ask), "the Inbox still offers the batch's ask"


@pytest.mark.asyncio
async def test_stopping_a_run_ends_the_batch_its_step_started(runs):
    w = runs
    t = await _a_run_whose_step_started_a_batch(w)

    assert service.cancel_run(t.parent.id)["ok"] is True
    assert await asyncio.wait_for(t.parent_driving, timeout=10) is RunStatus.CANCELLED

    await _assert_the_batch_ended(
        w, t, f"the workflow run “{RUN_NAME}” that started it was cancelled"
    )


@pytest.mark.asyncio
async def test_a_run_that_fails_ends_the_batch_its_step_started(runs):
    """The step's subagent is allowed and fails at once (this world has no model), so the step
    fails and, being the run's, fails the run; the batch it had started ends with it."""
    w = runs
    t = await _a_run_whose_step_started_a_batch(w, fail_run=True)

    assert w.state.resolve_approval(t.step_ask, True, by=YOU) is True
    assert await asyncio.wait_for(t.parent_driving, timeout=10) is RunStatus.FAILED

    await _assert_the_batch_ended(w, t, f"the workflow run “{RUN_NAME}” that started it failed")


@pytest.mark.asyncio
async def test_deleting_a_run_ends_the_batch_its_step_started(runs):
    """A run that ended without ending its steps' batch (it ended before this rule, or in a
    process that died first) still ends it when it is deleted."""
    w = runs
    t = await _a_run_whose_step_started_a_batch(w)
    # The run ends with its step still on record and the batch left running, as an earlier
    # ending left it: its controller is stopped, and its row written ended by hand.
    await w.state.workflows.controllers.pop(t.parent.id).stop()
    t.parent_driving.cancel()
    parent = store.get(t.parent.id)
    parent.status = RunStatus.COMPLETE
    store.save(parent)
    assert store.get(t.batch.id).status is RunStatus.RUNNING, "premise: the batch is going"

    result = await service.delete_run(t.parent.id, supervisor=w.state.workflows)
    assert result["ok"] is True, result

    await _assert_the_batch_ended(
        w, t, f"the workflow run “{RUN_NAME}” that started it was deleted"
    )


@pytest.mark.asyncio
async def test_a_run_stopped_by_its_loop_ends_the_batch_naming_the_loop(runs):
    """What ended the run is what ends the batch: a run cancelled because its loop was stopped
    ends its step's batch saying so, as the loop's other children do."""
    w = runs
    t = await _a_run_whose_step_started_a_batch(w)

    assert service.cancel_run(t.parent.id, reason="its loop “Release notes” was stopped")["ok"]
    assert await asyncio.wait_for(t.parent_driving, timeout=10) is RunStatus.CANCELLED

    await _assert_the_batch_ended(w, t, "its loop “Release notes” was stopped")


@pytest.mark.asyncio
async def test_a_batch_no_step_of_the_run_started_goes_on(runs):
    """The positive control: the run's end reaches what ITS steps started, by the link."""
    w = runs
    t = await _a_run_whose_step_started_a_batch(w)
    other = _run("subagent-batch-2", _batch_spec(), session_key="subagent:0000abcd")
    other.status = RunStatus.RUNNING
    store.save(other)

    assert service.cancel_run(t.parent.id)["ok"] is True
    await asyncio.wait_for(t.parent_driving, timeout=10)

    assert not store.cancel_requested(other.id)
    assert store.get(other.id).status is RunStatus.RUNNING
    store.request_cancel(t.batch.id)  # this test's own batch, ended so nothing is left driving
    await asyncio.wait_for(t.batch_driving, timeout=10)
