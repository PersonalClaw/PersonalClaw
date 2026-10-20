"""``--fixture-costs`` reports each fixture's own setup and teardown time, merged across xdist
workers.

A run of its own, in a folder of its own, with only the report's plugin: two fixtures that spend
3 ms and 5 ms per setup, and a fixture that asks for both from its own code
(``request.getfixturevalue``), so both are set up inside the dependent's own setup, one after the
other. Each sleeper must show its own time, the dependent about none of it, and each test's total
the two sleepers' times added together, not twice that, because the time spent setting up a
fixture inside another is charged to it alone. The total is compared with what the sleepers
measured in the same run, so a slow machine slows both sides.
Run once without workers and once with two, which also proves the controller merges every
worker's measurements into the one file.

The teardown half plants a dependency and its dependent that each spend a known CPU time after
their ``yield``, and a fixture that sleeps in its teardown. Each must show its own time, the
dependency none of its dependent's, the sleep in wall time and not in CPU time, and each test's
teardown total the three added together.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

#: How far each test's setup total may sit from the two sleepers' medians added together. Thirty
#: correct runs on a machine with a load average of 15-33 put it 0.04-0.90 ms above that sum;
#: the nearest wrong attribution (a nested frame read without being popped, or popped from the
#: wrong end) puts it about 3.1 ms above. 2 ms is over twice the worst correct gap, and a
#: millisecond short of the nearest wrong one.
_MARGIN_MS = 2.0

_CONFTEST = """
import fixture_costs


def pytest_configure(config):
    config.pluginmanager.register(fixture_costs.Plugin(config.rootpath), "fixture-costs")
"""

_TESTS = """
import time

import pytest


def _spend(seconds):
    # A busy wait, not time.sleep: a sleep is only a lower bound, and a machine that coalesces
    # timers sleeps far longer than asked (measured: 38.6 ms for a 5 ms sleep).
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


@pytest.fixture
def short_sleeper():
    _spend(0.003)


@pytest.fixture
def sleeper():
    _spend(0.005)


@pytest.fixture
def depends_on_the_sleeper(request):
    # Asked for from this fixture's own code, both sleepers are set up inside this fixture's
    # pytest_fixture_setup call, one after the other. A declared argument would not be: pytest
    # sets those up before it calls the hook, so they never nest inside it and would prove
    # nothing about the attribution. Two children, so that taking the wrong frame off the stack,
    # or reading one without taking it off, charges a sleeper's time to the wrong fixture.
    request.getfixturevalue("short_sleeper")
    request.getfixturevalue("sleeper")


@pytest.mark.parametrize("n", range(12))
def test_uses_both(depends_on_the_sleeper, n):
    pass
"""


def _run(tmp_path: Path, tests: str, workers: str) -> dict:
    """Run *tests* in a pytest of its own with only the report's plugin, and read the report."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(textwrap.dedent(_CONFTEST))
    (tmp_path / "test_planted.py").write_text(textwrap.dedent(tests))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n", workers],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    return json.loads((tmp_path / "reports" / "fixture-costs.json").read_text())


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_planted_5ms_fixture_reports_about_5ms(tmp_path: Path, workers: str) -> None:
    report = _run(tmp_path, _TESTS, workers)
    assert report["tests"] == 12
    assert report["workers"] == int(workers)
    rows = {row["fixture"].rsplit("::", 1)[1]: row["setup"] for row in report["fixtures"]}
    short = rows["short_sleeper"]
    assert short["calls"] == 12
    assert 2.7 <= short["median_ms"] <= 15, short
    sleeper = rows["sleeper"]
    assert sleeper["calls"] == 12
    assert 4.5 <= sleeper["median_ms"] <= 25, sleeper
    depends = rows["depends_on_the_sleeper"]
    assert depends["calls"] == 12
    assert depends["median_ms"] < 1, depends
    # Each test's total against what the two sleepers measured in this same run, not against a
    # fixed number: on a loaded machine every setup runs slower, and a fixed bound failed there
    # with nothing wrong. Counted once, the total is the sleepers' time plus a little of its own
    # (the dependent, pytest's parametrize fixture, the hook itself). Counted twice, it is about
    # double the sleepers' time, and a frame left on the stack or taken from the wrong end adds
    # about the short sleeper's 3 ms again.
    sleepers = short["median_ms"] + sleeper["median_ms"]
    total = report["per_test"]["setup"]["median_ms"]
    assert abs(total - sleepers) <= _MARGIN_MS, (total, sleepers, report["per_test"]["setup"])
    assert total < 1.5 * sleepers, (total, sleepers, report["per_test"]["setup"])


