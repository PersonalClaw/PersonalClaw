"""An app's provider runs git through ``personalclaw.sdk.git`` the way core runs its own.

Imported as an app imports them, through ``personalclaw.sdk.*``: the apps that run git this way
(git-repo, git-sync, notes and spec-builder) live in the apps repository. Each test drives the
helpers the way such an app does, with a real git in a real repository: the argv and environment
it runs git with, the URL it checks before it runs, the words it shows when git was refused, what
git printed once it is masked, and the refusal of a git too old to run.
"""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from personalclaw.sdk.git import (
    GitTooOld,
    git_argv,
    git_env,
    git_problem,
    remote_refusal,
    talks_to_remote,
    transport_refusal,
)
from personalclaw.sdk.security import mask_child_output

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the planted programs are shell scripts")

#: The identity an app commits as, given on the command line as notes and git-sync give theirs.
_IDENTITY = ["-c", "user.name=An App", "-c", "user.email=app@example.com"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with one commit, under a HOME of its own with an empty git configuration."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    path = tmp_path / "repo"
    path.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "first"]):
        subprocess.run(git_argv(["-C", str(path), *_IDENTITY, *args]), env=git_env(), check=True)
    return path


def test_a_hook_the_repository_sets_does_not_run(repo, tmp_path) -> None:
    """An agent's shell can write the repository's ``.git``: a hook planted there stays unrun."""
    mark = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(mark))}\n", encoding="utf-8")
    hook.chmod(0o755)
    args = ["-C", str(repo), *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "second"]

    subprocess.run(["git", *args], env=git_env(), check=True)
    assert mark.exists(), "the control never ran the planted hook: the test is vacuous"
    mark.unlink()

    subprocess.run(git_argv(args), env=git_env(), check=True)
    assert not mark.exists(), "the app's git ran a hook its repository names"


def test_its_environment_is_the_child_allowlist_and_the_agent_only_for_a_remote(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token-for-the-allowlist")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    pull = ["-C", "/srv/notes", "pull", "--ff-only", "origin", "main"]
    status = ["-C", "/srv/notes", "status", "--porcelain"]

    assert talks_to_remote(pull) and not talks_to_remote(status)
    local, remote = git_env(remote=talks_to_remote(status)), git_env(remote=talks_to_remote(pull))

    assert "GITHUB_TOKEN" not in local and "GITHUB_TOKEN" not in remote
    assert "SSH_AUTH_SOCK" not in local
    assert remote["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
    assert local["GIT_TERMINAL_PROMPT"] == "0", "git never waits on a password prompt"


def test_a_remote_at_a_local_path_is_refused_in_the_apps_words(repo, tmp_path) -> None:
    """Checked before git runs for a URL the owner typed, and said the same way for a git that
    was refused anyway."""
    assert remote_refusal("https://git.example.com/notes.git") == ""
    assert remote_refusal("ssh://git@git.example.com/notes.git") == ""
    before = remote_refusal(str(repo))
    assert "Reach it over ssh or https instead." in before

    clone = subprocess.run(
        git_argv(["clone", "-q", str(repo), str(tmp_path / "clone")]),
        env=git_env(remote=True),
        capture_output=True,
        text=True,
    )

    assert clone.returncode != 0
    assert transport_refusal(clone.stderr) == before
    assert transport_refusal("fatal: repository not found") == ""


def test_what_git_printed_is_masked_before_the_app_shows_it(repo) -> None:
    """A commit message is whatever its author wrote: a login in a URL, an escape sequence."""
    message = "sync via http://ada:hunter2-pass@proxy.example.com:3128 \x1b[2Kdone"
    subprocess.run(
        git_argv(["-C", str(repo), *_IDENTITY, "commit", "-q", "--allow-empty", "-m", message]),
        env=git_env(),
        check=True,
    )
    shown = subprocess.run(
        git_argv(["-C", str(repo), "log", "-1", "--format=%B"]),
        env=git_env(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "hunter2-pass" in shown, "the control: git printed the login"
    masked = mask_child_output(shown)
    assert "hunter2-pass" not in masked
    assert "\x1b" not in masked and "\\x1b[2K" in masked
    assert masked.startswith("sync via http://")


def test_a_git_too_old_to_run_is_refused_by_name(tmp_path, monkeypatch) -> None:
    """What an app's doctor says before anything runs, and what its git call raises."""
    bin_dir = tmp_path / "old-git-bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\necho 'git version 2.11.0'\n", encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    problem = git_problem()
    assert problem.startswith(
        "PersonalClaw needs git 2.12 or newer, and this machine has git 2.11.0."
    ), problem

    with pytest.raises(GitTooOld) as caught:
        git_argv(["-C", str(tmp_path), "status"])
    assert str(caught.value) == problem
    assert isinstance(caught.value, OSError), "caught where a git that is not installed is caught"
