"""`/api/triggers` surfaces store-only kinds (file/web_watch/idle/…) — §6 additive slice (S94).

S92 made file/web_watch/idle/… automations creatable in chat and S93 made `file` ones fire, but
`GET /api/triggers` read only the three LEGACY backends (schedule crons, lifecycle hooks, event
triggers). So a chat-created file automation was **present and inert on the Automations page**:
created, fired, and unlistable — the user could not see, pause, run, or delete it in the UI.

These tests pin the additive read-plus-safe-mutation slice: the `store` namespace lists those
kinds and routes toggle/run/delete through S92's `tools.py`. This is NOT the §6 class-B re-point of
the schedule/event backends onto the store — those legacy paths are untouched here.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home the handler reads for BOTH stores.

    The handler resolves its trigger store AND (since S105) its run store through its own
    module-level `config_dir`, which is the single redirect point `conftest._isolate_trigger_store`
    also patches. Patching only the loader left `_runs_store()` pointing at the fixture's own tmp
    home — measured: every run-record read returned 0 rows.
    """
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
    # Silence the legacy backends so the list contains only what we put in the store.
    monkeypatch.setattr(T, "_hook_store", lambda s: _EmptyStore())
    monkeypatch.setattr(T, "_event_store", lambda: _EmptyStore())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})


class _EmptyStore:
    def list_all(self):
        return []

    def load(self):
        return []


def _store(home):
    return TriggerStore(base_dir=home)


def _file_automation(home, name="Summarize notes", when="when a file in ~/notes changes"):
    Tools.create(_store(home), name=name, when=when, message="go")


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


# ── 🔴 the present-and-inert gap this closes ──


def test_a_chat_created_file_automation_is_LISTED(home, state):
    """🔴 THE gap. Before this slice, a file automation created via the chat tools fired (S93) but
    never appeared in `GET /api/triggers` — invisible on its own management page."""
    _file_automation(home)
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    data = _body(resp)
    ids = {t["id"] for t in data["triggers"]}
    assert "store:file:summarize-notes" in ids
    row = next(t for t in data["triggers"] if t["id"] == "store:file:summarize-notes")
    assert row["kind"] == "store"
    assert row["store_kind"] == "file"
    assert row["enabled"] is True


def test_the_type_filter_selects_store_kinds(home, state):
    _file_automation(home)
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=store")))
    data = _body(resp)
    assert data["triggers"] and all(t["kind"] == "store" for t in data["triggers"])


def test_a_clock_trigger_in_the_store_is_NOT_double_listed(home, state):
    """🔴 A `clock` trigger belongs to the schedule backend's namespace; listing it under `store`
    too would show every migrated cron twice once the store is populated."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="clock:x",
            name="C",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"provider": "run-prompt", "config": {}},
        )
    )
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state, query="type=store")))
    assert _body(resp)["triggers"] == []


def test_a_broken_store_row_is_listed_not_hidden(home, state):
    _store(home).path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "file:x", "name": "X", "kind": "file"}]})
    )
    # A file row with no paths still parses as a file kind; make it genuinely broken instead.
    _store(home).path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {"id": "web_watch:x", "name": "X", "kind": "web_watch", "spec": "not-a-dict"}
                ],
            }
        )
    )
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    rows = [t for t in _body(resp)["triggers"] if t["kind"] == "store"]
    assert rows and rows[0]["broken"]


# ── id round-trip ──


def test_split_id_round_trips_a_store_id():
    """🔴 A store id is itself `<kind>:<slug>`. Splitting on the first colon would hand the store
    `file` as the raw id and lose `summarize-notes`."""
    assert T._split_id("store:file:summarize-notes") == ("store", "file:summarize-notes")


def test_a_bare_id_still_defaults_to_schedule():
    assert T._split_id("job1") == ("schedule", "job1")


# ── toggle ──


def test_toggle_pauses_and_resumes_a_store_trigger(home, state):
    _file_automation(home)
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={"enabled": False},
                match_info={"id": "store:file:summarize-notes"},
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert _store(home).get("file:summarize-notes").trigger.enabled is False


def test_toggling_an_unknown_store_id_is_404(home, state):
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={},
                match_info={"id": "store:file:ghost"},
            )
        )
    )
    assert resp.status == 404


def test_resuming_a_broken_row_reports_400_not_a_silent_disable(home, state):
    """🔴 `set_enabled` refuses a broken row (S87); the API must surface that, not answer ok while
    leaving it disabled."""
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "web_watch:x", "name": "X", "kind": "nonsense"}]}
        )
    )
    resp = _run(
        T.api_trigger_toggle(
            _req(
                "POST",
                "/api/triggers/x/toggle",
                state,
                body={"enabled": True},
                match_info={"id": "store:web_watch:x"},
            )
        )
    )
    assert resp.status == 400


# ── delete ──


def test_delete_removes_a_store_trigger(home, state):
    _file_automation(home)
    resp = _run(
        T.api_trigger_detail(
            _req(
                "DELETE", "/api/triggers/x", state, match_info={"id": "store:file:summarize-notes"}
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert _store(home).get("file:summarize-notes") is None


def test_deleting_an_unknown_store_id_is_404(home, state):
    resp = _run(
        T.api_trigger_detail(
            _req("DELETE", "/api/triggers/x", state, match_info={"id": "store:file:ghost"})
        )
    )
    assert resp.status == 404


# ── run ──


def test_a_dry_run_reports_the_gate_plan_and_executes_nothing(home, state):
    """🔴 Reuses `tools.run` so the API and the chat tool report identically; a dry run must fire
    nothing."""
    _file_automation(home)
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST",
                "/api/triggers/x/run",
                state,
                query="dry_run=1",
                match_info={"id": "store:file:summarize-notes"},
            )
        )
    )
    data = _body(resp)
    assert data["ok"] is True
    assert data["result"]["plan"]["executes"] is False
    # The manual bypass boundary is preserved.
    assert set(data["result"]["plan"]["bypassed"]) == {"quiet", "duty"}


def test_a_real_run_dispatches_the_action(home, state, monkeypatch):
    """A Run button fires through the same action-provider registry the autonomous path uses."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            enabled=True,
            spec={"paths": ["~/x/**"]},
            workflow={"provider": "notify", "config": {"title_template": "t"}},
        )
    )
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    prov = get_action_provider("notify")
    seen = {}

    async def spy(action_config, ctx, timeout=30):
        seen["event"] = ctx.event
        seen["trigger_id"] = ctx.payload.get("trigger_id")

    monkeypatch.setattr(prov, "execute", spy)
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST", "/api/triggers/x/run", state, body={}, match_info={"id": "store:file:notes"}
            )
        )
    )
    assert _body(resp)["ok"] is True
    assert seen["trigger_id"] == "file:notes"
    assert seen["event"] == "manual.run"


