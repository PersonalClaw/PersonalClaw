"""A stopped workflow run ends the agents and the runs its steps launched, each saying why.

Four step kinds start work that outlives their step. An Invoke Agent (`invoke-agent`) or Run
Prompt (`run-prompt`) action step starts an agent (``SubagentInfo.workflow_run``), a `subworkflow`
step starts a child run it waits on, and a Run workflow (`run-workflow`) step starts a run of its
own (both ``parent_run_id``; the second with ``spawned_by_node_id``). A run that was stopped read
Cancelled while all four went on: the agents kept spending and asking for approvals, and the child
runs kept running.

The rule, by how the run ended (``started_work.end_run``):

* cancelled or deleted: all four end, saying the run's clause, a queued Run workflow run and what
  the agents started from their own sessions included;
* failed, escalated or declined: the subworkflow runs it was still waiting on end, and the one a
  settled step let go, the agents and the Run workflow runs its steps launched to go on alone go
  on;
* finished: nothing more ends.

No ending reaches a fork. A child it ends ends what it launched in turn, through its own controller.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from personalclaw import started_work
from personalclaw.workflows import store
from personalclaw.workflows.models import RunStatus, WorkflowRun

NAME = "Weekly digest"
CLAUSE = f"the workflow run “{NAME}” that started it was cancelled"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)


class _Agent:
    def __init__(self, agent_id: str, *, workflow_run: str = "", done: bool = False) -> None:
        self.id = agent_id
        self.workflow_run = workflow_run
        self.done = done
        self.parent_session_key = ""
        self.parent_run = ""
        self.started = 0.0


class _Subagents:
    """The subagent manager's surface ``started_work`` reads: its agents, and their stop."""

    def __init__(self, *agents: _Agent) -> None:
        self.all_agents = list(agents)
        self.stopped: list[tuple[list[str], str]] = []

    async def stop_agents(self, agent_ids, *, because: str) -> int:
        ids = list(agent_ids)
        self.stopped.append((ids, because))
        for agent in self.all_agents:
            if agent.id in ids:
                agent.done = True
        return len(ids)


def _run(**over: Any) -> WorkflowRun:
    fields: dict[str, Any] = {"id": "", "workflow_name": "digest", "title": NAME, **over}
    return store.create(WorkflowRun(**fields))


def _world() -> tuple[WorkflowRun, dict[str, WorkflowRun], _Subagents]:
    """A run whose steps launched an Invoke Agent agent and a Run Prompt agent (one still going,
    one done), a subworkflow's child run and a Run workflow step's run; beside them a fork of
    it, another run's child and another run's agent, none of which are its steps' work."""
    parent = _run(status=RunStatus.RUNNING)
    other = _run(status=RunStatus.RUNNING)
    live = {"status": RunStatus.RUNNING}
    children = {
        "subworkflow": _run(parent_run_id=parent.id, **live),
        "run-workflow": _run(parent_run_id=parent.id, spawned_by_node_id="kick_off", **live),
        "fork": _run(parent_run_id=parent.id, forked_from={"run_id": parent.id}, **live),
        "another's child": _run(parent_run_id=other.id, **live),
    }
    subagents = _Subagents(
        _Agent("invoke-agent", workflow_run=parent.id),
        _Agent("run-prompt", workflow_run=parent.id),
        _Agent("finished", workflow_run=parent.id, done=True),
        _Agent("another's", workflow_run=other.id),
    )
    return parent, children, subagents


def _end(parent: WorkflowRun, subagents: _Subagents, ending: str) -> None:
    asyncio.run(started_work.end_run(parent, ending=ending, subagents=subagents, instances={}))


def _cancelled(children: dict[str, WorkflowRun]) -> dict[str, str]:
    return {
        kind: store.cancel_reason(run.id)
        for kind, run in children.items()
        if store.cancel_requested(run.id)
    }


def _stopped(subagents: _Subagents) -> dict[str, str]:
    return {agent_id: because for ids, because in subagents.stopped for agent_id in ids}


