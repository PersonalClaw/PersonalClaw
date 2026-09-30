"""The agent's shell refuses a command that shows what a credential folder under the home holds.

A command reading a file inside `~/.aws` or `~/.ssh` was refused, while one listing the folder
itself (`ls ~/.aws`, `find ~/.ssh`) ran and returned the names of the files in it. The folder is
protected, not only the files inside it: a command that names the folder itself, or a glob the
shell expands to it or to what it holds, is refused with the same sentence a read of a file inside
it gets. Using a key by its own path (`ssh -i ~/.ssh/key`) still runs, and a folder that merely has
one of those names in its own is not a credential folder.

Every home here is a scratch folder under the test's temporary directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.security import is_sensitive_bash_command

CREDENTIAL = "Blocked: command accesses sensitive credential path"


@pytest.fixture
def user(tmp_path, monkeypatch) -> Path:
    """A scratch user home holding a credential folder or two and ordinary folders beside them."""
    home = tmp_path / "user"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "config").write_text("[default]\nregion = example-1\n", encoding="utf-8")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("not a key\n", encoding="utf-8")
    (home / "src" / "ssh-helper").mkdir(parents=True)
    (home / "src" / "ssh-helper" / "README.md").write_text("helper\n", encoding="utf-8")
    (home / ".aws-notes").mkdir()
    (home / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "data"))
    return home


@pytest.mark.parametrize(
    "command",
    [
        "ls ~/.aws",
        "ls -la ~/.ssh",
        "ls ~/.ssh/",
        "ls $HOME/.aws",
        "ls {home}/.ssh",
        "ls ~/.SSH",
        "find ~/.ssh",
        "tree ~/.aws",
        "du -a ~/.ssh",
        "cd ~/.ssh && ls",
    ],
)
def test_listing_a_credential_folder_is_refused(command, user):
    assert is_sensitive_bash_command(command.format(home=user)) == CREDENTIAL, command


@pytest.mark.parametrize("command", ["ls -d ~/.a*", "ls ~/.*", "ls ~/.ssh/*"])
def test_a_glob_the_shell_expands_to_the_folder_or_into_it_is_refused(command, user):
    assert is_sensitive_bash_command(command) == CREDENTIAL, command


def test_a_linked_credential_folder_is_refused_by_both_its_names(user):
    """A dotfile manager makes `~/.ssh` a link to a folder of its own."""
    target = user / "dotfiles" / "ssh"
    target.mkdir(parents=True)
    (target / "id_ed25519").write_text("not a key\n", encoding="utf-8")
    (user / ".ssh" / "id_ed25519").unlink()
    (user / ".ssh").rmdir()
    (user / ".ssh").symlink_to(target, target_is_directory=True)
    assert is_sensitive_bash_command("ls ~/.ssh") == CREDENTIAL
    assert is_sensitive_bash_command(f"ls {target}") == CREDENTIAL


@pytest.mark.parametrize("command", ["cat ~/.aws/config", "cat ~/.ssh/id_ed25519"])
def test_reading_a_file_inside_is_still_refused(command, user):
    assert is_sensitive_bash_command(command) == CREDENTIAL, command


@pytest.mark.parametrize(
    "command",
    [
        "ls ~",
        "ls -la ~/Documents",
        "ls -la ~/src/ssh-helper",
        "find ~/src/ssh-helper -name '*.md'",
        "ls ~/.aws-notes",
        "du -sh ~/*",
        "ls .ssh",
        "ssh -i ~/.ssh/id_ed25519 host.example.com",
    ],
)
def test_ordinary_work_beside_those_folders_passes(command, user):
    """A look-alike name, the home itself, a glob the shell does not expand to a hidden folder, a
    project's own `.ssh` folder and a key used by its path all stay allowed."""
    project = user / "src" / "ssh-helper"
    (project / ".ssh").mkdir(exist_ok=True)
    assert is_sensitive_bash_command(command, cwd=project) is None, command


# ── through the agent's shell tool ─────────────────────────────────────────────────────────────


class _Spawns:
    def __init__(self, real) -> None:
        self.calls: list[tuple] = []
        self._real = real

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return await self._real(*args, **kwargs)


def _shell(user: Path, monkeypatch):
    from personalclaw import sandbox
    from personalclaw.agents.native import builtin_tools as bt

    spawns = _Spawns(sandbox.create_subprocess_limited)
    monkeypatch.setattr(sandbox, "create_subprocess_limited", spawns)
    provider = bt.NativeBuiltinToolProvider(cwd=user / "src", categories=bt.PLATFORM_CATEGORIES)
    return provider, spawns


def test_the_agents_shell_refuses_listing_the_folder_before_it_runs(user, monkeypatch):
    provider, spawns = _shell(user, monkeypatch)
    result = asyncio.run(provider._t_bash({"command": f"ls {user / '.ssh'}"}))
    assert not result.success
    assert result.error == CREDENTIAL
    assert "id_ed25519" not in (result.output or "") + (result.error or "")
    assert spawns.calls == [], "a refused command must not reach the spawn"


def test_the_agents_shell_lists_a_look_alike_project_folder(user, monkeypatch):
    provider, spawns = _shell(user, monkeypatch)
    result = asyncio.run(provider._t_bash({"command": f"ls {user / 'src' / 'ssh-helper'}"}))
    assert result.success, result.error
    assert "README.md" in (result.output or "")
    assert len(spawns.calls) == 1
