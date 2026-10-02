"""A knowledge step's budget counts its own work, and a stopped step leaves nothing running.

The budget was a flat ``asyncio.wait_for`` over wall time, so it counted time the step could not
use: while another step's engine held the interpreter lock, the event loop could not run, and a
frame extraction whose ffmpeg finished in 13 seconds was failed at the 2-minute mark as "did not
finish within 2 minutes". A step stopped for its budget left its ffmpeg running, logged nothing,
and its item offered no way to run it again.

The frozen-process stand-in is libc's ``usleep`` through ``ctypes.PyDLL``, which keeps the
interpreter lock for the whole call (``ctypes.CDLL`` would let it go).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import shutil
import time
from pathlib import Path

import pytest

from personalclaw import cancellation
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.graph import NodeSpec, PipelineGraph
from personalclaw.knowledge.pipeline.nodes.media_nodes import _run_cmd
from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

#: How long the stand-in freezes the process.
_FREEZE = 1.5


def _freeze(seconds: float) -> None:
    ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


def _graph(*specs: NodeSpec) -> PipelineGraph:
    g = PipelineGraph(item_type="t")
    for spec in specs:
        g.add(spec)
    g.validate()
    return g


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone_within(pid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not _alive(pid)


class _Freezes:
    """A step whose engine holds the interpreter lock in a worker thread, after the steps beside
    it have started."""

    node_type = "freezes_the_process"
    backend = "stub"
    uses_use_case = None

    async def run(self, inputs, ctx):
        await asyncio.sleep(0.1)
        await asyncio.to_thread(_freeze, _FREEZE)
        return NodeOutput(node_type=self.node_type, backend=self.backend, pooled=False)


class _Quick:
    """A step that needs 0.2 s of its own."""

    node_type = "quick_step"
    backend = "stub"
    uses_use_case = None

    async def run(self, inputs, ctx):
        await asyncio.sleep(0.2)
        return NodeOutput(node_type=self.node_type, backend=self.backend, text="done in time")


class _Lingers:
    """A step whose program runs far past its budget, telling the test its pid first."""

    node_type = "lingers"
    backend = "stub"
    uses_use_case = None

    def __init__(self, pidfile: Path) -> None:
        self.pidfile = pidfile

    async def run(self, inputs, ctx):
        sh, sleep = shutil.which("sh"), shutil.which("sleep")
        assert sh and sleep
        await _run_cmd(
            [
                sh,
                "-c",
                f"echo $$ > {self.pidfile}.part; mv {self.pidfile}.part "
                f"{self.pidfile}; exec {sleep} 30",
            ]
        )
        return NodeOutput(node_type=self.node_type, backend=self.backend, text="never")


@pytest.mark.asyncio
async def test_a_step_that_finished_while_the_process_was_frozen_is_not_out_of_time():
    """🔴 Red before: the quick step's 1-second budget expired while the process was frozen, and
    it was failed as "did not finish within 1 seconds" though its work took 0.2 s."""
    register_node(_Freezes())
    register_node(_Quick())
    g = _graph(
        NodeSpec("freezes_the_process", backend="stub", timeout_s=30.0),
        NodeSpec("quick_step", backend="stub", timeout_s=1.0),
    )

    began = time.monotonic()
    res = await PipelineExecutor(g).run(NodeContext(item_id="item-1", item_type="t"))
    took = time.monotonic() - began

    assert took >= _FREEZE, f"control: the process was not frozen ({took:.2f}s)"
    assert "quick_step" in res.ran, res.outcomes.get("quick_step")
    assert res.failed == []


@pytest.mark.asyncio
async def test_a_step_out_of_its_own_time_is_stopped_its_program_killed_and_it_says_so(
    tmp_path, caplog
):
    """🔴 Red before: the step's ffmpeg-like program ran on after the step was failed, nothing
    was logged, and the outcome offered no way to run it again."""
    pidfile = tmp_path / "pid"
    register_node(_Lingers(pidfile))
    g = _graph(NodeSpec("lingers", backend="stub", timeout_s=2.0))

    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.pipeline.executor"):
        res = await PipelineExecutor(g).run(NodeContext(item_id="item-2", item_type="t"))

    assert res.failed == ["lingers"]
    outcome = res.outcomes["lingers"]
    assert outcome.reason == "It did not finish within 2 seconds, so it was stopped."
    assert outcome.retry is True and outcome.to_dict()["retry"] is True
    assert pidfile.exists(), "the program never started"
    assert await _gone_within(int(pidfile.read_text()), 3.0), "the step's program ran on"
    lines = [r.getMessage() for r in caplog.records if r.name.endswith("pipeline.executor")]
    assert len(lines) == 1, lines
    assert "lingers" in lines[0] and "item-2" in lines[0] and "2 seconds" in lines[0]


@pytest.mark.asyncio
async def test_a_cancelled_run_kills_its_steps_program_and_says_which_step(tmp_path, caplog):
    """A run cancelled from outside (the gateway stopping) takes the step's program with it."""
    pidfile = tmp_path / "pid"
    register_node(_Lingers(pidfile))
    g = _graph(NodeSpec("lingers", backend="stub", timeout_s=60.0))

    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.pipeline.executor"):
        run = asyncio.ensure_future(
            PipelineExecutor(g).run(NodeContext(item_id="item-3", item_type="t"))
        )
        end = time.monotonic() + 20
        while not pidfile.exists():
            assert time.monotonic() < end and not run.done()
            await asyncio.sleep(0.05)
        pid = int(pidfile.read_text())
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    assert await _gone_within(pid, 3.0), "the step's program ran on after the run was cancelled"
    lines = [r.getMessage() for r in caplog.records if r.name.endswith("pipeline.executor")]
    assert len(lines) == 1 and "lingers" in lines[0] and "item-3" in lines[0], lines


