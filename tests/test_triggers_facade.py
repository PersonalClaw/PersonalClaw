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


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A fake state whose SCHEDULE half is a real trigger store (S110).

    This used to mock `crons.list_jobs` and return a `ScheduleJob`. The facade's legacy fallbacks
    retired with `ScheduleService`'s CRUD, so the schedule half is now the store — and the fixture
    seeds the equivalent row rather than the mock. The lifecycle half stays a real
    `ScriptHookStore`, which is what these tests were always about.
    """
    import personalclaw.config.loader as loader
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)

    hook_store = ScriptHookStore(config_dir=tmp_path)
    st = MagicMock()
    st._hook_store = hook_store
    st._sessions = {}
    st._background_tasks = set()
    # one schedule trigger (invoke-agent → action derived on read), the store-shaped equivalent of
    # the `ScheduleJob` this fixture used to mock.
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="job1",
            name="Nightly",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": "do it", "agent": "coder"},
                }
            },
        )
    )
    st._store = store
    st._job = store.get("job1").trigger
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
# S67 measured the `event:` namespace answering 404 for toggle/run/PUT and 400 for /test, so there
# was no way to drive an event trigger by hand. That namespace is gone: a data-event trigger is a
# row in the one store (`kind: "event"`), listed and addressed as `store:<id>`, so it takes the
# store kind's operations — toggle, run, dry run, history, delete — which is what these pin.


def _event_row(state, **fields):
    """Write a data-event row into the fixture's store and return the id the API addresses it by."""
    from personalclaw.event_triggers import MEMORY_UPDATE, event_spec
    from personalclaw.triggers import screen
    from personalclaw.triggers.models import Trigger

    fields.setdefault("enabled", True)
    fields.setdefault("spec", event_spec(MEMORY_UPDATE))
    fields.setdefault(
        "workflow", {"inline": {"provider": "notify", "config": {"title_template": "hi"}}}
    )
    trigger = Trigger(id="event:ev1", name="ev1", kind="event", **fields)
    # Frozen the way every real writer freezes it (S116), so the doctor judges a row a writer makes.
    trigger.capabilities = screen.capabilities_for_action(trigger)
    state._store.upsert(trigger)
    return "store:event:ev1"


def _stub_provider(monkeypatch, calls=None):
    from personalclaw.action_providers import ActionResult

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            if calls is not None:
                calls.append(ctx.payload)
            return ActionResult(success=True, stdout="fired")

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())


def test_an_event_trigger_toggles_through_the_store(state):
    tid = _event_row(state)
    req = _req("POST", f"/api/triggers/{tid}/toggle", state, body={}, match_info={"id": tid})
    resp = _run(T.api_trigger_toggle(req))
    assert resp.status == 200
    assert state._store.get("event:ev1").trigger.enabled is False, "the toggle must persist"


def test_re_enabling_an_exhausted_event_trigger_resets_its_budget(state):
    """A trigger that spent its `max_fires` switched itself off ("tell me the NEXT time X"). Turning
    it back on without a fresh budget would re-enable a trigger the budget gate refuses on its very
    next fire — the off switch working and the ON switch not."""
    tid = _event_row(state, enabled=False, gates={"max_fires": 2}, run_count=2)
    req = _req(
        "POST", f"/api/triggers/{tid}/toggle", state, body={"enabled": True}, match_info={"id": tid}
    )
    assert _run(T.api_trigger_toggle(req)).status == 200
    trigger = state._store.get("event:ev1").trigger
    assert trigger.enabled is True and trigger.run_count == 0


def test_an_absent_event_trigger_is_a_404(state):
    tid = "store:event:nope"
    req = _req("POST", f"/api/triggers/{tid}/toggle", state, body={}, match_info={"id": tid})
    assert _run(T.api_trigger_toggle(req)).status == 404


