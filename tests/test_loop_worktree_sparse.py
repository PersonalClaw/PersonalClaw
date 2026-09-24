"""Sparse + pooled + reused worktree hydration (HARNESS-CRAFT HC-2).

Drives REAL git against temp repos, because every claim in HC-2 is a claim about what
git actually does, and the interesting behaviours are the ones a mock would paper over:

* An out-of-cone write lands on disk, but ``git add -A`` then refuses it and stages
  NOTHING (exit 1) — and ``merge_worktree`` discards that exit code, so the work vanishes
  while the merge reports success. The widening tests therefore assert the file is IN THE
  RESULTING COMMIT, never that a widen command was issued.
* "Sparse" is only meaningful if something is genuinely ABSENT, so each sparse test
  first asserts an out-of-scope path is missing. Without that floor, a full checkout
  would satisfy every other assertion in the file.
* The pool bound is asserted as a NUMBER under a monkeypatched ``cpu_count``, both above
  and below the ceiling — on an 18-core dev box an unbounded pool would look identical
  to a bounded one.

Every worktree, branch and repo lives under ``tmp_path``; ``config_dir`` is redirected
so the worktrees root never touches a real PersonalClaw home.
"""

from __future__ import annotations

import os
import subprocess
import threading

import pytest

from personalclaw.loop import worktree as wt

