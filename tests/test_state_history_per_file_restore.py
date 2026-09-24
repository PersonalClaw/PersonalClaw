"""DAS-9 — per-file time travel: restoring ONE note, not the whole tree.

Criterion 6 wants the memory tree restorable from the panel. Whole-root
rollback/revert already did that for a root; this covers the subset half, and the
rails here exist because the subset is exactly where the panel's copy can quietly
become false:

* the panel promises **rollback discards later edits, revert keeps them**. That
  distinction is asserted for the same target, in the same fixture, from the same
  starting state — because the cheap wrong implementation of a per-file revert
  ("check out the parent for those paths") passes every test that only checks
  "did the file change?" while turning revert into rollback;
* an unknown or escaping path must RAISE. A silently-dropped path is a restore the
  user believes happened and did not, so the "changes nothing" half is asserted
  too: no commit, and no parked service ref;
* whole-root behaviour must be byte-identical for BOTH ``paths=None`` and
  ``paths=[]`` — a shipped surface calls it that way. Asserted by proving the
  hard-reset shape (HEAD lands ON the target, the commit count DROPS), which a
  subset implementation cannot fake since it ADDS a commit;
* the gitignored-secret claim has to survive the new mechanism, including the
  "never runs ``git clean``" half. That one is asserted over the module's syntax
  tree rather than its text, so the prose in the docstring that discusses ``git
  clean`` cannot make the rail pass or fail for the wrong reason.

Every test runs against an isolated home AND an isolated workspace: with no
seeded workspace the memory root would be a git repository over the developer's
real ``~/workplace``.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from personalclaw.durability import state_history as sh

pytestmark = pytest.mark.skipif(not sh.git_available(), reason="git is required for time-travel")


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Both rails: an isolated home AND an isolated workspace."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "env-home"))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "env-ws"))


def _fresh(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """An independent (home, workspace) pair, so two runs can be compared."""
    home = tmp_path / name / "home"
    ws = tmp_path / name / "ws"
    home.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    return home, ws


def _root(home: Path, ws: Path, root_id: str) -> sh.HistoryRoot:
    root = next(r for r in sh.roots(home=home, workspace=ws) if r.id == root_id)
    sh.ensure_repo(root, home=home)
    return root


def _git_out(root: sh.HistoryRoot, home: Path, *args: str) -> str:
    gd = sh.git_dir(root, home=home)
    proc = subprocess.run(
        ["git", f"--git-dir={gd}", f"--work-tree={root.worktree}", *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(root.worktree),
    )
    return proc.stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


A_V1 = "alpha\nbeta\ngamma\n"
A_TARGET = "alpha\nBETA-BAD\ngamma\n"
A_HEAD = "alpha\nBETA-BAD\ngamma\ndelta-later\n"
#: What a per-file REVERT must produce: the target's line-2 change undone, the
#: later append kept.
A_REVERTED = "alpha\nbeta\ngamma\ndelta-later\n"


def _memory_fixture(tmp_path: Path, name: str) -> tuple[sh.HistoryRoot, Path, Path, str]:
    """Three commits over two memory notes. Returns (root, home, ws, target sha).

    Shape chosen so the target's hunk and the later edit are SEPARABLE — a
    one-line file would make every later edit overlap and the revert half of the
    distinction would be untestable rather than untrue.
    """
    home, ws = _fresh(tmp_path, name)
    root = _root(home, ws, "memory")
    _write(ws / "memory" / "a.md", A_V1)
    _write(ws / "memory" / "b.md", "b-first\n")
    sh.commit(root, home=home)
    _write(ws / "memory" / "a.md", A_TARGET)
    _write(ws / "memory" / "b.md", "b-second\n")
    target = sh.commit(root, home=home)
    _write(ws / "memory" / "a.md", A_HEAD)
    _write(ws / "memory" / "b.md", "b-third-later\n")
    sh.commit(root, home=home)
    assert sh.commit_count(root, home=home) == 3
    assert target
    return root, home, ws, target


# ── the whole-root path must not move ──────────────────────────────────────


class TestWholeRootIsUnchanged:
    """``paths=None`` and ``paths=[]`` both mean the whole root, as shipped."""

    @pytest.mark.parametrize("subset", [None, []])
    def test_rollback_still_hard_resets(self, tmp_path, subset):
        name = "reset-none" if subset is None else "reset-empty"
        home, ws = _fresh(tmp_path, name)
        root = _root(home, ws, "config")
        (home / "config.json").write_text('{"v": 1}')
        first = sh.commit(root, home=home)
        (home / "config.json").write_text('{"v": 2}')
        second = sh.commit(root, home=home)

        result = sh.rollback(root, first, paths=subset, home=home)

        # The hard-reset shape: HEAD lands ON the target and the rolled-away
        # commit LEAVES the timeline. A subset implementation adds a commit
        # instead, so it cannot satisfy either assertion.
        assert result["head"] == first
        assert result["prior_head"] == second
        assert sh.commit_count(root, home=home) == 1
        assert result["paths"] == []
        assert (home / "config.json").read_text() == '{"v": 1}'

    @pytest.mark.parametrize("subset", [None, []])
    def test_revert_still_adds_one_inverse_commit(self, tmp_path, subset):
        name = "revert-none" if subset is None else "revert-empty"
        home, ws = _fresh(tmp_path, name)
        root = _root(home, ws, "config")
        (home / "entity_settings").mkdir(parents=True)
        (home / "config.json").write_text("cfg-v1")
        sh.commit(root, home=home)
        (home / "entity_settings" / "bad.json").write_text("bad")
        bad = sh.commit(root, home=home)
        (home / "config.json").write_text("cfg-v2")
        sh.commit(root, home=home)

        result = sh.revert(root, bad, paths=subset, home=home)

        assert not (home / "entity_settings" / "bad.json").exists()
        assert (home / "config.json").read_text() == "cfg-v2", "the later edit must survive"
        assert sh.commit_count(root, home=home) == 4
        assert result["reverted"] == bad
        assert result["paths"] == []

    def test_the_two_spellings_produce_the_same_tree(self, tmp_path):
        """Not just "both work" — the same bytes, so neither can drift alone."""
        trees = []
        for name, subset in (("same-none", None), ("same-empty", [])):
            root, home, ws, target = _memory_fixture(tmp_path, name)
            sh.rollback(root, target, paths=subset, home=home)
            trees.append(
                (
                    _git_out(root, home, "ls-tree", "-r", "HEAD"),
                    (ws / "memory" / "a.md").read_text(),
                    (ws / "memory" / "b.md").read_text(),
                    sh.commit_count(root, home=home),
                )
            )
        assert trees[0] == trees[1]
        assert trees[0][1] == A_TARGET, "the whole-root rollback must have happened"

    def test_previews_report_an_empty_subset_for_the_whole_root(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "preview-whole")

        roll = sh.preview_rollback(root, target, home=home)
        rev = sh.preview_revert(root, target, home=home)

        assert roll["paths"] == []
        assert rev["paths"] == []
        # The count of commits a whole-root rollback discards is still reported.
        assert roll["commits_rolled_away"] == 1
        assert {f["path"] for f in roll["files"]} == {"memory/a.md", "memory/b.md"}
        assert sh.preview(root, target, operation="rollback", home=home)["paths"] == []
        assert sh.preview(root, target, operation="revert", home=home)["paths"] == []


# ── the distinction the panel's copy promises ──────────────────────────────


class TestRollbackDiscardsAndRevertKeeps:
    """The one thing a per-file restore must not blur.

    Same target, same starting state, same file — only the operation differs. The
    two expected contents are spelled out, so the "revert = checkout the parent"
    mistake fails on BOTH assertions instead of looking like a near-miss.
    """

    def test_per_file_rollback_discards_the_later_edit(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "distinct-rollback")

        sh.rollback(root, target, paths=["memory/a.md"], home=home)

        assert (ws / "memory" / "a.md").read_text() == A_TARGET
        assert "delta-later" not in (ws / "memory" / "a.md").read_text()

    def test_per_file_revert_keeps_the_later_edit(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "distinct-revert")

        sh.revert(root, target, paths=["memory/a.md"], home=home)

        assert (ws / "memory" / "a.md").read_text() == A_REVERTED
        assert "delta-later" in (ws / "memory" / "a.md").read_text()
        assert "BETA-BAD" not in (ws / "memory" / "a.md").read_text()

    def test_the_two_outcomes_are_not_the_same(self, tmp_path):
        """The collapse rail: if either operation drifts into the other, this fails.

        Deliberately not `!=` alone — a broken revert that produced a THIRD wrong
        content would still satisfy an inequality.
        """
        rolled_root, rolled_home, rolled_ws, rolled_target = _memory_fixture(tmp_path, "pair-roll")
        sh.rollback(rolled_root, rolled_target, paths=["memory/a.md"], home=rolled_home)
        rev_root, rev_home, rev_ws, rev_target = _memory_fixture(tmp_path, "pair-rev")
        sh.revert(rev_root, rev_target, paths=["memory/a.md"], home=rev_home)

        rolled = (rolled_ws / "memory" / "a.md").read_text()
        reverted = (rev_ws / "memory" / "a.md").read_text()
        assert rolled == A_TARGET
        assert reverted == A_REVERTED
        assert rolled != reverted


# ── per-file rollback ──────────────────────────────────────────────────────


class TestPerFileRollback:
    def test_only_the_named_path_is_restored(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "roll-one")

        result = sh.rollback(root, target, paths=["memory/a.md"], home=home)

        assert (ws / "memory" / "a.md").read_text() == A_TARGET
        assert (
            ws / "memory" / "b.md"
        ).read_text() == "b-third-later\n", "sibling edit must survive"
        assert result["paths"] == ["memory/a.md"]
        changed = _git_out(root, home, "show", "--name-only", "--format=", "HEAD").split()
        assert changed == ["memory/a.md"], "the recorded commit must touch nothing else"

    def test_it_adds_a_commit_rather_than_rewriting_history(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "roll-adds")
        head_before = _git_out(root, home, "rev-parse", "HEAD").strip()

        result = sh.rollback(root, target, paths=["memory/a.md"], home=home)

        assert sh.commit_count(root, home=home) == 4
        assert result["head"] not in (head_before, target)
        assert result["prior_head"] == head_before
        # Still reachable: the subset rollback is an ordinary descendant commit.
        assert head_before in _git_out(root, home, "log", "--format=%H").split()

    def test_prior_head_is_parked_in_a_service_ref(self, tmp_path):
        """Forward travel must work for the subset shape too, not just whole-root."""
        root, home, ws, target = _memory_fixture(tmp_path, "roll-ref")
        head_before = _git_out(root, home, "rev-parse", "HEAD").strip()

        result = sh.rollback(root, target, paths=["memory/a.md"], home=home)

        assert result["prior_ref"].startswith(sh.REF_PREFIX)
        assert (
            head_before in _git_out(root, home, "log", "--format=%H", result["prior_ref"]).split()
        )
        assert [r["sha"] for r in sh.forward_refs(root, home=home)] == [head_before]

    def test_a_path_added_after_the_target_is_removed(self, tmp_path):
        """ "Restore it to how it was" means the file was not there yet."""
        root, home, ws, target = _memory_fixture(tmp_path, "roll-remove")
        _write(ws / "memory" / "new.md", "added after the target\n")
        sh.commit(root, home=home)

        sh.rollback(root, target, paths=["memory/new.md"], home=home)

        assert not (ws / "memory" / "new.md").exists()
        assert (ws / "memory" / "a.md").read_text() == A_HEAD, "unnamed paths keep later edits"

    def test_several_paths_at_once(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "roll-many")

        result = sh.rollback(root, target, paths=["memory/b.md", "memory/a.md"], home=home)

        assert result["paths"] == ["memory/a.md", "memory/b.md"], "normalized and sorted"
        assert (ws / "memory" / "a.md").read_text() == A_TARGET
        assert (ws / "memory" / "b.md").read_text() == "b-second\n"

    def test_the_preview_is_restricted_to_the_subset(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "roll-preview")

        prev = sh.preview_rollback(root, target, paths=["memory/a.md"], home=home)

        assert [f["path"] for f in prev["files"]] == ["memory/a.md"]
        assert prev["paths"] == ["memory/a.md"]
        # No commit leaves the timeline for a subset, so the panel must not say one
        # does. This is the number the confirm dialog renders.
        assert prev["commits_rolled_away"] == 0
        assert "BETA-BAD" in prev["files"][0]["diff"]


# ── per-file revert ────────────────────────────────────────────────────────


class TestPerFileRevert:
    def test_a_sibling_is_untouched(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "rev-sibling")

        result = sh.revert(root, target, paths=["memory/a.md"], home=home)

        assert (ws / "memory" / "b.md").read_text() == "b-third-later\n"
        assert result["reverted"] == target
        assert result["paths"] == ["memory/a.md"]
        changed = _git_out(root, home, "show", "--name-only", "--format=", "HEAD").split()
        assert changed == ["memory/a.md"]

    def test_it_adds_a_commit(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "rev-adds")

        sh.revert(root, target, paths=["memory/a.md"], home=home)

        assert sh.commit_count(root, home=home) == 4

    def test_an_overlap_raises_naming_the_file_and_changes_nothing(self, tmp_path):
        """A later edit ON the reverted hunk. Loud, and byte-identical afterwards."""
        home, ws = _fresh(tmp_path, "rev-overlap")
        root = _root(home, ws, "memory")
        _write(ws / "memory" / "a.md", A_V1)
        sh.commit(root, home=home)
        _write(ws / "memory" / "a.md", A_TARGET)
        target = sh.commit(root, home=home)
        _write(ws / "memory" / "a.md", "alpha\nBETA-LATER\ngamma\n")
        head = sh.commit(root, home=home)
        before = (ws / "memory" / "a.md").read_bytes()

        with pytest.raises(sh.OverlapError) as exc:
            sh.revert(root, target, paths=["memory/a.md"], home=home)

        assert exc.value.files == ["memory/a.md"]
        assert "memory/a.md" in str(exc.value)
        assert (ws / "memory" / "a.md").read_bytes() == before
        assert _git_out(root, home, "rev-parse", "HEAD").strip() == head
        assert _git_out(root, home, "status", "--porcelain").strip() == ""
        assert sh.commit_count(root, home=home) == 3

    def test_an_overlap_outside_the_subset_does_not_block_it(self, tmp_path):
        """Scoping cuts both ways: only the SELECTED paths can veto the revert.

        The same commit is reverted twice — once for the conflicting file (must
        raise) and once for the clean one (must succeed) — so a subset that
        quietly widens to the whole commit fails the second half.
        """
        home, ws = _fresh(tmp_path, "rev-scope")
        root = _root(home, ws, "memory")
        _write(ws / "memory" / "a.md", A_V1)
        _write(ws / "memory" / "b.md", "one\ntwo\nthree\n")
        sh.commit(root, home=home)
        _write(ws / "memory" / "a.md", A_TARGET)
        _write(ws / "memory" / "b.md", "one\nTWO-BAD\nthree\n")
        target = sh.commit(root, home=home)
        _write(ws / "memory" / "a.md", A_HEAD)  # a separable later edit
        _write(ws / "memory" / "b.md", "one\nTWO-LATER\nthree\n")  # ON the reverted hunk
        sh.commit(root, home=home)

        with pytest.raises(sh.OverlapError) as exc:
            sh.revert(root, target, paths=["memory/b.md"], home=home)
        assert exc.value.files == ["memory/b.md"]

        # The repo is still usable, and b.md's overlap does not veto a.md.
        sh.revert(root, target, paths=["memory/a.md"], home=home)
        assert (ws / "memory" / "a.md").read_text() == A_REVERTED
        assert (ws / "memory" / "b.md").read_text() == "one\nTWO-LATER\nthree\n"

    def test_the_preview_is_restricted_to_the_subset(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "rev-preview")

        prev = sh.preview_revert(root, target, paths=["memory/b.md"], home=home)

        assert [f["path"] for f in prev["files"]] == ["memory/b.md"]
        assert prev["paths"] == ["memory/b.md"]
        assert prev["commits_rolled_away"] == 0

    def test_preview_dispatches_the_subset(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "rev-dispatch")
        for operation in ("rollback", "revert"):
            prev = sh.preview(root, target, operation=operation, paths=["memory/a.md"], home=home)
            assert prev["paths"] == ["memory/a.md"]
            assert [f["path"] for f in prev["files"]] == ["memory/a.md"]


# ── the subset is validated, never silently narrowed ───────────────────────


class TestSubsetValidation:
    @pytest.mark.parametrize(
        ("bad", "because"),
        [
            ("../escape.md", "escapes the root"),
            ("memory/../../escape.md", "escapes the root"),
            ("./..", "escapes the root"),
            ("/etc/passwd", "repo-relative"),
            ("/", "repo-relative"),
            ("", "empty path"),
        ],
    )
    def test_an_escaping_or_absolute_path_raises(self, tmp_path, bad, because):
        """Matched on the REASON, not merely on "something raised".

        Every one of these would also be refused later as "not changed by this
        operation", so a test that accepted any `HistoryError` would pass with the
        escape guard deleted — it would be measuring the wrong gate.
        """
        root, home, ws, target = _memory_fixture(tmp_path, "bad-path")
        for call in (sh.rollback, sh.revert, sh.preview_rollback, sh.preview_revert):
            with pytest.raises(sh.HistoryError, match=because):
                call(root, target, paths=[bad], home=home)

    def test_an_unknown_path_raises_and_changes_nothing(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "unknown-path")
        head = _git_out(root, home, "rev-parse", "HEAD").strip()

        for call in (sh.rollback, sh.revert, sh.preview_rollback, sh.preview_revert):
            with pytest.raises(sh.HistoryError, match="memory/nope.md"):
                call(root, target, paths=["memory/nope.md"], home=home)

        assert _git_out(root, home, "rev-parse", "HEAD").strip() == head
        assert sh.commit_count(root, home=home) == 3
        assert (ws / "memory" / "a.md").read_text() == A_HEAD
        # Validation runs BEFORE the ref is parked: a refused subset leaves no
        # trace at all, not even a forward-travel entry the panel would render.
        assert sh.forward_refs(root, home=home) == []

    def test_a_tracked_path_this_commit_did_not_touch_raises(self, tmp_path):
        """Not "does the file exist" — "does THIS operation change it"."""
        root, home, ws, target = _memory_fixture(tmp_path, "untouched-path")
        _write(ws / "memory" / "c.md", "only ever written once\n")
        sh.commit(root, home=home)

        with pytest.raises(sh.HistoryError, match="memory/c.md"):
            sh.revert(root, target, paths=["memory/c.md"], home=home)
        assert (ws / "memory" / "c.md").read_text() == "only ever written once\n"

    def test_a_mix_of_known_and_unknown_is_refused_whole(self, tmp_path):
        """The all-or-nothing rail: the known path must NOT be restored alone."""
        root, home, ws, target = _memory_fixture(tmp_path, "mixed-paths")

        with pytest.raises(sh.HistoryError, match="memory/nope.md"):
            sh.rollback(root, target, paths=["memory/a.md", "memory/nope.md"], home=home)

        assert (ws / "memory" / "a.md").read_text() == A_HEAD
        assert sh.commit_count(root, home=home) == 3

    def test_equivalent_spellings_normalize_to_one_entry(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "normalize")

        result = sh.rollback(
            root, target, paths=["./memory/a.md", "memory//a.md", "memory/a.md"], home=home
        )

        assert result["paths"] == ["memory/a.md"]
        assert (ws / "memory" / "a.md").read_text() == A_TARGET


# ── secrets survive the new mechanism too ──────────────────────────────────


class TestSecretsSurviveASubsetRestore:
    def test_a_gitignored_secret_in_the_memory_tree_keeps_its_bytes(self, tmp_path):
        root, home, ws, target = _memory_fixture(tmp_path, "subset-secret")
        secret = ws / "memory" / ".env"
        secret.write_text("OPENAI_API_KEY=sk-keep-me")
        before = secret.read_bytes()

        sh.rollback(root, target, paths=["memory/a.md"], home=home)

        assert (ws / "memory" / "a.md").read_text() == A_TARGET, "the rollback must have happened"
        assert secret.is_file(), "the ignored secret was deleted by the per-file rollback"
        assert secret.read_bytes() == before

    def test_the_credential_store_survives_a_per_file_rollback(self, tmp_path):
        home, ws = _fresh(tmp_path, "subset-creds")
        root = _root(home, ws, "config")
        creds = home / "security" / "credentials.json"
        creds.parent.mkdir(parents=True)
        creds.write_text("SUPER-SECRET")
        (home / ".local_secret").write_text("gateway-token")
        (home / "config.json").write_text('{"v": 1}')
        first = sh.commit(root, home=home)
        (home / "config.json").write_text('{"v": 2}')
        sh.commit(root, home=home)

        sh.rollback(root, first, paths=["config.json"], home=home)

        assert (home / "config.json").read_text() == '{"v": 1}'
        assert creds.read_text() == "SUPER-SECRET"
        assert (home / ".local_secret").read_text() == "gateway-token"

    @pytest.mark.parametrize("operation", ["rollback", "revert"])
    def test_an_untracked_file_is_not_swept_away(self, tmp_path, operation):
        """The same claim in its general form: a subset restore is not a clean."""
        root, home, ws, target = _memory_fixture(tmp_path, f"subset-untracked-{operation}")
        stray = ws / "memory" / "scratch.tmp"  # matches NOISE_EXCLUDE
        stray.write_text("work in progress")

        getattr(sh, operation)(root, target, paths=["memory/a.md"], home=home)

        assert stray.read_text() == "work in progress"
        assert (ws / "memory" / "a.md").read_text() != A_HEAD, "the restore must have happened"

    def test_the_module_never_invokes_git_clean(self, tmp_path):
        """Asserted over the SYNTAX TREE, not the text.

        The module's docstring discusses ``git clean`` in prose, so a substring
        scan would either always fail or have to special-case the docstring. This
        walks every ``_git(...)`` call and looks at the literal argv tokens.
        """
        source = Path(sh.__file__).read_text(encoding="utf-8")
        tokens: set[str] = set()
        calls = 0
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name) or func.id != "_git":
                continue
            calls += 1
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    tokens.add(arg.value)

        # Vacuity floor: a walk that matched nothing would "prove" anything.
        assert calls > 10, f"the _git call walk found only {calls} calls"
        assert {"reset", "checkout", "commit", "apply"} <= tokens, sorted(tokens)
        assert "clean" not in tokens, "a subset restore must never run `git clean`"
