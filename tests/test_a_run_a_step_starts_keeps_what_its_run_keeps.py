"""A run a workflow step starts is its run's work, and keeps what its run keeps.

A ``run-workflow`` step starts a run of another workflow and leaves it running. That run recorded
nothing of the run that started it and inherited none of its mode, unlike the child a subworkflow
node starts: so a run an Incognito or Temporary chat started could start an ordinary run, on any
model, that kept everything it was given and outlived the chat. Now the run a step starts records
its parent, the tree it belongs to and the step, and keeps the parent's mode and the model its work
stays on; an ordinary run's step starts an ordinary run, as before. Its ending is still said in
the Inbox when it fails, since the step that started it said only that it launched it.

Driven as the run's controller dispatches the step (``engine.dispatch_action``), with the real
provider and the supervisor's launch recorded rather than run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.workflows import attention
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import ownership, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

pytestmark = pytest.mark.anyio

#: The workflow a step starts, and the model a private chat's turn runs on (invented names).
CHILD = "tidy-guest-list"
CHAT_MODEL = "local:scripted-chat-model"
STEP = "start-tidy"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))


class _ChildDefs(defs_mod.WorkflowDefProvider):
    """The one workflow a step here starts."""

    spec = {"name": CHILD, "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}}}

    @property
    def name(self) -> str:
        return "step-run-child"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [self.spec], 1

    async def get_def(self, name: str):
        return self.spec if name == CHILD else None


@pytest.fixture
def launched(monkeypatch) -> list[WorkflowRun]:
    """The runs the supervisor was asked to launch: every run a step started."""
    runs: list[WorkflowRun] = []

    async def _launch(run: WorkflowRun, spec: dict[str, Any]) -> None:
        runs.append(run)

    defs_mod.register_provider(_ChildDefs())
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=SimpleNamespace(launch=_launch)),
    )
    try:
        yield runs
    finally:
        defs_mod.unregister_provider("step-run-child")


def _run_started_by(mode: str, *, chat: str) -> WorkflowRun:
    """A run a chat started, as its record keeps the mode it inherited and its chat's model."""
    keeps = ownership.MemoryMode(mode)
    extra = (
        {}
        if keeps is ownership.MemoryMode.NORMAL
        else (ownership.stamp_run_mode({}, keeps, model=CHAT_MODEL))
    )
    return store.create(
        WorkflowRun(
            id="",
            workflow_name="plan-the-party",
            status=RunStatus.RUNNING,
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=chat),
            project_id="p-lakehouse",
            extra=extra,
        )
    )


async def _its_step_starts(run: WorkflowRun) -> Any:
    """The run's ``run-workflow`` step, dispatched the way its controller dispatches it."""
    step = {
        "kind": "action",
        "id": STEP,
        "config": {"provider": "run-workflow", "with": {"workflow": CHILD}},
    }
    return await dispatch_action(
        Node.from_dict(step),
        BindingContext(),
        get_provider=lambda name: RunWorkflowActionProvider(),
        run_id=run.id,
        project_id=run.project_id,
        instance_path="root.children[0]",
    )


@pytest.mark.parametrize("mode", ["temporary", "incognito"])
async def test_a_private_chats_run_starts_only_a_run_that_keeps_nothing(launched, mode) -> None:
    """🔴 Before: the run the step started kept everything, on any model, and named no parent."""
    parent = _run_started_by(mode, chat=f"dashboard:chat-steps-{mode}")

    result = await _its_step_starts(parent)

    assert result.state == InstanceState.DEGRADED, result.failure
    [child] = launched
    assert ownership.run_mode(child) is ownership.MemoryMode(mode)
    assert ownership.run_model(child) == CHAT_MODEL
    assert (child.parent_run_id, child.root_run_id, child.spawned_by_node_id) == (
        parent.id,
        parent.id,
        STEP,
    )
    assert child.project_id == parent.project_id
    assert ownership.run_mode(store.get(child.id)) is ownership.MemoryMode(mode)


async def test_an_ordinary_run_starts_an_ordinary_run_and_names_its_parent(launched) -> None:
    """CONTROL: a run of yours starts a run that keeps what it does, now recorded as its child."""
    parent = _run_started_by("normal", chat="dashboard:chat-steps-ordinary")

    result = await _its_step_starts(parent)

    assert result.state == InstanceState.DEGRADED, result.failure
    [child] = launched
    assert ownership.run_mode(child) is ownership.MemoryMode.NORMAL
    assert child.parent_run_id == parent.id and child.spawned_by_node_id == STEP


async def test_a_step_whose_run_cannot_be_read_starts_nothing(launched) -> None:
    """What the step's run keeps cannot be said, so nothing is started rather than a run that
    might keep what it may not."""
    result = await _dispatch_for_a_missing_run()

    assert result.state == InstanceState.FAILED
    assert "the run this step belongs to could not be read" in (result.failure.cause_plain or "")
    assert launched == []
    assert store.list_runs(workflow_name=CHILD)[1] == 0


async def _dispatch_for_a_missing_run() -> Any:
    missing = WorkflowRun(id="run-gone", workflow_name="plan-the-party")
    return await _its_step_starts(missing)


def test_a_failed_run_a_step_left_running_still_says_so_in_the_inbox(monkeypatch) -> None:
    """The step that started it said only that it launched it, so its own ending is said; a
    subworkflow's child, whose node waits on it and says how it went, says nothing of its own."""
    raised: list[str] = []
    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item", lambda *a, **kw: raised.append(kw.get("title"))
    )
    launched_by_a_step = SimpleNamespace(
        id="r-left",
        title="",
        workflow_name=CHILD,
        origin=RunOrigin(kind=OriginKind.HOOK),
        parent_run_id="r-parent",
        spawned_by_node_id=STEP,
        attention=None,
    )
    waited_on = SimpleNamespace(**{**vars(launched_by_a_step), "id": "r-sub"})
    waited_on.spawned_by_node_id = None

    attention._announce_workflow_end(None, launched_by_a_step, RunStatus.FAILED)
    attention._announce_workflow_end(None, waited_on, RunStatus.FAILED)

    assert raised == ["Workflow run failed"]
