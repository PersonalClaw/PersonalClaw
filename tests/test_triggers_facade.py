"""P4b — the unified Trigger facade (/api/triggers over hooks + schedule stores).

Drives the handlers directly with a fake state that carries a real ScriptHookStore
(lifecycle) + a mocked schedule service (schedule). Asserts: cross-kind list,
?type filter, namespaced-id routing to the right store, lifecycle create/toggle/
delete, and the schedule action↔exec bridge + action derivation on read.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.hooks import ScriptHookStore
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action


@pytest.fixture
def state(tmp_path):
    hook_store = ScriptHookStore(config_dir=tmp_path)
    st = MagicMock()
    st._hook_store = hook_store
    st._sessions = {}
    st.crons.is_running.return_value = False
    st.crons.running_since.return_value = None
    # one schedule job (invoke-agent exec mode → action derived on read)
    job = ScheduleJob(id="job1", name="Nightly",
                      action=make_agent_action(message="do it", agent="coder"),
                      schedule=ScheduleDefinition(kind="every", every_secs=3600))
    st.crons.list_jobs.return_value = [job]
    st._job = job
    return st


def _req(method, path, state, *, body=None, match_info=None, query=None):
    app = web.Application()
    app["state"] = state
    full = path + ("?" + query if query else "")
    req = make_mocked_request(method, full, match_info=match_info or {}, app=app)
    req["user"] = "tester"
    if body is not None:
        async def _json():
            return body
        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# Patch the hook-store accessor to use the fake state's store.
@pytest.fixture(autouse=True)
def _patch_store(monkeypatch, state):
    monkeypatch.setattr(T, "_hook_store", lambda s: s._hook_store)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})


def test_list_both_kinds(state):
    state._hook_store.create({"name": "on-stop", "event": "Stop", "provider": "bash",
                              "provider_config": {"command": "echo hi"}})
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    data = _body(resp)
    kinds = {t["kind"] for t in data["triggers"]}
    assert kinds == {"schedule", "lifecycle"}
    sched = next(t for t in data["triggers"] if t["kind"] == "schedule")
    # action derived from invoke-agent exec mode
    assert sched["id"] == "schedule:job1"
    assert sched["action"]["provider"] == "invoke-agent"
    assert sched["action"]["config"]["agent"] == "coder"


def test_type_filter(state):
    state._hook_store.create({"name": "h", "event": "Stop", "provider": "bash",
                              "provider_config": {"command": "x"}})
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=lifecycle")))
    data = _body(resp)
    assert data["triggers"] and all(t["kind"] == "lifecycle" for t in data["triggers"])


def test_create_lifecycle(state):
    body = {"trigger_type": "lifecycle", "name": "auditor", "event": "PreToolUse",
            "matcher": "write_file", "action": {"provider": "bash", "config": {"command": "log"}}}
    resp = _run(T.api_trigger_create(_req("POST", "/api/triggers", state, body=body)))
    assert resp.status == 200
    t = _body(resp)["trigger"]
    assert t["kind"] == "lifecycle" and t["id"].startswith("lifecycle:")
    assert t["action"] == {"provider": "bash", "config": {"command": "log"}}
    assert t["event"] == "PreToolUse" and t["matcher"] == "write_file"


def test_create_rejects_unknown_kind(state):
    resp = _run(T.api_trigger_create(_req("POST", "/api/triggers", state, body={"trigger_type": "bogus"})))
    assert resp.status == 400


def test_toggle_and_delete_lifecycle_route_by_id(state):
    hook = state._hook_store.create({"name": "h", "event": "Stop", "provider": "bash",
                                     "provider_config": {"command": "x"}})
    tid = f"lifecycle:{hook.id}"
    # toggle
    resp = _run(T.api_trigger_toggle(_req("POST", f"/api/triggers/{tid}/toggle", state, match_info={"id": tid})))
    assert resp.status == 200
    assert state._hook_store.get(hook.id).enabled is False
    # delete
    req = _req("DELETE", f"/api/triggers/{tid}", state, match_info={"id": tid})
    req = make_mocked_request("DELETE", f"/api/triggers/{tid}", match_info={"id": tid}, app=req.app)
    req["user"] = "tester"
    resp = _run(T.api_trigger_detail(req))
    assert resp.status == 200
    assert state._hook_store.get(hook.id) is None


def test_run_rejects_lifecycle(state):
    req = _req("POST", "/api/triggers/lifecycle:x/run", state, match_info={"id": "lifecycle:x"})
    resp = _run(T.api_trigger_run(req))
    assert resp.status == 400  # lifecycle triggers fire on events, not /run


def test_schedule_run_dispatches(state):
    state.crons.is_running.return_value = False
    state._background_tasks = set()
    req = _req("POST", "/api/triggers/schedule:job1/run", state, match_info={"id": "schedule:job1"})
    resp = _run(T.api_trigger_run(req))
    assert resp.status == 200
    assert _body(resp)["name"] == "Nightly"


# ── P4d: variable catalog ──

def test_variables_catalog(state):
    from personalclaw.hooks import HOOK_EVENTS
    from personalclaw.schedule import SCHEDULE_VARS

    req = _req("GET", "/api/triggers/variables", state)
    resp = _run(T.api_trigger_variables(req))
    assert resp.status == 200
    body = _body(resp)
    # schedule vars are the source-of-truth list, verbatim
    assert body["schedule"] == list(SCHEDULE_VARS)
    # lifecycle covers every fireable event exactly once, each well-formed
    events = [e["event"] for e in body["lifecycle"]]
    assert set(events) == set(HOOK_EVENTS)
    assert len(events) == len(HOOK_EVENTS)
    for e in body["lifecycle"]:
        assert e["vars"] and e["vars"][0] == "$EVENT"
        assert e["label"] and e["desc"] and isinstance(e["blocking"], bool)
    # PreToolUse is the canonical blocking + tool-matcher event
    pre = next(e for e in body["lifecycle"] if e["event"] == "PreToolUse")
    assert pre["blocking"] is True
    assert "$tool_name" in pre["vars"]