@pytest.mark.asyncio
async def test_a_steps_own_timeout_is_its_failure_not_its_budget_running_out():
    """🔴 Red before: a step whose own call timed out (a model that did not answer) was reported
    as having run out of its 2-minute budget, which it had not."""

    class _OwnTimeout:
        node_type = "own_timeout"
        backend = "stub"
        uses_use_case = None

        async def run(self, inputs, ctx):
            raise asyncio.TimeoutError("the model did not answer within 30 seconds")

    register_node(_OwnTimeout())
    g = _graph(NodeSpec("own_timeout", backend="stub", timeout_s=120.0))

    res = await PipelineExecutor(g).run(NodeContext(item_id="item-4", item_type="t"))

    assert res.failed == ["own_timeout"]
    assert res.outcomes["own_timeout"].reason == "The model did not answer within 30 seconds."
    assert res.outcomes["own_timeout"].retry is False


@pytest.mark.asyncio
async def test_a_bounded_wait_does_not_count_time_the_loop_could_not_run():
    """The one bounded wait (``cancellation.wait_for_unpaused``) counts only time the event loop
    could run: work needing 0.3 s, frozen out for 1.5 s, still finishes inside a 0.5 s bound.
    🔴 Red before: the frozen 1.5 s was charged to the work."""

    async def _work():
        await asyncio.sleep(0.3)
        return "finished"

    async def _freeze_soon():
        await asyncio.sleep(0.05)
        await asyncio.to_thread(_freeze, _FREEZE)

    freezer = asyncio.ensure_future(_freeze_soon())
    got = await cancellation.wait_for_unpaused(_work(), 0.5, what="test work")
    await freezer
    assert got == "finished"


@pytest.mark.asyncio
async def test_a_bounded_wait_still_stops_work_that_runs_past_it():
    """The control for the one above: work that truly runs long is still stopped, and the error
    says the bound ran out (a ``TimeoutError``, so a caller catching that still catches it)."""
    stopped = asyncio.Event()

    async def _work():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.set()
            raise

    with pytest.raises(cancellation.OutOfTime):
        await cancellation.wait_for_unpaused(_work(), 0.3, what="test work")
    assert stopped.is_set()
    assert issubclass(cancellation.OutOfTime, asyncio.TimeoutError)
