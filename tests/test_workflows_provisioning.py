"""Workspace provisioning + teardown wiring (WORK-CONTAINERS §4.1, WF2WOR-4).

S49 and S52 shipped the two DECISION layers and both had zero production callers, so a spec's
`workspace:` block was parsed nowhere and every run silently ran in place. These tests pin the
performer and its two call sites, and they drive a REAL git repo for the same reason S52 did: the
properties under test are facts about git's behaviour, not about our wrappers.

Four things were MEASURED against a real repo before writing the module, and three of them
changed the design:

* `git add -A` in a worktree commits the preserved `.env` AND `.pclaw-setup/` into the run branch.
  So the durable record of a run's work would carry the user's local credentials into git history,
  and both reintegration verbs would then offer to apply them. `_commit_outstanding` excludes them
  with git pathspecs at the ADD, not at review time — a review filter cannot un-commit a secret.
* `git branch -m` on a branch checked out in a LIVE worktree succeeds and the worktree follows it,
  which is what makes the task→run branch rename free. But `-m` onto an EXISTING name fails with
  fatal 128, and a retried run finds its own previous run-branch already there — so it is `-M`.
* `git commit` with nothing staged exits 1 with "nothing added to commit". That means "there was
  nothing to save", not "saving failed", so it is not an error.
* `git checkout <branch>` REFUSES a branch a live worktree holds (fatal 128). That is why both
  verbs are offers the user runs rather than actions the gateway performs — the safe order depends
  on state the gateway does not own.

Isolation: `PERSONALCLAW_HOME` is set, not just `config_dir` patched. Measured previously (S52):
patching the module attribute redirected every consumer and broke an unrelated test in the full
xdist mix.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from personalclaw.workflows import provisioning, store
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.workspace import Mode, WorkspaceSpec, parse_workspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    ).stdout


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home. BOTH the env var and the store's `config_dir` — the env var is what
    `loop.worktree` honors (it imports `config_dir` inside its function), and the patch is what
    the workflow store's module-level import reads."""
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: h)
    return h


@pytest.fixture()
def repo(tmp_path):
    """A real single-commit repo with an untracked `.env` — the adoption-critical shape."""
    ws = tmp_path / "repo"
    ws.mkdir()
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "t@t")
    git(ws, "config", "user.name", "T")
    (ws / "a.txt").write_text("one\n")
    (ws / ".env").write_text("TOKEN=local-secret\n")
    git(ws, "add", "a.txt")
    git(ws, "commit", "-qm", "init")
    return ws


def _run(**kw) -> WorkflowRun:
    return store.create(WorkflowRun(id="", workflow_name="wsp", **kw))


async def _ok_runner(recorded: list[tuple[str, str]]):
    """A runner that records instead of spawning — the `EngineServices.teardown_runner` seam."""

    async def runner(command: str, cwd: str) -> tuple[bool, str]:
        recorded.append((command, cwd))
        return True, "ok"

    return runner


# ── the spec seam: a declaration, not a default ──


class TestSpecResolution:
    def test_a_spec_with_NO_workspace_block_declares_nothing(self) -> None:
        """The property a measurement forced. Provisioning every run into a scratch dir made every
        stale RUNNING run look like an isolated substrate to the boot sweep, so a crash-survivor
        with journal-backed resumable work would be SUSPENDED awaiting a manual Resume instead of
        adopted (`test_an_adopted_run_resumes_without_re_running_finished_work` caught it)."""
        assert provisioning.declares_workspace({"name": "x", "root": {}}) is False
        assert provisioning.declares_workspace({"workspace": {"mode": "scratch"}}) is True
        # A non-dict block is not a declaration either — `parse_workspace` would call it fatal, and
        # refusing a run over `workspace: "yes"` when the author declared nothing coherent would be
        # worse than ignoring it.
        assert provisioning.declares_workspace({"workspace": "yes"}) is False

    def test_the_config_default_fills_an_UNDECLARED_mode_only(self) -> None:
        """A declared mode always wins: a config knob that overrode an explicit declaration would
        make a template's own isolation statement advisory."""
        spec, _ = provisioning.resolve_spec(
            {"workspace": {"setup": "npm ci"}}, default_mode="worktree"
        )
        assert spec.mode is Mode.WORKTREE
        spec, _ = provisioning.resolve_spec(
            {"workspace": {"mode": "in_place"}}, default_mode="worktree"
        )
        assert spec.mode is Mode.IN_PLACE

    def test_an_unparseable_default_mode_falls_back_to_scratch_NOT_in_place(self) -> None:
        """S49's ruling: `in_place` is never a default. A config typo must not be the thing that
        puts a destructive step against the user's real tree."""
        spec, issues = provisioning.resolve_spec({"workspace": {"setup": "x"}}, default_mode="wat")
        assert spec.mode is Mode.SCRATCH
        assert any(i.code == "unknown_default_mode" for i in issues)
        assert not any(i.fatal for i in issues), "a bad config value degrades, it does not refuse"


