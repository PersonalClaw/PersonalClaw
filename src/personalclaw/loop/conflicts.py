"""A finished task whose work conflicts with its workspace, and how its owner resolves it.

A Code loop merges each finished task's branch into the workspace's checked-out branch
(``kinds.sdlc.CodeKind._reap_merge_done``). When that merge conflicts, it is undone and the task's
work stays exactly as it was: on its own branch, at the commit it reached, in its own worktree. The
loop pauses and asks its owner, and nothing of the work is discarded until she chooses:

* **redo**: the task runs again from the workspace's branch as it is now. The attempt that
  conflicted is set aside: its branch and worktree are reset onto that branch, and the scheduler
  starts the task afresh.
* **resolve**: she resolves the conflict herself, in her workspace or on the task's branch, and
  Resumes. Her question is the scheduler's (``files.SCHEDULER_QUESTION``), so Resume merges the
  branch again, and the loop asks again while it still conflicts.
* **drop**: the task's branch and worktree are deleted and the task is cancelled; the loop carries
  on without it.

A choice names the commit she read (``tip``): a branch that moved since is read again. The question
lives in the loop's folder, which its workers can write, so a choice is taken only for a task this
loop has queued, and what the page shows of the work is read from git.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from personalclaw.loop import files as loop_files
from personalclaw.loop import store, worktree
from personalclaw.loop.loop import Loop, LoopStatus

logger = logging.getLogger(__name__)

#: What its owner can choose by request. Resolving it is hers to do, then Resume.
CHOICES = ("redo", "drop")


def named(files: list[str]) -> str:
    """The files a conflict is in, as its sentences name them: the first five, then an ellipsis."""
    return ", ".join(files[:5]) + ("…" if len(files) > 5 else "")


def question(title: str, branch: str, into: str, files: list[str]) -> str:
    """What its owner is asked when a finished task's work conflicts with *into*."""
    where = f" in {named(files)}" if files else ""
    return (
        f'Task "{title}" is done, but its work conflicts with {into}{where}, so none of it was '
        f"merged. It is kept as it is on branch {branch}. Choose on the project's page: redo the "
        f"task on top of {into} as it is now, resolve the conflict yourself and then Resume, or "
        "drop its work."
    )


def ask(loop: Loop, task_id: str, title: str, ws: str, files: list[str]) -> None:
    """Put the conflict to *loop*'s owner, as the scheduler's question, with what it is about:
    the task, its branch and the commit it is at, the branch it went into, the files it conflicted
    on, and the worktree the branch is checked out in."""
    branch = worktree.branch_name(task_id)
    into = worktree.base_branch(ws)
    loop_files.write_question(
        loop.id,
        question(title, branch, into, files),
        asked_by=loop_files.SCHEDULER_QUESTION,
        conflict={
            "task_id": task_id,
            "title": title,
            "branch": branch,
            "tip": worktree.branch_tip(ws, task_id),
            "into": into,
            "files": list(files),
            "path": worktree.worktree_path(ws, task_id, loop.tasks_project_id),
        },
    )


def waiting(loop: Loop) -> dict | None:
    """The conflict *loop* is paused on, as :func:`ask` put it, or ``None``."""
    if loop.status != LoopStatus.NEEDS_INPUT.value:
        return None
    asked = loop_files.pending_question(loop.id) or {}
    conflict = asked.get("conflict")
    if not isinstance(conflict, dict) or not str(conflict.get("task_id") or ""):
        return None
    return conflict


async def review(loop: Loop, conflict: dict) -> dict:
    """What the page shows of the conflict: the branch the work went into (``into``), the files it
    conflicts on as git reads them now (``files``: empty once it would merge cleanly, the ones the
    merge met when git cannot say), and the task's work as a merge review shows it (``task``: its
    branch, the commit it is at, its commits, its file summary and diff, and its worktree folder).
    """
    ws = (loop.workspace_dir or "").strip()
    task_id = str(conflict["task_id"])
    shown = await asyncio.to_thread(worktree.merge_review, ws, [task_id])
    now = await asyncio.to_thread(worktree.merge_conflicts, ws, task_id)
    recorded = [str(f) for f in conflict.get("files") or [] if isinstance(f, str)]
    task = next(iter(shown["tasks"]), {"task_id": task_id})
    return {
        "into": shown["into"],
        "files": recorded if now is None else now,
        "task": {
            **task,
            "title": str(conflict.get("title") or ""),
            "path": worktree.worktree_path(ws, task_id, loop.tasks_project_id),
        },
        "cut": shown["cut"],
    }


@dataclass(frozen=True)
class Outcome:
    """How a choice ended: ``code`` is ``""`` when it did what was asked, else the wire code of the
    refusal (nothing was changed)."""

    code: str = ""


async def choose(svc, loop: Loop, task_id: str, tip: str, choice: str) -> Outcome:
    """Carry out *choice* (one of :data:`CHOICES`) for the conflict *loop* is paused on, at *tip*,
    the commit its owner read. The question is cleared; the caller resumes the loop."""
    from personalclaw.loop import manager

    conflict = waiting(loop)
    queued = (loop.kind_config or {}).get("queued_task_ids") or []
    if conflict is None or str(conflict["task_id"]) != task_id or task_id not in queued:
        return Outcome("loop_conflict_not_waiting")
    ws = (loop.workspace_dir or "").strip()
    if not tip or tip != await asyncio.to_thread(worktree.branch_tip, ws, task_id):
        return Outcome("loop_conflict_moved")
    # No worker of the attempt that conflicted fires again (its turn ended before the merge).
    await manager.teardown_task_worker(svc, loop.id, task_id)
    if choice == "redo":
        await _redo(loop, ws, task_id)
    else:
        await _drop(loop, ws, task_id)
    loop_files.clear_question(loop.id)
    logger.info(
        "loop %s: its owner chose to %s task %s after its conflict", loop.id, choice, task_id
    )
    return Outcome()


async def _redo(loop: Loop, ws: str, task_id: str) -> None:
    """Set the attempt aside and open the task again, so the scheduler starts it afresh on the
    workspace's branch as it is now. The worktree is reset rather than cut again, which keeps its
    hydration; one that cannot be reset is removed with its branch, and the scheduler cuts a new
    one when it starts the task."""
    from personalclaw.tasks import registry

    project = loop.tasks_project_id
    if not await asyncio.to_thread(worktree.reset_worktree, ws, task_id, project):
        await asyncio.to_thread(worktree.remove_worktree, ws, task_id, project)
    await registry.update_task(task_id, provider_name="native", status="open")


async def _drop(loop: Loop, ws: str, task_id: str) -> None:
    """Delete the task's branch and worktree, cancel the task and take it off the queue: a
    cancelled task is resolved, so what depends on it goes on, and autopilot does not queue it
    again."""
    from personalclaw.tasks import registry

    await asyncio.to_thread(worktree.discard, ws, [task_id], loop.tasks_project_id)
    await registry.update_task(task_id, provider_name="native", status="cancelled")
    store.unqueue_tasks(loop.id, [task_id])
