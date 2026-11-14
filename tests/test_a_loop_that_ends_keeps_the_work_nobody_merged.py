"""A loop's run ends one way, whatever ends it, and never throws away work nobody merged.

A Code loop's task worker works in its own git worktree, on its own branch, and the scheduler
merges a task back once it is done. Once a failed run was taught to keep its task workers'
worktrees, two other endings still lost work or left the loop working:

* a run that spent its budget (cycles, cost or time) completed, and completing removed every task
  worktree and branch, so a task worker still at work lost its worktree, edits its owner had
  approved included;
* a run whose worker's turns kept failing was marked failed and nothing more, so its workers'
  nudge loops stayed switched on and fired the failed loop's next cycle.

Every ending now goes through one function (``manager.end_run``): every nudge loop of the loop
(its stage worker's, its task workers' and its planner's) is switched off, the turns in flight are
stopped, merged work is cleaned up, and work nobody merged is kept. The loop's page names what is
kept and where, and its owner merges or discards it; only a delete discards it unasked, and its
dialog says so.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest
from a_code_loops_git_runs_off_the_gateways_loop_shared import (  # noqa: F401
    _CAP,
    _held,
    _lock_waits,
    _when,
    hold,
)
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager, store, tasks_link
from personalclaw.loop import watchdog as W
from personalclaw.loop import worktree
from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason
from personalclaw.loop.manager import session_key, task_session_key
from personalclaw.loop.plan_walkthrough import planner_session_key

#: The cap of each test here that drives a loop over real git repositories: up to 15.8 s alone
#: on a Mac under load (tests/timeout-marks.toml), past the suite's 30 s in a loaded set.
_GIT_LOOP_CAP = pytest.mark.timeout(60)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _settled():
    """Let every task the code under test started finish (an ending a callback scheduled)."""
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*others)


class _Svc:
    """The nudge loops as the real service keeps them: one per session, kept on disk."""

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


SVC = _Svc()


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    # One task worker at a time, as on a model that runs on this machine.
    monkeypatch.setattr("personalclaw.llm.registry.sends_to_this_machine", lambda entry: True)
    SVC._loops.clear()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: SVC)
    kinds.ensure_loaded()
    yield tmp_path
    kinds.get("code")._stood_down.clear()
    manager._LOOP_GRANTS.clear()
    manager._DELETING.clear()
    manager._GIT_LOCKS.clear()


def _git(ws, home, *args):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "user.name=t", "-c", "user.email=t@example.com",
         *args],
        cwd=ws, check=True, capture_output=True, env=env, text=True,
    )  # fmt: skip


def _repo(tmp_path, name="newsfold"):
    ws = tmp_path / name
    ws.mkdir()
    (ws / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### Fixed\n")
    _git(ws, tmp_path, "init", "-q")
    # The identity its owner configured: every commit the loop makes here carries it.
    _git(ws, tmp_path, "config", "user.name", "t")
    _git(ws, tmp_path, "config", "user.email", "t@example.com")
    _git(ws, tmp_path, "add", "CHANGELOG.md")
    _git(ws, tmp_path, "commit", "-q", "-m", "init")
    return ws


@pytest.fixture()
def repo(tmp_path):
    return _repo(tmp_path)


class _Turns:
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
    conversation_log = None

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


LINE = "- Digest titles are escaped once. (#21)\n"


def _started(ws, *, state=None, project_id="", name="digest titles", attended=True):
    """A running code loop on autopilot, Attended unless said otherwise, with one task in its
    stage, that its scheduler has not asked about yet."""
    loop = store.create(
        Loop(
            id="",
            name=name,
            kind="code",
            task="add a Fixed line for the digest titles",
            attended=attended,
            autopilot=True,
            model="ollama:local-model",
            max_cycles=30,
            workspace_dir=str(ws),
            project_id=project_id,
            plan=[
                {"stage": "implementation", "title": "Impl", "tasks": [{"title": "CHANGELOG.md"}]}
            ],
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    state = state or _State()
    _run(manager.start(state, SVC, loop.id))
    return store.get(loop.id), state


def _fanned_out(ws, *, state=None, project_id="", name="digest titles", attended=True):
    """A running code loop on autopilot, Attended unless said otherwise, whose one task worker has
    started in its own worktree and has made the edit its owner approved there, not yet merged.
    Its planner's nudge row is still on disk from its walkthrough, as one is after a restart."""
    loop, state = _started(ws, state=state, project_id=project_id, name=name, attended=attended)
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))
    loop = store.get(loop.id)
    (tid,) = loop.kind_config["queued_task_ids"]
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is not None, "no task worker started"
    _run(SVC.add(session_name=planner_session_key(loop.id)))
    wt = worktree.worktree_path(str(ws), tid, loop.tasks_project_id)
    with open(os.path.join(wt, "CHANGELOG.md"), "a", encoding="utf-8") as fh:
        fh.write(LINE)
    return loop, tid, wt, state


def _edit_in(wt) -> bool:
    try:
        with open(os.path.join(wt, "CHANGELOG.md"), encoding="utf-8") as fh:
            return "escaped once" in fh.read()
    except OSError:
        return False


def _rows(loop_id):
    """Every nudge row of the loop: its stage worker's, its task workers' and its planner's."""
    mine = (session_key(loop_id), planner_session_key(loop_id))
    return [
        r
        for r in SVC.list_all()
        if r.session_name in mine or r.session_name.startswith(f"{session_key(loop_id)}-")
    ]


