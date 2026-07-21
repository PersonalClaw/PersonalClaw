"""WS dead-client cleanup, plan_memory rotation, and plan_lessons cache."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.state import DashboardState

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return DashboardState(
        sessions=MagicMock(count=0),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=0.0,
    )


@pytest.fixture
def plan_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.context_management.config_dir", lambda: tmp_path)
    return tmp_path / "plan_memory"


# ── WS dead client cleanup ─────────────────────────────────────────


class TestWsDeadClientCleanup:
    """Scenarios for _send_ws_all dead client detection."""

    def test_closed_client_removed_from_all_lists(self, state: DashboardState) -> None:
        """A closed WS should be removed from _ws_clients, log subs, and subagent subs."""
        ws = MagicMock(closed=True)
        ws.send_str = AsyncMock()
        state.register_ws(ws)
        state.subscribe_logs(ws)
        state.subscribe_subagents(ws)

        state.broadcast_ws("test", {"x": 1})

        assert ws not in state._ws_clients
        assert ws not in state._ws_log_subscribers
        assert ws not in state._ws_subagent_subscribers
        ws.send_str.assert_not_called()

    @pytest.mark.asyncio
    async def test_alive_client_kept(self, state: DashboardState) -> None:
        """Alive WS should remain and receive messages."""
        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)

        state.broadcast_ws("ping", {})

        assert ws in state._ws_clients
        ws.send_str.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_alive_and_dead(self, state: DashboardState) -> None:
        """Only dead clients removed; alive ones stay and receive."""
        alive1 = MagicMock(closed=False, send_str=AsyncMock())
        alive2 = MagicMock(closed=False, send_str=AsyncMock())
        dead1 = MagicMock(closed=True, send_str=AsyncMock())
        dead2 = MagicMock(closed=True, send_str=AsyncMock())
        for ws in [alive1, dead1, alive2, dead2]:
            state.register_ws(ws)

        state.broadcast_ws("test", {})

        assert alive1 in state._ws_clients
        assert alive2 in state._ws_clients
        assert dead1 not in state._ws_clients
        assert dead2 not in state._ws_clients
        alive1.send_str.assert_called_once()
        alive2.send_str.assert_called_once()
        dead1.send_str.assert_not_called()
        dead2.send_str.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_exception_removes_client(self, state: DashboardState) -> None:
        """Client that raises on send_str should be removed."""
        ws = MagicMock(closed=False)
        ws.send_str = MagicMock(side_effect=ConnectionResetError)
        state.register_ws(ws)

        state.broadcast_ws("test", {})

        assert ws not in state._ws_clients

    @pytest.mark.asyncio
    async def test_subagent_broadcast_removes_closed(self, state: DashboardState) -> None:
        """broadcast_ws_subagent_subscribers also removes closed clients."""
        ws_dead = MagicMock(closed=True, send_str=AsyncMock())
        ws_alive = MagicMock(closed=False, send_str=AsyncMock())
        state.subscribe_subagents(ws_dead)
        state.subscribe_subagents(ws_alive)

        state.broadcast_ws_subagent_subscribers("chunk", {"id": "a1"})

        assert ws_dead not in state._ws_subagent_subscribers
        assert ws_alive in state._ws_subagent_subscribers
        ws_alive.send_str.assert_called_once()
        ws_dead.send_str.assert_not_called()

    def test_empty_clients_noop(self, state: DashboardState) -> None:
        """No error when broadcasting to empty client list."""
        state.broadcast_ws("test", {})
        state.broadcast_ws_subagent_subscribers("test", {})

    def test_unregister_cleans_all_subscriber_lists(self, state: DashboardState) -> None:
        """unregister_ws should remove from all subscriber lists."""
        ws = MagicMock()
        state.register_ws(ws)
        state.subscribe_logs(ws)
        state.subscribe_subagents(ws)

        state.unregister_ws(ws)

        assert ws not in state._ws_clients
        assert ws not in state._ws_log_subscribers
        assert ws not in state._ws_subagent_subscribers

    def test_unregister_idempotent(self, state: DashboardState) -> None:
        """Double unregister should not raise."""
        ws = MagicMock()
        state.register_ws(ws)
        state.unregister_ws(ws)
        state.unregister_ws(ws)  # should not raise


# ── Plan memory rotation ────────────────────────────────────────────


class TestPlanMemoryRotation:
    """Scenarios for append_plan_event JSONL rotation."""

    def test_append_creates_file(self, plan_dir) -> None:
        from personalclaw.context_management import append_plan_event, plan_memory_path

        append_plan_event("sess1", {"type": "plan_created", "task_description": "test"})
        path = plan_memory_path()
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["session_id"] == "sess1"
        assert event["type"] == "plan_created"

    def test_no_rotation_under_limit(self, plan_dir) -> None:
        from personalclaw.context_management import append_plan_event, plan_memory_path

        for i in range(10):
            append_plan_event("sess1", {"type": "test", "i": i})
        lines = plan_memory_path().read_text().strip().splitlines()
        assert len(lines) == 10

    def test_rotation_at_limit(self, plan_dir) -> None:
        from personalclaw.context_management import (
            _PLAN_MEMORY_MAX_LINES,
            append_plan_event,
            plan_memory_path,
        )

        # Fill to exactly the limit
        path = plan_memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for i in range(_PLAN_MEMORY_MAX_LINES):
                f.write(json.dumps({"type": "old", "i": i}) + "\n")

        # One more should trigger rotation
        append_plan_event("sess1", {"type": "new", "i": 999})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == _PLAN_MEMORY_MAX_LINES

        # Last line should be the new event
        last = json.loads(lines[-1])
        assert last["type"] == "new"
        assert last["i"] == 999

        # First line should NOT be i=0 (it was rotated out)
        first = json.loads(lines[0])
        assert first["i"] != 0

    def test_rotation_preserves_newest(self, plan_dir) -> None:
        from personalclaw.context_management import (
            _PLAN_MEMORY_MAX_LINES,
            append_plan_event,
            plan_memory_path,
        )

        path = plan_memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write 600 lines (over limit)
        with open(path, "w") as f:
            for i in range(600):
                f.write(json.dumps({"type": "old", "i": i}) + "\n")

        append_plan_event("sess1", {"type": "trigger"})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == _PLAN_MEMORY_MAX_LINES

    def test_load_plan_memory_works_after_rotation(self, plan_dir) -> None:
        from personalclaw.context_management import (
            append_plan_event,
            load_plan_memory,
        )

        for i in range(5):
            append_plan_event("sessA", {"type": "test", "i": i})
        for i in range(3):
            append_plan_event("sessB", {"type": "test", "i": i})

        all_events = load_plan_memory()
        assert len(all_events) == 8

        sess_a = load_plan_memory("sessA")
        assert len(sess_a) == 5

        sess_b = load_plan_memory("sessB")
        assert len(sess_b) == 3


# ── Plan lessons cache ──────────────────────────────────────────────


class TestPlanLessonsCache:
    """Scenarios for load_plan_lessons TTL cache."""

    def test_returns_empty_when_no_file(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        cm._plan_lessons_cache = (0.0, "")
        result = cm.load_plan_lessons()
        assert result == ""

    def test_reads_file_content(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        cm._plan_lessons_cache = (0.0, "")
        path = cm.plan_lessons_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- lesson 1\n- lesson 2\n")

        result = cm.load_plan_lessons()
        assert "lesson 1" in result
        assert "lesson 2" in result

    def test_cache_hit_within_ttl(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        cm._plan_lessons_cache = (time.time(), "cached value")
        # Even if file has different content, cache should be returned
        path = cm.plan_lessons_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("different content")

        result = cm.load_plan_lessons()
        assert result == "cached value"

    def test_cache_miss_after_ttl(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        # Set cache to expired (31s ago)
        cm._plan_lessons_cache = (time.time() - 31, "stale")
        path = cm.plan_lessons_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fresh content")

        result = cm.load_plan_lessons()
        assert result == "fresh content"

    def test_save_invalidates_cache(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        cm._plan_lessons_cache = (time.time(), "old cached")
        cm.save_plan_lessons("new lessons from consolidation cycle")

        # Cache should now have the new value
        assert cm._plan_lessons_cache[1] == "new lessons from consolidation cycle"
        # And load should return it without reading file
        result = cm.load_plan_lessons()
        assert result == "new lessons from consolidation cycle"

    def test_save_too_short_ignored(self, plan_dir) -> None:
        import personalclaw.context_management as cm

        cm._plan_lessons_cache = (0.0, "")
        cm.save_plan_lessons("short")  # < 20 chars, should be ignored
        path = cm.plan_lessons_path()
        assert not path.exists()


# ── WS user experience edge cases ───────────────────────────────────


class TestWsNormalUserExperience:
    """Ensure dead client cleanup does NOT break normal user flows."""

    @pytest.mark.asyncio
    async def test_rapid_broadcasts_all_delivered(self, state: DashboardState) -> None:
        """Simulate a chat turn: ~20 rapid broadcasts all reach alive client."""
        ws = MagicMock(closed=False)
        ws.send_str = AsyncMock()
        state.register_ws(ws)

        for i in range(20):
            state.broadcast_ws("chat_segment", {"session": "s1", "i": i})

        assert ws.send_str.call_count == 20
        assert ws in state._ws_clients

    @pytest.mark.asyncio
    async def test_dead_client_does_not_block_alive_delivery(self, state: DashboardState) -> None:
        """Dead client mid-list doesn't prevent later alive clients from receiving."""
        alive1 = MagicMock(closed=False, send_str=AsyncMock())
        dead = MagicMock(closed=True, send_str=AsyncMock())
        alive2 = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(alive1)
        state.register_ws(dead)
        state.register_ws(alive2)

        state.broadcast_ws("chat_message", {"session": "s1", "content": "hello"})

        alive1.send_str.assert_called_once()
        alive2.send_str.assert_called_once()
        dead.send_str.assert_not_called()
        # Verify message content is identical for both alive clients
        assert alive1.send_str.call_args == alive2.send_str.call_args

    @pytest.mark.asyncio
    async def test_client_dies_mid_session_next_broadcast_cleans(
        self, state: DashboardState
    ) -> None:
        """Client starts alive, becomes closed, gets cleaned on next broadcast."""
        ws = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(ws)

        # First broadcast — alive, receives message
        state.broadcast_ws("chat_status", {"session": "s1", "status": "Thinking…"})
        assert ws.send_str.call_count == 1
        assert ws in state._ws_clients

        # Client disconnects (browser tab closed)
        ws.closed = True

        # Next broadcast — detected and removed
        state.broadcast_ws("chat_segment", {"session": "s1"})
        assert ws.send_str.call_count == 1  # no new call
        assert ws not in state._ws_clients

    def test_notification_push_with_mixed_clients(self, state: DashboardState) -> None:
        """push_notification → _send_ws_all path works with mixed alive/dead."""
        alive = MagicMock(closed=False, send_str=AsyncMock())
        dead = MagicMock(closed=True, send_str=AsyncMock())
        state.register_ws(alive)
        state.register_ws(dead)

        # Simulate push_notification which calls _send_ws_all internally
        state._send_ws_all(json.dumps({"type": "notification", "data": {"text": "hi"}}))

        alive.send_str.assert_called_once()
        dead.send_str.assert_not_called()

    def test_multiple_tabs_independent_lifecycle(self, state: DashboardState) -> None:
        """3 tabs open, 1 dies — other 2 unaffected across multiple broadcasts."""
        tab1 = MagicMock(closed=False, send_str=AsyncMock())
        tab2 = MagicMock(closed=False, send_str=AsyncMock())
        tab3 = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(tab1)
        state.register_ws(tab2)
        state.register_ws(tab3)

        # All 3 receive first broadcast
        state.broadcast_ws("slots", {"data": []})
        assert tab1.send_str.call_count == 1
        assert tab2.send_str.call_count == 1
        assert tab3.send_str.call_count == 1

        # Tab 2 dies
        tab2.closed = True

        # Remaining tabs still receive
        state.broadcast_ws("chat_done", {"session": "s1"})
        assert tab1.send_str.call_count == 2
        assert tab2.send_str.call_count == 1  # no new call
        assert tab3.send_str.call_count == 2
        assert len(state._ws_clients) == 2

    def test_send_str_raises_but_other_clients_still_served(self, state: DashboardState) -> None:
        """One client raises ConnectionResetError — others still get the message."""
        good1 = MagicMock(closed=False, send_str=AsyncMock())
        bad = MagicMock(closed=False, send_str=MagicMock(side_effect=OSError("broken pipe")))
        good2 = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(good1)
        state.register_ws(bad)
        state.register_ws(good2)

        state.broadcast_ws("test", {"x": 1})

        good1.send_str.assert_called_once()
        good2.send_str.assert_called_once()
        assert bad not in state._ws_clients
        assert good1 in state._ws_clients
        assert good2 in state._ws_clients

    def test_subagent_subscriber_alive_receives_chunks(self, state: DashboardState) -> None:
        """Normal subagent streaming: subscriber gets all chunks."""
        ws = MagicMock(closed=False, send_str=AsyncMock())
        state.subscribe_subagents(ws)

        for i in range(10):
            state.broadcast_ws_subagent_subscribers(
                "subagent_chunk", {"id": "a1", "text": f"chunk-{i}"}
            )

        assert ws.send_str.call_count == 10
        assert ws in state._ws_subagent_subscribers

    def test_log_subscriber_not_removed_by_broadcast_ws(self, state: DashboardState) -> None:
        """Log subscriber that's alive should survive broadcast_ws calls."""
        ws = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(ws)
        state.subscribe_logs(ws)

        # Regular broadcast should not affect log subscription
        for _ in range(5):
            state.broadcast_ws("chat_segment", {"session": "s1"})

        assert ws in state._ws_clients
        assert ws in state._ws_log_subscribers

    def test_dead_removal_is_immediate_not_deferred(self, state: DashboardState) -> None:
        """Dead client is removed in the same broadcast call, not deferred."""
        dead = MagicMock(closed=True, send_str=AsyncMock())
        state.register_ws(dead)
        assert len(state._ws_clients) == 1

        state.broadcast_ws("test", {})

        # Removed immediately — not waiting for next cycle
        assert len(state._ws_clients) == 0

    def test_message_json_integrity_preserved(self, state: DashboardState) -> None:
        """Verify the JSON message format is unchanged by our changes."""
        ws = MagicMock(closed=False, send_str=AsyncMock())
        state.register_ws(ws)

        state.broadcast_ws("chat_message", {"session": "s1", "content": "hello 你好"})

        sent = json.loads(ws.send_str.call_args[0][0])
        assert sent["type"] == "chat_message"
        assert sent["data"]["session"] == "s1"
        assert sent["data"]["content"] == "hello 你好"
