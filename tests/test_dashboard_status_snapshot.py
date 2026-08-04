"""Tests for DashboardState.status_snapshot() — shared status payload."""

import time
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.state import DashboardState


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    crons = MagicMock()
    crons.list_jobs.return_value = [{"id": "j1"}, {"id": "j2"}]
    lessons = MagicMock()
    lessons.load_all.return_value = [{"rule": "r1"}]
    return DashboardState(
        sessions=MagicMock(count=3),
        crons=crons,
        lessons=lessons,
        start_time=time.time() - 120,
        subagents=MagicMock(count=1),
    )


def _store_trigger(tmp_path, trigger_id, *, enabled=True, valid=True):
    """Write a real store trigger under the state's home, the way the runtime does."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    spec = {"kind": "interval", "every_secs": 3600} if valid else {}
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(id=trigger_id, name=trigger_id, kind="clock", enabled=enabled, spec=spec)
    )


class TestStatusSnapshot:
    def test_the_trigger_count_comes_from_the_store(self, state: DashboardState, tmp_path) -> None:
        """🔴 SUPERSEDED CONTRACT (S107). This asserted `cron_jobs == 2` off a `crons.list_jobs()`
        mock. The S100/S101 cutover left `ScheduleService` holding nothing, so the metric the
        dashboard renders as "triggers" reported 0 on a machine with automations — measured on a
        home with three valid store triggers, two enabled. The count now reads the unified store.

        (The fixture's mock returns dict-shaped jobs, which have no `.id`, so the legacy fold-in
        contributes nothing here — which is exactly why the old assertion could not have caught the
        regression it was supposed to guard.)
        """
        _store_trigger(tmp_path, "clock:a")
        _store_trigger(tmp_path, "clock:b", enabled=False)
        assert state.status_snapshot()["cron_jobs"] == 2

    def test_contains_core_fields(self, state: DashboardState) -> None:
        snap = state.status_snapshot()
        assert snap["sessions"] == 3
        assert snap["lessons"] == 1
        assert snap["subagents"] == 1
        assert snap["no_crons"] is False
        assert "uptime" in snap
        assert "start_time" in snap

    def test_no_crons_true(self, state: DashboardState) -> None:
        state.no_crons = True
        assert state.status_snapshot()["no_crons"] is True

    def test_no_subagents(self, state: DashboardState) -> None:
        state.subagents = None
        assert state.status_snapshot()["subagents"] == 0

    def test_new_fields_propagate_to_all_callers(self, state: DashboardState) -> None:
        """Any field added to status_snapshot is automatically in SSE/WS/API."""
        snap = state.status_snapshot()
        # These keys must exist — if one is missing, a caller will lose it
        required = {
            "uptime",
            "start_time",
            "sessions",
            "messages",
            "cron_jobs",
            "lessons",
            "subagents",
            "update_available",
            "no_crons",
        }
        assert required.issubset(snap.keys())

    def test_update_available_passthrough(self, state: DashboardState) -> None:
        assert state.status_snapshot()["update_available"] is False
        assert state.status_snapshot(update_available=True)["update_available"] is True


class TestAllStatusSnapshotCallersPassUpdateAvailable:
    """Every call to status_snapshot() must pass update_available explicitly."""

    def test_ws_has_no_status_push(self) -> None:
        """ws.py must NOT push periodic status frames — the FE polls
        GET /api/status; the old 5s {"type": "dashboard"} push had no
        frontend consumer and was removed."""
        import inspect

        from personalclaw.dashboard import ws

        source = inspect.getsource(ws)
        assert "status_snapshot" not in source
        assert "_push_status" not in source

    # NOTE: the global SSE handler (api_stream) was removed in the transport
    # de-duplication (SSE M3), and the WS 5s status push was removed too (no
    # FE consumer) — /api/status (handlers_system) is the ONE status surface.

    def test_system_api_passes_update_available(self) -> None:
        import inspect

        from personalclaw.dashboard import handlers_system

        source = inspect.getsource(handlers_system)
        assert "update_available=" in source


class TestTriggerCounts:
    """`DashboardState.trigger_counts()` — the one source both status surfaces share (S107)."""

    def test_an_empty_home_counts_zero(self, state: DashboardState) -> None:
        assert state.trigger_counts() == {"total": 0, "enabled": 0, "broken": 0}

    def test_enabled_is_counted_separately_from_total(
        self, state: DashboardState, tmp_path
    ) -> None:
        _store_trigger(tmp_path, "clock:on")
        _store_trigger(tmp_path, "clock:off", enabled=False)
        counts = state.trigger_counts()
        assert counts["total"] == 2
        assert counts["enabled"] == 1

    def test_a_broken_row_is_counted_and_never_enabled(
        self, state: DashboardState, tmp_path
    ) -> None:
        """The store refuses to enable a row that fails validation, so a broken trigger must show up
        as broken rather than merely vanish from the enabled count with no explanation."""
        _store_trigger(tmp_path, "clock:bad", valid=False)
        counts = state.trigger_counts()
        assert counts["total"] == 1
        assert counts["enabled"] == 0
        assert counts["broken"] == 1

    def test_a_legacy_job_sharing_an_id_is_not_double_counted(
        self, state: DashboardState, tmp_path
    ) -> None:
        """A home mid-migration holds the same automation in both places. Counting it twice would
        make the status card report more automations than the user has."""
        from types import SimpleNamespace

        _store_trigger(tmp_path, "clock:shared")
        state.crons.list_jobs.return_value = [
            SimpleNamespace(id="clock:shared", enabled=True),
            SimpleNamespace(id="legacy-only", enabled=True),
        ]
        counts = state.trigger_counts()
        assert counts["total"] == 2
        assert counts["enabled"] == 2

    def test_a_raising_legacy_service_does_not_break_the_status_read(
        self, state: DashboardState, tmp_path
    ) -> None:
        _store_trigger(tmp_path, "clock:a")
        state.crons.list_jobs.side_effect = OSError("crons.json is gibberish")
        assert state.trigger_counts()["total"] == 1

    def test_an_unusable_store_reports_zeros_rather_than_500ing(
        self, state: DashboardState, monkeypatch
    ) -> None:
        """`GET /api/status` is what a user opens when something is already wrong."""
        monkeypatch.setattr(
            "personalclaw.triggers.store.TriggerStore",
            MagicMock(side_effect=OSError("home is gone")),
        )
        assert state.trigger_counts() == {"total": 0, "enabled": 0, "broken": 0}

    def test_the_legacy_status_method_is_gone(self) -> None:
        """🔴 The clean break. `ScheduleService.status()` reported `{"running": false, "jobs": 0,
        "enabled": 0}` on a healthy machine — the counts came from a service the cutover emptied,
        and `running` was False BY DESIGN because `load_without_timer` never sets it. Leaving it
        in place would mean a second, wrong answer to the same question."""
        from personalclaw.schedule import ScheduleService

        assert not hasattr(ScheduleService, "status")
        assert not hasattr(ScheduleService, "set_refresh_callback")