def _enabled(loop_id):
    return sorted(r.session_name for r in _rows(loop_id) if r.active)


# ── a run that spent its budget keeps the work nobody merged ─────────────────


BUDGETS = [
    ("cycle budget reached", LoopStopReason.CYCLE_BUDGET),
    ("cost budget reached ($2.00 >= $2.00)", LoopStopReason.COST_BUDGET),
    ("deadline reached (3600s active >= 3600s)", LoopStopReason.DEADLINE),
]


@pytest.mark.parametrize(("reason", "why"), BUDGETS, ids=[b[1].value for b in BUDGETS])
def test_a_run_that_spent_its_budget_keeps_the_task_work_nobody_merged(repo, reason, why):
    loop, tid, wt, state = _fanned_out(repo)
    wd = W.LoopWatchdog(state, SVC)

    # How the watchdog ends a run whose budget ran out.
    _run(wd._complete(loop.id, reason=reason, genuine=False, stop_reason=why))

    assert store.get(loop.id).status == LoopStatus.COMPLETE.value
    assert _edit_in(wt), "the budget's end removed the worktree holding the approved edit"
    assert worktree.branch_exists(str(repo), tid), "and its branch"
    assert _enabled(loop.id) == [], "a finished loop still had a nudge loop switched on"


def test_a_run_whose_cycles_ran_out_on_the_poll_keeps_it_too(repo):
    """The poll's own budget end: the stage worker's nudge loop fired its last cycle."""
    loop, tid, wt, state = _fanned_out(repo)
    loop_dir = loop_files.loop_dir(loop.id)
    (loop_dir / "findings" / "cycle_001.json").write_text('{"cycle": 1, "summary": "planned it"}')
    loop_files.write_credited_cycles(loop.id, 1)
    stage = SVC.get_by_session(session_key(loop.id))
    stage.cycle_count, stage.active = loop.max_cycles, False
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True

    _run(wd._poll_once())  # first sight: the finding is already credited
    _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status == LoopStatus.COMPLETE.value, after.error_message
    assert after.stop_reason == LoopStopReason.CYCLE_BUDGET.value
    assert _edit_in(wt) and worktree.branch_exists(str(repo), tid)


def test_a_finished_run_cleans_up_a_task_worktree_that_holds_nothing_unmerged(repo):
    loop, tid, wt, state = _fanned_out(repo)
    _git(wt, repo.parent, "checkout", "--", "CHANGELOG.md")  # the task made no change after all

    _run(W.LoopWatchdog(state, SVC)._complete(loop.id, reason="done-ness signal met"))

    assert not os.path.isdir(wt), "a worktree with nothing in it outlived its run"
    assert not worktree.branch_exists(str(repo), tid)


def test_a_finished_run_cleans_up_work_already_merged_by_hand(repo):
    loop, tid, wt, state = _fanned_out(repo)
    _git(wt, repo.parent, "commit", "-q", "-am", "the line")
    _git(repo, repo.parent, "merge", "-q", worktree.branch_name(tid))

    _run(W.LoopWatchdog(state, SVC)._complete(loop.id, reason="done-ness signal met"))

    assert not os.path.isdir(wt) and not worktree.branch_exists(str(repo), tid)
    assert "escaped once" in (repo / "CHANGELOG.md").read_text()


# ── a failed run switches its workers off ────────────────────────────────────


def test_a_failed_loop_has_no_enabled_nudge_rows(repo):
    """Two failed worker turns in a row fail the loop (`record_turn_outcome`, the gateway's
    turn-done callback). The failed loop's workers must not fire another cycle."""
    loop, tid, wt, state = _fanned_out(repo)
    stage = SVC.get_by_session(session_key(loop.id))
    _run(SVC.update(stage.id, active=True))  # its stage worker was taking turns too
    store.update_status(loop.id, LoopStatus.RUNNING)
    wd = W.LoopWatchdog(state, SVC)

    async def _two_failed_turns():
        wd.record_turn_outcome(loop.id, ok=False)
        wd.record_turn_outcome(loop.id, ok=False)
        await _settled()

    _run(_two_failed_turns())

    assert store.get(loop.id).status == LoopStatus.FAILED.value
    assert _enabled(loop.id) == [], "the failed loop's nudge loops stayed switched on"
    # Kept, switched off, for Resume — with the work in its worktree.
    worker = SVC.get_by_session(task_session_key(loop.id, tid))
    assert worker is not None and worker.active is False
    assert SVC.get_by_session(planner_session_key(loop.id)) is None
    assert _edit_in(wt) and worktree.branch_exists(str(repo), tid)


def test_resume_after_two_failed_turns_switches_the_workers_back_on(repo):
    loop, tid, wt, state = _fanned_out(repo)
    store.update_status(loop.id, LoopStatus.RUNNING)
    wd = W.LoopWatchdog(state, SVC)

    async def _two_failed_turns():
        wd.record_turn_outcome(loop.id, ok=False)
        wd.record_turn_outcome(loop.id, ok=False)
        await _settled()

    _run(_two_failed_turns())
    _run(manager.start(state, SVC, loop.id))

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert SVC.get_by_session(task_session_key(loop.id, tid)).active is True
    assert _edit_in(wt)


# ── every ending, the same way ───────────────────────────────────────────────


def _complete(state, loop):
    _run(W.LoopWatchdog(state, SVC)._complete(loop.id, reason="all stages complete"))