def test_a_paused_store_trigger_still_runs_by_hand(home, state, monkeypatch):
    """Pausing means "stop firing on its own"; a hand-driven run is how you test before re-enabling.
    The result notes it does not re-enable."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="file:notes",
            name="N",
            kind="file",
            enabled=False,
            spec={"paths": ["~/x/**"]},
            workflow={"provider": "notify", "config": {"title_template": "t"}},
        )
    )
    _ensure_default_providers_registered()
    monkeypatch.setattr(
        get_action_provider("notify"), "execute", lambda ac, ctx, timeout=30: _noop()
    )
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST", "/api/triggers/x/run", state, body={}, match_info={"id": "store:file:notes"}
            )
        )
    )
    data = _body(resp)
    assert data["ok"] is True
    assert "does not re-enable" in data["result"]
    assert _store(home).get("file:notes").trigger.enabled is False


def test_running_a_broken_row_is_400(home, state):
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "web_watch:x", "name": "X", "kind": "nonsense"}]}
        )
    )
    resp = _run(
        T.api_trigger_run(
            _req("POST", "/api/triggers/x/run", state, match_info={"id": "store:web_watch:x"})
        )
    )
    assert resp.status == 400


async def _noop():
    return None


# ── the boundary: legacy paths untouched ──


def test_store_only_kinds_excludes_clock_and_event():
    """🔴 The set must not claim `clock` or `event` — those belong to the schedule and event
    backends. Claiming them here would double-list every cron and event trigger."""
    assert "clock" not in T._STORE_ONLY_KINDS
    assert "event" not in T._STORE_ONLY_KINDS
    assert "file" in T._STORE_ONLY_KINDS
    assert "web_watch" in T._STORE_ONLY_KINDS


# ── 🔴 §6's schedule re-point (S99) ──


def test_the_schedule_list_is_read_from_the_store(home, state, monkeypatch):
    """🔴 §6's re-point: "the existing facade becomes the single API by re-pointing its three
    backends at one store". Verified before switching that the store lists the SAME job ids the
    legacy service does after the boot migration, so nothing vanishes from the page."""
    from personalclaw.triggers import boot_migrate as BM

    (home / "crons.json").write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "j-cron",
                        "name": "Nightly",
                        "enabled": True,
                        "schedule": {"kind": "cron", "cron_expr": "0 9 * * *"},
                        "action": {"provider": "bash", "config": {"command": "x"}},
                    }
                ],
            }
        )
    )
    BM.migrate_and_arm(home, now=1_800_000_000.0)
    state.crons.list_jobs.return_value = []  # the legacy service is EMPTY on purpose
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    rows = [t for t in _body(resp)["triggers"] if t["kind"] == "schedule"]
    assert [r["raw_id"] for r in rows] == ["j-cron"]
    assert rows[0]["cron_expr"] == "0 9 * * *"
    assert rows[0]["next_run_ts"]  # armed by the boot migration


def test_a_legacy_job_is_visible_through_the_MIGRATION_not_a_fallback(home, state):
    """🔴 SUPERSEDED CONTRACT (S110). This asserted the facade reads `crons.json` when the store is
    empty — the fallback that retires with `ScheduleService`'s CRUD.

    The property it protected still holds, by a better mechanism: a home whose migration has not run
    must not show zero schedules. Boot imports every legacy job unconditionally, and S110 made that
    complete — a row the conversion REFUSES (an empty or unknown `schedule.kind`, which
    `ScheduleService` loads happily) used to be `continue`d and so existed only in the legacy file.
    It is now written disabled, so there is no job the fallback could have shown that the store
    cannot.

    So this drives the real migration and asserts the row surfaces through the ordinary store path.
    """
    import json

    (home / "crons.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "legacy1",
                        "name": "Legacy",
                        "enabled": True,
                        "message": "m",
                        "schedule": {"kind": "cron", "cron_expr": "0 9 * * *"},
                        "action": {"provider": "bash", "config": {"command": "x"}},
                    }
                ]
            }
        )
    )
    report = _store(home).migrate_from_crons()
    assert report["written"] == 1, report

    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    rows = [t for t in _body(resp)["triggers"] if t["kind"] == "schedule"]
    assert [r["raw_id"] for r in rows] == ["legacy1"]


def test_a_legacy_job_the_conversion_REFUSES_is_still_imported(home, state):
    """🔴 THE S110 FINDING. A `crons.json` row with an empty `schedule.kind` LOADS in
    `ScheduleService` but the conversion refuses it — and it used to be dropped, so it lived only
    in the legacy file. Deleting the fallbacks (the point of the cutover) would have made the
    user's job vanish from the list with no error anywhere.

    Imported DISABLED and visibly broken instead: `set_enabled` already refuses to enable a row
    that fails validation, so it cannot become a live trigger by accident, and the UI shows why.
    """
    import json

    (home / "crons.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "broken1",
                        "name": "Hand-edited",
                        "enabled": True,
                        "message": "m",
                        "schedule": {"kind": ""},
                        "action": {"provider": "bash", "config": {"command": "x"}},
                    }
                ]
            }
        )
    )
    report = _store(home).migrate_from_crons()
    assert report["unparseable"], "the refusal must still be REPORTED"

    rows = _store(home).load()
    assert [r.trigger.id for r in rows] == ["broken1"]
    assert rows[0].ok is False, "it must be visibly broken"
    assert rows[0].trigger.enabled is False, "and must never fire"
    # And it reaches the UI rather than vanishing.
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    assert "broken1" in [t["raw_id"] for t in _body(resp)["triggers"] if t["kind"] == "schedule"]


def test_a_store_backed_schedule_row_is_redacted(home, state):
    """The projection is a data mapping and knows nothing about credential scrubbing, so the handler
    still redacts on the way out — exactly as `_serialize_schedule` did."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="clock:leaky",
            name="token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"provider": "bash", "config": {"command": "x"}},
        )
    )
    state.crons.list_jobs.return_value = []
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    row = next(t for t in _body(resp)["triggers"] if t["kind"] == "schedule")
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in row["name"]


