"""A project's standing guidance reaches its runs' workers and judges, as the run page says.

🔴 The defect (Backlog: "The run page's Judge guidance says it reaches the worker and the judge,
and neither is given it"). On a run in a project, the steering panel's Judge guidance saves the
project's instructions and says "it reaches the worker and the judge. Applies to future cycles".
No prompt a run's worker or judge started from held them: a judge gate's instruction was built
from its own prompt and evidence, and a stage's task from its template, so guidance saved to change
how a run is judged changed nothing about the run.

Now each stage's task and each judge gate's rubric, in a run of a project whose instructions say
something, open with them, labelled as the owner's standing guidance for the project
(``step_dispatch.OWNER_GUIDANCE_BLOCK``). They are read when each step starts, so guidance saved
while a run goes reaches its next step, and they are handed over as a value
(``{{run.guidance}}``), so guidance that reads like a binding is given as written. And because
they are now every step's guidance, an agent can no longer write them: ``project_create`` names a
project and is refused its instructions, as an app already is.

Driven through a real ``RunController`` over a project kept by the real store under a scratch
home; the stage's helper and the judge's model are faked, each recording what it was handed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.workflows import step_dispatch, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun

GUIDANCE = "Reject any change that has no test."


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    return home


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _Helpers:
    """The run's stage helpers: each records its task and is done at once; *during* runs while
    the first one works."""

    def __init__(self, during=None) -> None:
        self.tasks: list[str] = []
        self.infos: dict[str, _Info] = {}
        self.during = during

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}")
        self.infos[info.id] = info
        self.tasks.append(str(kw.get("task") or ""))
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.done:
            if self.during is not None and len(self.tasks) == 1:
                self.during()
            info.done = True
            info.result = "made the change and its test"
        return info


class _Judge:
    """The judge's model: records each instruction it is handed and passes with proof."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, prompt, *, use_case="reasoning", output_type=None):
        self.prompts.append(str(prompt))
        return json.dumps({"verdict": "PASS", "proof": "read the diff; the test is there"})


def _stage(node_id: str) -> dict[str, Any]:
    return {"kind": "stage", "id": node_id, "config": {"prompt": f"Do step {node_id}."}}


JUDGE = {"kind": "gate", "id": "judged", "config": {"kind": "judge", "prompt": "Is it done?"}}


def _project(instructions: str = GUIDANCE) -> str:
    return (
        HierarchyStore().create_project(name="Billing", agent_instructions_template=instructions).id
    )


def _drive(*children: dict[str, Any], project_id: str = "", during=None):
    spec = {
        "name": "guided",
        "root": {"kind": "sequence", "id": "root", "children": list(children)},
    }
    run = store.create(WorkflowRun(id="", workflow_name="guided", project_id=project_id))
    store.write_spec(run.id, spec)
    helpers, judge = _Helpers(during), _Judge()
    controller = RunController(
        run, spec, services=EngineServices(subagents=helpers, completion=judge)
    )
    status = asyncio.run(controller.run_to_completion(timeout=20.0))
    return status, helpers, judge, run


def test_a_project_runs_worker_and_judge_are_given_its_guidance():
    """🔴 Before: neither the stage's task nor the judge's instruction held a word of it."""
    status, helpers, judge, _run = _drive(_stage("work"), JUDGE, project_id=_project())
    assert status is RunStatus.COMPLETE, status
    (task,) = helpers.tasks
    assert task.startswith("The owner's standing guidance for this project"), task[:300]
    assert GUIDANCE in task and task.endswith("Do step work."), task
    (instruction,) = judge.prompts
    assert GUIDANCE in instruction and "Is it done?" in instruction, instruction[:2000]


def test_guidance_that_reads_like_a_binding_is_handed_over_as_written():
    said = "Name a missing input as {{inputs.missing}} would, and keep {{secret:API_KEY}} as is."
    status, helpers, judge, _run = _drive(_stage("work"), JUDGE, project_id=_project(said))
    assert status is RunStatus.COMPLETE, "guidance is not read as the step's bindings"
    (task,) = helpers.tasks
    assert said in task, task
    assert said in judge.prompts[0]


def test_guidance_saved_while_a_run_goes_reaches_its_next_step():
    """The panel's "applies to future cycles": read when each step starts."""
    project_id = _project()
    newer = "Every change also says what it costs."

    def save_newer() -> None:
        HierarchyStore().update_project(project_id, agent_instructions_template=newer)

    status, helpers, _judge, _run = _drive(
        _stage("first"), _stage("second"), project_id=project_id, during=save_newer
    )
    assert status is RunStatus.COMPLETE
    first, second = helpers.tasks
    assert GUIDANCE in first and newer not in first
    assert newer in second and GUIDANCE not in second


@pytest.mark.parametrize("where", ["no project", "a project with no instructions"])
def test_a_run_with_no_guidance_is_handed_its_prompts_as_written(where):
    project_id = "" if where == "no project" else _project("   ")
    status, helpers, judge, _run = _drive(_stage("work"), JUDGE, project_id=project_id)
    assert status is RunStatus.COMPLETE
    assert helpers.tasks == ["Do step work."]
    assert "standing guidance" not in judge.prompts[0]


def test_only_a_stage_and_a_judge_gate_are_given_it():
    """An approval gate's prompt is the question she is asked; an action hands no model a task."""
    ctx = BindingContext(run_guidance=GUIDANCE)
    for kind, config in (
        ("gate", {"kind": "approval", "prompt": "Ship it?"}),
        ("action", {"provider": "notify", "prompt": "Shipped."}),
    ):
        node = Node.from_dict({"kind": kind, "id": "n", "config": config})
        assert step_dispatch._with_owner_guidance(node, ctx) is node, kind
    for kind, config in (("stage", {"prompt": "Work."}), ("gate", dict(JUDGE["config"]))):
        node = Node.from_dict({"kind": kind, "id": "n", "config": config})
        given = step_dispatch._with_owner_guidance(node, ctx)
        assert given.config["prompt"].startswith(step_dispatch.OWNER_GUIDANCE_BLOCK), kind
        assert node.config["prompt"] == config["prompt"], "the spec's own node is left as it is"


@pytest.fixture
def agent_tools(tmp_path):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.tasks import registry

    registry._providers.clear()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path / "home"),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path / "home"),
    ):
        yield NativeBuiltinToolProvider(workspace)
    registry._providers.clear()


@pytest.mark.asyncio
async def test_an_agent_cannot_write_a_projects_instructions(agent_tools):
    """🔴 Before: an agent's `project_create` stored the instructions it was given, which every
    stage and judge of the project's runs would now be handed as hers."""
    refused = await agent_tools.invoke(
        "project_create", {"name": "Billing", "agent_instructions_template": GUIDANCE}
    )
    assert not refused.success and "instructions are the owner's" in refused.error, refused
    assert not [p for p in HierarchyStore().list_projects() if p.name == "Billing"]
    made = await agent_tools.invoke("project_create", {"name": "Billing"})
    assert made.success, made
    [project] = [p for p in HierarchyStore().list_projects() if p.name == "Billing"]
    assert project.agent_instructions_template == ""
