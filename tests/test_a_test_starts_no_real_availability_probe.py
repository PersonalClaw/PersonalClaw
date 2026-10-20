"""No test starts the real availability probe unless it asks to.

🔴 The defect. The availability board measures a provider app by starting
``personalclaw availability-probe <app>``, a child process that imports the app and runs its
hook. A test that boots the dashboard (its startup warms the board), lists providers or finishes
an install started that child without meaning to, against its own home but running real app
code, and left it to the loop's teardown to cancel. Measured over 331 test files, 33 tests in 13
of them did, beyond the probe's own tests. On Python 3.12 a cancel that lands while the child's
pipes connect is never woken, so such a teardown can hang until the test times out.

``tests/conftest.py``'s ``_no_real_availability_probe`` now settles what a board on the real probe
command is asked with ``unknown``, and starts nothing. What this file holds:

* a board on the real probe command starts no process in a test;
* a board a test gave its own child still runs that child, so the guard is not every board's;
* a test that requests ``real_availability_probe`` gets the real command.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from personalclaw.providers import availability

APP = "probe-guard-app"
IMPL = "provider:create_provider"


class _Spawns:
    """Stands in for ``asyncio.create_subprocess_exec``: records each argv and starts nothing."""

    def __init__(self) -> None:
        self.argvs: list[tuple[str, ...]] = []

    async def __call__(self, *argv: str, **_kwargs: object) -> object:
        self.argvs.append(argv)
        raise OSError("the test started no process")


async def _measured(board: availability.AvailabilityBoard) -> availability.Availability:
    """The board's answer for the fixture app once its measurement has ended."""
    assert board.read(APP, IMPL).state == availability.CHECKING
    task = board._task
    assert task is not None, "reading an unmeasured app schedules its measurement"
    await asyncio.wait_for(task, timeout=30)
    return board.read(APP, IMPL)


@pytest.mark.asyncio
async def test_a_board_on_the_real_probe_starts_no_process(monkeypatch) -> None:
    spawns = _Spawns()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawns)
    board = availability.AvailabilityBoard()

    answer = await _measured(board)

    assert spawns.argvs == [], f"a test started the real availability probe: {spawns.argvs}"
    assert answer.state == availability.UNKNOWN
    assert "does not run in tests" in answer.reason


@pytest.mark.asyncio
async def test_a_board_given_its_own_child_still_runs_it() -> None:
    line = json.dumps(
        {"name": APP, "implementation": IMPL, "state": "unavailable", "reason": "scripted"}
    )
    board = availability.AvailabilityBoard(
        argv_for=lambda _names: [sys.executable, "-c", f"print({line!r})"]
    )

    answer = await _measured(board)

    assert (answer.state, answer.reason) == (availability.UNAVAILABLE, "scripted")


@pytest.mark.asyncio
async def test_a_test_that_asks_for_the_real_probe_gets_it(
    monkeypatch, real_availability_probe
) -> None:
    spawns = _Spawns()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawns)
    board = availability.AvailabilityBoard()

    answer = await _measured(board)

    assert [argv[-2:] for argv in spawns.argvs] == [(availability.PROBE_COMMAND, APP)]
    assert answer.state == availability.UNKNOWN
    assert "could not start" in answer.reason
