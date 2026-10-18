"""``tests/run_facts.py``: each pytest process records where it ran in the junit report (#3948).

The telemetry collector (``tools/ci/telemetry.py``) reads a platform and a worker count from each
report, so the shard planner keeps one platform's and one ``-n``'s seconds apart. The suite
records both as junit properties on each process's first test case. Checked by running pytest
on a tree the test makes, in one process and under xdist.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import run_facts

from tools.ci import telemetry

TESTS = Path(__file__).resolve().parent

CONFTEST = f"""
import sys

sys.path.insert(0, {str(TESTS)!r})
sys.path.insert(0, {str(TESTS.parent)!r})  # tools.ci, as the suite's pythonpath gives it
import run_facts


def pytest_configure(config):
    run_facts.configure(config)
"""


def _run(tmp_path: Path, *args: str) -> list[dict[str, str]]:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    body = "".join(f"def test_{i}():\n    pass\n\n\n" for i in range(6))
    (tests / "test_some.py").write_text(body, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
        + ["tests", "--junitxml=junit.xml", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return [
        {p.get("name"): p.get("value") for p in case.iter("property")}
        for case in ET.parse(tmp_path / "junit.xml").iter("testcase")
    ]


@pytest.mark.parametrize("workers, expected", [([], 0), (["-n", "2"], 2)], ids=["one", "xdist"])
def test_each_process_records_its_platform_and_worker_count_once(
    workers: list[str], expected: int, tmp_path: Path
) -> None:
    cases = _run(tmp_path, *workers)
    recorded = [c for c in cases if c]
    assert len(cases) == 6
    assert len(recorded) == max(expected, 1), "one record per process, on its first test"
    for facts in recorded:
        assert facts == {
            telemetry.PLATFORM_PROPERTY: run_facts.platform_id(),
            telemetry.WORKERS_PROPERTY: str(expected),
        }


def test_the_platform_names_the_os_machine_and_python() -> None:
    system, machine, python = run_facts.platform_id().split("-")
    assert system == platform.system().lower() and machine == platform.machine().lower()
    assert python == platform.python_version()


def test_the_suite_registers_it() -> None:
    assert "run_facts.configure(config)" in (TESTS / "conftest.py").read_text(encoding="utf-8")
