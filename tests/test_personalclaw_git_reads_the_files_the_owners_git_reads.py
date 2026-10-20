"""PersonalClaw's git reads the configuration files the owner's own git reads.

``GIT_CONFIG_GLOBAL`` names the global file in place of ``~/.gitconfig``, ``GIT_CONFIG_SYSTEM`` the
system file, and ``GIT_CONFIG_NOSYSTEM`` turns every system file off, the one a git distribution
bundles included. ``git_env`` dropped all three, so a gateway started with them read other files
than the owner's git did, and signed in with the helpers named there: a test suite that points its
git at an empty global file and no system file still got the machine's own keychain helper
(``osxkeychain``, from the file Apple's git bundles) on the command line of every command that
talks to a remote.

What is read here is git's configuration and nothing else: no command in this file talks to a
remote, and no credential helper runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw.net.git import CONFIG_FILE_ENV, git_argv, git_env


def _helpers(argv: list[str]) -> list[str]:
    """The credential helpers *argv* signs in with: its ``credential.helper`` settings after the
    last empty one, which clears the list."""
    helpers: list[str] = []
    for flag, setting in zip(argv, argv[1:]):
        if flag != "-c" or not setting.lower().startswith("credential.helper="):
            continue
        value = setting.split("=", 1)[1]
        helpers = [*helpers, value] if value else []
    return helpers


def _config(path: Path, helper: str) -> Path:
    path.write_text(f"[credential]\n\thelper = {helper}\n", encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A scratch home whose ``.gitconfig`` names a helper of its own, and none of the variables
    under test set."""
    home = tmp_path / "home"
    home.mkdir()
    _config(home / ".gitconfig", str(home / "home-helper"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in CONFIG_FILE_ENV:
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GIT_CONFIG_GLOBAL", "/srv/example/gitconfig"),
        ("GIT_CONFIG_SYSTEM", "/srv/example/system-gitconfig"),
        ("GIT_CONFIG_NOSYSTEM", "1"),
    ],
)
def test_git_env_keeps_what_chooses_gits_configuration_files(home, monkeypatch, name, value):
    assert name not in git_env(site="t"), "the control: nothing set, nothing passed"
    monkeypatch.setenv(name, value)
    assert git_env(site="t")[name] == value
    assert git_env(site="t", remote=True)[name] == value


def test_the_owners_sign_in_comes_from_the_global_file_their_environment_names(
    home, tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    assert _helpers(git_argv(["ls-remote", "origin"])) == [
        str(home / "home-helper")
    ], "the control: with no GIT_CONFIG_GLOBAL, the home's .gitconfig is the global file"

    named = _config(tmp_path / "global", str(tmp_path / "named-helper"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(named))

    assert _helpers(git_argv(["ls-remote", "origin"])) == [str(tmp_path / "named-helper")]


def test_with_no_system_files_no_system_or_bundled_helper_signs_in(home, tmp_path, monkeypatch):
    """A system file the environment names is read as the owner's git reads it, and with
    ``GIT_CONFIG_NOSYSTEM`` no system file is — the one Apple's git bundles, which names the
    keychain helper, among them."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(_config(tmp_path / "system", "pc-fixture-helper")))
    assert "pc-fixture-helper" in _helpers(git_argv(["ls-remote", "origin"]))

    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    assert _helpers(git_argv(["ls-remote", "origin"])) == []
    assert _helpers(git_argv(["push", "origin", "main"])) == []