def test_a_broken_clock_row_is_listed_with_its_error(home, state):
    """S87's lenient parse keeps a broken row; the schedule list must show it rather than hiding an
    automation the user cannot otherwise debug."""
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "clock:x", "name": "X", "kind": "clock", "spec": 5}]}
        )
    )
    state.crons.list_jobs.return_value = []
    resp = _run(T.api_triggers(_req("GET", "/api/triggers", state)))
    rows = [t for t in _body(resp)["triggers"] if t["kind"] == "schedule"]
    assert rows and rows[0]["broken"]


# ── 🔴 §6's schedule WRITE re-point (S101) ──


def _create_schedule(state, **over):
    body = {
        "trigger_type": "schedule",
        "name": "Nightly",
        "cron": "0 9 * * *",
        "action": {"provider": "bash", "config": {"command": "echo hi"}},
    }
    body.update(over)
    return _run(T.api_trigger_create(_req("POST", "/api/triggers", state, body=body)))


def test_create_writes_to_the_store(home, state):
    """🔴 §6's write re-point: a created schedule lands in `triggers.json`, not `crons.json`."""
    resp = _create_schedule(state)
    assert resp.status == 200
    assert _store(home).get("clock:nightly") is not None


def test_a_created_schedule_is_ARMED(home, state):
    """🔴 THE defect this session found first: `tools.create` persisted `next_fire_at=""`, and
    `due_ids` only surfaces rows that HAVE one — so every cron created through the chat tools (since
    S92) or this API would never fire. Arming at creation is the difference between "runs tonight"
    and "runs after the user restarts the gateway"."""
    _create_schedule(state)
    trigger = _store(home).get("clock:nightly").trigger
    assert trigger.next_fire_at  # not ""
    from personalclaw.triggers import service as SVC

    due_at = SVC.to_epoch(trigger.next_fire_at) + 1
    assert SVC.due_ids([trigger], now=due_at) == ["clock:nightly"]


