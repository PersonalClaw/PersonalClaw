"""A finished task whose work conflicts with the workspace keeps that work, and its owner chooses.

A Code loop merges each finished task's branch into the workspace's checked-out branch. When that
merge conflicted, a loop on autopilot reset the task's branch onto the workspace and ran the task
again, twice at most: the finished work, its commits included, was thrown away with nobody asked,
and an Attended loop did it to work its owner had just approved. A loop that asked instead told her
to "resolve it on branch …, then resume", and an Attended loop stopped on that same question again
at Resume.

Now the merge is undone and the work stays as it was, on its own branch at the commit it reached,
in its own worktree, and the loop pauses and asks: redo the task on top of the branch as it is now,
resolve the conflict herself and Resume (the loop then merges it), or drop the work. Real
repositories and worktrees throughout.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop import worktree
from personalclaw.loop.loop import LoopStatus
from personalclaw.loop.manager import task_session_key
from tests.test_a_loop_that_ends_keeps_the_work_nobody_merged import (  # noqa: F401 — autouse
    LINE,
    SVC,
    _call,
    _Ctx,
    _edit_in,
    _fanned_out,
    _git,
    _repo,
    _run,
    _tmp_home,
)

HER_LINE = "- Feed titles keep their case. (#20)\n"


@pytest.fixture()
def repo(tmp_path):
    return _repo(tmp_path)


def _status(tid) -> str:
    from personalclaw.tasks import registry

    task = _run(registry.get_task(tid, provider_name="native"))
    return getattr(task.status, "value", task.status)


def _finished(loop, tid):
    """Its worker did the task: it marked it done and wrote its finding. The edit is still in the
    worktree, uncommitted, and the merge commits it on the task's branch."""
    from personalclaw.tasks import registry

    _run(registry.update_task(tid, provider_name="native", status="done"))
    findings = loop_files.loop_dir(loop.id) / "findings"
    findings.mkdir(exist_ok=True)
    (findings / f"task_{tid}_001.json").write_text(
        json.dumps({"cycle": 1, "stage": "implementation", "summary": "added the Fixed line"})
    )
    loop_files.record_cycle_findings(loop.id)
    loop_files.write_credited_cycles(loop.id, 1)


def _her_line(repo):
    """She committed a line of her own where the task added its line, on the branch it goes into."""
    with open(repo / "CHANGELOG.md", "a", encoding="utf-8") as fh:
        fh.write(HER_LINE)
    _git(repo, repo.parent, "commit", "-q", "-am", "her own line")


def _conflicted(repo):
    """An Unattended loop paused on its one task's work, which conflicts with her branch."""
    loop, tid, wt, state = _fanned_out(repo, attended=False)
    _finished(loop, tid)
    _her_line(repo)
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))
    return store.get(loop.id), tid, wt, state


def _approved_then_conflicted(repo):
    """An Attended loop whose owner approved merging the task's work, after which she committed a
    line of her own on the branch it goes into, before the scheduler merged it."""
    loop, tid, wt, state = _fanned_out(repo)
    _finished(loop, tid)
    code = kinds.get("code")
    _run(code.schedule(store.get(loop.id), _Ctx(state, SVC)))
    tip = worktree.branch_tip(str(repo), tid)
    status, body = _call(
        H.api_loop_merge, state, "POST", "/", {"tips": {tid: tip}, "confirm": True}, id=loop.id
    )
    assert status == 200, body
    _her_line(repo)
    _run(code.schedule(store.get(loop.id), _Ctx(state, SVC)))
    return store.get(loop.id), tid, wt, state, tip


def _choose(state, loop, body):
    return _call(H.api_loop_conflict, state, "POST", "/", body, id=loop.id)


def _her_branch(repo) -> str:
    return (repo / "CHANGELOG.md").read_text()


# ── the work is kept and she is asked ────────────────────────────────────────


