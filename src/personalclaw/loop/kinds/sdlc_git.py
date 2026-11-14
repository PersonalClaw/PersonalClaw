"""What a code loop's scheduler asks git in a worker thread, so the gateway keeps answering.

The code kind's pass (``sdlc.CodeKind``) runs on the gateway's event loop and hands each git step
of a land to a worker thread: an Attended loop's review of a finished task (:func:`review`), an
Unattended loop's check for commits under another name (:func:`other_names`), and every poll's
checks (:class:`PollGit`, read by ``CodeKind._poll_git``). Each answers what git found, and the
pass decides on the event loop what to do with it.
"""

from __future__ import annotations

from typing import NamedTuple


class PollGit(NamedTuple):
    """What a poll asks git before it schedules anything (``CodeKind._poll_git``)."""

    #: The workspace is in a git working tree.
    repo: bool
    #: Git has an identity to commit as there.
    named: bool
    #: Git can cut task worktrees there.
    parallel: bool
    #: Where the run starts, to record at its first poll; ``""`` for nothing to record.
    base: str


class Review(NamedTuple):
    """What git found of a finished task before it lands (``CodeKind._land``)."""

    #: Nothing its worktree holds is left uncommitted.
    committed: bool = False
    #: Its branch brings nothing the workspace lacks, so it was removed with its worktree.
    nothing_new: bool = False
    #: …and its commits were already there.
    already: bool = False
    #: The identity git commits as in the workspace.
    identity: tuple[str, str] | None = None
    #: The commits on its branch made as anyone else.
    other: list[str] = []
    #: The commit its branch is at.
    tip: str = ""


def other_names(ws: str, tid: str) -> Review:
    """In a worker thread: the commits on task *tid*'s branch made as anyone but the identity
    git commits as in *ws* (a worker that set a name of its own)."""
    from personalclaw.loop import worktree

    identity = worktree.commit_identity(ws)
    if identity is None:  # the scheduler asks for one before anything gets this far
        return Review(committed=True)
    return Review(
        committed=True, identity=identity, other=worktree.commits_not_by(ws, tid, identity)
    )


def review(ws: str, tid: str, project: str) -> Review:
    """In a worker thread: an Attended loop's finished task, readied for its owner's review.
    What its worktree holds is committed on its branch; a branch that brings nothing new is
    removed with its worktree; and its other names and its tip are read."""
    from personalclaw.loop import worktree

    if not worktree.commit_pending(ws, tid, project):
        return Review()
    if worktree.brings_nothing_new(ws, worktree.branch_name(tid)):
        already = bool(worktree.branch_commits(ws, tid))
        worktree.remove_worktree(ws, tid, project)
        return Review(committed=True, nothing_new=True, already=already)
    named = other_names(ws, tid)
    return named._replace(tip=worktree.branch_tip(ws, tid))
