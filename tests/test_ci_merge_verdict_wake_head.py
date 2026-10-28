"""merge-verdict.yml's decide step checks a contract run's wake head whole before main's controller
reads it. What is not a full commit id ends the step with nothing posted, and never reaches the
controller, so a value that starts with ``-`` is never read as one of its options. The step's own
shell runs here, as the runner runs it, with a stand-in for the controller.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "merge-verdict.yml").read_text())
DECIDE = "decide the verdict from the API"
HEAD = "0123456789abcdef0123456789abcdef01234567"
NOTICE = "::notice title=no head::the dispatch names no full commit id"


def _decide(tmp_path: Path, head: str) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """The step woken by a dispatch that names ``head``: the shell's result, its outputs' lines,
    and where the stand-in records the arguments it was started with, when it is."""
    [step] = [s for s in WORKFLOW["jobs"]["post"]["steps"] if s.get("name") == DECIDE]
    bin_dir, called, outputs = tmp_path / "bin", tmp_path / "called", tmp_path / "outputs"
    bin_dir.mkdir()
    stand_in = bin_dir / "python3"
    stand_in.write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" > '{called}'\n")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    outputs.write_text("")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GH_TOKEN": "",
        "RUN_ID": "",
        "CHECK_RUN_ID": "",
        "ACTION": "",
        "WAKE_HEAD": head,
        "APP_ID": "1",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SHA": "f" * 40,
        "GITHUB_OUTPUT": str(outputs),
        "RUNNER_TEMP": str(tmp_path),
    }
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return done, outputs.read_text().splitlines(), called


@pytest.mark.parametrize(
    "head",
    [
        "main",
        HEAD[:-1],
        HEAD.upper(),
        "g" * 40,
        "$(id)",
        f"{HEAD}; true",
        f"../../{HEAD}",
        f" {HEAD}",
        f"{HEAD}\n",
        "-x",
        "--run-id",
        f"--run-id\n{HEAD}",
        f"{HEAD}\n--run-id",
    ],
)
def test_a_dispatch_whose_head_is_no_full_commit_id_posts_nothing(
    head: str, tmp_path: Path
) -> None:
    done, outputs, called = _decide(tmp_path, head)
    assert done.returncode == 0, done.stderr
    assert not called.exists(), "the controller was started with a head the step should refuse"
    assert "post=false" in outputs
    assert done.stdout.splitlines() == [NOTICE], "the step echoes the dispatch's text"


def test_a_dispatch_that_names_a_full_commit_id_hands_it_to_the_controller(tmp_path: Path) -> None:
    """Not vacuous: the stand-in is the controller the step starts, and a good head reaches it."""
    done, outputs, called = _decide(tmp_path, HEAD)
    assert done.returncode == 0, done.stderr
    args = called.read_text().split("\0")[:-1]
    assert args[:4] == ["-m", "tools.ci", "merge-controller", "decide"], args
    assert args[args.index("--head") + 1] == HEAD
    assert "--start" not in args and "--run-id" not in args and "--waiting" not in args
    assert "post=false" not in outputs
