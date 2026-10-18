"""A stop that cancels a task waits for it a bounded time, names what did not finish, and goes on.

🔴 The defect. Every stop that cancelled a task then awaited it (``task.cancel(); await task``, or
a ``gather`` of the cancelled tasks) waited for the task to LEAVE, and nothing bounded how long
that took. A task can outlive its cancel in ways it cannot help. On Python 3.12, a cancel that
lands while asyncio starts a child process and connects its pipes, together with asyncio's own
task that connects them, leaves the start waiting for a wake-up that never comes (3.13 fixed it).
A task whose cleanup waits for its child's pipes waits for as long as a grandchild that inherited
them runs. And a task can catch the cancel. The stop then never finished: the availability
board's, a subagent's, a session's, the gateway's own.

Now each of those stops goes through :func:`personalclaw.cancellation.cancel_and_wait`: cancel,
wait at most a grace period, log what did not finish, carry on.

What this file holds:

* the helper itself, against a task that never leaves after a cancel, one that leaves at once,
  and a caller that is itself cancelled while it waits;
* two real stops, the availability board's and the gateway's, against a start that never leaves
  after a cancel, which is how Python 3.12 leaves one. At ``origin/main`` both hang;
* a tree-wide rail: no function in ``src/personalclaw`` waits without a bound on a task it
  cancelled, except the few named below, each with the reason its task cannot be starting a
  process.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from test_gateway import _make_orchestrator

from personalclaw import cancellation
from personalclaw.cancellation import cancel_and_wait
from personalclaw.providers.availability import AvailabilityBoard

#: Long enough that a stop meeting its bound is plainly not the stop that never returns.
_WITHIN = 5.0


class _StartThatNeverLeaves:
    """Stands in for starting a child process: once cancelled, the start does not leave until the
    test lets it. Python 3.12 does this to a start cancelled while it connects the child's pipes,
    together with asyncio's own task that connects them: it waits for a wake-up that never comes.
    """

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancels = 0
        self._released = asyncio.Event()

    def release(self) -> None:
        self._released.set()

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        self.entered.set()
        while not self._released.is_set():
            try:
                await self._released.wait()
            except asyncio.CancelledError:
                self.cancels += 1  # does not leave on a cancel
        raise OSError("the start was let go by the test")


@pytest.fixture
def short_grace(monkeypatch) -> float:
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 0.3)
    return 0.3


async def _finished(future: asyncio.Future, within: float = _WITHIN) -> bool:
    """Whether *future* finished within *within* seconds, without cancelling it."""
    done, _ = await asyncio.wait({future}, timeout=within)
    return future in done


async def _let_go(start: _StartThatNeverLeaves, *futures: asyncio.Future) -> None:
    """Release the stub and let what waited on it end, so nothing outlives the test."""
    start.release()
    live = {f for f in futures if f is not None}
    if live:
        await asyncio.wait(live, timeout=_WITHIN)
    for future in live:
        if future.done() and not future.cancelled():
            future.exception()


# ── the helper ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_task_that_never_leaves_is_named_and_left_behind(short_grace, caplog):
    start = _StartThatNeverLeaves()
    task = asyncio.ensure_future(start())
    await start.entered.wait()
    waiting = asyncio.ensure_future(cancel_and_wait([task], what="the fixture stop"))
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(waiting), "the wait on a task that never leaves never ended"
        assert waiting.result() == {task}
        assert start.cancels == 1 and not task.done(), "it was cancelled once, and left running"
        assert "the fixture stop" in caplog.text and "_StartThatNeverLeaves" in caplog.text
    finally:
        await _let_go(start, task, waiting)


@pytest.mark.asyncio
async def test_a_task_that_leaves_on_its_cancel_is_waited_for(short_grace, caplog):
    task = asyncio.ensure_future(asyncio.sleep(60))
    with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
        assert await cancel_and_wait([task, None], what="the fixture stop") == set()
    assert task.cancelled()
    assert "the fixture stop" not in caplog.text


@pytest.mark.asyncio
async def test_a_task_already_done_is_not_cancelled_and_its_error_is_taken(short_grace, caplog):
    async def fails() -> None:
        raise ValueError("the fixture task failed")

    failed = asyncio.ensure_future(fails())
    await asyncio.wait({failed})
    with caplog.at_level(logging.DEBUG, logger="personalclaw.cancellation"):
        assert await cancel_and_wait([failed], what="the fixture stop") == set()
    assert not failed.cancelled()
    assert "the fixture task failed" in caplog.text


@pytest.mark.asyncio
async def test_the_caller_being_cancelled_is_not_swallowed(monkeypatch):
    """The gateway's stop runs under its own deadline, and that deadline is a cancel of the stop.
    A wait that swallowed it would let the rest of the stop run with no deadline at all."""
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 60.0)
    start = _StartThatNeverLeaves()
    task = asyncio.ensure_future(start())
    await start.entered.wait()
    stop = asyncio.ensure_future(cancel_and_wait([task], what="the fixture stop"))
    try:
        await asyncio.sleep(0.05)
        stop.cancel()
        assert await _finished(stop, within=2.0), "the caller's cancel was held by the wait"
        assert stop.cancelled()
    finally:
        await _let_go(start, task, stop)


# ── the real stops ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_availability_board_stop_is_not_held_by_a_start_that_never_leaves(
    monkeypatch, short_grace, caplog
):
    """The gateway stop's hook (``dashboard/lifecycle_hooks.py``): the board's drain is starting the
    availability child when the stop cancels it. At ``origin/main`` the stop never returns."""
    start = _StartThatNeverLeaves()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    board = AvailabilityBoard(argv_for=lambda names: ["/nonexistent/pc-fixture-probe", *names])
    board.recheck("fixture-app")
    drain = board._task
    await asyncio.wait_for(start.entered.wait(), timeout=_WITHIN)
    stop = asyncio.ensure_future(board.shutdown())
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(stop), "the stop waited on a drain that never left"
        assert start.cancels == 1
        assert "provider availability check" in caplog.text
        assert "AvailabilityBoard._drain" in caplog.text
    finally:
        await _let_go(start, drain, stop)


@pytest.mark.parametrize("where", ["an in-flight channel message", "a background service"])
@pytest.mark.asyncio
async def test_the_gateway_stop_is_not_held_by_a_task_that_never_leaves(short_grace, caplog, where):
    """The gateway's own stop, with every service it would stop absent but the one stuck task.
    At ``origin/main`` it never returns."""
    orch = _make_orchestrator()
    orch.heartbeat_svc = None
    orch.inbox_svc = None
    orch.subagent_mgr = None
    orch.sessions = None
    orch.dashboard_state = None
    orch._dashboard_runner = None
    start = _StartThatNeverLeaves()
    stuck = asyncio.ensure_future(start())
    if where == "an in-flight channel message":
        orch._handler_tasks.add(stuck)
    else:
        orch._clock_task = stuck
    await start.entered.wait()
    stop = asyncio.ensure_future(orch._shutdown())
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(stop), f"the gateway stop waited on {where} that never left"
        stop.result()
        assert start.cancels == 1
        assert "_StartThatNeverLeaves" in caplog.text
    finally:
        await _let_go(start, stuck, stop)


# ── the rail: no unbounded wait on a task the same function cancelled ─────────────────────────
