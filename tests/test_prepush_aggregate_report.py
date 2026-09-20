"""The shipped pre-push aggregate reports every independent result in one run."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_prepush.sh"
ZERO = "0" * 40

_IDENT = (
    "-c",
    "user.name=Gate Test",
    "-c",
    "user.email=gate@test.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(root: Path, message: str, files: dict[str, str]) -> str:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", message, cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


class Sandbox:
    def __init__(self, root: Path, base: str, fakebin: Path) -> None:
        self.root = root
        self.base = base
        self.fakebin = fakebin

    def push_line(self, head: str) -> str:
        return f"refs/heads/main {head} refs/heads/main {ZERO}\n"

    def run(self, head: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PATH"] = str(self.fakebin)
        return subprocess.run(
            ["sh", str(self.root / "scripts" / "run_prepush.sh")],
            cwd=self.root,
            input=self.push_line(head),
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )


def _mirror_path_without_gate_tools(dest: Path) -> None:
    excluded = {"black", "isort", "flake8", "npm", "node", "npx"}
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            contents = list(Path(entry).iterdir())
        except OSError:
            continue
        for tool in contents:
            if tool.name in excluded:
                continue
            link = dest / tool.name
            if link.exists() or link.is_symlink():
                continue
            try:
                if tool.is_dir() or not os.access(tool, os.X_OK):
                    continue
                link.symlink_to(tool)
            except OSError:
                continue


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    _git("init", "-q", "-b", "main", cwd=root)
    base = _commit(root, "base", {"notes.txt": "base\n"})
    _git("update-ref", "refs/remotes/origin/main", base, cwd=root)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _mirror_path_without_gate_tools(fakebin)
    for tool in ("black", "isort", "flake8"):
        stub = fakebin / tool
        stub.write_text(
            f"#!/bin/sh\nprintf '{tool} failed\\n'\nexit 1\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return Sandbox(root, base, fakebin)


def test_three_independent_failures_all_surface_in_one_run(sandbox: Sandbox) -> None:
    head = _commit(
        sandbox.root,
        "python change",
        {"src/personalclaw/probe.py": "PROBE = True\n"},
    )

    result = sandbox.run(head)

    assert result.returncode != 0
    assert result.stdout.count("Gate                  | Result | Failures") == 1
    for name in ("black", "isort", "flake8"):
        assert f"{name}" in result.stdout
        assert f"{name} FAIL:" in result.stdout
        assert f"{name} failed" in result.stdout
    assert "SUMMARY: 3 of 9 gate(s) FAILED" in result.stdout


def test_an_absent_toolchain_is_reported_as_skipped_not_passed(
    sandbox: Sandbox,
) -> None:
    head = _commit(
        sandbox.root,
        "frontend change",
        {"web/probe.ts": "export const probe = true;\n"},
    )

    result = sandbox.run(head)

    assert result.returncode == 0
    for name in (
        "npm-ci",
        "typecheck-web",
        "test-web",
        "build-web",
        "playwright-chromium",
        "render-smoke",
    ):
        row = next(line for line in result.stdout.splitlines() if line.startswith(name))
        assert "| SKIP" in row
        assert "| PASS" not in row
    assert "web toolchain (npm/node) not found" in result.stdout