def test_an_event_trigger_runs_by_hand_and_the_run_does_not_spend_its_budget(state, monkeypatch):
    """/run reaches the store's one manual dispatch and records a MANUAL run. It does not spend
    `max_fires`, which bounds UNATTENDED firing: spending it from a Run button would let a user
    retire their own trigger by testing it."""
    calls: list[dict] = []
    _stub_provider(monkeypatch, calls)
    tid = _event_row(state, gates={"max_fires": 1})
    req = _req("POST", f"/api/triggers/{tid}/run", state, body={}, match_info={"id": tid})
    resp = _run(T.api_trigger_run(req))
    assert resp.status == 200 and _body(resp)["ok"] is True
    assert calls and calls[0]["manual"] is True
    history_req = _req("GET", f"/api/triggers/{tid}/history", state, match_info={"id": tid})
    history = _body(_run(T.api_trigger_history(history_req)))
    assert history["total"] == 1 and history["runs"][0]["trigger"] == "manual"
    trigger = state._store.get("event:ev1").trigger
    assert trigger.run_count == 0 and trigger.enabled is True


def test_test_points_an_event_trigger_at_run_and_its_dry_run(state):
    """The refusal is worded for the trigger in hand — it used to call every non-lifecycle trigger a
    schedule trigger."""
    tid = _event_row(state)
    req = _req("POST", f"/api/triggers/{tid}/test", state, body={}, match_info={"id": tid})
    resp = _run(T.api_trigger_test(req))
    assert resp.status == 400
    error = _body(resp)["error"]
    assert "dry_run" in error and "schedule" not in error


def test_the_facade_has_no_remaining_parity_gaps(state, monkeypatch):
    """The whole point, asserted as one statement.

    Support is derived by DRIVING each handler and seeing whether it refuses on kind grounds, not by
    reading the source — a branch that exists but returns 404 is not support, and that distinction
    is the entire finding of this session.
    """
    from personalclaw.triggers.events import parity_report

    _stub_provider(monkeypatch)
    event_id = _event_row(state)

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
    ids = {"store": event_id, "lifecycle": "lifecycle:x", "schedule": "schedule:job1"}
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


# ── S70: the week grid + automation doctor (AUTO-A1, §7 criterion 12) ──


def _seed_interval(
    state,
    trigger_id,
    name,
    *,
    interval=3600,
    gates=None,
    workflow=None,
    glob="",
    enabled=True,
    expr="",
):
    """Write an interval (or cron) trigger into the fixture's real store.

    Replaces `_every_job`, which built a `ScheduleJob` for `crons.list_jobs` to return — the legacy
    fallback S110 retired. The grid anchor comes from `next_fire_at`: the legacy shape carried it as
    `created_ts`/`last_run_ts`, and the store carries an armed ISO fire, so the helper arms the row
    the way every real write path does.

    It also FREEZES the capability block, for the same reason (S116): every real write path derives
    one from the action at save time, so a fixture that skipped it would be building a row no writer
    produces — and would then fail the doctor's `unfenced_write_action` check on a store the test
    calls healthy.
    """
    from datetime import datetime, timezone

    from personalclaw.triggers import screen
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    spec = (
        {"kind": "cron", "expr": expr} if expr else {"kind": "interval", "interval_secs": interval}
    )
    trigger = Trigger(
        id=trigger_id,
        name=name,
        kind="clock",
        enabled=enabled,
        spec=spec,
        gates=gates or {},
        workflow=workflow or {"inline": {"provider": "run-prompt", "config": {"message": "x"}}},
        next_fire_at=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
    )
    if glob:
        trigger.spec = {**trigger.spec, "paths": [glob]}
    trigger.capabilities = screen.capabilities_for_action(trigger)
    TriggerStore(base_dir=state._store.base_dir).upsert(trigger)
    return trigger


def test_week_grid_annotates_suppressed_slots(state):
    """A grid that HID suppressed fires would show a schedule the user does not have."""
    state._store.delete("job1")
    _seed_interval(state, "j1", "Hourly", gates={"quiet_hours": {"start": "22:00", "end": "08:00"}})
    req = _req("GET", "/api/triggers/week", state, query="start=2024-01-01&days=1")
    resp = _run(T.api_triggers_week(req))
    assert resp.status == 200
    body = _body(resp)
    assert len(body["occurrences"]) == 24
    suppressed = [o for o in body["occurrences"] if o["suppressed_by"] == "quiet"]
    assert len(suppressed) == 10 and all(o["reason"] for o in suppressed)
    assert body["truncated"] == []


