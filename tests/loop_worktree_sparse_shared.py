"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_loop_worktree_sparse.py``

Its checks:
``tests/rails/test_loop_worktree_sparse_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from personalclaw.loop import worktree as wt


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    """Worktrees under a temp config dir, and the per-process caches cleared — both
    ``_FILE_COUNT_CACHE`` and ``_TRACKED_DIRS_CACHE`` are keyed by workspace abspath, so
    a leftover entry from another test's tmp_path would answer for this one."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
    wt._FILE_COUNT_CACHE.clear()
    wt._TRACKED_DIRS_CACHE.clear()
    yield
    wt._FILE_COUNT_CACHE.clear()
    wt._TRACKED_DIRS_CACHE.clear()


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _repo(tmp_path, name="repo", extra: dict[str, str] | None = None) -> str:
    """A repo with three top-level dirs plus a root file, committed.

    The root file matters: cone mode keeps root-level files hydrated, which is what
    makes a scoped worktree still usable (a real task needs pyproject/Makefile), and a
    test that never checks it would not notice if that stopped being true.
    """
    d = tmp_path / name
    for sub in ("src", "docs", "web"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "src" / "app.py").write_text("print('app')\n")
    (d / "src" / "util.py").write_text("X = 1\n")
    (d / "docs" / "guide.md").write_text("# guide\n")
    (d / "web" / "main.ts").write_text("export const a = 1;\n")
    (d / "README.md").write_text("# root\n")
    for rel, body in (extra or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(d, "init", "-q")
    # The identity its owner configured, which every commit PersonalClaw makes here carries.
    _git(d, "config", "user.name", "t")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return str(d)


def _tree(path: str) -> set[str]:
    """Repo-relative files present in the WORKING TREE (not the index)."""
    out: set[str] = set()
    for root, dirs, files in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), path).replace(os.sep, "/"))
    return out
