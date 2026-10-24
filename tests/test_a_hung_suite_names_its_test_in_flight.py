"""A suite that hangs names the test it hung on (#3905).

``tests/in_flight.py`` keeps the tests the xdist controller has heard start and not finish, and
rewrites them, oldest first, to ``in-flight-<leg>.txt`` beside the run's JUnit report every ten
seconds. Here a real pytest at ``-n 2`` runs a test that blocks far past the kill, and is killed
whole once the file has been rewritten with it in flight, as a job's cap stops a hung shard: the
file names that test, oldest. A run that ends leaves the file empty, and a worker never arms it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent

CONFTEST = """
import in_flight

def pytest_configure(config):
    in_flight.configure(config)
"""

BLOCKS = """
import time

def test_quick_one():
    pass

def test_quick_two():
    pass

def test_blocks_far_past_the_kill():
    time.sleep(600)
"""

QUICK = """
def test_quick_one():
    pass

def test_quick_two():
    pass
"""


def _project(tmp_path: Path, tests: str) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (project / "test_suite.py").write_text(tests, encoding="utf-8")
    return project


def _pytest(project: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")]),
    }
    env.pop("PYTEST_ADDOPTS", None)
    argv = [sys.executable, "-m", "pytest", "-n", "2", "-p", "no:cacheprovider", "-q"]
    argv += ["--junitxml=reports/junit-plant.xml", "test_suite.py"]
    return subprocess.Popen(
        argv,
        cwd=project,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _entries(report: Path) -> list[list[str]]:
    return [line.split("\t") for line in report.read_text(encoding="utf-8").splitlines()]


def _named(report: Path, nodeid: str) -> bool:
    try:
        return any(entry[2:] == [nodeid] for entry in _entries(report))
    except OSError:
        return False


def test_a_run_killed_on_a_hung_test_names_it_oldest(tmp_path: Path) -> None:
    project = _project(tmp_path, BLOCKS)
    report = project / "reports" / "in-flight-plant.txt"
    hung = "test_suite.py::test_blocks_far_past_the_kill"
    run = _pytest(project)
    try:
        # The file is rewritten every ten seconds: wait for the rewrite that has the test in it.
        by = time.monotonic() + 60
        while not _named(report, hung):
            assert run.poll() is None, "the run ended before it was killed"
            assert time.monotonic() < by, "no rewrite named the hung test within a minute"
            time.sleep(0.2)
    finally:
        os.killpg(run.pid, signal.SIGKILL)
        run.wait(timeout=30)
    entries = _entries(report)
    assert entries, "the file named no test in flight"
    started, worker, nodeid = entries[0]
    assert nodeid == hung
    assert worker.startswith("gw"), worker
    assert started.endswith("Z") and "T" in started
    assert [e[2] for e in entries] == [nodeid], "the quick tests finished and left it"


def test_a_run_that_ends_leaves_the_file_empty(tmp_path: Path) -> None:
    project = _project(tmp_path, QUICK)
    run = _pytest(project)
    assert run.wait(timeout=60) == 0
    assert (project / "reports" / "in-flight-plant.txt").read_text(encoding="utf-8") == ""
    left = [p.name for p in (project / "reports").iterdir() if p.name.endswith(".tmp")]
    assert left == [], "a rewrite left its temporary file behind"


def test_a_worker_and_a_run_with_no_report_arm_nothing(pytestconfig) -> None:
    import in_flight

    class Option:
        xmlpath = "reports/junit-test-shard-3.xml"
        numprocesses = 2

    class Config:
        option = Option()
        pluginmanager = None

    assert in_flight.report_path(Config()) == Path("reports/in-flight-test-shard-3.txt")
    worker = Config()
    worker.workerinput = {"workerid": "gw0"}  # type: ignore[attr-defined]
    in_flight.configure(worker)  # a worker: never reaches the plugin manager, which is None
    Option.xmlpath = None
    in_flight.configure(Config())  # no JUnit report: nothing to write, nothing registered


def test_the_oldest_test_in_flight_comes_first(tmp_path: Path, monkeypatch) -> None:
    import in_flight

    plugin = in_flight.Plugin(tmp_path / "in-flight-x.txt", workers=True)
    clock = iter([300.0, 100.0, 200.0])
    monkeypatch.setattr(in_flight.time, "time", lambda: next(clock))
    for nodeid in ("t.py::newest", "t.py::oldest", "t.py::middle"):
        plugin.pytest_runtest_logstart(nodeid, None)
    plugin.pytest_runtest_logfinish("t.py::middle", None)
    plugin.write()
    entries = _entries(tmp_path / "in-flight-x.txt")
    assert [e[2] for e in entries] == ["t.py::oldest", "t.py::newest"]
    assert [e[1] for e in entries] == ["?", "?"], "no report has named their workers yet"
