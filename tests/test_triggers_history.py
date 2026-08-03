"""One run-history feed across all three trigger kinds (AUTO §7 criterion 4 — S84).

Criterion 4: "A hook, an event trigger, and a cron all show run history in the same feed with the
same record shape and typed outcomes."

**Measured before writing.** `/api/triggers/history` existed and its own docstring said "(schedule
runs)". The per-trigger route answered `supported: false` for the other two kinds — honest then,
since only schedules had rows. And `FireRecord`, the typed row S62 designed for exactly this, was
**exported and never constructed** (`grep 'FireRecord('` outside its module: nothing).

The load-bearing tests are the two honesty ones: a counter must not become N fabricated rows
(`test_a_fire_counter_becomes_ONE_summary_row`), and `launched` must not become `ran`
(`test_launched_maps_to_deferred_not_ran`).
"""

from __future__ import annotations

import asyncio
import pathlib
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.event_triggers import EventTrigger, EventTriggerStore
from personalclaw.hooks import ScriptHook, ScriptHookStore
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action
from personalclaw.triggers.history import (
    HOOK_STATUS_TO_OUTCOME,
    SCHEDULE_STATUS_TO_OUTCOME,
    event_trigger_to_record,
    feed_response,
    hook_to_record,
    outcome_counts,
    schedule_run_to_record,
    unified_feed,
)
from personalclaw.triggers.models import FIRE_OUTCOMES, Outcome, RunWeight

NOW = time.time()


def _run(**over):
    """A `ScheduleRun` dict in the shape the real store writes (verified against `_record_run`)."""
    row = {
        "run_id": "r1",
        "job_id": "j1",
        "trigger": "schedule",
        "started_at": NOW - 120,
        "finished_at": NOW - 110,
        "duration_ms": 10_000,
        "status": "success",
        "summary": "all good",
        "trace": "t",
        "error": "",
    }
    row.update(over)
    return row


# ── the schedule projection ──


def test_a_successful_run_maps_to_ran():
    rec = schedule_run_to_record(_run())
    assert rec.outcome == Outcome.RAN.value
    assert rec.trigger_id == "schedule:j1"
    assert rec.run_id == "r1"
    assert rec.duration_secs == 10.0
    assert rec.weight == RunWeight.FULL.value


def test_a_failure_maps_to_failed_and_carries_the_error():
    rec = schedule_run_to_record(_run(status="failure", error="boom"))
    assert rec.outcome == Outcome.FAILED.value
    assert rec.reason == "boom"


def test_a_timeout_folds_into_failed_but_says_so():
    """`FIRE_OUTCOMES` has no timeout member, and adding one would change a vocabulary five modules
    switch on. The reason carries the distinction, which is where a user reads it."""
    rec = schedule_run_to_record(_run(status="timeout", error="Timed out after 30s"))
    assert rec.outcome == Outcome.FAILED.value
    assert rec.reason.startswith("timed out:")


def test_launched_maps_to_deferred_not_ran():
    """🔴 The T7 distinction, preserved. `launched` means the action kicked off a background turn and
    nobody has seen the result; calling it `ran` would report success for unfinished work."""
    rec = schedule_run_to_record(_run(status="launched", summary="", error=""))
    assert rec.outcome == Outcome.DEFERRED.value
    assert rec.outcome != Outcome.RAN.value
    assert "not yet known" in rec.reason
    # And it has not earned a full run record yet.
    assert rec.weight == RunWeight.LEDGER.value


def test_an_unknown_status_becomes_failed_not_ran():
    """`FireRecord.from_dict`'s own rule: a row this build cannot classify must not count as a
    success, because a success is what the health rollup treats as nothing to look at."""
    assert schedule_run_to_record(_run(status="wat")).outcome == Outcome.FAILED.value


def test_every_mapped_outcome_is_in_the_typed_vocabulary():
    """A mapping to a name outside `FIRE_OUTCOMES` would make the feed unfilterable."""
    for value in list(SCHEDULE_STATUS_TO_OUTCOME.values()) + list(HOOK_STATUS_TO_OUTCOME.values()):
        assert value in FIRE_OUTCOMES


def test_the_summary_is_used_when_there_is_no_error():
    assert schedule_run_to_record(_run(summary="did a thing")).reason == "did a thing"


def test_a_malformed_timestamp_does_not_raise():
    """One corrupt row must not empty the whole feed — the failure that makes a user think nothing
    ever ran."""
    rec = schedule_run_to_record(_run(started_at="not-a-time", finished_at=None))
    assert rec.started_at == "not-a-time"  # a string passes through as-is
    assert rec.finished_at == ""


