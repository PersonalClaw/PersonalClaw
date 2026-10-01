"""A loop that is still planning stops when its owner says so, and stays stopped across a restart.

Measured on a Code loop: "Cancel and edit the task" sent nothing, so the loop stayed "planning";
its planner's nudged turns ran on, each cut at its 600 s limit, and went on again after two
gateway restarts (the restart re-drives a loop it finds planning, and the planner's nudge row is
kept on disk). Nothing on its pages could stop it. Four things now hold:

* a delete (what Cancel does) and a Stop end the walkthrough pass in flight, remove the planner's
  nudge row and stop its turn;
* a pass never starts for a loop that is no longer planning — a stop that lands between a pass and
  its retry used to have the retry re-arm the planner;
* at boot no planner pass is in flight, so every planner nudge row is a leftover and goes; only a
  loop still planning is driven again.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.planning import runner as R
from personalclaw.planning.session import PlanSession, PlanStep


class _FakeSse:
    def publish(self, *a, **k):
        pass


class _Turns:
    def __init__(self):
        self.stopped: list[str] = []

    async def stop_turn(self, key, *, force=False, **_kw):
        self.stopped.append(key)
        return "soft"


class _FakeState:
    conversation_log = None

    def __init__(self):
        self._sessions: dict = {}
        self.sessions = _Turns()

    def push_refresh(self, *kinds):
        pass

    def loop_sse(self):
        return _FakeSse()

    def notify(self, *a, **k):
        pass

    def waiting_on_owner(self, key):
        return False


class _Svc:
    """The nudge rows, as the real service keeps them (they outlive a restart)."""

    def __init__(self):
        self.rows: dict[str, SimpleNamespace] = {}

    async def add(self, *, session_name, **kw):
        row = SimpleNamespace(id=f"N{len(self.rows) + 1}", session_name=session_name, active=True)
        self.rows[row.id] = row
        return row

    async def update(self, loop_id, **kw):
        for k, v in kw.items():
            setattr(self.rows[loop_id], k, v)

    async def remove(self, loop_id):
        self.rows.pop(loop_id, None)

    def get_by_session(self, key):
        return next((r for r in self.rows.values() if r.session_name == key), None)

    def list_all(self):
        return list(self.rows.values())


SVC = _Svc()


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    SVC.rows.clear()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: SVC)
    kinds.ensure_loaded()
    return tmp_path


def _req(app, method, path, *, body=None, match_info=None):
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "alice"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


async def _planner_is_drafting(state, cid: str) -> None:
    """The planner's nudge row and its turn in flight, as a pass leaves them."""
    key = pw.planner_session_key(cid)
    await SVC.add(session_name=key)
    state._sessions[key] = SimpleNamespace(key=key, running=True, _queue=[])


async def _kicked_planning(app, monkeypatch, planning: dict) -> str:
    async def _a_long_pass(state, svc, lid):
        planning["started"] = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            planning["cancelled"] = True
            raise
        return "gated"

    monkeypatch.setattr("personalclaw.loop.plan_walkthrough.advance_plan", _a_long_pass)
    created = await H.api_loop_create(
        _req(app, "POST", "/api/loops", body={"kind": "goal", "task": "plan the move"})
    )
    cid = json.loads(created.body.decode())["id"]
    kicked = await H.api_loop_plan_start(
        _req(app, "POST", f"/api/loops/{cid}/plan/start", match_info={"id": cid})
    )
    assert kicked.status == 202
    store.update_status(cid, LoopStatus.PLANNING)
    await asyncio.sleep(0.05)
    assert planning.get("started"), "vacuity: the walkthrough pass never started"
    return cid