# ── the PID lock, outside the workspace ──


class TestWorkspaceLock:
    def test_it_lives_OUTSIDE_the_workspace(self, home) -> None:
        """A lock inside the worktree would be deleted by the very teardown whose contention it
        guards, and would be invisible to a second process probing for a live holder."""
        lock = provisioning.acquire_workspace_lock("r-1")
        try:
            assert lock.acquired
            assert "locks" in lock.path
            assert "worktrees" not in lock.path
        finally:
            lock.release()

    def test_a_named_workspace_keys_on_the_NAME_and_an_unnamed_one_on_the_RUN(self) -> None:
        """Two runs sharing one named workspace are the contention case; two per-run workspaces
        cannot collide. Keying both on the run id would make the shared case lockless."""
        assert provisioning.lock_key("r-1", "shared") == provisioning.lock_key("r-2", "shared")
        assert provisioning.lock_key("r-1") != provisioning.lock_key("r-2")

    def test_it_FAILS_FAST_on_live_contention_and_names_the_holder(self, home) -> None:
        """Fail-fast, never wait in line: two runs interleaving writes in one worktree is worse
        than telling the second one now. The holder is named because a refusal nobody can
        attribute is one a user cannot act on."""
        first = provisioning.acquire_workspace_lock("r-1", name="shared")
        try:
            assert first.acquired
            second = provisioning.acquire_workspace_lock("r-2", name="shared")
            assert second.acquired is False
            assert second.held_by == os.getpid()
            assert str(os.getpid()) in second.reason
        finally:
            first.release()

    def test_it_SELF_HEALS_from_a_stale_pid(self, home) -> None:
        """flock is released by the OS on death, so the file survives with a dead pid in it. The
        next acquirer takes the lock and overwrites the record — a crashed run must not wedge its
        workspace forever."""
        from personalclaw.concurrency import lock_path

        path = lock_path(provisioning.lock_key("r-9"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("999999999\n")  # a pid no live process can have

        lock = provisioning.acquire_workspace_lock("r-9")
        try:
            assert lock.acquired, "a stale pid must not block acquisition"
            assert path.read_text().strip() == str(os.getpid()), "the record self-heals"
        finally:
            lock.release()

    def test_the_pid_probe_reads_a_missing_process_as_dead_and_ours_as_alive(self) -> None:
        assert provisioning.pid_alive(os.getpid()) is True
        assert provisioning.pid_alive(999_999_999) is False
        assert provisioning.pid_alive(0) is False


# ── provisioning: create → preserve → setup, in that order ──


class TestProvisionOrder:
    async def test_preserve_runs_BEFORE_setup(self, home, repo) -> None:
        """S49's measured rule. An `npm install` that runs before `.npmrc` is copied in reaches for
        the wrong registry — so the assertion is that the file EXISTS when setup executes, which is
        the only form of the ordering that matters."""
        run = _run(project_id="p-1")
        seen: list[bool] = []

        async def runner(command: str, cwd: str) -> tuple[bool, str]:
            seen.append((os.path.join(cwd, ".env")) and os.path.exists(os.path.join(cwd, ".env")))
            return True, "ok"

        spec = WorkspaceSpec(mode=Mode.WORKTREE, preserve_patterns=[".env"], setup="echo build")
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo), runner=runner
        )
        assert result.ok and result.isolated
        assert ".env" in result.preserved
        assert seen == [True], "setup saw the preserved file, so preserve ran first"
        assert result.setup_ran == ["echo build"]

    async def test_a_FATAL_declaration_provisions_nothing(self, home, repo) -> None:
        """An ignored fatal issue is the inert-control shape. `parse_workspace` marks a greedy
        preserve pattern fatal because `**` copies the whole tree into the workspace it is being
        isolated FROM."""
        run = _run()
        spec, issues = parse_workspace({"mode": "worktree", "preserve_patterns": ["**"]})
        assert any(i.fatal for i in issues)
        result = await provisioning.provision(
            spec, run_id=run.id, workspace_dir=str(repo), issues=issues
        )
        assert result.ok is False and result.fatal is True
        assert result.path == "", "nothing was created"

    async def test_a_setup_FAILURE_does_not_block_the_run(self, home, repo) -> None:
        """S52's contract: `SetupResult.blocked_run` is False by construction. Refusing to run the
        workflow because `npm install` failed would make declaring setup a liability, and a user
        would stop declaring it."""
        run = _run()

        async def runner(command: str, cwd: str) -> tuple[bool, str]:
            return False, "ENOTFOUND registry.example"

        spec = WorkspaceSpec(mode=Mode.SCRATCH, setup="npm ci")
        result = await provisioning.provision(
            spec, run_id=run.id, workspace_dir=str(repo), runner=runner
        )
        assert result.ok is True, "the run proceeds"
        assert len(result.setup_failed) == 1
        assert "ENOTFOUND" in result.setup_failed[0]
        assert result.to_dict()["setup"]["blocked_run"] is False

    async def test_a_failed_step_is_NOT_marked_done_so_a_resume_retries_it(
        self, home, repo
    ) -> None:
        """Marking a failure done would make it permanent across every subsequent resume — and the
        conditions setup fails on (an offline registry, a missing binary) are usually transient."""
        run = _run()
        spec = WorkspaceSpec(mode=Mode.SCRATCH, setup="npm ci")

        async def failing(command: str, cwd: str) -> tuple[bool, str]:
            return False, "boom"

        first = await provisioning.provision(
            spec, run_id=run.id, workspace_dir=str(repo), runner=failing
        )
        calls: list[tuple[str, str]] = []
        second = await provisioning.provision(
            spec,
            run_id=run.id,
            workspace_dir=str(repo),
            runner=await _ok_runner(calls),
        )
        assert first.setup_failed and second.setup_ran == ["npm ci"]
        assert calls, "the failed step ran again on the second pass"