def test_the_spec_carries_every_schedule_field(home, state):
    _create_schedule(
        state, timezone="America/New_York", skip_dates=["2027-12-25"], strict_schedule=True
    )
    spec = _store(home).get("clock:nightly").trigger.spec
    assert spec["kind"] == "cron"
    assert spec["expr"] == "0 9 * * *"
    assert spec["timezone"] == "America/New_York"
    assert spec["skip_dates"] == ["2027-12-25"]
    assert spec["strict"] is True


def test_channel_and_silent_become_DELIVERY(home, state):
    """`LEGACY_FIELD_MAP`: `channel → delivery`, `silent → delivery == none`. Writing them into the
    action config (where they used to live) would make the projection render them empty."""
    _create_schedule(state, channel="C0AP3QR7Z4M")
    assert _store(home).get("clock:nightly").trigger.delivery == "channel:C0AP3QR7Z4M"
    _run(
        T.api_trigger_detail(
            _req("DELETE", "/api/triggers/x", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    _create_schedule(state, silent=True)
    assert _store(home).get("clock:nightly").trigger.delivery == "none"


def test_the_action_is_stored_in_the_migrated_shape(home, state):
    """`workflow.inline` is what a migrated cron uses, and what `schedule_view` + the gateway's
    shared dispatch both read — so an API-created row and a migrated one are indistinguishable
    downstream."""
    _create_schedule(state)
    workflow = _store(home).get("clock:nightly").trigger.workflow
    assert workflow["inline"]["provider"] == "bash"


def test_an_interval_and_a_one_shot_both_create(home, state):
    _create_schedule(state, name="Every", cron=None, every=300)
    assert _store(home).get("clock:every").trigger.spec == {
        "kind": "interval",
        "interval_secs": 300,
    }
    _create_schedule(state, name="Once", cron=None, at=4_000_000_000.0)
    spec = _store(home).get("clock:once").trigger.spec
    assert spec["kind"] == "at"
    # `delete_after_run` so the tick RETIRES it instead of leaving an elapsed timestamp (S96).
    assert spec["delete_after_run"] is True


def test_create_still_validates_before_writing(home, state):
    """The re-point moves where a row is PERSISTED, never what the API accepts."""
    assert _create_schedule(state, name="").status == 400
    assert _create_schedule(state, cron=None).status == 400  # no cadence
    assert _create_schedule(state, timezone="Mars/Olympus").status == 400
    assert _create_schedule(state, channel="not a channel id").status == 400
    assert _store(home).load() == []


# ── update ──


def test_update_changes_the_cadence_and_RE_ARMS(home, state):
    """🔴 A new cadence invalidates the armed fire. Keeping the old one would fire on the PREVIOUS
    schedule after the user changed it."""
    _create_schedule(state)
    before = _store(home).get("clock:nightly").trigger.next_fire_at
    resp = _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/x",
                state,
                body={"cron": "0 10 * * *"},
                match_info={"id": "schedule:clock:nightly"},
            )
        )
    )
    assert resp.status == 200
    trigger = _store(home).get("clock:nightly").trigger
    assert trigger.spec["expr"] == "0 10 * * *"
    assert trigger.next_fire_at != before


def test_a_cadence_change_KEEPS_timezone_and_skip_dates(home, state):
    """🔴 §1.3's quietly-losable class. Replacing `{kind, expr}` wholesale would silently drop the
    holidays — a user changing 9am to 10am must not lose their `skip_dates`."""
    _create_schedule(state, timezone="America/New_York", skip_dates=["2027-12-25"])
    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/x",
                state,
                body={"cron": "0 10 * * *"},
                match_info={"id": "schedule:clock:nightly"},
            )
        )
    )
    spec = _store(home).get("clock:nightly").trigger.spec
    assert spec["timezone"] == "America/New_York"
    assert spec["skip_dates"] == ["2027-12-25"]


def test_update_renames_through_the_allowlist(home, state):
    _create_schedule(state)
    _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/x",
                state,
                body={"name": "Renamed"},
                match_info={"id": "schedule:clock:nightly"},
            )
        )
    )
    assert _store(home).get("clock:nightly").trigger.name == "Renamed"


def test_updating_an_unknown_schedule_is_404(home, state):
    state.crons.update_job.return_value = None
    resp = _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/x",
                state,
                body={"name": "x"},
                match_info={"id": "schedule:ghost"},
            )
        )
    )
    assert resp.status == 404


