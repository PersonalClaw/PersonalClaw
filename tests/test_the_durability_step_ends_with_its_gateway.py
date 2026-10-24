"""A gateway's stop waits for the durability step its loop is running, and starts no other.

The durability loop hands each step (graph maintenance, the evals watchdog, the due backup jobs)
to an executor thread. The gateway's stop cancelled the loop's task, and a cancel ends the loop's
await, not the thread: the step ran on after the gateway was gone. On the breadth canary leg
(#4768) a test's gateway stopped while its loop was exporting, the test's home was undone, and
the export's audit line reached for the default home during the next test's setup.

Now the stop asks the loop to end after the step in progress and waits for that step, up to
`CLOSE_TIMEOUT_SECS`, before cancelling what is left.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

from personalclaw.durability import service


class _Step:
    """A step that runs until the test lets it end, on the thread the loop hands it to."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def __call__(self, *_args, **_kwargs) -> list:
        self.started.set()
        self.release.wait(30)
        self.finished.set()
        return []

    async def has_started(self) -> None:
        assert await asyncio.to_thread(self.started.wait, 10), "the loop never ran the step"

    async def end(self) -> None:
        """Let the step end and wait for its thread to finish it, so no test leaves one behind."""
        self.release.set()
        assert await asyncio.to_thread(self.finished.wait, 10), "the step's thread never ended"


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Every step of a tick but the backup jobs does nothing, and records that it ran."""
    ran = {
        "graph": MagicMock(return_value=None),
        "evals": MagicMock(return_value=None),
    }

    async def _footprint() -> None:
        ran["footprint"]()

    ran["footprint"] = MagicMock(return_value=None)
    monkeypatch.setattr(service, "_tick_graph_maintenance", ran["graph"])
    monkeypatch.setattr(service, "_tick_evals_watchdog", ran["evals"])
    monkeypatch.setattr(service, "_tick_footprint_maintenance", _footprint)
    monkeypatch.setattr(service, "enabled", lambda: True)
    return ran


@pytest.mark.asyncio
async def test_a_gateway_stop_waits_for_the_backup_step_it_is_running(tmp_path, monkeypatch, steps):
    """🔴 Before: the stop returned while the step's thread was still exporting."""
    from personalclaw.dashboard.server import start_dashboard

    step = _Step()
    monkeypatch.setattr(service, "run_due_jobs", step)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        await step.has_started()
        asyncio.get_running_loop().call_later(0.3, step.release.set)
        await runner.cleanup()
        assert step.finished.is_set(), "the gateway's stop returned with its step still running"
    finally:
        await step.end()


@pytest.mark.asyncio
async def test_no_further_step_starts_once_the_stop_is_asked(monkeypatch, steps):
    step = _Step()
    monkeypatch.setattr(service, "_tick_graph_maintenance", step)
    svc = service.DurabilityService(tick_secs=0.01)
    await svc.start()
    try:
        await step.has_started()
        asyncio.get_running_loop().call_later(0.1, step.release.set)
        await svc.aclose()
        assert step.finished.is_set()
        assert not steps["evals"].called, "a step started after the stop was asked"
        assert svc._task is None
    finally:
        await step.end()


@pytest.mark.asyncio
async def test_between_ticks_the_stop_ends_the_loop_at_once(steps):
    svc = service.DurabilityService(tick_secs=3600)
    await svc.start()
    for _ in range(100):
        if steps["footprint"].called:
            break
        await asyncio.sleep(0.01)
    task = svc._task
    started = time.monotonic()
    await svc.aclose()
    assert time.monotonic() - started < 1, "the stop waited out the tick"
    assert task is not None and task.done() and not task.cancelled(), "it ended, not cancelled"


@pytest.mark.asyncio
async def test_a_step_that_does_not_end_is_cancelled_after_the_bound(monkeypatch, steps, caplog):
    step = _Step()
    monkeypatch.setattr(service, "run_due_jobs", step)
    svc = service.DurabilityService(tick_secs=0.01)
    await svc.start()
    try:
        await step.has_started()
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger=service.logger.name):
            await svc.aclose(timeout=0.2)
        assert time.monotonic() - started < 5
        assert svc._task is None
        assert "still running after" in caplog.text
    finally:
        await step.end()