def test_an_empty_run_dict_is_survivable():
    rec = schedule_run_to_record({})
    assert rec.outcome == Outcome.FAILED.value


# ── the hook projection ──


def test_a_hook_that_ran_projects_its_last_run():
    hook = ScriptHook(
        id="h1", name="fmt", event="Stop", run_count=12, last_run=NOW, last_status="ok"
    )
    rec = hook_to_record(hook)
    assert rec is not None
    assert rec.trigger_id == "lifecycle:h1"
    assert rec.outcome == Outcome.RAN.value
    assert rec.counters == {"run_count": 12}


def test_a_hook_with_history_is_marked_incomplete():
    """A hook keeps only its most recent run, so any count above 1 means earlier rows are gone."""
    once = ScriptHook(id="h1", name="x", event="Stop", run_count=1, last_run=NOW, last_status="ok")
    many = ScriptHook(id="h2", name="y", event="Stop", run_count=9, last_run=NOW, last_status="ok")
    assert hook_to_record(once).incomplete is False
    assert hook_to_record(many).incomplete is True


def test_a_hook_that_never_ran_projects_NOTHING():
    """🔴 A synthetic row for a hook that never fired reads as "it ran and recorded nothing" — the
    same lie the event-kind `supported: false` response was written to avoid."""
    assert hook_to_record(ScriptHook(id="h1", name="x", event="Stop")) is None


def test_a_failing_hook_maps_to_failed_with_a_reason():
    hook = ScriptHook(
        id="h1", name="x", event="Stop", run_count=2, last_run=NOW, last_status="error"
    )
    rec = hook_to_record(hook)
    assert rec.outcome == Outcome.FAILED.value
    assert "error" in rec.reason


def test_a_blocked_hook_maps_to_refused():
    hook = ScriptHook(
        id="h1", name="x", event="PreToolUse", run_count=1, last_run=NOW, last_status="blocked"
    )
    assert hook_to_record(hook).outcome == Outcome.REFUSED.value


# ── the event projection ──


def test_a_fire_counter_becomes_ONE_summary_row():
    """🔴 Deliberately one row for N fires. The store keeps `fire_count` + `last_fired_at` and
    nothing else, so N rows would mean N invented timestamps — a fabricated history is worse
    than an honest summary."""
    trigger = EventTrigger(
        id="e1",
        pattern="memory",
        action_provider="run-prompt",
        action_config={},
        fire_count=5,
        last_fired_at=NOW,
    )
    rec = event_trigger_to_record(trigger)
    assert rec is not None
    assert rec.counters == {"fire_count": 5}
    assert rec.incomplete is True
    assert "counter, not per-fire rows" in rec.reason


def test_an_event_summary_is_ledger_weight_not_a_run():
    """A reader or health rollup treating it as a run would double-count every fire behind it."""
    trigger = EventTrigger(
        id="e1", pattern="x", action_provider="p", action_config={}, fire_count=3, last_fired_at=NOW
    )
    assert event_trigger_to_record(trigger).weight == RunWeight.LEDGER.value


def test_an_event_trigger_that_never_fired_projects_NOTHING():
    trigger = EventTrigger(id="e1", pattern="x", action_provider="p", action_config={})
    assert event_trigger_to_record(trigger) is None


# ── the merged feed ──


def _feed():
    hook = ScriptHook(
        id="h1", name="fmt", event="Stop", run_count=4, last_run=NOW - 300, last_status="ok"
    )
    event = EventTrigger(
        id="e1",
        pattern="m",
        action_provider="p",
        action_config={},
        fire_count=5,
        last_fired_at=NOW - 600,
    )
    runs = [_run(run_id="r2", started_at=NOW - 30, status="failure", error="boom"), _run()]
    return unified_feed(schedule_runs=runs, hooks=[hook], event_triggers=[event])


def test_all_three_kinds_appear_in_one_feed():
    """The criterion, stated directly."""
    kinds = {r.trigger_id.split(":", 1)[0] for r in _feed()}
    assert kinds == {"schedule", "lifecycle", "event"}


def test_every_row_has_the_SAME_shape():
    """ "the same record shape" — asserted as one distinct key set across all three kinds."""
    shapes = {tuple(sorted(r.to_dict())) for r in _feed()}
    assert len(shapes) == 1


def test_every_row_has_a_typed_outcome():
    """ "typed outcomes" — no prose statuses leak through the projection."""
    assert all(r.outcome in FIRE_OUTCOMES for r in _feed())


def test_the_feed_is_newest_first():
    stamps = [r.started_at for r in _feed()]
    assert stamps == sorted(stamps, reverse=True)


