"""``--fixture-costs`` reports each fixture's own setup time, merged across xdist workers.

A run of its own, in a folder of its own, with only the report's plugin: two fixtures that spend
3 ms and 5 ms per setup, and a fixture that asks for both from its own code
(``request.getfixturevalue``), so both are set up inside the dependent's own setup, one after the
other. Each sleeper must show its own time, the dependent about none of it, and each test's total
the two sleepers' times added together, not twice that, because the time spent setting up a
fixture inside another is charged to it alone. The total is compared with what the sleepers
measured in the same run, so a slow machine slows both sides.
Run once without workers and once with two, which also proves the controller merges every
worker's measurements into the one file.
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


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_planted_5ms_fixture_reports_about_5ms(tmp_path: Path, workers: str) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(textwrap.dedent(_CONFTEST))
    (tmp_path / "test_planted.py").write_text(textwrap.dedent(_TESTS))
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
    report = json.loads((tmp_path / "reports" / "fixture-costs.json").read_text())
    assert report["tests"] == 12
    assert report["workers"] == int(workers)
    rows = {row["fixture"].rsplit("::", 1)[1]: row for row in report["fixtures"]}
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
    total = report["per_test_setup"]["median_ms"]
    assert abs(total - sleepers) <= _MARGIN_MS, (total, sleepers, report["per_test_setup"])
    assert total < 1.5 * sleepers, (total, sleepers, report["per_test_setup"])