#: Teardowns planted for the teardown half: a dependency that spends 5 ms of CPU after its yield,
#: a fixture that depends on it and spends 3 ms after its own (pytest tears the dependent down
#: first), and a fixture that SLEEPS 20 ms in its teardown, which shows in wall time and not in
#: CPU time.
_TEARDOWN_TESTS = """
import time

import pytest


def _spend(seconds):
    # Spent on this thread's CPU clock, not the wall clock: on a loaded machine a wall-clock busy
    # wait is descheduled for part of its time (measured: 4.1 ms of CPU in a 5 ms wait), and
    # what is planted here is CPU, which the report must then read as at least that.
    end = time.thread_time() + seconds
    while time.thread_time() < end:
        pass


@pytest.fixture
def slow_to_close():
    yield
    _spend(0.005)


@pytest.fixture
def closes_after_its_dependent(slow_to_close):
    yield
    _spend(0.003)


@pytest.fixture
def waits_to_close():
    yield
    time.sleep(0.02)


@pytest.mark.parametrize("n", range(12))
def test_uses_them(closes_after_its_dependent, waits_to_close, n):
    pass
"""


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_planted_teardown_is_charged_to_its_own_fixture(tmp_path: Path, workers: str) -> None:
    """Each teardown shows its own time, the dependent's is not charged to its dependency, the
    sleep shows in wall time only, and each test's teardown total is the three added together."""
    report = _run(tmp_path, _TEARDOWN_TESTS, workers)
    assert report["tests"] == 12
    rows = {row["fixture"].rsplit("::", 1)[1]: row["teardown"] for row in report["fixtures"]}
    # Judged in CPU time, which is what was planted: the wall clock of a loaded machine adds
    # whatever it was descheduled for (a 20 ms sleep measured 70-89 ms wall here).
    dependency = rows["slow_to_close"]
    assert dependency["calls"] == 12, dependency
    assert 4.9 <= dependency["cpu_median_ms"] <= 6.5, dependency
    assert dependency["median_ms"] >= dependency["cpu_median_ms"], dependency
    dependent = rows["closes_after_its_dependent"]
    assert dependent["calls"] == 12, dependent
    # Charged with the other's 3 ms or 5 ms, either would read about 8 ms.
    assert 2.9 <= dependent["cpu_median_ms"] <= 4.5, dependent
    asleep = rows["waits_to_close"]
    assert asleep["calls"] == 12, asleep
    assert asleep["median_ms"] >= 19, asleep
    assert asleep["cpu_median_ms"] < 1, asleep
    # Each test's teardown total is the three teardowns, once each: five correct runs put the
    # CPU total within 0.02 ms of the medians' sum; counting nothing, or one twice, misses by 3 ms
    # or more.
    planted = sum(row["cpu_median_ms"] for row in (dependency, dependent, asleep))
    total = report["per_test"]["teardown"]["cpu_median_ms"]
    assert abs(total - planted) <= 1.0, (total, planted, report["per_test"]["teardown"])
    assert report["per_test"]["teardown"]["median_ms"] >= asleep["median_ms"], report["per_test"]


class _BusyClock:
    """``time`` as the report reads it, on a thread that is busy the whole time: both clocks read
    the same elapsed time, and each read of either takes the next of *steps* (microseconds)."""

    def __init__(self, steps: list[float]) -> None:
        self._steps = list(steps)
        self._now = 0.0

    def _read(self) -> float:
        self._now += self._steps.pop(0) / 1e6
        return self._now

    perf_counter = _read
    thread_time = _read


def _a_fixture() -> None:
    """The function a fake fixture definition names, so the report can key it by its file."""


class _FixtureDef:
    func = staticmethod(_a_fixture)
    argname = "busy"
    scope = "function"

    def __init__(self) -> None:
        self.finalizers: list = []

    def addfinalizer(self, finalizer) -> None:
        self.finalizers.append(finalizer)


#: Reads that took 1 µs, 1 µs, 5 ms (the span itself) and 3 µs: the last read took longer than
#: the second. Read wall first at both ends, the CPU span is the last three steps (5,003 µs) and
#: the wall span the middle two (5,001 µs), which is how CI read a busy 5 ms teardown as 5.018 ms
#: of CPU in 5.017 ms of wall time.
_STEPS = [1.0, 1.0, 5000.0, 3.0]


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_a_busy_span_never_reads_more_cpu_time_than_wall_time(
    tmp_path: Path, monkeypatch, phase: str
) -> None:
    """🔴 Red before: read wall first at both ends, a span whose last clock read took longer than
    its second read more CPU time than wall time. However long each read takes, the wall span now
    holds the CPU span."""
    import fixture_costs

    monkeypatch.setattr(fixture_costs, "time", _BusyClock(_STEPS))
    plugin = fixture_costs.Plugin(tmp_path)
    fixturedef = _FixtureDef()
    if phase == "setup":
        hook = plugin.pytest_fixture_setup(fixturedef, None)
        next(hook)
        with pytest.raises(StopIteration):
            next(hook)
    else:
        plugin._key(fixturedef)
        plugin._teardown_starts(id(fixturedef))
        plugin.pytest_fixture_post_finalizer(fixturedef, None)
    measured = next(iter(plugin._fixtures.values()))[phase]
    assert len(measured["wall"]) == len(measured["cpu"]) == 1, measured
    assert measured["cpu"][0] >= 5000, measured
    assert measured["wall"][0] >= measured["cpu"][0], measured
