"""A rename must not rewrite the cadence, and the cadence floor warning must reach a surface (#531).

Two coupled defects, measured on `origin/main` against an isolated `PERSONALCLAW_HOME` with a real
gateway on `:10531` before any of this was written.

**One — the editor rewrote a cadence nobody edited.** `web/.../ScheduleForm.tsx` projects
`every_secs` into a `{value, unit}` display form on load (`toDraft`) and re-derives `body.every`
from
that display form on every save (`draftToPayload`), unconditionally. The pair was not invertible, so
a save that changed only the NAME changed the schedule::

    POST /api/triggers {"every": 5}                       -> 201, spec.interval_secs = 5
    PUT  /api/triggers/... {name: "…RENAMED", every: 60}   -> 200, spec.interval_secs = 60   (12x)
    POST /api/triggers {"every": 3601}                     -> 201, spec.interval_secs = 3601
    PUT  /api/triggers/... {name: "…RENAMED", every: 3600} -> 200, spec.interval_secs = 3600 (-1s)

The codec is fixed in `scheduleMeta.ts` and railed by `cadenceRoundTrip.test.ts`. What is railed
HERE
is the server half nobody had looked at: the same unconditional rebuild also **re-armed** the
trigger, because `_update_schedule` set `cadence_changed` on the PRESENCE of a key rather than on a
change of value, and the form sends `timezone` on every save. Measured: two consecutive renames of
one 3600s trigger, changing nothing but the name, moved `next_fire_at` 04:33:08 -> 04:34:21. A
trigger 59 minutes into an hourly cadence lost the hour, silently, on a cosmetic edit.

**Two — the warning that would have caught it was computed and thrown away.**
`models.validate_spec` raises a warning for any interval under `MIN_CLOCK_INTERVAL_SECS`, and its
own
comment promises "it fires, and it is visibly flagged". Measured on the trigger the create page
produces for Interval / 1 / minutes::

    row.warnings             ['60s is below the 900s floor for an LLM-invoking trigger; …']
    row.errors               []
    wire 'broken'            []            <- the projection passed row.errors ONLY
    'warnings' key on wire   False
    GET /api/triggers/doctor {"healthy": true, "findings": [], "count": 0}
    store.health()           {"warnings": 1, …}   <- and ZERO production callers

Three drops, so three assertions below: the wire, the doctor, and `health()` — which is deleted
rather than given a caller, because a count is strictly weaker than the per-row signal the other two
now carry, and inventing a consumer to justify a producer is this bug one level up.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers.models import MIN_CLOCK_INTERVAL_SECS
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home the handler reads for BOTH stores — the `test_triggers_facade_store` fixture."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


@pytest.fixture
def state(home):
    from unittest.mock import MagicMock

    st = MagicMock()
    st.crons.list_jobs.return_value = []
    st._hook_store = None
    return st


@pytest.fixture(autouse=True)
def _patch_legacy(monkeypatch):
    monkeypatch.setattr(T, "_hook_store", lambda s: _EmptyStore())
    monkeypatch.setattr(T, "_event_store", lambda: _EmptyStore())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})


class _EmptyStore:
    def list_all(self):
        return []

    def load(self):
        return []


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


def _create(state, *, name, every):
    resp = _run(
        T.api_trigger_create(
            _req(
                "POST",
                "/api/triggers",
                state,
                body={
                    "trigger_type": "schedule",
                    "name": name,
                    "every": every,
                    "action": {"provider": "invoke-agent", "config": {"task_template": "go"}},
                },
            )
        )
    )
    data = _body(resp)
    assert "error" not in data, data
    return data["trigger"]


def _spec(home, trigger_id):
    rows = json.loads((home / "triggers.json").read_text())["triggers"]
    return next(r for r in rows if r["id"] == trigger_id)


def _row_from_list(state, wire_id):
    data = _body(_run(T.api_triggers(_req("GET", "/api/triggers", state))))
    return next(t for t in data["triggers"] if t["id"] == wire_id)


# ── the wire carries the warning ──


def test_the_LIST_row_carries_the_sub_floor_WARNING(home, state):
    """🔴 DROP SITE 1. The projection passed `row.errors` only, so the wire had a `broken` key and no
    `warnings` key at all — for the exact trigger the create page's default (Interval / 1 / minutes)
    produces."""
    _create(state, name="fast-poll", every=60)
    row = _row_from_list(state, "schedule:clock:fast-poll")

    assert "warnings" in row, "the wire must have a place to put a warning at all"
    assert row["broken"] == [], "a sub-floor interval is not an ERROR — the trigger runs"
    assert row["warnings"], "…but it is not nothing either"
    assert str(MIN_CLOCK_INTERVAL_SECS) in row["warnings"][0]


def test_a_trigger_ABOVE_the_floor_warns_about_nothing(home, state):
    """The vacuity floor. "Always emit a warning" would pass the test above and make the badge noise
    on every row, which is how a real signal gets trained away."""
    _create(state, name="hourly", every=3600)
    row = _row_from_list(state, "schedule:clock:hourly")
    assert row["warnings"] == [] and row["broken"] == []


def test_the_floor_is_a_WARNING_and_the_create_still_succeeds(home, state):
    """R1 makes the floor overridable, so a 5-second cadence must persist verbatim. Measured on the
    live API before the fix and preserved here: the server never had a lower bound, and adding one
    would refuse a trigger the plan says to allow."""
    created = _create(state, name="five-seconds", every=5)
    assert created["every_secs"] == 5
    assert _spec(home, "clock:five-seconds")["spec"]["interval_secs"] == 5


def test_the_WRITE_response_carries_the_same_issues_as_the_list(home, state):
    """The create/update/toggle responses used to pass NO issues, so they answered `broken: []` and
    (later) `warnings: []` for a row the list reported on. Two shapes for one trigger is how a UI
    ends up with two ideas of it — the point of `_schedule_row_for` existing at all."""
    created = _create(state, name="fast-poll", every=60)
    listed = _row_from_list(state, "schedule:clock:fast-poll")
    assert created["warnings"] == listed["warnings"] != []
    assert created["broken"] == listed["broken"]

    updated = _body(
        _run(
            T.api_trigger_detail(
                _req(
                    "PUT",
                    "/api/triggers/schedule:clock:fast-poll",
                    state,
                    body={"name": "fast-poll renamed"},
                    match_info={"id": "schedule:clock:fast-poll"},
                )
            )
        )
    )["trigger"]
    assert updated["warnings"] == listed["warnings"]


def test_a_STORE_ONLY_kind_carries_warnings_too(home, state):
    """`_serialize_store` had the same drop, and there is no reason a `file` automation's advisory
    should be visible when a schedule's is not — one projection contract, both namespaces.

    The advisory used here is `validate_spec`'s unknown-key warning, not the interval floor: it is
    the one a store-only kind can actually carry, and it is the reason the fold is derived from
    `row.issues` instead of re-deriving a single rule. Asserting the MESSAGE rather than the
    presence
    of the key — a mutant that hard-coded `"warnings": []` survived the presence check.
    """
    (home / "triggers.json").write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {
                        "id": "file:notes",
                        "name": "Summarize notes",
                        "kind": "file",
                        # `path_glob` is not a `file` spec key (`paths` is), so the store keeps the
                        # row and warns — an automation watching nothing, with a diagnosis attached.
                        "spec": {"paths": ["~/notes/**"], "path_glob": "~/notes/**"},
                    }
                ],
            }
        )
    )
    row = _row_from_list(state, "store:file:notes")
    assert row["broken"] == []
    # Messages only, matching `broken`'s long-standing shape — the doctor is where a finding gains
    # its `spec.<field>` path, because that surface is a diagnosis and this one is a badge.
    assert row["warnings"] == ["file triggers do not use 'path_glob'"]


# ── the doctor sees it ──


def test_the_DOCTOR_reports_the_sub_floor_interval(home, state):
    """🔴 DROP SITE 2. `diagnose()` reads projected dicts and `semantic_spec_issues` owns the
    fire-path semantics; NEITHER re-runs `validate_spec`, so the doctor answered
    `healthy: true, findings: []` on a home holding a 60-second LLM-invoking trigger whose own
    `row.warnings` named the problem."""
    _create(state, name="fast-poll", every=60)
    report = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))

    assert report["healthy"] is False
    codes = {f["code"] for f in report["findings"]}
    assert "spec_warning" in codes, report["findings"]
    finding = next(f for f in report["findings"] if f["code"] == "spec_warning")
    assert finding["trigger_id"] == "schedule:clock:fast-poll", "must join back onto the list row"
    assert (
        "interval_secs" in finding["detail"] and str(MIN_CLOCK_INTERVAL_SECS) in finding["detail"]
    )
    assert finding["fix"], "a finding with no fix is a complaint"


def test_the_DOCTOR_stays_quiet_on_a_clean_home(home, state):
    """The doctor's silence has to mean something, or the assertion above is satisfied by a doctor
    that flags everything."""
    _create(state, name="hourly", every=3600)
    report = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert report["healthy"] is True and report["findings"] == []


def test_the_DOCTOR_reports_a_parse_ERROR_as_well(home, state):
    """The same fold covers `error` severity, which the doctor could not see either. Reported apart
    from the warning because the consequences differ: the store keeps this row and refuses to arm
    it, so the automation exists and cannot fire."""
    (home / "triggers.json").write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {
                        "id": "clock:broken",
                        "name": "broken",
                        "kind": "clock",
                        # A cron clock with no expression: `validate_spec` calls this an ERROR
                        # because the row cannot arm to anything at all.
                        "spec": {"kind": "cron"},
                    }
                ],
            }
        )
    )
    report = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert report["healthy"] is False
    assert "invalid_spec" in {f["code"] for f in report["findings"]}, report["findings"]


# ── health() is gone, not re-homed ──


def test_the_dead_store_SUMMARISER_is_gone(home):
    """🔴 DROP SITE 3. `store.health()` summed `r.warnings` across every row — the only function in
    the package that acknowledged them — and had zero production callers: measured across `src/`,
    the
    only two were in `tests/test_triggers_store.py`, testing it on itself.

    Deleted rather than given a caller. Its content is now live per row and by name through the two
    assertions above, and a count is strictly weaker than either. This asserts the deletion so
    nobody
    re-adds a third path for the same signal.
    """
    import personalclaw.triggers.store as store_mod

    assert not hasattr(store_mod, "health")


# ── a rename changes nothing but the name ──


def test_a_NAME_ONLY_edit_leaves_the_CADENCE_alone(home, state):
    """The server half of the round-trip defect: the exact body `draftToPayload` sends for a
    name-only edit, including the recomputed `every` the fixed codec now produces."""
    _create(state, name="odd-cadence", every=3601)
    assert _spec(home, "clock:odd-cadence")["spec"]["interval_secs"] == 3601

    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/schedule:clock:odd-cadence",
                state,
                body={
                    "name": "odd-cadence renamed",
                    "timezone": "",
                    "silent": False,
                    "strict_schedule": False,
                    "channel": "",
                    "skip_dates": [],
                    # `intervalToSecs(secsToInterval(3601))` — 3600 before the codec fix,
                    # 3601 after.
                    "every": 3601,
                    "message": "go",
                    "agent": "",
                    "model": "",
                    "approval_mode": "",
                },
                match_info={"id": "schedule:clock:odd-cadence"},
            )
        )
    )
    stored = _spec(home, "clock:odd-cadence")
    assert stored["name"] == "odd-cadence renamed"
    assert stored["spec"]["interval_secs"] == 3601


def test_a_NAME_ONLY_edit_does_not_RE_PHASE_the_schedule(home, state, monkeypatch):
    """🔴 The second, always-on half: `cadence_changed` fired on the PRESENCE of a key, and the form
    sends `timezone` on every save — so every cosmetic edit cleared `next_fire_at` and re-armed.

    The clock is DRIVEN, not slept on: `_arm_if_needed` computes the next fire from `time.time()`,
    so
    a wall-clock test would either be flaky or need a real delay. Advancing a fake clock by an hour
    between the create and the rename makes the re-arm unmissable — if the rename re-arms, the armed
    instant jumps by that hour.
    """
    import personalclaw.triggers.arm as arm_mod

    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(arm_mod.time, "time", lambda: clock["now"])

    _create(state, name="hourly", every=3600)
    armed_before = _spec(home, "clock:hourly")["next_fire_at"]
    assert armed_before, "the create must arm, or this test proves nothing"

    clock["now"] += 3600.0
    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/schedule:clock:hourly",
                state,
                body={
                    "name": "hourly renamed",
                    "timezone": "",
                    "silent": False,
                    "strict_schedule": False,
                    "channel": "",
                    "skip_dates": [],
                    "every": 3600,
                    "message": "go",
                    "agent": "",
                    "model": "",
                    "approval_mode": "",
                },
                match_info={"id": "schedule:clock:hourly"},
            )
        )
    )
    after = _spec(home, "clock:hourly")
    assert after["name"] == "hourly renamed"
    assert after["next_fire_at"] == armed_before, "a rename must not move the next fire"


def test_a_REAL_cadence_change_still_re_arms(home, state, monkeypatch):
    """The vacuity floor for the fix above. "Only re-arm on a change" is one line away from an
    editor
    whose new schedule never takes effect until the next restart — a strictly worse bug, because the
    user watches the number change and the machine keeps the old one."""
    import personalclaw.triggers.arm as arm_mod

    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(arm_mod.time, "time", lambda: clock["now"])

    _create(state, name="hourly", every=3600)
    armed_before = _spec(home, "clock:hourly")["next_fire_at"]

    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/schedule:clock:hourly",
                state,
                body={"name": "hourly", "every": 300},
                match_info={"id": "schedule:clock:hourly"},
            )
        )
    )
    # No clock advance needed here: 300s from the same instant is a different instant than 3600s.
    after = _spec(home, "clock:hourly")
    assert after["spec"]["interval_secs"] == 300
    assert after["next_fire_at"] != armed_before, "a new cadence must invalidate the armed fire"


def test_changing_the_TIMEZONE_still_re_arms(home, state, monkeypatch):
    """`timezone` is the key the form sends unconditionally, so it is the one this fix had to stop
    treating as a change — WITHOUT losing the case where it genuinely is one."""
    import personalclaw.triggers.arm as arm_mod

    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(arm_mod.time, "time", lambda: clock["now"])

    _create(state, name="hourly", every=3600)
    armed_before = _spec(home, "clock:hourly")["next_fire_at"]

    # The clock has to MOVE for a re-arm to be observable: an interval arms to `now + interval`, so
    # re-arming under a frozen clock lands on the same instant and would prove nothing either way.
    clock["now"] += 3600.0
    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/schedule:clock:hourly",
                state,
                body={"name": "hourly", "timezone": "America/Los_Angeles"},
                match_info={"id": "schedule:clock:hourly"},
            )
        )
    )
    after = _spec(home, "clock:hourly")
    assert after["spec"]["timezone"] == "America/Los_Angeles"
    assert after["next_fire_at"] != armed_before
