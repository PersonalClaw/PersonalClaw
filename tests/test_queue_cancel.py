"""Tests for queue cancel feature.

Covers:
- _ChatSession queue helper methods (queue_append, queue_insert, queue_pop, queue_remove_by_id)
- DELETE /api/chat/sessions/{session}/queue/{queue_id} endpoint
- Queue ID propagation in queue_push/queue_pop WS events
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.chat import api_chat_session_queue_cancel
from personalclaw.dashboard.sse import SseHub
from personalclaw.dashboard.state import DashboardState, _ChatSession

# ── Unit tests: _ChatSession queue helpers ──


class TestQueueHelpers:
    def test_queue_append_returns_id(self):
        session = _ChatSession("s1")
        qid = session.queue_append("hello")
        assert isinstance(qid, str)
        assert len(qid) == 12
        assert len(session._queue) == 1
        assert session._queue[0] == {"id": qid, "content": "hello"}

    def test_queue_append_unique_ids(self):
        session = _ChatSession("s1")
        id1 = session.queue_append("a")
        id2 = session.queue_append("b")
        assert id1 != id2

    def test_queue_insert_at_front(self):
        session = _ChatSession("s1")
        session.queue_append("second")
        qid = session.queue_insert(0, "first")
        assert session._queue[0]["content"] == "first"
        assert session._queue[0]["id"] == qid
        assert session._queue[1]["content"] == "second"

    def test_queue_pop_returns_dict(self):
        session = _ChatSession("s1")
        qid = session.queue_append("msg")
        item = session.queue_pop(0)
        assert item == {"id": qid, "content": "msg"}
        assert len(session._queue) == 0

    def test_queue_pop_fifo(self):
        session = _ChatSession("s1")
        session.queue_append("first")
        session.queue_append("second")
        item = session.queue_pop(0)
        assert item["content"] == "first"
        assert session._queue[0]["content"] == "second"

    def test_queue_remove_by_id_found(self):
        session = _ChatSession("s1")
        session.queue_append("keep")
        qid = session.queue_append("remove me")
        session.queue_append("also keep")
        content = session.queue_remove_by_id(qid)
        assert content == "remove me"
        assert len(session._queue) == 2
        assert [q["content"] for q in session._queue] == ["keep", "also keep"]

    def test_queue_remove_by_id_not_found(self):
        session = _ChatSession("s1")
        session.queue_append("msg")
        result = session.queue_remove_by_id("nonexistent")
        assert result is None
        assert len(session._queue) == 1

    def test_queue_remove_by_id_empty_queue(self):
        session = _ChatSession("s1")
        result = session.queue_remove_by_id("anything")
        assert result is None

    def test_queue_remove_by_id_duplicate_content(self):
        """When two items have the same content, only the one with matching ID is removed."""
        session = _ChatSession("s1")
        id1 = session.queue_append("same text")
        id2 = session.queue_append("same text")
        content = session.queue_remove_by_id(id2)
        assert content == "same text"
        assert len(session._queue) == 1
        assert session._queue[0]["id"] == id1


# ── API tests: DELETE /api/chat/sessions/{session}/queue/{queue_id} ──


def _make_state():
    state = DashboardState.__new__(DashboardState)
    state._sessions = {}
    state._ws_clients = []
    state._sse = SseHub()
    state._background_tasks = set()
    # YOLO is process-global (personalclaw.trust_mode); the autouse
    # _reset_trust_mode fixture keeps it OFF — no instance field to set.
    state._restricted_keys = set()
    state.sessions = None
    state.conversation_log = None
    state.channel_manager = None
    return state


def _make_app(state):
    app = web.Application()
    app["state"] = state
    app.router.add_delete(
        "/api/chat/sessions/{session}/queue/{queue_id}",
        api_chat_session_queue_cancel,
    )
    return app


class TestQueueCancelEndpoint:
    @pytest.mark.asyncio
    async def test_cancel_removes_from_queue(self):
        """Cancelling a queued message removes it from the backend queue."""
        state = _make_state()
        session = state.get_or_create_session("chat-1")
        qid = session.queue_append("cancel me")
        session.append("queued", "cancel me", json.dumps({"queue_id": qid}))

        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete(f"/api/chat/sessions/chat-1/queue/{qid}")
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True
                assert "cancel me" in data["content"]

        assert len(session._queue) == 0
        # Queued message should also be removed from messages
        assert not any(m["role"] == "queued" for m in session.messages)

    @pytest.mark.asyncio
    async def test_cancel_session_not_found(self):
        state = _make_state()
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/chat/sessions/nonexistent/queue/abc")
                assert resp.status == 404

    @pytest.mark.asyncio
    async def test_cancel_queue_id_not_found(self):
        state = _make_state()
        session = state.get_or_create_session("chat-1")
        session.queue_append("keep me")

        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/chat/sessions/chat-1/queue/wrong-id")
                assert resp.status == 404
                data = await resp.json()
                assert "not found" in data["error"]

        # Queue should be untouched
        assert len(session._queue) == 1

    @pytest.mark.asyncio
    async def test_cancel_middle_item(self):
        """Cancelling a middle item preserves order of remaining items."""
        state = _make_state()
        session = state.get_or_create_session("chat-1")
        session.queue_append("first")
        qid2 = session.queue_append("second")
        session.queue_append("third")

        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete(f"/api/chat/sessions/chat-1/queue/{qid2}")
                assert resp.status == 200

        assert [q["content"] for q in session._queue] == ["first", "third"]

    @pytest.mark.asyncio
    async def test_cancel_broadcasts_ws_event(self):
        """Cancelling broadcasts a queue_cancel WS event."""
        state = _make_state()
        session = state.get_or_create_session("chat-1")
        qid = session.queue_append("cancel me")
        state.broadcast_ws = MagicMock()

        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                await client.delete(f"/api/chat/sessions/chat-1/queue/{qid}")

        state.broadcast_ws.assert_any_call(
            "queue_cancel",
            {"session": "chat-1", "queue_id": qid, "content": "cancel me"},
        )

    @pytest.mark.asyncio
    async def test_cancel_with_duplicate_content(self):
        """When two messages have identical content, only the targeted one is removed."""
        state = _make_state()
        session = state.get_or_create_session("chat-1")
        id1 = session.queue_append("same text")
        id2 = session.queue_append("same text")
        # Add queued placeholders with queue_id in cls metadata
        session.append("queued", "same text", json.dumps({"queue_id": id1}))
        session.append("queued", "same text", json.dumps({"queue_id": id2}))

        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_app(state)
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete(f"/api/chat/sessions/chat-1/queue/{id2}")
                assert resp.status == 200

        assert len(session._queue) == 1
        assert session._queue[0]["id"] == id1
        # The first placeholder (id1) should remain, second (id2) removed
        queued_msgs = [m for m in session.messages if m.get("role") == "queued"]
        assert len(queued_msgs) == 1
        cls = json.loads(queued_msgs[0].get("cls", "{}"))
        assert cls.get("queue_id") == id1