@pytest.mark.parametrize("ending", ["was cancelled", "was deleted"])
def test_a_stopped_run_ends_every_agent_and_run_its_steps_launched(ending: str) -> None:
    parent, children, subagents = _world()
    _end(parent, subagents, ending)
    clause = f"the workflow run “{NAME}” that started it {ending}"
    assert _stopped(subagents) == {"invoke-agent": clause, "run-prompt": clause}
    assert _cancelled(children) == {"subworkflow": clause, "run-workflow": clause}


@pytest.mark.parametrize("ending", ["failed", "has stopped", "was declined"])
def test_a_run_that_ends_unfinished_ends_only_the_runs_it_was_waiting_on(ending: str) -> None:
    """Nothing waits for a subworkflow's run once its parent has ended. What its steps launched
    to go on alone, an agent or a Run workflow step's run, the owner did not stop."""
    parent, children, subagents = _world()
    _end(parent, subagents, ending)
    assert _stopped(subagents) == {}
    clause = f"the workflow run “{NAME}” that started it {ending}"
    assert _cancelled(children) == {"subworkflow": clause}


def test_a_fire_and_forget_agent_outlives_a_failed_run_but_not_a_cancelled_one() -> None:
    failed, _children, after_failure = _world()
    _end(failed, after_failure, "failed")
    assert not any(a.done for a in after_failure.all_agents if a.id == "invoke-agent")
    cancelled, _children, after_cancel = _world()
    _end(cancelled, after_cancel, "was cancelled")
    assert all(a.done for a in after_cancel.all_agents if a.id == "invoke-agent")


def test_a_finished_run_leaves_what_its_steps_launched_going() -> None:
    parent, children, subagents = _world()
    _end(parent, subagents, "has finished")
    assert _stopped(subagents) == {}
    assert _cancelled(children) == {}


def test_a_run_its_loop_or_turn_stopped_ends_them_saying_that() -> None:
    """A run another ending stopped carries that ending's clause, and so does what it launched."""
    parent, children, subagents = _world()
    asyncio.run(
        started_work.end_run(
            parent,
            ending="was cancelled",
            because="its chat turn was stopped",
            subagents=subagents,
            instances={},
        )
    )
    assert set(_stopped(subagents).values()) == {"its chat turn was stopped"}
    assert set(_cancelled(children).values()) == {"its chat turn was stopped"}


def test_a_cancelled_run_stops_its_launched_agent_as_it_finishes() -> None:
    """Through the controller's own ending (``RunController._finish``), not only the helper."""
    from personalclaw.workflows.controller import EngineServices, RunController

    parent, children, subagents = _world()
    spec = {"name": "digest", "root": {"kind": "sequence", "id": "s", "children": []}}
    ctl = RunController(parent, spec, services=EngineServices(subagents=subagents))
    asyncio.run(ctl._finish(RunStatus.CANCELLED))
    assert _stopped(subagents) == {"invoke-agent": CLAUSE, "run-prompt": CLAUSE}
    assert _cancelled(children) == {"subworkflow": CLAUSE, "run-workflow": CLAUSE}


def test_a_failed_run_leaves_a_subworkflow_run_it_stopped_waiting_for() -> None:
    """A `subworkflow` step that settled recorded its child (``child_run_attach``): it stopped
    waiting for that run, which goes on alone after a failure. A cancel still ends it."""
    from personalclaw.workflows.journal import Journal

    parent, children, subagents = _world()
    Journal(parent.id).child_run_attach(parent.id, children["subworkflow"].id, "nested")
    _end(parent, subagents, "failed")
    assert _cancelled(children) == {}
    _end(parent, subagents, "was cancelled")
    assert _cancelled(children) == {"subworkflow": CLAUSE, "run-workflow": CLAUSE}


