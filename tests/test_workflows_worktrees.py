"""Tests for code-kind run worktrees (WORK-CONTAINERS §4.1, S52).

These use a REAL git repo rather than mocked subprocess calls, because the properties under test are
facts about git's behaviour, not about our wrappers. Two of them were measured before any code was
written:

* `add_worktree` on an existing id returns the SAME path rather than failing — which is what makes
  resume free rather than something to implement.
* An untracked `.env` is genuinely ABSENT from a fresh worktree. That is why `preserve_patterns` is
  adoption-critical: a worktree where every build fails reads to a user as "isolation is broken".

The defect this module's tests exist to pin: the review diff listed the preserved `.env` and the
engine's own `.pclaw-setup/` markers as user changes. A review panel full of machinery is
one the user
skims, with the file that mattered in the same list.
"""

import subprocess

import pytest

from personalclaw.workflows.worktrees import (
    INFRASTRUCTURE_PATHS,
    MAX_PRESERVE_BYTES,
    PRESERVE_DENYLIST,
    RUN_BRANCH_PREFIX,
    Reintegration,
    cleanup_markers,
    inspect_worktree,
    is_infrastructure,
    mark_setup_done,
    parse_status,
    pending_setup,
    plan_teardown,
    preserve,
    reintegration_offer,
    resume_safe,
    run_branch,
    setup_steps,
    substrate_for,
    worktree_env,
)


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    ).stdout


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A real single-commit repo, with the worktrees root inside tmp_path."""
    ws = tmp_path / "repo"
    ws.mkdir()
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "t@t")
    git(ws, "config", "user.name", "T")
    (ws / "a.txt").write_text("one\n")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "init")
    # Isolate via PERSONALCLAW_HOME, which `config.loader` honors, rather than monkeypatching
    # `config.loader.config_dir` itself. Measured: the module-attribute patch redirected EVERY
    # consumer for the duration, and `cli_doctor` then read a `project_dir` out of a tmp home that
    # pytest deleted — so `TestDoctor::test_doctor_with_agent` reported "stale project_dir" and
    # exited 1. A failure in a test with nothing to do with worktrees, deterministic in the full
    # xdist mix and invisible in isolation. `worktree.py` imports `config_dir` inside its function,
    # so there is no module attribute to patch narrowly; the env var is the real seam.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return ws


@pytest.fixture()
def worktree(repo, tmp_path):
    from personalclaw.loop import worktree as wt_machinery

    path = wt_machinery.add_worktree(str(repo), "run-52", project_id="p-1")
    assert path, "the proven machinery must produce a worktree for these tests to mean anything"
    return path


# ── the proven machinery's properties, measured not assumed ──


def test_adding_a_worktree_TWICE_returns_the_same_path(repo):
    """This is what makes resume free. If a second add failed, every resume would need to detect and
    skip it — and the detection would be a second source of truth about whether the
    worktree exists.
    """
    from personalclaw.loop import worktree as wt_machinery

    first = wt_machinery.add_worktree(str(repo), "run-x", project_id="p-1")
    second = wt_machinery.add_worktree(str(repo), "run-x", project_id="p-1")
    assert first == second


def test_an_untracked_local_config_file_is_ABSENT_from_a_fresh_worktree(repo, worktree):
    """The measurement that makes `preserve_patterns` load-bearing rather than a nicety."""
    from pathlib import Path

    (repo / ".env").write_text("SECRET=1\n")
    assert not (Path(worktree) / ".env").exists()


# ── preserve copies IN, never OUT ──


def test_a_preserved_file_lands_in_the_worktree(repo, worktree):
    from pathlib import Path

    (repo / ".env").write_text("SECRET=1\n")
    result = preserve(repo, worktree, [".env"])
    assert result.copied == [".env"]
    assert (Path(worktree) / ".env").read_text() == "SECRET=1\n"


def test_preserve_does_NOT_copy_back_to_the_real_tree(repo, worktree):
    """A pattern that copied a worktree file back over the user's tree would make an isolated
    run able
    to modify the thing it was isolated from — the one property isolation buys."""
    from pathlib import Path

    (Path(worktree) / "only-in-worktree.txt").write_text("x\n")
    preserve(repo, worktree, ["only-in-worktree.txt"])
    assert not (repo / "only-in-worktree.txt").exists()


@pytest.mark.parametrize("name", sorted(PRESERVE_DENYLIST))
def test_a_denylisted_path_is_refused_whatever_the_glob_matches(repo, worktree, name):
    """`.git` would corrupt the worktree's own repo state; the caches defeat the point of a cheap
    isolation step."""
    target = repo / name
    target.mkdir(exist_ok=True)
    (target / "inner.txt").write_text("x")
    result = preserve(repo, worktree, [f"{name}/*"])
    assert result.copied == []
    assert any("denylisted" in s for s in result.skipped)


def test_an_oversize_file_is_skipped_WITH_a_reason(repo, worktree):
    """A user whose build fails needs to know their file was skipped for being 4MB — a silent skip
    makes the isolation look broken for a reason nothing reports."""
    (repo / "big.bin").write_bytes(b"x" * (MAX_PRESERVE_BYTES + 1))
    result = preserve(repo, worktree, ["big.bin"])
    assert result.copied == []
    assert any("exceeds the preserve cap" in s for s in result.skipped)


def test_a_directory_match_is_skipped_rather_than_recursed(repo, worktree):
    (repo / "conf").mkdir()
    result = preserve(repo, worktree, ["conf"])
    assert any("directories are not preserved" in s for s in result.skipped)


def test_preserving_nothing_is_not_an_error(repo, worktree):
    assert preserve(repo, worktree, []).copied == []
    assert preserve(repo, worktree, ["nope-*.txt"]).copied == []


def test_preserve_into_a_missing_target_returns_empty(repo, tmp_path):
    assert preserve(repo, tmp_path / "gone", [".env"]).copied == []


# ── setup idempotency ──


def test_setup_splits_on_NEWLINES_only():
    """Splitting on `&&` or `;` would shred a single shell command that legitimately chains,
    and each
    step is marker-guarded individually."""
    assert setup_steps("npm ci && npm run build\npytest -q") == [
        "npm ci && npm run build",
        "pytest -q",
    ]


def test_every_step_is_pending_on_a_fresh_worktree(worktree):
    to_run, done = pending_setup(worktree, "echo one\necho two")
    assert to_run == ["echo one", "echo two"]
    assert done == []


def test_a_marked_step_is_not_re_run(worktree):
    """Setup runs on EVERY resume by contract. A `git clone` that re-runs fails, and a setup block
    that fails on resume makes resume unusable."""
    mark_setup_done(worktree, "echo one")
    to_run, done = pending_setup(worktree, "echo one\necho two")
    assert to_run == ["echo two"]
    assert done == ["echo one"]


def test_an_EDITED_step_re_runs(worktree):
    """Markers are content-addressed. One keyed by position would skip an edited step as
    though it had
    already run — the silent kind of stale."""
    mark_setup_done(worktree, "echo one")
    to_run, _done = pending_setup(worktree, "echo one --force\necho two")
    assert "echo one --force" in to_run


def test_setup_failure_NEVER_blocks_the_run():
    """Refusing to run the workflow because `npm install` failed would make declaring setup a
    liability, and a user would stop declaring it."""
    from personalclaw.workflows.worktrees import SetupResult

    assert SetupResult(failed=["npm ci"]).blocked_run is False


def test_markers_can_be_cleared_for_a_REUSED_workspace(worktree):
    """Only for a named/reused workspace: a fresh worktree has no markers, and a per-run one
    is about
    to be deleted. The count is returned because 0 and 5 mean different things to a caller deciding
    whether the reuse was clean."""
    mark_setup_done(worktree, "a")
    mark_setup_done(worktree, "b")
    assert cleanup_markers(worktree) == 2
    assert cleanup_markers(worktree) == 0


# ── resume safety ──


def test_a_live_worktree_is_RESUMABLE(worktree):
    ok, why = resume_safe(worktree, "echo one")
    assert ok is True
    assert "resumable" in why


def test_a_MISSING_worktree_is_not_resumable(tmp_path):
    """Offering a Resume that cannot work is worse than an honest abort — the user clicks it and
    nothing happens."""
    ok, why = resume_safe(tmp_path / "gone", "echo one")
    assert ok is False
    assert "gone" in why


def test_the_resume_reason_reports_the_setup_SPLIT(worktree):
    mark_setup_done(worktree, "echo one")
    _ok, why = resume_safe(worktree, "echo one\necho two")
    assert "1 setup step(s) already done" in why


# ── teardown order ──


def test_teardown_runs_BEFORE_deletion():
    """Its job is to stop services and sync work out, and both need the directory to still exist."""
    plan = plan_teardown(teardown="docker compose down")
    steps = plan.steps
    assert [i for i, s in enumerate(steps) if "docker compose down" in s][0] < [
        i for i, s in enumerate(steps) if "remove the worktree" in s
    ][0]


def test_an_ephemeral_workspace_COMMITS_before_removal():
    """A run record pointing at a deleted directory has lost the work; a per-run branch survives, so
    the record references git."""
    steps = plan_teardown(ephemeral=True).steps
    assert [i for i, s in enumerate(steps) if "per-run branch" in s][0] < [
        i for i, s in enumerate(steps) if "remove the worktree" in s
    ][0]


def test_keep_open_SKIPS_deletion():
    """For the case where the workspace IS the deliverable. Deleting it because the run ended would
    destroy the output."""
    plan = plan_teardown(teardown="echo bye", keep_open=True)
    assert plan.deletes is False
    assert any("KEEP the workspace" in s for s in plan.steps)
    assert not any("remove the worktree" in s for s in plan.steps)


def test_teardown_still_runs_when_the_workspace_is_kept():
    """A service the run started must still be stopped — keeping the directory is not keeping the
    processes."""
    assert any("echo bye" in s for s in plan_teardown(teardown="echo bye", keep_open=True).steps)


def test_a_non_ephemeral_workspace_does_not_commit():
    """A named workspace the user owns must not have a run's commit forced onto it."""
    assert not any("per-run branch" in s for s in plan_teardown(ephemeral=False).steps)


