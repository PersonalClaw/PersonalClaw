"""Pytest must fail before collection when ``personalclaw`` comes from another worktree."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_conftest_refuses_a_package_imported_from_another_worktree(tmp_path: Path) -> None:
    invoking = tmp_path / "invoking-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(invoking), "HEAD"],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    # The branch's first commit does not exist yet when this regression runs locally, so
    # the new worktree's committed conftest is necessarily the pre-change one. Exercise
    # the current guard in the real worktree rather than requiring a commit before tests.
    shutil.copy2(_REPO / "tests" / "conftest.py", invoking / "tests" / "conftest.py")
    probe = invoking / "tests" / "_provenance_probe.py"
    probe.write_text("def test_probe():\n    assert True\n", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(_REPO / "src"), str(invoking / "tests")))
    pytest_args = [
        "-n0",
        "--no-cov",
        "--collect-only",
        "-q",
        str(probe.relative_to(invoking)),
    ]
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                ("import personalclaw, pytest; " f"raise SystemExit(pytest.main({pytest_args!r}))"),
            ],
            cwd=invoking,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(invoking)],
            cwd=_REPO,
            check=True,
            capture_output=True,
            text=True,
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "imported personalclaw from outside the invoking repository root" in output
    assert f"imported package root: {(_REPO / 'src' / 'personalclaw').resolve()}" in output
    assert f"invoking repository root: {invoking.resolve()}" in output
