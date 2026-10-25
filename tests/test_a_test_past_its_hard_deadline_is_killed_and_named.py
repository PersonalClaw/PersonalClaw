"""A test still running past its hard deadline is killed, and named with its stacks
(tests/hard_deadline.py).

Each plant is a pytest run of its own, under xdist as CI runs the suite, with the deadline's grace
at 1 s so it comes in seconds: twice the test's timeout, and 1 s.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import hard_deadline
import pytest

TESTS = Path(__file__).resolve().parent

_CONFTEST = """
import hard_deadline


def pytest_addoption(parser):
    hard_deadline.addoption(parser)


def pytest_configure(config):
    hard_deadline.configure(config)
"""


def _run(tmp_path: Path, tests: str, *args: str) -> tuple[subprocess.CompletedProcess, str, float]:
    """The planted *tests* run with the suite's registration, under xdist, with a JUnit report;
    what it printed, its report, and how long it took."""
    root = tmp_path / "planted"
    root.mkdir()
    (root / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (root / "test_planted.py").write_text(textwrap.dedent(tests), encoding="utf-8")
    junit = tmp_path / "junit-planted.xml"
    env = {**os.environ, "PYTHONPATH": str(TESTS)}
    env.pop("PYTEST_TIMEOUT", None)
    began = time.monotonic()
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-o",
            "asyncio_mode=strict",
        ]
        + ["-o", "hard_deadline_grace=1", f"--junitxml={junit}", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    report = junit.read_text(encoding="utf-8") if junit.exists() else ""
    return ran, report, time.monotonic() - began


_STUBBORN = """
    import asyncio

    import pytest


    @pytest.mark.asyncio
    @pytest.mark.timeout(1)
    async def test_leaves_a_task_that_ignores_its_cancel():
        async def stubborn_waiter():
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    continue

        asyncio.get_running_loop().create_task(stubborn_waiter())
        await asyncio.sleep(10)  # the alarm ends the call here; the teardown cancels the waiter


    def test_that_finishes():
        assert True