# ── the per-run branch ──


def test_the_branch_name_is_DETERMINISTIC():
    """A random suffix would leave one abandoned branch per retry, and a user reading `git branch`
    could not tell which held the work."""
    assert run_branch("r-42") == run_branch("r-42") == f"{RUN_BRANCH_PREFIX}r-42"


def test_the_branch_prefix_distinguishes_runs_from_loop_tasks():
    """A user reading `git branch` should be able to tell which subsystem made a branch."""
    from personalclaw.loop.worktree import branch_name

    assert not run_branch("x").startswith(branch_name("x").rsplit("-", 1)[0])


def test_an_unsafe_run_id_is_sanitized_into_a_valid_branch():
    assert "/" not in run_branch("../../etc/passwd").removeprefix(RUN_BRANCH_PREFIX)


def test_an_empty_run_id_still_yields_a_branch():
    assert run_branch("") == f"{RUN_BRANCH_PREFIX}unknown"


# ── the status parser, against REAL git output ──


def test_the_parser_handles_every_real_status_shape(repo):
    """Driven against real `git status --porcelain` rather than handwritten fixtures: the two-column
    form is easy to get wrong, and a parser that read one column would report a staged deletion as
    unstaged — making the cockpit's stage/discard buttons act on the wrong thing."""
    (repo / "mod.txt").write_text("b\n")
    (repo / "del.txt").write_text("c\n")
    (repo / "ren.txt").write_text("d\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second")

    (repo / "mod.txt").write_text("changed\n")
    (repo / "new.txt").write_text("e\n")
    git(repo, "rm", "-q", "del.txt")
    (repo / "staged.txt").write_text("f\n")
    git(repo, "add", "staged.txt")
    git(repo, "mv", "ren.txt", "renamed.txt")

    entries = {e.path: e for e in parse_status(git(repo, "status", "--porcelain"))}
    assert entries["mod.txt"].status == "modified" and entries["mod.txt"].staged is False
    assert entries["del.txt"].status == "deleted" and entries["del.txt"].staged is True
    assert entries["staged.txt"].status == "added" and entries["staged.txt"].staged is True
    assert entries["new.txt"].status == "untracked" and entries["new.txt"].staged is False
    # A rename reads as `R  old -> new`; the NEW path is what the user reviews.
    assert entries["renamed.txt"].status == "renamed"


