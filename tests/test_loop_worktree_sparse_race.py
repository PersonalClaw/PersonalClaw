"""Regression: concurrent worktree creation must not lose the cone OR the worktree.

TWO measured races live here, both from ``add_worktrees``' thread pool.

**The cone.** The first ``sparse-checkout set`` in a repo writes
``extensions.worktreeConfig`` into the SHARED ``.git/config``, so concurrent arming
raced on the config lockfile and the losers silently fell back to FULL hydration
(measured: ``could not lock config file … File exists`` → ``docs/guide.md`` present
in a src-scoped worktree). Fix is two-layered: the batch pre-arms the shared write
while still serial, and ``set_sparse_scope`` retries the one transient lock error
class.

**The worktree itself.** ``git worktree add`` is not safe against a concurrent ``git
worktree add`` in the same repo, and it fails HARD. On git 2.55 an add writes
``.git/worktrees/<id>/gitdir`` BEFORE that entry's ``commondir``, so a sibling entry
is briefly discoverable while its ``commondir`` is a zero-byte file; polling the
directory through a wide batch caught the state directly (``commondir=0 gitdir=118``),
and forging it makes a concurrent add die on demand with ``fatal: failed to read
.git/worktrees/<sibling>/commondir: Undefined error: 0``, exit 128 → ``add_worktree``
returns None. That is what turned ``main`` red on macOS/3.12 (run 35348911351). The
same forging shows the blast radius is exactly one command — ``checkout``,
``sparse-checkout set`` and ``status`` inside a worktree all survive it — so the fix
serializes REGISTRATION per repo and leaves HYDRATION in the pool. Both halves of that
sentence are asserted below; asserting only the first would be satisfied by
serializing the whole batch and quietly deleting the pool's reason to exist.

The forged-state premise is deliberately NOT a test: the sibling ``commondir`` read
only exists in gits new enough to ``repo_init`` each linked worktree, so pinning it
would go red on the older git in another matrix leg rather than on a regression here.
"""

from __future__ import annotations

import os
import threading
import time

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


class TestRegistrationIsSerialized:
    """The second race, asserted on OUR code rather than on git's timing: an 8-wide
    batch must never have two ``git worktree add`` invocations in flight at once, and
    must still overlap the hydration. Both are load-independent — peak concurrency and
    a rendezvous, not a duration — so neither can flake on a busy runner."""

    def _spy(self, monkeypatch, match: tuple[str, ...], enter, leave=None):
        """Wrap ``wt._git`` so ``enter``/``leave`` bracket every call whose argv starts
        with ``match``. Bracketing the real call (not replacing it) keeps the batch a
        genuine end-to-end run, so a green here is a green on real git."""
        real = wt._git

        def watched(cwd, *args, **kw):
            hit = args[: len(match)] == match
            if hit:
                enter()
            try:
                return real(cwd, *args, **kw)
            finally:
                if hit and leave is not None:
                    leave()

        monkeypatch.setattr(wt, "_git", watched)

    def test_registrations_never_overlap(self, tmp_path, monkeypatch):
        """``git worktree add`` dies on a sibling add's half-written metadata, so a
        second one must never be in flight. The 10ms hold widens the window a real
        racer lands in; without the per-repo lock the peak is the pool width."""
        ws = _repo(tmp_path)
        guard = threading.Lock()
        state = {"live": 0, "peak": 0}

        def enter():
            with guard:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            time.sleep(0.01)

        def leave():
            with guard:
                state["live"] -= 1

        self._spy(monkeypatch, ("worktree", "add"), enter, leave)
        got = wt.add_worktrees(ws, [(f"t-s{i}", ["src"]) for i in range(8)])
        assert all(p is not None for p in got.values()), got
        assert state["peak"] == 1, f"two registrations overlapped (peak={state['peak']})"

    def test_hydration_still_overlaps(self, tmp_path, monkeypatch):
        """The vacuity floor for the test above: serializing the WHOLE add would pass
        it too, and would delete the pool's only reason to exist. Two hydrations must
        meet at a barrier — a rendezvous rather than a stopwatch, so the assertion
        cannot be satisfied (or broken) by how loaded the host is."""
        monkeypatch.setattr(os, "cpu_count", lambda: 4)  # pool_size is then exactly 4
        ws = _repo(tmp_path)
        met = threading.Event()
        rendezvous = threading.Barrier(2)

        def enter():
            try:
                rendezvous.wait(timeout=30)
                met.set()
            except threading.BrokenBarrierError:
                pass  # serialized: the first waiter times out and breaks the barrier

        self._spy(monkeypatch, ("checkout",), enter)
        got = wt.add_worktrees(ws, [(f"t-h{i}", ["src"]) for i in range(8)])
        assert all(p is not None for p in got.values()), got
        assert met.is_set(), "hydration ran serially — the pool overlaps nothing"