"""


def test_a_teardown_that_never_ends_is_killed_and_named_with_its_stacks(tmp_path):
    """🔴 Red before: the alarm ended the call, and the teardown, cancelling a task that ignores
    its cancel, ran with the one alarm spent and never ended (the run's own 25 s limit here; the
    job's on CI). Now its worker is ended at 3 s, xdist names the test it crashed in, the report
    carries every thread's stack and the task that would not end, and the other test still runs."""
    ran, report, took = _run(tmp_path, _STUBBORN, "-n", "1")
    out = ran.stdout + ran.stderr
    assert (
        "crashed while running 'test_planted.py::test_leaves_a_task_that_ignores_its_cancel'" in out
    )
    assert "hard deadline: test_planted.py::test_leaves_a_task_that_ignores_its_cancel" in out
    assert "past its deadline of 3s (twice its timeout of 1s, and 1s)" in out, out[-3000:]
    measured = re.search(r"was still running ([0-9.]+)s after it started", out)
    assert measured and float(measured.group(1)) >= 3.0, "the time it ran is measured, past 3 s"
    # The stacks: the loop's thread in the runner's close, and the task that would not end.
    assert "_cancel_all_tasks" in out and "stubborn_waiter" in out, out[-3000:]
    assert "stubborn_waiter" in report and "hard deadline" in report
    assert "1 passed" in out, out[-1500:]
    assert ran.returncode == 1 and took < 60, (ran.returncode, took)


def test_an_earlier_runs_dump_is_never_attached_to_a_later_crash(tmp_path):
    """A dump an earlier run left under the same name, naming the same test, is gone before any
    test runs: each process removes its own dump at its start. Else a crash of that test for
    another reason, here its process exiting, would carry the stale stacks as its own."""
    stale = tmp_path / "hard-deadline-planted-gw0.txt"
    stale.write_text("hard deadline: test_planted.py::test_whose_process_exits STALE\n", "utf-8")
    ran, report, _ = _run(
        tmp_path,
        """
        import os


        def test_whose_process_exits():
            os._exit(3)
        """,
        "-n",
        "1",
        "--timeout=5",
    )
    out = ran.stdout + ran.stderr
    assert "crashed while running 'test_planted.py::test_whose_process_exits'" in out, out[-1500:]
    assert "STALE" not in out and "STALE" not in report, out[-1500:]
    assert not stale.exists()


def test_a_test_that_ends_is_left_alone(tmp_path):
    ran, report, _ = _run(
        tmp_path,
        """
        import time


        def test_that_takes_a_moment():
            time.sleep(0.5)
        """,
        "-n",
        "1",
        "--timeout=1",
    )
    out = ran.stdout + ran.stderr
    assert ran.returncode == 0, out[-1500:]
    assert "hard deadline" not in out and "crashed" not in out
    assert not list(tmp_path.glob("hard-deadline-*.txt")), "no dump is left by a run that ended"


@pytest.mark.parametrize(
    "mark, args, says",
    [
        (
            "@pytest.mark.timeout(1)",
            ("--timeout=4",),
            "past its deadline of 3s (twice its timeout of 1s, and 1s)",
        ),
        ("", ("--timeout=2",), "past its deadline of 5s (twice its timeout of 2s, and 1s)"),
    ],
    ids=["its own mark", "the run's timeout"],
)
def test_the_deadline_is_twice_the_tests_own_timeout_and_the_grace(tmp_path, mark, args, says):
    """A marked test's deadline follows its mark, over the run's ``--timeout``; one without a mark
    follows the run's. The planted teardown never ends once the alarm has ended the call."""
    ran, _, took = _run(
        tmp_path,
        f"""
        import threading
        import time

        import pytest


        @pytest.fixture
        def never_ends():
            yield
            threading.Event().wait()


        {mark}
        def test_whose_teardown_never_ends(never_ends):
            time.sleep(30)  # the alarm ends the call here
        """,
        "-n",
        "1",
        *args,
    )
    out = ran.stdout + ran.stderr
    assert says in out, out[-3000:]
    assert "in never_ends" in out, "the stack names the teardown it was in"
    assert took < 60, took


def test_a_fixture_that_never_ends_is_killed_and_named(tmp_path):
    """A hang in a fixture's setup, past the alarm (it takes the alarm's Failed and waits again),
    is the test's own work, so its worker is ended at the deadline and the stack names the
    fixture."""
    ran, _, took = _run(
        tmp_path,
        """
        import threading

        import pytest


        @pytest.fixture
        def stubborn_setup():
            while True:
                try:
                    threading.Event().wait()
                except BaseException:  # the alarm's Failed, taken and waited out again
                    continue
            yield


        def test_whose_fixture_never_ends(stubborn_setup):
            pass
        """,
        "-n",
        "1",
        "--timeout=1",
    )
    out = ran.stdout + ran.stderr
    assert "crashed while running 'test_planted.py::test_whose_fixture_never_ends'" in out
    assert "past its deadline of 3s (twice its timeout of 1s, and 1s)" in out, out[-3000:]
    assert "in stubborn_setup" in out, "the stack names the fixture it was in"
    assert took < 60, took


def test_a_test_that_sets_the_clock_back_still_has_its_hang_caught(tmp_path):
    """The opposite patch: a test that sets ``time.monotonic`` to 0.0 for the rest of its run,
    and whose teardown never ends, is still ended at its deadline. A watchdog that read the
    patched clock would find the deadline still to come, for good."""
    ran, _, took = _run(
        tmp_path,
        """
        import threading
        import time

        import pytest


        @pytest.fixture
        def never_ends():
            yield
            threading.Event().wait()


        def test_that_sets_the_clock_back(monkeypatch, never_ends):
            monkeypatch.setattr("time.monotonic", lambda: 0.0)  # undone after never_ends ends
            time.sleep(30)  # the alarm ends the call here
        """,
        "-n",
        "1",
        "--timeout=1",
    )
    out = ran.stdout + ran.stderr
    crashed = "crashed while running 'test_planted.py::test_that_sets_the_clock_back'"
    assert crashed in out, out[-3000:]
    assert "past its deadline of 3s (twice its timeout of 1s, and 1s)" in out
    assert "in never_ends" in out
    assert took < 60, took


def test_a_test_that_patches_the_clock_never_moves_the_deadline(tmp_path, monkeypatch):
    """Merge run 40189449012 ended a worker running ``test_evaluate_reads_no_clock``, which sets
    ``time.monotonic`` to 9e12 for its call: the watchdog read that clock, woken by the test's
    start only once the test had set it, and took the test for one 9e12 s past its deadline.
    The watchdog's clock is its own. Here it is held from reading until the clock is patched, as
    that busy worker held it, and then left to read: it ends nothing."""
    ended: list[int] = []
    dump = tmp_path / "hard-deadline.txt"
    real, patched, read = time.monotonic, threading.Event(), threading.Event()

    def clock() -> float:
        """The real clock, which says when the watchdog reads it once the test's patch is in."""
        if patched.is_set() and threading.current_thread().name == "hard-deadline":
            read.set()
        return real()

    watchdog = hard_deadline.Watchdog(dump, end=ended.append, clock=clock)
    with watchdog._changed:  # the watchdog reads the deadline only once this is let go
        watchdog.arm("test_planted.py::test_reads_no_clock", 30.0, 30.0)
        monkeypatch.setattr("time.monotonic", lambda: 9e12)
        patched.set()
    assert read.wait(10), "the watchdog never read its deadline"
    watchdog.disarm()  # takes the watchdog's lock: once it is waiting again, or done ending
    assert ended == [], dump.read_text(encoding="utf-8")[:500]
    assert not dump.exists()
