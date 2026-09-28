"""PersonalClaw's git refuses a git older than 2.12, and every refusal names the version it needs.

``net.git.git_argv`` gives git the settings that stop a repository's own configuration from
running a program. The newest of them, ``protocol.<name>.allow``, arrived in git 2.12: an older
git ignores it, so an ``ext::`` remote a repository names runs its command, and before 2.9 its
hooks run too (``core.hooksPath``). So an older git is refused before it runs, with the version
needed, the one found and what to do, and each surface that runs git says that in its own place.

Every test puts a stand-in ``git`` first on ``PATH``. It answers ``git version`` with the line the
test gives it and records every other run, so a refused git is one that recorded nothing.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from personalclaw.net import git as core_git
from personalclaw.net.git import GitTooOld, git_argv, git_problem, git_version

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in git is a shell script")


class StandInGit:
    """A ``git`` first on ``PATH`` that reports a version and records every other run."""

    def __init__(self, bin_dir: Path, ran: Path) -> None:
        self.path = bin_dir / "git"
        self._ran = ran
        self._builds = 0

    def says(self, version_line: str) -> None:
        """Install a build of the stand-in that answers ``git version`` with *version_line*."""
        self.path.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = version ]; then\n'
            f"  echo {shlex.quote(version_line)}\n"
            "  exit 0\n"
            "fi\n"
            f'echo "$*" >> {shlex.quote(str(self._ran))}\n',
            encoding="utf-8",
        )
        self.path.chmod(0o755)
        # A new build of the same path is a later modification time, whatever the file system's
        # clock resolution: what the version cache is keyed on.
        self._builds += 1
        stamp = self._builds * 1_000_000_000
        os.utime(self.path, ns=(stamp, stamp))

    def runs(self) -> list[str]:
        return self._ran.read_text(encoding="utf-8").splitlines() if self._ran.exists() else []


@pytest.fixture
def stand_in(tmp_path, monkeypatch) -> StandInGit:
    bin_dir = tmp_path / "stand-in-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return StandInGit(bin_dir, tmp_path / "ran")


@pytest.mark.parametrize(
    "says, found",
    [
        ("git version 2.11.4", "2.11.4"),
        ("git version 2.9.5", "2.9.5"),
        ("git version 1.8.3.1", "1.8.3.1"),
    ],
)
def test_a_git_older_than_2_12_is_refused_before_it_runs(stand_in, says, found) -> None:
    stand_in.says(says)

    with pytest.raises(GitTooOld) as caught:
        git_argv(["status", "--porcelain"])

    message = str(caught.value)
    assert message.startswith(
        f"PersonalClaw needs git 2.12 or newer, and this machine has git {found}. "
        "Install a newer git"
    ), message
    assert isinstance(caught.value, OSError), "caught where a git that is not installed is caught"
    assert git_problem() == message
    assert stand_in.runs() == [], "a refused git never ran"


def test_a_command_that_talks_to_a_remote_is_refused_before_it_reads_the_owners_settings(
    stand_in,
) -> None:
    """A fetch reads the owner's own sign-in settings with a git of its own first: that one does
    not run either."""
    stand_in.says("git version 2.11.0")

    with pytest.raises(GitTooOld):
        git_argv(["fetch", "--tags", "origin"])

    assert stand_in.runs() == []


@pytest.mark.parametrize(
    "says, version",
    [
        ("git version 2.12.0", (2, 12, 0)),
        ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
        ("git version 2.45.1.windows.1", (2, 45, 1)),
    ],
)
def test_git_2_12_and_newer_run(stand_in, says, version) -> None:
    stand_in.says(says)

    assert git_version() == version
    assert git_problem() == ""
    subprocess.run(
        git_argv(["status", "--porcelain"]), env=core_git.git_env(site="test"), check=True
    )
    assert stand_in.runs() and stand_in.runs()[-1].endswith("status --porcelain")


def test_a_new_build_of_git_is_read_again(stand_in) -> None:
    """The version is read once per build: installing a newer git clears the refusal without a
    restart."""
    stand_in.says("git version 2.11.0")
    assert "has git 2.11.0" in git_problem()

    stand_in.says("git version 2.43.0")
    assert git_version() == (2, 43, 0)
    assert git_problem() == ""


def test_a_git_that_reports_no_version_is_left_to_its_command(stand_in) -> None:
    """A version that cannot be read refuses nothing: the command fails on its own if that git
    is broken, and says so there."""
    stand_in.says("not a version line")

    assert git_version() is None
    assert git_problem() == ""
    assert git_argv(["status"])[0] == "git"


def test_no_git_on_path_is_left_to_the_spawn(tmp_path, monkeypatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert git_version() is None
    assert git_problem() == "git is not on PATH"
    with pytest.raises(FileNotFoundError):
        subprocess.run(git_argv(["status"]), env={"PATH": str(empty)}, check=False)


def test_the_updater_says_the_refusal_in_place_of_git(stand_in, tmp_path) -> None:
    """``personalclaw update`` prints what the failed step said, and that is the refusal."""
    from personalclaw import cli_server, self_update

    stand_in.says("git version 2.11.0")

    fetched = self_update.git_fetch_tags(str(tmp_path))

    assert fetched.returncode != 0
    assert "git 2.12 or newer" in cli_server._git_said(fetched)
    assert "has git 2.11.0" in cli_server._git_said(fetched)
    assert stand_in.runs() == []


def test_time_travel_is_off_and_its_history_says_why(stand_in, tmp_path) -> None:
    from personalclaw.durability import state_history

    stand_in.says("git version 2.11.0")

    assert state_history.git_available() is False
    root = state_history.roots(tmp_path)[0]
    with pytest.raises(state_history.HistoryError, match=r"git 2\.12 or newer"):
        state_history.commit(root, home=tmp_path)
    assert stand_in.runs() == []


def test_the_history_job_says_why_it_skipped(stand_in, monkeypatch) -> None:
    from personalclaw.durability import service

    monkeypatch.setattr(service, "_cfg", lambda: type("Cfg", (), {"time_travel": True})())
    stand_in.says("git version 2.11.0")

    result = service.run_history_commit()

    assert result.skipped.startswith("PersonalClaw needs git 2.12 or newer")


def test_a_loop_and_self_qa_see_no_git_they_can_use(stand_in, tmp_path) -> None:
    from personalclaw.loop import worktree as loop_worktree
    from personalclaw.selfqa.fix_branch import create_fix_branch

    stand_in.says("git version 2.11.0")

    assert loop_worktree.git_available() is False
    assert loop_worktree.can_parallelize(str(tmp_path)) is False
    made = create_fix_branch(tmp_path, "a" * 40, enabled=True)
    assert made.created is False
    assert made.reason.startswith("PersonalClaw needs git 2.12 or newer")
    assert stand_in.runs() == []


def test_the_doctor_names_both_versions(stand_in, capsys, monkeypatch) -> None:
    from personalclaw import cli_doctor

    stand_in.says("git version 2.11.0")

    lines = _doctor_dependencies(cli_doctor, capsys, monkeypatch)

    git_line = next(line for line in lines if line.lstrip().startswith("git:"))
    assert "❌" in git_line
    assert "is git 2.11.0; PersonalClaw needs git 2.12 or newer" in git_line
    assert any("Fix: install git 2.12 or newer" in line for line in lines)


def test_the_doctor_shows_the_version_of_a_git_it_can_use(stand_in, capsys, monkeypatch) -> None:
    from personalclaw import cli_doctor

    stand_in.says("git version 2.43.0")

    lines = _doctor_dependencies(cli_doctor, capsys, monkeypatch)

    git_line = next(line for line in lines if line.lstrip().startswith("git:"))
    assert "✅" in git_line and git_line.endswith("(git 2.43.0)")


class _StopAfterDependencies(Exception):
    pass


def _doctor_dependencies(cli_doctor, capsys, monkeypatch) -> list[str]:
    """The doctor's Dependencies block: its first rows, printed before it probes anything else.
    The first row after git is node's, which is where this stops it."""
    real_which = cli_doctor.shutil.which

    def which(name, *args, **kwargs):
        if name == "node":
            raise _StopAfterDependencies
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(cli_doctor.shutil, "which", which)
    with pytest.raises(_StopAfterDependencies):
        cli_doctor._doctor()
    return capsys.readouterr().out.splitlines()
