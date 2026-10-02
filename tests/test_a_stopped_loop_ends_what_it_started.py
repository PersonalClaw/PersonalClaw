"""Stopping a loop ends everything it started, and nothing it started can be allowed afterwards.

Measured on a running gateway: a loop's cycle handed two tasks to ``subagent_run``, which ran them
as one batch workflow run, each task's subagent waiting on its owner's approval. She stopped the
loop. The loop read "Stopped", yet six minutes later the batch's two steps still read "running"
with their leases renewing, and the Inbox still offered both approvals with nothing saying the loop
was over — so an Allow would have started agents for a loop she had stopped.

The link is exact: the batch run records the session that started it (``origin.session_key``, the
loop worker's ``dashboard:loop-<id>``), and a background subagent records its parent session the
same way. So the Stop ends each child, recorded as its loop's stop: the run is cancelled saying so,
its stage's subagent is stopped, and the approval it was waiting on expires naming the loop — on
every surface, and at the door that would answer it.

These drive the REAL loop Stop (`loop.manager.stop`) over a real ``RunController`` and a real
``SubagentManager`` whose spawn approval goes through the real ``DashboardState.request_approval``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    _app,
    _open_rows,
    _real_manager,
    _resolved,
    _until,
)

from personalclaw.loop import manager as loop_manager
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

LOOP_NAME = "Release notes"


@pytest.fixture
def loop_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as native

    monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _NudgeService:
    """The nudge service's public surface, holding no worker rows: the Stop's teardown asks it."""

    def get_by_session(self, _session_name: str) -> None:
        return None

    def list_all(self) -> list[Any]:
        return []

    async def remove(self, _row_id: str) -> None:
        return None


def _running_loop() -> Loop:
    loop = loop_store.create(Loop(id="", name=LOOP_NAME, kind="goal", task="verify the notes"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    return loop


def _batch_spec() -> dict[str, Any]:
    """A compiled batch's shape: a quorum parallel of labelled stages."""
    return {
        "name": "subagent-batch-1",
        "root": {
            "kind": "parallel",
            "id": "batch",
            "config": {"join": "quorum", "quorum": 1},
            "children": [
                {
                    "kind": "stage",
                    "id": "audit_the_notes_0",
                    "label": "Audit the notes",
                    "config": {"prompt": "audit the notes"},
                }
            ],
        },
    }


def _child_run(session_key: str) -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-1",
            origin=RunOrigin(kind=OriginKind.API, session_key=session_key),
        )
    )
    store.write_spec(run.id, _batch_spec())
    return run


@pytest.fixture
def stopped_world(world, loop_home, monkeypatch):  # noqa: F811 - the imported fixture
    """A running loop whose worker started a batch run that waits on a spawn approval."""
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    manager = _real_manager(world.state)
    loop = _running_loop()
    run = _child_run(f"dashboard:loop-{loop.id}")
    controller = RunController(
        run,
        _batch_spec(),
        services=EngineServices(subagents=manager, cwd="", attention_state=world.state),
    )
    controllers = {run.id: controller}
    world.state.workflows = SimpleNamespace(controller=controllers.get)
    world.manager, world.loop, world.run, world.controller = manager, loop, run, controller
    world.controllers = controllers
    return world


async def _until_spawn_waits(w) -> str:
    await _until(
        lambda: any(a.startswith("spawn:") for a in w.state._pending_approvals),
        "the step's spawn never asked for approval",
    )
    (approval_id,) = [a for a in w.state._pending_approvals if a.startswith("spawn:")]
    return approval_id


def _row(w, approval_id: str):
    (row,) = [i for i in w.inbox.items.values() if i.refs.get("approval") == approval_id]
    return row


@pytest.mark.asyncio
async def test_stopping_the_loop_ends_its_batch_run_and_its_waiting_ask(stopped_world):
    w = stopped_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)
    # Premises: the ask is the batch step's, and it is on the Inbox.
    assert w.state._pending_approvals[approval_id]["session"] == (
        f"workflow:{w.run.id}:audit_the_notes_0"
    )
    assert _open_rows(w, approval_id), "the approval never reached the Inbox"

    await loop_manager.stop(w.state, _NudgeService(), w.loop.id)
    status = await asyncio.wait_for(driving, timeout=10)

    # The run ended, recorded as its loop's stop.
    assert status is RunStatus.CANCELLED
    run = store.get(w.run.id)
    assert run.status is RunStatus.CANCELLED
    assert run.error_message == f"Stopped because its loop “{LOOP_NAME}” was stopped."
    (inst,) = store.read_state(w.run.id).values()
    assert inst.state is InstanceState.CANCELLED, "the step still reads running"
    info = w.manager.get(approval_id.removeprefix("spawn:"))
    assert info.cancelled and info.done
    assert info.error == f"Cancelled because its loop “{LOOP_NAME}” was stopped", info.error

    # The ask expired, saying the loop was stopped, on every surface.
    assert approval_id not in w.state._pending_approvals, "Home and To triage still list it"
    (frame,) = _resolved(w, approval_id)
    assert frame["outcome"] == "cancelled" and frame["approved"] is False, frame
    assert frame["ended"] == "the loop that started its run was stopped", frame
    row = _row(w, approval_id)
    assert row.status == "expired", row.status
    assert row.refs["ended"] == "the loop that started its run was stopped"


