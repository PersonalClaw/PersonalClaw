"""A test still running past its hard deadline is killed, and named with its stacks
(tests/hard_deadline.py).

Each plant is a pytest run of its own, under xdist as CI runs the suite, with the deadline's grace
at 1 s so it comes in seconds: twice the test's timeout, and 1 s.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

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
    assert "twice its timeout of 1s and 1s" in out, out[-3000:]
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
            "still running 3s after it started, twice its timeout of 1s",
        ),
        ("", ("--timeout=2",), "still running 5s after it started, twice its timeout of 2s"),
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