def _fail_stalled(state, loop):
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=1.0)
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())
    wd._last_activity[loop.id] = 1.0
    _run(wd._poll_once())
    assert store.get(loop.id).status == LoopStatus.FAILED.value


def _stop(state, loop):
    _run(manager.stop(state, SVC, loop.id))


def _delete(state, loop):
    _run(manager.teardown_for_delete(state, SVC, loop.id))


ENDINGS = {"complete": _complete, "failed": _fail_stalled, "stopped": _stop, "deleted": _delete}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_every_ending_switches_off_every_nudge_loop_and_stops_every_turn(repo, ending):
    loop, tid, _wt, state = _fanned_out(repo)
    stage = SVC.get_by_session(session_key(loop.id))
    _run(SVC.update(stage.id, active=True))
    task_key = task_session_key(loop.id, tid)
    planner = state.get_or_create_session(name=planner_session_key(loop.id))

    def _mid_turn():
        state._sessions[task_key].running = True
        planner.running = True

    if ending != "failed":  # a stall is a worker with no turn running
        _mid_turn()

    ENDINGS[ending](state, loop)

    assert _enabled(loop.id) == [], f"a {ending} loop kept a nudge loop switched on"
    if ending != "failed":
        assert f"dashboard:{task_key}" in state.sessions.stopped, "the task worker kept working"
        assert f"dashboard:{planner_session_key(loop.id)}" in state.sessions.stopped
    if ending == "deleted":
        assert _rows(loop.id) == [], "a deleted loop left nudge rows behind"


def test_a_stopped_run_keeps_the_work_nobody_merged(repo):
    loop, tid, wt, state = _fanned_out(repo)

    _run(manager.stop(state, SVC, loop.id))

    assert store.get(loop.id).status == LoopStatus.STOPPED.value
    assert _edit_in(wt) and worktree.branch_exists(str(repo), tid)


# ── a loop that ends while its worktree is cut ───────────────────────────────


#: How each ending lands on the event loop while git cuts a task's worktree in a worker thread.
ENDS_MID_CUT = {
    "stopped": lambda state, loop_id: manager.stop(state, SVC, loop_id),
    "paused": lambda state, loop_id: manager.pause(state, SVC, loop_id),
    "deleted": lambda state, loop_id: manager.teardown_for_delete(state, SVC, loop_id),
}


@_GIT_LOOP_CAP
@pytest.mark.parametrize("ending", list(ENDS_MID_CUT))
def test_a_loop_that_ends_while_its_worktree_is_cut_starts_no_worker_for_it(
    repo, monkeypatch, request, hold, ending  # noqa: F811 - the imported fixture
):
    """Git cuts a task's worktree in a worker thread, so a Stop, a Pause or a delete can land
    while it does. The loop then starts no worker for that task, and a loop that cannot resume
    keeps no fresh checkout: a paused one keeps it, since its Resume takes the worktree back.
    The ending's own git waits for the cut (``manager.git_lock``): none of it runs on a worktree
    git is still making."""
    loop, state = _started(repo)
    # Held at its checkout, after `worktree add` registered the worktree and before it is filled.
    seen = _held(monkeypatch, "_git", *hold, when=lambda *args, **_kw: args[1:] == ("checkout",))
    ctx = _Ctx(state, SVC)
    # The ending got as far as it can while the cut runs: it waits on the loop's git lock, it
    # reached its own git, or it is over.
    as_far = threading.Event()
    request.addfinalizer(as_far.set)
    during: list[str] = []
    for name in ("sweep_finished", "discard", "commit_pending"):
        real = getattr(worktree, name)

        def watched(*args, _real=real, _name=name, **kw):
            if not seen.returned:
                during.append(_name)
            if _name != "commit_pending":
                as_far.set()
            return _real(*args, **kw)

        monkeypatch.setattr(worktree, name, watched)
    _lock_waits(monkeypatch, as_far)

    async def end_it():
        queued = list(store.get(loop.id).kind_config["queued_task_ids"])
        over = asyncio.ensure_future(ENDS_MID_CUT[ending](state, loop.id))
        over.add_done_callback(lambda _done: as_far.set())
        if not await asyncio.to_thread(as_far.wait, _CAP):
            raise AssertionError(f"the {ending} never got as far as it can while the cut runs")
        return queued, over

    async def a_pass():
        return await asyncio.gather(
            kinds.get("code").schedule(store.get(loop.id), ctx), _when(*hold, end_it)
        )

    stopped, ((tid,), over) = _run(a_pass())
    _run(over)

    assert seen.on_loop is False
    assert during == [], f"the {ending}'s git ran on a worktree git was still making: {during}"
    assert stopped is True, "the pass did not say the loop stopped running"
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is None, "a worker started"
    assert "task_started" not in [e for e, _ in ctx.events]
    from personalclaw.tasks import registry

    task = _run(registry.get_task(tid, provider_name="native"))
    assert (
        task is None or getattr(task.status, "value", task.status) != "in_progress"
    ), "starting its worker marked the task in progress"
    wt = worktree.worktree_path(str(repo), tid, loop.tasks_project_id)
    if ending == "paused":
        assert os.path.isdir(wt), "a paused loop lost the worktree its Resume takes back"
    else:
        assert not os.path.isdir(wt), f"a {ending} loop kept a fresh checkout"
        assert not worktree.branch_exists(str(repo), tid)


