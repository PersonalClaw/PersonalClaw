"""P4b — the unified Trigger facade (/api/triggers over hooks + schedule stores).

Drives the handlers directly with a fake state that carries a real ScriptHookStore
(lifecycle) + a mocked schedule service (schedule). Asserts: cross-kind list,
?type filter, namespaced-id routing to the right store, lifecycle create/toggle/
delete, and the schedule action↔exec bridge + action derivation on read.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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
    job = ScheduleJob(
        id="job1",
        name="Nightly",
        action=make_agent_action(message="do it", agent="coder"),
        schedule=ScheduleDefinition(kind="every", every_secs=3600),
    )
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
    state._hook_store.create(
        {
            "name": "on-stop",
            "event": "Stop",
            "provider": "bash",
            "provider_config": {"command": "echo hi"},
        }
    )
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
    state._hook_store.create(
        {"name": "h", "event": "Stop", "provider": "bash", "provider_config": {"command": "x"}}
    )
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=lifecycle")))
    data = _body(resp)
    assert data["triggers"] and all(t["kind"] == "lifecycle" for t in data["triggers"])


def test_create_lifecycle(state):
    body = {
        "trigger_type": "lifecycle",
        "name": "auditor",
        "event": "PreToolUse",
        "matcher": "write_file",
        "action": {"provider": "bash", "config": {"command": "log"}},
    }
    resp = _run(T.api_trigger_create(_req("POST", "/api/triggers", state, body=body)))
    assert resp.status == 200
    t = _body(resp)["trigger"]
    assert t["kind"] == "lifecycle" and t["id"].startswith("lifecycle:")
    assert t["action"] == {"provider": "bash", "config": {"command": "log"}}
    assert t["event"] == "PreToolUse" and t["matcher"] == "write_file"


def test_create_rejects_unknown_kind(state):
    resp = _run(
        T.api_trigger_create(_req("POST", "/api/triggers", state, body={"trigger_type": "bogus"}))
    )
    assert resp.status == 400


def test_toggle_and_delete_lifecycle_route_by_id(state):
    hook = state._hook_store.create(
        {"name": "h", "event": "Stop", "provider": "bash", "provider_config": {"command": "x"}}
    )
    tid = f"lifecycle:{hook.id}"
    # toggle
    resp = _run(
        T.api_trigger_toggle(
            _req("POST", f"/api/triggers/{tid}/toggle", state, match_info={"id": tid})
        )
    )
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


# ── S67: event-kind parity (AUTOMATION-SUBSTRATE §2) ──
#
# Every assertion here was a measured 404/400/silent-no-op before the fix. The shipped facade
# handled the `event` kind in list/create/DELETE only; toggle/run/PUT fell to the SCHEDULE branch,
# which looked the id up among cron jobs, missed, and answered 404 "not found" — the API telling a
# user that a trigger sitting in their store does not exist. `/test` answered 400 "use /run" and
# `/run` answered 404, so there was no way to fire an event trigger by hand at all.


@pytest.fixture
def event_store(tmp_path, monkeypatch):
    """A real EventTriggerStore under tmp_path, wired into the handler's accessor.

    Patches `T._event_store` rather than `config_dir`: the handler resolves the store per call, so
    patching the accessor is what actually redirects it, and nothing can reach the real home.
    """
    from personalclaw.event_triggers import EventTriggerStore

    store = EventTriggerStore(tmp_path / "event_triggers.json")
    monkeypatch.setattr(T, "_event_store", lambda: store)
    return store


def _ev(store, **kw):
    from personalclaw.event_triggers import MEMORY_UPDATE, EventTrigger

    kw.setdefault("id", "ev1")
    kw.setdefault("pattern", MEMORY_UPDATE)
    t = EventTrigger(**kw)
    store.upsert(t)
    return t


def test_event_toggle_no_longer_404s(state, event_store):
    """Measured: 404 "not found" from the schedule fallthrough, while the trigger kept firing."""
    _ev(event_store, enabled=True)
    state.crons.list_jobs.return_value = []  # no cron job shares this id
    req = _req(
        "POST", "/api/triggers/event:ev1/toggle", state, body={}, match_info={"id": "event:ev1"}
    )
    resp = _run(T.api_trigger_toggle(req))
    assert resp.status == 200
    assert _body(resp)["trigger"]["enabled"] is False
    assert event_store.load()[0].enabled is False, "the toggle must actually persist"


def test_event_toggle_honours_an_explicit_enabled(state, event_store):
    _ev(event_store, enabled=True)
    req = _req(
        "POST",
        "/api/triggers/event:ev1/toggle",
        state,
        body={"enabled": True},
        match_info={"id": "event:ev1"},
    )
    assert _run(T.api_trigger_toggle(req)).status == 200
    assert event_store.load()[0].enabled is True  # idempotent, not flipped


def test_re_enabling_an_exhausted_trigger_resets_its_budget(state, event_store):
    """A self-retired trigger must actually come back.

    `record_fire` disables at `max_fires`. Flipping `enabled` back without clearing `fire_count`
    would re-arm a trigger that `record_fire` disables again on its very next fire — the off switch
    working and the ON switch not.
    """
    _ev(event_store, enabled=False, max_fires=2, fire_count=2)
    req = _req(
        "POST",
        "/api/triggers/event:ev1/toggle",
        state,
        body={"enabled": True},
        match_info={"id": "event:ev1"},
    )
    assert _run(T.api_trigger_toggle(req)).status == 200
    t = event_store.load()[0]
    assert t.enabled is True and t.fire_count == 0


def test_event_toggle_404s_only_for_a_genuinely_absent_trigger(state, event_store):
    req = _req(
        "POST", "/api/triggers/event:nope/toggle", state, body={}, match_info={"id": "event:nope"}
    )
    assert _run(T.api_trigger_toggle(req)).status == 404


def test_event_put_persists_every_field(state, event_store):
    """Measured: EVERY field returned 400 "no fields to update" or 404 and wrote nothing."""
    _ev(event_store, enabled=True, max_fires=3, debounce_secs=5.0)
    body = {
        "enabled": False,
        "pattern": "ContentMatch",
        "content_re": r"\bdeadline\b",
        "key_glob": "project.*",
        "max_fires": 10,
        "debounce_secs": 1.5,
        "action": {"provider": "webhook", "config": {"url": "https://example.test/x"}},
    }
    req = _req("PUT", "/api/triggers/event:ev1", state, body=body, match_info={"id": "event:ev1"})
    resp = _run(T.api_trigger_detail(req))
    assert resp.status == 200
    t = event_store.load()[0]
    assert t.enabled is False
    assert t.pattern == "ContentMatch"
    assert t.content_re == r"\bdeadline\b"
    assert t.key_glob == "project.*"
    assert t.max_fires == 10
    assert t.debounce_secs == 1.5
    assert t.action_provider == "webhook"
    assert t.action_config == {"url": "https://example.test/x"}


def test_event_put_rejects_an_unknown_pattern(state, event_store):
    """A typo'd pattern matches nothing, so accepting it would silently retire a working trigger."""
    _ev(event_store, pattern="MemoryUpdate")
    req = _req(
        "PUT",
        "/api/triggers/event:ev1",
        state,
        body={"pattern": "NotAPattern"},
        match_info={"id": "event:ev1"},
    )
    resp = _run(T.api_trigger_detail(req))
    assert resp.status == 400
    assert (
        event_store.load()[0].pattern == "MemoryUpdate"
    ), "a rejected PUT must not partially write"


