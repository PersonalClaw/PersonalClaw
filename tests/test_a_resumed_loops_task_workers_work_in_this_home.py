"""A resumed loop's task workers work in this home's worktrees and loop folder.

🔴 The defect (Backlog: "A loop resumed after a restore at another path sends its task workers to
the old home's folders"). Each parallel task worker of a loop has a nudge row whose message names
its worktree, the loop's folder and its findings file as absolute paths, and whose stop file is
the loop folder's; its kept chat records the worktree as the folder it works in. A resume rebuilt
the stage worker's row from the live home and only switched each task worker's row back on, and a
task worker's kept chat came back working in the folder its record names. So in a home restored
at another path, a resumed loop's task workers read their guidance, wrote their findings and edited
code in the old home's folders, and a stop written in the new home never stopped them.

Now a resume makes each task worker's row again from this home, as its spawn makes it, moves a
kept chat recording another folder to this home's worktree (`DashboardState.move_chat_to`), and
only switches a row back on when it already says what this home would. A task worker this home has
no worktree for goes, and its task goes back to open for the loop to take again.

Driven with two homes under `tmp_path`: a loop and its task worker made in the first, the whole
home copied to the second as a restore at another path brings it, and the loop resumed there with
the real dashboard state and chat records.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import worktree as loop_worktree
from personalclaw.loop.loop import Loop
from personalclaw.session import SessionManager

pytestmark = pytest.mark.anyio

TASK = SimpleNamespace(
    id="t-query",
    title="Fix the slow query",
    description="The report query scans the whole table.",
    action_plan=[{"content": "Add the index"}],
    exit_criteria=[{"description": "The report loads in under a second"}],
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _NudgeRows:
    """The nudge service's rows, as `AutoNudgeService` keeps them: `add` replaces a session's row
    with a new one, `update` changes the one it has."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.made = 0

    async def add(self, *, session_name: str, message: str, **kw: Any) -> SimpleNamespace:
        self.made += 1
        row = SimpleNamespace(
            id=f"row-{self.made}",
            session_name=session_name,
            message=message,
            active=True,
            cycle_count=0,
            stop_sentinel_path=str(kw.get("stop_sentinel_path", "")),
        )
        self.rows = {k: r for k, r in self.rows.items() if r.session_name != session_name}
        self.rows[row.id] = row
        return row

    async def update(self, row_id: str, **kw: Any) -> None:
        row = self.rows.get(row_id)
        if row is not None:
            for name, value in kw.items():
                setattr(row, name, value)

    async def remove(self, row_id: str) -> None:
        self.rows.pop(row_id, None)

    def get_by_session(self, session_name: str) -> SimpleNamespace | None:
        return next((r for r in self.rows.values() if r.session_name == session_name), None)

    def list_all(self) -> list[SimpleNamespace]:
        return list(self.rows.values())