@_GIT_LOOP_CAP
@pytest.mark.parametrize("tree", ["clean", "dirty"])
@pytest.mark.parametrize("ending", list(ENDS_MID_CUT))
def test_a_loop_that_ends_while_git_checks_its_tree_cuts_no_worktree(
    repo, monkeypatch, hold, ending, tree  # noqa: F811 - the imported fixture
):
    """An ending that lands while git checks the tree, before any worktree is cut: the pass cuts
    none, starts no worker and says the loop stopped running, whatever git found. A dirty tree,
    which keeps a stage's tasks with its worker, is what git finds most often."""
    loop, state = _started(repo)
    if tree == "dirty":
        with open(os.path.join(repo, "CHANGELOG.md"), "a", encoding="utf-8") as fh:
            fh.write(LINE)
    seen = _held(monkeypatch, "has_uncommitted_changes", *hold)
    cuts = []
    monkeypatch.setattr(worktree, "add_worktrees", lambda *args, **kw: cuts.append(args) or {})
    ctx = _Ctx(state, SVC)

    async def a_pass():
        return await asyncio.gather(
            kinds.get("code").schedule(store.get(loop.id), ctx),
            _when(*hold, lambda: ENDS_MID_CUT[ending](state, loop.id)),
        )

    stopped, _ended = _run(a_pass())

    assert seen.on_loop is False
    assert stopped is True, "the pass did not say the loop stopped running"
    assert cuts == [], f"a {ending} loop had a worktree cut"
    assert "task_started" not in [e for e, _ in ctx.events]


@_GIT_LOOP_CAP
@pytest.mark.parametrize("ending", ["stopped", "deleted"])
def test_an_ending_still_under_way_when_the_cut_returns_starts_no_worker(
    repo, monkeypatch, hold, ending  # noqa: F811 - the imported fixture
):
    """A Stop or a delete is still ending its run when the cut returns, its children not yet
    told: the pass starts no worker for a loop that is going, and keeps no fresh checkout."""
    from personalclaw.loop import children

    loop, state = _started(repo)
    seen = _held(monkeypatch, "add_worktrees", *hold)
    reached, release = asyncio.Event(), asyncio.Event()

    async def end_children(*_args, **_kw):
        reached.set()
        await release.wait()
        return 0

    monkeypatch.setattr(children, "end_children", end_children)
    ctx = _Ctx(state, SVC)

    async def start_the_ending():
        queued = list(store.get(loop.id).kind_config["queued_task_ids"])
        over = asyncio.ensure_future(ENDS_MID_CUT[ending](state, loop.id))
        await reached.wait()
        return queued, over

    async def a_pass():
        return await asyncio.gather(
            kinds.get("code").schedule(store.get(loop.id), ctx), _when(*hold, start_the_ending)
        )

    stopped, ((tid,), over) = _run(a_pass())

    try:
        assert seen.on_loop is False
        assert stopped is True, "the pass did not say the loop stopped running"
        assert SVC.get_by_session(task_session_key(loop.id, tid)) is None, "a worker started"
        assert "task_started" not in [e for e, _ in ctx.events]
        wt = worktree.worktree_path(str(repo), tid, loop.tasks_project_id)
        assert not os.path.isdir(wt), f"a {ending} loop kept a fresh checkout"
        assert not worktree.branch_exists(str(repo), tid)
    finally:
        release.set()
        _run(over)


@pytest.mark.parametrize("where", ["queued", "reaped"])
@pytest.mark.parametrize("ending", ["paused", "stopped"])
def test_a_loop_paused_or_stopped_while_the_pass_reads_its_tasks_starts_no_worker(
    repo, monkeypatch, ending, where
):
    """A pass reads the stage's tasks (autopilot's read), and each queued task it reaps, in a
    worker thread, so a Pause or a Stop can land meanwhile. The pass then compares with the loop
    it began with: it starts no worker and says the loop stopped running."""
    from personalclaw.loop import children
    from personalclaw.tasks import registry

    loop, state = _started(repo)
    reached, release = asyncio.Event(), asyncio.Event()

    async def end_children(*_args, **_kw):
        reached.set()
        await release.wait()
        return 0

    monkeypatch.setattr(children, "end_children", end_children)
    over: list[asyncio.Future] = []

    async def end_it():
        if ending == "paused":
            await manager.pause(state, SVC, loop.id)
        else:
            over.append(asyncio.ensure_future(manager.stop(state, SVC, loop.id)))
            await reached.wait()

    code = kinds.get("code")
    landed: list[str] = []
    if where == "queued":
        collect = registry.collect_tasks

        async def ended_first(*args, **kw):
            if not landed:
                landed.append(where)
                await end_it()
            return await collect(*args, **kw)

        monkeypatch.setattr(registry, "collect_tasks", ended_first)
    else:
        get_task = code._get_task

        async def ended_first(task_id):
            if not landed:
                landed.append(where)
                await end_it()
            return await get_task(task_id)

        monkeypatch.setattr(code, "_get_task", ended_first)
    ctx = _Ctx(state, SVC)

    try:
        stopped = _run(code.schedule(store.get(loop.id), ctx))
        (tid,) = store.get(loop.id).kind_config["queued_task_ids"]
        assert landed == [where], f"the {ending} never landed in the pass's {where} read"
        assert stopped is True, "the pass did not say the loop stopped running"
        assert SVC.get_by_session(task_session_key(loop.id, tid)) is None, "a worker started"
        assert "task_started" not in [e for e, _ in ctx.events]
    finally:
        release.set()
        for ending_task in over:
            _run(ending_task)


