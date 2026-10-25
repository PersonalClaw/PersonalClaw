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

import nested_pytest
import pytest
import run_facts

from tools.ci import telemetry

TESTS = Path(__file__).resolve().parent

CONFTEST = f"""
import sys

sys.path.insert(0, {str(TESTS)!r})  # tests/ alone, as a test's own pytest run has it
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
        [
            *nested_pytest.argv(tmp_path),
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
        ]
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


@pytest.mark.timeout(50)
@pytest.mark.parametrize("workers, expected", [([], 0), (["-n", "2"], 2)], ids=["one", "xdist"])
def test_each_process_records_its_platform_and_worker_count_once(
    workers: list[str], expected: int, tmp_path: Path
) -> None:
    cases = _run(tmp_path, *workers)
    recorded = [c for c in cases if c]
    assert len(cases) == 6
    assert len(recorded) == max(expected, 1), "one record per process, on its first test"
    for facts in recorded:
        want = {
            telemetry.PLATFORM_PROPERTY: run_facts.platform_id(),
            telemetry.WORKERS_PROPERTY: str(expected),
        }
        if run_facts.cpu_model():
            want[telemetry.CPU_PROPERTY] = run_facts.cpu_model()
        assert facts == want


def test_the_platform_names_the_os_machine_and_python() -> None:
    system, machine, python = run_facts.platform_id().split("-")
    assert system == platform.system().lower() and machine == platform.machine().lower()
    assert python == platform.python_version()


def test_the_suite_registers_it() -> None:
    assert "run_facts.configure(config)" in (TESTS / "conftest.py").read_text(encoding="utf-8")


def test_the_property_names_are_the_collectors() -> None:
    """The suite writes the two properties under the names the collector reads."""
    assert run_facts.PLATFORM_PROPERTY == telemetry.PLATFORM_PROPERTY
    assert run_facts.WORKERS_PROPERTY == telemetry.WORKERS_PROPERTY
    assert run_facts.CPU_PROPERTY == telemetry.CPU_PROPERTY


def test_the_cpu_model_is_read_from_linuxs_cpuinfo(tmp_path: Path, monkeypatch) -> None:
    info = tmp_path / "cpuinfo"
    info.write_text("processor\t: 0\nmodel name\t: AMD EPYC 7763 64-Core Processor\n", "utf-8")
    monkeypatch.setattr(run_facts, "CPUINFO", info)
    monkeypatch.setattr(run_facts.sys, "platform", "linux")
    run_facts.cpu_model.cache_clear()
    try:
        assert run_facts.cpu_model() == "AMD EPYC 7763 64-Core Processor"
    finally:
        run_facts.cpu_model.cache_clear()


def test_a_cpu_model_that_cannot_be_read_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_facts, "CPUINFO", tmp_path / "missing")
    monkeypatch.setattr(run_facts.sys, "platform", "linux")
    run_facts.cpu_model.cache_clear()
    try:
        assert run_facts.cpu_model() is None
    finally:
        run_facts.cpu_model.cache_clear()


def test_the_cpu_model_is_read_from_macos_sysctl(monkeypatch) -> None:
    """macOS names its model in ``machdep.cpu.brand_string``, not in a file."""
    read: list[bytes] = []

    def sysctl(name: bytes) -> str:
        read.append(name)
        return "Apple M1 (Virtual)"

    monkeypatch.setattr(run_facts, "sysctl_string", sysctl)
    monkeypatch.setattr(run_facts.sys, "platform", "darwin")
    run_facts.cpu_model.cache_clear()
    try:
        assert run_facts.cpu_model() == "Apple M1 (Virtual)"
        assert read == [run_facts.BRAND_SYSCTL]
    finally:
        run_facts.cpu_model.cache_clear()


def test_the_sysctl_read_is_the_one_macos_gives() -> None:
    """On macOS, what ``sysctlbyname`` reads is what ``sysctl -n`` prints; with no libSystem, as
    on Linux, nothing is read. A sysctl that does not exist reads as None on both."""
    got = run_facts.sysctl_string(run_facts.BRAND_SYSCTL)
    if sys.platform == "darwin":
        printed = subprocess.run(
            ["sysctl", "-n", run_facts.BRAND_SYSCTL.decode()],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert got and got == printed
    else:
        assert got is None
    assert run_facts.sysctl_string(b"no.such.sysctl") is None


def test_the_first_test_records_the_cpu_model_when_there_is_one(monkeypatch) -> None:
    """Off Linux and macOS there is no model to read, so a run there records none; this pins
    that a model, when read, is written beside the platform and the worker count."""

    class _Item:
        config = type("Config", (), {})()
        user_properties: list = []

    monkeypatch.setattr(run_facts, "cpu_model", lambda: "AMD EPYC 7763 64-Core Processor")
    item = _Item()
    item.user_properties = []
    run_facts._FirstTest().pytest_runtest_setup(item)
    assert (telemetry.CPU_PROPERTY, "AMD EPYC 7763 64-Core Processor") in item.user_properties
