"""Unified Loop manager (Slice 2b.ii.b) — the shared lifecycle that arms/pauses/
stops/nudges/reaps every kind, delegating brief+nudge to the kind strategy."""

from __future__ import annotations

import asyncio
import os

import pytest

from personalclaw.loop import manager, store
from personalclaw.loop.loop import Loop, LoopStatus


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    # manager.start → tasks_link.provision writes a Tasks Project + lists via the
    # Tasks hierarchy store (which projects.py also backs). Isolate it per-test too,
    # else every test that starts a loop leaks a Project into the shared/real config
    # dir — across a serial run the auto-named projects pile up and collide
    # ('G (142)' already exists). Was previously only wired on TestTaskWorker.
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat
    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _FakeSession:
    def __init__(self, key):
        self.key = key
        self._trust = False
        self._running = False
        self.acp_provider = ""; self.acp_provider_agent = ""; self.reasoning_effort = ""; self.acp_mode = ""

    @property
    def running(self):
        return self._running


class _FakeState:
    def __init__(self):
        self._sessions = {}

    def get_or_create_session(self, *, name, agent, model, workspace_dir, app, project_id=""):
        s = self._sessions.get(name) or _FakeSession(name)
        s.agent = agent; s.workspace_dir = workspace_dir; s.app = app
        s.project_id = project_id  # S5: worker artifacts scope to the loop's Project
        self._sessions[name] = s
        return s

    def push_sessions_update(self):
        pass


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active = lid, session_name, True


class _FakeSvc:
    def __init__(self):
        self._loops = {}; self._n = 0

    async def add(self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, first_idle_secs=0):
        # Model real autonudge: ONE loop per session — adding replaces any existing
        # loop on the same session (else get_by_session returns a stale earlier one).
        for lid in [lid for lid, lp in self._loops.items() if lp.session_name == session_name]:
            del self._loops[lid]
        self._n += 1
        lp = _FakeNudge(f"N{self._n}", session_name)
        lp.message = message
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    async def update(self, loop_id, **kw):
        lp = self._loops.get(loop_id)
        if lp:
            for k, v in kw.items():
                setattr(lp, k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _goal(**over):
    base = dict(id="", name="G", kind="goal", task="investigate the latency regression",
                kind_config={"goal_type": "open_ended"})
    base.update(over)
    return store.create(Loop(**base))


class TestStartArmsWorker:
    def test_start_writes_brief_arms_session_and_nudge(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        out = _run(manager.start(state, svc, g.id))
        assert out.status == "running"
        # brief written + status mirrored
        d = store.safe_loop_dir(g.id)
        assert (d / "brief.md").exists() and "# Goal Loop Brief" in (d / "brief.md").read_text()
        # session armed with trust + the kind's default agent + recorded session_key
        sess = state._sessions[manager.session_key(g.id)]
        assert sess._trust is True and sess.agent == "personalclaw-loop"
        assert store.get(g.id).session_key == manager.session_key(g.id)
        # autonudge armed with the goal cycle nudge (unattended → autonomous-framed)
        nl = svc.get_by_session(manager.session_key(g.id))
        assert nl is not None and "findings/cycle_NNN.json" in nl.message

    def test_code_kind_uses_coder_agent(self):
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login to the app",
                              kind_config={"entry_stage": "design"}))
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert state._sessions[manager.session_key(c.id)].agent == "personalclaw-coder"

    def test_goal_start_does_not_provision_tasks_project(self):
        # goal is NOT task-driven — starting it must NOT auto-create a Tasks Project
        # (legacy goal behavior: sub-goals become Tasks only via explicit decompose).
        g = _goal(plan=[{"title": "sub-goal one"}, {"title": "sub-goal two"}])
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        assert store.get(g.id).tasks_project_id == ""  # no Project provisioned

    def test_code_start_provisions_tasks_project(self):
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login to the app",
                              plan=[{"stage": "implementation", "title": "Impl"}],
                              kind_config={"entry_stage": "implementation"}))
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert store.get(c.id).tasks_project_id != ""  # task-driven → Project provisioned

    def test_rearm_nudge_message_refreshes_to_current_stage(self):
        # When a code stage advances, the live worker's autonudge message must be
        # rebuilt from the loop's CURRENT state — not stay stale on the old stage.
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login here",
                              plan=[{"stage": "design", "title": "D", "objective": "design it"},
                                    {"stage": "implementation", "title": "I", "objective": "build it"}],
                              phase_status={"design": "active"}))
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        nl = svc.get_by_session(manager.session_key(c.id))
        assert "stage 1/2" in nl.message  # armed on the design stage
        # advance the stage on disk, then re-arm
        store.set_phase_status(c.id, "design", "done")
        store.set_phase_status(c.id, "implementation", "active")
        _run(manager.rearm_nudge_message(svc, c.id))
        assert "stage 2/2" in svc.get_by_session(manager.session_key(c.id)).message

    def test_rearm_nudge_message_noop_without_live_worker(self):
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login here",
                              kind_config={"entry_stage": "design"}))
        # no worker armed → no-op, never raises
        _run(manager.rearm_nudge_message(_FakeSvc(), c.id))

    def test_design_start_does_not_provision_empty_project(self):
        # design has no plan yet (its step walkthrough is the deferred Design slice), so
        # provisioning would spawn a Tasks Project with ZERO lists — empty clutter. It
        # free-runs off its brief until the slice lands (provisions_tasks=False).
        d = store.create(Loop(id="", name="D", kind="design",
                              task="Build a design system for the marketing site"))
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, d.id))
        assert store.get(d.id).tasks_project_id == ""  # no empty Project spawned