def test_an_unknown_status_code_is_KEPT():
    """A file the parser does not understand is still a file the user changed. Dropping it
    would make
    the diff panel quietly incomplete."""
    entries = parse_status("XY weird.txt")
    assert entries[0].path == "weird.txt"


def test_a_short_or_empty_line_is_skipped():
    assert parse_status("") == []
    assert parse_status("M\n") == []


# ── the review diff excludes machinery ──


def test_the_review_diff_excludes_the_ENGINES_OWN_markers(repo, worktree):
    """Measured live: git reports the marker dir as `.pclaw-setup/` WITH a trailing slash, so
    a prefix
    check written against the bare name matched nothing and the markers stayed in the panel — the
    exclusion existed and did half its job."""
    from pathlib import Path

    mark_setup_done(worktree, "echo one")
    (Path(worktree) / "a.txt").write_text("the real change\n")
    porcelain = git(worktree, "status", "--porcelain")
    state = inspect_worktree("run-52", worktree, porcelain)
    assert [c.path for c in state.changed] == ["a.txt"]


def test_the_review_diff_excludes_PRESERVED_files(repo, worktree):
    """A preserved `.env` is not a change the run made, and listing it as one trains the user
    to skim
    the panel that exists so they do not have to."""
    from pathlib import Path

    (repo / ".env").write_text("SECRET=1\n")
    result = preserve(repo, worktree, [".env"])
    (Path(worktree) / "a.txt").write_text("the real change\n")
    state = inspect_worktree(
        "run-52", worktree, git(worktree, "status", "--porcelain"), preserved=result.copied
    )
    assert [c.path for c in state.changed] == ["a.txt"]


