"""The source-tree checks split from
``tests/test_private_home_file_modes.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import private_home_file_modes_shared
import source_index
from rails_repo import REPO

_umask = private_home_file_modes_shared._umask


SRC = REPO / "src" / "personalclaw"


def _sources() -> tuple[tuple[Path, ast.Module], ...]:
    """Every module under ``src/``, parsed, from the suite's one source index."""
    return tuple(source_index.package_trees())


def _called_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _literal_mode(call: ast.Call) -> int | None:
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, int):
                return kw.value.value
    return None


def _opens_for_writing(call: ast.Call, *, mode_index: int) -> bool:
    mode = call.args[mode_index] if len(call.args) > mode_index else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and any(ch in str(mode.value) for ch in "wax+")


#: How source spells the settings files that carry secrets or their references: ``config.json``
#: (``config_path()``), ``mcp.json`` and the agent config it is copied into, whose MCP ``env``
#: blocks hold tokens (``agents_dir() / AGENT_FILENAME``, ``_installed_agent_config()``).
_SECRET_SETTINGS_CALLS = {"config_path", "_installed_agent_config"}


_SECRET_SETTINGS_LEAVES = {"config.json", "mcp.json", "personalclaw.json", "AGENT_FILENAME"}


def _names_a_secret_settings_file(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Call):
        return _called_name(expr) in _SECRET_SETTINGS_CALLS
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        leaf = expr.right
        if isinstance(leaf, ast.Constant):
            return leaf.value in _SECRET_SETTINGS_LEAVES
        if isinstance(leaf, ast.Name):
            return leaf.id in _SECRET_SETTINGS_LEAVES
    return False


def test_secret_bearing_settings_files_are_written_only_through_atomic_write():
    """``config.json``, ``mcp.json`` and the agent config hold secrets or their references, so they
    are written the one way that makes a file private and whole. Six writes went around that
    with a bare ``write_text``: ``cli_chat`` CREATED ``config.json`` that way on a fresh install
    (0644); the agent-settings handlers rewrote ``config.json`` and the agent config in place (a
    crash mid-write truncating it); ``doctor``'s auto-fix rewrote the agent config in place,
    keeping whatever mode an older release had left it at; and the eval overlay wrote a cell
    home's ``config.json`` the same way.

    A name a function binds to one of those paths is the file it is about to touch; any
    ``write_text``/``write_bytes``/write-mode ``open`` on that name is an offender."""
    offenders: set[str] = set()
    for path, tree in _sources():
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {
                target.id
                for node in ast.walk(func)
                if isinstance(node, ast.Assign) and _names_a_secret_settings_file(node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if not names:
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                func_ = node.func
                if isinstance(func_, ast.Attribute) and isinstance(func_.value, ast.Name):
                    if func_.value.id in names and (
                        func_.attr in {"write_text", "write_bytes"}
                        or (func_.attr == "open" and _opens_for_writing(node, mode_index=0))
                    ):
                        offenders.add(f"{path.relative_to(SRC)}:{node.lineno}")
                elif isinstance(func_, ast.Name) and func_.id == "open" and node.args:
                    target = node.args[0]
                    if (
                        isinstance(target, ast.Name)
                        and target.id in names
                        and _opens_for_writing(node, mode_index=1)
                    ):
                        offenders.add(f"{path.relative_to(SRC)}:{node.lineno}")
    assert sorted(offenders) == []


def test_no_source_hands_the_atomic_writers_a_group_or_world_mode():
    """Static leg of the rail: a literal wide mode is a bug even on a path no test drives."""
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"atomic_write", "atomic_write_bytes", "_atomic_write"}:
                continue
            mode = _literal_mode(node)
            if mode is not None and mode & 0o077:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} mode={oct(mode)}")
    assert offenders == []
