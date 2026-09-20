"""The scanner measurement refuses mutable or mismatched checkout provenance."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import measure_scanner_scope


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "apps"
    repo.mkdir()
    _git(repo, "init", "-q")
    bundle = repo / "demo"
    bundle.mkdir()
    (bundle / "app.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.1.0",
                "displayName": "Demo",
                "description": "Scanner provenance fixture",
            }
        ),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_measurement_prints_the_validated_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _checkout(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")

    assert measure_scanner_scope.main(["measure_scanner_scope.py", str(repo), "--ref", "HEAD"]) == 0

    captured = capsys.readouterr()
    assert f"checkout          : {repo.resolve()}" in captured.out
    assert "requested ref     : HEAD" in captured.out
    assert f"validated commit  : {sha}" in captured.out
    assert "bundles scanned  : 1" in captured.out
    assert captured.err == ""


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_measurement_refuses_a_dirty_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _checkout(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "demo" / "README.md").write_text("uncommitted\n", encoding="utf-8")

    assert measure_scanner_scope.main(["measure_scanner_scope.py", str(repo), "--ref", "HEAD"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"checkout {repo.resolve()} at {sha} is dirty" in captured.err
    assert "demo/README.md" in captured.err


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_measurement_refuses_a_ref_other_than_head(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _checkout(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    (repo / "demo" / "README.md").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "second",
    )
    head = _git(repo, "rev-parse", "HEAD")

    assert (
        measure_scanner_scope.main(["measure_scanner_scope.py", str(repo), "--ref", previous]) == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"checkout HEAD {head}" in captured.err
    assert f"requested ref {previous!r} ({previous})" in captured.err
