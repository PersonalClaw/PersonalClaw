"""A task merged on its owner's approval is settled: the loop carries on and asks nothing more.

Seen on an Attended code loop: the owner approved a finished task's merge, the scheduler merged it
and removed its worktree and branch, and the loop then paused asking the owner to commit that work
again under their own name, "(git could not list its commits)". Nothing had been committed under
another name, and the work was already in. Two things made that question:

* The scheduler's path for a task whose worker's chat is still in memory (tearing the worker down
  removes its nudge loop, not its chat) landed a done task without asking whether its branch was
  still there, so a task whose own merge had removed it was landed again.
* The identity check read a branch that is gone as one commit by someone else.

Now a done task whose branch is gone is taken off the queue and asked nothing, and a branch that is
gone holds no one's commits. A branch that exists but that git cannot list still keeps its work out,
and a commit made under another name still pauses the loop.

Driven through the real code kind's scheduler on a real git repository of the test's own.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_an_attended_loop_merges_only_what_its_owner_approves import (  # noqa: F401
    OWNER,
    TASK,
    _approved,
    _Ctx,
    _home,
    _loop,
    _repo,
    _schedule,
    code,
)

from personalclaw.loop import files as loop_files
from personalclaw.loop import store, worktree
from personalclaw.loop.loop import LoopStatus
from personalclaw.loop.manager import task_session_key


def _chat_still_in_memory(ctx: _Ctx, loop_id: str) -> None:
    """The task worker's chat, kept in the gateway's memory after its worker was torn down."""
    ctx.state._sessions[task_session_key(loop_id, TASK)] = SimpleNamespace(running=False)


def test_a_task_its_own_merge_removed_is_asked_nothing_on_the_next_pass(
    tmp_path, code  # noqa: F811 - the imported fixture
):
    """🔴 Red before: the next pass landed the task again, read its missing branch as a commit by
    someone else, and paused the loop asking its owner to recommit work already merged."""
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _approved(code, repo, loop)
    ctx = _Ctx()
    _schedule(code, loop, ctx)  # the approved merge lands, and removes the task's branch
    assert not worktree.branch_exists(str(repo), TASK)
    store.update_status(loop.id, LoopStatus.RUNNING)
    # The task is queued again, as a pass that read the loop before the merge still sees it.
    store.queue_tasks(loop.id, [TASK])
    _chat_still_in_memory(ctx, loop.id)

    _schedule(code, loop, ctx)

    now = store.get(loop.id)
    assert str(getattr(now.status, "value", now.status)) == "running", now.status
    assert loop_files.pending_question(loop.id) is None
    assert TASK not in now.kind_config.get("queued_task_ids", [])
    assert len(loop_files.get_merges(loop.id)) == 1, "merged once"


def test_a_branch_that_is_gone_holds_no_ones_commits(tmp_path):
    """🔴 Red before: it read as one commit by someone else."""
    repo = _repo(tmp_path)

    assert worktree.commits_not_by(str(repo), TASK, OWNER) == []


def test_a_branch_git_cannot_list_still_keeps_its_work_out(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    worktree.add_worktree(str(repo), TASK)
    real = worktree._git

    def log_fails(cwd, *args):
        return (128, "") if args and args[0] == "log" else real(cwd, *args)

    monkeypatch.setattr(worktree, "_git", log_fails)

    assert worktree.commits_not_by(str(repo), TASK, OWNER) == [
        f"{worktree.branch_name(TASK)} (git could not list its commits)"
    ]
