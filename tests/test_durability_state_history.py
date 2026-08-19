"""DAS-9 — workspace time-travel: the seam, the debounce, rollback/revert, secrets.

Every test here runs against an isolated home AND an isolated workspace. Setting
only ``PERSONALCLAW_HOME`` does not confine the workspace: with no seeded
``workspace_dir`` the resolver falls through to the real ``~/workplace``, so the
memory root would be a git repository over the developer's actual workspace.

Four things these rails exist to prove, because each is a claim that reads true
and can be false:

* the ``atomic_write`` seam actually fires (a hook nobody calls is not a seam);
* the debounce COLLAPSES a burst into one commit and SERIALIZES per root — driven
  with a controllable clock, because a fixed ``sleep`` would measure a skeleton
  and pass just as happily with the debounce ripped out;
* secrets are gitignored AND survive a rollback with unchanged bytes — the two
  halves of a claim that sounds self-contradictory;
* the history never reaches a transport, proved by building the export entry set
  and looking for it, not by reading an exclusion list.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from personalclaw import atomic_write as aw
from personalclaw.durability import history_debounce as hd
from personalclaw.durability import inventory as inv
from personalclaw.durability import state_history as sh

pytestmark = pytest.mark.skipif(not sh.git_available(), reason="git is required for time-travel")


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Both rails: an isolated home AND an isolated workspace."""
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    home.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    # A leftover subscriber from another test would make a "the seam fires" rail
    # pass for the wrong reason.
    for hook in aw.post_write_hooks():
        aw.unregister_post_write_hook(hook)
    yield
    hd.uninstall(flush=False)
    for hook in aw.post_write_hooks():
        aw.unregister_post_write_hook(hook)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def ws(tmp_path) -> Path:
    return tmp_path / "ws"


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


# ── the atomic_write seam ──────────────────────────────────────────────────


class TestPostWriteSeam:
    def test_atomic_write_notifies_registered_hooks(self, tmp_path):
        seen: list[Path] = []
        aw.register_post_write_hook(seen.append)
        aw.atomic_write(tmp_path / "a.json", "{}")
        aw.atomic_write_bytes(tmp_path / "b.bin", b"\x00")
        assert [p.name for p in seen] == [
            "a.json",
            "b.bin",
        ], "both the text and the bytes entry point must ride the same seam"

    def test_a_failing_hook_never_fails_the_write(self, tmp_path):
        def boom(_path):
            raise RuntimeError("hook exploded")

        aw.register_post_write_hook(boom)
        target = tmp_path / "c.json"
        aw.atomic_write(target, "payload")
        assert target.read_text() == "payload"

    def test_a_hook_that_writes_does_not_recurse(self, tmp_path):
        calls: list[Path] = []

        def reentrant(path: Path) -> None:
            calls.append(path)
            if len(calls) < 5:  # would recurse forever without the guard
                aw.atomic_write(tmp_path / "nested.json", "x")

        aw.register_post_write_hook(reentrant)
        aw.atomic_write(tmp_path / "outer.json", "y")
        assert [p.name for p in calls] == ["outer.json"]

    def test_unregister_is_idempotent(self, tmp_path):
        def hook(_p):
            pass

        aw.register_post_write_hook(hook)
        aw.register_post_write_hook(hook)  # double-register must subscribe once
        assert aw.post_write_hooks().count(hook) == 1
        aw.unregister_post_write_hook(hook)
        aw.unregister_post_write_hook(hook)  # unknown hook is a no-op
        assert hook not in aw.post_write_hooks()

    def test_the_seam_is_wired_to_the_debouncer_by_install(self, home):
        debouncer = hd.install(home=home, start=False)
        assert debouncer is not None
        assert (
            debouncer.notify in aw.post_write_hooks()
        ), "install() must SUBSCRIBE — a debouncer nobody notifies is inert"
        hd.uninstall(flush=False)
        assert debouncer.notify not in aw.post_write_hooks()


# ── roots and path classification ──────────────────────────────────────────