def test_a_conflict_keeps_the_finished_work_on_its_branch_and_asks(repo):
    loop, tid, wt, _state = _conflicted(repo)

    assert loop.status == LoopStatus.NEEDS_INPUT.value, "the loop carried on past the conflict"
    # The work is as the task left it: its commit on its own branch, the edit in its worktree.
    assert [c.split(" ", 1)[1] for c in worktree.branch_commits(str(repo), tid)] == [
        f"task {tid}: work"
    ], "the finished work was thrown away to run the task again"
    assert _edit_in(wt)
    assert _status(tid) == "done", "the finished task was opened again"
    # Nothing of it reached her branch, which is as she left it.
    assert HER_LINE in _her_branch(repo) and LINE not in _her_branch(repo)
    assert _git(repo, repo.parent, "status", "--porcelain").stdout == ""
    asked = loop_files.pending_question(loop.id)
    assert asked["asked_by"] == loop_files.SCHEDULER_QUESTION
    branch = worktree.branch_name(tid)
    assert asked["conflict"] == {
        "task_id": tid,
        "title": "CHANGELOG.md",
        "branch": branch,
        "tip": worktree.branch_tip(str(repo), tid),
        "into": worktree.base_branch(str(repo)),
        "files": ["CHANGELOG.md"],
        "path": wt,
    }
    question = asked["question"]
    assert "conflicts with" in question and "in CHANGELOG.md" in question and branch in question
    for choice in ("redo the task on top of", "resolve the conflict yourself", "drop its work"):
        assert choice in question


def test_work_she_approved_is_kept_when_its_merge_conflicts(repo):
    loop, tid, wt, _state, tip = _approved_then_conflicted(repo)

    assert loop.status == LoopStatus.NEEDS_INPUT.value
    assert worktree.branch_tip(str(repo), tid) == tip, "the branch she approved was reset"
    assert loop_files.pending_question(loop.id)["conflict"]["tip"] == tip
    assert _status(tid) == "done" and _edit_in(wt)


def test_the_page_reads_the_conflict_and_the_work_from_git(repo):
    loop, tid, wt, state = _conflicted(repo)

    status, body = _call(H.api_loop_conflict_review, state, "GET", "/", id=loop.id)

    assert status == 200, body
    assert body["files"] == ["CHANGELOG.md"] and body["into"] == worktree.base_branch(str(repo))
    task = body["task"]
    assert (task["task_id"], task["title"], task["path"]) == (tid, "CHANGELOG.md", wt)
    assert task["tip"] == worktree.branch_tip(str(repo), tid)
    assert [c.split(" ", 1)[1] for c in task["commits"]] == [f"task {tid}: work"]
    assert "+- Digest titles are escaped once. (#21)" in task["diff"]
    assert body["cut"] is False


def test_once_she_resolved_it_the_page_says_nothing_conflicts(repo):
    """What conflicts is read from git each time, not from what the loop's folder says."""
    loop, tid, wt, state = _conflicted(repo)
    _she_merges_it_by_hand(repo, tid)

    status, body = _call(H.api_loop_conflict_review, state, "GET", "/", id=loop.id)

    assert (status, body["files"]) == (200, [])


def test_git_says_what_a_merge_would_conflict_on_without_touching_her_tree(repo):
    ws = str(repo)
    wt = worktree.add_worktree(ws, "t-files")
    with open(os.path.join(wt, "CHANGELOG.md"), "a", encoding="utf-8") as fh:
        fh.write(LINE)
    _git(wt, repo.parent, "commit", "-q", "-am", "the task's line")
    assert worktree.merge_conflicts(ws, "t-files") == []

    _her_line(repo)

    assert worktree.merge_conflicts(ws, "t-files") == ["CHANGELOG.md"]
    assert _git(repo, repo.parent, "status", "--porcelain").stdout == ""
    assert LINE not in _her_branch(repo)
    assert worktree.merge_conflicts(ws, "t-gone") is None, "no branch is not 'nothing conflicts'"


# ── resolve: she does it, then Resume ────────────────────────────────────────