def test_a_delete_mid_planning_ends_the_pass_the_planner_row_and_its_turn(monkeypatch):
    planning: dict[str, object] = {}

    async def _go() -> None:
        app = web.Application()
        app["state"] = state = _FakeState()
        cid = await _kicked_planning(app, monkeypatch, planning)
        await _planner_is_drafting(state, cid)

        deleted = await H.api_loop_delete(
            _req(app, "DELETE", f"/api/loops/{cid}", match_info={"id": cid})
        )

        assert deleted.status == 200
        assert store.get(cid) is None
        assert planning.get("cancelled"), "the deleted loop's planner pass kept drafting"
        assert SVC.get_by_session(pw.planner_session_key(cid)) is None, "its nudge row stayed"
        assert f"dashboard:{pw.planner_session_key(cid)}" in state.sessions.stopped

    asyncio.run(_go())


def test_a_stop_mid_planning_ends_the_planner_and_the_loop_stays_stopped(monkeypatch):
    planning: dict[str, object] = {}

    async def _go() -> None:
        app = web.Application()
        app["state"] = state = _FakeState()
        cid = await _kicked_planning(app, monkeypatch, planning)
        await _planner_is_drafting(state, cid)

        stopped = await H.api_loop_action(
            _req(app, "PATCH", f"/api/loops/{cid}", body={"action": "stop"}, match_info={"id": cid})
        )

        assert stopped.status == 200, json.loads(stopped.body.decode())
        assert store.get(cid).status == LoopStatus.STOPPED.value
        assert planning.get("cancelled"), "the stopped loop's planner pass kept drafting"
        assert SVC.get_by_session(pw.planner_session_key(cid)) is None, "its nudge row stayed"
        assert f"dashboard:{pw.planner_session_key(cid)}" in state.sessions.stopped

    asyncio.run(_go())


def test_no_pass_starts_for_a_loop_that_is_no_longer_planning(monkeypatch):
    loop = store.create(Loop(id="", name="", kind="code", task="stop escaping titles twice"))
    step = PlanStep(id="step-0", kind="problem_framing", title="Frame it", objective="o")
    loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[step]))
    store.update_status(loop.id, LoopStatus.STOPPED)
    passes: list[str] = []

    async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
        passes.append(brief)
        return R.PlannerPass(ended=R.STOPPED)

    monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)

    assert asyncio.run(pw.run_step_pass(object(), object(), loop.id, "step-0")) is None
    assert asyncio.run(pw.run_design_pass(object(), object(), loop.id)) is None
    assert passes == [], "a stopped loop's planner was armed again"


def test_a_stop_between_a_pass_and_its_retry_skips_the_retry(monkeypatch):
    loop = store.create(Loop(id="", name="", kind="code", task="stop escaping titles twice"))
    step = PlanStep(id="step-0", kind="problem_framing", title="Frame it", objective="o")
    loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[step]))
    store.update_status(loop.id, LoopStatus.PLANNING)
    passes: list[str] = []

    async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
        passes.append(brief)
        store.update_status(loop.id, LoopStatus.STOPPED)  # the owner stops it during the pass
        return R.PlannerPass(ended=R.TIMED_OUT, limit_secs=600)

    monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)

    asyncio.run(pw.run_step_pass(object(), object(), loop.id, "step-0"))

    assert len(passes) == 1, "the retry re-armed the planner of a stopped loop"


def test_at_boot_every_planner_row_is_a_leftover_and_only_a_planning_loop_is_driven(monkeypatch):
    planning = store.create(Loop(id="", name="", kind="goal", task="plan the move"))
    store.update_status(planning.id, LoopStatus.PLANNING)
    stopped = store.create(Loop(id="", name="", kind="code", task="escape titles once"))
    store.update_status(stopped.id, LoopStatus.STOPPED)
    for cid in (planning.id, stopped.id, "0a1b2c3d"):  # the last one's loop is gone
        asyncio.run(SVC.add(session_name=pw.planner_session_key(cid)))
    worker = asyncio.run(SVC.add(session_name=manager.session_key(planning.id)))
    driven: list[str] = []

    async def _advance(state, svc, lid):
        driven.append(lid)
        return "gated"

    monkeypatch.setattr(pw, "advance_plan", _advance)

    asyncio.run(W.LoopWatchdog(_FakeState(), SVC)._boot_sweep())

    assert [r.session_name for r in SVC.list_all()] == [worker.session_name], SVC.list_all()
    assert driven == [planning.id]