class TestRoots:
    def test_every_done_when_root_is_covered(self, home, ws):
        ids = {r.id for r in sh.roots(home=home, workspace=ws)}
        assert ids == {"config", "skills", "prompts", "projects", "memory"}

    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("config.json", "config"),
            ("entity_settings/default.json", "config"),
            ("skills/mine/SKILL.md", "skills"),
            ("prompts/p.json", "prompts"),
            ("prompt_snippets/s.md", "prompts"),
            ("projects/proj-1/context/notes.md", "projects"),
        ],
    )
    def test_home_paths_route_to_their_root(self, home, ws, rel, expected):
        assert sh.root_for_path(home / rel, home=home) is not None
        assert sh.root_for_path(home / rel, home=home).id == expected

    def test_memory_tree_and_ext_route_to_the_memory_root(self, home, ws):
        for rel in ("memory/MEMORY.md", "_ext/some-slug/memory/notes.md"):
            root = sh.root_for_path(ws / rel, home=home)
            assert root is not None and root.id == "memory", rel

    def test_untracked_home_paths_route_nowhere(self, home):
        for rel in ("security/credentials.json", ".local_secret", "knowledge/knowledge.db"):
            assert sh.root_for_path(home / rel, home=home) is None, rel

    def test_history_storage_is_recognised_as_its_own(self, home):
        assert sh.is_history_path(sh.history_dir(home) / "config.git" / "HEAD", home=home)
        assert not sh.is_history_path(home / "config.json", home=home)


# ── committing + timeline ──────────────────────────────────────────────────


