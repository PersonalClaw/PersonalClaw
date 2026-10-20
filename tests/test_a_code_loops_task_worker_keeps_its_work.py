"""A code loop's task worker keeps its work: a slow turn is not a stall, a failed run keeps what it
made, and Resume gives the worker a turn.

Measured on an Attended code loop over an existing repository, one task worker on a local model:
the owner approved the worker's first edit, the worker's next model call took fifteen minutes on a
busy machine, and the loop failed ten minutes in with "No activity — the worker stalled". Three
things made that one slow call cost the run:

* the watchdog read only the STAGE worker's session for a turn in flight, so a task worker
  mid-turn looked like nobody working;
* the failure tore the workers down the way a Stop does, and that removes every task worktree, so
  the edit she had approved went with it;
* the scheduler read "this task's worker loop is gone" as "it ran out of cycles", so every Resume
  (and every steer) bounced straight back to "ran out of cycles" without the worker taking a turn.
  The nudge service never removes a loop that ran out of cycles: it switches it off and keeps it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager, store, tasks_link
from personalclaw.loop import watchdog as W
from personalclaw.loop import worktree
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import task_session_key


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    # One task worker at a time, as on a model that runs on this machine.
    monkeypatch.setattr("personalclaw.llm.registry.sends_to_this_machine", lambda entry: True)
    kinds.ensure_loaded()
    yield tmp_path
    kinds.get("code")._stood_down.clear()
    manager._LOOP_GRANTS.clear()


def _git(ws, home, *args):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "user.name=t", "-c", "user.email=t@example.com",
         *args],
        cwd=ws, check=True, capture_output=True, env=env,
    )  # fmt: skip


@pytest.fixture()
def repo(tmp_path):
    ws = tmp_path / "newsfold"
    ws.mkdir()
    (ws / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### Fixed\n")
    _git(ws, tmp_path, "init", "-q")
    # The identity its owner configured: every commit the loop makes here carries it.
    _git(ws, tmp_path, "config", "user.name", "t")
    _git(ws, tmp_path, "config", "user.email", "t@example.com")
    _git(ws, tmp_path, "add", "CHANGELOG.md")
    _git(ws, tmp_path, "commit", "-q", "-m", "init")
    return ws


class _Svc:
    """The nudge loops as the real service keeps them: one per worker session, with its budget."""

    def __init__(self):
        self._loops: dict[str, SimpleNamespace] = {}
        self._n = 0

    async def add(self, *, session_name, max_cycles=0, **_kw):
        for lid in [k for k, lp in self._loops.items() if lp.session_name == session_name]:
            del self._loops[lid]
        self._n += 1
        lp = SimpleNamespace(
            id=f"N{self._n}",
            session_name=session_name,
            active=True,
            cycle_count=0,
            max_cycles=max_cycles,
            error_count=0,
        )
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        for k, v in kw.items():
            setattr(self._loops[loop_id], k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


class _Turns:
    """The chat Stop the loop manager stops a worker's turn through."""

    def __init__(self):
        self.stopped: list[str] = []

    async def stop_turn(self, key, *, force=False, **_kw):
        self.stopped.append(key)
        return "soft"


def _session(name):
    return SimpleNamespace(
        key=name,
        running=False,
        messages=[],
        acp_provider="",
        acp_provider_agent="",
        reasoning_effort="",
        acp_mode="",
        _extra_tool_roots=None,
        _queue=[],
    )


class _State:
    def __init__(self):
        self._sessions: dict = {}
        self.sessions = _Turns()

    def get_or_create_session(self, *, name, **_kw):
        return self._sessions.setdefault(name, _session(name))

    def push_sessions_update(self):
        pass

    def loop_sse(self):
        from personalclaw.dashboard.sse import SseRegistry

        return SseRegistry()

    def push_refresh(self, *kinds):
        pass

    def notify(self, *args, **kwargs):
        pass

    def waiting_on_owner(self, key):
        return False


class _Ctx:
    def __init__(self, state, svc):
        self.state, self.svc, self.events = state, svc, []

    def publish(self, loop_id, event, data=None):
        self.events.append((event, data))

    async def complete(self, loop_id, reason=""):
        pass


def _fanned_out(ws):
    """A running Attended code loop on autopilot whose one task worker has started in its own
    worktree and has made the edit its owner approved."""
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="add a Fixed line for the digest titles",
            attended=True,
            autopilot=True,
            model="ollama:local-model",
            max_cycles=30,
            workspace_dir=str(ws),
            plan=[
                {"stage": "implementation", "title": "Impl", "tasks": [{"title": "CHANGELOG.md"}]}
            ],
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    state, svc = _State(), _Svc()
    _run(manager.start(state, svc, loop.id))
    _schedule(loop, state, svc)
    loop = store.get(loop.id)
    (tid,) = loop.kind_config["queued_task_ids"]
    assert svc.get_by_session(task_session_key(loop.id, tid)) is not None, "no task worker started"
    wt = worktree.worktree_path(str(ws), tid, loop.tasks_project_id)
    with open(os.path.join(wt, "CHANGELOG.md"), "a", encoding="utf-8") as fh:
        fh.write("- Digest titles are escaped once. (#21)\n")
    return loop, tid, wt, state, svc


