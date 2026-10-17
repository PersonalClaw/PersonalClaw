"""``--fixture-costs`` reports each fixture's own setup time, merged across xdist workers.

A run of its own, in a folder of its own, with only the report's plugin: a fixture that spends
5 ms per setup and a fixture that depends on it. The sleeper must show a median of about 5 ms, and
the fixture that depends on it about none of that, because the time spent setting up a dependency
is charged to the dependency. Run once without workers and once with two, which also proves the
controller merges every worker's measurements into the one file.
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

_CONFTEST = """
import fixture_costs


def pytest_configure(config):
    config.pluginmanager.register(fixture_costs.Plugin(config.rootpath), "fixture-costs")
"""

_TESTS = """
import time

import pytest


@pytest.fixture
def sleeper():
    # A busy wait, not time.sleep: a sleep is only a lower bound, and a machine that coalesces
    # timers sleeps far longer than asked (measured: 38.6 ms for a 5 ms sleep).
    end = time.perf_counter() + 0.005
    while time.perf_counter() < end:
        pass


@pytest.fixture
def depends_on_the_sleeper(sleeper):
    return None


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
    sleeper = rows["sleeper"]
    assert sleeper["calls"] == 12
    assert 4.5 <= sleeper["median_ms"] <= 25, sleeper
    depends = rows["depends_on_the_sleeper"]
    assert depends["calls"] == 12
    assert depends["median_ms"] < 1, depends
    assert 4.5 <= report["per_test_setup"]["median_ms"], report["per_test_setup"]
