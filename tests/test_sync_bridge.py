"""Tests for sync_bridge module — session handoff utilities."""

import json
import time
from unittest.mock import patch

import pytest

from personalclaw.history import ConversationLog
from personalclaw.sync_bridge import (
    _PENDING_RESUME_TTL,
    _pending_resume_msg_ts,
    _pending_resumes,
    format_session_list,
    handoff_to_channel,
    list_recent_sessions,
    peek_pending_resume,
    pop_pending_resume,
    pop_pending_resume_ts,
    set_pending_resume,
)


class TestListRecentSessions:
    def test_returns_sessions_with_source(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard_chat-1-100", "user", "hello")
        log.append("thread-chan-1", "user", "hi from the channel")
        log.append("cron_job-1", "user", "cron msg")
        result = list_recent_sessions(log, limit=10)
        sources = {s["source"] for s in result}
        assert sources == {"dashboard", "channel", "cron"}

    def test_respects_limit(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        for i in range(20):
            log.append(f"thread-{i}", "user", f"msg {i}")
        result = list_recent_sessions(log, limit=5)
        assert len(result) == 5

    def test_empty_log(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        assert list_recent_sessions(log) == []

    def test_skips_empty_keys(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("valid-key", "user", "hello")
        result = list_recent_sessions(log)
        assert len(result) == 1
        assert result[0]["key"] == "valid-key"


class TestFormatSessionList:
    def test_empty_returns_message(self):
        assert format_session_list([]) == "No recent sessions found."

    def test_formats_numbered_list(self):
        now = time.time()
        sessions = [
            {"key": "k1", "title": "Chat about code", "source": "dashboard", "modified": now - 120},
            {"key": "k2", "title": "Channel thread", "source": "channel", "modified": now - 7200},
        ]
        result = format_session_list(sessions)
        assert "`1`" in result
        assert "`2`" in result
        assert "🖥️" in result  # dashboard icon
        assert "💬" in result  # channel icon
        assert "2m ago" in result
        assert "2h ago" in result

    def test_age_days(self):
        sessions = [
            {"key": "k1", "title": "Old", "source": "channel", "modified": time.time() - 86400 * 3},
        ]
        result = format_session_list(sessions)
        assert "3d ago" in result


class TestPendingResumeStateMachine:
    """Tests for set/peek/pop pending resume state with TTL expiry."""

    def setup_method(self):
        _pending_resumes.clear()
        _pending_resume_msg_ts.clear()

    def test_set_and_peek(self):
        sessions = [{"key": "k1"}, {"key": "k2"}]
        set_pending_resume("thread-1", sessions)
        result = peek_pending_resume("thread-1")
        assert result == sessions

    def test_peek_returns_none_when_empty(self):
        assert peek_pending_resume("nonexistent") is None

    def test_peek_does_not_consume(self):
        set_pending_resume("thread-1", [{"key": "k1"}])
        peek_pending_resume("thread-1")
        assert peek_pending_resume("thread-1") is not None

    def test_pop_consumes(self):
        set_pending_resume("thread-1", [{"key": "k1"}])
        result = pop_pending_resume("thread-1")
        assert result == [{"key": "k1"}]
        assert pop_pending_resume("thread-1") is None

    def test_pop_returns_none_when_empty(self):
        assert pop_pending_resume("nonexistent") is None

    def test_ttl_expiry(self):
        set_pending_resume("thread-1", [{"key": "k1"}])
        # Simulate time passing beyond TTL
        with patch("personalclaw.sync_bridge.time") as mock_time:
            # set_pending_resume used real time.monotonic(); now peek uses mock
            mock_time.monotonic.return_value = time.monotonic() + _PENDING_RESUME_TTL + 1
            assert peek_pending_resume("thread-1") is None

    def test_msg_ts_stored_and_popped(self):
        set_pending_resume("thread-1", [{"key": "k1"}], bot_list_ts="B1", user_cmd_ts="U1")
        assert pop_pending_resume_ts("thread-1") == ("B1", "U1")
        # Second pop returns empty
        assert pop_pending_resume_ts("thread-1") == ("", "")

    def test_msg_ts_not_stored_when_empty(self):
        set_pending_resume("thread-1", [{"key": "k1"}])
        assert pop_pending_resume_ts("thread-1") == ("", "")


class MockDelivery:
    """In-memory ChannelDelivery for handoff tests (open_dm + deliver_text)."""

    def __init__(self):
        self.delivered: list[tuple[str, str]] = []
        self._next_ts = 1000000

    async def open_dm(self, user_id):
        return f"D-{user_id}"

    async def deliver_text(self, channel, text, thread_ts=None):
        ts = f"{self._next_ts}.000000"
        self._next_ts += 1
        self.delivered.append((channel, text))
        return ts


class TestHandoffToChannel:
    """Tests for handoff_to_channel async function (ChannelDelivery contract)."""

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard:chat-1", "user", "hello")
        log.append("dashboard:chat-1", "assistant", "hi there")
        delivery = MockDelivery()
        result = await handoff_to_channel(
            delivery, "U123", log, "dashboard:chat-1", title="Test Chat"
        )
        assert result is not None
        # open_dm targeted the owner; the handoff header was delivered there.
        assert len(delivery.delivered) >= 1
        channel, text = delivery.delivered[0]
        assert channel == "D-U123"
        assert "Test Chat" in text

    @pytest.mark.asyncio
    async def test_empty_session_returns_none(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        delivery = MockDelivery()
        result = await handoff_to_channel(delivery, "U123", log, "nonexistent")
        assert result is None
        assert delivery.delivered == []

    @pytest.mark.asyncio
    async def test_links_session_via_sessions_object(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard:chat-1", "user", "hello")
        delivery = MockDelivery()

        class FakeSessions:
            def __init__(self):
                self.linked = {}

            def set_channel_link(self, key, ts, ch):
                self.linked[key] = (ts, ch)

        sessions = FakeSessions()
        result = await handoff_to_channel(
            delivery,
            "U123",
            log,
            "dashboard:chat-1",
            title="Test",
            channel="C456",
            sessions=sessions,
        )
        assert result is not None
        assert "dashboard:chat-1" in sessions.linked
        # explicit channel wins over open_dm
        assert delivery.delivered[0][0] == "C456"

    @pytest.mark.asyncio
    async def test_title_from_metadata_when_not_provided(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard:chat-1", "user", "hello")
        # Set title in metadata
        path = tmp_path / "dashboard_chat-1.jsonl"
        lines = path.read_text().splitlines(keepends=True)
        meta = json.loads(lines[0])
        meta["title"] = "Auto Title"
        lines[0] = json.dumps(meta) + "\n"
        path.write_text("".join(lines))

        delivery = MockDelivery()
        result = await handoff_to_channel(delivery, "U123", log, "dashboard:chat-1")
        assert result is not None
        assert "Auto Title" in delivery.delivered[0][1]
