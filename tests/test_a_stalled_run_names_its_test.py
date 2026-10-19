"""A test run that stalls leaves behind which test it was in, and every thread's stack.

Measured: three Linux shards stopped printing 43-45 minutes before the job's 55-minute cap, at
78-89% of their items, with pytest's controller and all four workers still alive when the runner
killed them. Nothing named the test: pytest writes its JUnit report only when the session ends, so
it wrote none, and the 120 s per-test timeout never fired.

``tests/stall_catcher.py`` leaves two things behind instead, in ``reports/stalls/``, when
``PERSONALCLAW_TEST_STALL_DUMP_SECONDS`` is set (CI sets it under each suite job's cap):

* every pytest process dumps every thread's stack at that many seconds after it started, from
  faulthandler's own C thread, so a test that holds the interpreter lock is dumped too;
* the run's controller writes a JUnit report as the run goes, with each test that has started and
  not finished marked as still running.

A run that finishes leaves neither: its own JUnit report says what happened.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from table_rows import row

_TESTS = Path(__file__).resolve().parent

#: Seconds after it starts that each process of the planted run dumps its threads.
_DUMP_AT = 3
#: How long a planted run is given to leave what it leaves, before the test stops it.
_GIVEN = 60

_PASSES = "def test_passes():\n    pass\n"
_SLEEPS = "import time\n\n\ndef test_hangs():\n    time.sleep(120)\n"
#: A hang that holds the interpreter lock: a backtracking match runs in C and never lets go.
_HOLDS = "import re\n\n\ndef test_hangs():\n" "    re.match(r'(a+)+$', 'a' * 40 + 'b')\n"
_FAILS_FIRST = (
    "import time\n\n\ndef test_a_fails():\n    assert False\n\n\n"
    "def test_hangs():\n    time.sleep(120)\n"
)


def _plant(tmp_path: Path, **files: str) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    for name, text in files.items():
        (project / f"{name}.py").write_text(textwrap.dedent(text))
    return project


def _start(project: Path, *args: str, dump_at: int | None = _DUMP_AT) -> subprocess.Popen:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "PERSONALCLAW_TEST_STALL"))
    }
    env["PYTHONPATH"] = str(_TESTS)
    if dump_at is not None:
        env["PERSONALCLAW_TEST_STALL_DUMP_SECONDS"] = str(dump_at)
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "stall_catcher", "-p", "no:cacheprovider", "-q"]
        + list(args),
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _stop(run: subprocess.Popen) -> str:
    """Kill the planted run and every process it started (its xdist workers), as the runner does
    at the cap: with no warning, so nothing it does on its way out counts."""
    # macOS answers EPERM, not ESRCH, for a group whose leader has already exited.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(run.pid, signal.SIGKILL)
    out, _ = run.communicate(timeout=30)
    return out.decode(errors="replace")


def _dumps(project: Path) -> dict[str, str]:
    folder = project / "reports" / "stalls"
    if not folder.is_dir():
        return {}
    return {p.name: p.read_text(errors="replace") for p in folder.glob("threads-*.txt")}


def _partial(project: Path) -> ET.Element | None:
    path = project / "reports" / "stalls" / "junit-so-far.xml"
    try:
        return ET.fromstring(path.read_text())
    except (OSError, ET.ParseError):
        return None


def _still_running(report: ET.Element | None) -> list[str]:
    if report is None:
        return []
    return [
        f"{case.get('classname')}::{case.get('name')}"
        for case in report.iter("testcase")
        if case.find("error") is not None
        and "still running" in (case.find("error").get("message") or "")
    ]


def _finished(report: ET.Element | None) -> list[str]:
    if report is None:
        return []
    return [
        f"{case.get('classname')}::{case.get('name')}"
        for case in report.iter("testcase")
        if case.find("error") is None
        or "still running" not in (case.find("error").get("message") or "")
    ]


def _wait_for(project: Path, processes: int) -> None:
    """Until *processes* of the run have dumped their threads, one dump is in ``test_hangs``, and
    the report says a test is still running, or :data:`_GIVEN` seconds."""
    deadline = time.monotonic() + _GIVEN
    while time.monotonic() < deadline:
        dumps = _dumps(project)
        in_it = [text for text in dumps.values() if "test_hangs" in text]
        if len(dumps) >= processes and in_it and _still_running(_partial(project)):
            return
        time.sleep(0.25)


def test_a_stalled_run_names_the_test_it_is_in(subtests, tmp_path):
    """🔴 Red before: a run stopped at the cap left nothing. Now, whether the stalled test waits
    (a sleep) or holds the interpreter lock (a C call that never returns to Python, which no
    Python thread can interrupt), every process still running dumps its threads: the controller,
    and the worker that runs it, whose dump is in ``test_hangs``. The other worker ran out of
    tests and ended its session, so it removed its own. The report written as the run went names
    the stalled test as still running, beside the test that had passed."""
    hangs = {"a test that waits": _SLEEPS, "a test that holds the interpreter lock": _HOLDS}
    assert len(hangs) == 2
    for name, hang in hangs.items():
        with row(subtests, hang=name):
            project = _plant(tmp_path / name.replace(" ", "-"), test_a=_PASSES, test_b=hang)
            run = _start(project, "-n", "2", "test_a.py", "test_b.py")
            try:
                _wait_for(project, processes=2)
            finally:
                out = _stop(run)

            dumps = _dumps(project)
            report = _partial(project)
            workers = sorted(n for n in dumps if n != "threads-controller.txt")
            assert "threads-controller.txt" in dumps, f"{name}: {sorted(dumps)}\n{out}"
            assert len(workers) == 1, f"{name}: the workers' dumps: {workers}\n{out}"
            in_it = [n for n, text in dumps.items() if "test_hangs" in text]
            assert in_it == workers, f"{name}: the dumps in the test: {in_it}\n{dumps}"
            assert "Thread 0x" in dumps[in_it[0]] and "test_b.py" in dumps[in_it[0]], dumps
            assert _still_running(report) == ["test_b::test_hangs"], f"{name}: {out}"
            assert _finished(report) == ["test_a::test_passes"], f"{name}: {out}"


def test_a_failing_test_does_not_cancel_the_dump(tmp_path):
    """🔴 Red before (a timer armed once): faulthandler holds one timer per process, and pytest
    cancels it whenever a test fails (its ``pytest_exception_interact``). A run that stalled after
    a failure dumped nothing. The timer is armed again after the failure, so it still fires."""
    project = _plant(tmp_path, test_it=_FAILS_FIRST)
    run = _start(project, "-p", "no:xdist", "test_it.py")
    try:
        _wait_for(project, processes=1)
    finally:
        out = _stop(run)

    dumps = _dumps(project)
    assert list(dumps) == ["threads-controller.txt"], f"{sorted(dumps)}\n{out}"
    assert "test_hangs" in dumps["threads-controller.txt"], dumps
    assert _still_running(_partial(project)) == ["test_it::test_hangs"], out
    assert _finished(_partial(project)) == ["test_it::test_a_fails"], out


def test_a_run_that_finishes_leaves_nothing(subtests, tmp_path):
    """A run that ends, passing or failing, leaves no stall files: its own JUnit report is the
    record, and an empty upload would read as a stall. Without the setting, nothing is armed."""
    cases = {
        "a passing run, set": (_PASSES, _DUMP_AT, 0),
        "a failing run, set": ("def test_fails():\n    assert False\n", _DUMP_AT, 1),
        "a passing run, not set": (_PASSES, None, 0),
    }
    assert len(cases) == 3
    for name, (text, dump_at, status) in cases.items():
        with row(subtests, run=name):
            project = _plant(tmp_path / name.replace(" ", "-").replace(",", ""), test_it=text)
            run = _start(project, "-n", "2", "test_it.py", dump_at=dump_at)
            out, _ = run.communicate(timeout=_GIVEN)
            assert run.returncode == status, f"{name}: {out.decode(errors='replace')}"
            folder = project / "reports" / "stalls"
            left = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
            assert left == [], f"{name}: the run left {left}"


_READS_THE_SETTING = (
    "import os\n\n\ndef test_it():\n"
    "    assert 'PERSONALCLAW_TEST_STALL_DUMP_SECONDS' not in os.environ\n"
)


def test_the_tests_of_a_run_do_not_inherit_the_setting(subtests, tmp_path):
    """🔴 Red before: a test that starts a pytest of its own (several do, over a planted project)
    passed the setting on, so the inner run armed a catcher of its own and could write over the
    outer run's files when both have one root. The setting is taken out of the environment once
    the run's own processes have read it: by each worker as it starts, and by the controller
    once its workers have started."""
    runs = {"with workers": ("-n", "2"), "without workers": ("-p", "no:xdist")}
    assert len(runs) == 2
    for name, args in runs.items():
        with row(subtests, run=name):
            project = _plant(tmp_path / name.replace(" ", "-"), test_it=_READS_THE_SETTING)
            run = _start(project, *args, "test_it.py")
            out, _ = run.communicate(timeout=_GIVEN)
            assert run.returncode == 0, f"{name}: {out.decode(errors='replace')}"


def test_an_interrupted_run_keeps_what_it_left(tmp_path):
    """🔴 Red before: a run stopped with an interrupt (a cancelled job's runner sends one before
    it kills) ended its session and removed its stall files on the way out, so the record of
    where it was went with it. An interrupted run keeps them."""
    project = _plant(tmp_path, test_a=_PASSES, test_b=_SLEEPS)
    run = _start(project, "-n", "2", "test_a.py", "test_b.py")
    try:
        _wait_for(project, processes=2)
        run.send_signal(signal.SIGINT)
        # The controller stops its workers on the way out, which takes a while on a busy machine.
        run.wait(timeout=_GIVEN)
    finally:
        out = _stop(run)

    dumps = _dumps(project)
    assert "threads-controller.txt" in dumps, f"{sorted(dumps)}\n{out}"
    assert any("test_hangs" in text for text in dumps.values()), f"{sorted(dumps)}\n{out}"
    assert _still_running(_partial(project)) == ["test_b::test_hangs"], out
