"""Tests for the dashboard module."""

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.state import (
    DashboardState,
    _fmt_duration,
    _load_notifications,
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
            start_time=0.0,
        )
        assert state.sessions.count == 3
        # `messages_received` used to be asserted here as `== 0`. It was initialized to 0 and never
        # incremented anywhere, so this line pinned the DEFECT: it could only ever pass, and it
        # made a writerless counter look covered. The attribute is gone; the session count above is
        # the state field that is actually populated.
        assert not hasattr(state, "messages_received")

    def test_state_init_with_channel_delivery(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
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

    def test_trim_fires_at_the_append_seam_on_memory_and_file_together(
        self, monkeypatch, tmp_path
    ) -> None:
        """The cap is enforced where rows are BORN: past 2× the cap, memory and the
        file drop to the newest cap-many rows in the same step — never at load or
        rewrite, which are lossless mirrors (Issue 420)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 5)
        state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
        for i in range(11):
            state._append_notification(
                {"kind": "cron", "title": f"n{i}", "body": "x", "ts": str(i)}
            )

        remaining = (tmp_path / "notifications.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(remaining) == 5
        assert json.loads(remaining[0])["title"] == "n6"
        assert json.loads(remaining[-1])["title"] == "n10"
        # Memory mirrors the file exactly — the invariant that makes rewrites lossless.
        assert [n["title"] for n in state._notification_log] == [f"n{i}" for i in range(6, 11)]

    def test_ack_never_deletes_rows_beyond_the_load_window(self, monkeypatch, tmp_path) -> None:
        """Issue 420's measured repro: a steady-state file holds up to 2× the cap;
        acking ONE row after a restart must set one flag — not rewrite the file down
        to a truncated in-memory view, permanently destroying the rest."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 5)
        path = tmp_path / "notifications.jsonl"
        rows = [
            json.dumps({"kind": "cron", "title": f"n{i}", "body": "x", "ts": str(i)})
            for i in range(9)  # between cap (5) and 2×cap (10): the normal steady state
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
        assert state.ack_notification("8") is True

        remaining = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        assert len(remaining) == 9, "one ack must never shorten the log"
        assert [n["title"] for n in remaining] == [f"n{i}" for i in range(9)]
        acked = {n["ts"]: n.get("acked", False) for n in remaining}
        assert acked["8"] is True
        assert sum(1 for v in acked.values() if v) == 1

    def test_delete_removes_exactly_one_row(self, monkeypatch, tmp_path) -> None:
        """Deleting one notification from a beyond-cap file removes that row alone."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 5)
        path = tmp_path / "notifications.jsonl"
        rows = [
            json.dumps({"kind": "cron", "title": f"n{i}", "body": "x", "ts": str(i)})
            for i in range(8)
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
        assert state.delete_notification("3") is True

        remaining = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        assert [n["ts"] for n in remaining] == ["0", "1", "2", "4", "5", "6", "7"]

    def test_notify_persists(self, monkeypatch, tmp_path) -> None:
        """DashboardState.notify() persists to disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = DashboardState(
            sessions=MagicMock(count=0),
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
            start_time=0.0,
        )
        # Should have loaded existing notifications
        assert len(state._notification_log) == 2
        assert state._notification_log[0]["title"] == "Old"
        assert state._notification_log[1]["title"] == "Old2"


class TestUnreadDerived:
    """unread is DERIVED from unresolved INBOX items — no cached counter, no log flags.

    Plan 42 T5.2 moved the badge off the notification log's `acked` flags. The two stores had
    become two answers to one question: the log tracked "was a toast acknowledged", the inbox
    tracks "is this dealt with". A user who handled a request in the inbox still saw a badge,
    and dismissing a toast cleared the badge for outstanding work.
    """

    def _state(self, monkeypatch, tmp_path) -> DashboardState:
        # BOTH config_dir seams: state.py for the notification log, inbox.py for the item
        # store. Patching only the first let unread_count() read the DEVELOPER'S real inbox
        # (observed: a count of 39 in a "fresh" test).
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
        return DashboardState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )

    def _add_item(self, tmp_path, status: str, item_id: str = "") -> str:
        from personalclaw.inbox import InboxItem, InboxStore

        store = InboxStore(tmp_path / "inbox.json")
        store.load()
        item = InboxItem(
            id=item_id or f"C1_{len(store.items) + 1}.0",
            channel="C1",
            channel_name="#t",
            thread_ts=None,
            message="m",
            sender_id="U1",
            sender_name="A",
        )
        item.status = status
        store.add(item)
        store.save()
        return item.id

    def test_counts_pending_inbox_items(self, monkeypatch, tmp_path) -> None:
        state = self._state(monkeypatch, tmp_path)
        assert state.unread_count() == 0
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "pending")
        state._inbox_store = None  # force a re-read (a live gateway reloads on write)
        assert state.unread_count() == 2

    def test_seen_does_not_count(self, monkeypatch, tmp_path) -> None:
        """The badge means "new since you last looked"; opening an item marks it SEEN.

        Counting SEEN would keep the badge lit until everything was RESOLVED, which is what
        the list itself is for.
        """
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "seen")
        state._inbox_store = None
        assert state.unread_count() == 1

    @pytest.mark.parametrize("resolved", ["handled", "dismissed", "sent"])
    def test_resolved_items_do_not_count(self, monkeypatch, tmp_path, resolved) -> None:
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, resolved)
        state._inbox_store = None
        assert state.unread_count() == 0

    def test_notifications_no_longer_drive_the_badge(self, monkeypatch, tmp_path) -> None:
        """THE demotion, asserted directly: a delivered toast is not unresolved work.

        This is the one accepted state break of plan 42 — a user's badge resets once on
        upgrade because it stops counting unacked log entries.
        """
        state = self._state(monkeypatch, tmp_path)
        state.notify("cron", "A", "a")
        state.notify("cron", "B", "b")
        assert len(state._notification_log) == 2, "the log still records the delivery"
        assert state.unread_count() == 0, "but it does not claim your attention"

    def test_clearing_notifications_does_not_change_the_badge(self, monkeypatch, tmp_path) -> None:
        """Previously, clearing toasts zeroed the badge — hiding outstanding work."""
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        state._inbox_store = None
        assert state.unread_count() == 1
        state.clear_notifications()
        state._inbox_store = None
        assert state.unread_count() == 1, "clearing the audit must not hide real work"

    def test_survives_restart(self, monkeypatch, tmp_path) -> None:
        """A fresh state object reads the persisted inbox, not an in-memory counter."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
        self._add_item(tmp_path, "pending")
        self._add_item(tmp_path, "handled")
        state = DashboardState(
            sessions=MagicMock(count=0),
            start_time=0.0,
        )
        assert state.unread_count() == 1

    def test_fails_to_zero_rather_than_raising(self, monkeypatch, tmp_path) -> None:
        """A badge is chrome; a broken read must not break the sessions payload."""
        state = self._state(monkeypatch, tmp_path)
        monkeypatch.setattr("personalclaw.inbox.InboxStore", MagicMock(side_effect=OSError("gone")))
        state._inbox_store = None
        assert state.unread_count() == 0

    def test_prefers_the_running_services_store(self, monkeypatch, tmp_path) -> None:
        """A live gateway's in-memory store is authoritative over a fresh disk read."""
        state = self._state(monkeypatch, tmp_path)
        self._add_item(tmp_path, "pending")
        svc = MagicMock()
        svc.inbox = MagicMock(items={})
        state._inbox_svc = svc
        assert state.unread_count() == 0, "the service's store won"

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
