"""The git settings file the environment names in place of the usual one is owner-only too.

Git reads the owner's settings from ``~/.gitconfig`` and the machine's from an ``etc/gitconfig``,
and ``owner_only`` fences both. ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` name another file
for git to read in their place, and that file is fenced the same way, read from the environment as
the other settings are (``owner_only.named_git_settings``): ``kind_of`` says it is git's, the
native file tools and the shell's screen refuse a change to it before anyone is asked, and the
macOS profile denies a write to it. A plain file beside it still writes, reading it is untouched,
and a value that names no file anyone keeps (the null device, a relative path) fences nothing.

Same harness as ``test_an_agent_changes_nothing_git_or_her_shell_runs_as_her.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_an_agent_changes_nothing_git_or_her_shell_runs_as_her import (  # noqa: F401 — fixture
    GITS,
    OWNERS,
    SETTINGS,
    _bash,
    _refusal,
    owner,
)

from personalclaw import owner_only

pytestmark = pytest.mark.security


@pytest.fixture
def named(owner, monkeypatch):  # noqa: F811 — the imported fixture, used by name
    """The owner's git settings kept in a dotfiles folder, and the machine's in a folder of their
    own, each named by its variable; the home folder an allowed working directory."""
    dot = owner.user / "dot"
    machine = owner.user / "tools" / "git"
    dot.mkdir()
    machine.mkdir(parents=True)
    own, system = dot / "gitconfig", machine / "system-gitconfig"
    own.write_text(SETTINGS)
    system.write_text(SETTINGS)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(own))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    allowed = {"agent": {"subagent_cwd_allowed_roots": [str(owner.user)]}}
    (owner.home / "config.json").write_text(json.dumps(allowed))
    return SimpleNamespace(own=own, system=system, beside=dot / "notes.txt", owner=owner)


def test_the_file_each_variable_names_is_gits(named) -> None:
    assert owner_only.kind_of(named.own) == owner_only.GIT
    assert owner_only.kind_of(named.system) == owner_only.GIT
    assert owner_only.kind_of(named.beside) == ""


def test_the_file_tools_refuse_a_change_to_it(named) -> None:
    for raw in ("~/dot/gitconfig", str(named.system)):
        refused = _refusal(named.owner, raw)
        assert refused is not None and refused.control == "owner_only", (raw, refused)
        assert GITS in str(refused) and OWNERS in str(refused), str(refused)
    assert _refusal(named.owner, "~/dot/notes.txt") is None


@pytest.mark.parametrize("which", ["own", "system"])
def test_the_shell_does_not_run_a_command_that_changes_it(named, which) -> None:
    path = getattr(named, which)
    result = _bash(named.owner, f"echo '[core]' >> {path}")
    assert not result.success
    assert f"Blocked: “{path}” is where git keeps" in result.error, result.error
    assert path.read_text() == SETTINGS


def test_the_shell_still_reads_it_and_writes_beside_it(named) -> None:
    for command in (f"cat {named.own}", f"echo hi > {named.beside}"):
        result = _bash(named.owner, command)
        assert result.success, (command, result.error)


def test_the_macos_profile_denies_a_write_to_it(named) -> None:
    from personalclaw.sandbox import _build_seatbelt_profile

    profile = _build_seatbelt_profile("standard")
    for path in (named.own, named.system):
        assert f'(deny file-write* (literal "{path}"))' in profile, path
    assert str(named.beside) not in profile


@pytest.mark.parametrize("value", ["/dev/null", "dot/gitconfig", ""])
def test_a_value_that_names_no_kept_file_fences_nothing(
    owner,  # noqa: F811 — the imported fixture, used by name
    monkeypatch,
    value,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", value)
    assert owner_only.kind_of("/dev/null") == ""
    result = _bash(owner, "echo hi > /dev/null")
    assert result.success, result.error