def test_the_pass_sweeps_a_fresh_checkout_only_once_the_endings_git_is_done(repo, monkeypatch):
    """A pass that finds its loop ended sweeps the checkouts it just cut, and an ending's settle
    may be at its git on those worktrees meanwhile: the sweep waits for it (``manager.git_lock``).
    """
    loop, _state = _started(repo)
    ids = list(_run(tasks_link.task_titles(loop)))
    lock = manager.git_lock(loop.id)
    reached = _lock_waits(monkeypatch)
    order: list[str] = []
    real = worktree.sweep_finished

    def swept(*args, **kw):
        order.append("the pass swept")
        reached.set()
        return real(*args, **kw)

    monkeypatch.setattr(worktree, "sweep_finished", swept)
    store.update_status(loop.id, LoopStatus.STOPPED)

    async def beside_the_endings_git():
        async with lock:  # the ending's settle, at its git
            sweep = asyncio.ensure_future(
                kinds.get("code")._keep_or_sweep(store.get(loop.id), str(repo), ids)
            )
            if not await asyncio.to_thread(reached.wait, _CAP):
                raise AssertionError("the pass's sweep neither waited on the lock nor swept")
            order.append("the ending's git let go")
        await sweep

    _run(beside_the_endings_git())

    assert order == [
        "the ending's git let go",
        "the pass swept",
    ], "the pass swept beside the ending's git"


def test_the_pass_merges_a_task_only_once_the_endings_git_is_done(repo, monkeypatch):
    """A merge runs git on a task's worktree and branch, and an ending's settle may be at its git
    on them meanwhile: the merge waits for it (``manager.git_lock``)."""
    from personalclaw.tasks import registry

    loop, tid, _wt, state = _fanned_out(repo)
    task = _run(registry.get_task(tid, provider_name="native"))
    lock = manager.git_lock(loop.id)
    reached = _lock_waits(monkeypatch)
    order: list[str] = []
    real = worktree.merge_worktree

    def merged(*args, **kw):
        order.append("the pass merged")
        reached.set()
        return real(*args, **kw)

    monkeypatch.setattr(worktree, "merge_worktree", merged)
    ctx = _Ctx(state, SVC)

    async def beside_the_endings_git():
        async with lock:  # the ending's settle, at its git
            merge = asyncio.ensure_future(
                kinds.get("code")._reap_merge_done(
                    store.get(loop.id), tid, task, str(repo), ctx, by="the loop"
                )
            )
            if not await asyncio.to_thread(reached.wait, _CAP):
                raise AssertionError("the merge neither waited on the lock nor ran git")
            order.append("the ending's git let go")
        await merge

    _run(beside_the_endings_git())

    assert order == [
        "the ending's git let go",
        "the pass merged",
    ], "the pass merged beside the ending's git"


def test_the_review_commits_a_task_only_once_the_endings_git_is_done(repo, monkeypatch):
    """An Attended loop's review commits a task's work in its worktree, and removes the worktree
    when the branch brings nothing new, and an ending's settle may be at its git on them
    meanwhile: the review waits for it (``manager.git_lock``)."""
    from personalclaw.tasks import registry

    loop, tid, _wt, state = _fanned_out(repo)
    task = _run(registry.get_task(tid, provider_name="native"))
    lock = manager.git_lock(loop.id)
    reached = _lock_waits(monkeypatch)
    order: list[str] = []
    real = worktree.commit_pending

    def committed(*args, **kw):
        order.append("the review committed")
        reached.set()
        return real(*args, **kw)

    monkeypatch.setattr(worktree, "commit_pending", committed)
    ctx = _Ctx(state, SVC)

    async def beside_the_endings_git():
        async with lock:  # the ending's settle, at its git
            land = asyncio.ensure_future(
                kinds.get("code")._land(store.get(loop.id), tid, task, str(repo), ctx)
            )
            if not await asyncio.to_thread(reached.wait, _CAP):
                raise AssertionError("the review neither waited on the lock nor ran git")
            order.append("the ending's git let go")
        await land

    _run(beside_the_endings_git())

    assert order == [
        "the ending's git let go",
        "the review committed",
    ], "the review committed beside the ending's git"


def test_a_review_that_waited_on_an_endings_settle_runs_no_git(repo, monkeypatch):
    """A Stop lands while the reap awaits (its task read, its branch check, the worker's
    teardown), before the review takes the lock, and its settle takes the lock first. The review
    waits for it, then reads the loop again with the lock held: the loop ended, so it runs no git
    on what the settle kept or removed, and the pass says the loop stopped."""
    from personalclaw.tasks import registry

    loop, tid, _wt, state = _fanned_out(repo)
    task = _run(registry.get_task(tid, provider_name="native"))
    running = store.get(loop.id)
    lock = manager.git_lock(loop.id)
    reached = _lock_waits(monkeypatch)
    order: list[str] = []
    real = worktree.commit_pending

    def committed(*args, **kw):
        order.append("the review committed")
        reached.set()
        return real(*args, **kw)

    monkeypatch.setattr(worktree, "commit_pending", committed)
    ctx = _Ctx(state, SVC)

    async def the_stop_first():
        store.update_status(loop.id, LoopStatus.STOPPED)  # a Stop sets its status first
        async with lock:  # then its settle, at its git
            land = asyncio.ensure_future(
                kinds.get("code")._land(running, tid, task, str(repo), ctx)
            )
            if not await asyncio.to_thread(reached.wait, _CAP):
                raise AssertionError("the review neither waited on the lock nor ran git")
            order.append("the ending's git let go")
        return await land

    stopped = _run(the_stop_first())

    assert stopped is True, "the pass did not say the loop stopped running"
    assert order == ["the ending's git let go"], f"the review ran git after the settle: {order}"


