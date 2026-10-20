"""The source-tree checks split from
``tests/test_owner_only_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import functools

import pytest
import source_index
from owner_only_census_shared import ROOT

from personalclaw import owner_only

SRC = ROOT / "src" / "personalclaw"
DOC = ROOT / "docs" / "architecture" / "security.md"


#: Every path an agent writes by that is held to the rule, by (file, function), with what it is.
WRITE_PATHS: dict[tuple[str, str], str] = {
    ("file_scope.py", "_owner_only"): "the native file tools' changes (FileScope.resolve)",
    ("acp/permission_authority.py", "screen_tool_call"): (
        "an agent CLI's own write, edit and patch, read on the files the call's input names"
    ),
    ("hooks.py", "HookManager.on_tool_call"): "a call's title or command, on every approval path",
    ("agents/native/builtin_tools.py", "NativeBuiltinToolProvider._bash_refusal"): (
        "the agent's shell"
    ),
    ("artifacts/source_files.py", "admitted"): "a file-backed artifact's every read and write",
    ("write_scope.py", "problem"): "an automation's files to change",
    ("sandbox.py", "_owner_only_targets"): "the macOS fence of the home's paths",
    ("sandbox.py", "_owners_own_fence"): "the macOS fence of the owner's own files",
}


#: A function each detector must not find asking: the control.
NOT_A_WRITE_PATH = ("file_scope.py", "pattern_refusal")


def _function(path: str, qualname: str) -> ast.AST:
    tree = ast.parse((SRC / path).read_text(encoding="utf-8"))
    scope: list[ast.AST] = [tree]
    for part in qualname.split("."):
        found = next(
            (
                node
                for parent in scope
                for node in ast.iter_child_nodes(parent)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == part
            ),
            None,
        )
        assert found is not None, f"{path}: {qualname} not found"
        scope = [found]
    return scope[0]


def _asks_the_rule(node: ast.AST) -> bool:
    """Whether a function reads ``owner_only``: a name or an import of it, or an attribute of it."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "owner_only":
            return True
        if isinstance(child, ast.ImportFrom) and (child.module or "").endswith("owner_only"):
            return True
        if isinstance(child, ast.ImportFrom) and any(a.name == "owner_only" for a in child.names):
            return True
    return False


def test_the_detector_finds_what_it_looks_for_and_only_that():
    assert _asks_the_rule(_function("file_scope.py", "_owner_only"))
    assert not _asks_the_rule(_function(*NOT_A_WRITE_PATH))


@pytest.mark.parametrize("site", sorted(WRITE_PATHS), ids=lambda s: f"{s[0]}::{s[1]}")
def test_every_write_path_asks_the_rule(site):
    path, qualname = site
    assert _asks_the_rule(_function(path, qualname)), f"{path}::{qualname} ({WRITE_PATHS[site]})"


def test_the_file_tools_ask_it_for_every_change():
    """The native file tools' one check of a path (``FileScope.resolve``) asks the rule's helper
    for a change, so ``write_file``, ``edit_file`` and every caller of the check are held to it."""
    resolve = _function("file_scope.py", "FileScope.resolve")
    calls = {
        child.func.id
        for child in ast.walk(resolve)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_owner_only" in calls


# ── the architecture doc ────────────────────────────────────────────────────────────────────


@functools.cache
def _doc_section() -> str:
    text = source_index.text(DOC)
    start = text.index("### What runs as the owner is owner-only (`owner_only.py`)")
    return text[start : text.index("\n### ", start + 1)]


def test_the_architecture_doc_names_every_entry():
    """What the doc says the rule holds is what it holds: each entry is named there. The entries
    are the rule's own; ``tests/test_owner_only_census.py`` holds the census's list to them."""
    section = _doc_section()
    names = [
        *owner_only.OWNER_ONLY_FILES,
        *(f"{name}/" for name in owner_only.OWNER_ONLY_DIRS),
        *owner_only.GIT_SETTINGS,
        f"{owner_only.GIT_HOOKS}/",
        "/".join(owner_only.GIT_REBASE_STEPS),
        owner_only.GIT_MODULES,
        "/".join(owner_only.GIT_SYSTEM_SETTINGS),
        "~/.gitconfig",
        "git/config",
        *owner_only.SHELL_STARTUP_FILES,
        "$ZDOTDIR",
        *owner_only.FISH_STARTUP_FILES,
        *(f"{name}/" for name in owner_only.FISH_STARTUP_DIRS),
    ]
    for name in names:
        assert f"`{name}`" in section, name
