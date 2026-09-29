"""A workflow run a restart suspended waits for Resume.

🔴 The watchdog's boot sweep suspends a run a killed gateway left running when its isolated
workspace survived: ``paused``, with a Resume that takes it on from there. Only the adoption on
that same first poll skipped it, though. The sweep wrote no pause intent, and the adoption loop
takes on every ``paused`` run without one, so the next poll — seconds later — adopted each
suspended run and resumed it unasked. The sweep now gives it the sticky intent every poll honours,
and Resume, which clears it (``service.resume_run``), is what takes it on.
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


def test_a_suspended_run_waits_for_resume(tmp_path, watchdog):
    run_id = _left_running(tmp_path, workspace_survived=True)

    assert watchdog.poll() == [], "the sweep's own poll adopted what it suspended"
    assert store.get(run_id).status == RunStatus.PAUSED
    assert watchdog.poll() == [], "the next poll resumed the suspended run unasked"
    assert store.pause_requested(run_id), "suspended with no pause intent to hold it"

    store.clear_pause(run_id)  # what Resume does (`service.resume_run`)
    assert watchdog.poll() == [run_id], "Resume took nothing on — the holds above are vacuous"


def test_the_control_a_run_whose_workspace_is_gone_is_cancelled_with_no_intent(tmp_path, watchdog):
    run_id = _left_running(tmp_path, workspace_survived=False)
    assert watchdog.poll() == []
    assert store.get(run_id).status == RunStatus.CANCELLED
    assert not store.pause_requested(run_id), "the intent is for a run that waits, only"