def _queued(parent: WorkflowRun) -> WorkflowRun:
    """The run a Run workflow step started, queued behind its workflow's overlap policy."""
    from personalclaw.workflows.overlap import QUEUED_KEY

    return _run(
        parent_run_id=parent.id,
        spawned_by_node_id="kick_off",
        status=RunStatus.DRAFT,
        extra={QUEUED_KEY: True},
    )


@pytest.mark.parametrize(
    ("ending", "status"),
    [
        ("was cancelled", RunStatus.CANCELLED),
        ("was deleted", RunStatus.CANCELLED),
        ("failed", RunStatus.DRAFT),
    ],
)
def test_a_stopped_run_ends_the_run_its_run_workflow_step_queued(
    ending: str, status: RunStatus
) -> None:
    parent, _children, subagents = _world()
    queued = _queued(parent)
    _end(parent, subagents, ending)
    after = store.get(queued.id)
    assert after is not None and after.status == status, (ending, after)


class _Supervisor:
    """The supervisor's surface the queue's drain reads: no controller held, and its launch."""

    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: WorkflowRun, spec: dict[str, Any]) -> None:
        self.launched.append(run.id)


def test_a_cancelled_run_ends_a_run_it_queued_behind_itself_before_the_queue_drains() -> None:
    """A Run workflow step that starts its own workflow (``on_overlap: queue``) queues that run
    behind this one. The run's ending ends it while it is still queued, before the drain, which
    reads the workflow free once this run has ended, could start it."""
    from personalclaw.workflows.controller import EngineServices, RunController

    parent = _run(status=RunStatus.RUNNING)
    queued = _queued(parent)
    spec = {"name": "digest", "root": {"kind": "sequence", "id": "s", "children": []}}
    store.write_spec(queued.id, spec)
    supervisor = _Supervisor()
    services = EngineServices(supervisor=supervisor, subagents=_Subagents())
    ctl = RunController(parent, spec, services=services)
    asyncio.run(ctl._finish(RunStatus.CANCELLED))
    assert supervisor.launched == []
    after = store.get(queued.id)
    assert after is not None and after.status == RunStatus.CANCELLED, after


@pytest.mark.parametrize(("ending", "ends"), [("was cancelled", True), ("failed", False)])
def test_a_stopped_run_ends_what_its_launched_agent_started(ending: str, ends: bool) -> None:
    """The Invoke Agent step's agent started a helper and a batch from its own session
    (``subagent:<id>``): they end with it on a cancel, and go on after a failure."""
    from personalclaw.workflows.models import RunOrigin

    parent, _children, subagents = _world()
    helper = _Agent("helper")
    helper.parent_session_key = "subagent:invoke-agent"
    subagents.all_agents.append(helper)
    batch = _run(status=RunStatus.RUNNING, origin=RunOrigin(session_key="subagent:invoke-agent"))
    _end(parent, subagents, ending)
    assert ("helper" in _stopped(subagents)) is ends, subagents.stopped
    assert store.cancel_requested(batch.id) is ends


def test_a_failed_parent_ends_what_its_cancelled_child_launched() -> None:
    """A parent's failure cancels the subworkflow run it was waiting on, and that run's own
    controller then ends as cancelled: its Invoke Agent step's agent is stopped too, saying why
    the parent's ending reached it."""
    from personalclaw.workflows.controller import EngineServices, RunController

    parent, children, subagents = _world()
    child = children["subworkflow"]
    subagents.all_agents.append(_Agent("child's agent", workflow_run=child.id))
    _end(parent, subagents, "failed")
    failed = f"the workflow run “{NAME}” that started it failed"
    assert store.cancel_reason(child.id) == failed
    spec = {"name": "digest", "root": {"kind": "sequence", "id": "s", "children": []}}
    ctl = RunController(store.get(child.id), spec, services=EngineServices(subagents=subagents))
    assert asyncio.run(ctl.run_to_completion(timeout=10)) == RunStatus.CANCELLED
    assert _stopped(subagents) == {"child's agent": failed}