class TestResumeIdempotency:
    async def test_the_same_worktree_is_reused_and_setup_is_SKIPPED(self, home, repo) -> None:
        """`add_worktree` is idempotent (measured) and markers are content-addressed, so a resume
        costs nothing rather than needing detection code."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, setup="echo one")
        calls: list[tuple[str, str]] = []
        runner = await _ok_runner(calls)

        first = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo), runner=runner
        )
        second = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo), runner=runner
        )
        assert first.path == second.path, "the SAME worktree, not a second one"
        assert first.setup_ran == ["echo one"]
        assert second.setup_ran == [] and second.setup_skipped == ["echo one"]
        assert len(calls) == 1, "setup executed exactly once across the two passes"

    async def test_an_EDITED_step_RE_RUNS_on_resume(self, home, repo) -> None:
        """A marker keyed by index would skip an edited step as though it had run. Content-
        addressing is what makes the guard honest."""
        run = _run(project_id="p-1")
        calls: list[tuple[str, str]] = []
        runner = await _ok_runner(calls)
        await provisioning.provision(
            WorkspaceSpec(mode=Mode.WORKTREE, setup="echo one"),
            run_id=run.id,
            project_id="p-1",
            workspace_dir=str(repo),
            runner=runner,
        )
        after = await provisioning.provision(
            WorkspaceSpec(mode=Mode.WORKTREE, setup="echo TWO"),
            run_id=run.id,
            project_id="p-1",
            workspace_dir=str(repo),
            runner=runner,
        )
        assert after.setup_ran == ["echo TWO"]
        assert [c[0] for c in calls] == ["echo one", "echo TWO"]


class TestDegradation:
    async def test_a_non_repo_workspace_degrades_to_scratch_WITH_a_reason(
        self, home, tmp_path
    ) -> None:
        """A user who declared `worktree` wanted isolation, and the isolation is deliverable
        without git. Refusing would trade the property they asked for against the mechanism they
        did not."""
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        run = _run()
        result = await provisioning.provision(
            WorkspaceSpec(mode=Mode.WORKTREE), run_id=run.id, workspace_dir=str(plain)
        )
        assert result.ok is True
        assert result.path and "not a git repo" in result.degraded_reason
        assert result.isolated is False, "an isolated mode that could not isolate reports honestly"

    async def test_container_mode_degrades_rather_than_refusing(self, home, repo) -> None:
        """WF2WOR-12 shipped container mode with the no-environment posture unchanged: a bare
        `container` declaration (no manifest) still RUNS — isolated scratch, reason recorded,
        no container id claimed."""
        run = _run()
        result = await provisioning.provision(
            WorkspaceSpec(mode=Mode.CONTAINER), run_id=run.id, workspace_dir=str(repo)
        )
        assert result.ok is True and result.path
        assert "no environment manifest" in result.degraded_reason
        assert result.container_id == "" and result.container_backend == ""

    async def test_in_place_reports_the_REAL_tree_and_is_not_isolated(self, home, repo) -> None:
        """Inventing a path would hide from every surface that the run worked in the user's tree —
        which is exactly the fact `in_place` needs to make visible."""
        run = _run()
        result = await provisioning.provision(
            WorkspaceSpec(mode=Mode.IN_PLACE), run_id=run.id, workspace_dir=str(repo)
        )
        assert result.path == str(repo) and result.isolated is False


# ── the run record ──


class TestRunRecord:
    async def test_worktree_path_IS_WRITTEN_for_every_isolated_mode(self, home, repo) -> None:
        """`watchdog._substrate_for` reads exactly this key and had ZERO writers before this atom —
        a live reader of a key nothing writes. Written for SCRATCH too: a scratch workspace that
        survived a restart is just as recoverable as a git one, so keying the sweep's decision on
        the mode name would abort recoverable work for the commoner mode."""
        run = _run()
        spec = WorkspaceSpec(mode=Mode.SCRATCH)
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
        provisioning.stamp_run(run, result, spec)
        store.save(run)

        reloaded = store.get(run.id)
        assert reloaded is not None
        assert reloaded.extra["worktree_path"] == result.path
        assert reloaded.extra["workspace"]["mode"] == "scratch"

    async def test_the_stamped_env_carries_PRESENCE_only(self, home, repo) -> None:
        """A run record is read by the cockpit, the export archive and a bug report. It must not be
        the thing that leaks a token."""
        run = _run()
        spec = WorkspaceSpec(mode=Mode.SCRATCH, env={"API_KEY": "{{secret:OPENAI}}", "MODE": None})
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
        provisioning.stamp_run(run, result, spec)
        assert run.extra["workspace"]["env"] == {"API_KEY": True, "MODE": False}
        assert "OPENAI" not in str(run.extra)

    async def test_a_degraded_isolated_run_CLEARS_a_stale_worktree_path(
        self, home, tmp_path
    ) -> None:
        """Left stale, the boot sweep would read the old path as a live substrate and suspend a run
        whose workspace no longer exists."""
        plain = tmp_path / "plain"
        plain.mkdir()
        run = _run()
        run.extra["worktree_path"] = "/gone/from/a/previous/pass"
        spec = WorkspaceSpec(mode=Mode.IN_PLACE)
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(plain))
        provisioning.stamp_run(run, result, spec)
        assert "worktree_path" not in run.extra

    async def test_preserved_workspace_path_is_set_only_when_ALIVE_and_DIRTY(
        self, home, repo
    ) -> None:
        """S52's rule: pointing a user at a clean directory is a false lead, and a record carrying
        a path that means nothing is worse than one carrying no path."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE)
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)

        clean = provisioning.inspect_run(run)
        provisioning.stamp_preserved_path(run, clean)
        assert "preserved_workspace_path" not in run.extra, "a clean worktree yields no path"

        (os.path.join(result.path, "new.txt"))
        with open(os.path.join(result.path, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("work\n")
        dirty = provisioning.inspect_run(run)
        assert dirty.alive and dirty.dirty
        assert provisioning.stamp_preserved_path(run, dirty) is True
        assert run.extra["preserved_workspace_path"] == result.path


# ── teardown before deletion ──


class TestTeardown:
    async def test_teardown_runs_while_the_directory_STILL_EXISTS(self, home, repo) -> None:
        """The order IS the contract. Teardown's job is to stop services and sync work out, and
        both need the directory to still be there — a plan that deleted first would run its own
        teardown against nothing and report success."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, teardown="docker compose down")
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)

        seen: list[bool] = []

        async def runner(command: str, cwd: str) -> tuple[bool, str]:
            seen.append(os.path.isdir(cwd))
            return True, "down"

        torn = await provisioning.teardown(run, workspace_dir=str(repo), runner=runner)
        assert seen == [True], "the workspace existed when teardown ran"
        assert torn.ran == ["docker compose down"]
        assert torn.removed is True and not os.path.isdir(result.path)

    async def test_outstanding_work_is_committed_WITHOUT_the_preserved_secret(
        self, home, repo
    ) -> None:
        """MEASURED: a plain `git add -A` committed the copied `.env` and `.pclaw-setup/` into the
        run branch, so the durable record would carry the user's credentials into git history and
        both verbs would offer to apply them. The exclusion runs at the ADD, because a review
        filter cannot un-commit a secret."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, preserve_patterns=[".env"], setup="echo hi")
        calls: list[tuple[str, str]] = []
        result = await provisioning.provision(
            spec,
            run_id=run.id,
            project_id="p-1",
            workspace_dir=str(repo),
            runner=await _ok_runner(calls),
        )
        provisioning.stamp_run(run, result, spec)
        assert ".env" in result.preserved
        with open(os.path.join(result.path, "real-work.txt"), "w", encoding="utf-8") as fh:
            fh.write("the thing the run produced\n")

        torn = await provisioning.teardown(run, workspace_dir=str(repo))
        assert torn.committed is True
        branch = torn.branch
        # The branch survives the removal — that is what makes the record reference git rather than
        # a deleted directory.
        listed = git(repo, "ls-tree", "-r", "--name-only", branch)
        names = set(listed.split())
        assert "real-work.txt" in names
        assert ".env" not in names, "the preserved secret never reached git history"
        assert not any(n.startswith(".pclaw-setup") for n in names), "no engine machinery committed"

    async def test_keep_open_still_runs_teardown_but_keeps_the_directory(self, home, repo) -> None:
        """Keeping the directory is not keeping the processes. A `docker compose` left up because
        the user wanted to inspect the files is a leak the override never asked for."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, teardown="stop-services")
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)
        calls: list[tuple[str, str]] = []
        torn = await provisioning.teardown(
            run, workspace_dir=str(repo), keep_open=True, runner=await _ok_runner(calls)
        )
        assert [c[0] for c in calls] == ["stop-services"]
        assert torn.removed is False and os.path.isdir(result.path)

    async def test_an_IN_PLACE_workspace_is_NEVER_removed(self, home, repo) -> None:
        """It is the user's real tree. Removing it would be the deleted-real-model incident,
        exactly."""
        run = _run()
        spec = WorkspaceSpec(mode=Mode.IN_PLACE, teardown="echo bye")
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
        provisioning.stamp_run(run, result, spec)
        torn = await provisioning.teardown(
            run, workspace_dir=str(repo), runner=await _ok_runner([])
        )
        assert torn.removed is False
        assert (repo / "a.txt").is_file(), "the real tree is untouched"

    async def test_a_teardown_FAILURE_does_not_block_the_removal(self, home, repo) -> None:
        """Both call sites are deletion paths. A run that cannot be deleted because its teardown
        threw would be a row visible forever with no way to remove it."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, teardown="boom")
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)

        async def exploding(command: str, cwd: str) -> tuple[bool, str]:
            raise RuntimeError("the teardown script is broken")

        torn = await provisioning.teardown(run, workspace_dir=str(repo), runner=exploding)
        assert torn.failed and torn.removed is True
        assert not os.path.isdir(result.path)


# ── the review + the two verbs ──


class TestReintegration:
    async def test_the_diff_EXCLUDES_engine_machinery(self, home, repo) -> None:
        """S52's measured defect: the changed-files panel listed the preserved `.env` and the
        engine's own `.pclaw-setup/` markers as user changes. A review panel full of machinery is
        one the user skims, with the file that mattered in the same list."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE, preserve_patterns=[".env"], setup="echo x")
        result = await provisioning.provision(
            spec,
            run_id=run.id,
            project_id="p-1",
            workspace_dir=str(repo),
            runner=await _ok_runner([]),
        )
        provisioning.stamp_run(run, result, spec)
        with open(os.path.join(result.path, "mine.txt"), "w", encoding="utf-8") as fh:
            fh.write("mine\n")

        body = provisioning.reintegration(run, workspace_dir=str(repo))
        paths = {c["path"] for c in body["workspace"]["changed"]}
        assert paths == {"mine.txt"}, f"machinery leaked into the review: {paths}"

    async def test_both_verbs_are_OFFERED_with_the_branch_named(self, home, repo) -> None:
        """Reintegration is offered, never performed — a run that auto-merged would decide for the
        user, and the decision is the whole reason the work was isolated."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE)
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)
        body = provisioning.reintegration(run, workspace_dir=str(repo))
        verbs = {v["verb"] for v in body["reintegration"]["verbs"]}
        assert verbs == {"apply_locally", "checkout_branch"}
        assert body["reintegration"]["branch"] == f"pclaw/run-{run.id}"

    async def test_a_real_CONFLICT_is_named_on_the_offer(self, home, repo) -> None:
        """ "Apply this" that then fails with a conflict is a worse experience than "apply this (1
        file conflicts)". The probe uses `merge-tree --write-tree`, which reports without touching
        either tree — a real merge-and-abort would leave the user's index dirty for a READ."""
        run = _run(project_id="p-1")
        spec = WorkspaceSpec(mode=Mode.WORKTREE)
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)
        # Divergent edits to the SAME file, each committed on its own side.
        with open(os.path.join(result.path, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("from the run\n")
        await provisioning.teardown(run, workspace_dir=str(repo), keep_open=True)
        (repo / "a.txt").write_text("from the user\n")
        git(repo, "commit", "-aqm", "local edit")

        body = provisioning.reintegration(run, workspace_dir=str(repo))
        assert body["reintegration"]["conflicts"] == ["a.txt"]
        offered = {v["verb"]: v["safe"] for v in body["reintegration"]["verbs"]}
        assert offered["apply_locally"] is False
        # Checkout stays safe WITH conflicts: nothing merges, so there is nothing to conflict with
        # until the user decides to merge.
        assert offered["checkout_branch"] is True
        # And the user's tree is untouched by the READ.
        assert (repo / "a.txt").read_text() == "from the user\n"

    def test_a_run_with_no_workspace_reviews_as_empty_rather_than_raising(self, home) -> None:
        run = _run()
        body = provisioning.reintegration(run, workspace_dir="")
        assert body["workspace"]["path"] == "" and body["workspace"]["changed"] == []


# ── the two deletion paths ──


class TestDeletionPaths:
    async def test_service_delete_run_tears_down_BEFORE_the_directory_goes(
        self, home, repo, monkeypatch
    ) -> None:
        """A scratch workspace lives UNDER the run dir, so the `rmtree` would take it out. Running
        teardown afterwards would execute `docker compose down` against a path that no longer holds
        the compose file."""
        from personalclaw.workflows import service

        run = _run(status=RunStatus.COMPLETE)
        spec = WorkspaceSpec(mode=Mode.SCRATCH, teardown="compose-down")
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
        provisioning.stamp_run(run, result, spec)
        run.status = RunStatus.COMPLETE
        store.save(run)

        seen: list[bool] = []
        real_run_step = provisioning.run_step

        async def spy(command, cwd, **kw):
            seen.append(os.path.isdir(str(cwd)))
            return True, "ok"

        monkeypatch.setattr(provisioning, "run_step", spy)
        assert real_run_step is not spy
        out = await service.delete_run(run.id)
        assert out["ok"] is True
        assert seen == [True], "teardown ran while the workspace still existed"
        assert store.get(run.id) is None
        assert not os.path.isdir(result.path)

    async def test_retention_expiry_tears_down_TOO(self, home, repo, monkeypatch) -> None:
        """Retention is the path that fires with nobody watching, so it is the one where a leak
        accumulates silently. Wiring only the explicit delete would leave every expired run's
        services running."""
        from personalclaw.workflows.watchdog import prune_runs

        made = []
        for i in range(3):
            run = store.create(
                WorkflowRun(
                    id="",
                    workflow_name="ret",
                    status=RunStatus.COMPLETE,
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                )
            )
            spec = WorkspaceSpec(mode=Mode.SCRATCH, teardown=f"stop-{i}")
            result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
            provisioning.stamp_run(run, result, spec)
            store.save(run)
            made.append(run)

        commands: list[str] = []

        async def spy(command, cwd, **kw):
            commands.append(str(command))
            return True, "ok"

        monkeypatch.setattr(provisioning, "run_step", spy)
        removed = await prune_runs("ret", keep=1)
        assert removed == 2
        # The two OLDEST were pruned, and each ran its own teardown command.
        assert sorted(commands) == ["stop-0", "stop-1"]

    async def test_the_config_switch_SKIPS_the_command_but_still_removes(
        self, home, repo, monkeypatch
    ) -> None:
        """The knob's real reader. Off is the escape hatch for a teardown command that is itself
        the problem; removal still happens, or the directory would be orphaned."""
        from personalclaw.config.loader import AppConfig
        from personalclaw.workflows import service

        run = _run()
        spec = WorkspaceSpec(mode=Mode.SCRATCH, teardown="never-runs")
        result = await provisioning.provision(spec, run_id=run.id, workspace_dir=str(repo))
        provisioning.stamp_run(run, result, spec)

        cfg = AppConfig.load()
        object.__setattr__(cfg.workflows, "workspace_teardown_on_expiry", False)
        monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))

        commands: list[str] = []

        async def spy(command, cwd, **kw):
            commands.append(str(command))
            return True, "ok"

        monkeypatch.setattr(provisioning, "run_step", spy)
        torn = await service.teardown_workspace(run, reason="retention")
        assert commands == [], "the declared command was skipped"
        assert torn.removed is True and not os.path.isdir(result.path)