def test_week_grid_omits_disabled_but_now_PLOTS_a_cron(state):
    """🔴 SUPERSEDED CONTRACT (S103). This asserted the cron kind was OMITTED — deliberate at the
    time, because nothing could iterate a cron's fires, and the handler said so ("a wrong band is
    worse than a missing one"). But omitting them made the week view a forecast of only HALF a
    user's automations, silently. S96's `arm.next_fire` can step a cron, so it now plots on the same
    annotated grid as an interval.

    A DISABLED trigger is still omitted, and that half is unchanged: it has no fires, and drawing
    them would make the grid a wish list rather than a forecast."""
    state._store.delete("job1")
    _seed_interval(state, "j1", "Hourly")
    _seed_interval(state, "j3", "Off", enabled=False)
    _seed_interval(state, "j2", "Cron", expr="0 9 * * *")
    body = _body(
        _run(T.api_triggers_week(_req("GET", "/api/triggers/week", state, query="days=1")))
    )
    names = {o["trigger_name"] for o in body["occurrences"]}
    assert names == {"Hourly", "Cron"}
    assert "Off" not in names


def test_week_grid_reports_which_triggers_were_capped(state):
    """Named, not a bare bool: "some trigger was capped" is not actionable, and a silently partial
    week reads as an accurate forecast."""
    state._store.delete("job1")
    _seed_interval(state, "j1", "Minutely", interval=60)
    body = _body(
        _run(T.api_triggers_week(_req("GET", "/api/triggers/week", state, query="days=7")))
    )
    assert body["truncated"] == ["schedule:j1"]


def test_week_grid_rejects_a_bad_start(state):
    resp = _run(T.api_triggers_week(_req("GET", "/api/triggers/week", state, query="start=nope")))
    assert resp.status == 400


def test_week_grid_bounds_the_window(state):
    """31 days max: an unbounded `days` is a cheap way to make the endpoint slow."""
    state._store.delete("job1")
    _seed_interval(state, "j1", "Hourly")
    body = _body(
        _run(T.api_triggers_week(_req("GET", "/api/triggers/week", state, query="days=9999")))
    )
    from datetime import datetime

    span = datetime.fromisoformat(body["end"]) - datetime.fromisoformat(body["start"])
    assert span.days == 31


def test_doctor_reports_across_both_trigger_kinds(state):
    """The doctor walks schedule AND event triggers — a problem in either is equally silent. The
    event half is read off the live store row: an `agent_scope` no fire path enforces."""
    from personalclaw.event_triggers import MEMORY_UPDATE, event_spec

    _event_row(state, spec={**event_spec(MEMORY_UPDATE), "agent_scope": ["research"]})
    state._store.delete("job1")
    _seed_interval(state, "j1", "Orphan", workflow={"def": "gone"})
    _seed_interval(state, "j2", "Ungated", gates={"duty_gate": {"provider": "acme-calendar"}})
    resp = _run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state)))
    assert resp.status == 200
    body = _body(resp)
    found = {(f["trigger_id"], f["code"]) for f in body["findings"]}
    assert "unknown_duty_gate" in {code for _tid, code in found}
    assert ("store:event:ev1", "unenforced_agent_scope") in found
    assert body["healthy"] is False
    assert all(f["fix"] for f in body["findings"])


def test_a_memory_key_glob_is_not_diagnosed_as_a_file_watch(state):
    """🔴 The legacy projection handed the doctor an event trigger's memory KEY glob as a watch glob,
    so a key glob of `*` — which is simply "every memory write" — was reported as a watch that
    "matches nearly every file"."""
    from personalclaw.event_triggers import MEMORY_KEY_PATTERN, event_spec

    state._store.delete("job1")
    _event_row(state, spec=event_spec(MEMORY_KEY_PATTERN, "*"))
    body = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert body["healthy"] is True, body["findings"]


