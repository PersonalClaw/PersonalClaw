"""A workflow run whose loop is over is closed at boot, never resumed.

Measured on a running gateway: a loop was stopped while its batch run (two tasks handed to
``subagent_run``) still waited on two approvals. At the next boot the workflow supervisor adopted
the run as a crash survivor and RESUMED it: it provisioned its workspace again, re-attempted both
steps' effects and posted two new approval asks — for a loop that had been stopped half an hour
before.

Boot recovery now reads the parent first. The run records the loop worker session that started it
(``origin.session_key``), so the supervisor asks whether that loop has ended — stopped, failed,
finished, or deleted — before it drives the run, and ends it through a controller saying which loop
ended it. The controller honours the cancel before it prepares anything, so nothing is provisioned,
attempted or asked. The same check runs on every poll after boot, for a loop that ends while its
run goes on.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import (
    InstanceState,
    NodeInstance,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.watchdog import WorkflowWatchdog

LOOP_NAME = "Release notes"


@pytest.fixture(autouse=True)
def _loop_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)


def _spec() -> dict[str, Any]:
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


def _loop(status: LoopStatus) -> Loop:
    loop = loop_store.create(Loop(id="", name=LOOP_NAME, kind="goal", task="verify the notes"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    if status is not LoopStatus.RUNNING:
        loop_store.update_status(loop.id, status, stop_reason=LoopStopReason.USER)
    return loop


def _crash_survivor(loop_id: str) -> WorkflowRun:
    """A batch run the previous process was driving: RUNNING, its step mid-flight."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-1",
            origin=RunOrigin(kind=OriginKind.API, session_key=f"dashboard:loop-{loop_id}"),
        )
    )
    run.status = RunStatus.RUNNING
    run.started_at = "2026-01-01T00:00:00Z"
    store.save(run)
    store.write_spec(run.id, _spec())
    path = "root.children[0]"
    store.write_state(
        run.id,
        {
            path: NodeInstance(
                path=path, state=InstanceState.RUNNING, attempt=1, subagent_id="7baf36db"
            )
        },
    )
    return run


def _watchdog() -> tuple[WorkflowWatchdog, Any]:
    """A fresh process's supervisor: its subagent manager knows none of the old process's agents,
    and must never be asked to start one."""
    subagents = SimpleNamespace(
        get=lambda _agent_id: None,
        cancel=AsyncMock(return_value=False),
        spawn=AsyncMock(side_effect=AssertionError("a stopped loop's run started a subagent")),
    )
    return WorkflowWatchdog(services=EngineServices(subagents=subagents, cwd="")), subagents


async def _boot(watchdog: WorkflowWatchdog, run_id: str) -> RunStatus:
    await watchdog._poll_once()
    controller = watchdog.controller(run_id)
    assert controller is not None, "the supervisor took no controller for the run"
    return await asyncio.wait_for(controller.run_to_completion(timeout=10.0), timeout=15)


def _kinds(run_id: str) -> list[str]:
    return [e.get("kind", "") for e in store.read_jsonl(run_id, "journal.jsonl")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "ending"),
    [
        (LoopStatus.STOPPED, f"its loop “{LOOP_NAME}” was stopped"),
        (LoopStatus.COMPLETE, f"its loop “{LOOP_NAME}” has finished"),
        (LoopStatus.FAILED, f"its loop “{LOOP_NAME}” failed"),
    ],
)
async def test_a_run_whose_loop_is_over_is_closed_not_resumed(status, ending):
    loop = _loop(status)
    run = _crash_survivor(loop.id)
    before = _kinds(run.id)
    watchdog, subagents = _watchdog()

    assert await _boot(watchdog, run.id) is RunStatus.CANCELLED

    ended = store.get(run.id)
    assert ended.status is RunStatus.CANCELLED
    assert ended.error_message == f"Stopped because {ending}."
    # Nothing was taken on again: no start, no workspace, no effect, no step, no subagent.
    new = _kinds(run.id)[len(before) :]
    assert not {"run_started", "workspace_provisioned", "effect", "step_started"} & set(new), new
    subagents.spawn.assert_not_awaited()
    (inst,) = store.read_state(run.id).values()
    assert inst.state is InstanceState.CANCELLED, "the step still reads running"


@pytest.mark.asyncio
async def test_a_run_whose_loop_was_deleted_is_closed():
    loop = _loop(LoopStatus.STOPPED)
    run = _crash_survivor(loop.id)
    loop_store.delete(loop.id)
    watchdog, _subagents = _watchdog()

    assert await _boot(watchdog, run.id) is RunStatus.CANCELLED
    assert store.get(run.id).error_message == "Stopped because its loop was deleted."


@pytest.mark.asyncio
async def test_a_run_whose_loop_has_not_ended_is_left_to_adoption():
    """The positive control: only an ENDED loop's run is closed. A running loop's run is not
    asked to stop, and nor is a paused one's: a pause is not an ending."""
    watchdog, _subagents = _watchdog()
    running = _crash_survivor(_loop(LoopStatus.RUNNING).id)
    paused_loop = _loop(LoopStatus.RUNNING)
    loop_store.update_status(paused_loop.id, LoopStatus.PAUSED)
    paused = _crash_survivor(paused_loop.id)

    assert watchdog._end_runs_whose_loop_ended() == 0
    assert not store.cancel_requested(running.id)
    assert not store.cancel_requested(paused.id)
