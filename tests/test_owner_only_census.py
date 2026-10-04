"""The owner-only census: every place an agent may not change, and every path it writes by that is
held to them.

``owner_only`` is one rule with three kinds of place: PersonalClaw's own files in its home, git's
own settings and hook scripts, and the owner's shell startup files. This census lists every entry
of each kind as the rule declares it, and fails for one it does not list, so a new entry is named
here first. For each it checks that the rule recognises a path of that entry, that the
architecture doc names it (what the doc says the rule holds is what it holds), and that a path
which only shares a name is not one. Then it reads every write path that must ask the rule, and
fails for one that stops asking. Every detector is shown to find what it looks for before its
answer is trusted.
"""

from __future__ import annotations

import ast
import functools
import os
from pathlib import Path

import pytest

import personalclaw.config.loader as loader
from personalclaw import owner_only

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "personalclaw"
DOC = ROOT / "docs" / "architecture" / "security.md"

#: PersonalClaw's own, by name in its home: the files, then the folders.
HOME_FILES = ("config.json", "mcp.json")
HOME_FOLDERS = ("hooks", "agents", "grants")

#: Git's, as the rule declares them.
GIT_SETTINGS = ("config", "config.worktree", "commondir")
GIT_HOOKS = "hooks"
GIT_REBASE_STEPS = ("rebase-merge", "git-rebase-todo")
GIT_MODULES = ".gitmodules"
GIT_SYSTEM_SETTINGS = ("etc", "gitconfig")

#: The shell's, as the rule declares them.
SHELL_STARTUP_FILES = (
    ".profile",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_logout",
    ".bash_aliases",
    ".zshenv",
    ".zprofile",
    ".zshrc",
    ".zlogin",
    ".zlogout",
    ".kshrc",
    ".mkshrc",
    ".cshrc",
    ".tcshrc",
    ".login",
    ".logout",
)
ZSH_STARTUP_FILES = (".zshenv", ".zprofile", ".zshrc", ".zlogin", ".zlogout")
FISH_STARTUP_FILES = ("config.fish",)
FISH_STARTUP_DIRS = ("conf.d", "functions")

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


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A home folder with PersonalClaw's home in it, and a repository laid out as git lays one."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    repo = pc / "workspace" / "site"
    git = repo / ".git"
    for folder in (git / "hooks", git / "objects", git / "refs"):
        folder.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    bare = pc / "workspace" / "backup.git"
    for folder in (bare / "objects", bare / "refs"):
        folder.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    linked = pc / "workspace" / "draft"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {git}/worktrees/draft\n")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("ZDOTDIR", str(user / ".config" / "zsh"))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    return user, pc, repo


# ── the entries ─────────────────────────────────────────────────────────────────────────────


def test_the_census_lists_every_entry_the_rule_declares():
    assert owner_only.OWNER_ONLY_FILES == HOME_FILES
    assert owner_only.OWNER_ONLY_DIRS == HOME_FOLDERS
    assert owner_only.GIT_SETTINGS == GIT_SETTINGS
    assert owner_only.GIT_HOOKS == GIT_HOOKS
    assert owner_only.GIT_REBASE_STEPS == GIT_REBASE_STEPS
    assert owner_only.GIT_MODULES == GIT_MODULES
    assert owner_only.GIT_SYSTEM_SETTINGS == GIT_SYSTEM_SETTINGS
    assert owner_only.SHELL_STARTUP_FILES == SHELL_STARTUP_FILES
    assert owner_only.ZSH_STARTUP_FILES == ZSH_STARTUP_FILES
    assert owner_only.FISH_STARTUP_FILES == FISH_STARTUP_FILES
    assert owner_only.FISH_STARTUP_DIRS == FISH_STARTUP_DIRS


def _entries(user: Path, pc: Path, repo: Path) -> dict[str, list[Path]]:
    """A path of every entry, by the kind the rule must say it is."""
    git = repo / ".git"
    config = user / ".config"
    return {
        owner_only.HOME: [pc / name for name in HOME_FILES]
        + [pc / name / "x" for name in HOME_FOLDERS],
        owner_only.GIT: [git / name for name in GIT_SETTINGS]
        + [
            git / GIT_HOOKS / "pre-commit",
            git.joinpath(*GIT_REBASE_STEPS),
            repo / GIT_MODULES,
            pc / "workspace" / "draft" / ".git",
            pc / "workspace" / "backup.git" / GIT_HOOKS / "post-receive",
            git / "modules" / "theme" / "config",
            user / ".gitconfig",
            config / "git" / "config",
            Path("/opt/local").joinpath(*GIT_SYSTEM_SETTINGS),
        ],
        owner_only.SHELL: [user / name for name in SHELL_STARTUP_FILES]
        + [config / "zsh" / name for name in ZSH_STARTUP_FILES]
        + [config / "fish" / name for name in FISH_STARTUP_FILES]
        + [config / "fish" / name / "x.fish" for name in FISH_STARTUP_DIRS],
    }


def test_the_rule_knows_every_entry_for_the_kind_it_is(owner):
    for kind, paths in _entries(*owner).items():
        for path in paths:
            assert owner_only.kind_of(path) == kind, (kind, path)


def test_a_path_that_only_shares_a_name_is_none_of_them(owner):
    """The control: a project's own ``hooks/`` and ``config``, git's ignore rules, the home's
    workspace, and a shell's history are not owner-only."""
    user, pc, repo = owner
    for path in (
        repo / ".gitignore",
        repo / ".gitattributes",
        repo / ".git" / "info" / "exclude",
        repo / "src" / "hooks" / "use-notes.ts",
        repo / "config",
        pc / "workspace" / "notes.md",
        pc / "workspace" / "hooks" / "x",
        user / ".zsh_history",
        user / ".config" / "fish" / "fish_variables",
        user / "notes" / ".profile.bak",
    ):
        assert owner_only.kind_of(path) == "", path


@functools.cache
def _doc_section() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("### What runs as the owner is owner-only (`owner_only.py`)")
    return text[start : text.index("\n### ", start + 1)]


def test_the_architecture_doc_names_every_entry():
    """What the doc says the rule holds is what it holds: each entry is named there."""
    section = _doc_section()
    names = [
        *HOME_FILES,
        *(f"{name}/" for name in HOME_FOLDERS),
        *GIT_SETTINGS,
        f"{GIT_HOOKS}/",
        "/".join(GIT_REBASE_STEPS),
        GIT_MODULES,
        "/".join(GIT_SYSTEM_SETTINGS),
        "~/.gitconfig",
        "git/config",
        *SHELL_STARTUP_FILES,
        "$ZDOTDIR",
        *FISH_STARTUP_FILES,
        *(f"{name}/" for name in FISH_STARTUP_DIRS),
    ]
    for name in names:
        assert f"`{name}`" in section, name


# ── the write paths ─────────────────────────────────────────────────────────────────────────


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


def test_the_fence_and_the_rule_name_the_same_owners_files(owner):
    """The macOS profile is built from the rule's own list of the owner's files, so the two cannot
    name different ones: each of the owner's files in her home folder is in the fence."""
    from personalclaw.sandbox import _owners_own_fence

    user = os.path.abspath(str(owner[0]))
    files, _folders = _owners_own_fence()
    in_home = [
        os.path.abspath(path)
        for path, is_dir, _kind in owner_only.owners_own()
        if not is_dir and os.path.dirname(os.path.abspath(path)) == user
    ]
    assert in_home and set(in_home) <= set(files)
