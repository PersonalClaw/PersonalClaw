"""The shipped pre-push aggregate reports every independent result in one run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    # ``python3`` is the sandbox's own (below), never the host's: a host whose ``python3`` is a
    # shim that cannot start would otherwise decide what the asset-licences gate does here.
    excluded = {"black", "isort", "flake8", "npm", "node", "npx", "python3"}
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
    # A python3 that runs: this test's own interpreter.
    (fakebin / "python3").symlink_to(sys.executable)
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
    assert "SUMMARY: 3 of 11 gate(s) FAILED" in result.stdout


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
        "stamp-web",
        "playwright-chromium",
        "render-smoke",
    ):
        row = next(line for line in result.stdout.splitlines() if line.startswith(name))
        assert "| SKIP" in row
        assert "| PASS" not in row
    assert "web toolchain (npm/node) not found" in result.stdout


# ── The asset-licences gate ─────────────────────────────────────────────────────────────────

CHECKER = REPO_ROOT / "scripts" / "check_asset_licenses.py"
#: A tree the licence check covers (``mobile/assets/``) that neither the lint half nor the
#: frontend chain owns, so the sandbox's failing lint stubs never run and the gate is alone.
RECORDED = "mobile/assets/recorded.txt"
_MANIFEST = (
    '{"schema_version": 1, "groups": [{"id": "fixtures", "source": "this repository",'
    ' "version": "repository-revision", "license": "MIT", "ownership": "project-owned",'
    f' "required_notice": "LICENSE", "files": ["{RECORDED}"]}}]}}\n'
)


def _row(stdout: str, name: str) -> str:
    return next(line for line in stdout.splitlines() if line.startswith(f"{name} "))


@pytest.fixture
def recorded(sandbox: Sandbox) -> Sandbox:
    """The sandbox with the real licence checker, a LICENSE and a manifest that records the one
    fixture the tree has, so the check passes on the base and only a planted file fails it."""
    shutil.copy2(CHECKER, sandbox.root / "scripts" / "check_asset_licenses.py")
    base = _commit(
        sandbox.root,
        "the licence record",
        {"LICENSE": "MIT\n", "ASSET_LICENSES.json": _MANIFEST, RECORDED: "recorded\n"},
    )
    _git("update-ref", "refs/remotes/origin/main", base, cwd=sandbox.root)
    sandbox.base = base
    return sandbox


def test_an_unrecorded_fixture_fails_the_push_with_the_checks_message(recorded: Sandbox) -> None:
    head = _commit(recorded.root, "a new fixture", {"mobile/assets/new.txt": "new\n"})

    result = recorded.run(head)

    assert result.returncode == 1, result.stdout
    assert "| FAIL" in _row(result.stdout, "asset-licenses")
    assert "asset-licenses FAIL:" in result.stdout
    # The checker's own words, so the push says which file and what to do.
    assert "asset mobile/assets/new.txt: missing from ASSET_LICENSES.json" in result.stdout
    assert "Record a new file in ASSET_LICENSES.json" in result.stdout
    assert "a tracked asset has no licence record" in result.stderr
    assert "SUMMARY: 1 of 11 gate(s) FAILED" in result.stdout


def test_a_renamed_fixture_is_checked_too(recorded: Sandbox) -> None:
    _git("mv", RECORDED, "mobile/assets/moved.txt", cwd=recorded.root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "move", cwd=recorded.root)
    head = _git("rev-parse", "HEAD", cwd=recorded.root)

    result = recorded.run(head)

    assert result.returncode == 1, result.stdout
    assert "asset mobile/assets/moved.txt: missing from ASSET_LICENSES.json" in result.stdout


def test_a_recorded_new_fixture_passes(recorded: Sandbox) -> None:
    manifest = _MANIFEST.replace(f'"{RECORDED}"', f'"{RECORDED}", "mobile/assets/new.txt"')
    head = _commit(
        recorded.root,
        "a new fixture, recorded",
        {"mobile/assets/new.txt": "new\n", "ASSET_LICENSES.json": manifest},
    )

    result = recorded.run(head)

    assert result.returncode == 0, result.stdout
    assert "| PASS" in _row(result.stdout, "asset-licenses")


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"notes.txt": "edited\n"}, id="an edited file"),
        pytest.param({"src/personalclaw/probe_data.txt": "new\n"}, id="a new file under src"),
    ],
)
def test_no_file_added_or_renamed_outside_src_skips_the_check(
    recorded: Sandbox, files: dict[str, str]
) -> None:
    # An unrecorded fixture already on the base would fail the check if it ran.
    _commit(recorded.root, "unrecorded on main", {"mobile/assets/old.txt": "old\n"})
    base = _git("rev-parse", "HEAD", cwd=recorded.root)
    _git("update-ref", "refs/remotes/origin/main", base, cwd=recorded.root)
    head = _commit(recorded.root, "no new asset", files)

    result = recorded.run(head)

    assert "| SKIP" in _row(result.stdout, "asset-licenses"), result.stdout
    assert "no file added or renamed outside src/" in result.stdout


_BROKEN_SHIM = "mise ERROR No version is set for shim: python3"


def test_a_python3_that_cannot_start_skips_the_check_and_says_why(recorded: Sandbox) -> None:
    """A version-manager shim with no version set is on PATH and exits non-zero. The gate must
    not run the check on it and report the shim's error as an asset failure: it skips, with the
    reason and what the probe printed, and the push goes through (CI still checks)."""
    shim = recorded.fakebin / "python3"
    shim.unlink()
    shim.write_text(f"#!/bin/sh\necho '{_BROKEN_SHIM}' >&2\nexit 127\n", encoding="utf-8")
    shim.chmod(0o755)
    # An unrecorded fixture: the check would FAIL it if a broken interpreter were taken as one.
    head = _commit(recorded.root, "a new fixture", {"mobile/assets/new.txt": "new\n"})

    result = recorded.run(head)

    assert result.returncode == 0, result.stdout + result.stderr
    row = _row(result.stdout, "asset-licenses")
    assert "| SKIP" in row and "| FAIL" not in row, row
    assert "asset-licenses SKIP:" in result.stdout
    assert "no working Python (.venv/bin/python or python3) runs here" in result.stdout
    # The probe's own output, so the reader sees why.
    assert _BROKEN_SHIM in result.stdout
    assert "python3 -c 'import sys' exited non-zero" in result.stdout
    assert "missing from ASSET_LICENSES.json" not in result.stdout


def test_the_repos_venv_python_is_preferred_over_python3(recorded: Sandbox) -> None:
    """With ``.venv/bin/python`` present it runs the check, even when ``python3`` is broken."""
    shim = recorded.fakebin / "python3"
    shim.unlink()
    shim.write_text(f"#!/bin/sh\necho '{_BROKEN_SHIM}' >&2\nexit 127\n", encoding="utf-8")
    shim.chmod(0o755)
    venv_bin = recorded.root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (recorded.root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    head = _commit(recorded.root, "a new fixture", {"mobile/assets/new.txt": "new\n"})

    result = recorded.run(head)

    assert result.returncode == 1, result.stdout
    assert "| FAIL" in _row(result.stdout, "asset-licenses")
    assert "command exited non-zero: .venv/bin/python scripts/check_asset_licenses.py" in (
        result.stdout
    )
    assert "asset mobile/assets/new.txt: missing from ASSET_LICENSES.json" in result.stdout