# ── a loop that cannot be resumed has nothing queued ─────────────────────────


@pytest.mark.parametrize("ending", ["complete", "stopped"])
def test_a_finished_or_stopped_loop_has_nothing_queued(repo, ending):
    """The queue is what the scheduler runs next, and nothing of a loop that cannot be resumed runs
    again. A stopped loop kept its task ids, so its page counted "1 queued" beside a task waiting
    for a stage that would never start."""
    loop, tid, _wt, state = _fanned_out(repo)
    assert store.get(loop.id).kind_config["queued_task_ids"] == [tid]

    ENDINGS[ending](state, loop)

    assert store.get(loop.id).kind_config["queued_task_ids"] == []


def test_a_failed_loop_keeps_its_queue_for_resume(repo):
    loop, tid, _wt, state = _fanned_out(repo)

    _fail_stalled(state, loop)

    assert store.get(loop.id).kind_config["queued_task_ids"] == [tid]


@_GIT_LOOP_CAP
def test_at_boot_a_loop_that_ended_with_tasks_queued_has_none(repo):
    """One that ended before its ending emptied the queue: the boot sweep empties it, and leaves a
    failed loop's for its Resume."""
    stopped, tid, _wt, state = _fanned_out(repo)
    store.update_status(stopped.id, LoopStatus.STOPPED)  # its ending never ran
    failed = store.create(Loop(id="", name="feed", kind="code", task="tidy the feed"))
    store.queue_tasks(failed.id, ["t-feed"])
    store.update_status(failed.id, LoopStatus.RUNNING)
    store.update_status(failed.id, LoopStatus.FAILED)

    _run(W.LoopWatchdog(state, SVC)._boot_sweep())

    assert store.get(stopped.id).kind_config["queued_task_ids"] == []
    assert store.get(failed.id).kind_config["queued_task_ids"] == ["t-feed"]


@_GIT_LOOP_CAP
def test_only_a_delete_discards_the_work_and_only_its_own(repo, tmp_path):
    from personalclaw import projects

    shared = projects._store().create_project("Newsfold").id
    mine, my_tid, my_wt, state = _fanned_out(repo, project_id=shared)
    other_repo = _repo(tmp_path, "newsfold-site")
    theirs, their_tid, their_wt, _ = _fanned_out(
        other_repo, state=state, project_id=shared, name="site digest"
    )
    assert os.path.dirname(my_wt) == os.path.dirname(their_wt), "the two share one worktree root"

    _run(manager.stop(state, SVC, mine.id))
    assert _edit_in(their_wt), "one loop's Stop removed another loop's worktree"
    _run(manager.teardown_for_delete(state, SVC, mine.id))

    assert not os.path.isdir(my_wt) and not worktree.branch_exists(str(repo), my_tid)
    assert _edit_in(their_wt), "one loop's delete removed another loop's worktree"
    assert worktree.branch_exists(str(other_repo), their_tid)


# ── the boot sweep finishes what an ending a restart cut short left ──────────


def test_at_boot_an_ended_loop_has_no_nudge_loop_switched_on(repo):
    failed, failed_tid, _wt, state = _fanned_out(repo)
    store.update_status(failed.id, LoopStatus.RUNNING)
    store.update_status(failed.id, LoopStatus.FAILED)  # its ending never ran
    finished = store.create(Loop(id="", name="notes", kind="general", task="tidy the notes"))
    store.update_status(finished.id, LoopStatus.RUNNING)
    store.update_status(finished.id, LoopStatus.COMPLETE)
    _run(SVC.add(session_name=session_key(finished.id)))

    _run(W.LoopWatchdog(state, SVC)._boot_sweep())

    assert _enabled(failed.id) == [] and _enabled(finished.id) == []
    assert (
        SVC.get_by_session(task_session_key(failed.id, failed_tid)) is not None
    ), "a failed loop's worker is kept for its Resume"
    assert SVC.get_by_session(session_key(finished.id)) is None


# ── the loop's page names what was kept, and its owner decides ───────────────


def _app(state):
    app = web.Application()
    app["state"] = state
    return app


def _call(handler, state, method, path, body=None, **match_info):
    import json

    if body is None and handler is H.api_loop_kept_work_merge:
        # What the page's Merge sends: the commit its review showed.
        loop = store.get(match_info["id"])
        tip = worktree.branch_tip(loop.workspace_dir, match_info["task_id"]) if loop else ""
        body = {"confirm": True, "tip": tip}
    req = make_mocked_request(method, path, match_info=match_info, app=_app(state))
    req["user"] = "owner"  # yours, as the auth middleware admits it: only you answer a loop
    if body is not None:

        async def _json():
            return body

        req.json = _json
    resp = _run(handler(req))
    return resp.status, json.loads(resp.body)


def _budget_spent(repo):
    loop, tid, wt, state = _fanned_out(repo)
    wd = W.LoopWatchdog(state, SVC)
    why = LoopStopReason.CYCLE_BUDGET
    _run(wd._complete(loop.id, reason="cycle budget reached", genuine=False, stop_reason=why))
    return loop, tid, wt, state