pytestmark = pytest.mark.skipif(not wt.git_available(), reason="git not installed")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    """Worktrees under a temp config dir, and the per-process caches cleared — both
    ``_FILE_COUNT_CACHE`` and ``_TRACKED_DIRS_CACHE`` are keyed by workspace abspath, so
    a leftover entry from another test's tmp_path would answer for this one."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
    wt._FILE_COUNT_CACHE.clear()
    wt._TRACKED_DIRS_CACHE.clear()
    yield
    wt._FILE_COUNT_CACHE.clear()
    wt._TRACKED_DIRS_CACHE.clear()


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _repo(tmp_path, name="repo", extra: dict[str, str] | None = None) -> str:
    """A repo with three top-level dirs plus a root file, committed.

    The root file matters: cone mode keeps root-level files hydrated, which is what
    makes a scoped worktree still usable (a real task needs pyproject/Makefile), and a
    test that never checks it would not notice if that stopped being true.
    """
    d = tmp_path / name
    for sub in ("src", "docs", "web"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "src" / "app.py").write_text("print('app')\n")
    (d / "src" / "util.py").write_text("X = 1\n")
    (d / "docs" / "guide.md").write_text("# guide\n")
    (d / "web" / "main.ts").write_text("export const a = 1;\n")
    (d / "README.md").write_text("# root\n")
    for rel, body in (extra or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return str(d)


def _tree(path: str) -> set[str]:
    """Repo-relative files present in the WORKING TREE (not the index)."""
    out: set[str] = set()
    for root, dirs, files in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), path).replace(os.sep, "/"))
    return out


# ── scope derivation ──────────────────────────────────────────────────────────


class TestScopeDerivation:
    def test_extracts_path_tokens_from_task_text(self):
        got = wt.scope_candidates(
            "Update src/personalclaw/loop/worktree.py and add tests/test_x.py; "
            "see the `docs/reference/configuration.md` table."
        )
        assert got == [
            "src/personalclaw/loop/worktree.py",
            "tests/test_x.py",
            "docs/reference/configuration.md",
        ]

    def test_bare_words_are_never_candidates(self):
        """A scope of bare nouns would hydrate nothing and break the task. Requiring a
        slash is the cheapest guard, so it gets its own test."""
        assert wt.scope_candidates("refactor the worktree module and its tests") == []

    def test_a_sentence_final_directory_mention_is_not_a_dot_token(self):
        """Found by driving the real path: "…do not touch web/." yielded the token
        ``web/.``, which then resolved to the ``web`` directory and silently widened the
        scope. The final component must start with a word char."""
        assert wt.scope_candidates("Do not touch web/.") == []
        assert wt.scope_candidates("Edit web/main.ts.") == ["web/main.ts"]

    def test_negative_polarity_is_not_modelled(self):
        """Documented, not accidental: a forbidding mention still contributes its path.

        Over-inclusion costs only some of the hydration saving; under-inclusion is
        recovered by auto-widening. Since neither can break a task, modelling polarity
        would add a natural-language guess to a path that is explicitly a HINT. Pinned so
        the behaviour is a decision rather than a surprise."""
        assert wt.scope_candidates("Do not touch web/src/pages") == ["web/src/pages"]

    def test_resolves_files_to_their_parent_directory(self, tmp_path):
        """Cone entries are DIRECTORIES, so a named file contributes its parent."""
        ws = _repo(tmp_path)
        assert wt.resolve_scope(ws, ["src/app.py"]) == ["src"]

    def test_resolves_a_named_directory_as_itself(self, tmp_path):
        ws = _repo(tmp_path)
        assert wt.resolve_scope(ws, ["docs"]) == ["docs"]

    def test_hallucinated_paths_are_dropped(self, tmp_path):
        """The scope is model-authored, so a path that does not exist is the expected
        case, not an error case — it must not become a cone entry (which would hydrate
        an empty tree and break the task)."""
        ws = _repo(tmp_path)
        assert wt.resolve_scope(ws, ["nope/whatever.py", "also/missing.ts"]) == []

    def test_a_real_path_survives_alongside_a_fake_one(self, tmp_path):
        ws = _repo(tmp_path)
        assert wt.resolve_scope(ws, ["nope/whatever.py", "web/main.ts"]) == ["web"]

    def test_traversal_candidates_are_refused(self, tmp_path):
        ws = _repo(tmp_path)
        assert wt.resolve_scope(ws, ["../../etc/passwd", "a/../../b/c.py"]) == []

    def test_too_wide_a_scope_means_no_scope(self, tmp_path, monkeypatch):
        """A scope naming everything is not a scope; hydrate fully rather than pay to
        enumerate it."""
        ws = _repo(tmp_path)
        monkeypatch.setattr(wt, "_MAX_SCOPE_DIRS", 2)
        assert wt.resolve_scope(ws, ["src/app.py", "docs/guide.md", "web/main.ts"]) == []

    def test_no_git_answer_means_full_hydration(self, tmp_path, monkeypatch):
        """``git ls-files`` failing must degrade to a full checkout, never to a guess."""
        ws = _repo(tmp_path)
        monkeypatch.setattr(wt, "_git", lambda *a, **k: (1, "boom"))
        wt._TRACKED_DIRS_CACHE.clear()
        assert wt.resolve_scope(ws, ["src/app.py"]) == []


# ── the config field, AT ITS CONSUMER ─────────────────────────────────────────


class TestConfigGate:
    """``loops.worktree_sparse`` is only real if the CONSUMER honours it. These drive
    ``scope_for_task`` — the chokepoint ``sdlc`` calls — not the primitives beneath it,
    because a field can wire through all five points and still be inert."""

    def test_default_is_on_and_yields_a_scope(self, tmp_path):
        ws = _repo(tmp_path)
        assert wt.sparse_enabled() is True
        assert wt.scope_for_task(ws, "Fix src/app.py") == ["src"]

    def test_disabling_the_field_disables_scoping(self, tmp_path, monkeypatch):
        ws = _repo(tmp_path)
        monkeypatch.setattr(wt, "sparse_enabled", lambda: False)
        assert wt.scope_for_task(ws, "Fix src/app.py") == []

    def test_config_off_produces_a_full_checkout_end_to_end(self, tmp_path, monkeypatch):
        """The flag's OUTCOME, not just its return value: with it off, the worktree
        holds every file."""
        ws = _repo(tmp_path)
        monkeypatch.setattr(wt, "sparse_enabled", lambda: False)
        scope = wt.scope_for_task(ws, "Fix src/app.py only")
        path = wt.add_worktree(ws, "t-off", scope=scope)
        assert path is not None
        assert "docs/guide.md" in _tree(path)

    def test_real_config_object_carries_the_field(self, tmp_path, monkeypatch):
        """Guards the wiring itself: a renamed/removed dataclass field would make
        ``sparse_enabled`` silently fail-open to False and disable HC-2 wholesale."""
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig()
        assert cfg.loops.worktree_sparse is True
        assert cfg.to_dict()["loops"]["worktree_sparse"] is True

    def test_sparse_enabled_reads_the_loaded_config(self, tmp_path, monkeypatch):
        """Round-trip through a real config FILE into ``sparse_enabled()``.

        This is the leg that catches a wrong accessor: ``sparse_enabled`` fails open to
        False, so reading a name that does not exist would look exactly like the field
        being switched off — and every sparse feature would be silently dead."""
        import json

        from personalclaw.config import loader

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(json.dumps({"loops": {"worktree_sparse": False}}))
        monkeypatch.setattr(loader, "config_dir", lambda: home)
        assert loader.AppConfig.load().loops.worktree_sparse is False
        assert wt.sparse_enabled() is False

        (home / "config.json").write_text(json.dumps({"loops": {"worktree_sparse": True}}))
        assert wt.sparse_enabled() is True, "fail-open masked the real read"


def test_sdlc_scheduler_calls_the_scoped_batch_api():
    """The call site, by AST — HC-2's whole value is that the SDLC fan-out uses the
    batched, scoped API. A green suite over ``worktree.py`` alone would not notice
    ``sdlc`` still calling ``add_worktree`` one task at a time in its loop."""
    import ast
    import inspect
    import textwrap

    from personalclaw.loop.kinds import sdlc

    tree = ast.parse(textwrap.dedent(inspect.getsource(sdlc.CodeKind._schedule_parallel)))
    calls = {
        ast.unparse(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "worktree.add_worktrees" in calls, calls
    assert "worktree.scope_for_task" in calls, calls
    assert "worktree.add_worktree" not in calls, "serial per-task creation is still here"


# ── sparse hydration ──────────────────────────────────────────────────────────


class TestSparseHydration:
    def test_scoped_worktree_omits_out_of_scope_paths(self, tmp_path):
        """The falsifiability floor for every sparse claim in this file."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-sparse", scope=["src"])
        assert path is not None
        tree = _tree(path)
        assert "src/app.py" in tree
        assert "docs/guide.md" not in tree, "sparse hydration did not reduce the tree"
        assert "web/main.ts" not in tree
        assert wt.sparse_scope(path) == ["src"]

    def test_root_files_stay_hydrated(self, tmp_path):
        """Cone mode keeps root-level files — without them a scoped worktree has no
        pyproject/Makefile and no real task could build in it."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-root", scope=["src"])
        assert "README.md" in _tree(path)

    def test_no_scope_hydrates_everything(self, tmp_path):
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-full", scope=[])
        assert {"src/app.py", "docs/guide.md", "web/main.ts"} <= _tree(path)
        assert wt.sparse_scope(path) == []

    def test_branch_carries_the_whole_repo_despite_a_sparse_tree(self, tmp_path):
        """Sparseness is a WORKING-TREE property. The commit must still contain the
        unhydrated files, which is what makes merge-back diff-identical."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-branch", scope=["src"])
        listed = _git(path, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        assert "docs/guide.md" in listed
        assert "web/main.ts" in listed


# ── auto-widening ─────────────────────────────────────────────────────────────


class TestAutoWiden:
    def test_out_of_scope_write_is_silently_dropped_without_widening(self, tmp_path):
        """The DEFECT this feature exists to prevent, pinned as a test so the remedy
        below is measured against a real failure and not a hypothetical one.

        ``git add -A`` refuses the out-of-cone path and exits 1, staging NOTHING. Git
        signals it; ``merge_worktree`` discards the exit code (it always has), which is
        what turns a git error into silent data loss. Pinned so the remedy is measured
        against the real failure and not a hypothetical one.
        """
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-drop", scope=["src"])
        os.makedirs(os.path.join(path, "docs"), exist_ok=True)
        with open(os.path.join(path, "docs/new.md"), "w") as f:
            f.write("out of scope\n")

        assert (
            _git(path, "add", "-A").returncode == 1
        ), "premise stale: add -A no longer refuses out-of-cone paths"
        assert (
            _git(path, "diff", "--cached", "--name-only").stdout.strip() == ""
        ), "premise stale: git now stages out-of-cone paths, so widening is unnecessary"
        _git(path, "commit", "-qm", "work")
        listed = _git(path, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        assert "docs/new.md" not in listed

    def test_without_widening_merge_reports_success_and_loses_the_work(self, tmp_path):
        """The end-to-end defect, through the REAL merge path with widening disabled.

        This is the shape that makes the widen load-bearing rather than cosmetic: the
        merge returns ``ok=True``, so every status surface says the task merged cleanly,
        while the file the task wrote is gone from the base branch. It is also the
        falsification target for :meth:`test_merge_back_carries_an_out_of_scope_write`.
        """
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-lost", scope=["src"])
        os.makedirs(os.path.join(path, "web"), exist_ok=True)
        with open(os.path.join(path, "web/lost.ts"), "w") as f:
            f.write("export const gone = 1;\n")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(wt, "widen_for_pending", lambda _p: [])
            result = wt.merge_worktree(ws, "t-lost")

        assert result.ok is True, "merge did not even report success — premise stale"
        assert not (
            tmp_path / "repo" / "web" / "lost.ts"
        ).exists(), "premise stale: the write survived without widening"

    def test_widening_makes_an_out_of_scope_write_land(self, tmp_path):
        """The done_when clause: the write SUCCEEDS (reaches the commit) and the cone
        GREW. Both are asserted — a widen that ran but did not cover the path, or a
        cone that grew without the file being committed, each fail here."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-widen", scope=["src"])
        assert wt.sparse_scope(path) == ["src"]
        os.makedirs(os.path.join(path, "docs"), exist_ok=True)
        with open(os.path.join(path, "docs/new.md"), "w") as f:
            f.write("out of scope\n")

        widened = wt.widen_for_pending(path)

        assert widened == ["docs"]
        assert wt.sparse_scope(path) == ["docs", "src"], "the cone did not grow"
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", "work")
        listed = _git(path, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        assert "docs/new.md" in listed, "the out-of-scope write did not reach the commit"

    def test_widening_does_not_fail_the_write_on_a_modified_in_scope_file(self, tmp_path):
        """An in-cone edit needs no widening; the cone must be left alone (a widen that
        fired here would erode sparseness on every ordinary task)."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-inscope", scope=["src"])
        with open(os.path.join(path, "src/app.py"), "a") as f:
            f.write("# edit\n")
        assert wt.widen_for_pending(path) == []
        assert wt.sparse_scope(path) == ["src"]

    def test_widening_is_a_noop_on_a_full_checkout(self, tmp_path):
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-nocone", scope=[])
        with open(os.path.join(path, "docs/guide.md"), "a") as f:
            f.write("edit\n")
        assert wt.widen_for_pending(path) == []

    def test_merge_back_carries_an_out_of_scope_write(self, tmp_path):
        """End to end through the real merge path — this is the clause that would break
        silently in production, because ``merge_worktree`` is where ``add -A`` runs."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-merge", scope=["src"])
        with open(os.path.join(path, "src/app.py"), "a") as f:
            f.write("# in scope\n")
        os.makedirs(os.path.join(path, "web"), exist_ok=True)
        with open(os.path.join(path, "web/extra.ts"), "w") as f:
            f.write("export const b = 2;\n")

        assert wt.merge_worktree(ws, "t-merge").ok is True

        assert (tmp_path / "repo" / "web" / "extra.ts").is_file()
        head = _git(ws, "show", "--name-only", "--format=", "HEAD").stdout
        assert (
            "web/extra.ts" in head
            or "web/extra.ts" in _git(ws, "ls-tree", "-r", "--name-only", "HEAD").stdout
        )


def test_merge_back_is_diff_identical_to_a_full_checkout(tmp_path):
    """HC-2's ``merge-back is diff-identical to full checkouts`` clause, as a direct
    comparison: the same edits made in a sparse worktree and in a full one produce the
    same tree hash on the base branch."""
    results = []
    for name, scope in (("sparse", ["src"]), ("full", [])):
        ws = _repo(tmp_path, name=name)
        path = wt.add_worktree(ws, "t-cmp", scope=scope)
        assert path is not None
        with open(os.path.join(path, "src/app.py"), "w") as f:
            f.write("print('changed')\n")
        os.makedirs(os.path.join(path, "docs"), exist_ok=True)
        with open(os.path.join(path, "docs/added.md"), "w") as f:
            f.write("added\n")
        assert wt.merge_worktree(ws, "t-cmp").ok is True
        results.append(_git(ws, "rev-parse", "HEAD^{tree}").stdout.strip())
    assert (
        results[0] and results[0] == results[1]
    ), f"sparse merge-back diverged from full: {results}"


# ── bounded pool ──────────────────────────────────────────────────────────────


class TestPoolBound:
    def test_ceiling_binds_on_a_big_box(self, monkeypatch):
        """The bound as a NUMBER. An unbounded pool on this 18-core dev box would pass
        any 'a pool exists' assertion, so the ceiling is asserted explicitly."""
        monkeypatch.setattr(os, "cpu_count", lambda: 64)
        assert wt.pool_size(16) == 4
        assert wt.POOL_CEILING == 4

    def test_cpu_count_binds_below_the_ceiling(self, monkeypatch):
        """The vacuity floor for the test above: if ``pool_size`` ignored ``cpu_count``
        and just returned the ceiling, the first test would still pass and this one
        fails."""
        monkeypatch.setattr(os, "cpu_count", lambda: 2)
        assert wt.pool_size(16) == 2

    def test_never_more_workers_than_work(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 64)
        assert wt.pool_size(2) == 2

    def test_never_zero(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: None)
        assert wt.pool_size(0) == 1
        assert wt.pool_size(None) >= 1

    def test_executor_is_constructed_with_the_bound(self, tmp_path, monkeypatch):
        """Asserting ``pool_size`` alone would not catch ``add_worktrees`` ignoring it,
        so capture the real ``max_workers`` the executor is built with."""
        import concurrent.futures as cf

        ws = _repo(tmp_path)
        monkeypatch.setattr(os, "cpu_count", lambda: 64)
        seen: list[int | None] = []
        real = cf.ThreadPoolExecutor

        class Spy(real):  # type: ignore[misc,valid-newtype]
            def __init__(self, max_workers=None, **kw):
                seen.append(max_workers)
                super().__init__(max_workers=max_workers, **kw)

        monkeypatch.setattr(cf, "ThreadPoolExecutor", Spy)
        wt.add_worktrees(ws, [(f"t-p{i}", ["src"]) for i in range(6)])
        assert seen == [4], f"executor was not bounded: {seen}"

    def test_batch_creates_every_worktree(self, tmp_path):
        ws = _repo(tmp_path)
        got = wt.add_worktrees(ws, [(f"t-b{i}", ["src"]) for i in range(4)])
        assert set(got) == {f"t-b{i}" for i in range(4)}
        for tid, path in got.items():
            assert path is not None and os.path.isdir(path), tid
            assert "src/app.py" in _tree(path)
            assert "docs/guide.md" not in _tree(path)

    def test_batch_runs_concurrently(self, tmp_path, monkeypatch):
        """Peak-in-flight > 1 — a sequential loop through the same specs fails this."""
        ws = _repo(tmp_path)
        lock = threading.Lock()
        state = {"live": 0, "peak": 0}
        real_add = wt.add_worktree

        def counting(*a, **kw):
            with lock:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            try:
                return real_add(*a, **kw)
            finally:
                with lock:
                    state["live"] -= 1

        monkeypatch.setattr(wt, "add_worktree", counting)
        wt.add_worktrees(ws, [(f"t-c{i}", ["src"]) for i in range(4)])
        assert state["peak"] > 1, "batch ran sequentially"

    def test_a_single_spec_skips_the_pool(self, tmp_path, monkeypatch):
        import concurrent.futures as cf

        ws = _repo(tmp_path)

        def boom(*a, **kw):
            raise AssertionError("a pool was built for one item")

        monkeypatch.setattr(cf, "ThreadPoolExecutor", boom)
        got = wt.add_worktrees(ws, [("t-solo", ["src"])])
        assert got["t-solo"] is not None

    def test_one_failure_does_not_sink_the_batch(self, tmp_path, monkeypatch):
        ws = _repo(tmp_path)
        real_add = wt.add_worktree

        def flaky(workspace, task_id, project_id="", scope=None):
            if task_id == "t-bad":
                raise RuntimeError("boom")
            return real_add(workspace, task_id, project_id, scope)

        monkeypatch.setattr(wt, "add_worktree", flaky)
        got = wt.add_worktrees(ws, [("t-bad", []), ("t-ok1", []), ("t-ok2", [])])
        assert got["t-bad"] is None
        assert got["t-ok1"] is not None and got["t-ok2"] is not None

    def test_empty_batch(self, tmp_path):
        assert wt.add_worktrees(_repo(tmp_path), []) == {}


# ── reuse pool ────────────────────────────────────────────────────────────────


class TestReusePool:
    def test_reset_removes_a_leftover_file(self, tmp_path):
        """The reuse pool's reason to exist: a surviving worktree must not hand the next
        task the previous one's files."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-reuse", scope=[])
        with open(os.path.join(path, "leftover.txt"), "w") as f:
            f.write("stale\n")
        assert os.path.isfile(os.path.join(path, "leftover.txt"))

        assert wt.reset_worktree(ws, "t-reuse") is True

        assert not os.path.exists(os.path.join(path, "leftover.txt"))

    def test_reset_reverts_a_modified_tracked_file(self, tmp_path):
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-dirty", scope=[])
        with open(os.path.join(path, "src/app.py"), "w") as f:
            f.write("garbage\n")
        assert wt.reset_worktree(ws, "t-dirty") is True
        assert open(os.path.join(path, "src/app.py")).read() == "print('app')\n"

    def test_reset_clears_a_staged_index(self, tmp_path):
        """A dirty INDEX is the leak a ``clean``-only reset would miss: the files look
        right but the next task's first commit carries the previous task's staging."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-index", scope=[])
        with open(os.path.join(path, "staged.txt"), "w") as f:
            f.write("staged\n")
        _git(path, "add", "staged.txt")
        assert _git(path, "diff", "--cached", "--name-only").stdout.strip() == "staged.txt"

        assert wt.reset_worktree(ws, "t-index") is True

        assert _git(path, "diff", "--cached", "--name-only").stdout.strip() == ""

    def test_reset_removes_ignored_build_output(self, tmp_path):
        """``-x`` too: an ignored dir is a previous task's build output, and leaving it
        is exactly the cross-task leak the pool exists to prevent."""
        ws = _repo(tmp_path, extra={".gitignore": "build/\n"})
        path = wt.add_worktree(ws, "t-ign", scope=[])
        os.makedirs(os.path.join(path, "build"), exist_ok=True)
        with open(os.path.join(path, "build/out.o"), "w") as f:
            f.write("junk\n")
        assert wt.reset_worktree(ws, "t-ign") is True
        assert not os.path.exists(os.path.join(path, "build/out.o"))

    def test_acquiring_an_existing_worktree_does_NOT_reset_it(self, tmp_path):
        """The resume guard, and the reason the reset is not on this path.

        ``add_worktree`` on an existing dir is ALSO how a loop restarting mid-task finds
        its worker's worktree. Resetting here would delete a live task's in-progress
        work, so the reset belongs at the phase/redo boundary instead. This test exists
        to red if someone 'optimizes' the reuse reset back into the acquire path."""
        ws = _repo(tmp_path)
        first = wt.add_worktree(ws, "t-acq", scope=[])
        with open(os.path.join(first, "in-progress.txt"), "w") as f:
            f.write("a live worker's work\n")

        second = wt.add_worktree(ws, "t-acq", scope=[])

        assert second == first
        assert os.path.isfile(
            os.path.join(second, "in-progress.txt")
        ), "resume path destroyed in-progress work"

    def test_reset_on_a_missing_worktree_is_false(self, tmp_path):
        """False is the teardown signal, so 'no worktree' must report it rather than
        claiming a successful reset of nothing."""
        assert wt.reset_worktree(_repo(tmp_path), "t-absent") is False

    def test_reset_leaves_the_branch_on_the_current_base(self, tmp_path):
        """The task re-runs on the MERGED base, so the reset branch must point at the
        workspace's HEAD, not at the commit the worktree was created from."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-base", scope=[])
        with open(os.path.join(tmp_path / "repo", "another.txt"), "w") as f:
            f.write("moved base\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "base moved")
        base = _git(ws, "rev-parse", "HEAD").stdout.strip()

        assert wt.reset_worktree(ws, "t-base") is True

        assert _git(path, "rev-parse", "HEAD").stdout.strip() == base


def test_conflict_redo_resets_then_falls_back_to_teardown():
    """The reuse pool's real CALL SITE, by AST.

    ``_reap_merge_done``'s conflict auto-resolve used to ``remove_worktree`` and pay full
    hydration again on the re-run; HC-2 makes it reset first. Asserting on
    ``worktree.py`` alone would not notice the scheduler never adopting it — and the
    ``remove_worktree`` call must still be REACHABLE, because it is the documented
    teardown fallback for any reset failure."""
    import ast
    import inspect
    import textwrap

    from personalclaw.loop.kinds import sdlc

    tree = ast.parse(textwrap.dedent(inspect.getsource(sdlc.CodeKind._reap_merge_done)))
    calls = [
        ast.unparse(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert "worktree.reset_worktree" in calls, calls
    assert "worktree.remove_worktree" in calls, "the teardown fallback is gone"
    # The teardown must be GUARDED by the reset's failure, not run unconditionally.
    guarded = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "worktree.reset_worktree" in ast.unparse(n.test)
        and "worktree.remove_worktree" in ast.unparse(n.body)
    ]
    assert guarded, "remove_worktree is not gated on a failed reset"

    def test_reuse_preserves_the_sparse_cone(self, tmp_path):
        """Reset must not silently re-hydrate the repo — that would make phase 2 of
        every loop pay the full cost the plan is trying to avoid."""
        ws = _repo(tmp_path)
        path = wt.add_worktree(ws, "t-cone", scope=["src"])
        assert wt.sparse_scope(path) == ["src"]
        assert wt.reset_worktree(ws, "t-cone") is True
        assert wt.sparse_scope(path) == ["src"]
        assert "docs/guide.md" not in _tree(path)


def test_fanout_of_four_is_faster_than_serial_creation(tmp_path):
    """HC-2's ``fan-out timing assertion``. Deliberately a RELATIVE comparison against
    serial creation in the same process and the same repo — an absolute millisecond
    budget would be a flake generator on a loaded CI box (HC-1 measured a 7.5 s spread
    across four samples on this machine under load).

    The claim under test is only that the batch is not SLOWER than the serial path it
    replaced; the pool's win is bounded by git's brief repo lock, so a modest margin is
    the honest assertion.
    """
    import time

    ws = _repo(tmp_path)
    serial_ids = [f"t-s{i}" for i in range(4)]
    t0 = time.perf_counter()
    for tid in serial_ids:
        assert wt.add_worktree(ws, tid, scope=["src"]) is not None
    serial = time.perf_counter() - t0
    for tid in serial_ids:
        wt.remove_worktree(ws, tid)

    t0 = time.perf_counter()
    got = wt.add_worktrees(ws, [(f"t-b{i}", ["src"]) for i in range(4)])
    batched = time.perf_counter() - t0

    assert all(p is not None for p in got.values())
    assert batched <= serial * 1.6, f"batched {batched:.3f}s vs serial {serial:.3f}s"