def _she_merges_it_by_hand(repo, tid):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(repo.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    merge = subprocess.run(
        ["git", "-c", "credential.helper=", "merge", "-q", worktree.branch_name(tid)],
        cwd=repo, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert merge.returncode != 0, "the merge was meant to conflict"
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n### Fixed\n" + HER_LINE + LINE
    )
    _git(repo, repo.parent, "commit", "-q", "-am", "merge the digest titles fix")


def test_resume_after_she_resolves_it_lands_the_work_on_an_attended_loop(repo):
    """Her question is the scheduler's, so Resume tries the merge again rather than stopping on
    the same question."""
    loop, tid, wt, state, _tip = _approved_then_conflicted(repo)
    _she_merges_it_by_hand(repo, tid)

    _run(manager.start(state, SVC, loop.id))
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status != LoopStatus.NEEDS_INPUT.value, loop_files.pending_question(loop.id)
    assert loop_files.pending_question(loop.id) is None
    assert not worktree.branch_exists(str(repo), tid) and not os.path.isdir(wt)
    assert tid not in after.kind_config["queued_task_ids"]
    assert LINE in _her_branch(repo) and HER_LINE in _her_branch(repo)


def test_resume_while_it_still_conflicts_asks_again(repo):
    loop, tid, wt, state = _conflicted(repo)
    tip = worktree.branch_tip(str(repo), tid)

    _run(manager.start(state, SVC, loop.id))
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    assert loop_files.pending_question(loop.id)["conflict"]["tip"] == tip
    assert worktree.branch_tip(str(repo), tid) == tip and _edit_in(wt)


# ── redo and drop ────────────────────────────────────────────────────────────


def test_redo_runs_the_task_again_on_top_of_her_branch(repo):
    loop, tid, wt, state = _conflicted(repo)
    tip = worktree.branch_tip(str(repo), tid)

    status, body = _choose(
        state, loop, {"choice": "redo", "task_id": tid, "tip": tip, "confirm": True}
    )

    assert status == 200, body
    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None
    head = _git(repo, repo.parent, "rev-parse", "HEAD").stdout.strip()
    assert worktree.branch_tip(str(repo), tid) == head, "it starts from her branch as it is now"
    with open(os.path.join(wt, "CHANGELOG.md"), encoding="utf-8") as fh:
        assert fh.read() == _her_branch(repo)
    assert _status(tid) == "open"
    # The scheduler starts it afresh: the finding of the attempt set aside is not this run's.
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is not None, "nothing ran it again"
    assert _status(tid) == "in_progress"


def test_drop_discards_the_work_and_the_loop_carries_on_without_it(repo):
    loop, tid, wt, state = _conflicted(repo)
    before = _her_branch(repo)
    tip = worktree.branch_tip(str(repo), tid)

    status, body = _choose(
        state, loop, {"choice": "drop", "task_id": tid, "tip": tip, "confirm": True}
    )

    assert status == 200, body
    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None
    assert not os.path.isdir(wt) and not worktree.branch_exists(str(repo), tid)
    assert _status(tid) == "cancelled"
    assert tid not in store.get(loop.id).kind_config["queued_task_ids"]
    assert _her_branch(repo) == before
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is None, "a dropped task ran again"


def test_a_choice_is_made_only_on_the_work_she_read(repo):
    loop, tid, wt, state = _conflicted(repo)
    tip = worktree.branch_tip(str(repo), tid)

    def refused(body):
        status, answer = _choose(state, loop, body)
        return status, answer["error"]["code"]

    drop = {"choice": "drop", "task_id": tid, "tip": tip}
    assert refused(drop) == (400, "confirm_required")
    assert refused({**drop, "confirm": "true"}) == (400, "confirm_required")
    assert refused({**drop, "choice": "merge", "confirm": True}) == (400, "invalid_request")
    assert refused({**drop, "tip": "", "confirm": True}) == (400, "invalid_request")
    assert refused({**drop, "tip": "0" * 40, "confirm": True}) == (409, "loop_conflict_moved")
    assert refused({**drop, "task_id": "t-elsewhere", "confirm": True}) == (
        409,
        "loop_conflict_not_waiting",
    )
    # Nothing was changed by any of them.
    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    assert worktree.branch_tip(str(repo), tid) == tip and _edit_in(wt) and _status(tid) == "done"


def test_a_question_naming_a_task_the_loop_never_queued_moves_nothing(repo):
    """The loop's folder is its workers' to write: a conflict question there is acted on only for a
    task the loop has queued."""
    loop, tid, wt, state = _conflicted(repo)
    asked = loop_files.pending_question(loop.id)
    other = {**asked["conflict"], "task_id": "t-forged"}
    loop_files.write_question(
        loop.id, asked["question"], asked_by=asked["asked_by"], conflict=other
    )

    status, body = _choose(
        state, loop, {"choice": "drop", "task_id": "t-forged", "tip": other["tip"], "confirm": True}
    )

    assert (status, body["error"]["code"]) == (409, "loop_conflict_not_waiting")
    assert worktree.branch_exists(str(repo), tid) and _edit_in(wt)


def test_a_loop_not_paused_on_a_conflict_has_none_to_show_or_resolve(repo):
    loop, tid, _wt, state = _fanned_out(repo, attended=False)

    status, body = _call(H.api_loop_conflict_review, state, "GET", "/", id=loop.id)
    assert (status, body["error"]["code"]) == (409, "loop_conflict_not_waiting")
    status, body = _choose(
        state, loop, {"choice": "drop", "task_id": tid, "tip": "x", "confirm": True}
    )
    assert (status, body["error"]["code"]) == (409, "loop_conflict_not_waiting")
    assert _call(H.api_loop_conflict_review, state, "GET", "/", id="5e1d0c47")[0] == 404
    assert _call(H.api_loop_conflict_review, state, "GET", "/", id="../x")[0] == 400


# ── a Stop while she decides, and a merge git refuses ────────────────────────


def test_a_stop_while_she_decides_keeps_the_work_for_the_end_screen(repo):
    loop, tid, _wt, state = _conflicted(repo)
    tip = worktree.branch_tip(str(repo), tid)

    _run(manager.stop(state, SVC, loop.id))

    assert loop_files.pending_question(loop.id) is None
    (kept,) = _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)[1]["kept"]
    assert (kept["task_id"], kept["tip"]) == (tid, tip)