def test_the_end_screen_names_the_kept_work_what_it_holds_and_where(repo):
    loop, tid, wt, state = _budget_spent(repo)

    status, body = _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)

    assert status == 200
    assert body["resumable"] is False
    (kept,) = body["kept"]
    assert kept["task_id"] == tid and kept["title"] == "CHANGELOG.md"
    assert kept["branch"] == worktree.branch_name(tid)
    assert kept["path"] == wt
    # Committed on its own branch as git is configured to commit there, so its review is whole.
    assert kept["changed"] == 0 and kept["commits"] == 1
    assert kept["tip"] == worktree.branch_tip(str(repo), tid)
    assert [c.split(" ", 1)[1] for c in kept["log"]] == [f"task {tid}: work"]
    assert "+- Digest titles are escaped once. (#21)" in kept["diff"]
    assert body["into"] and body["cut"] is False


def test_a_loop_that_does_not_exist_has_no_kept_work_to_list():
    state = _State()
    status, body = _call(H.api_loop_kept_work, state, "GET", "/", id="5e1d0c47")
    assert (status, body["error"]["code"]) == (404, "not_found")
    status, body = _call(H.api_loop_kept_work, state, "GET", "/", id="../x")
    assert status == 400


def test_a_loop_at_work_names_no_kept_work_and_refuses_to_merge_it(repo):
    loop, tid, wt, state = _fanned_out(repo)

    assert _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)[1]["kept"] == []
    for handler, method in (
        (H.api_loop_kept_work_merge, "POST"),
        (H.api_loop_kept_work_discard, "DELETE"),
    ):
        status, body = _call(handler, state, method, "/", id=loop.id, task_id=tid)
        assert (status, body["error"]["code"]) == (409, "loop_still_at_work")
    assert _edit_in(wt)


@_GIT_LOOP_CAP
def test_merge_puts_the_kept_work_in_the_workspace(repo):
    loop, tid, wt, state = _budget_spent(repo)

    status, body = _call(H.api_loop_kept_work_merge, state, "POST", "/", id=loop.id, task_id=tid)

    assert (status, body["kept"]) == (200, [])
    assert LINE in (repo / "CHANGELOG.md").read_text()
    assert _git(repo, repo.parent, "status", "--porcelain").stdout == "", "the merge was committed"
    assert not os.path.isdir(wt) and not worktree.branch_exists(str(repo), tid)
    # The loop's page lists it with the merges its scheduler made, as hers.
    (merged,) = loop_files.get_merges(loop.id)
    assert (merged["task_id"], merged["by"]) == (tid, "you")


# ── kept work merges by the rules every merge of a loop's follows ─────────────


def test_kept_work_merges_only_at_the_commit_she_reviewed(repo):
    loop, tid, wt, state = _budget_spent(repo)
    before = (repo / "CHANGELOG.md").read_text()

    status, body = _call(
        H.api_loop_kept_work_merge, state, "POST", "/", {"confirm": True, "tip": "0" * 40},
        id=loop.id, task_id=tid,
    )  # fmt: skip

    assert (status, body["error"]["code"]) == (409, "loop_merge_moved")
    assert (repo / "CHANGELOG.md").read_text() == before and worktree.branch_exists(str(repo), tid)


def test_an_edit_made_after_the_review_is_read_again_not_merged_unseen(repo):
    loop, tid, wt, state = _budget_spent(repo)
    reviewed = worktree.branch_tip(str(repo), tid)
    with open(os.path.join(wt, "NOTES.md"), "w", encoding="utf-8") as fh:
        fh.write("her own note, after she read the review\n")

    status, body = _call(
        H.api_loop_kept_work_merge, state, "POST", "/", {"confirm": True, "tip": reviewed},
        id=loop.id, task_id=tid,
    )  # fmt: skip

    assert (status, body["error"]["code"]) == (409, "loop_merge_moved")
    assert not (repo / "NOTES.md").exists()
    (kept,) = _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)[1]["kept"]
    assert "NOTES.md" in kept["stat"], "the review now shows what she added"


def test_with_no_git_identity_kept_work_is_not_merged(repo):
    loop, tid, wt, state = _budget_spent(repo)
    _git(repo, repo.parent, "config", "--unset", "user.name")
    before = (repo / "CHANGELOG.md").read_text()

    status, body = _call(H.api_loop_kept_work_merge, state, "POST", "/", id=loop.id, task_id=tid)

    assert (status, body["error"]["code"]) == (409, "kept_work_no_identity")
    assert (repo / "CHANGELOG.md").read_text() == before and worktree.branch_exists(str(repo), tid)


def test_work_committed_under_another_name_is_not_merged(repo):
    loop, tid, wt, state = _budget_spent(repo)
    _git(wt, repo.parent, "-c", "user.name=Someone Else", "-c", "user.email=else@example.com",
         "commit", "-q", "--allow-empty", "-m", "made as someone else")  # fmt: skip

    status, body = _call(H.api_loop_kept_work_merge, state, "POST", "/", id=loop.id, task_id=tid)

    assert (status, body["error"]["code"]) == (409, "kept_work_other_name")
    assert any("Someone Else" in c for c in body["error"]["detail"]["commits"])
    assert LINE not in (repo / "CHANGELOG.md").read_text()


def test_a_stop_while_finished_work_waits_for_her_merge_keeps_it_for_review(repo):
    """An Attended loop paused on finished work waiting for her merge, and she stopped it: the
    work is kept, its merge question is gone, and the end screen reviews it the same way."""
    from personalclaw.tasks import registry

    loop, tid, wt, state = _fanned_out(repo)
    _run(registry.update_task(tid, provider_name="native", status="done"))
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))
    waiting = store.get(loop.id)
    assert waiting.status == LoopStatus.NEEDS_INPUT.value
    assert loop_files.pending_question(loop.id)["merge"]["tasks"][0]["task_id"] == tid

    _run(manager.stop(state, SVC, loop.id))

    assert loop_files.pending_question(loop.id) is None
    (kept,) = _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)[1]["kept"]
    assert kept["task_id"] == tid and kept["tip"] == worktree.branch_tip(str(repo), tid)
    assert "+- Digest titles are escaped once. (#21)" in kept["diff"]


