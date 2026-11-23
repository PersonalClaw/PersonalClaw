"""A Code loop's merge that git refuses for anything but a content conflict says what git said.

Seen on a code loop: its owner approved merging a finished task, and the merge failed on a 0-byte
``.git/index.lock`` no git process held. The loop paused with "a git error, not a content
conflict" and named nothing, and git's own words were logged only below the gateway log's level,
so a Resume failed the same way until someone found the lock by hand. The same sentence hid every
merge git refuses before it starts, one over uncommitted changes in the workspace among them.

Now the merge result carries git's words (masked), the loop's question quotes them, and a lock file
git names that is older than any git step here runs is said to be left behind. A failed merge, and
a loop git step stopped at its bound, log a WARNING naming the step and the folder. And the
scheduler's status read takes no optional lock, so it never leaves one.

Real git against scratch repositories, in the style of ``tests/test_loop_worktree.py``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

import pytest

from personalclaw.loop import worktree as wt

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
    return tmp_path


def _commit(path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message],
        cwd=path,
        check=True,
    )


@pytest.fixture()
def repo(tmp_path):
    """A repository with an identity configured and one file, and a task's worktree with an edit."""
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=d, check=True)
    (d / "notes.txt").write_text("base\n")
    _commit(d, "init")
    path = wt.add_worktree(str(d), "t-feat")
    with open(os.path.join(path, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("the task's edit\n")
    return d


def _lock(repo, *, age: float):
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    then = time.time() - age
    os.utime(lock, (then, then))
    return lock


def test_a_merge_failing_on_a_stale_index_lock_says_so(repo, caplog):
    """🔴 Red before: ``MergeResult(ok=False, conflicts=[])`` with nothing of what git said, and a
    log line only at INFO."""
    _lock(repo, age=10 * 60)

    with caplog.at_level(logging.WARNING, logger="personalclaw.loop.worktree"):
        result = wt.merge_worktree(str(repo), "t-feat")

    assert (result.ok, result.conflicts) == (False, [])
    assert "index.lock" in result.error, result
    said = wt.why_not_merged(str(repo), result)
    assert said.startswith("Git said: ") and "index.lock" in said, said
    assert "A git lock file was left at .git/index.lock" in said, said
    assert "remove it, then resume" in said, said
    assert any("merge failed for t-feat" in r.getMessage() for r in caplog.records), caplog.records


def test_a_lock_a_git_may_still_hold_is_not_called_left_behind(repo):
    """A lock younger than any git step here runs may be a git at work: git's words only."""
    _lock(repo, age=1)

    result = wt.merge_worktree(str(repo), "t-feat")

    said = wt.why_not_merged(str(repo), result)
    assert "index.lock" in said and "left at" not in said, said


def test_a_merge_refused_over_uncommitted_changes_says_gits_sentence(repo):
    """🔴 Red before: "a git error", and nothing of the files it would have overwritten."""
    (repo / "notes.txt").write_text("an edit of hers, not committed\n")

    result = wt.merge_worktree(str(repo), "t-feat")

    assert (result.ok, result.conflicts) == (False, [])
    assert "would be overwritten" in result.error and "notes.txt" in result.error, result
    assert "left at" not in wt.why_not_merged(str(repo), result)


def test_a_merge_that_succeeds_or_conflicts_carries_no_error(repo):
    result = wt.merge_worktree(str(repo), "t-feat")
    assert result.ok and result.error == "" and wt.why_not_merged(str(repo), result) == ""


def test_a_git_step_stopped_at_its_bound_logs_a_warning_naming_it(repo, caplog, monkeypatch):
    """🔴 Red before: it was stopped with no log line, and a lock it held was left unexplained."""

    def _ran_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git merge", timeout=1)

    monkeypatch.setattr(wt.subprocess, "run", _ran_out)
    with caplog.at_level(logging.WARNING, logger="personalclaw.loop.worktree"):
        rc, _out = wt._git(
            str(repo), *wt._CONFIGURED_IDENTITY_ONLY, "merge", "--no-edit", "x", timeout=1
        )

    assert rc == 1
    [line] = [r.getMessage() for r in caplog.records if "was stopped after" in r.getMessage()]
    assert "loop git merge in " in line and str(repo) in line, line


def test_the_schedulers_status_read_takes_no_optional_lock(repo, monkeypatch):
    """🔴 Red before: ``git status`` could take the index lock to refresh the index."""
    seen: list[tuple[str, ...]] = []
    git = wt._git

    def _seen(workspace, *args, **kwargs):
        seen.append(args)
        return git(workspace, *args, **kwargs)

    monkeypatch.setattr(wt, "_git", _seen)
    wt.has_uncommitted_changes(str(repo))

    [args] = [a for a in seen if "status" in a]
    assert args[: args.index("status")] == ("--no-optional-locks",), args
