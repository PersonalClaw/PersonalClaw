"""A workflow run a restart suspended is taken on again; a run that must wait for its owner waits.

🔴 A gateway's death leaves its isolated runs ``running`` with no controller. The watchdog's boot
sweep suspends each one whose workspace or durable worker outlived the gateway, and the next
poll's adoption takes it on again — the durable-spawn promise (``tests/test_durable_spawn.py``).
A change gave those suspensions the sticky pause intent, reading a suspension as an owner's pause,
so every run a restart caught stayed paused until someone pressed Resume, and a run whose worker
was still running was never taken back.

The intent stays with what must wait for its owner: a pause the owner asked for, and a run a
restore brought back working (``store.hold_restored``). A gone workspace is still cancelled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

SPEC = {"name": "suspend", "version": 1, "root": {"kind": "sequence", "id": "main", "children": []}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # No durable worker probe: the workspace on disk is the whole substrate.
    from personalclaw.agents import runner_lifecycle

    monkeypatch.setattr(runner_lifecycle, "durable_sessions_enabled", lambda: False)
    return home


def _left_running(tmp_path: Path, *, workspace_survived: bool) -> str:
    """A run a killed gateway left ``running``, isolated in a workspace of its own."""
    workspace = tmp_path / "worktree"
    if workspace_survived:
        workspace.mkdir()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="suspend",
            status=RunStatus.RUNNING,
            extra={"worktree_path": str(workspace)},
        )
    )
    store.write_spec(run.id, SPEC)
    store.write_state(run.id, {})
    return run.id


class _Watchdog:
    """One watchdog, polled as the gateway polls it, on one event loop; what each poll adopted,
    nothing launched."""

    def __init__(self, monkeypatch, loop: asyncio.AbstractEventLoop) -> None:
        self.wd = WorkflowWatchdog()
        self.loop = loop
        self.adopted: list[str] = []

        async def adopt(run) -> None:
            self.adopted.append(run.id)

        monkeypatch.setattr(self.wd, "_adopt", adopt)

    def poll(self) -> list[str]:
        self.adopted = []
        self.loop.run_until_complete(self.wd._poll_once())
        return self.adopted


@pytest.fixture
def watchdog(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        yield _Watchdog(monkeypatch, loop)
    finally:
        loop.close()


def test_a_run_a_restart_suspended_is_taken_on_by_the_next_poll(tmp_path, watchdog):
    run_id = _left_running(tmp_path, workspace_survived=True)

    assert watchdog.poll() == [], "the sweep's own poll took on what it just suspended"
    assert store.get(run_id).status == RunStatus.PAUSED
    assert not store.pause_requested(run_id), "a restart is no owner's pause"
    assert watchdog.poll() == [run_id], "the run a restart caught was never taken on again"


def test_a_run_a_restore_held_waits_for_its_owner(tmp_path, home, watchdog):
    run_id = _left_running(tmp_path, workspace_survived=True)
    assert store.hold_restored(home) == [run_id]

    assert watchdog.poll() == [] and watchdog.poll() == [], "a held run was taken on unasked"
    assert store.get(run_id).status == RunStatus.PAUSED and store.pause_requested(run_id)
    store.clear_pause(run_id)  # what Resume does (`service.resume_run`)
    assert watchdog.poll() == [run_id], "Resume took nothing on — the holds above are vacuous"


def test_the_control_a_run_whose_workspace_is_gone_is_cancelled(tmp_path, watchdog):
    run_id = _left_running(tmp_path, workspace_survived=False)
    assert watchdog.poll() == [] and watchdog.poll() == []
    assert store.get(run_id).status == RunStatus.CANCELLED
    assert not store.pause_requested(run_id)