def test_merge_is_asked_for_in_so_many_words(repo):
    """It writes a commit into her repository, so a request that does not say it means to (as the
    page's Merge into workspace does) merges nothing."""
    loop, tid, wt, state = _budget_spent(repo)
    before = (repo / "CHANGELOG.md").read_text()

    for body in ({}, {"confirm": "true"}, {"confirm": 1}):
        status, answer = _call(
            H.api_loop_kept_work_merge, state, "POST", "/", body, id=loop.id, task_id=tid
        )
        assert (status, answer["error"]["code"]) == (400, "confirm_required")
    assert (repo / "CHANGELOG.md").read_text() == before and _edit_in(wt)


def test_merge_waits_for_a_workspace_with_nothing_uncommitted(repo):
    loop, tid, wt, state = _budget_spent(repo)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\nher own edit\n")

    status, body = _call(H.api_loop_kept_work_merge, state, "POST", "/", id=loop.id, task_id=tid)

    assert (status, body["error"]["code"]) == (409, "workspace_not_committed")
    assert _edit_in(wt) and "her own edit" in (repo / "CHANGELOG.md").read_text()


@pytest.mark.timeout(60)
def test_a_merge_that_conflicts_is_undone_and_keeps_the_work(repo):
    loop, tid, wt, state = _budget_spent(repo)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### Fixed\n- Other.\n")
    _git(repo, repo.parent, "commit", "-q", "-am", "her own line")

    status, body = _call(H.api_loop_kept_work_merge, state, "POST", "/", id=loop.id, task_id=tid)

    assert (status, body["error"]["code"]) == (409, "kept_work_conflicts")
    assert body["error"]["detail"]["conflicts"] == ["CHANGELOG.md"]
    assert _git(repo, repo.parent, "status", "--porcelain").stdout == "", "the merge was undone"
    assert worktree.branch_exists(str(repo), tid)


def test_discard_is_the_owners_and_removes_only_that_work(repo):
    loop, tid, wt, state = _budget_spent(repo)
    before = (repo / "CHANGELOG.md").read_text()

    assert (
        _call(H.api_loop_kept_work_discard, state, "DELETE", "/", id=loop.id, task_id="t-nope")[0]
        == 404
    )
    status, body = _call(
        H.api_loop_kept_work_discard, state, "DELETE", "/", id=loop.id, task_id=tid
    )

    assert (status, body["kept"]) == (200, [])
    assert not os.path.isdir(wt) and not worktree.branch_exists(str(repo), tid)
    assert (repo / "CHANGELOG.md").read_text() == before


def test_a_failed_loops_kept_work_says_resume_carries_it_on(repo):
    loop, tid, wt, state = _fanned_out(repo)
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=1.0)
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())
    wd._last_activity[loop.id] = 1.0
    _run(wd._poll_once())
    assert store.get(loop.id).status == LoopStatus.FAILED.value

    status, body = _call(H.api_loop_kept_work, state, "GET", "/", id=loop.id)
    assert status == 200 and body["resumable"] is True
    assert [k["task_id"] for k in body["kept"]] == [tid]

    # Discarding it lets Resume start the task afresh, not in a worktree that is gone.
    _call(H.api_loop_kept_work_discard, state, "DELETE", "/", id=loop.id, task_id=tid)
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is None


def test_resume_after_its_kept_work_is_discarded_starts_the_task_afresh(repo):
    """The finding the discarded attempt wrote is not the task's: Resume used to mark it done on
    that finding and then ask about "commits under another name" on a branch that was gone."""
    from personalclaw.tasks import registry

    loop, tid, wt, state = _fanned_out(repo)
    findings = loop_files.loop_dir(loop.id) / "findings"
    findings.mkdir(exist_ok=True)
    (findings / f"task_{tid}_001.json").write_text(
        '{"cycle": 1, "stage": "implementation", "summary": "added the line, still checking it"}'
    )
    loop_files.record_cycle_findings(loop.id)
    loop_files.write_credited_cycles(loop.id, 1)
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=1.0)
    wd = W.LoopWatchdog(state, SVC)
    wd._swept = True
    _run(wd._poll_once())
    wd._last_activity[loop.id] = 1.0
    _run(wd._poll_once())
    assert store.get(loop.id).status == LoopStatus.FAILED.value
    _call(H.api_loop_kept_work_discard, state, "DELETE", "/", id=loop.id, task_id=tid)

    _run(manager.start(state, SVC, loop.id))
    _run(kinds.get("code").schedule(store.get(loop.id), _Ctx(state, SVC)))

    after = store.get(loop.id)
    assert after.status == LoopStatus.RUNNING.value, loop_files.pending_question(loop.id)
    assert SVC.get_by_session(task_session_key(loop.id, tid)) is not None, "nothing ran the task"
    task = _run(registry.get_task(tid, provider_name="native"))
    assert getattr(task.status, "value", task.status) == "in_progress"
    assert os.path.isdir(wt) and not _edit_in(wt), "a fresh worktree, without the discarded edit"