@pytest.mark.parametrize(
    "path", [".pclaw-setup", ".pclaw-setup/", ".pclaw-setup/abc.done", "./.pclaw-setup/x"]
)
def test_every_marker_path_FORM_is_recognized(path):
    assert is_infrastructure(path) is True


@pytest.mark.parametrize("path", ["a.txt", "src/pclaw-setup.py", "docs/setup.md"])
def test_a_real_file_is_not_mistaken_for_machinery(path):
    """Over-exclusion is the worse direction here: a hidden file loses the change entirely, while a
    listed marker only costs a line."""
    assert is_infrastructure(path) is False


def test_the_marker_dir_name_is_SHARED_with_the_planner():
    """Two names for one convention would mean setup re-running because the performer looked in the
    wrong place."""
    from personalclaw.workflows.workspace import SETUP_MARKER_DIR

    assert SETUP_MARKER_DIR in INFRASTRUCTURE_PATHS


# ── state, substrate and the boot sweep ──


def test_a_live_worktree_reports_ALIVE(worktree):
    state = inspect_worktree("run-52", worktree)
    assert state.alive is True
    assert state.path


def test_a_missing_worktree_reports_dead(tmp_path):
    state = inspect_worktree("run-52", tmp_path / "gone")
    assert state.alive is False
    assert state.path == ""


