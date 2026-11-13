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
from a_code_loops_git_runs_off_the_gateways_loop_shared import (  # noqa: F401
    _CAP,
    _held,
    _lock_waits,
    _when,
    hold,
)

from personalclaw.loop import kinds, manager, store, tasks_link, worktree
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
    manager._GIT_LOCKS.clear()


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
    # The identity its owner configured: every commit the loop makes here carries it.
    _git(ws, tmp_path, "config", "user.name", "t")
    _git(ws, tmp_path, "config", "user.email", "t@example.com")
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
            # These loops merge their task workers' work by themselves: an Unattended loop's.
            **{"attended": False, **over},
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


# ── git runs off the gateway's event loop ─────────────────────────────────────

#: Each git step of a stage's fan-out, by the name the scheduler calls it by. An Unattended loop
#: makes the first commit of an unborn repository itself; an Attended one only checks for one.
_CUT_STEPS = [
    "ensure_base_commit",
    "has_base_commit",
    "has_uncommitted_changes",
    "scope_for_task",
    "add_worktrees",
]


async def _schedule_beside(loop, ctx, hold, then):  # noqa: F811 - the imported fixture
    """One scheduler pass, and *then* on the event loop once the held step began."""
    return await asyncio.gather(
        kinds.get("code").schedule(store.get(loop.id), ctx), _when(*hold, then)
    )


@pytest.mark.parametrize("step", _CUT_STEPS)
def test_the_gateway_answers_while_git_readies_a_stages_worktrees(
    repo, monkeypatch, hold, step  # noqa: F811 - the imported fixture
):
    """On a 10K-file repository a worktree took seconds to cut, and the gateway answered nothing
    meanwhile. Each step runs in a worker thread now: a request the gateway gets while git works
    is answered before git is done."""
    loop, state, svc = _armed(repo, attended=step == "has_base_commit")
    seen = _held(monkeypatch, step, *hold)
    made: list[str] = []
    if step == "has_base_commit":
        ensure = worktree.ensure_base_commit
        monkeypatch.setattr(
            worktree, "ensure_base_commit", lambda ws: made.append(ws) or ensure(ws)
        )

    async def a_request():
        return not seen.returned

    _stopped, answered_first = _run(_schedule_beside(loop, _Ctx(state, svc), hold, a_request))

    assert seen.on_loop is False, f"{step} ran on the gateway's event loop"
    assert answered_first, "the request waited for git"
    assert len(_task_workers(loop, svc)) == 2
    assert made == [], "an Attended loop made a base commit"


def test_a_pass_that_comes_while_worktrees_are_cut_cuts_nothing_again(
    repo, monkeypatch, hold  # noqa: F811 - the imported fixture
):
    """A pass that comes while git cuts the stage's worktrees finds no worker for those tasks
    yet; it leaves them to the pass that is cutting them."""
    loop, state, svc = _armed(repo)
    seen = _held(monkeypatch, "add_worktrees", *hold)
    ctx = _Ctx(state, svc)

    waits = _lock_waits(monkeypatch)

    async def another_pass():
        # Started, not awaited: one that got past the turn-away would wait on the cut's lock.
        second = asyncio.ensure_future(kinds.get("code").schedule(store.get(loop.id), ctx))
        second.add_done_callback(lambda _done: waits.set())
        if not await asyncio.to_thread(waits.wait, _CAP):
            raise AssertionError("the second pass neither ended nor waited on the cut")
        return second

    _stopped, second = _run(_schedule_beside(loop, ctx, hold, another_pass))
    _run(second)

    assert seen.on_loop is False
    assert seen.calls == 1, "a second pass cut the same tasks' worktrees again"
    assert len(_task_workers(loop, svc)) == 2
    assert [e for e, _ in ctx.events].count("task_started") == 2


@pytest.mark.parametrize("turn", ["still_running", "already_ended"])
def test_a_stage_worker_whose_cycle_begins_while_git_reads_its_tree_keeps_its_tasks(
    repo, monkeypatch, hold, turn  # noqa: F811 - the imported fixture
):
    """A cycle that began while git read the tree may have edited it after git looked, so its
    tasks stay its own, as they do when its edits are already there. One that is already over by
    the time git answers counts too. The stage worker is never stood down for it: switching its
    nudge loop back on restamps when it was armed, so a stand-down on every poll would starve
    it."""
    loop, state, svc = _armed(repo)
    seen = _held(
        monkeypatch, "has_uncommitted_changes", *hold, answer_first=turn == "already_ended"
    )
    stage = state._sessions[session_key(loop.id)]
    switched_off = []
    update = svc.update

    async def recorded(loop_id, **kw):
        if kw.get("active") is False:
            switched_off.append(loop_id)
        await update(loop_id, **kw)

    svc.update = recorded

    async def a_cycle_begins():
        stage.messages.append({"role": "user", "content": "cycle 1"})
        stage.running = True
        if turn == "already_ended":
            (repo / "digest.py").write_text("def title(t):\n    return t\n")
            stage.running = False

    _run(_schedule_beside(loop, _Ctx(state, svc), hold, a_cycle_begins))

    assert seen.on_loop is False
    assert _task_workers(loop, svc) == [], "a worktree cut from HEAD would miss that cycle's edits"
    assert switched_off == [], "the stage worker was stood down for a fan-out that never came"
    assert svc.get_by_session(session_key(loop.id)).active is True