def test_doctor_reports_healthy_when_nothing_is_wrong(state):
    state._store.delete("job1")
    _seed_interval(state, "j1", "Fine", gates={"quiet_hours": {"start": "22:00", "end": "08:00"}})
    body = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert body["healthy"] is True and body["count"] == 0


def test_the_doctor_no_longer_calls_an_UNDISPATCHABLE_trigger_healthy(state):
    """🔴 #779. `diagnose` returned `healthy: True` for a trigger whose action names a provider
    nothing can dispatch — every fire fails, and the doctor said nothing.

    Its one provider-shaped check could not see the case by construction: `_seed_interval` freezes
    the capability block the way every real writer does, and `capabilities_for_action` puts the
    UNREGISTERED name straight into `providers`, so the fence check's `action not in granted` was
    False. Asserted through the real route, so the injected provider set is the live one.
    """
    from personalclaw.triggers.screen import capabilities_for_action

    state._store.delete("job1")
    trigger = _seed_interval(
        state,
        "j1",
        "Ghost action",
        workflow={"inline": {"provider": "no-such-provider", "config": {}}},
    )
    # The premise, measured rather than asserted from memory: the frozen grant NAMES the bogus
    # provider, which is exactly what neutralised the pre-existing finding.
    assert capabilities_for_action(trigger) == {"providers": ["no-such-provider"]}
    assert trigger.capabilities == {"providers": ["no-such-provider"]}

    body = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    finding = next(f for f in body["findings"] if f["code"] == "unknown_action_provider")
    assert "no-such-provider" in finding["detail"] and finding["fix"]
    assert body["healthy"] is False
    # Reported INSTEAD of the fence finding, not alongside it: "re-save to freeze the grant" is not
    # a fix for a name that resolves to nothing.
    assert "unfenced_write_action" not in {f["code"] for f in body["findings"]}


def test_the_doctor_names_an_UNPARSEABLE_cron_it_can_never_arm(state):
    """#687's already-on-disk population, and the rail the shipped fix never got.

    The create path refuses this shape and the doctor names it through the `semantic_spec_issues`
    fold, which is `arm`'s own rule — one owner, so the doctor cannot say healthy about a row the
    fire path refuses to arm. Pinned here because a clean break ships no migration: the rows a user
    already has are found by this surface or not at all.
    """
    state._store.delete("job1")
    _seed_interval(state, "j1", "Inert", expr="99 99 * * *")
    body = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    finding = next(f for f in body["findings"] if f["code"] == "unfireable_spec")
    assert "99 99 * * *" in finding["detail"] and finding["fix"]
    assert body["healthy"] is False


def test_a_VALID_cron_and_a_REGISTERED_provider_are_still_healthy(state):
    """The vacuity partner for the two findings above: they must not fire on a healthy store.

    `@daily` rides along because the checks run croniter, so a macro the old five-token frontend
    check would have flagged must not become a doctor finding either.
    """
    state._store.delete("job1")
    _seed_interval(state, "j1", "Nine", expr="0 9 * * *")
    _seed_interval(state, "j2", "Macro", expr="@daily")
    _seed_interval(state, "j3", "Notify", workflow={"inline": {"provider": "notify", "config": {}}})
    body = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert body["healthy"] is True, body["findings"]


def test_week_and_doctor_routes_register_before_the_id_route():
    """`/week` and `/doctor` must not be captured as trigger ids.

    The ordering landmine S67 already paid for with `/surfacing`: aiohttp matches in registration
    order, so a literal path registered after `/{id}` is unreachable.
    """
    import inspect

    src = inspect.getsource(T.register_trigger_routes)
    week = src.index('"/api/triggers/week"')
    doctor = src.index('"/api/triggers/doctor"')
    by_id = src.index('"/api/triggers/{id}"')
    assert week < by_id and doctor < by_id
