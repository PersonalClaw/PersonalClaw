"""A task a workflow run files for one of its own steps is the run's, not the owner's.

The morning triage's run filed "Collect, filter, propose and deliver" in her Tasks, on Home ("5
tasks ready") and on the phone: open, although the step had already finished; attributed to her,
as if she had written it; and hers to do, although its status is the run's and an edit of it is
refused. A run files a task for each step it settles (`workflows.materialize`), and three things
were wrong with each:

* it was born OPEN, whatever its step's state, and nothing moved it after;
* the task store stamped the owner as its author, because it stamps whoever it is running for;
* so it counted as the owner's work, and her ready work, like any task she wrote.

Now a step's task is born with its step's state, names no person as its author, and is nobody's
work to pick up: the run does it. The morning triage's one step collects and delivers its digest,
and the digest it delivers is what the owner sees of it, so it files none.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.tasks.models import Task, TaskStatus, WorkflowTaskBinding

OWNER = "noor"


def _spec(children: list) -> dict:
    return {"name": "t", "root": {"kind": "sequence", "id": "s", "children": children}}


def _action(node_id: str, label: str = "") -> dict:
    node: dict = {
        "kind": "action",
        "id": node_id,
        "config": {"provider": "bash", "with": {"command": "true"}},
    }
    if label:
        node["label"] = label
    return node


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.identity.current_username", lambda: OWNER)
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    apreg._ensure_default_providers_registered()
    yield


async def _run_and_settle(spec: dict, run_id: str = "r-1") -> None:
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id=run_id, workflow_name="t")
    wstore.create(run)
    controller = RunController(run, spec, services=EngineServices())
    await controller.run_to_completion()
    if controller._projection_writes:
        await asyncio.gather(*list(controller._projection_writes))


async def _all_tasks() -> list[Task]:
    from personalclaw.tasks.registry import collect_tasks

    tasks, _cut = await collect_tasks()
    return tasks


def _steps_task() -> Task:
    async def go() -> Task:
        await _run_and_settle(_spec([_action("triage", "Collect, filter, propose and deliver")]))
        [task] = await _all_tasks()
        return task

    return asyncio.run(go())


def test_a_steps_task_is_born_with_its_steps_state():
    """🔴 Before: `open`, for a step that had already finished."""
    task = _steps_task()
    assert task.title == "Collect, filter, propose and deliver"
    assert task.status is TaskStatus.DONE


def test_a_steps_task_names_no_person_as_its_author():
    """🔴 Before: `author == "noor"`: the owner, as if she wrote it."""
    task = _steps_task()
    assert task.author == ""
    assert task.workflow_binding is not None and task.workflow_binding.managed


def test_a_steps_task_is_not_the_owners_work():
    """🔴 Before: hers (`belongs_to`), so `?mine=1` and Home counted it."""
    task = _steps_task()
    assert task.belongs_to(OWNER) is False

    async def mine() -> list[Task]:
        from personalclaw.tasks.registry import collect_tasks

        tasks, _cut = await collect_tasks(owner=OWNER)
        return tasks

    assert asyncio.run(mine()) == []


def test_a_task_the_run_manages_is_nobodys_ready_work():
    """Its status is the run's, so nobody picks it up: not the owner's Home, not an agent."""

    async def go() -> list[str]:
        from personalclaw.tasks.registry import create_task, ready_tasks

        await create_task(
            "native",
            title="Draft the plan",
            workflow_binding={"run_id": "r-2", "node_id": "plan", "managed": True},
        )
        await create_task("native", title="Book the physio")
        return [t.title for t in await ready_tasks()]

    assert asyncio.run(go()) == ["Book the physio"]


def test_the_owners_own_task_is_still_hers():
    """The control: a task she creates is authored by her, hers, and ready."""

    async def go() -> tuple[Task, list[str]]:
        from personalclaw.tasks.registry import create_task, ready_tasks

        task = await create_task("native", title="Book the physio")
        return task, [t.title for t in await ready_tasks()]

    task, ready = asyncio.run(go())
    assert task.author == OWNER and task.belongs_to(OWNER)
    assert ready == ["Book the physio"]


def test_a_task_someone_is_assigned_is_theirs_whoever_filed_it():
    """Assignment decides, for a run's task as for any: an assigned step is its assignee's."""
    binding = WorkflowTaskBinding(run_id="r-3", node_id="review", managed=True)
    assert Task(id="t", title="Review", assignee=OWNER, workflow_binding=binding).belongs_to(OWNER)
    assert not Task(id="u", title="Review", workflow_binding=binding).belongs_to(OWNER)
    # A task a run only PRODUCED (not managed) is ordinary work, attributed as any other.
    produced = WorkflowTaskBinding(run_id="r-3", node_id="review", managed=False)
    assert Task(id="v", title="Follow up", workflow_binding=produced).belongs_to(OWNER)


def test_the_morning_triage_files_no_task_for_its_own_step():
    """Its one step collects and delivers the digest, and the digest is what she sees of it."""
    from personalclaw.workflows.materialize import NON_MATERIALIZING_KINDS, should_materialize

    path = (
        Path(__file__).resolve().parents[1]
        / "src/personalclaw/workflows/bundled/morning-triage/workflow.json"
    )
    root = json.loads(path.read_text(encoding="utf-8"))["root"]
    steps = [n for n in root["children"] if n["kind"] not in NON_MATERIALIZING_KINDS]
    assert [n["id"] for n in steps] == ["triage"]
    assert should_materialize(steps[0]) == (False, "node declares materialize_task: false")


def test_a_step_that_files_no_task_is_not_told_its_opt_out_is_an_argument():
    """`materialize_task` is the engine's key, not the provider's: it stays beside `provider`."""
    from personalclaw.workflows.validator import validate_spec

    spec = _spec(
        [
            {
                "kind": "action",
                "id": "d",
                "config": {"provider": "notification-digest", "materialize_task": False},
            }
        ]
    )
    assert [i.code for i in validate_spec(spec).issues] == ["WF_ACTION_NO_ARGS"]