def test_a_merge_git_refuses_asks_as_the_scheduler_so_resume_tries_again(repo):
    """Not a conflict: a file of hers that git does not track sits where the task adds one. She is
    asked to check the workspace's git state, and on an Attended loop Resume used to stop on that
    same question again."""
    loop, tid, wt, state = _fanned_out(repo)
    with open(os.path.join(wt, "NOTES.md"), "w", encoding="utf-8") as fh:
        fh.write("the task's notes\n")
    _finished(loop, tid)
    code = kinds.get("code")
    _run(code.schedule(store.get(loop.id), _Ctx(state, SVC)))
    tip = worktree.branch_tip(str(repo), tid)
    _call(H.api_loop_merge, state, "POST", "/", {"tips": {tid: tip}, "confirm": True}, id=loop.id)
    (repo / "NOTES.md").write_text("her own notes, not in git\n")
    _run(code.schedule(store.get(loop.id), _Ctx(state, SVC)))
    asked = loop_files.pending_question(loop.id)
    assert "could not be merged (a git error, not a content conflict)" in asked["question"]
    assert asked["asked_by"] == loop_files.SCHEDULER_QUESTION
    assert worktree.branch_tip(str(repo), tid) == tip

    (repo / "NOTES.md").unlink()  # she moved her file out of the way
    _run(manager.start(state, SVC, loop.id))
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())

    asked = loop_files.pending_question(loop.id) or {}
    assert "git error" not in asked.get("question", ""), "Resume stopped on the same question"
    # Her approval was for the merge that failed, so the work is put to her again.
    assert asked["merge"]["tasks"][0]["task_id"] == tid