class _Home:
    """One home and the gateway state over it."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self._mp = monkeypatch

    def use(self) -> DashboardState:
        self._mp.setenv("PERSONALCLAW_HOME", str(self.root))
        sessions = SessionManager(AppConfig.load())
        state = DashboardState(
            sessions=sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=self.root / "history"),
        )
        state._hook_store = None
        state.broadcast_ws = lambda *a, **k: None
        state.push_sessions_update = lambda *a, **k: None
        return state


@pytest.fixture
def two_homes(tmp_path, monkeypatch):
    async def no_release(*_a, **_kw):
        return None

    async def the_task(task_id, **_kw):
        return TASK if task_id == TASK.id else None

    monkeypatch.setattr("personalclaw.tasks.registry.get_task", the_task)
    monkeypatch.setattr("personalclaw.tasks.registry.update_task", no_release)
    return _Home(tmp_path / "first", monkeypatch), _Home(tmp_path / "second", monkeypatch)


def _repo(where: Path) -> str:
    where.mkdir(parents=True)
    (where / "README.md").write_text("the project\n")
    return str(where)


async def _running_loop_with_a_task_worker(home: _Home, repo: str) -> tuple[str, Any]:
    """A Code-style loop started in *home*, with one task worker in its own worktree, saved and
    paused as a snapshot of a running loop leaves it (a replace restore brings it back held)."""
    state = home.use()
    svc = _NudgeRows()
    loop = store.create(
        Loop(id="", name="Speed", kind="goal", task="speed up the report", workspace_dir=repo)
    )
    await manager.start(state, svc, loop.id)
    worktree_dir = loop_worktree.worktree_path(repo, TASK.id, loop.tasks_project_id)
    os.makedirs(worktree_dir)
    loop = store.get(loop.id)
    await manager.spawn_task_worker(state, svc, loop, TASK, worktree_dir)
    # Its first cycle, so the chat is kept: a chat with nothing said in it is not saved.
    worker = state._sessions[manager.task_session_key(loop.id, TASK.id)]
    worker.append("nudge", "[auto-nudge cycle] work the task", "msg msg-nudge")
    worker.append("assistant", "Started on the index.", "msg msg-a")
    save_all_sessions_to_history(state)
    for row in svc.list_all():
        await svc.update(row.id, active=False)
    return loop.id, svc


def _task_row(svc: _NudgeRows, loop_id: str) -> SimpleNamespace:
    row = svc.get_by_session(manager.task_session_key(loop_id, TASK.id))
    assert row is not None
    return row


async def test_a_loop_resumed_in_a_home_at_another_path_works_in_that_homes_folders(
    two_homes, tmp_path
):
    """🔴 Before: the row's message, its stop file and the worker chat's folder named the first
    home's."""
    first, second = two_homes
    repo = _repo(tmp_path / "repo")
    loop_id, svc = await _running_loop_with_a_task_worker(first, repo)
    old_worktree = loop_worktree.worktree_path(repo, TASK.id, store.get(loop_id).tasks_project_id)
    old_folder = str(loop_files.loop_dir(loop_id))
    assert str(first.root) in old_worktree and str(first.root) in old_folder, "vacuity floor"

    shutil.copytree(first.root, second.root)  # a replace restore at another path
    state = second.use()
    kept = state.get_or_create_session(name=manager.task_session_key(loop_id, TASK.id))
    assert os.path.realpath(kept.workspace_dir) == os.path.realpath(
        old_worktree
    ), "vacuity floor: the restored chat records the first home's worktree"
    await manager.start(state, svc, loop_id)

    new_folder = str(loop_files.loop_dir(loop_id))
    new_worktree = loop_worktree.worktree_path(repo, TASK.id, store.get(loop_id).tasks_project_id)
    assert str(second.root) in new_worktree and str(second.root) in new_folder
    row = _task_row(svc, loop_id)
    assert row.active is True
    assert new_worktree in row.message and new_folder in row.message, row.message[:600]
    assert str(first.root) not in row.message, "nothing in it names the first home"
    assert row.stop_sentinel_path == os.path.join(new_folder, loop_files.STOP_SENTINEL)
    worker = state._sessions[manager.task_session_key(loop_id, TASK.id)]
    assert os.path.realpath(worker.workspace_dir) == os.path.realpath(new_worktree)

    # And it is the chat's record now: a gateway started again over this home opens it there.
    save_all_sessions_to_history(state)
    again = second.use().get_or_create_session(name=manager.task_session_key(loop_id, TASK.id))
    assert os.path.realpath(again.workspace_dir) == os.path.realpath(new_worktree)

    # The control: the stage worker's row is this home's, as it always was.
    stage = svc.get_by_session(manager.session_key(loop_id))
    assert stage is not None and stage.active is True
    assert stage.stop_sentinel_path == os.path.join(new_folder, loop_files.STOP_SENTINEL)


async def test_a_resume_in_the_same_home_only_switches_the_row_back_on(two_homes, tmp_path):
    first, _second = two_homes
    loop_id, svc = await _running_loop_with_a_task_worker(first, _repo(tmp_path / "repo"))
    row = _task_row(svc, loop_id)
    row.cycle_count = 4
    await manager.start(first.use(), svc, loop_id)
    again = _task_row(svc, loop_id)
    assert again.id == row.id and again.active is True and again.cycle_count == 4


async def test_a_task_worker_this_home_has_no_worktree_for_goes(two_homes, tmp_path):
    """A new machine restores no worktree: the task goes back to open for the loop to take again,
    and nothing is sent to work in a folder that is not there."""
    first, second = two_homes
    repo = _repo(tmp_path / "repo")
    loop_id, svc = await _running_loop_with_a_task_worker(first, repo)
    shutil.copytree(first.root, second.root, ignore=shutil.ignore_patterns("worktrees"))
    await manager.start(second.use(), svc, loop_id)
    assert svc.get_by_session(manager.task_session_key(loop_id, TASK.id)) is None
    assert svc.get_by_session(manager.session_key(loop_id)) is not None


async def test_a_task_worker_that_goes_keeps_no_grant_the_paused_run_had(two_homes, tmp_path):
    """ "This loop" granted every worker this run of the loop; a pause ends the run. A task worker
    the resume lets go (its worktree is gone) keeps its chat, on the loop's Mode as it is now."""
    first, _second = two_homes
    repo = _repo(tmp_path / "repo")
    state = first.use()
    svc = _NudgeRows()
    loop = store.create(
        Loop(id="", name="Speed", kind="goal", task="speed up the report", workspace_dir=repo)
    )
    await manager.start(state, svc, loop.id)
    worktree_dir = loop_worktree.worktree_path(repo, TASK.id, store.get(loop.id).tasks_project_id)
    os.makedirs(worktree_dir)
    await manager.spawn_task_worker(state, svc, store.get(loop.id), TASK, worktree_dir)
    worker = state._sessions[manager.task_session_key(loop.id, TASK.id)]
    manager.grant_every_worker(state, loop.id)
    assert worker._trust is True, "vacuity floor: the run's grant reached the task worker"

    await manager.pause(state, svc, loop.id)
    shutil.rmtree(worktree_dir)
    await manager.start(state, svc, loop.id)

    assert svc.get_by_session(manager.task_session_key(loop.id, TASK.id)) is None
    assert worker._trust is False, "the worker let go kept the paused run's grant"
