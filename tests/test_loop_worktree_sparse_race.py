"""Regression: concurrent sparse worktree creation must not lose the cone.

``add_worktrees`` fans ``sparse-checkout set`` out through a thread pool; the
first such call in a repo writes ``extensions.worktreeConfig`` into the SHARED
``.git/config``, so concurrent arming raced on the config lockfile and the
losers silently fell back to FULL hydration (measured: ``could not lock config
file … File exists`` → ``docs/guide.md`` present in a src-scoped worktree).
Fix is two-layered: the batch pre-arms the shared write while still serial,
and ``set_sparse_scope`` retries the one transient lock error class.
"""

from __future__ import annotations

import os

import pytest
from test_loop_worktree_sparse import _repo, _tree  # reuse the canonical fixtures

from personalclaw.loop import worktree as wt


class TestLockRetry:
    def test_retries_transient_config_lock_and_succeeds(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        real_git = wt._git

        def flaky(cwd, *args):
            if args[:2] == ("sparse-checkout", "set"):
                calls["n"] += 1
                if calls["n"] == 1:
                    return 1, "error: could not lock config file .git/config: File exists"
            return real_git(cwd, *args)

        monkeypatch.setattr(wt, "_git", flaky)
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-retry", scope=["src"])
        assert path is not None
        assert calls["n"] >= 2, "lock failure was not retried"
        assert "src/app.py" in _tree(path)
        assert "docs/guide.md" not in _tree(path), "cone lost despite retry"

    def test_non_lock_failure_does_not_retry(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def broken(cwd, *args):
            if args[:2] == ("sparse-checkout", "set"):
                calls["n"] += 1
                return 1, "fatal: this operation must be run in a work tree"
            return wt.__dict__["_git_orig"](cwd, *args)

        monkeypatch.setitem(wt.__dict__, "_git_orig", wt._git)
        monkeypatch.setattr(wt, "_git", broken)
        ws = _repo(tmp_path)
        # Sparse setup fails once, no retries; worktree survives fully hydrated.
        path = wt.add_worktree(ws, "t-hard", scope=["src"])
        assert path is not None
        assert calls["n"] == 1, "a permanent failure must not be retried"
        assert "docs/guide.md" in _tree(path)  # documented fallback: full checkout


class TestBatchDeterminism:
    @pytest.mark.parametrize("round_", range(3))
    def test_every_scoped_worktree_keeps_its_cone(self, tmp_path, round_):
        """The measured race lost the cone ~50% of the time on a cold repo;
        three fresh-repo rounds of an 8-wide batch pin the fix."""
        ws = _repo(tmp_path, name=f"repo{round_}")
        got = wt.add_worktrees(ws, [(f"t-d{i}", ["src"]) for i in range(8)])
        assert set(got) == {f"t-d{i}" for i in range(8)}
        for tid, path in got.items():
            assert path is not None and os.path.isdir(path), tid
            tree = _tree(path)
            assert "src/app.py" in tree, tid
            assert "docs/guide.md" not in tree, f"{tid} lost its sparse cone"
