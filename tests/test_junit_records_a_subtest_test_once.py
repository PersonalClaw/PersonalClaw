"""The JUnit report records a test with subtests at the time it took, once (tests/junit_once.py).

Each case plants a small suite and runs it in a pytest of its own, with the plugin or without it,
and reads the report it wrote. The planted conftest gives every report a fixed duration before
anything reads it (a test's call 1 s, each subtest 0.5 s, setup and teardown none), so the report's
times are exact and no case reads a clock: a test with two subtests is 1 s once, and 2 s counted
twice.
"""

from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import junit_once  # pytest's private names it reads: a pytest that moves them fails here
import nested_pytest
import pytest

TESTS = Path(__file__).resolve().parent

_CONFTEST = """\
import os

import junit_once
import pytest
from _pytest.subtests import SubtestReport


def pytest_configure(config):
    if os.environ.get("PLANT_JUNIT_ONCE") == "1":
        junit_once.configure(config)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    # Before the JUnit writer and an xdist worker's forwarding read it. Under xdist only the worker
    # sets them, so the controller's report holds what the worker forwarded.
    if os.environ.get("PLANT_XDIST") == "1" and not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    if isinstance(report, SubtestReport):
        report.duration = 0.5
    else:
        report.duration = 1.0 if report.when == "call" else 0.0
"""

_SUITE = """\
def test_two_subtests(subtests):
    for i in range(2):
        with subtests.test(i=i):
            pass


def test_no_subtests():
    pass


def test_a_failing_subtest(subtests):
    with subtests.test(i=0):
        assert False, "planted"
"""


def _report(tmp_path: Path, *, plugin: bool, args: tuple[str, ...] = ()) -> dict[str, ET.Element]:
    suite = tmp_path / ("with" if plugin else "without") / "-".join(args or ("one",))
    suite.mkdir(parents=True)
    (suite / "pytest.ini").write_text("[pytest]\n")
    (suite / "conftest.py").write_text(_CONFTEST)
    (suite / "test_planted.py").write_text(_SUITE)
    out = suite / "junit.xml"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTHONPATH")}
    env.update(
        PYTHONPATH=str(TESTS),
        PLANT_JUNIT_ONCE="1" if plugin else "0",
        PLANT_XDIST="1" if "-n" in args else "0",
    )
    ran = subprocess.run(
        nested_pytest.argv(suite, "-p", "no:cacheprovider", "-q", f"--junitxml={out}", *args),
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr  # the planted failing subtest
    return {case.get("name"): case for case in ET.parse(out).getroot().iter("testcase")}


def _seconds(cases: dict[str, ET.Element], name: str) -> float:
    return float(cases[name].get("time", "nan"))


_MODES = pytest.mark.parametrize("args", [(), ("-n", "2")], ids=["one process", "xdist workers"])


@_MODES
def test_a_test_with_subtests_is_recorded_at_the_time_it_took(tmp_path: Path, args) -> None:
    cases = _report(tmp_path, plugin=True, args=args)
    assert _seconds(cases, "test_two_subtests") == 1.0
    assert _seconds(cases, "test_no_subtests") == 1.0


@_MODES
def test_without_it_the_subtests_time_counts_twice(tmp_path: Path, args) -> None:
    """The vacuity floor: the planted test reads its call and its subtests again, 2 s."""
    cases = _report(tmp_path, plugin=False, args=args)
    assert _seconds(cases, "test_two_subtests") == 2.0
    assert _seconds(cases, "test_no_subtests") == 1.0


def test_a_failing_subtest_is_still_in_the_report(tmp_path: Path) -> None:
    """Only the duration a subtest adds goes: its failure is its test's, as before."""
    cases = _report(tmp_path, plugin=True)
    failed = cases["test_a_failing_subtest"]
    assert failed.find("failure") is not None, ET.tostring(failed, encoding="unicode")
    assert "planted" in ET.tostring(failed, encoding="unicode")


def test_the_suite_runs_it(request: pytest.FixtureRequest) -> None:
    """tests/conftest.py registers it, so every report the suite writes records its tests once."""
    plugin = request.config.pluginmanager.get_plugin("junit-once")
    assert isinstance(plugin, junit_once.Plugin), plugin
