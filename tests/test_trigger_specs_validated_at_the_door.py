"""Rail: trigger specs are validated at the door, and the doctor tells the truth.

One family, six filed members: `validate_cron_expr` existed but only the LLM path
called it, so `POST /api/triggers` (via `tools.create`) persisted enabled rows that
could never fire — and the doctor reported them healthy.

- #483/#687: any string was accepted as a cron expression; the row armed to nothing.
- #612: croniter-valid 6/7-field expressions (a SECONDS cadence) passed under a UI
  and API that promise five fields; the 900s floor only guarded `kind: interval`.
- #270: skip dates accepted any text; the fire path compares `%Y-%m-%d` strings, so
  a non-ISO entry is inert protection.
- #560: a well-formed skip date the schedule never fires on was equally inert, and
  nothing reported it.
- #779: an unregistered action provider was created enabled+armed and then rejected
  on every dispatch by the gateway.

The fix: `arm.semantic_spec_issues` (beside the fire path, mirroring its exact
matching rules), refused at authoring time in `tools.create` (errors) or echoed as
warnings, and folded into `GET /api/triggers/doctor` for existing rows.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.triggers.arm import semantic_spec_issues


def _errors(kind, spec):
    return [i for i in semantic_spec_issues(kind, spec) if i.severity == "error"]


def _warnings(kind, spec):
    return [i for i in semantic_spec_issues(kind, spec) if i.severity != "error"]


class TestSemanticSpecIssues:
    def test_garbage_cron_is_an_error(self):
        errs = _errors("clock", {"kind": "cron", "expr": "not a cron"})
        assert len(errs) == 1 and "never fire" in errs[0].message

    def test_out_of_range_fields_are_an_error(self):
        # "99 99 * * *" counts five tokens — the frontend hint passes it — but croniter refuses.
        assert _errors("clock", {"kind": "cron", "expr": "99 99 * * *"})
        assert _errors("clock", {"kind": "cron", "expr": "0 9 * * 8"})

    def test_daily_alias_is_valid(self):
        # #687's other half: "@daily" is croniter-valid and must NOT be rejected.
        spec = {"kind": "cron", "expr": "@daily"}
        assert _errors("clock", spec) == [] and _warnings("clock", spec) == []

    def test_six_field_seconds_cron_is_an_error(self):
        # #612: croniter accepts it, the five-field contract does not.
        errs = _errors("clock", {"kind": "cron", "expr": "* * * * * *"})
        assert len(errs) == 1 and "5 fields" in errs[0].message

    def test_sub_floor_five_field_cadence_warns_but_is_not_an_error(self):
        spec = {"kind": "cron", "expr": "* * * * *"}  # every 60s
        assert _errors("clock", spec) == []
        warns = _warnings("clock", spec)
        assert len(warns) == 1 and "floor" in warns[0].message

    def test_daily_cron_has_no_cadence_warning(self):
        assert _warnings("clock", {"kind": "cron", "expr": "0 9 * * 1"}) == []

    def test_non_iso_skip_date_is_an_error(self):
        errs = _errors("clock", {"kind": "cron", "expr": "0 9 * * 1", "skip_dates": ["not-a-date"]})
        assert len(errs) == 1 and "never suppress" in errs[0].message

    def test_impossible_calendar_date_is_an_error(self):
        assert _errors("clock", {"kind": "cron", "expr": "0 9 * * 1", "skip_dates": ["2026-02-30"]})

    def test_never_firing_skip_date_warns(self):
        # #560's exact row: Mondays cron, 2026-12-25 is a Friday — inert.
        warns = _warnings(
            "clock", {"kind": "cron", "expr": "0 9 * * 1", "skip_dates": ["2026-12-25"]}
        )
        assert len(warns) == 1 and "never fires on 2026-12-25" in warns[0].message

    def test_matching_skip_date_is_clean(self):
        # 2026-12-28 IS a Monday — a real skip, no finding.
        spec = {"kind": "cron", "expr": "0 9 * * 1", "skip_dates": ["2026-12-28"]}
        assert semantic_spec_issues("clock", spec) == []

    def test_skip_dates_validated_for_interval_kind_too(self):
        assert _errors("clock", {"kind": "interval", "interval_secs": 3600, "skip_dates": ["soon"]})

    def test_non_clock_kinds_are_untouched(self):
        assert semantic_spec_issues("event", {"source": "inbox"}) == []


class TestCreateRefusesAtTheDoor:
    def _store(self, tmp_path):
        from personalclaw.triggers.store import TriggerStore

        return TriggerStore(tmp_path / "triggers.json")

    def test_garbage_cron_is_refused(self, tmp_path):
        from personalclaw.triggers import tools

        result = tools.create(
            self._store(tmp_path),
            name="bad cron",
            kind="clock",
            spec={"kind": "cron", "expr": "whenever feels right"},
            message="hi",
        )
        assert not result.ok and "never fire" in result.text

    def test_unregistered_provider_is_refused(self, tmp_path):
        from personalclaw.triggers import tools

        result = tools.create(
            self._store(tmp_path),
            name="ghost provider",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"inline": {"provider": "definitely-not-registered", "config": {}}},
        )
        assert not result.ok
        assert "unknown action provider" in result.text
        assert "Registered providers" in result.text, "the refusal names the live registry"

    def test_valid_create_echoes_inert_skip_date_warning(self, tmp_path):
        from personalclaw.triggers import tools

        result = tools.create(
            self._store(tmp_path),
            name="monday brief",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * 1", "skip_dates": ["2026-12-25"]},
            message="hi",
        )
        assert result.ok, result.text
        assert "never fires on 2026-12-25" in result.text

    def test_clean_create_still_works(self, tmp_path):
        from personalclaw.triggers import tools

        result = tools.create(
            self._store(tmp_path),
            name="daily brief",
            kind="clock",
            spec={"kind": "cron", "expr": "@daily"},
            message="hi",
        )
        assert result.ok, result.text


@pytest.mark.asyncio
class TestApiAndDoctor:
    def _app_state(self, tmp_path, monkeypatch):
        import personalclaw.dashboard.handlers.triggers as h

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(tmp_path / "triggers.json")
        monkeypatch.setattr(h, "_trigger_store", lambda: store)
        return h, store

    async def test_post_api_triggers_rejects_garbage_cron_with_400(self, tmp_path, monkeypatch):
        h, _ = self._app_state(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        app = web.Application()
        state = MagicMock()
        app["state"] = state
        app.router.add_post("/api/triggers", h.api_trigger_create)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/triggers",
                json={
                    "trigger_type": "schedule",
                    "name": "x",
                    "cron": "whenever feels right",
                    "action": {"provider": "notify", "config": {}},
                },
            )
            assert resp.status == 400, "#483: any-string cron must be a 400, not a dead row"
            body = await resp.json()
            assert "never fire" in body["error"]

    async def test_doctor_reports_the_inert_skip_date(self, tmp_path, monkeypatch):
        h, store = self._app_state(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        from personalclaw.triggers.models import Trigger

        # #560's live row, written straight to the store (pre-fix rows exist in the wild).
        store.upsert(
            Trigger(
                id="t560",
                name="monday",
                kind="clock",
                spec={
                    "kind": "cron",
                    "expr": "0 9 * * 1",
                    "skip_dates": ["not-a-date", "2026-12-25"],
                },
                workflow={"inline": {"provider": "notify", "config": {}}},
            )
        )
        app = web.Application()
        app["state"] = MagicMock()
        app.router.add_get("/api/triggers/doctor", h.api_triggers_doctor)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/triggers/doctor")
            assert resp.status == 200
            report = await resp.json()
        findings = [f for f in report["findings"] if f["trigger_id"].endswith("t560")]
        codes = {f["code"] for f in findings}
        assert "unfireable_spec" in codes, "the malformed skip date is reported"
        assert "inert_spec_entry" in codes, "the never-firing skip date is reported"
        assert all(f["fix"] for f in findings), "a doctor finding always says what to do"
