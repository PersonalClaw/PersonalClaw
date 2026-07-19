"""Tests for the git worktree manager backing parallel task execution.

Exercises real git against a temp repo: capability detection, base-commit
bootstrap on a fresh init, worktree add/merge/remove, and the no-git fallback.
Skipped entirely if git isn't installed.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from personalclaw.loop import worktree as wt

pytestmark = pytest.mark.skipif(not wt.git_available(), reason="git not installed")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    """Root worktrees under a temp config dir (PClaw's working dir), NOT inside the
    test repo — mirrors production where worktrees live outside the user's checkout."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
    return tmp_path


def _init_repo(path: str, *, commit: bool = True) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if commit:
        (open(os.path.join(path, "README.md"), "w")).write("# repo\n")
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
            cwd=path, check=True,
        )


class TestCapability:
    def test_non_repo_dir_is_not_parallelizable(self, tmp_path):
        d = tmp_path / "plain"; d.mkdir()
        assert wt.is_git_repo(str(d)) is False
        assert wt.can_parallelize(str(d)) is False

    def test_repo_dir_is_parallelizable(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        assert wt.is_git_repo(str(d)) is True
        assert wt.can_parallelize(str(d)) is True

    def test_empty_path_not_parallelizable(self):
        assert wt.can_parallelize("") is False


class TestWorktreeLifecycle:
    def test_ensure_base_commit_on_fresh_init(self, tmp_path):
        d = tmp_path / "fresh"; d.mkdir()
        _init_repo(str(d), commit=False)  # unborn HEAD
        assert wt.ensure_base_commit(str(d)) is True
        # HEAD now resolves
        rc = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(d)).returncode
        assert rc == 0

    def test_add_and_remove_worktree(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-abc")
        assert path and os.path.isdir(path)
        assert os.path.basename(path) == "t-abc"
        # worktrees must live OUTSIDE the user's workspace (under PClaw's dir), so a
        # parallel run never pollutes the checkout — the vision's explicit rule.
        assert not os.path.abspath(path).startswith(os.path.abspath(str(d)) + os.sep)
        assert not os.path.isdir(os.path.join(str(d), ".pclaw-worktrees"))
        # it's a real linked worktree
        listing = subprocess.run(["git", "worktree", "list"], cwd=str(d), capture_output=True, text=True).stdout
        assert "t-abc" in listing
        wt.remove_worktree(str(d), "t-abc")
        assert not os.path.isdir(path)

    def test_unsafe_task_id_is_refused(self, tmp_path):
        # Defense-in-depth: task ids are the last path segment of a worktree dir +
        # part of a branch ref. A traversal / glob / separator id must never build a
        # path — worktree_path raises, and the public ops fail safe (None / no-op /
        # False) rather than escape the worktrees root.
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        for bad in ["../escape", "a/b", "x*", "..", "with space", ""]:
            with pytest.raises(ValueError):
                wt.worktree_path(str(d), bad)
            assert wt.add_worktree(str(d), bad) is None
            assert wt.branch_exists(str(d), bad) is False
            wt.remove_worktree(str(d), bad)  # must not raise
            assert wt.merge_worktree(str(d), bad).ok is False
        # nothing got created outside the (never-built) worktrees root
        assert not os.path.isdir(tmp_path / "pclaw" / "escape")

    def test_per_project_root_isolates_shared_workspace(self, tmp_path):
        # Projects native entity: with a project_id, worktrees live under
        # projects/<id>/worktrees — so two projects on the SAME workspace get isolated
        # roots and one's cleanup can't wipe the other's worktrees.
        d = tmp_path / "shared"; d.mkdir()
        _init_repo(str(d))
        pa = wt.add_worktree(str(d), "t-1", "p-aaaa1111")
        pb = wt.add_worktree(str(d), "t-2", "p-bbbb2222")
        assert pa and pb
        assert "/projects/p-aaaa1111/worktrees/" in pa.replace(os.sep, "/")
        assert "/projects/p-bbbb2222/worktrees/" in pb.replace(os.sep, "/")
        # tearing down project A leaves project B's worktree intact
        wt.cleanup_all(str(d), "p-aaaa1111")
        assert not os.path.isdir(pa)
        assert os.path.isdir(pb)

    def test_legacy_root_when_no_project(self, tmp_path):
        # No project_id → the legacy workspace-hash root (back-compat path).
        d = tmp_path / "repo2"; d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-x")
        assert path and "/code/worktrees/" in path.replace(os.sep, "/")

    def test_merge_worktree_brings_changes_back(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-feat")
        # do work in the worktree
        open(os.path.join(path, "feature.txt"), "w").write("hello from the task\n")
        assert wt.merge_worktree(str(d), "t-feat").ok is True
        # the file is now in the base checkout, and the worktree is gone
        assert os.path.isfile(os.path.join(str(d), "feature.txt"))
        assert not os.path.isdir(path)

    def test_branch_exists_tracks_add_merge_remove(self, tmp_path):
        # branch_exists is how the scheduler tells a done-but-unmerged task (its
        # branch lingers after a conflicted/aborted merge) apart from a fully reaped
        # one — so it can retry the merge on resume instead of skipping it forever.
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        assert wt.branch_exists(str(d), "t-x") is False     # no branch yet
        path = wt.add_worktree(str(d), "t-x")
        open(os.path.join(path, "x.txt"), "w").write("work\n")
        assert wt.branch_exists(str(d), "t-x") is True      # branch created
        assert wt.merge_worktree(str(d), "t-x").ok is True
        assert wt.branch_exists(str(d), "t-x") is False     # gone after clean merge+remove

    def test_branch_survives_a_conflicted_merge(self, tmp_path):
        # A genuine conflict leaves the branch in place (the work isn't lost) so the
        # scheduler can retry once the user resolves it — branch_exists proves it.
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        open(os.path.join(str(d), "f.txt"), "w").write("base\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"], cwd=str(d), check=True)
        path = wt.add_worktree(str(d), "t-conf")
        open(os.path.join(path, "f.txt"), "w").write("task edit\n")  # conflicting edit
        # diverge base on the same line
        open(os.path.join(str(d), "f.txt"), "w").write("base edit\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "div"], cwd=str(d), check=True)
        result = wt.merge_worktree(str(d), "t-conf")
        assert result.ok is False
        # The conflicted paths must be reported in the result — captured BEFORE the
        # abort. (The bug: the caller re-probed conflict_paths AFTER merge_worktree
        # aborted, always reading empty → every real conflict misreported as a
        # 'git error, not a content conflict'.)
        assert "f.txt" in result.conflicts
        assert wt.branch_exists(str(d), "t-conf") is True   # branch kept for retry

    def test_merge_commit_succeeds_without_git_identity(self, tmp_path, monkeypatch):
        # Clean-container case: a freshly git-init'd workspace with NO user.name/
        # user.email anywhere. A non-fast-forward merge creates a MERGE COMMIT that
        # needs an identity — merge_worktree must supply its own (-c flags) so the
        # task doesn't falsely wedge as a conflict. Scrub all git identity sources.
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            monkeypatch.delenv(var, raising=False)
        d = tmp_path / "repo"; d.mkdir()
        # base commit using the same isolated identity ensure_base_commit uses
        subprocess.run(["git", "init", "-q"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=PersonalClaw", "-c", "user.email=code@personalclaw.local",
                        "commit", "-q", "--allow-empty", "-m", "base"], cwd=str(d), check=True)
        # create the worktree FIRST (branches from current HEAD), then diverge base —
        # so the branch can't fast-forward and the merge MUST create a merge commit.
        path = wt.add_worktree(str(d), "t-feat")
        assert path
        open(os.path.join(path, "feature.txt"), "w").write("task work\n")
        # diverge base after the worktree branched
        open(os.path.join(str(d), "base.txt"), "w").write("base change\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=PersonalClaw", "-c", "user.email=code@personalclaw.local",
                        "commit", "-q", "-m", "base diverge"], cwd=str(d), check=True)
        # merge must succeed (true) — would fail "unable to auto-detect email" without the fix
        assert wt.merge_worktree(str(d), "t-feat").ok is True
        assert os.path.isfile(os.path.join(str(d), "feature.txt"))

    def test_add_worktree_idempotent(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        p1 = wt.add_worktree(str(d), "t-x")
        p2 = wt.add_worktree(str(d), "t-x")
        assert p1 == p2
        wt.remove_worktree(str(d), "t-x")

    def test_cleanup_all_removes_worktrees_dir(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        p1 = wt.add_worktree(str(d), "t-1")
        wt.add_worktree(str(d), "t-2")
        # the worktrees root is PClaw-owned (outside the workspace) and is removed
        root = os.path.dirname(p1)
        assert os.path.isdir(root)
        wt.cleanup_all(str(d))
        assert not os.path.isdir(root)
        # and nothing was ever created inside the user's checkout
        assert not os.path.isdir(os.path.join(str(d), ".pclaw-worktrees"))

    def test_cleanup_all_sweeps_orphan_task_branch(self, tmp_path):
        # A pclaw/task-* branch whose worktree dir is already gone (merged, or a prior
        # failed branch-delete) must still be swept — else it`s orphaned in the user`s
        # brownfield repo after the project is deleted.
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        wt.add_worktree(str(d), "t-orphan")
        # Detach the worktree dir but leave the branch behind (simulates the orphan).
        subprocess.run(["git", "worktree", "remove", "--force", wt.worktree_path(str(d), "t-orphan")],
                       cwd=str(d), check=True)
        branch = wt.branch_name("t-orphan")
        rc, out = wt._git(str(d), "for-each-ref", "--format=%(refname:short)", "refs/heads/")
        assert branch in out  # branch still present before cleanup
        wt.cleanup_all(str(d))
        rc2, out2 = wt._git(str(d), "for-each-ref", "--format=%(refname:short)", "refs/heads/")
        assert branch not in out2  # swept


class TestConflictDetection:
    def test_clean_repo_has_no_conflict_paths(self, tmp_path):
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        assert wt.conflict_paths(str(d)) == []

    def test_real_conflict_is_detected_and_merge_returns_false(self, tmp_path):
        # Two branches edit the SAME line → a genuine content conflict. merge_worktree
        # returns False AND conflict_paths lists the file (so the watchdog can show an
        # accurate "conflict in X" message rather than guessing).
        d = tmp_path / "repo"; d.mkdir()
        _init_repo(str(d))
        f = os.path.join(str(d), "shared.txt")
        open(f, "w").write("line one\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"], cwd=str(d), check=True)
        # worktree edits the shared line
        path = wt.add_worktree(str(d), "t-conf")
        open(os.path.join(path, "shared.txt"), "w").write("task version\n")
        # base edits the SAME line differently after the worktree branched
        open(f, "w").write("base version\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base edit"], cwd=str(d), check=True)
        assert wt.merge_worktree(str(d), "t-conf").ok is False
        # after merge_worktree aborted the conflicted merge, the tree is clean again
        assert wt.conflict_paths(str(d)) == []