class TestPauseStopResume:
    def test_pause_then_resume(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.pause(state, svc, g.id))
        assert store.get(g.id).status == "paused"
        _run(manager.start(state, svc, g.id))  # resume
        assert store.get(g.id).status == "running"

    def test_resume_rearms_with_current_stage_not_original(self):
        # A code loop paused on stage 1, then advanced to stage 2 (e.g. via the
        # cockpit) before resume, must re-arm targeting the CURRENT stage — start()
        # rebuilds the nudge from current state, not a captured-at-original message.
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login here",
                              plan=[{"stage": "design", "title": "D", "objective": "design it"},
                                    {"stage": "implementation", "title": "I", "objective": "build it"}],
                              phase_status={"design": "active"}))
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, c.id))
        assert "stage 1/2" in svc.get_by_session(manager.session_key(c.id)).message
        _run(manager.pause(state, svc, c.id))
        store.set_phase_status(c.id, "design", "done")
        store.set_phase_status(c.id, "implementation", "active")
        _run(manager.start(state, svc, c.id))  # resume
        assert "stage 2/2" in svc.get_by_session(manager.session_key(c.id)).message

    def test_pause_deactivates_main_and_task_workers(self):
        # A parallel loop's task-workers must also be deactivated on pause (not left
        # burning cycles). Deactivate, not remove — so resume can re-arm them.
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login here", kind_config={}))
        state, svc = _FakeState(), _FakeSvc()
        _run(svc.add(session_name=manager.session_key(c.id), message="", idle_secs=1, max_cycles=1, stop_sentinel_path=""))
        _run(svc.add(session_name=manager.task_session_key(c.id, "t-1"), message="", idle_secs=1, max_cycles=1, stop_sentinel_path=""))
        _run(manager.pause(state, svc, c.id))
        main = svc.get_by_session(manager.session_key(c.id))
        worker = svc.get_by_session(manager.task_session_key(c.id, "t-1"))
        assert main is not None and main.active is False        # still registered, deactivated
        assert worker is not None and worker.active is False     # task-worker too

    def test_stop_is_terminal_and_drops_sentinel(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.stop(state, svc, g.id))
        assert store.get(g.id).status == "stopped"
        assert manager.session_key(g.id) not in {l.session_name for l in svc._loops.values()}
        assert store.stop_sentinel_path(g.id).exists()


class TestNudge:
    def test_nudge_writes_guidance_and_appends_history(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        _run(manager.nudge(state, svc, g.id, "focus on the db path"))
        assert store.read_guidance(g.id) == "focus on the db path"
        assert store.get_nudges(g.id)[0]["text"] == "focus on the db path"

    def test_nudge_on_needs_input_resumes(self):
        g = _goal()
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.start(state, svc, g.id))
        store.update_status(g.id, LoopStatus.NEEDS_INPUT)
        store.write_question(g.id, "which db?")
        _run(manager.nudge(state, svc, g.id, "use postgres"))
        assert store.get(g.id).status == "running"          # re-armed
        assert store.pending_question(g.id) is None          # question cleared

    def test_nudge_on_needs_input_brownfield_missing_workspace_stays_paused(self):
        # User typed an answer instead of re-picking a gone brownfield folder — the
        # re-arm must NOT resurrect an empty codebase; keep them on NEEDS_INPUT.
        c = store.create(Loop(id="", name="C", kind="code",
                              task="fix the auth bug in the existing repo",
                              kind_config={"project_kind": "brownfield"},
                              workspace_dir="/nonexistent/repo/path"))
        store.update_status(c.id, LoopStatus.NEEDS_INPUT)
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.nudge(state, svc, c.id, "just an answer, not a re-pick"))
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value
        assert svc.get_by_session(manager.session_key(c.id)) is None  # NOT re-armed
        assert "missing" in (store.pending_question(c.id) or {}).get("question", "").lower()


