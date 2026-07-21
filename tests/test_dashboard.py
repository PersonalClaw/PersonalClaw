"""Tests for the dashboard module."""

import json
from unittest.mock import MagicMock

from personalclaw.dashboard.state import (
    DashboardState,
    _fmt_duration,
    _load_notifications,
    _maybe_trim_notifications,
    _persist_notification,
)


class TestDashboard:
    def test_fmt_duration_minutes(self) -> None:
        assert _fmt_duration(125) == "2m 5s"

    def test_fmt_duration_hours(self) -> None:
        assert _fmt_duration(3661) == "1h 1m"

    def test_fmt_duration_zero(self) -> None:
        assert _fmt_duration(0) == "0m 0s"

    def test_state_init(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=3),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        assert state.sessions.count == 3
        assert state.messages_received == 0

    def test_state_init_with_channel_delivery(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
            owner_id="U123",
        )
        # channel_delivery is the sole outbound-channel handle, set by the transport
        # at start_inbound; None until a channel connects.
        assert state.channel_delivery is None
        state.channel_delivery = MagicMock()
        assert state.channel_delivery is not None
        assert state.owner_id == "U123"


class TestNotificationPersistence:
    def test_persist_and_load(self, monkeypatch, tmp_path) -> None:
        """Notifications are persisted to JSONL and loaded on restart."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _persist_notification({"kind": "cron", "title": "Job A", "body": "result"})
        _persist_notification({"kind": "subagent", "title": "Sub B", "body": "done"})

        loaded = _load_notifications()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Job A"
        assert loaded[1]["title"] == "Sub B"

    def test_load_empty(self, monkeypatch, tmp_path) -> None:
        """Loading from nonexistent file returns empty list."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        assert _load_notifications() == []

    def test_load_corrupted_lines_skipped(self, monkeypatch, tmp_path) -> None:
        """Corrupted JSON lines are skipped during load."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        path = tmp_path / "notifications.jsonl"
        lines = [
            json.dumps({"kind": "cron", "title": "Good", "body": "ok"}),
            "this is not json",
            json.dumps({"kind": "cron", "title": "Also good", "body": "ok"}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        loaded = _load_notifications()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Good"
        assert loaded[1]["title"] == "Also good"

    def test_trim_large_file(self, monkeypatch, tmp_path) -> None:
        """File is trimmed when exceeding 2x max notifications."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 5)
        path = tmp_path / "notifications.jsonl"
        # Write 11 lines (> 2 * 5)
        lines: list[str] = []
        for i in range(11):
            lines.append(json.dumps({"kind": "cron", "title": f"n{i}", "body": "x"}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _maybe_trim_notifications(path)

        remaining = path.read_text(encoding="utf-8").splitlines()
        assert len(remaining) == 5
        # Should keep the last 5
        assert json.loads(remaining[0])["title"] == "n6"
        assert json.loads(remaining[-1])["title"] == "n10"

    def test_notify_persists(self, monkeypatch, tmp_path) -> None:
        """DashboardState.notify() persists to disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        state.notify("cron", "Test Job", "Result text")

        # Check in-memory
        assert len(state._notification_log) == 1
        assert state._notification_log[0]["title"] == "Test Job"

        # Check on disk
        loaded = _load_notifications()
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Test Job"
        assert "ts" in loaded[0]  # timestamp added

    def test_delete_notifications_for_loop(self, monkeypatch, tmp_path) -> None:
        """Deleting a loop purges its notifications (no dead 'Open goal' links)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        state.notify("success", "Goal loop complete", "done", meta={"loop_id": "aaaa1111"})
        state.notify("error", "Goal loop failed", "boom", meta={"loop_id": "bbbb2222"})
        state.notify("info", "Goal loop progress", "p", meta={"loop_id": "aaaa1111"})
        state.notify("cron", "Unrelated", "x")  # no loop_id → must survive
        removed = state.delete_notifications_for_loop("aaaa1111")
        assert removed == 2
        titles = [n["title"] for n in state._notification_log]
        assert "Goal loop complete" not in titles and "Goal loop progress" not in titles
        assert "Goal loop failed" in titles and "Unrelated" in titles
        # persisted
        assert len(_load_notifications()) == 2
        # no-op for empty/unknown
        assert state.delete_notifications_for_loop("") == 0

    def test_state_loads_existing_on_init(self, monkeypatch, tmp_path) -> None:
        """DashboardState.__init__ loads existing notifications from disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        # Pre-persist some notifications
        _persist_notification({"kind": "cron", "title": "Old", "body": "data"})
        _persist_notification({"kind": "cron", "title": "Old2", "body": "data2"})

        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        # Should have loaded existing notifications
        assert len(state._notification_log) == 2
        assert state._notification_log[0]["title"] == "Old"
        assert state._notification_log[1]["title"] == "Old2"


class TestUnreadDerived:
    """unread is DERIVED from the log's acked flags — no cached counter."""

    def _state(self, monkeypatch, tmp_path) -> DashboardState:
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        return DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )

    def test_counts_unacked_only(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.unread_count() == 0
        state.notify("cron", "A", "a")
        state.notify("cron", "B", "b")
        assert state.unread_count() == 2
        # ack one → decrements (the old cached counter never did)
        ts = state._notification_log[0]["ts"]
        assert state.ack_notification(ts)
        assert state.unread_count() == 1
        # unack → back up
        assert state.unack_notification(ts)
        assert state.unread_count() == 2

    def test_survives_restart(self, monkeypatch, tmp_path) -> None:
        """Persisted unacked notifications count as unread on boot (the old
        counter initialized to 0 regardless of reloaded state)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _persist_notification({"kind": "cron", "title": "N1", "body": "x", "ts": "t1"})
        _persist_notification(
            {"kind": "cron", "title": "N2", "body": "x", "ts": "t2", "acked": True}
        )
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        assert state.unread_count() == 1

    def test_clear_zeroes(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        state.clear_notifications()
        assert state.unread_count() == 0

    def test_no_cached_counter_attribute(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert not hasattr(state, "_unread_count")
        assert not hasattr(state, "mark_notifications_read")


class TestNotificationRemovalBroadcast:
    """delete/clear must broadcast a WS event so open views don't stay stale."""

    def _state_with_ws(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(),
            lessons=MagicMock(),
            start_time=0.0,
        )
        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)
        return state, ws

    def _sent_types(self, ws) -> list[tuple[str, object]]:
        out = []
        for call in ws.send_str.call_args_list:
            msg = json.loads(call[0][0])
            out.append((msg["type"], msg.get("data")))
        return out

    def test_delete_notification_broadcasts(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        ts = state._notification_log[0]["ts"]
        ws.send_str.reset_mock()
        assert state.delete_notification(ts)
        assert ("notification_removed", {"ts": ts}) in self._sent_types(ws)

    def test_delete_miss_does_not_broadcast(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        assert not state.delete_notification("no-such-ts")
        assert self._sent_types(ws) == []

    def test_delete_for_loop_broadcasts(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("info", "L", "x", meta={"loop_id": "aaaa1111"})
        state.notify("info", "L2", "y", meta={"loop_id": "aaaa1111"})
        removed_ts = [n["ts"] for n in state._notification_log]
        ws.send_str.reset_mock()
        assert state.delete_notifications_for_loop("aaaa1111") == 2
        assert ("notification_removed", {"ts": removed_ts}) in self._sent_types(ws)

    def test_clear_broadcasts_wildcard(self, monkeypatch, tmp_path) -> None:
        state, ws = self._state_with_ws(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        ws.send_str.reset_mock()
        state.clear_notifications()
        assert ("notification_removed", {"ts": "*"}) in self._sent_types(ws)