# ── the watchdog's substrate check ──


class TestSubstrateCheck:
    async def test_the_sweep_reads_the_REAL_worktree_state(self, home, repo) -> None:
        """S52 built `substrate_for` so "S46's boot sweep has one source of truth". Before this
        atom the sweep did its own `Path(wt).is_dir()`, so two places computed the same decision —
        and the disagreement shows up as a run aborted despite having recoverable work."""
        from personalclaw.workflows.controller import EngineServices
        from personalclaw.workflows.watchdog import WorkflowWatchdog

        run = _run(project_id="p-1", status=RunStatus.RUNNING)
        spec = WorkspaceSpec(mode=Mode.WORKTREE)
        result = await provisioning.provision(
            spec, run_id=run.id, project_id="p-1", workspace_dir=str(repo)
        )
        provisioning.stamp_run(run, result, spec)
        with open(os.path.join(result.path, "wip.txt"), "w", encoding="utf-8") as fh:
            fh.write("in progress\n")
        run.status = RunStatus.RUNNING
        store.save(run)

        wd = WorkflowWatchdog(None, EngineServices())
        substrate = wd._substrate_for(run)
        assert substrate.kind == "worktree" and substrate.alive is True
        assert substrate.isolated is True
        # The dirty state is recorded on the way past — a Resume affordance with no path to the
        # work is a button that tells the user nothing.
        assert store.get(run.id).extra["preserved_workspace_path"] == result.path

    async def test_a_GONE_workspace_reads_as_dead(self, home, repo) -> None:
        """A Resume that points at a gone worktree is worse than an honest abort."""
        from personalclaw.workflows.controller import EngineServices
        from personalclaw.workflows.watchdog import WorkflowWatchdog

        run = _run(status=RunStatus.RUNNING)
        run.extra["worktree_path"] = str(repo / "never-existed")
        run.extra["workspace"] = {"path": str(repo / "never-existed"), "isolated": True}
        store.save(run)
        wd = WorkflowWatchdog(None, EngineServices())
        substrate = wd._substrate_for(run)
        assert substrate.alive is False

    def test_an_inline_run_is_reported_NOT_isolated(self, home) -> None:
        """So the sweep leaves it to adoption, which resumes it from the journal — the DEVIATION
        the sweep's own docstring records."""
        from personalclaw.workflows.controller import EngineServices
        from personalclaw.workflows.watchdog import WorkflowWatchdog

        run = _run(status=RunStatus.RUNNING)
        wd = WorkflowWatchdog(None, EngineServices())
        assert wd._substrate_for(run).isolated is False