class TestTaskWorker:
    """Parallel task-worker session lifecycle (code/design): spawn arms a per-task
    worker keyed loop-<id>-<task_id>; teardown removes it + clears its guidance.
    (Tasks storage is isolated by the autouse _tmp_config fixture.)"""

    def test_spawn_and_teardown_task_worker(self):
        from personalclaw.loop import tasks_link
        from types import SimpleNamespace
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login",
                              plan=[{"stage": "implementation", "title": "I"}],
                              kind_config={"entry_stage": "design"}))
        tasks_link.provision(c.id)
        ids = _run(tasks_link.decompose_phase(c.id, "implementation", [{"title": "Build it"}]))
        tid = ids[0]
        task = SimpleNamespace(id=tid, title="Build it", description="", action_plan=[], exit_criteria=[])
        state, svc = _FakeState(), _FakeSvc()
        skey = _run(manager.spawn_task_worker(state, svc, store.get(c.id), task, "/ws/.wt/t"))
        assert skey == manager.task_session_key(c.id, tid) == f"loop-{c.id}-{tid}"
        assert svc.get_by_session(skey) is not None
        assert state._sessions[skey]._trust is True
        # steer reaches the per-task channel; teardown clears it + removes the loop
        store.write_task_guidance(c.id, tid, "prefer pure fns")
        _run(manager.teardown_task_worker(svc, c.id, tid))
        assert svc.get_by_session(skey) is None
        assert store.read_task_guidance(c.id, tid) == ""

    def test_teardown_reaps_task_workers_with_main(self):
        # _teardown removes the main worker AND any loop-<id>-* task-workers.
        c = store.create(Loop(id="", name="C", kind="code", task="t" * 12, kind_config={}))
        state, svc = _FakeState(), _FakeSvc()
        _run(svc.add(session_name=manager.session_key(c.id), message="", idle_secs=1, max_cycles=1, stop_sentinel_path=""))
        _run(svc.add(session_name=manager.task_session_key(c.id, "t-1"), message="", idle_secs=1, max_cycles=1, stop_sentinel_path=""))
        _run(manager.teardown_worker(svc, c.id))
        assert svc.get_by_session(manager.session_key(c.id)) is None
        assert svc.get_by_session(manager.task_session_key(c.id, "t-1")) is None

    def test_teardown_for_delete_cleans_up_worktrees(self, tmp_path):
        # A parallel code loop's git worktrees + branches must be cleaned on teardown,
        # else every deleted loop leaks .worktrees/<id> dirs + pclaw/task-* branches.
        import subprocess
        from personalclaw.loop import worktree
        ws = tmp_path / "repo"; ws.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=ws, check=True)
        (ws / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
        c = store.create(Loop(id="", name="C", kind="code", task="add oauth login here",
                              workspace_dir=str(ws), kind_config={}))
        assert worktree.ensure_base_commit(str(ws))
        wt = worktree.add_worktree(str(ws), "t-1", c.tasks_project_id)
        assert wt and os.path.isdir(wt)
        _run(manager.teardown_for_delete(_FakeSvc(), c.id))
        assert not os.path.isdir(wt)  # worktree removed


class TestReap:
    def test_reaps_running_orphan_with_no_live_session(self):
        g = _goal()
        store.update_status(g.id, LoopStatus.RUNNING)
        state, svc = _FakeState(), _FakeSvc()  # no live session
        n = _run(manager.reap_orphaned_loops(state, svc))
        assert n == 1
        assert svc.get_by_session(manager.session_key(g.id)) is not None  # re-armed

    def test_skips_paused_and_live(self):
        paused = _goal()
        store.update_status(paused.id, LoopStatus.RUNNING)
        store.update_status(paused.id, LoopStatus.PAUSED)
        state, svc = _FakeState(), _FakeSvc()
        assert _run(manager.reap_orphaned_loops(state, svc)) == 0

    def test_rekicks_planning_orphan(self, monkeypatch):
        g = _goal()
        store.update_status(g.id, LoopStatus.PLANNING)
        kicked = []
        async def _fake_advance(state, svc, lid):
            kicked.append(lid); return "gated"
        monkeypatch.setattr("personalclaw.loop.plan_walkthrough.advance_plan", _fake_advance)
        n = _run(manager.reap_orphaned_loops(_FakeState(), _FakeSvc()))
        assert n == 1 and kicked == [g.id]

    def test_brownfield_orphan_with_missing_workspace_pauses_not_rearms(self):
        # workspace_dir set to a non-existent path → launch_blocker fires → NEEDS_INPUT,
        # not a silent re-arm against a gone codebase.
        c = store.create(Loop(id="", name="C", kind="code",
                              task="fix the auth bug in the existing repo",
                              kind_config={"project_kind": "brownfield"},
                              workspace_dir="/nonexistent/repo/path"))
        store.update_status(c.id, LoopStatus.RUNNING)
        state, svc = _FakeState(), _FakeSvc()
        _run(manager.reap_orphaned_loops(state, svc))
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value
        assert svc.get_by_session(manager.session_key(c.id)) is None  # NOT re-armed
