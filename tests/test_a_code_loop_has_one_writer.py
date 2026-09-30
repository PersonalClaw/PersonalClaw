"""A code loop has one writer at a time: its stage worker or its task workers, never both.

Measured on a Code loop over an existing repository: the stage worker made its first cycle's
edits in the repository and left them uncommitted; the scheduler then started a task worker in a
worktree cut from HEAD, which did not have those edits, so it read the old file and made the same
edits again — while the stage worker went on working the same tree. Two sessions then queued on
the one local model until every call timed out.

So: a stage fans out only from a clean tree and only while its stage worker is between cycles;
the stage worker stands down while task workers run and stands back up when they drain; a loop
on a model that runs on this machine runs one task worker at a time; and the watchdog asks the
scheduler on every poll, so a stage fans out before its stage worker's first cycle on it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from personalclaw.loop import kinds, store, tasks_link
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import session_key, task_session_key


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    yield tmp_path
    kinds.get("code")._stood_down.clear()


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
    ws = tmp_path / "feedsmith"
    ws.mkdir()
    (ws / "digest.py").write_text("import html\n\ndef title(t):\n    return html.escape(t)\n")
    _git(ws, tmp_path, "init", "-q")
    _git(ws, tmp_path, "add", "digest.py")
    _git(ws, tmp_path, "commit", "-q", "-m", "init")
    return ws


class _Svc:
    """The nudge loops: one per worker session, active or stood down."""

    def __init__(self):
        self._loops: dict[str, SimpleNamespace] = {}
        self._n = 0

    async def add(self, *, session_name, **_kw):
        self._n += 1
        lp = SimpleNamespace(id=f"N{self._n}", session_name=session_name, active=True)
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
    )


class _State:
    def __init__(self):
        self._sessions: dict = {}

    def get_or_create_session(self, *, name, **_kw):
        return self._sessions.setdefault(name, _session(name))

    def push_sessions_update(self):
        pass

    # What the watchdog reads of the dashboard.
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


def _loop(ws, **over) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="stop escaping digest titles twice",
            autopilot=True,
            workspace_dir=str(ws),
            plan=[
                {
                    "stage": "implementation",
                    "title": "Impl",
                    "tasks": [{"title": "digest.py"}, {"title": "CHANGELOG.md"}],
                }
            ],
            phase_status={"implementation": "active"},
            **over,
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    return store.get(loop.id)


def _armed(ws, **over):
    """A running loop whose stage worker exists and is between cycles."""
    loop = _loop(ws, **over)
    state, svc = _State(), _Svc()
    state.get_or_create_session(name=session_key(loop.id))
    _run(svc.add(session_name=session_key(loop.id)))
    return loop, state, svc


def _task_workers(loop, svc) -> list[str]:
    queued = store.get(loop.id).kind_config.get("queued_task_ids", [])
    return [t for t in queued if svc.get_by_session(task_session_key(loop.id, t)) is not None]


def _schedule(loop, state, svc):
    return _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, svc)))


# ── when a stage fans out ─────────────────────────────────────────────────────


def test_a_clean_tree_fans_out_and_the_stage_worker_stands_down_until_its_tasks_drain(repo):
    from personalclaw.tasks import registry

    loop, state, svc = _armed(repo)
    stage = svc.get_by_session(session_key(loop.id))

    _schedule(loop, state, svc)

    assert len(_task_workers(loop, svc)) == 2
    assert stage.active is False, "the stage worker kept its cycles while task workers ran"

    for tid in store.get(loop.id).kind_config["queued_task_ids"]:
        _run(registry.update_task(tid, provider_name="native", status="done"))
    _schedule(loop, state, svc)

    assert _task_workers(loop, svc) == []
    assert stage.active is True, "the stage worker never came back once its tasks drained"


def test_edits_the_stage_worker_left_uncommitted_keep_its_tasks_its_own(repo):
    loop, state, svc = _armed(repo)
    (repo / "digest.py").write_text("def title(t):\n    return t\n")  # cycle 1's edit

    _schedule(loop, state, svc)

    assert _task_workers(loop, svc) == [], "a worktree cut from HEAD would redo that edit"
    assert svc.get_by_session(session_key(loop.id)).active is True
    assert len(store.get(loop.id).kind_config["queued_task_ids"]) == 2


def test_no_task_worker_starts_while_the_stage_worker_is_mid_turn(repo):
    loop, state, svc = _armed(repo)
    state._sessions[session_key(loop.id)].running = True

    _schedule(loop, state, svc)

    assert _task_workers(loop, svc) == []


def test_a_stage_worker_the_scheduler_did_not_stand_down_is_left_as_it_is(repo):
    loop, state, svc = _armed(repo)
    stage = svc.get_by_session(session_key(loop.id))
    stage.active = False  # it ran out of cycles, say
    (repo / "digest.py").write_text("changed\n")  # and nothing fans out

    _schedule(loop, state, svc)

    assert stage.active is False


def test_a_loop_on_a_model_on_this_machine_runs_one_task_worker_at_a_time(repo, monkeypatch):
    monkeypatch.setattr(
        "personalclaw.llm.registry.sends_to_this_machine", lambda entry: entry == "ollama"
    )
    loop, state, svc = _armed(repo, model="ollama:gemma4:12b")

    _schedule(loop, state, svc)

    assert len(_task_workers(loop, svc)) == 1


def test_a_loop_on_a_cloud_model_keeps_its_pool(repo, monkeypatch):
    monkeypatch.setattr("personalclaw.llm.registry.sends_to_this_machine", lambda entry: False)
    loop, state, svc = _armed(repo, model="cloud:big-model")

    _schedule(loop, state, svc)

    assert len(_task_workers(loop, svc)) == 2


# ── the watchdog asks on every poll ───────────────────────────────────────────


def test_a_stage_fans_out_before_the_stage_workers_first_finding(repo):
    from personalclaw.loop import watchdog as W

    loop, state, svc = _armed(repo)
    store.update_status(loop.id, LoopStatus.RUNNING)

    _run(W.LoopWatchdog(state, svc)._poll_once())

    assert len(_task_workers(loop, svc)) == 2
    assert svc.get_by_session(session_key(loop.id)).active is False