def test_a_dirty_worktree_surfaces_its_PRESERVED_PATH(repo, worktree):
    from pathlib import Path

    (Path(worktree) / "a.txt").write_text("changed\n")
    state = inspect_worktree("run-52", worktree, git(worktree, "status", "--porcelain"))
    assert state.preserved_workspace_path == str(worktree)


def test_a_CLEAN_worktree_surfaces_no_path(worktree):
    """Pointing a user at a directory with nothing in it is a false lead, and the run record would
    carry a path that means nothing."""
    state = inspect_worktree("run-52", worktree, git(worktree, "status", "--porcelain"))
    assert state.preserved_workspace_path == ""


def test_the_substrate_feeds_S46s_sweep_from_ONE_source(repo, worktree):
    """The sweep's whole decision turns on whether an isolated substrate is alive. Two places
    computing
    that would eventually disagree, and the disagreement shows up as a run aborted despite having
    recoverable work."""
    from personalclaw.workflows.containers import BoardState, sweep_decision
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    state = inspect_worktree("run-52", worktree)
    decision = sweep_decision(
        WorkflowRun(id="run-52", workflow_name="code", status=RunStatus.RUNNING, started_at="x"),
        substrate_for(state),
    )
    assert decision.board_state is BoardState.SUSPENDED
    assert decision.resumable is True


def test_a_DEAD_worktree_makes_the_sweep_abort_honestly(tmp_path):
    from personalclaw.workflows.containers import BoardState, sweep_decision
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    state = inspect_worktree("run-52", tmp_path / "gone")
    decision = sweep_decision(
        WorkflowRun(id="run-52", workflow_name="code", status=RunStatus.RUNNING, started_at="x"),
        substrate_for(state),
    )
    assert decision.board_state is BoardState.DONE
    assert decision.resumable is False


# ── reintegration is offered, never performed ──


def test_BOTH_verbs_are_offered():
    """They suit different situations, and picking for the user is what "review before it
    lands" exists
    to prevent."""
    verbs = {v["verb"] for v in reintegration_offer("r-1", changed=3)["verbs"]}
    assert verbs == {Reintegration.APPLY_LOCALLY.value, Reintegration.CHECKOUT_BRANCH.value}


def test_the_offer_says_nothing_is_applied_automatically():
    assert "automatically" in reintegration_offer("r-1", changed=1)["note"]


def test_CONFLICTS_are_surfaced_on_the_offer():
    """ "Apply this" that then fails with a conflict is worse than "apply this (2 files
    conflict)"."""
    offer = reintegration_offer("r-1", changed=5, conflicts=["a.py", "b.py"])
    assert offer["conflicts"] == ["a.py", "b.py"]
    assert "conflict" in offer["note"]


def test_apply_is_marked_UNSAFE_when_there_are_conflicts():
    offer = reintegration_offer("r-1", conflicts=["a.py"])
    by_verb = {v["verb"]: v["safe"] for v in offer["verbs"]}
    assert by_verb[Reintegration.APPLY_LOCALLY.value] is False


def test_CHECKOUT_stays_safe_even_with_conflicts():
    """Nothing merges on a checkout, so there is nothing to conflict WITH until the user
    decides to."""
    offer = reintegration_offer("r-1", conflicts=["a.py"])
    by_verb = {v["verb"]: v["safe"] for v in offer["verbs"]}
    assert by_verb[Reintegration.CHECKOUT_BRANCH.value] is True


def test_the_offer_names_the_branch():
    assert reintegration_offer("r-9")["branch"] == run_branch("r-9")


# ── stage env ──


def test_the_stage_env_sets_PWD_as_well_as_the_path():
    """Some tools read `PWD` rather than calling `getcwd`, and a stale `PWD` makes a build resolve
    relative paths against the user's real tree — the exact isolation failure the worktree
    prevents.
    """
    env = worktree_env("/tmp/wt")
    assert env["PWD"] == "/tmp/wt"
    assert env["PERSONALCLAW_RUN_WORKTREE"] == "/tmp/wt"