class TestCommit:
    def test_commits_only_the_allowlisted_paths(self, home, ws):
        (home / "config.json").write_text('{"v": 1}')
        (home / "entity_settings").mkdir()
        (home / "entity_settings" / "default.json").write_text("{}")
        (home / "security").mkdir()
        (home / "security" / "credentials.json").write_text("SUPER-SECRET")
        (home / ".local_secret").write_text("token-abc")
        root = _root(home, ws, "config")

        sha = sh.commit(root, home=home)
        assert sha, "a first write must produce a commit"
        tracked = sorted(_git_out(root, home, "ls-files").split())
        assert tracked == ["config.json", "entity_settings/default.json"]

    def test_an_unchanged_tree_produces_no_commit(self, home, ws):
        (home / "config.json").write_text("{}")
        root = _root(home, ws, "config")
        assert sh.commit(root, home=home)
        assert sh.commit(root, home=home) is None, "empty commits would make git log useless"
        assert sh.commit_count(root, home=home) == 1

    def test_timeline_carries_the_surface_and_the_unattended_filter(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("1")
        sh.commit(root, home=home, surface=sh.SURFACE_INTERACTIVE)
        (home / "config.json").write_text("2")
        with sh.writing_surface(sh.SURFACE_SCHEDULED):
            sh.commit(root, home=home)

        # Vacuity floor: the repo must actually hold commits, or every assertion
        # below is true of an empty timeline.
        assert sh.commit_count(root, home=home) == 2

        entries = sh.timeline(root, home=home)
        assert [e["surface"] for e in entries] == [sh.SURFACE_SCHEDULED, sh.SURFACE_INTERACTIVE]
        slept = sh.timeline(root, home=home, unattended_only=True)
        assert len(slept) == 1 and slept[0]["surface"] == sh.SURFACE_SCHEDULED
        assert slept[0]["unattended"] is True

    def test_timeline_of_an_empty_repo_is_empty_not_an_error(self, home, ws):
        root = _root(home, ws, "skills")
        assert sh.timeline(root, home=home) == []
        assert sh.commit_count(root, home=home) == 0

    def test_projects_root_tracks_context_and_nothing_else(self, home, ws):
        ctx = home / "projects" / "p1" / "context"
        ctx.mkdir(parents=True)
        (ctx / "brief.md").write_text("brief")
        (home / "projects" / "p1" / "runs.json").write_text("[]")
        root = _root(home, ws, "projects")
        assert sh.commit(root, home=home)
        assert sorted(_git_out(root, home, "ls-files").split()) == ["p1/context/brief.md"]


# ── secrets: BOTH halves of the contradiction ──────────────────────────────


class TestSecrets:
    def test_a_secret_never_enters_a_commit_object(self, home, ws):
        """Half one: no blob in the object database holds the secret bytes.

        Asserted over every reachable object, not over `ls-files`: a secret that
        was committed once and removed later would still be in the history, and
        `ls-files` would report it clean.
        """
        (home / "config.json").write_text("{}")
        (home / ".env").write_text("OPENAI_API_KEY=sk-do-not-commit")
        (home / "security").mkdir()
        (home / "security" / "credentials.json").write_text("SUPER-SECRET")
        (home / "skills").mkdir()
        (home / "skills" / ".env").write_text("ANTHROPIC_API_KEY=sk-also-secret")

        cfg = _root(home, ws, "config")
        skills = _root(home, ws, "skills")
        for _ in range(3):
            (home / "config.json").write_text('{"n": 1}')
            (home / "skills" / "s.md").write_text("skill body")
            sh.commit(cfg, home=home)
            sh.commit(skills, home=home)
        # Vacuity floor: without commits there is no object database to search.
        assert sh.commit_count(cfg, home=home) >= 1
        assert sh.commit_count(skills, home=home) >= 1

        for root in (cfg, skills):
            objects = _git_out(root, home, "rev-list", "--all", "--objects")
            names = {line.split(" ", 1)[1] for line in objects.splitlines() if " " in line}
            assert not {
                n for n in names if ".env" in n or "credentials" in n
            }, f"secret-shaped path reached the {root.id} object database: {names}"
            for line in objects.splitlines():
                sha = line.split(" ", 1)[0]
                kind = _git_out(root, home, "cat-file", "-t", sha).strip()
                if kind != "blob":
                    continue
                body = _git_out(root, home, "cat-file", "-p", sha)
                assert "sk-do-not-commit" not in body
                assert "sk-also-secret" not in body
                assert "SUPER-SECRET" not in body

    def test_a_secret_survives_a_rollback_with_unchanged_bytes(self, home, ws):
        """Half two: the hard reset does not delete what the ignore excluded.

        This is the half that a naive implementation gets wrong by reaching for
        `git clean -fdx` to make the tree "match" the target commit.
        """
        secret = home / ".env"
        secret.write_text("OPENAI_API_KEY=sk-keep-me")
        before = secret.read_bytes()
        (home / "config.json").write_text('{"v": 1}')
        root = _root(home, ws, "config")
        first = sh.commit(root, home=home)
        (home / "config.json").write_text('{"v": 2}')
        sh.commit(root, home=home)
        assert sh.commit_count(root, home=home) == 2

        sh.rollback(root, first, home=home)

        assert (home / "config.json").read_text() == '{"v": 1}', "the rollback must have happened"
        assert secret.is_file(), "the ignored secret was deleted by the rollback"
        assert secret.read_bytes() == before, "the ignored secret's bytes changed"

    def test_credential_store_survives_a_rollback_that_removes_a_tracked_file(self, home, ws):
        creds = home / "security" / "credentials.json"
        creds.parent.mkdir(parents=True)
        creds.write_text("SUPER-SECRET")
        (home / "config.json").write_text("{}")
        root = _root(home, ws, "config")
        first = sh.commit(root, home=home)
        (home / "entity_settings").mkdir()
        (home / "entity_settings" / "new.json").write_text("{}")
        sh.commit(root, home=home)

        sh.rollback(root, first, home=home)

        assert not (home / "entity_settings" / "new.json").exists(), "tracked add must be undone"
        assert creds.read_text() == "SUPER-SECRET"


# ── rollback vs revert ─────────────────────────────────────────────────────


class TestRollback:
    def test_prior_head_is_preserved_in_a_service_ref(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("1")
        first = sh.commit(root, home=home)
        (home / "config.json").write_text("2")
        second = sh.commit(root, home=home)

        result = sh.rollback(root, first, home=home)

        assert result["prior_head"] == second
        assert result["prior_ref"].startswith(sh.REF_PREFIX)
        # The point of the ref: the rolled-away commit stays LISTABLE, so forward
        # travel is possible instead of the commit being garbage.
        listed = _git_out(root, home, "log", "--format=%H", result["prior_ref"]).split()
        assert second in listed
        refs = sh.forward_refs(root, home=home)
        assert [r["sha"] for r in refs] == [second]

    def test_rollback_refuses_a_commit_that_is_not_in_this_repo(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("1")
        sh.commit(root, home=home)
        with pytest.raises(sh.HistoryError):
            sh.rollback(root, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", home=home)
        with pytest.raises(sh.HistoryError):
            sh.rollback(root, "HEAD~1", home=home)  # not a bare id: refused, not resolved

    def test_git_dir_outside_the_history_dir_is_refused(self, home, ws, tmp_path):
        """The guard that keeps `reset --hard` off anything that is not ours."""
        rogue = sh.HistoryRoot(
            id="../../rogue", label="Rogue", worktree=tmp_path, include=(), memory=False
        )
        with pytest.raises(sh.HistoryError, match="outside"):
            sh.ensure_repo(rogue, home=home)


class TestRevert:
    def test_revert_keeps_later_non_overlapping_edits(self, home, ws):
        root = _root(home, ws, "config")
        (home / "entity_settings").mkdir()
        (home / "config.json").write_text("cfg-v1")
        sh.commit(root, home=home)
        (home / "entity_settings" / "bad.json").write_text("bad")
        bad = sh.commit(root, home=home)
        (home / "config.json").write_text("cfg-v2")  # a LATER, unrelated edit
        sh.commit(root, home=home)
        assert sh.commit_count(root, home=home) == 3

        sh.revert(root, bad, home=home)

        assert not (home / "entity_settings" / "bad.json").exists(), "the bad add must be undone"
        assert (home / "config.json").read_text() == "cfg-v2", "the later edit must survive"
        assert sh.commit_count(root, home=home) == 4, "a revert ADDS a commit, it does not rewrite"

    def test_overlap_fails_loudly_naming_the_blocking_file(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("line-1\n")
        sh.commit(root, home=home)
        (home / "config.json").write_text("line-2\n")
        target = sh.commit(root, home=home)
        (home / "config.json").write_text("line-3\n")
        head = sh.commit(root, home=home)

        with pytest.raises(sh.OverlapError) as exc:
            sh.revert(root, target, home=home)

        assert exc.value.files == ["config.json"]
        assert "config.json" in str(exc.value)
        # Aborted cleanly: the work tree and HEAD are exactly as they were.
        assert (home / "config.json").read_text() == "line-3\n"
        assert _git_out(root, home, "rev-parse", "HEAD").strip() == head
        assert _git_out(root, home, "status", "--porcelain").strip() == ""


class TestPreview:
    def test_rollback_preview_names_files_and_renders_diffs(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("v1\n")
        first = sh.commit(root, home=home)
        (home / "config.json").write_text("v2\n")
        (home / "entity_settings").mkdir()
        (home / "entity_settings" / "e.json").write_text("added\n")
        sh.commit(root, home=home)

        prev = sh.preview_rollback(root, first, home=home)

        assert prev["operation"] == "rollback"
        assert prev["commits_rolled_away"] == 1
        paths = sorted(f["path"] for f in prev["files"])
        assert paths == ["config.json", "entity_settings/e.json"]
        cfg = next(f for f in prev["files"] if f["path"] == "config.json")
        assert cfg["rendered"] is True and "-v2" in cfg["diff"] and "+v1" in cfg["diff"]
        # A preview must not touch anything.
        assert (home / "config.json").read_text() == "v2\n"

    def test_revert_preview_shows_the_inverse_patch(self, home, ws):
        root = _root(home, ws, "config")
        (home / "config.json").write_text("keep\n")
        sh.commit(root, home=home)
        (home / "config.json").write_text("keep\nremove-me\n")
        target = sh.commit(root, home=home)

        prev = sh.preview_revert(root, target, home=home)

        assert prev["operation"] == "revert"
        diff = next(f for f in prev["files"] if f["path"] == "config.json")["diff"]
        assert "-remove-me" in diff

    def test_a_huge_diff_is_listed_not_rendered(self, home, ws, monkeypatch):
        monkeypatch.setattr(sh, "MAX_DIFF_BYTES", 64)
        root = _root(home, ws, "config")
        (home / "config.json").write_text("small\n")
        first = sh.commit(root, home=home)
        (home / "config.json").write_text("x" * 4096 + "\n")
        sh.commit(root, home=home)

        entry = next(
            f
            for f in sh.preview_rollback(root, first, home=home)["files"]
            if f["path"] == "config.json"
        )
        assert entry["rendered"] is False and entry["diff"] == ""
        assert entry["bytes"] > 64


# ── the adaptive debounce ──────────────────────────────────────────────────


class FakeClock:
    """A clock the test advances explicitly. No sleeping anywhere in these rails."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


class TestDebounce:
    def test_the_delay_ramps_from_ten_seconds_to_zero(self):
        assert hd.delay_for_writes(1) == pytest.approx(10.0)
        ramp = [hd.delay_for_writes(n) for n in range(1, hd.SUSTAINED_WRITES + 2)]
        assert ramp == sorted(ramp, reverse=True), "sustained writing must TIGHTEN the delay"
        assert ramp[-1] == 0.0, "the ramp must actually reach 0, not merely approach it"

    def test_a_burst_of_writes_collapses_into_one_commit(self, home, ws):
        root = _root(home, ws, "config")
        clock = FakeClock()
        commits: list[str] = []

        def committer(r, *, surface, home):  # noqa: A002 - mirrors sh.commit
            sha = sh.commit(r, surface=surface, home=home)
            commits.append(sha or "")
            return sha

        deb = hd.HistoryDebouncer(home=home, clock=clock, committer=committer)

        for i in range(5):
            (home / "config.json").write_text(f"v{i}")
            assert deb.notify(home / "config.json") is True
            clock.advance(0.1)
            # Nothing may fire while the window is open.
            assert deb.run_pending() == []

        assert deb.notifications == 5
        assert commits == [], "five writes inside the window must not produce five commits"

        # The 5th write's delay is 0.625s; cross it and exactly one commit lands.
        clock.advance(1.0)
        results = deb.run_pending()
        assert len(results) == 1 and results[0]["writes"] == 5
        assert sh.commit_count(root, home=home) == 1, "five writes → ONE commit"
        assert len(commits) == 1

    def test_the_rail_fails_when_the_debounce_is_disabled(self, home, ws):
        """The vacuity check for the collapse rail above.

        With the debounce neutered (every write due immediately) the same driving
        produces one commit PER pass, which is what the collapse assertion above
        must be able to detect.
        """
        _root(home, ws, "config")
        clock = FakeClock()
        deb = hd.HistoryDebouncer(home=home, clock=clock)
        passes = 0
        for i in range(3):
            (home / "config.json").write_text(f"v{i}")
            deb.notify(home / "config.json")
            # Simulate "no debounce": force everything due on every pass.
            passes += len(deb.run_pending(force=True))
            clock.advance(0.1)
        assert passes == 3, "without debouncing each write commits separately"

    def test_writes_after_the_burst_window_start_a_fresh_ramp(self, home, ws):
        _root(home, ws, "config")
        clock = FakeClock()
        deb = hd.HistoryDebouncer(home=home, clock=clock, committer=lambda *a, **k: None)
        (home / "config.json").write_text("a")
        deb.notify(home / "config.json")
        assert deb.pending_delay("config") == pytest.approx(hd.BASE_DELAY_SECS)
        deb.notify(home / "config.json")
        assert deb.pending_delay("config") == pytest.approx(hd.BASE_DELAY_SECS * hd.DECAY)

        clock.advance(hd.BURST_WINDOW_SECS + 1)
        deb.run_pending()
        deb.notify(home / "config.json")
        assert deb.pending_delay("config") == pytest.approx(
            hd.BASE_DELAY_SECS
        ), "a write after a quiet period is a NEW burst, not a continuation"

    def test_untracked_writes_are_ignored(self, home, ws):
        deb = hd.HistoryDebouncer(home=home, clock=FakeClock())
        assert deb.notify(home / "security" / "credentials.json") is False
        assert deb.notify(sh.history_dir(home) / "config.git" / "index") is False
        assert deb.pending_roots() == ()

    def test_commits_are_serialized_per_root(self, home, ws):
        """No two commits race on one root — measured, not assumed.

        Two threads are held inside the committer at the same time by a barrier;
        if the per-root lock did not exist, `inside` would reach 2 and two git
        processes would be writing one index.
        """
        _root(home, ws, "config")
        clock = FakeClock()
        inside = 0
        peak = 0
        attempts = 0
        entered = threading.Event()
        release = threading.Event()
        guard = threading.Lock()

        def committer(root, *, surface, home):  # noqa: ARG001
            nonlocal inside, peak, attempts
            with guard:
                attempts += 1
                inside += 1
                peak = max(peak, inside)
            entered.set()
            release.wait(timeout=5)
            with guard:
                inside -= 1
            return "sha"

        deb = hd.HistoryDebouncer(home=home, clock=clock, committer=committer)
        (home / "config.json").write_text("v")
        deb.notify(home / "config.json")
        clock.advance(hd.BASE_DELAY_SECS + 1)

        first = threading.Thread(target=lambda: deb.run_pending())
        first.start()
        assert entered.wait(timeout=5), "the first commit never started"

        # Second write arrives while the first commit holds the root.
        deb.notify(home / "config.json")
        clock.advance(hd.BASE_DELAY_SECS + 1)
        second_results: list[dict] = []
        second = threading.Thread(target=lambda: second_results.extend(deb.run_pending()))
        second.start()
        second.join(timeout=5)

        assert second_results and second_results[0].get("skipped") == "busy", (
            "the second pass must observe the root as BUSY — otherwise this rail "
            "never exercised the lock and proves nothing"
        )
        assert deb.skips_busy == 1
        release.set()
        first.join(timeout=5)
        assert peak == 1, f"two commits ran concurrently on one root (peak={peak})"
        assert attempts == 1

        # The skipped work is re-armed, not lost.
        assert "config" in deb.pending_roots()

    def test_flush_commits_everything_pending(self, home, ws):
        root = _root(home, ws, "config")
        clock = FakeClock()
        deb = hd.HistoryDebouncer(home=home, clock=clock)
        (home / "config.json").write_text("v")
        deb.notify(home / "config.json")
        assert sh.commit_count(root, home=home) == 0
        deb.flush()
        assert sh.commit_count(root, home=home) == 1

    def test_a_burst_containing_an_unattended_write_is_attributed_unattended(self, home, ws):
        root = _root(home, ws, "config")
        clock = FakeClock()
        deb = hd.HistoryDebouncer(home=home, clock=clock)
        (home / "config.json").write_text("a")
        deb.notify(home / "config.json")
        with sh.writing_surface(sh.SURFACE_BACKGROUND):
            (home / "config.json").write_text("b")
            deb.notify(home / "config.json")
        deb.flush()
        assert sh.commit_count(root, home=home) == 1
        assert sh.timeline(root, home=home)[0]["surface"] == sh.SURFACE_BACKGROUND

    def test_end_to_end_through_the_real_atomic_write_seam(self, home, ws):
        """The seam, the router and the committer wired together as they ship."""
        root = _root(home, ws, "config")
        clock = FakeClock()
        deb = hd.HistoryDebouncer(home=home, clock=clock)
        aw.register_post_write_hook(deb.notify)

        aw.atomic_write(home / "config.json", '{"through": "the seam"}')
        aw.atomic_write(home / "security" / "credentials.json", "SUPER-SECRET")

        assert deb.pending_roots() == ("config",), "only the tracked write may arm a commit"
        clock.advance(hd.BASE_DELAY_SECS + 1)
        deb.run_pending()
        assert sh.commit_count(root, home=home) == 1
        assert sorted(_git_out(root, home, "ls-files").split()) == ["config.json"]


# ── hourly memory commit (§3's deferred piece) ─────────────────────────────


class TestHourlyMemoryCommit:
    def test_commits_the_memory_tree_as_a_scheduled_surface(self, home, ws):
        (ws / "memory").mkdir(parents=True)
        (ws / "memory" / "MEMORY.md").write_text("# what I learned\n")
        results = sh.commit_memory_roots(home=home)
        assert [r["root"] for r in results] == ["memory"], "only memory roots run hourly"
        assert results[0]["changed"] is True

        root = next(r for r in sh.roots(home=home, workspace=ws) if r.id == "memory")
        assert sh.commit_count(root, home=home) == 1  # vacuity floor
        entry = sh.timeline(root, home=home)[0]
        assert entry["surface"] == sh.SURFACE_SCHEDULED and entry["unattended"] is True

    def test_the_service_job_runs_it_and_is_gated_on_the_config_flag(self, home, ws, monkeypatch):
        from personalclaw.durability import service

        (ws / "memory").mkdir(parents=True)
        (ws / "memory" / "MEMORY.md").write_text("hourly\n")

        result = service.run_history_commit()
        assert result.ok and not result.skipped, result.detail
        root = next(r for r in sh.roots(home=home, workspace=ws) if r.id == "memory")
        assert sh.commit_count(root, home=home) == 1

        class Off:
            time_travel = False

        monkeypatch.setattr(service, "_cfg", lambda: Off())
        assert service.run_history_commit().skipped == "time travel is off"

    def test_run_due_jobs_includes_the_history_job(self, home, ws, monkeypatch):
        import time

        from personalclaw.durability import service

        (ws / "memory").mkdir(parents=True)
        (ws / "memory" / "MEMORY.md").write_text("x\n")
        # Stamp the OTHER jobs as just-run so this rail exercises the history leg
        # alone; without this the tick also takes a snapshot and a restore drill.
        now = time.time()
        service.save_state(
            {"last_export": now, "last_snapshot": now, "last_drill": now, "last_sync": now}
        )

        results = service.run_due_jobs(force="history", now=now)

        assert [r.job for r in results] == ["history_commit"]
        assert results[0].ok, results[0].detail
        assert float(service.load_state().get("last_history", 0)) == pytest.approx(now)
        root = next(r for r in sh.roots(home=home, workspace=ws) if r.id == "memory")
        assert sh.commit_count(root, home=home) == 1  # vacuity floor


# ── time-travel never syncs ────────────────────────────────────────────────


class TestNeverSyncs:
    def test_the_export_entry_set_contains_no_history_path(self):
        """Built from the projection the transports actually use, not from a list."""
        entries = inv.export_entries()
        assert entries, "vacuity floor: an empty projection would pass trivially"
        for entry in entries:
            assert not sh.is_history_path(entry.path), entry.id
            assert sh.HISTORY_DIR_NAME not in entry.path.split("/")
        for entry in inv.backup_entries(include_derived=True):
            assert sh.HISTORY_DIR_NAME not in entry.path.split("/"), entry.id

    def test_a_real_shard_export_carries_nothing_from_the_history(self, home, ws, tmp_path):
        """Drive the actual exporter over a home with a POPULATED history repo."""
        from personalclaw.durability import shards

        (home / "config.json").write_text('{"v": 1}')
        (ws / "memory").mkdir(parents=True)
        (ws / "memory" / "MEMORY.md").write_text("remembered\n")
        root = _root(home, ws, "config")
        sh.commit(root, home=home)
        sh.commit_memory_roots(home=home)
        # Vacuity floor: the history must actually exist, or "absent from the
        # export" is true of nothing.
        assert sh.commit_count(root, home=home) >= 1
        history_files = list(sh.history_dir(home).rglob("*"))
        assert len(history_files) > 10, "the history repos must be populated"

        out = tmp_path / "shards"
        shards.export_shards(home, out)
        produced = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
        assert produced, "vacuity floor: an export that produced nothing proves nothing"
        assert not [p for p in produced if sh.HISTORY_DIR_NAME in p.split("/")]
        blob = "\n".join(p.read_text(errors="replace") for p in out.rglob("*") if p.is_file())
        assert "state-history" not in blob
        assert ".git" not in blob

    def test_the_history_dir_is_ignored_by_the_claims_everything_audit(self, home, ws):
        """It must be IGNORED, not unclaimed: an unclaimed path fails the audit."""
        (home / "config.json").write_text("{}")
        root = _root(home, ws, "config")
        sh.commit(root, home=home)
        assert sh.history_dir(home).is_dir()
        assert inv.is_ignored(sh.HISTORY_DIR_NAME)
        audit = inv.audit_home(home)
        assert sh.HISTORY_DIR_NAME + "/" not in audit.unclaimed
        assert audit.ignored >= 1


# ── config round-trip ──────────────────────────────────────────────────────


class TestConfig:
    def test_the_default_and_to_dict_agree(self):
        from personalclaw.config.loader import AppConfig

        assert AppConfig().durability.time_travel is True
        assert AppConfig().to_dict()["durability"]["time_travel"] is True

    def test_the_field_round_trips_through_load(self, home):
        import json

        from personalclaw.config.loader import AppConfig

        (home / "config.json").write_text(json.dumps({"durability": {"time_travel": False}}))
        loaded = AppConfig.load()
        assert loaded.durability.time_travel is False
        assert loaded.to_dict()["durability"]["time_travel"] is False

    def test_the_field_carries_ui_metadata(self):
        from dataclasses import fields

        from personalclaw.config.loader import DurabilityConfig

        meta = next(f for f in fields(DurabilityConfig) if f.name == "time_travel").metadata
        assert meta.get("label"), "a user-facing flag needs a label for the settings surface"
        assert meta.get("help")

    def test_it_is_in_the_patch_allowlist(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        assert _EDITABLE_CONFIG["durability.time_travel"] == {"type": "bool"}

    def test_an_unreadable_value_fails_open(self, home):
        import json

        from personalclaw.config.loader import AppConfig

        (home / "config.json").write_text(json.dumps({"durability": {"time_travel": "nope"}}))
        assert (
            AppConfig.load().durability.time_travel is True
        ), "history is fail-OPEN: a garbled config must not silently stop recording"


# ── the routes ─────────────────────────────────────────────────────────────


def _app(*, app_token: str = ""):
    from aiohttp import web

    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = app_token
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_get("/api/durability/history", mod.api_durability_history)
    app.router.add_get(
        "/api/durability/history/{root}/timeline", mod.api_durability_history_timeline
    )
    app.router.add_post("/api/durability/history/{root}/{op}", mod.api_durability_history_operate)
    return app


def _client():
    from aiohttp.test_utils import TestClient, TestServer

    return TestClient(TestServer(_app()))


@pytest.fixture
def seeded(home, ws, monkeypatch):
    """Two commits on the config root, reachable through `active_home()`.

    `config_dir` is pinned as well as the env var: the handlers resolve the home
    through `service.active_home()`, and a route test that only sets the env var
    would still let a fallback read the developer's real home.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    root = _root(home, ws, "config")
    (home / "config.json").write_text("route-v1\n")
    first = sh.commit(root, home=home)
    (home / "config.json").write_text("route-v2\n")
    second = sh.commit(root, home=home)
    assert sh.commit_count(root, home=home) == 2  # vacuity floor for every route rail
    return root, first, second


class TestHistoryRoutes:
    @pytest.mark.asyncio
    async def test_status_lists_the_roots(self, seeded):
        async with _client() as client:
            resp = await client.get("/api/durability/history")
            assert resp.status == 200
            body = await resp.json()
        assert body["enabled"] is True and body["git"] is True
        by_id = {r["id"]: r for r in body["roots"]}
        assert by_id["config"]["commits"] == 2
        assert set(by_id) == {"config", "skills", "prompts", "projects", "memory"}

    @pytest.mark.asyncio
    async def test_timeline_route_and_unattended_filter(self, seeded, home, ws):
        root, _first, _second = seeded
        (home / "config.json").write_text("route-v3\n")
        with sh.writing_surface(sh.SURFACE_SCHEDULED):
            sh.commit(root, home=home)
        async with _client() as client:
            resp = await client.get("/api/durability/history/config/timeline")
            body = await resp.json()
            assert resp.status == 200 and body["commits"] == 3
            assert len(body["entries"]) == 3

            resp = await client.get("/api/durability/history/config/timeline?unattended=1")
            filtered = await resp.json()
        assert len(filtered["entries"]) == 1
        assert filtered["entries"][0]["surface"] == sh.SURFACE_SCHEDULED

    @pytest.mark.asyncio
    async def test_unknown_root_is_404(self, seeded):
        async with _client() as client:
            resp = await client.get("/api/durability/history/nope/timeline")
            assert resp.status == 404
            assert (await resp.json())["error"]["code"] == "unknown_root"

    @pytest.mark.asyncio
    async def test_a_request_without_confirm_returns_the_preview_and_changes_nothing(
        self, seeded, home
    ):
        _root_, first, _second = seeded
        async with _client() as client:
            resp = await client.post("/api/durability/history/config/rollback", json={"sha": first})
            assert resp.status == 200
            body = await resp.json()
        assert body["confirmed"] is False
        assert body["expected_head"]
        assert [f["path"] for f in body["preview"]["files"]] == ["config.json"]
        assert (home / "config.json").read_text() == "route-v2\n", "a preview must not act"

    @pytest.mark.asyncio
    async def test_confirming_with_the_previewed_head_performs_the_rollback(self, seeded, home):
        _root_, first, second = seeded
        async with _client() as client:
            preview = await (
                await client.post("/api/durability/history/config/rollback", json={"sha": first})
            ).json()
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": first, "confirm": True, "expected_head": preview["expected_head"]},
            )
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        assert body["ok"] is True and body["prior_head"] == second
        assert body["reload_required"] is True, "a config rollback needs a process reload"
        assert (home / "config.json").read_text() == "route-v1\n"

    @pytest.mark.asyncio
    async def test_a_stale_preview_is_refused(self, seeded, home):
        """The preview is MANDATORY because a confirm needs the head it saw."""
        root, first, _second = seeded
        async with _client() as client:
            preview = await (
                await client.post("/api/durability/history/config/rollback", json={"sha": first})
            ).json()
            # The history moves underneath the preview.
            (home / "config.json").write_text("route-v3\n")
            sh.commit(root, home=home)
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": first, "confirm": True, "expected_head": preview["expected_head"]},
            )
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "preview_stale"
        assert (home / "config.json").read_text() == "route-v3\n", "the refused call must not act"

    @pytest.mark.asyncio
    async def test_confirming_without_an_expected_head_is_refused(self, seeded, home):
        _root_, first, _second = seeded
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback", json={"sha": first, "confirm": True}
            )
            assert resp.status == 409
        assert (home / "config.json").read_text() == "route-v2\n"

    @pytest.mark.asyncio
    async def test_a_revert_overlap_is_a_409_naming_the_files(self, seeded, home):
        _root_, _first, second = seeded
        root = _root_
        (home / "config.json").write_text("route-v3\n")
        sh.commit(root, home=home)
        async with _client() as client:
            preview = await (
                await client.post("/api/durability/history/config/revert", json={"sha": second})
            ).json()
            resp = await client.post(
                "/api/durability/history/config/revert",
                json={"sha": second, "confirm": True, "expected_head": preview["expected_head"]},
            )
            assert resp.status == 409
            body = await resp.json()
        assert body["error"]["code"] == "revert_overlap"
        assert body["files"] == ["config.json"]
        assert (home / "config.json").read_text() == "route-v3\n"

    @pytest.mark.asyncio
    async def test_unknown_sha_and_bad_operation_are_refused(self, seeded):
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback", json={"sha": "f" * 40}
            )
            assert resp.status == 404
            assert (await resp.json())["error"]["code"] == "unknown_commit"

            resp = await client.post("/api/durability/history/config/obliterate", json={"sha": "a"})
            assert resp.status == 404
            assert (await resp.json())["error"]["code"] == "unknown_operation"

            resp = await client.post("/api/durability/history/config/rollback", json={})
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "sha_required"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/durability/history"),
            ("get", "/api/durability/history/config/timeline"),
            ("post", "/api/durability/history/config/rollback"),
        ],
    )
    async def test_an_app_scoped_caller_is_refused(self, seeded, method, path):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(_app(app_token="notes"))) as client:
            resp = await getattr(client, method)(path, json={"sha": "a" * 40})
            assert resp.status == 403
            assert (await resp.json())["error"]["code"] == "owner_only"
