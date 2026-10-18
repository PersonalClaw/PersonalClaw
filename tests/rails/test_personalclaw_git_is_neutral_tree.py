"""The source-tree checks split from
``tests/test_personalclaw_git_is_neutral.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import os
import shutil

import pytest
import source_index
from personalclaw_git_is_neutral_shared import _argv_kind, _bound_argvs

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(os.name == "nt", reason="the planted programs are POSIX shell scripts"),
]


#: Clones into a directory PersonalClaw has just made. Their configuration is what git writes
#: for the clone, so nothing an agent wrote reaches them; and a Store source may be a local
#: path, which the neutral settings refuse.
_FRESH_CLONE = {
    "apps/catalog.py::_read_git_registry::subprocess.run",
    "apps/catalog.py::_scan_git_source::subprocess.run",
    "apps/source.py::_clone_git::subprocess.run",
    # Its own, stricter settings: no configuration file at all, HTTPS only, no credentials.
    "net/git.py::run_git_guarded::subprocess.run",
}


def _git_spawn_census() -> dict[str, set[str]]:
    """``file::qualname::callee`` → the kinds of git argv its spawn calls use."""
    from spawn_ceiling_audit_shared import _SPAWN_CALLEES, _callee, _normalize, _src_root

    root = _src_root()
    out: dict[str, set[str]] = {}
    for path, tree in source_index.package_trees():
        rel = path.relative_to(root).as_posix()

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []
                self.bound: list[dict[str, str]] = [{}]

            def visit_FunctionDef(self, n) -> None:
                self.q.append(n.name)
                self.bound.append({**self.bound[-1], **_bound_argvs(n)})
                self.generic_visit(n)
                self.bound.pop()
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, n) -> None:
                self.q.append(n.name)
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                callee = _normalize(_callee(n))
                if callee in _SPAWN_CALLEES and n.args:
                    kind = _argv_kind(n.args[0], self.bound[-1])
                    if kind:
                        key = f"{rel}::{'.'.join(self.q) or '<module>'}::{callee}"
                        out.setdefault(key, set()).add(kind)
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_every_git_personalclaw_runs_is_neutral_but_a_fresh_clone():
    census = _git_spawn_census()
    raw = {key for key, kinds in census.items() if "raw" in kinds}
    assert raw == _FRESH_CLONE, (
        "a git spawn builds its argv by hand. Use `net.git.git_argv(args)` so a repository's own "
        f"configuration cannot run a program as the gateway:\n  {sorted(raw - _FRESH_CLONE)}\n"
        f"no longer a raw git spawn (drop it from _FRESH_CLONE): {sorted(_FRESH_CLONE - raw)}"
    )


def test_the_git_census_sees_the_neutral_sites():
    """Non-vacuity: the census finds the call sites that run git through ``git_argv``."""
    neutral = {key for key, kinds in _git_spawn_census().items() if "neutral" in kinds}
    for key in (
        "durability/state_history.py::_git::subprocess.run",
        "self_update.py::_run_git::subprocess.run",
        "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec",
        "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec",
        "loop/worktree.py::_git::subprocess.run",
        "selfqa/fix_branch.py::_git::subprocess.run",
        "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec",
        "workflows/review_service.py::_git::asyncio.create_subprocess_exec",
        "triggers/liveness.py::_dirty_git_active::subprocess.run",
        "cli_doctor.py::_git_is_inside_work_tree::subprocess.run",
    ):
        assert key in neutral, key