# ── config: both keys have a real reader ──


class TestConfigReaders:
    def test_workspace_default_mode_is_READ_by_resolve_spec(self) -> None:
        """A knob nothing reads is the inert-control class this program keeps finding."""
        for mode in ("scratch", "worktree", "in_place", "container"):
            spec, _ = provisioning.resolve_spec({"workspace": {"setup": "x"}}, default_mode=mode)
            assert spec.mode.value == mode

    def test_workspace_teardown_on_expiry_is_READ_by_teardown_workspace(self) -> None:
        """Asserted structurally so the reader cannot be deleted without reding a test: the gate
        lives in `service.teardown_workspace`, and both deletion paths go through it."""
        import inspect

        from personalclaw.workflows import service

        src = inspect.getsource(service.teardown_workspace)
        assert "workspace_teardown_on_expiry" in src

    def test_both_keys_round_trip_through_load(self, tmp_path, monkeypatch) -> None:
        """The four-point contract's load half, driven rather than asserted from the dataclass."""
        import json

        from personalclaw.config.loader import AppConfig

        home = tmp_path / "cfghome"
        home.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        (home / "config.json").write_text(
            json.dumps(
                {
                    "workflows": {
                        "workspace_default_mode": "worktree",
                        "workspace_teardown_on_expiry": False,
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.workflows.workspace_default_mode == "worktree"
        assert cfg.workflows.workspace_teardown_on_expiry is False

    def test_an_unknown_stored_mode_loads_as_scratch(self, tmp_path, monkeypatch) -> None:
        """Not as the declared value and NOT as `in_place`: a config typo must not be what puts a
        destructive step against the user's real tree."""
        import json

        from personalclaw.config.loader import AppConfig

        home = tmp_path / "cfghome2"
        home.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        (home / "config.json").write_text(
            json.dumps({"workflows": {"workspace_default_mode": "in-place-typo"}}),
            encoding="utf-8",
        )
        assert AppConfig.load().workflows.workspace_default_mode == "scratch"
