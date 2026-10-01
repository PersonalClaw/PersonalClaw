"""A loop that ends leaves none of its tasks "in progress": no worker has them any more.

Measured on a live install: a Code loop was stopped, its worktree and branch were removed, and
a day later the companion's Tasks and the task list still read its task as in progress. A
worker marks the task it takes in progress (the spawn does, and so does the worker's own
``task_update``), and nothing moved the task on when the worker went away. The
same happened to a task worker torn down before its task was done: its task stayed in progress,
and the loop's scheduler never takes a task in progress, so a resumed loop never ran it again.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.loop import manager, store, tasks_link
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.tasks import registry


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _State:
    def __init__(self) -> None:
        self._sessions: dict = {}

    def get_or_create_session(self, *, name, agent, model, workspace_dir, app, project_id=""):
        from types import SimpleNamespace

        s = self._sessions.get(name) or SimpleNamespace(
            key=name, running=False, acp_provider="", acp_provider_agent="", reasoning_effort=""
        )
        self._sessions[name] = s
        return s

    def push_sessions_update(self) -> None:
        pass


class _Svc:
    """The nudge service, as far as the loop lifecycle reads it."""

    def __init__(self) -> None:
        self._loops: dict = {}

    async def add(
        self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, first_idle_secs=0
    ):
        from types import SimpleNamespace

        lp = SimpleNamespace(id=f"N{len(self._loops) + 1}", session_name=session_name, active=True)
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, name):
        return next((lp for lp in self._loops.values() if lp.session_name == name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        pass

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _code_loop(*titles: str) -> tuple[Loop, list[str]]:
    """A code loop with one Implementation phase holding *titles* as real tasks."""
    loop = store.create(
        Loop(
            id="",
            name="Tidy the changelog",
            kind="code",
            task="tidy the changelog's release notes",
            plan=[{"stage": "implementation", "title": "Implementation"}],
            kind_config={"entry_stage": "implementation"},
        )
    )
    tasks_link.provision(loop.id)
    ids = _run(
        tasks_link.decompose_phase(loop.id, "implementation", [{"title": t} for t in titles])
    )
    return store.get(loop.id), ids


def _status(task_id: str) -> str:
    task = _run(registry.get_task(task_id, provider_name="native"))
    assert task is not None
    return task.status.value


def _mark(task_id: str, status: str) -> None:
    _run(registry.update_task(task_id, provider_name="native", status=status))


def test_stopping_a_loop_puts_its_task_in_progress_back_to_open():
    loop, (verify,) = _code_loop("Check the headings")
    state, svc = _State(), _Svc()
    # The task worker's spawn is what marks the task in progress.
    task = _run(registry.get_task(verify, provider_name="native"))
    _run(manager.spawn_task_worker(state, svc, loop, task, "/nonexistent/worktree"))
    assert _status(verify) == "in_progress"

    _run(manager.stop(state, svc, loop.id))

    assert store.get(loop.id).status == LoopStatus.STOPPED.value
    assert _status(verify) == "open", "the stopped loop left its task reading 'in progress'"


def test_only_the_tasks_in_progress_move_and_done_work_stays_done():
    loop, (done, working, waiting) = _code_loop("Write it", "Check it", "Ship it")
    _mark(done, "done")
    # The stage worker marks the task it starts itself (`task_update … in_progress`).
    _mark(working, "in_progress")

    _run(manager.stop(_State(), _Svc(), loop.id))

    assert (_status(done), _status(working), _status(waiting)) == ("done", "open", "open")


def test_a_loop_that_fails_or_finishes_releases_its_tasks_too():
    # The watchdog's failed and complete paths end the loop through `end_run`.
    loop, (working,) = _code_loop("Check the headings")
    _mark(working, "in_progress")
    store.update_status(loop.id, LoopStatus.RUNNING)
    store.update_status(loop.id, LoopStatus.FAILED)

    _run(manager.end_run(_State(), _Svc(), loop.id))

    assert _status(working) == "open"


def test_a_task_worker_torn_down_before_its_task_is_done_frees_the_task_for_a_resume():
    loop, (task_id,) = _code_loop("Check the headings")
    store.queue_tasks(loop.id, [task_id])
    loop = store.get(loop.id)
    state, svc = _State(), _Svc()
    task = _run(registry.get_task(task_id, provider_name="native"))
    _run(manager.spawn_task_worker(state, svc, loop, task, "/nonexistent/worktree"))
    assert _run(tasks_link.ready_queued_tasks(store.get(loop.id), "implementation")) == []

    # It ran out of cycles with no result: the scheduler tears the worker down and asks.
    _run(manager.teardown_task_worker(svc, loop.id, task_id))

    assert _status(task_id) == "open"
    ready = _run(tasks_link.ready_queued_tasks(store.get(loop.id), "implementation"))
    assert [t.id for t in ready] == [task_id], "a resumed loop could never run this task again"


def test_a_finished_task_is_left_done_when_its_worker_is_torn_down():
    loop, (task_id,) = _code_loop("Check the headings")
    _mark(task_id, "in_progress")
    _mark(task_id, "done")
    _run(manager.teardown_task_worker(_Svc(), loop.id, task_id))
    assert _status(task_id) == "done"


def test_a_goal_loop_releases_its_own_sub_goals_and_not_a_sibling_loops():
    # Goal loops of one project share its "Sub-goals" list, so a loop's own sub-goals are its
    # LINKED tasks, never the whole list.
    mine = store.create(
        Loop(
            id="",
            name="Mine",
            kind="goal",
            task="find the latency regression",
            kind_config={"goal_type": "open_ended", "sub_goals": ["profile the db path"]},
        )
    )
    (my_goal,) = _run(tasks_link.decompose_sub_goals(mine.id))
    shared_list = store.get(mine.id).task_list_ids["sub_goals"]
    sibling = store.create(
        Loop(
            id="",
            name="Sibling",
            kind="goal",
            task="shrink the image sizes",
            kind_config={"goal_type": "open_ended"},
        )
    )
    theirs = _run(
        registry.create_task(
            provider_name="native", title="audit the images", task_list_id=shared_list
        )
    )
    store.link_tasks(sibling.id, [theirs.id])
    _mark(my_goal, "in_progress")
    _mark(theirs.id, "in_progress")

    _run(manager.stop(_State(), _Svc(), mine.id))

    assert _status(my_goal) == "open"
    assert _status(theirs.id) == "in_progress", "a sibling loop's task was released"


def test_a_runs_own_task_is_left_to_the_run():
    # A task a workflow run manages takes its status from the run's projection alone.
    loop, (working,) = _code_loop("Check the headings")
    list_id = store.get(loop.id).task_list_ids["implementation"]
    managed = _run(
        registry.create_task(
            provider_name="native",
            title="Summarise the changes",
            task_list_id=list_id,
            workflow_binding={"run_id": "r-1", "node_id": "summarise", "managed": True},
        )
    )
    _mark(working, "in_progress")
    _mark(managed.id, "in_progress")

    _run(manager.stop(_State(), _Svc(), loop.id))

    assert (_status(working), _status(managed.id)) == ("open", "in_progress")


def test_the_boot_sweep_releases_what_an_ended_loop_left_in_progress():
    # A gateway that went down between a loop's end and its release, or a home written before
    # the release existed: the ended loop's task still reads in progress at the next start.
    ended, (left_behind,) = _code_loop("Find the slow query")
    _mark(left_behind, "in_progress")
    store.update_status(ended.id, LoopStatus.STOPPED)
    live, (working,) = _code_loop("Still being worked on")
    _mark(working, "in_progress")
    store.update_status(live.id, LoopStatus.PAUSED)

    _run(W.LoopWatchdog(_State(), _Svc())._boot_sweep())

    assert _status(left_behind) == "open"
    assert _status(working) == "in_progress", "a loop that has not ended keeps its task"