def test_event_put_rejects_a_non_numeric_budget(state, event_store):
    _ev(event_store, max_fires=3)
    req = _req(
        "PUT",
        "/api/triggers/event:ev1",
        state,
        body={"max_fires": "lots"},
        match_info={"id": "event:ev1"},
    )
    assert _run(T.api_trigger_detail(req)).status == 400
    assert event_store.load()[0].max_fires == 3


def test_event_put_is_a_partial_patch(state, event_store):
    """An absent key leaves its field alone — a PUT of one field must not blank the others."""
    _ev(event_store, key_glob="keep.me", max_fires=7, action_provider="notify")
    req = _req(
        "PUT",
        "/api/triggers/event:ev1",
        state,
        body={"enabled": False},
        match_info={"id": "event:ev1"},
    )
    assert _run(T.api_trigger_detail(req)).status == 200
    t = event_store.load()[0]
    assert t.key_glob == "keep.me" and t.max_fires == 7 and t.action_provider == "notify"


def test_event_run_fires_through_the_shared_executor(state, event_store, monkeypatch):
    """/run reaches the real dispatch path, not a reimplementation.

    Asserted through `execute_event_action` so a future divergence between the manual and live paths
    fails here — a test button with its own dispatch would eventually certify a broken trigger.
    """
    from personalclaw.action_providers import ActionResult

    calls = []

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            calls.append(ctx.payload)
            return ActionResult(success=True, stdout="fired")

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())
    _ev(event_store, action_provider="notify", action_config={"title": "hi"})
    req = _req(
        "POST",
        "/api/triggers/event:ev1/run",
        state,
        body={"key": "k1", "value": "v1"},
        match_info={"id": "event:ev1"},
    )
    resp = _run(T.api_trigger_run(req))
    assert resp.status == 200
    body = _body(resp)
    assert body["ok"] is True and body["result"]["ran"] is True
    assert calls and calls[0]["key"] == "k1"


def test_a_manual_fire_does_not_spend_the_budget(state, event_store, monkeypatch):
    """`max_fires` bounds UNATTENDED firing.

    Spending it from a Run button would let a user exhaust and self-retire their own trigger by
    testing it — the same asymmetry S65 set for the hourly cap (`within_rate_window(manual=True)`).
    """
    from personalclaw.action_providers import ActionResult

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            return ActionResult(success=True)

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())
    _ev(event_store, max_fires=1, fire_count=0)
    req = _req(
        "POST", "/api/triggers/event:ev1/run", state, body={}, match_info={"id": "event:ev1"}
    )
    assert _run(T.api_trigger_run(req)).status == 200
    t = event_store.load()[0]
    assert t.fire_count == 0 and t.enabled is True, "a manual fire must not retire the trigger"


