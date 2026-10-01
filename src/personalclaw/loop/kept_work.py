"""The work a loop's run left unmerged, and the two things its owner can do with it.

A code loop's task workers each work in their own git worktree, on their own branch. A task that
finishes is merged back into the workspace by the scheduler. One still at work when the run ends
(its budget spent, a failure, a Stop) is not, and its worktree can hold edits its owner approved.
The ending keeps that work (``manager.end_run``), committed on its own branch as git is configured
to commit there. This module puts it to its owner the way an Attended loop puts finished work
(``kinds.sdlc``, ``GET /api/loops/{id}/merge``): each task's branch, the commit it is at, its
commits and its diff, read by the same ``worktree.merge_review``. Only its owner decides, one task
at a time: merge it into the workspace at exactly the commit she reviewed, under the same rules
(git's configured identity, nothing committed under another name), or discard it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from personalclaw.loop import manager, tasks_link, worktree
from personalclaw.loop.loop import ENDED_STATUSES, RESUMABLE_ENDED_STATUSES, Loop, LoopStatus

logger = logging.getLogger(__name__)


def has_ended(loop: Loop) -> bool:
    """Whether *loop*'s run has ended, so nothing of it merges its tasks any more."""
    return LoopStatus(loop.status) in ENDED_STATUSES


def resumable(loop: Loop) -> bool:
    """Whether *loop* can be resumed, so Resume carries its kept work on."""
    return LoopStatus(loop.status) in RESUMABLE_ENDED_STATUSES


async def kept_work(loop: Loop) -> list[dict]:
    """The task work *loop*'s ended run kept because its workspace does not have it: each task's
    id and title, its branch and worktree folder, and how many commits and changed files it holds
    that the workspace lacks (``None`` where git could not say). Empty while the loop is at work,
    and for a loop with no workspace."""
    ws = (loop.workspace_dir or "").strip()
    if not ws or not has_ended(loop):
        return []
    titles = await tasks_link.task_titles(loop)
    works = await asyncio.to_thread(worktree.task_work, ws, list(titles), loop.tasks_project_id)
    return [
        {
            "task_id": w.task_id,
            "title": titles.get(w.task_id, ""),
            "branch": w.branch,
            "path": w.path,
            "commits": w.commits,
            "changed": w.changed,
        }
        for w in works
        if w.unmerged
    ]


async def kept_review(loop: Loop) -> dict:
    """The kept work with what merging each would bring in: the branch it would go into
    (``into``), and for each task, besides :func:`kept_work`'s fields, the commit its branch is at
    (``tip``, what a merge names), its commits (``log``), and its file summary and diff against the
    workspace's branch (``stat``, ``diff``), as an Attended loop's merge review shows them
    (``worktree.merge_review``); ``cut`` says the diffs were longer than one review shows."""
    kept = await kept_work(loop)
    if not kept:
        return {"into": "", "kept": [], "cut": False}
    ws = loop.workspace_dir.strip()
    review = await asyncio.to_thread(worktree.merge_review, ws, [w["task_id"] for w in kept])
    by_id = {t["task_id"]: t for t in review["tasks"]}
    shown = []
    for w in kept:
        t = by_id.get(w["task_id"], {})
        shown.append(
            {
                **w,
                "tip": t.get("tip", ""),
                "log": t.get("commits", []),
                "stat": t.get("stat", ""),
                "diff": t.get("diff", ""),
            }
        )
    return {"into": review["into"], "kept": shown, "cut": review["cut"]}


@dataclass(frozen=True)
class Outcome:
    """How a merge or a discard ended: ``code`` is ``""`` when it did what was asked, else the
    wire code of the refusal, with the files a merge conflicted on or the commits made under
    another name (``named``)."""

    code: str = ""
    conflicts: tuple[str, ...] = ()
    named: tuple[str, ...] = ()


async def _find(loop: Loop, task_id: str) -> dict | None:
    return next((w for w in await kept_work(loop) if w["task_id"] == task_id), None)


async def _release_worker(svc, loop: Loop, task_id: str) -> None:
    """A failed loop kept the task's worker for Resume; once its work is merged or discarded the
    worker's worktree is gone, so the worker goes too, and Resume starts the task afresh."""
    if svc is not None and resumable(loop):
        await manager.teardown_task_worker(svc, loop.id, task_id)


async def merge(svc, loop: Loop, task_id: str, tip: str) -> Outcome:
    """Merge the work *loop* kept for *task_id* into its workspace at *tip*, the commit its owner
    reviewed, by the rules every merge of a loop's follows (``kinds.sdlc``): it is made as git is
    configured to commit there, and a branch holding a commit made under another name is not
    merged. What the worktree holds and has not committed is committed on its branch first, so a
    branch that is then at another commit than the one reviewed is not merged: its review is read
    again (``loop_merge_moved``). The branch is merged into the workspace's checked-out branch, the
    merge is recorded on the loop's page, and the worktree and branch go. A conflict undoes the
    merge and keeps the work as it was."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop.kinds.sdlc import MERGED_BY_OWNER

    if not has_ended(loop):
        return Outcome("loop_still_at_work")
    kept = await _find(loop, task_id)
    if kept is None:
        return Outcome("not_found")
    ws = loop.workspace_dir.strip()
    project = loop.tasks_project_id
    if await asyncio.to_thread(worktree.has_uncommitted_changes, ws):
        return Outcome("workspace_not_committed")
    identity = await asyncio.to_thread(worktree.commit_identity, ws)
    if identity is None:
        return Outcome("kept_work_no_identity")
    if not await asyncio.to_thread(worktree.commit_pending, ws, task_id, project):
        return Outcome("kept_work_unmerged")
    if not tip or tip != await asyncio.to_thread(worktree.branch_tip, ws, task_id):
        return Outcome("loop_merge_moved")
    other = await asyncio.to_thread(worktree.commits_not_by, ws, task_id, identity)
    if other:
        return Outcome("kept_work_other_name", named=tuple(other))
    into = await asyncio.to_thread(worktree.base_branch, ws)
    commits = await asyncio.to_thread(worktree.branch_commits, ws, task_id)
    result = await asyncio.to_thread(worktree.merge_worktree, ws, task_id, project)
    if not result.ok:
        return Outcome(
            "kept_work_conflicts" if result.conflicts else "kept_work_unmerged",
            tuple(result.conflicts),
        )
    if not result.already:
        loop_files.record_merge(
            loop.id,
            {
                "task_id": task_id,
                "title": kept["title"] or task_id,
                "branch": kept["branch"],
                "into": into,
                "commits": commits,
                "head": result.head,
                "by": MERGED_BY_OWNER,
            },
        )
    await _release_worker(svc, loop, task_id)
    logger.info("loop %s: its kept work for task %s was merged by its owner", loop.id, task_id)
    return Outcome()


async def discard(svc, loop: Loop, task_id: str) -> Outcome:
    """Discard the work *loop* kept for *task_id*: its worktree and branch are deleted, with every
    change and commit on them. Only its owner asks for this, from the loop's page."""
    if not has_ended(loop):
        return Outcome("loop_still_at_work")
    if await _find(loop, task_id) is None:
        return Outcome("not_found")
    await asyncio.to_thread(
        worktree.discard, loop.workspace_dir.strip(), [task_id], loop.tasks_project_id
    )
    await _release_worker(svc, loop, task_id)
    logger.info("loop %s: its kept work for task %s was discarded by its owner", loop.id, task_id)
    return Outcome()
