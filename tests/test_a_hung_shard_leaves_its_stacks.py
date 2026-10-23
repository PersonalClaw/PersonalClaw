"""A suite leg that runs to its cap leaves every process's stacks in its log (#3905, red (g)).

The rules are ``tests/hang_stacks.py``. The deadline is the catalogue's own cap less the margin,
found by the run's ``--junitxml``; and a planted run on "GitHub Actions", through this suite's
conftest with the deadline planted at two seconds, writes the stuck test's frames to the stderr
pytest started with, from the controller and from an xdist worker, and goes on to pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import hang_stacks
import pytest

from tools.ci import catalogue

_TESTS_DIR = Path(__file__).resolve().parent

_PLANT = """
    import hang_stacks

    hang_stacks.deadline = lambda xmlpath, jobs: 2.0
"""

_STUCK = """
    import time


    def test_stays_past_the_deadline():
        time.sleep(4)
"""


def _cap(job_id: str) -> int:
    [job] = [j for j in catalogue.read().jobs if j.id == job_id]
    return job.timeout_min * 60


def test_the_deadline_is_each_legs_cap_less_the_margin() -> None:
    jobs = catalogue.read().jobs
    shard = hang_stacks.deadline("reports/junit-test-shard-2.xml", jobs)
    assert shard == _cap("python-suite") - hang_stacks.MARGIN_SECS
    macos = hang_stacks.deadline("reports/junit-test-macos-3.xml", jobs)
    assert macos == _cap("python-suite-macos") - hang_stacks.MARGIN_SECS
    assert hang_stacks.deadline("reports/someone-elses.xml", jobs) is None
    assert hang_stacks.deadline(None, jobs) is None


def test_nothing_is_armed_off_github_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert hang_stacks.configure(object()) is None


def test_a_tree_without_tools_ci_arms_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child pytest on a tree of its own, under ``GITHUB_ACTIONS=true``, has no ``tools.ci``
    to import: configure answers None rather than failing the run at startup (#4778)."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setitem(sys.modules, "tools.ci", None)
    monkeypatch.setitem(sys.modules, "tools.ci.catalogue", None)
    assert hang_stacks.configure(object()) is None


def _run(tmp_path: Path, *args: str, actions: bool) -> subprocess.CompletedProcess[str]:
    (tmp_path / "plant_deadline.py").write_text(textwrap.dedent(_PLANT))
    (tmp_path / "test_stuck.py").write_text(textwrap.dedent(_STUCK))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env.pop("GITHUB_ACTIONS", None)
    if actions:
        env["GITHUB_ACTIONS"] = "true"
    paths = [str(tmp_path), str(_TESTS_DIR), str(_TESTS_DIR.parent), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(paths).rstrip(os.pathsep)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "plant_deadline",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "--junitxml=reports/junit-test-shard-1.xml",
            *args,
            "test_stuck.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize("workers", [(), ("-n", "1")], ids=["one process", "an xdist worker"])
def test_a_run_past_its_deadline_writes_the_stuck_tests_stack_and_goes_on(
    tmp_path: Path, workers: tuple[str, ...]
) -> None:
    proc = _run(tmp_path, *workers, actions=True)
    out = proc.stdout[-3000:] + proc.stderr[-6000:]
    assert proc.returncode == 0, out
    assert "1 passed" in proc.stdout, out
    assert "Timeout (0:00:02)!" in proc.stderr and "most recent call first" in proc.stderr, out
    assert "test_stays_past_the_deadline" in proc.stderr, out


def test_a_run_off_github_actions_writes_no_stack(tmp_path: Path) -> None:
    proc = _run(tmp_path, actions=False)
    out = proc.stdout[-3000:] + proc.stderr[-3000:]
    assert proc.returncode == 0, out
    assert "most recent call first" not in proc.stderr, out