def _schedule(loop, state, svc):
    return _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, svc)))


def _watched(state, svc, loop):
    """A watchdog that has already met the loop and last saw it do anything long ago."""
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=1.0)  # running for a long time
    wd = W.LoopWatchdog(state, svc)
    _run(wd._poll_once())  # first sight seeds the liveness clock
    wd._last_activity[loop.id] = 1.0
    return wd


def _edit_in(wt) -> bool:
    try:
        with open(os.path.join(wt, "CHANGELOG.md"), encoding="utf-8") as fh:
            return "escaped once" in fh.read()
    except OSError:
        return False


# ── a slow turn is not a stall ────────────────────────────────────────────────


def test_a_task_workers_turn_in_flight_is_activity(repo):
    loop, tid, _wt, state, svc = _fanned_out(repo)
    state._sessions[task_session_key(loop.id, tid)].running = True  # a long model call
    wd = _watched(state, svc, loop)

    _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status == LoopStatus.RUNNING.value, after.error_message


def test_a_loop_with_no_worker_turn_at_all_still_fails_as_stalled(repo):
    loop, _tid, _wt, state, svc = _fanned_out(repo)
    wd = _watched(state, svc, loop)

    _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status == LoopStatus.FAILED.value
    assert "stalled" in (after.error_message or "")


# ── a failed run keeps what it made ───────────────────────────────────────────


def test_a_failed_run_keeps_the_task_workers_worktree_and_its_edit(repo):
    loop, tid, wt, state, svc = _fanned_out(repo)
    wd = _watched(state, svc, loop)

    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.FAILED.value
    assert _edit_in(wt), "the failure removed the worktree holding the approved edit"
    assert worktree.branch_exists(str(repo), tid)
    worker = svc.get_by_session(task_session_key(loop.id, tid))
    assert worker is not None and worker.active is False, "the failed run's worker kept its cycles"


def test_a_wedged_failure_stops_the_turn_still_in_flight(repo):
    loop, tid, _wt, state, svc = _fanned_out(repo)
    key = task_session_key(loop.id, tid)
    state._sessions[key].running = True
    wd = _watched(state, svc, loop)
    wd._running_since[loop.id] = 1.0  # running far longer than any turn may

    _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status == LoopStatus.FAILED.value
    assert "wedged" in (after.error_message or "")
    assert f"dashboard:{key}" in state.sessions.stopped, "a failed loop's worker kept working"


def test_resume_after_a_failure_carries_on_with_the_kept_work(repo):
    loop, tid, wt, state, svc = _fanned_out(repo)
    _run(_watched(state, svc, loop)._poll_once())
    assert store.get(loop.id).status == LoopStatus.FAILED.value

    _run(manager.start(state, svc, loop.id))
    _schedule(loop, state, svc)

    after = store.get(loop.id)
    assert after.status == LoopStatus.RUNNING.value, loop_files.pending_question(loop.id)
    assert loop_files.pending_question(loop.id) is None
    worker = svc.get_by_session(task_session_key(loop.id, tid))
    assert worker is not None and worker.active is True, "Resume never gave the worker a turn"
    assert _edit_in(wt)


# ── "ran out of cycles" means it did ──────────────────────────────────────────


def test_a_worker_that_used_its_whole_budget_asks_its_owner(repo):
    loop, tid, _wt, state, svc = _fanned_out(repo)
    worker = svc.get_by_session(task_session_key(loop.id, tid))
    worker.cycle_count, worker.active = worker.max_cycles, False  # what the nudge service does

    assert _schedule(loop, state, svc) is True

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    question = (loop_files.pending_question(loop.id) or {}).get("question", "")
    assert "ran out of cycles" in question
    # On autopilot the task panel offers a steer and no remove, so the ask offers no remove either.
    assert "remove" not in question, question


def test_a_steer_after_running_out_of_cycles_gives_the_task_a_fresh_worker(repo):
    loop, tid, _wt, state, svc = _fanned_out(repo)
    worker = svc.get_by_session(task_session_key(loop.id, tid))
    worker.cycle_count, worker.active = worker.max_cycles, False
    _schedule(loop, state, svc)
    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value

    _run(manager.nudge(state, svc, loop.id, "Add the line under Fixed.", task_id=tid))
    assert _schedule(loop, state, svc) is False

    assert store.get(loop.id).status == LoopStatus.RUNNING.value, loop_files.pending_question(
        loop.id
    )
    fresh = svc.get_by_session(task_session_key(loop.id, tid))
    assert fresh is not None and fresh.active and fresh.cycle_count == 0


def test_a_task_whose_old_turn_outlived_its_worker_loop_is_armed_again(repo):
    loop, tid, wt, state, svc = _fanned_out(repo)
    key = task_session_key(loop.id, tid)
    _run(svc.remove(svc.get_by_session(key).id))
    state._sessions[key].running = True  # a turn from before the worker loop went

    task = SimpleNamespace(id=tid, title="CHANGELOG.md", description="")
    _run(manager.spawn_task_worker(state, svc, store.get(loop.id), task, wt))

    assert svc.get_by_session(key) is not None, "the task kept a session but nothing to run it"