@pytest.mark.asyncio
async def test_an_allow_after_the_loop_is_stopped_starts_nothing(stopped_world):
    w = stopped_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)

    await loop_manager.stop(w.state, _NudgeService(), w.loop.id)
    await asyncio.wait_for(driving, timeout=10)

    async with TestClient(TestServer(_app(w.state))) as http:
        resp = await http.post(f"/api/approvals/{approval_id}/approve")
    assert resp.status in (404, 409), await resp.text()
    await asyncio.sleep(0.05)
    w.manager._sessions.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_door_refuses_an_allow_before_the_run_has_applied_the_stop(stopped_world):
    """Between the Stop and the run's next step its ask is still listed: an Allow then is
    refused, naming the loop."""
    w = stopped_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)
    loop_store.update_status(w.loop.id, LoopStatus.STOPPED)

    async with TestClient(TestServer(_app(w.state))) as http:
        resp = await http.post(f"/api/approvals/{approval_id}/approve")
        body = await resp.json()
    assert resp.status == 409, body
    assert body["error"]["message"] == (
        "Nothing was run: the loop that started its run was stopped."
    ), body
    w.manager._sessions.get_or_create.assert_not_awaited()
    store.request_cancel(w.run.id)
    await asyncio.wait_for(driving, timeout=10)


@pytest.mark.asyncio
async def test_a_subagent_the_loop_started_is_stopped_with_it(world, loop_home):  # noqa: F811
    """A single task runs as a background subagent, listed under the loop's worker session. The
    Stop ends it even when no worker turn is running at that moment."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.subagent import SubagentManager

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=RuntimeError("no provider in this test"))
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("check the dates", None))
    ctx.hooks.auto_approve_subagent_spawn = False
    holder: dict[str, Any] = {}

    async def _spawn_approve(event: Any, parent_key: str = "") -> bool:
        # As the gateway lists it: under the parent session's bare name.
        info = holder["manager"].get(str(event.request_id).removeprefix("spawn:"))
        session = info.parent_session_key.removeprefix("dashboard:") if info else ""
        return await world.state.request_approval(
            str(event.request_id), "subagent", event.title, session=session
        )

    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, on_spawn_approval=_spawn_approve, is_yolo=lambda: False
    )
    holder["manager"] = manager
    world.state.subagents = manager
    loop = _running_loop()
    info = manager.spawn("check the dates", parent_session_key=f"dashboard:loop-{loop.id}")
    approval_id = f"spawn:{info.id}"
    await _until(lambda: approval_id in world.state._pending_approvals, "never asked")

    await loop_manager.stop(world.state, _NudgeService(), loop.id)
    await _until(lambda: approval_id not in world.state._pending_approvals, "still listed")

    assert info.cancelled and info.done
    assert info.error == f"Cancelled because its loop “{LOOP_NAME}” was stopped", info.error
    (frame,) = _resolved(world, approval_id)
    assert frame["ended"] == "the loop that asked for it was stopped", frame
    assert _row(world, approval_id).status == "expired"


@pytest.mark.asyncio
async def test_work_another_chat_started_is_left_alone(stopped_world):
    """The positive control: the Stop ends what ITS loop started, by the link, and nothing else."""
    w = stopped_world
    w.state.workflows = SimpleNamespace(controller=lambda _run_id: None)
    for run in (w.run, other := _child_run("dashboard:chat-a")):
        run.status = RunStatus.RUNNING
        store.save(run)

    await loop_manager.stop(w.state, _NudgeService(), w.loop.id)

    assert not store.cancel_requested(other.id)
    assert store.get(other.id).status is RunStatus.RUNNING
    assert store.cancel_requested(w.run.id)


@pytest.mark.asyncio
async def test_a_loop_that_fails_ends_its_batch_run_too(stopped_world):
    """A failed loop keeps its task workers' worktrees for a Resume, but a batch run is no worker's:
    nothing would read what it found, so it ends with the failure, and its ask with it."""
    w = stopped_world
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _until_spawn_waits(w)
    loop_store.update_status(w.loop.id, LoopStatus.FAILED)

    await loop_manager.stand_down(w.state, _NudgeService(), w.loop.id)

    assert await asyncio.wait_for(driving, timeout=10) is RunStatus.CANCELLED
    assert store.get(w.run.id).error_message == f"Stopped because its loop “{LOOP_NAME}” failed."
    (frame,) = _resolved(w, approval_id)
    assert frame["ended"] == "the loop that started its run failed", frame