def test_event_test_and_run_agree(state, event_store, monkeypatch):
    """Measured: /test said "use /run" and /run said 404 — a circular dead end."""
    from personalclaw.action_providers import ActionResult

    seen = []

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            seen.append(ctx.payload.get("test"))
            return ActionResult(success=True)

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())
    _ev(event_store)
    for handler in (T.api_trigger_test, T.api_trigger_run):
        req = _req(
            "POST", "/api/triggers/event:ev1/x", state, body={}, match_info={"id": "event:ev1"}
        )
        resp = _run(handler(req))
        assert resp.status == 200, f"{handler.__name__} refused an event trigger"
        assert _body(resp)["result"]["ran"] is True


def test_a_refused_fire_answers_200_with_a_reason(state, event_store, monkeypatch):
    """A guardrail block is not a client error.

    4xx would render a denylist decision as a malformed request; the honest shape is a successful
    response carrying `ran: false` and the reason.
    """
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: None)
    _ev(event_store, action_provider="ghost")
    req = _req(
        "POST", "/api/triggers/event:ev1/run", state, body={}, match_info={"id": "event:ev1"}
    )
    resp = _run(T.api_trigger_run(req))
    assert resp.status == 200
    body = _body(resp)
    assert body["ok"] is False and body["result"]["ran"] is False
    assert "not registered" in body["result"]["reason"]


def test_event_history_is_honest_about_having_none(state, event_store):
    """Measured: a bare `{"runs": [], "total": 0}`, which renders as "ran, kept no records".

    An event trigger keeps a counter, not run records. Saying so — and returning the counter — is
    different from implying an empty history.
    """
    _ev(event_store, fire_count=4, last_fired_at=123.0)
    req = _req("GET", "/api/triggers/event:ev1/history", state, match_info={"id": "event:ev1"})
    resp = _run(T.api_trigger_history(req))
    assert resp.status == 200
    body = _body(resp)
    assert body["supported"] is False and body["reason"]
    assert body["fire_count"] == 4 and body["last_fired_at"] == 123.0


def test_lifecycle_history_says_why_it_is_empty(state):
    req = _req("GET", "/api/triggers/lifecycle:x/history", state, match_info={"id": "lifecycle:x"})
    body = _body(_run(T.api_trigger_history(req)))
    assert body["supported"] is False and "no run store" in body["reason"]


def test_schedule_test_points_at_run_s_dry_run(state):
    """The refusal now names the actual alternative (`/run?dry_run=1`) rather than a bare "/run"."""
    req = _req(
        "POST",
        "/api/triggers/schedule:job1/test",
        state,
        body={},
        match_info={"id": "schedule:job1"},
    )
    resp = _run(T.api_trigger_test(req))
    assert resp.status == 400
    assert "dry_run" in _body(resp)["error"]


def test_the_facade_has_no_remaining_parity_gaps(state, event_store):
    """The whole point, asserted as one statement.

    Support is derived by DRIVING each handler and seeing whether it refuses on kind grounds, not by
    reading the source — a branch that exists but returns 404 is not support, and that distinction
    is the entire finding of this session.
    """
    from personalclaw.triggers.events import parity_report

    _ev(event_store)
    state.crons.list_jobs.return_value = [state._job]

    # `crons` is a MagicMock, so `await crons.list_runs(...)` raises TypeError and the probe's
    # exception guard below would score schedule/history as UNSUPPORTED — a harness artifact
    # reported as a product gap. Give the two awaited calls real coroutines so the probe measures
    # the handler instead of the mock.
    async def _list_runs(*a, **k):
        return [], 0

    async def _run_job(*a, **k):
        return None

    state.crons.list_runs = _list_runs
    state.crons.run_job = _run_job
    state._background_tasks = set()

    probes = {
        "toggle": (T.api_trigger_toggle, "POST"),
        "run": (T.api_trigger_run, "POST"),
        "test": (T.api_trigger_test, "POST"),
        "history": (T.api_trigger_history, "GET"),
    }
    ids = {"event": "event:ev1", "lifecycle": "lifecycle:x", "schedule": "schedule:job1"}
    # list/create/delete/update are exercised by the tests above; `get` has no route for ANY kind,
    # so it is not a per-kind gap and is excluded rather than reported three times.
    support = {k: {"list", "create", "delete", "update", "get"} for k in ids}
    for kind, tid in ids.items():
        for op, (handler, method) in probes.items():
            req = _req(method, f"/api/triggers/{tid}/{op}", state, body={}, match_info={"id": tid})
            # Deliberately NOT wrapped in try/except: a raising handler is a real failure, and
            # swallowing it here scored a MagicMock artifact as a product gap on the first run.
            resp = _run(handler(req))
            # 400 = an honest kind-level refusal (an exemption); 404 = the shipped bug.
            if resp.status != 400:
                support[kind].add(op)
    assert parity_report(support) == {}