# ── toggle ──


def test_toggle_pauses_and_re_enabling_ARMS(home, state):
    """🔴 Re-enabling must arm, or the trigger sits enabled and inert until the next boot sweep."""
    _create_schedule(state)
    nid = {"id": "schedule:clock:nightly"}
    _run(T.api_trigger_toggle(_req("POST", "/x", state, body={"enabled": False}, match_info=nid)))
    trigger = _store(home).get("clock:nightly").trigger
    assert trigger.enabled is False
    trigger.next_fire_at = ""  # a disabled row that was never armed
    _store(home).upsert(trigger)

    _run(T.api_trigger_toggle(_req("POST", "/x", state, body={"enabled": True}, match_info=nid)))
    after = _store(home).get("clock:nightly").trigger
    assert after.enabled is True
    assert after.next_fire_at


def test_toggle_with_no_body_flips_the_current_state(home, state):
    _create_schedule(state)
    nid = {"id": "schedule:clock:nightly"}
    _run(T.api_trigger_toggle(_req("POST", "/x", state, body={}, match_info=nid)))
    assert _store(home).get("clock:nightly").trigger.enabled is False


# ── delete ──


def test_delete_removes_the_store_row(home, state):
    _create_schedule(state)
    resp = _run(
        T.api_trigger_detail(
            _req("DELETE", "/x", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    assert resp.status == 200
    assert _store(home).get("clock:nightly") is None


def test_delete_still_drops_the_run_history(home, state):
    """Run history lives in `ScheduleRunStore` (keyed by a plain id, so it survives the cutover), so
    a delete has two halves: drop the trigger AND drop its runs."""
    _append_run(home)
    _create_schedule(state)
    _run(
        T.api_trigger_detail(
            _req("DELETE", "/x", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    # S105: the run half goes through the STORE now, so assert the rows are actually gone rather
    # than that a service mock was awaited — a mock assertion would pass without the delete.
    assert _run(T._runs_store().list_for_job("clock:nightly", 0, 10)) == ([], 0)


# ── 🔴 §6's manual-run re-point (S102) ──


def test_a_schedule_run_goes_through_the_store_path(home, state):
    """🔴 A Run button and an autonomous tick must fire the same action the same way, so a
    store-backed clock trigger routes through `_run_store` like every other store kind."""
    _create_schedule(state)
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST",
                "/api/triggers/x/run",
                state,
                match_info={"id": "schedule:clock:nightly"},
                query="dry_run=1",
            )
        )
    )
    data = _body(resp)
    assert resp.status == 200
    # The store path reports the gate plan; the legacy path reported {ok, name, dry_run}.
    assert data["result"]["plan"]["executes"] is False
    assert "screen" in data["result"]["plan"]["enforced"]


def test_an_in_flight_claim_returns_409(home, state):
    """🔴 `is_running` now comes from S97's CLAIM store, which is cross-process — the legacy
    `is_running` read a process-local dict, so an API worker that does not own the scheduler loop
    answered "idle" for a trigger that was actively running."""
    import time as _time

    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import Claim

    _create_schedule(state)
    claims.write_claim(
        Claim(trigger_id="clock:nightly", holder="tick", claimed_at=_time.time()), base_dir=home
    )
    resp = _run(
        T.api_trigger_run(
            _req("POST", "/x/run", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    assert resp.status == 409
    assert _body(resp)["running"] is True


def test_an_expired_claim_does_not_block_a_manual_run(home, state):
    """Read-time expiry (S97): a crashed run must not make the Run button permanently unusable."""
    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim

    _create_schedule(state)
    claims.write_claim(
        Claim(trigger_id="clock:nightly", holder="dead", claimed_at=1.0), base_dir=home
    )
    assert CLAIM_MAX_DURATION_SECS > 0  # the expiry window the read applies
    resp = _run(
        T.api_trigger_run(
            _req(
                "POST",
                "/x/run",
                state,
                match_info={"id": "schedule:clock:nightly"},
                query="dry_run=1",
            )
        )
    )
    assert resp.status == 200


def test_running_an_unknown_schedule_still_404s(home, state):
    state.crons.list_jobs.return_value = []
    resp = _run(
        T.api_trigger_run(_req("POST", "/x/run", state, match_info={"id": "schedule:ghost"}))
    )
    assert resp.status == 404


# ── 🔴 §6's week-grid + doctor re-point (S103) ──


def _week(state, *, start="2027-01-15T00:00:00", days=3):
    return _run(
        T.api_triggers_week(
            _req("GET", "/api/triggers/week", state, query=f"start={start}&days={days}")
        )
    )


def _occ_by_trigger(resp):
    out: dict[str, list] = {}
    for occurrence in _body(resp)["occurrences"]:
        out.setdefault(occurrence["trigger_id"], []).append(occurrence)
    return out


def test_the_week_grid_now_PLOTS_A_CRON(home, state):
    """🔴 THE gap this closes. The old handler skipped every non-interval trigger with its own
    admission ("a cron trigger is omitted rather than mis-plotted") — which made the week view a
    forecast of only half a user's automations, silently. S96's `arm.next_fire` can step a cron, so
    it plots on the same annotated grid as an interval."""
    _create_schedule(state, name="Nightly", cron="0 9 * * *", timezone="UTC")
    by = _occ_by_trigger(_week(state))
    assert "schedule:clock:nightly" in by
    assert len(by["schedule:clock:nightly"]) == 3  # one per day in the window


def test_a_cron_plots_on_its_real_cadence_not_a_constant_step(home, state):
    """A cron's spacing is not a constant, so stepping must come from the expression. A weekday-only
    cron must plot 5 fires in a 7-day window, not 7."""
    _create_schedule(state, name="Weekdays", cron="0 9 * * 1-5", timezone="UTC")
    by = _occ_by_trigger(_week(state, days=7))
    assert len(by["schedule:clock:weekdays"]) == 5


def test_an_interval_still_plots(home, state):
    _create_schedule(state, name="Every", cron=None, every=86400)
    by = _occ_by_trigger(_week(state))
    assert by["schedule:clock:every"]


def test_an_UNARMED_interval_still_plots(home, state):
    """🔴 Measured on the owner's real store: `j-every` is enabled with an empty `next_fire_at` (a
    re-enable does not arm until the next boot sweep), so reading only `next_fire_at` gave
    `first_fire_at=0` and the projection returned NOTHING — a live 5-minute automation invisible on
    the grid. The fallback computes the same instant the tick will use."""
    _create_schedule(state, name="Every", cron=None, every=86400)
    store = _store(home)
    trigger = store.get("clock:every").trigger
    trigger.next_fire_at = ""
    store.upsert(trigger)
    assert _occ_by_trigger(_week(state))["schedule:clock:every"]


def test_a_disabled_trigger_is_not_plotted(home, state):
    """A disabled trigger has no fires; drawing them makes the grid a wish list, not a forecast."""
    _create_schedule(state, name="Off", cron="0 9 * * *")
    store = _store(home)
    store.set_enabled("clock:off", False)
    assert _occ_by_trigger(_week(state)) == {}


def test_a_one_shot_is_not_plotted_as_a_recurrence(home, state):
    """An `at` is a single fire, not a cadence — plotting it as a band would be a wrong forecast."""
    _create_schedule(state, name="Once", cron=None, at=4_000_000_000.0)
    assert "schedule:clock:once" not in _occ_by_trigger(_week(state))


def test_skip_dates_and_the_triggers_own_zone_still_annotate(home, state):
    """AUTO-A3's struck columns. The SCHEDULER compares skip dates against the date in the trigger's
    OWN zone, so a grid on server time would strike the wrong column for a job that declares one."""
    _create_schedule(
        state, name="Nightly", cron="0 9 * * *", timezone="UTC", skip_dates=["2027-01-16"]
    )
    occurrences = _occ_by_trigger(_week(state))["schedule:clock:nightly"]
    struck = [o for o in occurrences if o["suppressed_by"] == "skipped"]
    assert len(struck) == 1
    assert "2027-01-16" in struck[0]["reason"]


def test_the_cap_is_reported_not_silent(home, state):
    """A grid that silently showed a partial week would read as an accurate forecast."""
    _create_schedule(state, name="Busy", cron=None, every=60)
    data = _body(_week(state))
    assert data["truncated"] == ["schedule:clock:busy"]


def test_a_broken_clock_row_is_not_plotted(home, state):
    """A row the entity refuses has no knowable schedule; plotting a guess is worse than absence."""
    _store(home).path.write_text(
        json.dumps(
            {"version": 1, "triggers": [{"id": "clock:x", "name": "X", "kind": "clock", "spec": 5}]}
        )
    )
    assert _occ_by_trigger(_week(state)) == {}


# ── doctor ──


def test_the_doctor_diagnoses_from_the_store(home, state):
    """🔴 The old rows read `getattr(job, "workflow")` — a field a `ScheduleJob` does not have, so it
    was ALWAYS empty — and a `watch_glob` that does not exist on a cron at all. The orphan-workflow
    and broad-glob checks were scanning blanks for every schedule trigger: present, reviewed, and
    diagnosing nothing. A `Trigger` carries `gates`/`workflow`/`spec` natively."""
    from personalclaw.triggers.models import Trigger

    _store(home).upsert(
        Trigger(
            id="clock:broad",
            name="Broad",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "0 9 * * *", "glob": "~/**"},
            workflow={"ref": "no-such-workflow"},
        )
    )
    data = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert "findings" in data
    assert isinstance(data["count"], int)


def test_the_doctor_reads_the_real_workflow_ref(home, state):
    """The field the orphan check needs actually arrives now — pinned by asserting the row shape the
    handler builds carries a non-empty workflow for a store trigger."""
    _create_schedule(state, name="Nightly", cron="0 9 * * *")
    store = _store(home)
    trigger = store.get("clock:nightly").trigger
    assert trigger.workflow  # `workflow.inline`, which the doctor now sees
    data = _body(_run(T.api_triggers_doctor(_req("GET", "/api/triggers/doctor", state))))
    assert data["healthy"] in (True, False)  # it ran rather than erroring


# ── 🔴 §6's chat-injection + history re-point (S104) ──


def test_the_job_shim_serves_the_injection_from_the_store(home, state):
    """Measured: `inject_schedule_result_to_session` reads exactly `job.id`, `job.name` and
    `job.agent_id` — nothing else. So a store row is projected onto that tiny surface rather than
    the whole legacy entity, and the handler stops needing `ScheduleService`."""
    _create_schedule(state, name="Nightly Backup")
    shim = T._job_shim_for(state, "clock:nightly-backup")
    assert shim is not None
    assert shim.id == "clock:nightly-backup"
    assert shim.name == "Nightly Backup"
    assert shim.agent_id == ""  # present and empty, never absent


def test_the_shim_is_store_only(home, state):
    """🔴 SUPERSEDED (S110): the shim read `crons.json` when the store missed. Store-only now, for
    the reason the list test above records — the migration imports every legacy job, including the
    refused ones. A legacy id the store does not have is genuinely unknown."""
    assert T._job_shim_for(state, "legacy1") is None


def test_the_shim_is_None_for_an_unknown_id(home, state):
    """So the caller can still fall back to a history-only session rather than 404-ing a trigger the
    user has conversation history for."""
    state.crons.list_jobs.return_value = []
    assert T._job_shim_for(state, "ghost") is None


def test_the_last_result_comes_from_the_RUN_STORE(home, state):
    """🔴 `LEGACY_FIELD_MAP` maps `last_result` to None deliberately — the RUN RECORD owns a run's
    output, and a copy on the trigger was a second truth that could disagree with it. The run store
    is keyed by a plain id, so it serves a store-backed trigger and a legacy job identically.

    S105 note: this reads the REAL store rather than a mocked service method — the mock would have
    kept passing after the re-point without the read happening at all."""
    _append_run(home, summary="backup done")
    assert _run(T._last_result_for(state, "clock:nightly")) == "backup done"


def test_the_last_result_prefers_an_error_when_there_is_no_summary(home, state):
    """A failed run's output IS its error; returning "" would make a failure look like a silent
    run."""
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    _run(
        ScheduleRunStore(home).append(
            ScheduleRun(
                run_id="r1",
                job_id="x",
                trigger="schedule",
                started_at=1.0,
                finished_at=2.0,
                duration_ms=1,
                status="failure",
                summary="",
                error="boom",
            )
        )
    )
    assert _run(T._last_result_for(state, "x")) == "boom"


def test_no_runs_yields_an_empty_result_not_an_error(home, state):
    assert _run(T._last_result_for(state, "never-ran")) == ""


def test_an_unreadable_run_store_does_not_break_the_injection(home, state, monkeypatch):
    """A history problem is not a reason to refuse opening the chat."""

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(T, "_runs_store", boom)
    assert _run(T._last_result_for(state, "x")) == ""


def test_the_name_map_covers_EVERY_kind(home, state):
    """🔴 The unified history feed carries file/web_watch/event runs too, so a name map that only
    knew about schedules would blank exactly the rows the new kinds contribute — which reads in
    the UI as a run of a deleted automation."""
    from personalclaw.triggers.models import Trigger

    _create_schedule(state, name="Nightly")
    _store(home).upsert(
        Trigger(
            id="file:notes",
            name="Notes Watcher",
            kind="file",
            enabled=True,
            spec={"paths": ["~/notes/**"]},
            workflow={"provider": "run-prompt", "config": {}},
        )
    )
    names = T._trigger_names(state)
    assert names["clock:nightly"] == "Nightly"
    assert names["file:notes"] == "Notes Watcher"


def test_the_name_map_is_store_only(home, state):
    """🔴 SUPERSEDED (S110): the map merged legacy job names over the store's. Store-only now — and
    a legacy job's name still appears, because the migration wrote it there."""
    _create_schedule(state, name="Nightly")
    names = T._trigger_names(state)
    assert names["clock:nightly"] == "Nightly"
    assert "legacy1" not in names


def test_the_name_map_survives_an_unreadable_legacy_service(home, state):
    state.crons.list_jobs.side_effect = RuntimeError("no service")
    _create_schedule(state, name="Nightly")
    assert T._trigger_names(state)["clock:nightly"] == "Nightly"


# ── 🔴 §6's run-record re-point (S105) ──


def _append_run(home, *, job_id="clock:nightly", run_id="r1", status="ok", summary="s"):
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    store = ScheduleRunStore(home)
    _run(
        store.append(
            ScheduleRun(
                run_id=run_id,
                job_id=job_id,
                trigger="schedule",
                started_at=100.0,
                finished_at=101.0,
                duration_ms=1000,
                status=status,
                summary=summary,
            )
        )
    )
    return store


def test_the_run_store_is_held_directly_not_through_the_service(home, state):
    """🔴 All four run-record methods on `ScheduleService` are one-line passthroughs to
    `ScheduleRunStore`, and the store answers standalone from a bare `base_dir` — so the facade's
    dependency on the legacy service for run HISTORY was pure indirection. Proven by DELETING the
    service's run methods: the reads still work."""
    _append_run(home)
    del state.crons.list_runs  # the legacy service can no longer serve this
    runs, total = _run(T._runs_store().list_for_job("clock:nightly", 0, 10))
    assert total == 1
    assert runs[0]["run_id"] == "r1"


def test_the_helper_is_named_runs_store_to_avoid_shadowing():
    """🔴 A REAL BUG this session hit: the module already has `async def _run_store(raw, request)`
    (S94's manual-fire path), so defining a second `_run_store()` silently SHADOWED it — driven, the
    history endpoint raised "missing 2 required positional arguments". Python reports a same-name
    redefinition only at the call site, which in a 1400-line handler module is a real hazard."""
    import inspect

    assert callable(T._runs_store)
    # The S94 handler still takes its two arguments.
    assert list(inspect.signature(T._run_store).parameters) == ["raw", "request"]


def test_the_last_run_status_is_read_from_the_store(home, state):
    """T7's honest badge: the PERSISTENT status survives restarts and keeps `launched` distinct from
    `ok`, where a trigger's own field would report a fire-and-forget run as a success."""
    _append_run(home, status="launched")
    del state.crons.last_run_status  # no legacy service involvement
    assert T._last_run_status(state, "clock:nightly") == "launched"


def test_no_runs_yields_None_for_the_badge(home, state):
    """None, not "" — the serializer treats it as "no badge" rather than an empty status."""
    assert T._last_run_status(state, "never-ran") is None


def test_a_broken_run_store_does_not_break_the_serializer(home, state, monkeypatch):
    """The list must render even when history is unreadable; a badge is not worth a 500."""

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(T, "_runs_store", boom)
    assert T._last_run_status(state, "clock:nightly") is None


def test_per_trigger_history_reads_the_store(home, state):
    _append_run(home)
    del state.crons.list_runs
    resp = _run(
        T.api_trigger_history(
            _req("GET", "/x/history", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    data = _body(resp)
    assert data["total"] == 1
    assert data["runs"][0]["run_id"] == "r1"


def test_one_full_run_reads_the_store(home, state):
    _append_run(home)
    del state.crons.get_run
    resp = _run(
        T.api_trigger_history_detail(
            _req(
                "GET",
                "/x/history/r1",
                state,
                match_info={"id": "schedule:clock:nightly", "run_id": "r1"},
            )
        )
    )
    assert _body(resp)["run"]["run_id"] == "r1"


def test_the_cross_trigger_feed_reads_the_store_and_joins_names(home, state):
    """A run row carries only a `job_id`, so the name is a join (S104's map) over store rows."""
    _append_run(home)
    _create_schedule(state, name="Nightly")
    del state.crons.list_all_runs
    resp = _run(
        T.api_trigger_history_all(_req("GET", "/api/triggers/history", state, query="shape=legacy"))
    )
    rows = _body(resp)["runs"]
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["job_name"] == "Nightly"


def test_deleting_a_trigger_drops_its_runs_through_the_store(home, state):
    """A delete has two halves; the run half no longer needs the legacy service."""
    _append_run(home)
    _create_schedule(state, name="Nightly")
    del state.crons.delete_runs
    _run(
        T.api_trigger_detail(
            _req("DELETE", "/x", state, match_info={"id": "schedule:clock:nightly"})
        )
    )
    runs, total = _run(T._runs_store().list_for_job("clock:nightly", 0, 10))
    assert (runs, total) == ([], 0)


def test_the_facade_no_longer_calls_any_run_method_on_the_service():
    """🔴 The property this session establishes, asserted on the SOURCE: a call that came back would
    re-couple the facade to a class the cutover is retiring, and no behavioural test would notice.
    """
    import inspect

    src = inspect.getsource(T)
    for method in ("crons.list_runs", "crons.list_all_runs", "crons.get_run", "crons.delete_runs"):
        assert method not in src, method
