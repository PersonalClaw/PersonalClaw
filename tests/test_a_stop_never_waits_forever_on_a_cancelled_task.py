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
  process;
* the budget: the whole stop, with its unwind and a margin, ends inside the shortest stop window
  PersonalClaw ships under, read from what ships, and no bound inside the stop is one the stop's
  own limit always cuts first.
"""

from __future__ import annotations

import asyncio
import logging
import plistlib
import re
from pathlib import Path

import pytest
import yaml
from test_gateway import _make_orchestrator

from personalclaw import cancellation, gateway
from personalclaw.cancellation import cancel_and_wait
from personalclaw.durability import export_follow
from personalclaw.durability import service as durability
from personalclaw.providers.availability import AvailabilityBoard
from personalclaw.service import linux, macos

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


# ── the budget: the whole stop ends inside the shortest window it is stopped under ────────────
#
# A supervisor gives a stop a window after SIGTERM, then sends SIGKILL, and a stop still running
# then is killed in the middle of a step: the time its bounds promised was never there. Docker's
# window is the shortest PersonalClaw ships under: `docker stop` on the image, and the compose
# files, which set no `stop_grace_period`. launchd's plist sets no `ExitTimeOut` (20 s) and the
# systemd unit sets `TimeoutStopSec=20`. Each window is read from what ships, so a compose file or
# unit that shortens one is measured here.
#
# The stop is held whole by its own limit (`gateway._SHUTDOWN_SECS`): its steps' bounds, summed,
# run past it today, and only the limit ends the stop in time. So a bound inside the stop that is
# as long as the limit hides its wait: the limit always cuts it first. Those named below are the
# stop's own and the durability close's.

#: Docker's stop window when nothing sets one (`docker stop --time`, compose's
#: `stop_grace_period`).
_DOCKER_DEFAULT_STOP_SECS = 10.0
#: launchd's `ExitTimeOut` for a job whose plist sets none.
_LAUNCHD_DEFAULT_STOP_SECS = 20.0
#: systemd's `DefaultTimeoutStopSec`, for a unit that sets no `TimeoutStopSec`.
_SYSTEMD_DEFAULT_STOP_SECS = 90.0
#: What the window keeps past the stop's limit and its unwind: the orphan reap, the exit, and the
#: supervisor's own lag.
_STOP_MARGIN_SECS = 2.0

_REPO = Path(__file__).resolve().parents[1]

#: The bounded steps inside the stop, each by the constant that bounds it.
_STEPS = {
    "the running chat turns (gateway._END_TURNS_SECS)": gateway._END_TURNS_SECS,
    "a cancelled task (cancellation.CANCEL_GRACE_SECS)": cancellation.CANCEL_GRACE_SECS,
    "the durability step (durability.CLOSE_TIMEOUT_SECS)": durability.CLOSE_TIMEOUT_SECS,
    "a re-export the follower is running (export_follow.STOP_WAIT_SECS)": (
        export_follow.STOP_WAIT_SECS
    ),
}


def _duration(text: object) -> float:
    """A compose or systemd duration in seconds: ``20s``, ``1m30s``, ``500ms`` or a number."""
    if isinstance(text, (int, float)):
        return float(text)
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "min": 60.0, "h": 3600.0}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|min|s|m|h)?", str(text).strip())
    assert parts, f"not a duration: {text!r}"
    return sum(float(number) * units[unit or "s"] for number, unit in parts)


def _shipped_windows(repo: Path) -> dict[str, float]:
    """Each stop window the gateway ships under, by what sets it."""
    windows = {"`docker stop` on the image": _DOCKER_DEFAULT_STOP_SECS}
    for path in sorted((repo / "deploy" / "compose").glob("*.yaml")):
        services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
        for name, spec in services.items():
            if "gateway" in name:
                given = (spec or {}).get("stop_grace_period")
                windows[f"{path.name}'s {name}"] = (
                    _DOCKER_DEFAULT_STOP_SECS if given is None else _duration(given)
                )
    unit = re.search(r"^TimeoutStopSec=(\S+)$", linux.render_unit(), re.MULTILINE)
    windows["the systemd unit"] = _duration(unit.group(1)) if unit else _SYSTEMD_DEFAULT_STOP_SECS
    plist = plistlib.loads(macos.render_plist().encode())
    windows["the launchd plist"] = float(plist.get("ExitTimeOut", _LAUNCHD_DEFAULT_STOP_SECS))
    return windows


def _over_budget(
    windows: dict[str, float], *, limit: float, unwind: float, steps: dict[str, float]
) -> list[str]:
    """What does not fit, each said in a sentence, or nothing."""
    problems = []
    where, shortest = min(windows.items(), key=lambda item: item[1])
    need = limit + unwind + _STOP_MARGIN_SECS
    if need > shortest:
        problems.append(
            f"the stop takes up to {limit:g} s, {unwind:g} s to unwind and a "
            f"{_STOP_MARGIN_SECS:g} s margin, {need:g} s in all, past the {shortest:g} s of {where}"
        )
    for what, bound in steps.items():
        if bound >= limit:
            problems.append(
                f"{what} waits up to {bound:g} s, and the stop's own {limit:g} s limit always cuts "
                "it first"
            )
    return problems


def test_the_whole_stop_ends_inside_the_shortest_window_it_ships_under() -> None:
    windows = _shipped_windows(_REPO)
    assert min(windows.values()) == _DOCKER_DEFAULT_STOP_SECS, windows
    assert (
        _over_budget(
            windows, limit=gateway._SHUTDOWN_SECS, unwind=gateway._UNWIND_SECS, steps=_STEPS
        )
        == []
    )


def test_a_compose_file_that_shortens_the_window_is_measured(tmp_path) -> None:
    (tmp_path / "deploy" / "compose").mkdir(parents=True)
    (tmp_path / "deploy" / "compose" / "compose.yaml").write_text(
        "services:\n  personalclaw-gateway:\n    stop_grace_period: 5s\n", encoding="utf-8"
    )
    windows = _shipped_windows(tmp_path)
    assert windows["compose.yaml's personalclaw-gateway"] == 5.0
    assert _over_budget(
        windows, limit=gateway._SHUTDOWN_SECS, unwind=gateway._UNWIND_SECS, steps=_STEPS
    ) == [
        "the stop takes up to 7 s, 1 s to unwind and a 2 s margin, 10 s in all, past the 5 s of "
        "compose.yaml's personalclaw-gateway"
    ]


def test_the_old_ten_second_limit_runs_past_docker_s_window() -> None:
    """🔴 The limit before: a stop that used all of it was killed before it exited."""
    assert _over_budget(
        _shipped_windows(_REPO), limit=10.0, unwind=gateway._UNWIND_SECS, steps=_STEPS
    ) == [
        "the stop takes up to 10 s, 1 s to unwind and a 2 s margin, 13 s in all, past the 10 s of "
        "`docker stop` on the image"
    ]


def test_a_step_bound_as_long_as_the_stop_s_limit_is_named() -> None:
    """The durability close at 30 s, as it was first written: the stop's limit would cut it."""
    steps = {**_STEPS, "the durability step (durability.CLOSE_TIMEOUT_SECS)": 30.0}
    assert _over_budget(
        _shipped_windows(_REPO),
        limit=gateway._SHUTDOWN_SECS,
        unwind=gateway._UNWIND_SECS,
        steps=steps,
    ) == [
        "the durability step (durability.CLOSE_TIMEOUT_SECS) waits up to 30 s, and the stop's own "
        "7 s limit always cuts it first"
    ]


@pytest.mark.parametrize(
    ("text", "seconds"), [("20s", 20.0), ("1m30s", 90.0), ("500ms", 0.5), ("20", 20.0), (15, 15.0)]
)
def test_a_duration_reads_as_compose_and_systemd_write_it(text, seconds) -> None:
    assert _duration(text) == seconds