def test_untimed_rows_sort_LAST():
    """An unparseable or absent time is not news; a feed leading with it would bury the run that
    just happened."""
    timed = _run(run_id="timed", started_at=NOW)
    untimed = _run(run_id="untimed", started_at=None, finished_at=None)
    feed = unified_feed(schedule_runs=[untimed, timed])
    assert feed[0].run_id == "timed"
    assert feed[-1].run_id == "untimed"


def test_the_limit_is_honoured():
    runs = [_run(run_id=f"r{i}", started_at=NOW - i) for i in range(30)]
    assert len(unified_feed(schedule_runs=runs, limit=5)) == 5


def test_an_empty_feed_is_an_empty_list_not_an_error():
    assert unified_feed() == []


def test_one_bad_row_does_not_empty_the_feed():
    """A projection that raised on a malformed record would take the whole feed with it."""

    class Exploding:
        @property
        def run_count(self):
            raise RuntimeError("boom")

    feed = unified_feed(schedule_runs=[_run()], hooks=[Exploding()])
    assert len(feed) == 1


# ── the wire shape ──


def test_the_response_names_which_kinds_and_how_many_are_summaries():
    payload = feed_response(_feed())
    assert payload["kinds"] == ["event", "lifecycle", "schedule"]
    assert payload["summaries"] == 2  # the hook's last-run row and the event counter
    assert payload["total"] == len(payload["runs"])


def test_outcome_counts_only_uses_the_typed_vocabulary():
    counts = outcome_counts(_feed())
    assert set(counts) <= set(FIRE_OUTCOMES)
    assert counts["ran"] >= 1


def test_outcome_counts_omits_zero_rows():
    """A caller renders the chips it gets; a wall of zeros is noise."""
    assert all(v > 0 for v in outcome_counts(_feed()).values())


# ── the live endpoint ──


@pytest.fixture
def app_with_all_kinds(tmp_path, monkeypatch):
    """A real app with a real hook store, a real event store, and a fake cron service.

    The cron service is faked because `ScheduleService.list_all_runs` reads a run store this test
    does not need to populate; the OTHER two are real, because their projections read fields whose
    names
    this session got wrong once already (`list_hooks` vs `list_all`).

    `PERSONALCLAW_HOME` is redirected because the handler's `_event_store()` resolves through
    `config_dir()`, NOT anything the fixture passes in — measured: without this the event rows
    silently vanished while every other assertion passed, because the fixture wrote to `tmp_path`
    and the handler read the real home.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    cfg = pathlib.Path(tmp_path)
    hooks = ScriptHookStore(config_dir=cfg)
    ran = hooks.create({"name": "fmt", "event": "PostToolUse", "provider": "run-prompt"})
    live = hooks.get(ran.id)
    # `update()`'s allowlist is CONFIG fields only — the runtime fields are written by `_fire`
    # (hooks.py:712), so a fixture has to set them the way the runtime does.
    live.run_count, live.last_run, live.last_status = 7, NOW - 300, "ok"
    hooks._save()
    hooks.create({"name": "never", "event": "Stop", "provider": "run-prompt"})

    events = EventTriggerStore(cfg / "event_triggers.json")
    events.save(
        [
            EventTrigger(
                id="e1",
                pattern="memory",
                action_provider="run-prompt",
                action_config={},
                fire_count=5,
                last_fired_at=NOW - 600,
            )
        ]
    )

    class FakeCrons:
        def list_jobs(self, include_disabled=False):
            return [
                ScheduleJob(
                    id="j1",
                    name="Nightly",
                    action=make_agent_action(message="x"),
                    schedule=ScheduleDefinition(kind="every", every_secs=3600),
                )
            ]

        async def list_all_runs(self, offset=0, limit=20, job_id=None):
            rows = [_run(run_id="r2", started_at=NOW - 30, status="failure", error="boom"), _run()]
            rows = [r for r in rows if not job_id or r["job_id"] == job_id]
            return rows[offset : offset + limit], len(rows)

    app = web.Application()

    class _State:
        pass

    state = _State()
    state.crons = FakeCrons()
    state._hook_store = hooks
    state._sessions = {}
    app["state"] = state
    T.register_trigger_routes(app)
    return app, ran.id


def _get(app, path):
    async def _run_it():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(path)
            return resp.status, await resp.json()

    return asyncio.run(_run_it())


def test_the_endpoint_returns_all_three_kinds(app_with_all_kinds):
    """🔴 The defect: this route was schedule-only, so the feed a user opens to answer "what did my
    machine do" showed one kind of automation and silently omitted the other two."""
    app, _hook_id = app_with_all_kinds
    status, body = _get(app, "/api/triggers/history?limit=20")
    assert status == 200
    assert body["kinds"] == ["event", "lifecycle", "schedule"]
    assert body["summaries"] == 2