def test_a_task_taken_off_the_queue_while_worktrees_are_cut_gets_no_worker(
    repo, monkeypatch, hold  # noqa: F811 - the imported fixture
):
    """Its owner takes a task off the queue while git cuts its worktree: no worker starts for it,
    and the other task's does."""
    loop, state, svc = _armed(repo)
    seen = _held(monkeypatch, "add_worktrees", *hold)
    ctx = _Ctx(state, svc)

    async def take_one_off():
        queued = store.get(loop.id).kind_config["queued_task_ids"]
        store.unqueue_tasks(loop.id, [queued[0]])
        return queued

    _stopped, queued = _run(_schedule_beside(loop, ctx, hold, take_one_off))

    assert seen.on_loop is False
    assert svc.get_by_session(task_session_key(loop.id, queued[0])) is None
    assert [t for t in queued if svc.get_by_session(task_session_key(loop.id, t))] == queued[1:]
    assert [d["task_id"] for e, d in ctx.events if e == "task_started"] == queued[1:]


@pytest.mark.parametrize("status", ["cancelled", "done"])
def test_a_task_cancelled_or_done_while_worktrees_are_cut_gets_no_worker(
    repo, monkeypatch, hold, status  # noqa: F811 - the imported fixture
):
    """Its owner cancels a task, or marks it done, while git cuts its worktree: no worker starts
    for it, it keeps that status, and the other task's worker starts. Nothing takes such a task
    off the queue, and starting a worker marks its task in progress."""
    from personalclaw.tasks import registry

    loop, state, svc = _armed(repo)
    seen = _held(monkeypatch, "add_worktrees", *hold)
    ctx = _Ctx(state, svc)

    async def resolve_one():
        queued = list(store.get(loop.id).kind_config["queued_task_ids"])
        await registry.update_task(queued[0], provider_name="native", status=status)
        return queued

    _stopped, queued = _run(_schedule_beside(loop, ctx, hold, resolve_one))

    assert seen.on_loop is False
    assert svc.get_by_session(task_session_key(loop.id, queued[0])) is None
    assert [d["task_id"] for e, d in ctx.events if e == "task_started"] == queued[1:]
    kept = _run(registry.get_task(queued[0], provider_name="native")).status
    assert getattr(kept, "value", kept) == status, f"starting its worker undid the {status}"


@pytest.mark.parametrize("meanwhile", ["unqueued", "stopped"])
def test_a_change_while_one_task_starts_reaches_the_next_ones_start(repo, meanwhile):
    """A pass starts its tasks' workers one after another, and each start waits on the gateway.
    Its owner takes the next task off the queue, or stops the loop, while the first one starts:
    the next one gets no worker, and a stopped loop keeps no fresh checkout for it."""
    loop, state, svc = _armed(repo)
    ctx = _Ctx(state, svc)
    first: list[str] = []
    add = svc.add

    async def changed_meanwhile(*, session_name, **kw):
        row = await add(session_name=session_name, **kw)
        if session_name.startswith(f"{session_key(loop.id)}-") and not first:
            queued = store.get(loop.id).kind_config["queued_task_ids"]
            first.append(next(t for t in queued if task_session_key(loop.id, t) == session_name))
            first.append(next(t for t in queued if t != first[0]))
            if meanwhile == "unqueued":
                store.unqueue_tasks(loop.id, [first[1]])
            else:
                store.update_status(loop.id, LoopStatus.STOPPED)
        return row

    svc.add = changed_meanwhile

    stopped = _run(kinds.get("code").schedule(store.get(loop.id), ctx))

    started, other = first
    assert [d["task_id"] for e, d in ctx.events if e == "task_started"] == [started]
    assert svc.get_by_session(task_session_key(loop.id, other)) is None, "the next task started"
    if meanwhile == "stopped":
        assert stopped is True, "the pass did not say the loop stopped running"
        wt = worktree.worktree_path(str(repo), other, loop.tasks_project_id)
        assert not os.path.isdir(wt), "a stopped loop kept the next task's fresh checkout"
