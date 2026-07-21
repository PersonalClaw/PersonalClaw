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


class TestStatusSnapshot:
    def test_contains_core_fields(self, state: DashboardState) -> None:
        snap = state.status_snapshot()
        assert snap["sessions"] == 3
        assert snap["cron_jobs"] == 2
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