def test_the_endpoint_rows_share_one_shape(app_with_all_kinds):
    app, _hook_id = app_with_all_kinds
    _status, body = _get(app, "/api/triggers/history")
    shapes = {tuple(sorted(row)) for row in body["runs"]}
    assert len(shapes) == 1


def test_the_endpoint_reports_the_schedule_total_separately(app_with_all_kinds):
    """Mixing the paginated schedule total with two summary rows would make the pager overshoot."""
    app, _hook_id = app_with_all_kinds
    _status, body = _get(app, "/api/triggers/history")
    assert body["schedule_total"] == 2
    assert body["total"] == len(body["runs"])


def test_the_legacy_shape_is_still_available(app_with_all_kinds):
    """The cron-history UI renders `trace`/`summary`, which the typed row does not carry."""
    app, _hook_id = app_with_all_kinds
    _status, body = _get(app, "/api/triggers/history?shape=legacy")
    assert "trace" in body["runs"][0]
    assert "kinds" not in body


def test_filtering_to_one_hook_does_not_pull_in_other_kinds(app_with_all_kinds):
    """A request for one trigger must not gain rows for every automation on the machine."""
    app, hook_id = app_with_all_kinds
    _status, body = _get(app, f"/api/triggers/history?trigger_id=lifecycle:{hook_id}")
    assert body["kinds"] == ["lifecycle"]
    assert len(body["runs"]) == 1


def test_filtering_to_a_schedule_excludes_hooks_and_events(app_with_all_kinds):
    app, _hook_id = app_with_all_kinds
    _status, body = _get(app, "/api/triggers/history?trigger_id=schedule:j1")
    assert body["kinds"] == ["schedule"]


def test_a_bad_limit_is_a_400(app_with_all_kinds):
    app, _hook_id = app_with_all_kinds
    status, _body = _get(app, "/api/triggers/history?limit=nope")
    assert status == 400


def test_the_endpoint_carries_typed_outcome_counts(app_with_all_kinds):
    app, _hook_id = app_with_all_kinds
    _status, body = _get(app, "/api/triggers/history")
    assert set(body["outcomes"]) <= set(FIRE_OUTCOMES)


def test_firerecord_is_now_actually_constructed():
    """It was exported and never constructed — the shape existed on paper and nothing produced it.
    This asserts the module that closed that gap really returns one."""
    from personalclaw.triggers.models import FireRecord

    assert isinstance(schedule_run_to_record(_run()), FireRecord)


# ── criterion 11: no credential reaches the feed (found while auditing S85's sibling) ──

CANARY = "sk-ant-api03-LEAKCANARY99887766554433"


def test_a_credential_in_a_run_error_never_reaches_the_reason():
    """🔴 Found by driving criterion 11 across every surface, in code written the same day.

    `reason` carries a schedule run's raw `error`/`summary`. A run that failed while printing a
    token put that token straight into the feed. The live endpoint happens to pre-redact via
    `_redact_run`, so the shipped path was safe — but these projections are PUBLIC functions,
    and a second caller passing raw store rows would leak. Defence belongs at this boundary, not
    in the caller.
    """
    rec = schedule_run_to_record(_run(status="failure", error=CANARY, summary=CANARY))
    assert CANARY not in rec.reason
    assert "REDACTED" in rec.reason


def test_a_credential_in_a_run_summary_is_redacted_too():
    rec = schedule_run_to_record(_run(status="success", error="", summary=f"used {CANARY}"))
    assert CANARY not in rec.reason


def test_a_timeout_reason_is_redacted_after_its_prefix_is_added():
    """The timeout branch REWRITES the reason, so redaction has to happen after that, not before."""
    rec = schedule_run_to_record(_run(status="timeout", error=f"Timed out holding {CANARY}"))
    assert CANARY not in rec.reason
    assert rec.reason.startswith("timed out:")


def test_a_credential_in_a_hook_status_is_redacted():
    hook = ScriptHook(
        id="h1", name="x", event="Stop", run_count=2, last_run=NOW, last_status=CANARY
    )
    assert CANARY not in hook_to_record(hook).reason


def test_no_projected_row_leaks_a_credential_into_the_feed():
    """The whole-feed assertion: one grep over the serialized rows, so a NEW field that forgets to
    redact fails here even if its own test does not exist yet."""
    import json

    hook = ScriptHook(
        id="h1", name="x", event="Stop", run_count=2, last_run=NOW, last_status=CANARY
    )
    feed = unified_feed(
        schedule_runs=[_run(status="failure", error=CANARY, summary=CANARY)], hooks=[hook]
    )
    assert CANARY not in json.dumps([r.to_dict() for r in feed])
