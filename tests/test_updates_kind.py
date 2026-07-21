"""Install-kind detection (plan 34 S4 T4.1, contract C1).

Four fixtures — one per InstallKind — pin the resolution order:
env (container/desktop) wins first, then a .git working tree => git, else pip.
Each test isolates the two env vars the classifier reads (monkeypatch.delenv)
so it never inherits the runner's real environment.
"""

from __future__ import annotations

import pytest

from personalclaw.dashboard.handlers import updates_kind
from personalclaw.dashboard.handlers.updates_kind import detect_install_kind


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)


def test_container_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Even with a git tree present, the container env marker takes precedence.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "container"


def test_desktop_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    assert detect_install_kind() == "desktop"


def test_env_kind_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "  Container ")
    assert detect_install_kind() == "container"


def test_unknown_env_kind_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # A junk value is ignored — resolution falls through to git/pip probing.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "banana")
    assert detect_install_kind() == "pip"


def test_git_when_project_dir_has_dot_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_worktree_dot_git_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # In a git worktree/submodule, .git is a FILE pointing at the real gitdir.
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_when_dot_git_in_monorepo_parent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Monorepo layout: the project dir is nested one level under the repo root
    # (which carries .git). The parent probe catches it.
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "PersonalClaw"
    nested.mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(nested))
    assert detect_install_kind() == "git"


def test_pip_when_no_env_no_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # A project dir with NO .git (e.g. an unpacked source dir) is not "git".
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "pip"


def test_pip_when_nothing_set() -> None:
    # No env markers, no project dir -> a plain wheel/uv/pipx install.
    assert detect_install_kind() == "pip"


def test_install_kind_literal_values() -> None:
    # Guard the contract's value set (C1 / C2 wire shape).
    assert updates_kind._ENV_KINDS == {"container", "desktop"}
