"""Tests for dashboard chat session — session management, pagination, history persistence."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import (
    AsyncIterator,
    _make_app,
    _make_app_with_agent_routes,
    _make_folder_app,
    _make_state,
)

from personalclaw.dashboard.state import _MAX_SESSION_MESSAGES, DashboardState, _ChatSession
from personalclaw.history import ConversationLog

# ── Session unit tests ──


class TestChatSession:
    def test_append_and_drain(self):
        session = _ChatSession("s1")
        session.append("user", "hello", "msg")
        session.append("assistant", "hi", "msg")
        pending = session.drain()
        assert len(pending) == 2
        assert pending[0]["role"] == "user"
        assert pending[1]["role"] == "assistant"
        assert session.drain() == []

    def test_drain_clears_stale_pending_after_reader_disconnect(self):
        """Simulate SSE reader disconnect: pending chunks must be discarded."""
        session = _ChatSession("s1")
        session._has_reader = True
        session.append("assistant", "stale response", "msg")
        assert len(session._pending) == 1
        session.drain()
        session._has_reader = False
        assert session._pending == []
        assert session.drain() == []
        session.append("assistant", "fresh response", "msg")
        pending = session.drain()
        assert len(pending) == 1
        assert pending[0]["content"] == "fresh response"

    def test_total_messages_survives_trim(self):
        session = _ChatSession("s1")
        count = _MAX_SESSION_MESSAGES + 100
        for i in range(count):
            session.append("user", f"msg {i}")
        assert len(session.messages) == _MAX_SESSION_MESSAGES
        assert session.total_messages == count

    def test_trim_keeps_latest(self):
        session = _ChatSession("s1")
        count = _MAX_SESSION_MESSAGES + 50
        for i in range(count):
            session.append("user", f"msg {i}")
        assert session.messages[0]["content"] == "msg 50"
        assert session.messages[-1]["content"] == f"msg {count - 1}"

    def test_to_dict(self):
        session = _ChatSession("s1", title="Test Chat")
        session.append("user", "hi")
        d = session.to_dict()
        assert d["key"] == "s1"
        assert d["title"] == "Test Chat"
        assert d["messages"] == 1
        assert d["running"] is False
        assert d["pending_approval"] is False

    def test_pending_approval_flag(self):
        session = _ChatSession("s1")
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["test"] = fut
        assert session.to_dict()["pending_approval"] is True
        fut.set_result("approved")
        assert session.to_dict()["pending_approval"] is False
        loop.close()

    def test_pending_subagent_failures_initialized_empty(self):
        session = _ChatSession("s1")
        assert session._pending_subagent_failures == []

    def test_pending_subagent_failures_drain(self):
        session = _ChatSession("s1")
        session._pending_subagent_failures.append(
            "[Subagent completion event]\nAgent `a1` ❌ timed out"
        )
        session._pending_subagent_failures.append(
            "[Subagent completion event]\nAgent `a2` ❌ timed out"
        )
        # Simulate drain logic from _run_chat
        failures = session._pending_subagent_failures[:]
        session._pending_subagent_failures.clear()
        message = "\n\n".join(failures) + "\n\n" + "user message"
        assert "[Subagent completion event]" in message
        assert "Agent `a1`" in message
        assert "Agent `a2`" in message
        assert message.endswith("user message")
        assert session._pending_subagent_failures == []


@pytest.mark.asyncio
class TestApiChatDrainOnDisconnect:
    """Cover the session.drain() call in chat_handlers' SSE finally block."""

    async def test_sse_reader_drains_pending_on_cancel(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)

        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")

        async def fake_run_chat(st, sl, msg):
            sl.append("chunk", "partial answer", "chunk")
            await asyncio.sleep(60)

        monkeypatch.setattr("personalclaw.dashboard.chat_handlers._run_chat", fake_run_chat)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat",
                json={"message": "hello", "session": "s1"},
                timeout=None,
            )
            line = b""
            async for chunk in resp.content.iter_any():
                line += chunk
                if b"partial answer" in line:
                    break

            resp.close()
            await asyncio.sleep(0.1)

        assert session._pending == []
        assert session._has_reader is False


# ── Session detail pagination (HTTP) ──


class TestSessionDetailPagination:
    @pytest.mark.asyncio
    async def test_default_returns_latest(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("test")
        for i in range(10):
            session.append("user", f"msg {i}")
        session.drain()
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/test")
            data = await resp.json()
            assert data["total"] == 10
            assert len(data["messages"]) == 10
            assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_pagination_with_before(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("test")
        log = state.conversation_log
        for i in range(300):
            log.append("dashboard:test", "user", f"msg {i}")
            session.append("user", f"msg {i}")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/test?limit=200")
            data = await resp.json()
            assert data["has_more"] is True
            assert len(data["messages"]) == 200
            assert data["total"] == 300

            resp = await client.get("/api/chat/sessions/test?limit=200&before=100")
            data = await resp.json()
            assert len(data["messages"]) == 100
            assert data["has_more"] is False
            assert data["messages"][0]["content"] == "msg 0"

    @pytest.mark.asyncio
    async def test_empty_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("empty")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/empty")
            data = await resp.json()
            assert data["total"] == 0
            assert data["messages"] == []
            assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/nonexistent")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_disk_only_session_rehydrates_on_open(self, tmp_path, monkeypatch):
        """A session on disk but NOT in memory (post-restart) opens via rehydrate,
        not 404 — otherwise chat history is unreachable after a gateway restart."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        for i in range(4):
            log.append("dashboard:ghost", "user", f"msg {i}")
        assert "ghost" not in state._sessions  # never loaded into memory
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/ghost")
            assert resp.status == 200
            data = await resp.json()
            assert data["total"] == 4


# ── Chat history LIST: merge in-memory + disk ──


class TestChatSessionsListMerge:
    @pytest.mark.asyncio
    async def test_list_includes_disk_only_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:on_disk", "user", "hi")  # disk only, not in memory
        async with TestClient(TestServer(_make_app(state))) as client:
            data = await (await client.get("/api/chat/sessions")).json()
            keys = {s["key"] for s in data}
            assert "on_disk" in keys

    @pytest.mark.asyncio
    async def test_list_tags_worker_sessions_by_origin(self, tmp_path, monkeypatch):
        # Unified design: worker sessions are NOT dropped server-side — they're
        # surfaced but tagged with a non-"manual" origin so the UI can default-hide
        # them behind a filter and link each back to its cockpit.
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:loop-abc123", "user", "worker noise")
        log.append("dashboard:campaign-def456", "user", "worker noise")
        log.append("dashboard:real_chat", "user", "real conversation")
        async with TestClient(TestServer(_make_app(state))) as client:
            data = await (await client.get("/api/chat/sessions")).json()
            by_key = {s["key"]: s for s in data}
            assert by_key["real_chat"]["origin"] == "manual"
            assert by_key["loop-abc123"]["origin"] == "loop"
            assert by_key["campaign-def456"]["origin"] == "campaign"

    @pytest.mark.asyncio
    async def test_in_memory_session_not_duplicated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:dup", "user", "hi")  # on disk
        sess = state.get_or_create_session("dup")  # AND in memory
        sess.append("user", "hi")
        async with TestClient(TestServer(_make_app(state))) as client:
            data = await (await client.get("/api/chat/sessions")).json()
            assert [s["key"] for s in data].count("dup") == 1

    @pytest.mark.asyncio
    async def test_list_tags_in_memory_worker_sessions_by_origin(self, tmp_path, monkeypatch):
        """A loop/campaign worker session live IN MEMORY is surfaced with a non-manual
        origin too (same include-and-tag contract as the disk branch), so the UI can
        filter it out — it is not dropped server-side."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("loop-live123").append("user", "worker turn")
        state.get_or_create_session("real_chat").append("user", "hi")
        async with TestClient(TestServer(_make_app(state))) as client:
            by_key = {s["key"]: s for s in await (await client.get("/api/chat/sessions")).json()}
            assert by_key["real_chat"]["origin"] == "manual"
            assert by_key["loop-live123"]["origin"] == "loop"


# ── History persistence and disk fallback ──


class TestHistoryPersistence:
    def test_tool_messages_saved(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard:s1", "user", "hello")
        log.append("dashboard:s1", "tool", "✅ bash")
        log.append("dashboard:s1", "assistant", "hi there")
        msgs = log.read_messages("dashboard:s1")
        assert len(msgs) == 3
        assert msgs[1]["role"] == "tool"

    @pytest.mark.asyncio
    async def test_disk_fallback_for_trimmed_session(self, tmp_path, monkeypatch):
        """Default view uses in-memory; pagination of older messages uses disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("big")
        log = state.conversation_log

        # Use a count that fits in memory — test disk pagination without trim
        for i in range(300):
            log.append("dashboard:big", "user", f"msg {i}")
            session.append("user", f"msg {i}")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            # Default: in-memory
            resp = await client.get("/api/chat/sessions/big?limit=200")
            data = await resp.json()
            assert data["total"] == 300
            assert data["has_more"] is True
            assert data["messages"][-1]["content"] == "msg 299"

            # Pagination with before: falls back to disk
            resp = await client.get("/api/chat/sessions/big?limit=200&before=100")
            data = await resp.json()
            assert len(data["messages"]) == 100
            assert data["messages"][0]["content"] == "msg 0"
            assert data["has_more"] is False


# ── Session lifecycle ──


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("a")
        state.get_or_create_session("b")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions")
            data = await resp.json()
            keys = [s["key"] for s in data]
            assert "a" in keys and "b" in keys

    @pytest.mark.asyncio
    async def test_approve_no_pending(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "approved"})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_approve_resolves_future(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["test"] = fut

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "approved"})
            data = await resp.json()
            assert data["ok"] is True
            assert fut.result() == "approved"

    @pytest.mark.asyncio
    async def test_trust_sets_flag_and_approves(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["test"] = fut

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "trust"})
            data = await resp.json()
            assert data["ok"] is True
            assert session._trust is True
            assert fut.result() == "approved"

    @pytest.mark.asyncio
    async def test_approve_broadcasts_approval_resolved_single_pending(self, tmp_path, monkeypatch):
        """Single pending future without explicit request_id: extracts id and broadcasts."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        session._approval_futures["req-abc"] = fut
        state.broadcast_ws = MagicMock()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "approved"})
            assert (await resp.json())["ok"] is True
            state.broadcast_ws.assert_any_call(
                "approval_resolved", {"id": "req-abc", "approved": True}
            )

    @pytest.mark.asyncio
    async def test_approve_broadcasts_with_explicit_request_id(self, tmp_path, monkeypatch):
        """Explicit request_id is forwarded in the broadcast."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        session._approval_futures["req-xyz"] = fut
        state.broadcast_ws = MagicMock()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/approve",
                json={"action": "approved", "request_id": "req-xyz"},
            )
            assert (await resp.json())["ok"] is True
            state.broadcast_ws.assert_any_call(
                "approval_resolved", {"id": "req-xyz", "approved": True}
            )

    @pytest.mark.asyncio
    async def test_reject_broadcasts_approved_false(self, tmp_path, monkeypatch):
        """Rejection broadcasts approved=False."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        session._approval_futures["req-rej"] = fut
        state.broadcast_ws = MagicMock()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/approve",
                json={"action": "rejected", "request_id": "req-rej"},
            )
            assert (await resp.json())["ok"] is True
            state.broadcast_ws.assert_any_call(
                "approval_resolved", {"id": "req-rej", "approved": False}
            )


# ── Multi-session isolation ──


class TestMultiSessionIsolation:
    @pytest.mark.asyncio
    async def test_sessions_have_independent_messages(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        s1 = state.get_or_create_session("s1")
        s2 = state.get_or_create_session("s2")
        s1.append("user", "hello from s1")
        s2.append("user", "hello from s2")
        s2.append("assistant", "reply in s2")
        s1.drain()
        s2.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            r1 = await (await client.get("/api/chat/sessions/s1")).json()
            r2 = await (await client.get("/api/chat/sessions/s2")).json()
            assert r1["total"] == 1
            assert r2["total"] == 2
            assert r1["messages"][0]["content"] == "hello from s1"


# ── Full pagination walk (simulates infinite scroll) ──


class TestFullPaginationWalk:
    @pytest.mark.asyncio
    async def test_walk_all_pages(self, tmp_path, monkeypatch):
        """Simulate frontend infinite scroll — walk backwards through all messages."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("walk")
        log = state.conversation_log
        total_msgs = 450

        for i in range(total_msgs):
            log.append("dashboard:walk", "user", f"msg {i}")
            session.append("user", f"msg {i}")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            all_collected: list[str] = []
            before = None
            pages = 0

            while True:
                url = "/api/chat/sessions/walk?limit=100"
                if before is not None:
                    url += f"&before={before}"
                resp = await client.get(url)
                data = await resp.json()
                msgs = data["messages"]
                all_collected = [m["content"] for m in msgs] + all_collected
                pages += 1

                if not data["has_more"]:
                    break
                before = data["total"] - len(all_collected)

            assert len(all_collected) == total_msgs
            assert all_collected[0] == "msg 0"
            assert all_collected[-1] == f"msg {total_msgs - 1}"
            assert pages > 1

    @pytest.mark.asyncio
    async def test_walk_with_trimmed_memory(self, tmp_path, monkeypatch):
        """Pagination with before uses disk — can access all messages."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("trim")
        log = state.conversation_log

        for i in range(400):
            log.append("dashboard:trim", "user", f"msg {i}")
            session.append("user", f"msg {i}")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            # Default: in-memory
            resp = await client.get("/api/chat/sessions/trim?limit=200")
            data = await resp.json()
            assert data["total"] == 400
            assert data["messages"][-1]["content"] == "msg 399"

            # Pagination: disk has all 400
            resp = await client.get("/api/chat/sessions/trim?limit=200&before=200")
            data = await resp.json()
            assert data["total"] == 400
            assert data["messages"][0]["content"] == "msg 0"
            assert data["has_more"] is False


# ── SSE broadcast: _has_reader mutual exclusion ──


class TestHasReaderFlag:
    """Verify _has_reader prevents duplicate message delivery."""

    def test_broadcast_skipped_when_reader_active(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        received: list[dict] = []
        session._on_message = lambda key, msg: received.append(msg)

        session._has_reader = True
        session.append("assistant", "should not broadcast")
        assert len(received) == 0

    def test_broadcast_fires_when_no_reader(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        received: list[dict] = []
        session._on_message = lambda key, msg: received.append(msg)

        session._has_reader = False
        session.append("assistant", "should broadcast")
        assert len(received) == 1
        assert received[0]["role"] == "assistant"

    def test_chunk_never_broadcast(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        received: list[dict] = []
        session._on_message = lambda key, msg: received.append(msg)

        session._has_reader = False
        session.append("chunk", "text")
        assert len(received) == 0

    def test_user_never_broadcast(self, tmp_path, monkeypatch):
        """User messages are added optimistically by frontend — no SSE broadcast."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        received: list[dict] = []
        session._on_message = lambda key, msg: received.append(msg)

        session._has_reader = False
        session.append("user", "hello")
        assert len(received) == 0

    def test_tool_and_permission_broadcast(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        received: list[dict] = []
        session._on_message = lambda key, msg: received.append(msg)

        session._has_reader = False
        session.append("tool", "✅ bash")
        session.append("permission", "run ls")
        assert len(received) == 2


# ── Chunk cleanup after response ──


class TestChunkCleanup:
    def test_chunks_removed_from_messages(self):
        """After assistant response, chunk messages should be cleaned up."""
        session = _ChatSession("s1")
        session.append("user", "hello")
        session.append("chunk", "He")
        session.append("chunk", "llo")
        session.append("chunk", " world")
        assert sum(1 for m in session.messages if m["role"] == "chunk") == 3

        # Simulate what _run_chat does after streaming
        session.messages = [m for m in session.messages if m.get("role") != "chunk"]
        session.append("assistant", "Hello world")
        assert sum(1 for m in session.messages if m["role"] == "chunk") == 0
        assert session.messages[-1]["role"] == "assistant"
        assert session.messages[0]["role"] == "user"


# ── _prepare_messages filtering ──


class TestPrepareMessages:
    def test_queued_preserved_done_stripped(self):
        """queued messages must survive _prepare_messages so the frontend shows the banner after tab switch."""  # noqa: E501
        from personalclaw.dashboard.chat import _prepare_messages

        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "queued", "content": "next msg"},
            {"role": "done", "content": ""},
            {"role": "assistant", "content": "hi"},
        ]
        out = _prepare_messages(msgs, running=False)
        roles = [m["role"] for m in out]
        assert "queued" in roles, "queued must be preserved for tab-switch indicator"
        assert "done" not in roles, "done must be stripped"

    def test_chunks_collapsed_to_streaming(self):
        """Trailing chunks should be collapsed into a single streaming message."""
        from personalclaw.dashboard.chat import _prepare_messages

        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "chunk", "content": "Hel"},
            {"role": "chunk", "content": "lo"},
        ]
        out = _prepare_messages(msgs, running=True)
        assert out[-1]["role"] == "streaming"
        assert "Hel" in out[-1]["content"]

    def test_queued_placeholder_removed_on_processing(self):
        """When a queued message starts processing, its placeholder is replaced by a user entry."""
        import json

        from personalclaw.dashboard.chat import _remove_queued_by_id

        session = _ChatSession("s1")
        session.append("user", "first")
        qid = session.queue_append("second")
        session.append("queued", "second", json.dumps({"queue_id": qid}))

        item = session.queue_pop(0)
        _remove_queued_by_id(session.messages, item["id"])
        session.append("user", item["content"], "msg msg-u")

        roles = [m["role"] for m in session.messages]
        assert "queued" not in roles, "queued placeholder must be removed once processing starts"
        assert roles.count("user") == 2

    def test_duplicate_queued_removes_only_targeted(self):
        """When the same text is queued twice, only the targeted placeholder is removed by ID."""
        import json

        from personalclaw.dashboard.chat import _remove_queued_by_id

        session = _ChatSession("s1")
        qid1 = session.queue_append("hello")
        qid2 = session.queue_append("hello")
        session.append("queued", "hello", json.dumps({"queue_id": qid1}))
        session.append("queued", "hello", json.dumps({"queue_id": qid2}))

        item = session.queue_pop(0)
        _remove_queued_by_id(session.messages, item["id"])
        session.append("user", item["content"], "msg msg-u")

        queued = [m for m in session.messages if m.get("role") == "queued"]
        assert len(queued) == 1, "second queued placeholder must survive"
        # Verify the surviving placeholder is the one with qid2
        surviving_cls = json.loads(queued[0].get("cls", "{}"))
        assert surviving_cls.get("queue_id") == qid2


# ── History save on close (not per-turn) ──


class TestHistorySaveOnClose:
    @pytest.mark.asyncio
    async def test_delete_hard_purges_history(self, tmp_path, monkeypatch):
        """Product decision (2026-07-03): the explicit Delete button HARD-deletes —
        it purges the persisted history rather than soft-saving it. (Soft-close /
        archive now lives only in /cleanup — see TestCleanup.)"""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hello")
        session.append("assistant", "hi")
        session.drain()
        # Persist first (a real chat has an on-disk transcript before delete).
        from personalclaw.dashboard.chat import _save_session_to_history

        _save_session_to_history(state, session, force=True)
        assert state.conversation_log.has_log("dashboard:s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.delete("/api/chat/sessions/s1")
            data = await resp.json()
            assert data["ok"] is True

        # Hard-deleted: the JSONL history is GONE, not soft-saved.
        assert not state.conversation_log.has_log("dashboard:s1")
        assert state.conversation_log.read_messages("dashboard:s1") == []

    def test_transient_roles_excluded_from_history(self, tmp_path, monkeypatch):
        """chunk, done, queued, permission are not persisted (the _save_session_to_history
        contract — exercised directly since delete now hard-purges rather than saves)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import _save_session_to_history

        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "run ls")
        session.append("permission", "ls")
        session.append("tool", "ls")
        session.append("queued", "next msg")
        session.append("chunk", "partial")
        session.append("done", "")
        session.append("assistant", "done")
        session.drain()

        _save_session_to_history(state, session, force=True)

        msgs = state.conversation_log.read_messages("dashboard:s1")
        roles = [m["role"] for m in msgs]
        assert "chunk" not in roles
        assert "done" not in roles
        assert "queued" not in roles
        assert "permission" not in roles
        assert roles == ["user", "tool", "assistant"]

    def test_close_saves_mode_to_history(self, tmp_path, monkeypatch):
        """Session mode is persisted in session metadata on close."""
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("modesess1", mode="plan")
        session.append("user", "plan")
        session.drain()

        _save_session_to_history(state, session, closed=True)

        meta = state.conversation_log._read_metadata("dashboard:modesess1")
        assert meta.get("mode") == "plan"

    def test_close_does_not_persist_trust(self, tmp_path, monkeypatch):
        """Trust flags are ephemeral — not written to session metadata."""
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("t1")
        session._trust = True
        session._trust_reads = True
        session.append("user", "hi")
        session.drain()
        _save_session_to_history(state, session, closed=True)
        meta = state.conversation_log._read_metadata("dashboard:t1")
        assert meta.get("trust") is None
        assert meta.get("trust_reads") is None


# ── Resume deduplication ──


class TestResumeDedupe:
    @pytest.mark.asyncio
    async def test_resume_existing_session_returns_it(self, tmp_path, monkeypatch):
        """Resuming a session that's already active should return existing session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:s1", "user", "hello")

        async with TestClient(TestServer(_make_app(state))) as client:
            # First resume
            r1 = await (
                await client.post("/api/chat/sessions/s1/resume", json={"key": "dashboard:s1"})
            ).json()
            assert r1["ok"] is True

            # Add a message to the active session
            state._sessions["s1"].append("user", "new msg")
            state._sessions["s1"].drain()

            # Second resume — should return existing with new msg
            r2 = await (
                await client.post("/api/chat/sessions/s1/resume", json={"key": "dashboard:s1"})
            ).json()
            assert r2["ok"] is True
            assert r2["total"] == 2  # original + new

            # Should still be one session, not two
            resp = await client.get("/api/chat/sessions")
            sessions = await resp.json()
            assert sum(1 for s in sessions if s["key"] == "s1") == 1

    @pytest.mark.asyncio
    async def test_resume_then_save_no_duplicate_history(self, tmp_path, monkeypatch):
        """Resume → add messages → save should rewrite the transcript, not append a
        duplicate copy. (Uses _save_session_to_history — the archive/persist path —
        since Delete now hard-purges rather than saving.)"""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import _save_session_to_history

        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:s1", "user", "hello")
        log.append("dashboard:s1", "assistant", "hi")

        async with TestClient(TestServer(_make_app(state))) as client:
            await client.post("/api/chat/sessions/s1/resume", json={"key": "dashboard:s1"})
            state._sessions["s1"].append("user", "new question")
            state._sessions["s1"].append("assistant", "new answer")
            state._sessions["s1"].drain()
            _save_session_to_history(state, state._sessions["s1"], force=True)

        # 4 messages (original 2 + new 2), not duplicated (full-file rewrite).
        msgs = log.read_messages("dashboard:s1")
        assert len(msgs) == 4


# ── History key prefix handling ──


class TestHistoryKeyPrefix:
    @pytest.mark.asyncio
    async def test_no_double_dashboard_prefix(self, tmp_path, monkeypatch):
        """A 'dashboard:'-prefixed session key must not get double-prefixed on save
        (dashboard:dashboard:…). Exercised via the save path since Delete now purges."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import _save_session_to_history

        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:chat-1", "user", "hello")

        async with TestClient(TestServer(_make_app(state))) as client:
            await client.post(
                "/api/chat/sessions/dashboard:chat-1/resume",
                json={"key": "dashboard:chat-1"},
            )
            state._sessions["dashboard:chat-1"].append("user", "new msg")
            state._sessions["dashboard:chat-1"].drain()
            _save_session_to_history(state, state._sessions["dashboard:chat-1"], force=True)

        # Saved under dashboard:chat-1, not dashboard:dashboard:chat-1.
        msgs = log.read_messages("dashboard:chat-1")
        assert len(msgs) == 2
        assert log.read_messages("dashboard:dashboard:chat-1") == []


# ── Default view uses in-memory (not stale disk) ──


class TestInMemoryAuthority:
    @pytest.mark.asyncio
    async def test_default_view_shows_current_messages(self, tmp_path, monkeypatch):
        """Default session detail should return in-memory messages, not stale disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        # Stale disk data
        log.append("dashboard:s1", "user", "old question")
        log.append("dashboard:s1", "assistant", "old answer")

        # Active session with different messages
        session = state.get_or_create_session("s1")
        session.append("user", "new question")
        session.append("tool", "✅ running")
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/s1")
            data = await resp.json()
            # Should show in-memory (2 msgs), not disk (2 different msgs)
            assert data["total"] == 2
            assert data["messages"][0]["content"] == "new question"
            assert data["messages"][1]["content"] == "✅ running"

    @pytest.mark.asyncio
    async def test_full_load_prepends_older_disk_messages(self, tmp_path, monkeypatch):
        """No-limit path prepends older disk messages when restore truncated."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        # Simulate: 8 messages on disk total (5 older + 3 recent)
        for i in range(8):
            log.append("dashboard:s2", "user", f"msg {i}")
        # Session has only the last 3 in memory (simulating truncated restore)
        session = state.get_or_create_session("s2")
        session.append("user", "msg 5")
        session.append("user", "msg 6")
        session.append("user", "msg 7")
        session.drain()
        # Flag that restore truncated older messages
        session._disk_older_count = 5

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/chat/sessions/s2")
            data = await resp.json()
            assert data["total"] == 8  # 5 older + 3 recent
            assert data["has_more"] is False
            assert data["messages"][0]["content"] == "msg 0"
            assert data["messages"][4]["content"] == "msg 4"
            assert data["messages"][5]["content"] == "msg 5"

    @pytest.mark.asyncio
    async def test_legacy_pagination_with_limit(self, tmp_path, monkeypatch):
        """Legacy limit-based pagination reads from chained disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        for i in range(10):
            log.append("dashboard:s3", "user", f"msg {i}")
        session = state.get_or_create_session("s3")  # noqa: F841

        async with TestClient(TestServer(_make_app(state))) as client:
            # limit=3 returns last 3, has_more=True
            resp = await client.get("/api/chat/sessions/s3?limit=3")
            data = await resp.json()
            assert data["total"] == 10
            assert data["has_more"] is True
            assert len(data["messages"]) == 3
            assert data["messages"][-1]["content"] == "msg 9"

            # limit=3&before=5 returns msgs 2-4
            resp = await client.get("/api/chat/sessions/s3?limit=3&before=5")
            data = await resp.json()
            assert data["has_more"] is True  # msgs 0, 1 still older
            assert [m["content"] for m in data["messages"]] == ["msg 2", "msg 3", "msg 4"]

            # before=2 returns last 2 older
            resp = await client.get("/api/chat/sessions/s3?limit=100&before=2")
            data = await resp.json()
            assert data["has_more"] is False
            assert [m["content"] for m in data["messages"]] == ["msg 0", "msg 1"]


# ── Session rename tests ──


class TestSessionRename:
    @pytest.mark.asyncio
    async def test_rename_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_session_title = MagicMock()
        session = state.get_or_create_session("s1")
        session.append("user", "hello")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/title", json={"title": "My Chat"})
            data = await resp.json()
            assert resp.status == 200
            assert data["ok"] is True
            assert data["title"] == "My Chat"
            assert session.title == "My Chat"
            assert session._titled is True
            state.push_session_title.assert_called_once_with("s1", "My Chat")

    @pytest.mark.asyncio
    async def test_rename_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/nonexistent/title", json={"title": "X"})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_rename_empty_title(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/title", json={"title": "  "})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rename_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch(
                "/api/chat/sessions/s1/title",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rename_truncates_at_200(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_session_title = MagicMock()
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            long_title = "x" * 300
            resp = await client.patch("/api/chat/sessions/s1/title", json={"title": long_title})
            data = await resp.json()
            assert resp.status == 200
            assert len(data["title"]) == 200
            assert state._sessions["s1"].title == "x" * 200
            state.push_session_title.assert_called_once_with("s1", "x" * 200)

    @pytest.mark.asyncio
    async def test_resumed_session_preserves_title(self, tmp_path, monkeypatch):
        """Resumed session should set _titled=True so auto-title doesn't overwrite."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        log = state.conversation_log
        log.append("dashboard:s1", "user", "hello")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/resume",
                json={"key": "dashboard:s1", "title": "My Custom Title"},
            )
            assert resp.status == 200
            session = state._sessions["s1"]
            assert session.title == "My Custom Title"
            assert session._titled is True


# ── Session color tests ──


class TestSessionColor:
    @pytest.mark.asyncio
    async def test_set_color_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": 3})
            data = await resp.json()
            assert resp.status == 200
            assert data["ok"] is True
            assert data["color_index"] == 3
            assert state._sessions["s1"].color_index == 3
            state.push_sessions_update.assert_called()

    @pytest.mark.asyncio
    async def test_set_color_null(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        session.color_index = 5

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": None})
            data = await resp.json()
            assert resp.status == 200
            assert data["color_index"] is None
            assert session.color_index is None

    @pytest.mark.asyncio
    async def test_set_color_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/nope/color", json={"color_index": 0})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_set_color_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch(
                "/api/chat/sessions/s1/color",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_set_color_negative_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": -1})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_set_color_bool_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": True})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_set_color_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": 0})
            data = await resp.json()
            assert resp.status == 200
            assert data["color_index"] == 0
            assert session.color_index == 0

    @pytest.mark.asyncio
    async def test_set_color_large_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.patch("/api/chat/sessions/s1/color", json={"color_index": 99999})
            assert resp.status == 400

    def test_color_zero_persisted(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.color_index = 0
        session.append("user", "hello")
        session.drain()

        _save_session_to_history(state, session, closed=True)

        meta = state.conversation_log._read_metadata("dashboard:s1")
        assert meta.get("color_index") == 0

    def test_color_persisted_in_history(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.color_index = 4
        session.append("user", "hello")
        session.drain()

        _save_session_to_history(state, session, closed=True)

        meta = state.conversation_log._read_metadata("dashboard:s1")
        assert meta.get("color_index") == 4

    def test_color_null_not_persisted(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hello")
        session.drain()

        _save_session_to_history(state, session, closed=True)

        meta = state.conversation_log._read_metadata("dashboard:s1")
        assert "color_index" not in meta


# ── Slash command tests ──


class TestBlockedSlashCommands:
    """Tests for _BLOCKED_SLASH_COMMANDS blocking dangerous commands."""

    def test_quit_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/quit" in _BLOCKED_SLASH_COMMANDS

    def test_exit_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/exit" in _BLOCKED_SLASH_COMMANDS

    def test_q_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/q" in _BLOCKED_SLASH_COMMANDS

    def test_editor_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/editor" in _BLOCKED_SLASH_COMMANDS

    def test_chat_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/chat" in _BLOCKED_SLASH_COMMANDS

    def test_paste_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/paste" in _BLOCKED_SLASH_COMMANDS

    def test_reply_is_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/reply" in _BLOCKED_SLASH_COMMANDS

    def test_compact_is_not_blocked(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS

        assert "/compact" not in _BLOCKED_SLASH_COMMANDS

    def test_blocked_is_subset_of_slash(self):
        from personalclaw.dashboard.chat import _BLOCKED_SLASH_COMMANDS, _SLASH_COMMANDS

        assert _BLOCKED_SLASH_COMMANDS.issubset(_SLASH_COMMANDS)

    @pytest.mark.asyncio
    async def test_blocked_command_returns_warning_no_session(self, tmp_path, monkeypatch):
        """Posting /quit should add warning to session and never acquire a session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "/quit")

        # Should have the warning message
        texts = [m["content"] for m in session.messages if m.get("role") == "assistant"]
        assert any("not available in the dashboard" in t for t in texts)
        # Should never have called get_or_create (no session acquired)
        state.sessions.get_or_create.assert_not_called()


# ── Background session leak regression ──


class TestTitleGenerationSessionLeak:
    """_generate_title_via_provider must release BACKGROUND_KEY even when stream() raises."""

    @pytest.mark.asyncio
    async def test_background_session_released_on_stream_error(self, tmp_path):
        from personalclaw.dashboard.chat import _generate_title_via_provider
        from personalclaw.session import BACKGROUND_KEY

        state = _make_state(tmp_path)

        # Mock client whose stream() raises mid-iteration
        mock_client = MagicMock()

        async def _exploding_stream(prompt):
            raise RuntimeError("throttle / ACP error")
            yield  # noqa: unreachable — makes this an async generator

        mock_client.stream = _exploding_stream
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()

        messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

        with pytest.raises(RuntimeError, match="throttle"):
            await _generate_title_via_provider(state, messages)

        # The critical assertion: release MUST be called even though stream() raised
        state.sessions.release.assert_called_once_with(BACKGROUND_KEY)

    @pytest.mark.asyncio
    async def test_permission_request_rejected_during_title_gen(self, tmp_path):
        from personalclaw.dashboard.chat import _generate_title_via_provider
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_PERMISSION_REQUEST,
            EVENT_TEXT_CHUNK,
            LLMEvent,
        )
        from personalclaw.session import BACKGROUND_KEY

        state = _make_state(tmp_path)
        mock_client = MagicMock()
        mock_client.reject_tool = AsyncMock()

        async def _stream(prompt):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="My Title")
            yield LLMEvent(kind=EVENT_PERMISSION_REQUEST, request_id="req-1")
            yield LLMEvent(kind=EVENT_COMPLETE)

        mock_client.stream = _stream
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()

        messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        title = await _generate_title_via_provider(state, messages)

        mock_client.reject_tool.assert_called_once_with("req-1")
        assert title == "My Title"
        state.sessions.release.assert_called_once_with(BACKGROUND_KEY)

    @pytest.mark.asyncio
    async def test_complete_event_breaks_stream(self, tmp_path):
        from personalclaw.dashboard.chat import _generate_title_via_provider
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
        from personalclaw.session import BACKGROUND_KEY

        state = _make_state(tmp_path)
        mock_client = MagicMock()

        async def _stream(prompt):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Good")
            yield LLMEvent(kind=EVENT_COMPLETE)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=" SHOULD NOT APPEAR")

        mock_client.stream = _stream
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()

        messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        title = await _generate_title_via_provider(state, messages)

        assert title == "Good"
        state.sessions.release.assert_called_once_with(BACKGROUND_KEY)


# ── Inline tool cards: _flush_segment and segment flush in _run_chat ──


class TestFlushSegment:
    """Unit tests for _flush_segment helper function."""

    def test_flush_segment_persists_and_broadcasts(self, tmp_path, monkeypatch):
        """_flush_segment persists assistant message and broadcasts chat_segment.

        Validates: Requirements 1.1, 1.2, 4.3, 6.3
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        session = state.get_or_create_session("s1")
        # Simulate accumulated chunks
        session.append("chunk", "Hello ")
        session.append("chunk", "world")

        from personalclaw.dashboard.chat import _flush_segment

        _flush_segment(state, session, "Hello world")

        # Chunks should be removed
        chunk_msgs = [m for m in session.messages if m.get("role") == "chunk"]
        assert len(chunk_msgs) == 0
        # Assistant message should be persisted
        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Hello world"
        # chat_segment should be broadcast
        state.broadcast_ws.assert_called_once_with("chat_segment", {"session": "s1"})


class TestRunChatSegmentFlush:
    """Tests for segment flush behavior in _run_chat()."""

    @staticmethod
    def _make_mock_client(events):
        """Create a mock ACP client that yields the given LLMEvent list."""
        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)

        async def _stream(msg):
            for ev in events:
                yield ev

        client.stream = _stream
        client.stream_command = _stream
        return client

    @staticmethod
    def _make_state_for_run_chat(tmp_path, monkeypatch):
        """Create a DashboardState wired for _run_chat tests."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.context_builder = None
        state.consolidator = None
        state._hook_store = None
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        return state

    @pytest.mark.asyncio
    async def test_text_tool_text_complete_produces_two_segments(self, tmp_path, monkeypatch):
        """Mock event stream: text → tool_call → text → complete produces
        two assistant messages and one tool message.

        Validates: Requirements 1.1, 1.2, 1.3, 4.3
        """
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            EVENT_TOOL_CALL,
            LLMEvent,
        )

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Before tool"),
            LLMEvent(kind=EVENT_TOOL_CALL, title="read_file", tool_kind="read"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="After tool"),
            LLMEvent(kind=EVENT_COMPLETE),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")

        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # Check persisted messages (exclude transient roles)
        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 2
        assert assistant_msgs[0]["content"] == "Before tool"
        assert assistant_msgs[1]["content"] == "After tool"

        # Verify both chat_segment and tool_call are broadcast
        ws_calls = [(c.args[0], c.args[1]) for c in state.broadcast_ws.call_args_list]
        ws_types = [t for t, _ in ws_calls]
        assert "chat_segment" in ws_types
        assert "tool_call" in ws_types

    @pytest.mark.asyncio
    async def test_text_permission_request_flushes_segment(self, tmp_path, monkeypatch):
        """Mock event stream: text → permission_request flushes segment
        before permission flow.

        Validates: Requirements 1.4
        """
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_PERMISSION_REQUEST,
            EVENT_TEXT_CHUNK,
            LLMEvent,
        )

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Analyzing..."),
            LLMEvent(
                kind=EVENT_PERMISSION_REQUEST,
                title="bash",
                tool_kind="execute",
                request_id="req-1",
            ),
            LLMEvent(kind=EVENT_COMPLETE),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        # Enable YOLO mode so permission auto-approves (simplifies test)
        state.enable_yolo()
        session = state.get_or_create_session("s1")

        client = self._make_mock_client(events)
        client.approve_tool = AsyncMock()
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "run ls")

        # Segment should have been flushed before permission flow
        ws_types = [c.args[0] for c in state.broadcast_ws.call_args_list]
        assert "chat_segment" in ws_types
        # The flushed segment should be persisted as assistant
        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert any(m["content"] == "Analyzing..." for m in assistant_msgs)

    @pytest.mark.asyncio
    async def test_text_only_complete_no_segments(self, tmp_path, monkeypatch):
        """Text-only stream → complete produces one assistant message (no segments).

        Validates: Requirements 8.1
        """
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            LLMEvent,
        )

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Just text"),
            LLMEvent(kind=EVENT_COMPLETE),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")

        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # No chat_segment events
        ws_types = [c.args[0] for c in state.broadcast_ws.call_args_list]
        assert "chat_segment" not in ws_types
        # One assistant message
        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Just text"

    @pytest.mark.asyncio
    async def test_regenerate_variants_broadcast_even_when_segment_not(self, tmp_path, monkeypatch):
        """A regenerate turn attaches pending variants at the END-OF-TURN flush,
        which runs with broadcast=False (the active tab already streamed the text).
        The `chat_variant_switch` metadata signal MUST still fire so the ‹n/N›
        switcher lights up live — it is independent of the streaming-finalize
        `chat_segment` broadcast. Regression guard for the live-switcher bug.
        """
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            LLMEvent,
        )

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Compass."),
            LLMEvent(kind=EVENT_COMPLETE),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        # Simulate a pending regenerate: the prior answer stashed as a variant.
        session._pending_variants = [{"content": "Lantern.", "ts": ""}]

        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        ws_calls = [(c.args[0], c.args[1]) for c in state.broadcast_ws.call_args_list]
        ws_types = [t for t, _ in ws_calls]
        # Text-only turn → no chat_segment (broadcast=False end-of-turn flush)...
        assert "chat_segment" not in ws_types
        # ...but the variant metadata MUST be broadcast so the switcher appears live.
        assert "chat_variant_switch" in ws_types
        vpayload = next(d for t, d in ws_calls if t == "chat_variant_switch")
        assert vpayload["count"] == 2  # prior "Lantern." + fresh "Compass."
        assert vpayload["index"] == 1  # the fresh answer is active
        assert vpayload["content"] == "Compass."
        # Variants persisted on the single assistant message.
        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert len(assistant_msgs[0]["variants"]) == 2
        assert assistant_msgs[0]["variant_idx"] == 1
        # Pending buffer consumed.
        assert not session._pending_variants

    @pytest.mark.asyncio
    async def test_chunk_seq_monotonically_increasing_across_segments(self, tmp_path, monkeypatch):
        """chunk_seq values in broadcast calls are monotonically increasing
        across segments.

        Validates: Requirements 7.1
        """
        from personalclaw.llm.base import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            EVENT_TOOL_CALL,
            LLMEvent,
        )

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="a"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="b"),
            LLMEvent(kind=EVENT_TOOL_CALL, title="read_file", tool_kind="read"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="c"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="d"),
            LLMEvent(kind=EVENT_COMPLETE),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")

        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # Collect all seq values from chat_chunk broadcasts
        seq_values: list[int] = []
        for call in state.broadcast_ws.call_args_list:
            if call.args[0] == "chat_chunk":
                seq_values.append(call.args[1]["seq"])

        assert len(seq_values) == 4  # 4 text chunks
        # Verify strict monotonic increase
        for i in range(1, len(seq_values)):
            assert seq_values[i] > seq_values[i - 1], f"seq not monotonic: {seq_values}"


class TestModelBackfillOnComplete:
    """Regression tests for the late-backfill of the model that RAN at EVENT_COMPLETE.

    Background: Claude Code reports its model only after the prompt is
    dispatched (via the `init` system event). The original eager backfill
    at the start of _run_chat reads client._model too early for CC, so it
    stays empty. The fix re-reads client._model at EVENT_COMPLETE into a local
    ``_record_model`` used for the cost estimate — WITHOUT writing it onto
    session.model (the user's selection). These tests observe that resolved
    model via ``estimate_cost`` (its surviving consumer; the token-shard
    persistence was removed with the usage subsystem) and assert session.model
    is never clobbered — the mid-session model-switch regression guard.
    """

    @staticmethod
    def _capture_estimate_model(monkeypatch):
        """Patch estimate_cost to record the model it's called with. It fires only
        when the provider reported no cost AND a non-empty model resolved — exactly
        the ``_record_model`` the backfill produces. Returns the capture list."""
        captured: list = []

        def _fake_estimate(model, **kw):
            captured.append(model)
            return 0.0

        monkeypatch.setattr("personalclaw.pricing.estimate_cost", _fake_estimate)
        return captured

    @staticmethod
    def _make_mock_client(events, prov_model=""):
        """Mock provider that exposes a nested client._model attribute,
        mirroring AcpClient/CcClient layout (provider.client._model).
        """
        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)
        # Expose `client.client._model` like the real provider wrappers
        inner = MagicMock()
        inner._model = prov_model
        client.client = inner

        async def _stream(msg):
            for ev in events:
                yield ev

        client.stream = _stream
        client.stream_command = _stream
        return client

    @staticmethod
    def _make_state_for_run_chat(tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.context_builder = None
        state.consolidator = None
        state._hook_store = None
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        return state

    @pytest.mark.asyncio
    async def test_late_backfill_populates_model_for_cc_session(self, tmp_path, monkeypatch):
        """When session.model is empty at EVENT_COMPLETE but the provider has
        learned its model (CC init event), persist_token_record receives the
        provider model and session.model is updated.

        The mock starts with an empty ``inner._model`` so the *early* backfill
        at the top of _run_chat finds nothing, then mutates ``inner._model``
        just before yielding EVENT_COMPLETE — mirroring CC reporting its
        model only after the prompt is dispatched. This way only the *late*
        backfill branch can populate the record's model, so removing the
        late-backfill code would cause this test to fail.
        """
        from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

        events = [
            LLMEvent(
                kind=EVENT_COMPLETE,
                input_tokens=12,
                output_tokens=34,
            ),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.model = ""  # CC has not yet emitted init when _run_chat begins

        # Build a mock whose inner._model starts EMPTY so the early backfill
        # branch (chat_runner.py:471-476) finds nothing and leaves session.model
        # blank. Then mutate inner._model mid-stream — just before yielding
        # EVENT_COMPLETE — so only the late backfill branch can populate it.
        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)
        inner = MagicMock()
        inner._model = ""  # empty at session-create time
        client.client = inner

        async def _stream(msg):
            # Simulate CC's `init` system event arriving mid-turn, after the
            # prompt has been dispatched but before EVENT_COMPLETE.
            inner._model = "opus"
            for ev in events:
                yield ev

        client.stream = _stream
        client.stream_command = _stream
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        captured = self._capture_estimate_model(monkeypatch)

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        assert captured == ["opus"], "the cost estimate should reflect the model that ran"
        # session.model (the USER'S selection) must stay on "auto" — the provider's
        # internal model is used for the estimate only, never written back. Writing
        # it back clobbered the user's selection with an ACP CLI's default model
        # (the mid-session model-switch bug).
        assert session.model == "", "provider model must NOT overwrite the user's selection"

    @pytest.mark.asyncio
    async def test_late_backfill_skips_auto_sentinel(self, tmp_path, monkeypatch):
        """The sentinel value 'auto' (CC's pre-init placeholder) must not be treated
        as a real model — _record_model stays blank, so the cost estimate (which is
        guarded on a non-empty model) never fires and session.model stays blank.
        """
        from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

        events = [
            LLMEvent(kind=EVENT_COMPLETE, input_tokens=5, output_tokens=7),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.model = ""

        client = self._make_mock_client(events, prov_model="auto")
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        captured = self._capture_estimate_model(monkeypatch)

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # 'auto' is not a real model → no resolved model → estimate never runs.
        assert captured == []
        assert session.model == ""

    @pytest.mark.asyncio
    async def test_early_backfill_does_not_clobber_auto_with_provider_default(
        self, tmp_path, monkeypatch
    ):
        """Regression (mid-session model switch): when the user leaves model on
        "auto" and the ACP provider already exposes its internal default model at
        session-create time (e.g. claude-code's bundle DEFAULT_MODEL
        "claude-opus-4-8"), that model must NOT be written onto session.model — doing
        so made the dropdown silently switch to a model no model-provider offers.
        The record still reflects what ran; the user's "auto" selection stands."""
        from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

        events = [LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=4)]
        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.model = ""  # user is on "auto"

        # Provider reports its internal default model from the very start.
        client = self._make_mock_client(events, prov_model="claude-opus-4-8")
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        captured = self._capture_estimate_model(monkeypatch)

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # The cost estimate reflects the model that ran...
        assert captured == ["claude-opus-4-8"]
        # ...but the user's selection ("auto") is preserved, NOT clobbered.
        assert session.model == ""

    @pytest.mark.asyncio
    async def test_existing_session_model_is_not_overwritten(self, tmp_path, monkeypatch):
        """OpenCode resolves model synchronously; session.model is already set
        when EVENT_COMPLETE arrives. Backfill must not clobber it.
        """
        from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

        events = [
            LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=2),
        ]

        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.model = "claude-opus-4.6"

        # Even if the inner client somehow reports a different value,
        # session.model wins because it was already set explicitly.
        client = self._make_mock_client(events, prov_model="should-not-be-used")
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        captured = self._capture_estimate_model(monkeypatch)

        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "hello")

        # The estimate uses the already-set session.model, not the provider default.
        assert captured == ["claude-opus-4.6"]
        assert session.model == "claude-opus-4.6"


class TestPrepareMessagesInterleaved:
    """Tests for _prepare_messages with interleaved assistant/tool/chunk messages."""

    def test_interleaved_assistant_tool_chunk_structure(self):
        """_prepare_messages with interleaved assistant/tool/chunk returns
        correct structure.

        Validates: Requirements 6.1
        """
        from personalclaw.dashboard.chat import _prepare_messages

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Before tool", "cls": "msg msg-a"},
            {"role": "tool", "content": "✅ read_file", "cls": "msg msg-tool"},
            {"role": "assistant", "content": "After tool", "cls": "msg msg-a"},
            {"role": "chunk", "content": "still "},
            {"role": "chunk", "content": "streaming"},
        ]

        result = _prepare_messages(messages, running=True)

        # user, assistant, tool, assistant, streaming (collapsed chunks)
        assert len(result) == 5
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Before tool"
        assert result[2]["role"] == "tool"
        assert result[3]["role"] == "assistant"
        assert result[3]["content"] == "After tool"
        assert result[4]["role"] == "streaming"
        assert result[4]["content"] == "still streaming"

    def test_no_trailing_chunks_no_streaming(self):
        """Without trailing chunks, no streaming message is produced."""
        from personalclaw.dashboard.chat import _prepare_messages

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Segment 1", "cls": "msg msg-a"},
            {"role": "tool", "content": "✅ bash", "cls": "msg msg-tool"},
            {"role": "assistant", "content": "Segment 2", "cls": "msg msg-a"},
        ]

        result = _prepare_messages(messages, running=False)

        assert len(result) == 4
        roles = [m["role"] for m in result]
        assert "streaming" not in roles
        assert "chunk" not in roles


# ── Runtime wiring tests (multi-agent-orchestration) ──


class TestRuntimeWiring:
    """Tests for multi-agent-orchestration runtime wiring.

    Requirements: 1.3, 2.3, 2.4, 3.1
    """

    @pytest.mark.asyncio
    async def test_api_chat_session_agent_resolves_working_directory(self, tmp_path, monkeypatch):
        """Switching a session's agent resolves the new agent's working directory.

        The named-workspace registry was flattened: a session's workspace IS its
        working directory (``workspace_dir``), resolved from the agent's bindings
        and set on the live session.

        Requirements: 1.3
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        state.sessions.reset = AsyncMock()

        mock_cfg = MagicMock()
        mock_cfg.agents = {"oncall": MagicMock(memory_store="oncall-mem")}

        mock_bindings = MagicMock()
        mock_bindings.workspace_dir = Path("/tmp/oncall")
        mock_bindings.memory_store_name = "oncall-mem"

        monkeypatch.setattr("personalclaw.dashboard.chat.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr("personalclaw.dashboard.chat_handlers.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_handlers.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )

        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            await client.post("/api/chat/sessions/s1/agent", json={"agent": "oncall"})

        # The live session adopts the agent and its resolved working directory.
        assert session.agent == "oncall"
        assert session.workspace_dir == "/tmp/oncall"

    @pytest.mark.asyncio
    async def test_api_chat_session_agent_persists_to_metadata(self, tmp_path, monkeypatch):
        """Switching a session's agent writes the new value to the JSONL metadata.

        Without this, a session resumed after a gateway restart reverts to
        whatever agent (if any) was recorded in the initial metadata line.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        state.sessions.reset = AsyncMock()

        # Seed a session file so update_metadata has something to patch.
        # Use the canonical colon-separated key that the API handler derives
        # via _history_key_for("s1") → "dashboard:s1".  Using "dashboard_s1"
        # (underscore) maps to the same *file* on disk (_safe_key converts
        # both to "dashboard_s1.jsonl") but creates a different *cache key*,
        # so update_metadata's cache invalidation for "dashboard:s1" would
        # leave the "dashboard_s1" cache entry stale.
        history_key = "dashboard:s1"
        state.conversation_log.append(history_key, "user", "hi", agent="old-agent")
        assert state.conversation_log.get_metadata(history_key).get("agent") == "old-agent"

        # Minimal config stub (agent-binding resolution is exercised by the
        # workspace-focused test above; here we only care about persistence).
        mock_cfg = MagicMock()
        mock_cfg.agents = {}
        monkeypatch.setattr("personalclaw.dashboard.chat.AppConfig.load", lambda: mock_cfg)

        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/agent", json={"agent": "new-agent"})
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["agent"] == "new-agent"

        meta = state.conversation_log.get_metadata(history_key)
        assert (
            meta.get("agent") == "new-agent"
        ), f"expected new-agent in metadata, got {meta.get('agent')!r}"

    @pytest.mark.asyncio
    async def test_api_chat_session_create_response_includes_workspace_dir(
        self, tmp_path, monkeypatch
    ):
        """api_chat_session_create resolves the agent's working directory onto the session.

        The named-workspace registry was flattened: a session's workspace IS its
        working directory (``workspace_dir``), resolved from the agent's bindings.

        Requirements: 2.4
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_cfg = MagicMock()
        mock_cfg.agents = {"research": MagicMock(memory_store="default")}

        mock_bindings = MagicMock()
        mock_bindings.workspace_dir = Path("/tmp/research")
        mock_bindings.memory_store_name = "default"

        monkeypatch.setattr("personalclaw.dashboard.chat.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr("personalclaw.dashboard.chat_handlers.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_handlers.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )

        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            resp = await client.post(
                "/api/chat/sessions",
                json={"name": "new-session", "agent": "research"},
            )
            data = await resp.json()
            assert resp.status == 200
            assert data["workspace_dir"] == "/tmp/research"

    def test_get_or_create_session_accepts_workspace_dir_parameter(self, tmp_path, monkeypatch):
        """get_or_create_session accepts workspace_dir and sets it on the session.

        Requirements: 2.3
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        session = state.get_or_create_session(
            "ws-test", agent="oncall", workspace_dir="/tmp/oncall"
        )
        assert session.workspace_dir == "/tmp/oncall"
        assert session.agent == "oncall"

        # Default working directory is empty when not specified
        session2 = state.get_or_create_session("ws-default")
        assert session2.workspace_dir == ""

        # Mode parameter
        session3 = state.get_or_create_session("mode-test", mode="plan")
        assert session3.mode == "plan"
        assert state.get_or_create_session("ws-default").mode == ""

    @pytest.mark.asyncio
    async def test_run_chat_passes_memory_store_to_build_message(self, tmp_path, monkeypatch):
        """_run_chat resolves agent bindings and passes memory_store to build_message.

        Requirements: 3.1
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)

        # Track calls to build_message
        build_message_calls: list[dict] = []

        def mock_build_message(self_ctx, text, is_new, session_key=None, **kwargs):
            build_message_calls.append({"text": text, "kwargs": kwargs})
            return text, MagicMock(action=None, text="")

        # Mock config loading
        mock_cfg = MagicMock()
        mock_cfg.agents = {"oncall": MagicMock(workspace="oncall-ws", memory_store="oncall-mem")}
        mock_cfg.default_agent = "default"

        mock_bindings = MagicMock()
        mock_bindings.memory_store_name = "oncall-mem"

        monkeypatch.setattr("personalclaw.dashboard.chat.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )
        monkeypatch.setattr("personalclaw.dashboard.chat_runner.AppConfig.load", lambda: mock_cfg)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_runner.resolve_agent_bindings",
            lambda cfg, name: mock_bindings,
        )

        # Create a context builder with mocked build_message
        from personalclaw.context import ContextBuilder
        from personalclaw.memory import MemoryStore
        from personalclaw.skills import SkillsLoader

        ctx_builder = ContextBuilder(
            memory=MemoryStore(workspace=tmp_path / "ws"),
            skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        )
        monkeypatch.setattr(
            ctx_builder, "build_message", lambda *a, **kw: mock_build_message(ctx_builder, *a, **kw)
        )

        state = _make_state(tmp_path, context_builder=ctx_builder)

        # Create a session with an agent
        session = state.get_or_create_session("mem-test", agent="oncall")

        # Mock session manager to return a mock client
        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        state.sessions.get_pid = MagicMock(return_value=None)

        # Import and run _run_chat
        from personalclaw.dashboard.chat import _run_chat

        await _run_chat(state, session, "test message")

        # Verify build_message was called with memory_store
        assert len(build_message_calls) == 1
        assert build_message_calls[0]["kwargs"].get("memory_store") == "oncall-mem"


class TestRunChatToolBoundarySegments:
    """Test that _run_chat inserts whitespace across tool call boundaries."""

    @pytest.mark.asyncio
    async def test_tool_boundary_splits_segments(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import LLMEvent

        monkeypatch.setattr("personalclaw.dashboard.chat.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())

        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state._hook_store = None

        events = [
            LLMEvent(kind="text_chunk", text="Let me check."),
            LLMEvent(kind="tool_call", title="Read File", tool_kind="read"),
            LLMEvent(kind="text_chunk", text="Done!"),
            LLMEvent(kind="complete"),
        ]

        fake_client = AsyncMock()

        async def _stream(msg):
            for e in events:
                yield e

        fake_client.stream = _stream
        fake_client.context_usage_pct = MagicMock(return_value=0.0)
        state.sessions.get_or_create = AsyncMock(return_value=(fake_client, True, False))
        state.sessions.get_pid = MagicMock(return_value=None)
        state.sessions.check_context_usage = MagicMock()
        state.sessions.record_success = MagicMock()
        state.sessions.record_failure = AsyncMock()
        state.sessions.release = MagicMock()

        session = state.get_or_create_session("s1")
        await _run_chat(state, session, "do it")

        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        # With _flush_segment, text is split into separate segments at tool boundaries
        # so gluing can't happen — each segment is independent
        assert len(assistant_msgs) == 2
        assert "Let me check." in assistant_msgs[0]["content"]
        assert "Done!" in assistant_msgs[1]["content"]

    @pytest.mark.asyncio
    async def test_tool_boundary_empty_chunk_still_splits(self, tmp_path, monkeypatch):
        """Empty text chunk after tool call doesn't prevent segment splitting."""
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import LLMEvent

        monkeypatch.setattr("personalclaw.dashboard.chat.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())

        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state._hook_store = None

        events = [
            LLMEvent(kind="text_chunk", text="Before."),
            LLMEvent(kind="tool_call", title="T", tool_kind="read"),
            LLMEvent(kind="text_chunk", text=""),  # empty chunk
            LLMEvent(kind="text_chunk", text="After!"),
            LLMEvent(kind="complete"),
        ]

        fake_client = AsyncMock()

        async def _stream(msg):
            for e in events:
                yield e

        fake_client.stream = _stream
        fake_client.context_usage_pct = MagicMock(return_value=0.0)
        state.sessions.get_or_create = AsyncMock(return_value=(fake_client, True, False))
        state.sessions.get_pid = MagicMock(return_value=None)
        state.sessions.check_context_usage = MagicMock()
        state.sessions.record_success = MagicMock()
        state.sessions.record_failure = AsyncMock()
        state.sessions.release = MagicMock()

        session = state.get_or_create_session("s1")
        await _run_chat(state, session, "do it")

        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        # Segments are flushed at tool boundaries; empty chunks don't create segments
        assert len(assistant_msgs) == 2
        assert "Before." in assistant_msgs[0]["content"]
        assert "After!" in assistant_msgs[1]["content"]


# ── Mode/approval policy propagation (HTTP handlers) ──


class TestApiChatModePropagation:
    """api_chat_mode propagates approval policy to all session sessions."""

    @pytest.mark.asyncio
    async def test_yolo_mode_propagates_auto_policy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        state.get_or_create_session("s1")
        state.get_or_create_session("s2")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "yolo"})
            data = await resp.json()
            assert data["ok"] is True

        calls = state.sessions.set_approval_policy.call_args_list
        keys = [c.args[0] for c in calls]
        policies = [c.args[1] for c in calls]
        assert "dashboard:s1" in keys
        assert "dashboard:s2" in keys
        assert all(p == "auto" for p in policies)

    @pytest.mark.asyncio
    async def test_normal_mode_clears_policy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.enable_yolo(ttl_secs=1800)
        state = _make_state(tmp_path)
        state.enable_yolo()
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        session._trust = False

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "normal"})
            data = await resp.json()
            assert data["ok"] is True

        state.sessions.set_approval_policy.assert_called_with("dashboard:s1", "")

    @pytest.mark.asyncio
    async def test_plan_task_mode_sets_field(self, tmp_path, monkeypatch):
        """Plan is a TASK mode (orthogonal to approval): POST /api/chat/task-mode sets
        _task_mode='plan' WITHOUT touching the approval flags (clean-break: task mode
        complements approval, no longer a mutually-exclusive approval rung)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        session._trust = True  # approval posture is INDEPENDENT — plan must NOT clear it

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/task-mode", json={"mode": "plan", "session": "s1"})
            assert (await resp.json())["ok"] is True

        assert session._task_mode == "plan"
        assert session._trust is True  # orthogonal: Plan + Trust coexist

    @pytest.mark.asyncio
    async def test_task_mode_switch_and_validation(self, tmp_path, monkeypatch):
        """Switching task mode replaces it; an invalid mode is rejected 400."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        session._task_mode = "plan"

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/task-mode", json={"mode": "ask", "session": "s1"})
            assert (await resp.json())["ok"] is True
            assert session._task_mode == "ask"

            resp = await client.post("/api/chat/task-mode", json={"mode": "agent", "session": "s1"})
            assert (await resp.json())["ok"] is True
            assert session._task_mode == "agent"

            resp = await client.post("/api/chat/task-mode", json={"mode": "bogus", "session": "s1"})
            assert resp.status == 400
            assert session._task_mode == "agent"  # unchanged on rejection

    @pytest.mark.asyncio
    async def test_acp_agent_override_sets_session_fields(self, tmp_path, monkeypatch):
        """POST /acp-agent sets ephemeral provider/provider_agent/model/effort on
        the session (no config write) and surfaces them in to_dict."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/acp-agent",
                json={
                    "provider": "acp:test-cli",
                    "provider_agent": "gpu-dev",
                    "model": "glm-5",
                    "reasoning_effort": "high",
                },
            )
            data = await resp.json()
            assert data["ok"] is True

        assert session.acp_provider == "acp:test-cli"
        assert session.acp_provider_agent == "gpu-dev"
        assert session.model == "glm-5"
        assert session.reasoning_effort == "high"
        d = session.to_dict()
        assert d["acp_provider"] == "acp:test-cli" and d["acp_provider_agent"] == "gpu-dev"

    @pytest.mark.asyncio
    async def test_acp_agent_override_rejects_non_acp_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/acp-agent", json={"provider": "native"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_selecting_saved_agent_clears_acp_override(self, tmp_path, monkeypatch):
        """Switching to a saved/native agent clears a prior ephemeral ACP override."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        session.acp_provider = "acp:test-cli"
        session.acp_provider_agent = "gpu-dev"

        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/agent", json={"agent": "default"})
            assert (await resp.json())["ok"] is True
        assert session.acp_provider == "" and session.acp_provider_agent == ""

    @pytest.mark.asyncio
    async def test_task_mode_in_session_to_dict(self, tmp_path, monkeypatch):
        """The task mode is surfaced in the session dict for the frontend composer."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        assert session.to_dict().get("task_mode") == "agent"  # default
        session._task_mode = "build"
        assert session.to_dict().get("task_mode") == "build"

    @pytest.mark.asyncio
    async def test_session_detail_restores_both_composer_axes(self, tmp_path, monkeypatch):
        """Reopening a session must hand back BOTH composer axes so the UI restores
        the real posture instead of reverting to Agent/Normal. task_mode is verbatim;
        approval is the single enum derived from yolo(global)/trust/trust_reads."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session._task_mode = "plan"
        session._trust = True  # orthogonal: Plan + Trust is a valid combination
        async with TestClient(TestServer(_make_app(state))) as client:
            data = await (await client.get("/api/chat/sessions/s1")).json()
        # Plan task mode + Trust approval both round-trip (not reset to defaults).
        assert data["task_mode"] == "plan"
        assert data["approval"] == "trust"

    @pytest.mark.asyncio
    async def test_session_detail_approval_precedence(self, tmp_path, monkeypatch):
        """approval reflects the yolo>trust>trust_reads>normal precedence the gate uses."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        async with TestClient(TestServer(_make_app(state))) as _client:
            assert (await (await _client.get("/api/chat/sessions/s1")).json())[
                "approval"
            ] == "normal"
            session._trust_reads = True
            assert (await (await _client.get("/api/chat/sessions/s1")).json())[
                "approval"
            ] == "trust_reads"
            session._trust = True
            assert (await (await _client.get("/api/chat/sessions/s1")).json())[
                "approval"
            ] == "trust"
            state.enable_yolo()  # global, outranks per-session trust
            assert (await (await _client.get("/api/chat/sessions/s1")).json())["approval"] == "yolo"

    @pytest.mark.asyncio
    async def test_trust_mode_scoped_to_session(self, tmp_path, monkeypatch):
        """Trust with session_key only trusts that session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        s2 = state.get_or_create_session("s2")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "trust", "session": "s1"})
            assert (await resp.json())["ok"] is True

        assert s1._trust is True
        assert s2._trust is False

    @pytest.mark.asyncio
    async def test_trust_mode_all_sessions_when_no_session(self, tmp_path, monkeypatch):
        """Trust without session_key trusts all sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "trust"})
            assert (await resp.json())["ok"] is True

        assert s1._trust is True

    @pytest.mark.asyncio
    async def test_normal_mode_scoped_resets_only_session(self, tmp_path, monkeypatch):
        """Normal mode with session_key should only reset that session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        s1._trust = True
        s2 = state.get_or_create_session("s2")
        s2._trust = True

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "normal", "session": "s1"})
            assert (await resp.json())["ok"] is True

        assert s1._trust is False
        assert s2._trust is True

    @pytest.mark.asyncio
    async def test_normal_mode_resets_all_sessions_when_no_session(self, tmp_path, monkeypatch):
        """Normal mode without session_key resets all session trust."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        s1._trust = True

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "normal"})
            assert (await resp.json())["ok"] is True

        assert s1._trust is False

    @pytest.mark.asyncio
    async def test_trust_mode_unknown_session_returns_400(self, tmp_path, monkeypatch):
        """Trust with unknown session_key must return 400, not trust all."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/mode", json={"mode": "trust", "session": "nonexistent"}
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "unknown session"

    @pytest.mark.asyncio
    async def test_normal_mode_unknown_session_returns_400(self, tmp_path, monkeypatch):
        """Normal with unknown session_key must return 400, not reset all."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/mode", json={"mode": "normal", "session": "nonexistent"}
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "unknown session"

    @pytest.mark.asyncio
    async def test_trust_session_preserves_other_session_trust(self, tmp_path, monkeypatch):
        """trusting session B must not wipe trust from session A."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        s2 = state.get_or_create_session("s2")

        async with TestClient(TestServer(_make_app(state))) as client:
            await client.post("/api/chat/mode", json={"mode": "trust", "session": "s1"})
            assert s1._trust is True

            await client.post("/api/chat/mode", json={"mode": "trust", "session": "s2"})
            assert s2._trust is True
            assert s1._trust is True  # must survive

    @pytest.mark.asyncio
    async def test_yolo_restores_per_session_trust(self, tmp_path, monkeypatch):
        """YOLO does not mutate per-session trust; disabling preserves it."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        s2 = state.get_or_create_session("s2")

        async with TestClient(TestServer(_make_app(state))) as client:
            # Set per-session modes: s1=trust, s2=trust_reads
            await client.post("/api/chat/mode", json={"mode": "trust", "session": "s1"})
            await client.post("/api/chat/mode", json={"mode": "trust_reads", "session": "s2"})
            assert s1._trust is True
            assert s2._trust_reads is True

            # YOLO overrides everything
            await client.post("/api/chat/mode", json={"mode": "yolo"})
            assert s1._trust is True  # unchanged
            assert s2._trust_reads is True  # unchanged

            # Set s1 to normal (leaving YOLO) — s2 should be untouched
            await client.post("/api/chat/mode", json={"mode": "normal", "session": "s1"})
            assert s1._trust is False
            assert s1._trust_reads is False
            assert s2._trust_reads is True  # preserved

    def test_yolo_auto_expires_and_clears_untrusted_policies(self, tmp_path, monkeypatch):
        """YOLO expiry clears policies for untrusted sessions only."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        state.get_or_create_session("s2")
        s1._trust = True

        # Enable YOLO then force an already-lapsed (nonzero) expiry so the next
        # is_yolo_active() read auto-expires it (canonical trust_mode behavior;
        # expires_at==0.0 means permanent, so use a small positive past value).
        state.enable_yolo()
        import personalclaw.trust_mode as _tm

        _tm._TRUST._expires_at = 1.0  # positive but far in the past → expired

        assert state.is_yolo_active() is False
        assert s1._trust is True  # per-session trust survives expiry

        cleared = [
            c[0][0] for c in state.sessions.set_approval_policy.call_args_list if c[0][1] == ""
        ]
        assert "dashboard:s2" in cleared
        assert "dashboard:s1" not in cleared


class TestApproveYoloPropagation:
    """api_chat_session_approve with yolo action propagates policy to all sessions."""

    @pytest.mark.asyncio
    async def test_yolo_approve_propagates_to_all_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        s1 = state.get_or_create_session("s1")
        state.get_or_create_session("s2")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        s1._approval_futures["test"] = fut

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "yolo"})
            data = await resp.json()
            assert data["ok"] is True

        calls = state.sessions.set_approval_policy.call_args_list
        keys = [c.args[0] for c in calls]
        assert "dashboard:s1" in keys
        assert "dashboard:s2" in keys

    @pytest.mark.asyncio
    async def test_trust_approve_propagates_to_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        session._approval_futures["test"] = fut

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "trust"})
            data = await resp.json()
            assert data["ok"] is True

        state.sessions.set_approval_policy.assert_called_with("dashboard:s1", "auto")


# ── Coverage: bulk-approve broadcasts ──


class TestBulkApproveBroadcast:
    """Trust/YOLO mode change bulk-approve must broadcast approval_resolved."""

    @pytest.mark.asyncio
    async def test_mode_yolo_broadcasts_for_pending(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.dashboard.chat.sel", lambda: MagicMock())
        state = _make_state(tmp_path)
        state.push_sessions_update = MagicMock()
        state.broadcast_ws = MagicMock()
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        f1: asyncio.Future[str] = loop.create_future()
        f2: asyncio.Future[str] = loop.create_future()
        session._approval_futures["req-1"] = f1
        session._approval_futures["req-2"] = f2

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "yolo"})
            assert (await resp.json())["ok"] is True

        broadcast_calls = [
            c for c in state.broadcast_ws.call_args_list if c.args[0] == "approval_resolved"
        ]
        ids = {c.args[1]["id"] for c in broadcast_calls}
        assert "req-1" in ids
        assert "req-2" in ids


# ── Coverage: multi-pending approval 400 and trust auto-approve ──


class TestMultiPendingApproval:
    """Cover the 400 response when multiple approvals are pending without request_id."""

    @pytest.mark.asyncio
    async def test_multi_pending_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        session._approval_futures["a1"] = loop.create_future()
        session._approval_futures["a2"] = loop.create_future()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/approve", json={"action": "approved"})
            assert resp.status == 400
            data = await resp.json()
            assert "pending" in data
            assert set(data["pending"]) == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_approve_with_request_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        session._approval_futures["specific"] = fut

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/s1/approve",
                json={"action": "approved", "request_id": "specific"},
            )
            assert resp.status == 200
            assert fut.result() == "approved"


# ── Agent passing via /api/chat (external-agent integration) ──


class TestApiChatAgentPassing:
    @pytest.mark.asyncio
    async def test_agent_set_on_new_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat?ws=1",
                json={
                    "message": "hello",
                    "session": "external-my-skill",
                    "agent": "my-custom-agent",
                },
            )
            data = await resp.json()
            assert data["ok"] is True
            assert state._sessions["external-my-skill"].agent == "my-custom-agent"

    @pytest.mark.asyncio
    async def test_agent_mismatch_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("session-x")
        session.agent = "agent-a"
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat?ws=1",
                json={"message": "hello", "session": "session-x", "agent": "agent-b"},
            )
            assert resp.status == 409

    @pytest.mark.asyncio
    async def test_empty_agent_on_agent_session_allowed(self, tmp_path, monkeypatch):
        """Follow-up message with no agent on an agent-bound session must not 409."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("session-y")
        session.agent = "agent-a"
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat?ws=1",
                json={"message": "follow-up", "session": "session-y", "agent": ""},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_invalid_agent_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from unittest.mock import patch

        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._emit_agent_assignment") as mock_emit:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hello", "session": "s1", "agent": "../evil"},
                )
            assert resp.status == 400
            mock_emit.assert_called_once_with("s1", "../evil", outcome="denied_invalid")

    @pytest.mark.asyncio
    async def test_non_string_agent_logs_actual_value(self, tmp_path, monkeypatch):
        """Fix for Post 22: str(agent) preserves malicious input in audit trail."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from unittest.mock import patch

        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._emit_agent_assignment") as mock_emit:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hello", "session": "s1", "agent": 123},
                )
            assert resp.status == 400
            mock_emit.assert_called_once_with("s1", "123", outcome="denied_invalid")

    @pytest.mark.asyncio
    async def test_no_agent_no_emit(self, tmp_path, monkeypatch):
        """Fix for Post 23: no SEL event when no agent involved (reduces audit noise)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from unittest.mock import patch

        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._emit_agent_assignment") as mock_emit:
            async with TestClient(TestServer(_make_app(state))) as client:
                await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "no-agent-session"},
                )
            mock_emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_sel_event_on_running_session_rejection(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from unittest.mock import MagicMock, patch

        state = _make_state(tmp_path)
        session = state.get_or_create_session("session-r")
        mock_task = MagicMock()
        mock_task.done.return_value = False
        session.task = mock_task
        with patch("personalclaw.dashboard.chat_handlers._emit_agent_assignment") as mock_emit:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "session-r", "agent": "new-agent"},
                )
                assert resp.status == 409
            mock_emit.assert_called_once_with("session-r", "new-agent", outcome="denied_running")


class TestPlanValidationStuck:
    """Tests for has_plan=False after strip_plan_markers on invalid plans."""

    def test_strip_plan_markers_clears_has_plan(self):
        """After stripping, has_plan must be False so ensure_go_all_option doesn't run."""
        from personalclaw.context_management import (
            strip_plan_markers,
            validate_plan_format,
        )

        # Simulate a response that looks like a plan but fails validation
        bad_plan = "📋 Plan for: test\n\nThis has no Stage lines.\n\n[OPTION: Go | Cancel]"
        has_plan, valid, _ = validate_plan_format(bad_plan)
        assert has_plan, "Expected plan header to be detected"
        assert not valid, "Expected plan to be invalid (no Stage lines)"
        stripped = strip_plan_markers(bad_plan)
        has_plan_after, _, _ = validate_plan_format(stripped)
        assert not has_plan_after, (
            "strip_plan_markers must remove plan markers so "
            "validate_plan_format no longer detects a plan"
        )
        assert "📋" not in stripped


class TestStageFailureEscalation:
    """Test that stage failures trigger human question logic (escalation)."""

    def test_single_failure_allows_retry(self):
        """A single task failure does NOT trigger escalation — retry is allowed."""
        from personalclaw.context_management import OrchestrationTracker

        tracker = OrchestrationTracker()
        tracker.record_round(1)
        # First failure: should not escalate
        hit_limit = tracker.record_failure("task-a")
        assert not hit_limit
        assert not tracker.has_escalated
        assert tracker.failure_count("task-a") == 1

    def test_repeated_failures_trigger_escalation(self):
        """After MAX_TASK_FAILURES (3), has_escalated becomes True."""
        from personalclaw.context_management import (
            MAX_TASK_FAILURES,
            OrchestrationTracker,
        )

        tracker = OrchestrationTracker()
        tracker.record_round(1)
        for i in range(MAX_TASK_FAILURES - 1):
            assert not tracker.record_failure("task-a")
        # The Nth failure triggers escalation
        assert tracker.record_failure("task-a")
        assert tracker.has_escalated

    def test_success_resets_failure_count(self):
        """record_success clears the failure counter for a task."""
        from personalclaw.context_management import OrchestrationTracker

        tracker = OrchestrationTracker()
        tracker.record_round(1)
        tracker.record_failure("task-a")
        tracker.record_failure("task-a")
        assert tracker.failure_count("task-a") == 2
        tracker.record_success("task-a")
        assert tracker.failure_count("task-a") == 0
        assert not tracker.has_escalated

    def test_stage_round_limit_triggers_escalation(self):
        """After MAX_STAGE_ROUNDS (3) rounds in a stage, has_escalated is True."""
        from personalclaw.context_management import MAX_STAGE_ROUNDS, OrchestrationTracker

        tracker = OrchestrationTracker()
        for i in range(MAX_STAGE_ROUNDS):
            tracker.record_round(1)
        assert tracker.has_escalated

    def test_reset_after_guidance_clears_rounds(self):
        """User guidance resets round counters, allowing retry."""
        from personalclaw.context_management import MAX_STAGE_ROUNDS, OrchestrationTracker

        tracker = OrchestrationTracker()
        for i in range(MAX_STAGE_ROUNDS):
            tracker.record_round(1)
        assert tracker.has_escalated
        tracker.reset_after_guidance()
        assert not tracker.has_escalated
        assert tracker.round_count(1) == 0

    def test_force_fail_after_max_escalations(self):
        """After MAX_STAGE_ESCALATIONS resets, stage is force-failed."""
        from personalclaw.context_management import (
            MAX_STAGE_ESCALATIONS,
            MAX_STAGE_ROUNDS,
            OrchestrationTracker,
        )

        tracker = OrchestrationTracker()
        for _esc in range(MAX_STAGE_ESCALATIONS):
            for _r in range(MAX_STAGE_ROUNDS):
                tracker.record_round(1)
            tracker.reset_after_guidance()
        assert tracker.is_force_failed(1)


# ── Tests: prompt-busy session recovery ──


class TestPromptBusyRecovery:
    """When ACP agent returns 'Prompt already in progress', _run_chat must
    reset the session and re-queue the message so the next attempt cold-starts."""

    @pytest.mark.asyncio
    async def test_prompt_busy_resets_session_and_requeues(self, tmp_path: Path) -> None:
        from personalclaw.acp.client import AcpError
        from personalclaw.dashboard.chat import _run_chat

        state = _make_state(tmp_path)
        state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), False, False))
        state.sessions.release = MagicMock()
        state.sessions.reset = AsyncMock()
        state.sessions.set_approval_policy = MagicMock()
        state.sessions.check_context_usage = MagicMock()
        state.sessions.get_channel_link = MagicMock(return_value=(None, None))
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.is_yolo_active = MagicMock(return_value=False)
        state._background_tasks = set()

        session = state.get_or_create_session("busy-session")
        session.append("user", "hello", "msg msg-u")

        # Make client.stream raise "already in progress"
        mock_client = state.sessions.get_or_create.return_value[0]

        async def _raise_busy(msg):
            raise AcpError("Prompt error: {'data': 'Prompt already in progress'}")
            yield  # make it an async generator  # noqa: E501

        mock_client.stream = _raise_busy
        mock_client.stream_command = _raise_busy
        mock_client.shutdown = AsyncMock()

        await _run_chat(state, session, "test message")

        # Session must be reset (kill the stuck ACP agent process)
        state.sessions.reset.assert_awaited_once()
        # The finally block drains the re-queued message into a new task
        assert session.task is not None
        # No ❌ error shown to the user for the busy case
        error_msgs = [m for m in session.messages if m.get("role") == "error"]
        assert not any("already in progress" in m.get("content", "") for m in error_msgs)

    @pytest.mark.asyncio
    async def test_process_exited_resets_session_and_requeues(self, tmp_path: Path) -> None:
        """When ACP subprocess dies (SIGTERM/SIGKILL), _run_chat must reset
        the session and re-queue the message so autonudges land on a fresh
        provider instead of a bare ❌ error card with no work done."""
        from personalclaw.acp.client import AcpError
        from personalclaw.dashboard.chat import _run_chat

        state = _make_state(tmp_path)
        state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), False, False))
        state.sessions.release = MagicMock()
        state.sessions.reset = AsyncMock()
        state.sessions.set_approval_policy = MagicMock()
        state.sessions.check_context_usage = MagicMock()
        state.sessions.get_channel_link = MagicMock(return_value=(None, None))
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.is_yolo_active = MagicMock(return_value=False)
        state._background_tasks = set()

        session = state.get_or_create_session("dead-session")
        session.append("user", "hello", "msg msg-u")

        mock_client = state.sessions.get_or_create.return_value[0]

        async def _raise_dead(msg):
            raise AcpError("ACP process exited (code=-15)")
            yield  # make it an async generator  # noqa: E501

        mock_client.stream = _raise_dead
        mock_client.stream_command = _raise_dead
        mock_client.shutdown = AsyncMock()

        await _run_chat(state, session, "test message")

        state.sessions.reset.assert_awaited_once()
        assert session.task is not None
        error_msgs = [m for m in session.messages if m.get("role") == "error"]
        assert not any("process exited" in m.get("content", "") for m in error_msgs)


# ── Tests: session.task None guard ──


class TestSessionTaskNoneGuard:
    """stop/delete must not crash when session.task is None."""

    @pytest.mark.asyncio
    async def test_stop_not_running(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        state.get_or_create_session("s1")
        # task is None → running is False → stop is a no-op
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_delete_not_running(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        # task is None → running is False → delete skips cancel
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.delete("/api/chat/sessions/s1")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_stop_with_real_task_cancels(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.sessions.stop_turn = AsyncMock(return_value="soft")
        session = state.get_or_create_session("s1")
        session.task = asyncio.get_running_loop().create_future()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200
            state.sessions.stop_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_with_real_task_cancels(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.task = asyncio.get_running_loop().create_future()

        async with TestClient(TestServer(_make_app(state))) as client:
            with patch("personalclaw.dashboard.chat_handlers._save_session_to_history"):
                resp = await client.delete("/api/chat/sessions/s1")
            assert resp.status == 200
            assert session.task.cancelled()


# ── Bulk cleanup tests ──


class TestBulkCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_archives_stale_sessions(self, tmp_path, monkeypatch):
        """Stale sessions are archived; fresh and pinned are kept."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()

        stale = state.get_or_create_session("stale1")
        stale.append("user", "old msg", ts=old_ts)
        stale.drain()

        fresh = state.get_or_create_session("fresh1")
        fresh.append("user", "new msg", ts=fresh_ts)
        fresh.drain()

        pinned = state.get_or_create_session("pinned1")
        pinned.pinned = True
        pinned.append("user", "pinned msg", ts=old_ts)
        pinned.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 3, "active_session": "fresh1"},
            )
            data = await resp.json()
            assert data["ok"] is True
            assert data["archived"] == 1
            assert "stale1" in data["keys"]

        assert "stale1" not in state._sessions
        assert "fresh1" in state._sessions
        assert "pinned1" in state._sessions

    @pytest.mark.asyncio
    async def test_cleanup_skips_active_session(self, tmp_path, monkeypatch):
        """The active session is never archived even if stale."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        session = state.get_or_create_session("active")
        session.append("user", "old", ts=old_ts)
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 1, "active_session": "active"},
            )
            data = await resp.json()
            assert data["archived"] == 0
        assert "active" in state._sessions

    @pytest.mark.asyncio
    async def test_cleanup_saves_to_history(self, tmp_path, monkeypatch):
        """Archived sessions are persisted to conversation log."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        session = state.get_or_create_session("to-archive")
        session.append("user", "save me", ts=old_ts)
        session.append("assistant", "saved", ts=old_ts)
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 3},
            )

        msgs = state.conversation_log.read_messages("dashboard:to-archive")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "save me"

    @pytest.mark.asyncio
    async def test_cleanup_defaults_to_3_days(self, tmp_path, monkeypatch):
        """Without max_inactive_days, defaults to 3."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        ts_2d = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        session = state.get_or_create_session("recent")
        session.append("user", "hi", ts=ts_2d)
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/cleanup", json={})
            data = await resp.json()
            assert data["archived"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_empty_sessions_uses_created_at(self, tmp_path, monkeypatch):
        """Sessions with no messages use created_at for staleness."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        session = state.get_or_create_session("empty-old")
        session.created_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 7},
            )
            data = await resp.json()
            assert data["archived"] == 1
            assert "empty-old" in data["keys"]

    @pytest.mark.asyncio
    async def test_cleanup_no_stale_returns_zero(self, tmp_path, monkeypatch):
        """When all sessions are fresh, nothing is archived."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timezone

        fresh_ts = datetime.now(timezone.utc).isoformat()
        session = state.get_or_create_session("fresh")
        session.append("user", "hi", ts=fresh_ts)
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 1},
            )
            data = await resp.json()
            assert data["ok"] is True
            assert data["archived"] == 0
            assert data["keys"] == []

    @pytest.mark.asyncio
    async def test_cleanup_rollback_on_save_failure(self, tmp_path, monkeypatch):
        """When _save_session_to_history raises, session is restored and reported as failed."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        session = state.get_or_create_session("fail-save")
        session.append("user", "msg", ts=old_ts)
        session.drain()

        with patch(
            "personalclaw.dashboard.chat_handlers._save_session_to_history",
            side_effect=OSError("disk full"),
        ):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/cleanup",
                    json={"max_inactive_days": 1},
                )
                data = await resp.json()
                assert data["archived"] == 0
                assert "fail-save" in data["failed"]

        # Session must be restored (not lost)
        assert "fail-save" in state._sessions
        # No history entry should exist (save failed)
        msgs = state.conversation_log.read_messages("dashboard:fail-save")
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_cleanup_cancels_running_task(self, tmp_path, monkeypatch):
        """Running tasks on stale sessions are cancelled after archive."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        session = state.get_or_create_session("running1")
        session.append("user", "msg", ts=old_ts)
        session.drain()
        session.task = asyncio.get_running_loop().create_future()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 1},
            )
            data = await resp.json()
            assert data["archived"] == 1
        assert session.task.cancelled()

    @pytest.mark.asyncio
    async def test_cleanup_skips_unparseable_timestamps(self, tmp_path, monkeypatch):
        """Sessions with unparseable timestamps are skipped, not archived."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        session = state.get_or_create_session("bad-ts")
        session.append("user", "hi", ts="not-a-date")
        session.created_at = "also-not-a-date"
        session.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 1},
            )
            data = await resp.json()
            assert data["archived"] == 0
        assert "bad-ts" in state._sessions

    @pytest.mark.asyncio
    async def test_cleanup_dry_run_returns_keys_without_archiving(self, tmp_path, monkeypatch):
        """dry_run=True returns stale keys and active_is_stale but does not archive anything."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()

        stale = state.get_or_create_session("stale1")
        stale.append("user", "old msg", ts=old_ts)
        stale.drain()

        active_stale = state.get_or_create_session("active1")
        active_stale.append("user", "old active msg", ts=old_ts)
        active_stale.drain()

        fresh = state.get_or_create_session("fresh1")
        fresh.append("user", "new msg", ts=fresh_ts)
        fresh.drain()

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/cleanup",
                json={"max_inactive_days": 3, "active_session": "active1", "dry_run": True},
            )
            data = await resp.json()
            assert data["ok"] is True
            assert data["dry_run"] is True
            assert "stale1" in data["keys"]
            assert "active1" not in data["keys"]
            assert data["count"] == 1
            assert data["active_is_stale"] is True

        # Sessions should NOT have been removed
        assert "stale1" in state._sessions
        assert "active1" in state._sessions
        assert "fresh1" in state._sessions


class TestHistoryKeyFor:
    """Tests for _history_key_for — canonical history key from session key."""

    def test_already_canonical(self):
        from personalclaw.dashboard.chat import _history_key_for

        assert _history_key_for("dashboard:chat-1-100") == "dashboard:chat-1-100"

    def test_strips_single_prefix(self):
        from personalclaw.dashboard.chat import _history_key_for

        assert _history_key_for("dashboard_chat-1-100") == "dashboard:chat-1-100"

    def test_strips_double_prefix(self):
        from personalclaw.dashboard.chat import _history_key_for

        assert _history_key_for("dashboard_dashboard_chat-1-100") == "dashboard:chat-1-100"

    def test_strips_triple_prefix(self):
        from personalclaw.dashboard.chat import _history_key_for

        assert _history_key_for("dashboard_dashboard_dashboard_x") == "dashboard:x"

    def test_raw_key_gets_prefix(self):
        from personalclaw.dashboard.chat import _history_key_for

        assert _history_key_for("chat-1-100") == "dashboard:chat-1-100"


# ── Folder CRUD tests ──


class TestFolderCRUD:
    @pytest.mark.asyncio
    async def test_list_folders_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/chat/folders")
            assert resp.status == 200
            assert await resp.json() == []

    @pytest.mark.asyncio
    async def test_create_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Oncall"})
            assert resp.status == 201
            data = await resp.json()
            assert data["name"] == "Oncall"
            assert "id" in data
            assert data["collapsed"] is False
            # Persisted to disk
            assert (tmp_path / "folders.json").exists()

    @pytest.mark.asyncio
    async def test_create_folder_with_parent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Parent"})
            parent = await resp.json()
            resp = await client.post(
                "/api/chat/folders", json={"name": "Child", "parent_id": parent["id"]}
            )
            child = await resp.json()
            assert child["parent_id"] == parent["id"]

    @pytest.mark.asyncio
    async def test_create_folder_invalid_parent_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/chat/folders", json={"name": "Orphan", "parent_id": "nonexistent"}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_folder_empty_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": ""})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_folder_rename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Old"})
            folder = await resp.json()
            resp = await client.patch(f"/api/chat/folders/{folder['id']}", json={"name": "New"})
            assert resp.status == 200
            data = await resp.json()
            assert data["name"] == "New"

    @pytest.mark.asyncio
    async def test_update_folder_collapse(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "F"})
            folder = await resp.json()
            resp = await client.patch(f"/api/chat/folders/{folder['id']}", json={"collapsed": True})
            data = await resp.json()
            assert data["collapsed"] is True

    @pytest.mark.asyncio
    async def test_update_folder_empty_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Keep"})
            folder = await resp.json()
            resp = await client.patch(f"/api/chat/folders/{folder['id']}", json={"name": "  "})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_update_nonexistent_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/folders/nonexistent", json={"name": "X"})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Delete Me"})
            folder = await resp.json()
            resp = await client.delete(f"/api/chat/folders/{folder['id']}")
            assert resp.status == 200
            resp = await client.get("/api/chat/folders")
            assert await resp.json() == []

    @pytest.mark.asyncio
    async def test_delete_folder_reparents_children(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state._folders = [
            {"id": "parent", "name": "Parent", "order": 0, "collapsed": False},
            {"id": "child", "name": "Child", "order": 1, "collapsed": False, "parent_id": "parent"},
        ]
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            await client.delete("/api/chat/folders/parent")
            assert len(state._folders) == 1
            assert state._folders[0]["id"] == "child"
            assert state._folders[0].get("parent_id") == ""

    @pytest.mark.asyncio
    async def test_delete_folder_ungroups_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.folder_id = "f-del"
        state._folders.append({"id": "f-del", "name": "X", "order": 0, "collapsed": False})
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            await client.delete("/api/chat/folders/f-del")
            assert session.folder_id == ""

    @pytest.mark.asyncio
    async def test_assign_session_to_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("mysession")
        state._folders = [{"id": "f1", "name": "Test", "order": 0, "collapsed": False}]
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/chat/sessions/mysession/folder", json={"folder_id": "f1"}
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["folder_id"] == "f1"
            assert state._sessions["mysession"].folder_id == "f1"

    @pytest.mark.asyncio
    async def test_unassign_session_from_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("mysession")
        session.folder_id = "f1"
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/sessions/mysession/folder", json={"folder_id": ""})
            assert resp.status == 200
            assert state._sessions["mysession"].folder_id == ""

    @pytest.mark.asyncio
    async def test_assign_folder_nonexistent_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/sessions/nope/folder", json={"folder_id": "f1"})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_assign_nonexistent_folder_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("mysession")
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/chat/sessions/mysession/folder", json={"folder_id": "nonexistent"}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_pin_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("mysession")
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/sessions/mysession/pin", json={"pinned": True})
            assert resp.status == 200
            data = await resp.json()
            assert data["pinned"] is True
            assert state._sessions["mysession"].pinned is True

    @pytest.mark.asyncio
    async def test_unpin_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("mysession")
        session.pinned = True
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/sessions/mysession/pin", json={"pinned": False})
            assert resp.status == 200
            assert state._sessions["mysession"].pinned is False

    @pytest.mark.asyncio
    async def test_sessions_include_pinned(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.pinned = True
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/chat/sessions")
            sessions = await resp.json()
            assert any(s.get("pinned") is True for s in sessions)

    @pytest.mark.asyncio
    async def test_sessions_include_folder_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.folder_id = "f-abc"
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/chat/sessions")
            sessions = await resp.json()
            assert any(s["folder_id"] == "f-abc" for s in sessions)


class TestFolderPersistence:
    def test_load_folders_from_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        import json

        (tmp_path / "folders.json").write_text(
            json.dumps([{"id": "f1", "name": "Test", "order": 0}])
        )
        state = _make_state(tmp_path)
        state.load_folders()
        assert len(state._folders) == 1
        assert state._folders[0]["name"] == "Test"

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state._folders = [{"id": "f1", "name": "Roundtrip", "order": 0, "collapsed": True}]
        state.save_folders()
        state._folders = []
        state.load_folders()
        assert state._folders[0]["name"] == "Roundtrip"
        assert state._folders[0]["collapsed"] is True

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.load_folders()
        assert state._folders == []

    def test_load_corrupted_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        (tmp_path / "folders.json").write_text("not json")
        state = _make_state(tmp_path)
        state.load_folders()
        assert state._folders == []


class TestGenerateFolderIcon:
    @pytest.mark.asyncio
    async def test_valid_emoji_stored(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.dashboard.chat_folders import _generate_folder_icon

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        # Mock LLM session
        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "🚀"
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()
        state.push_sessions_update = MagicMock()

        folder = {"id": "f1", "name": "Deploy"}
        state._folders = [folder]
        await _generate_folder_icon(state, folder)

        assert folder["icon"] == "🚀"
        state.save_folders.assert_called_once()
        state.push_sessions_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_output_rejected(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.dashboard.chat_folders import _generate_folder_icon

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "This is not an emoji"
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()

        folder = {"id": "f1", "name": "Deploy"}
        state._folders = [folder]
        await _generate_folder_icon(state, folder)

        assert "icon" not in folder
        state.save_folders.assert_not_called()

    @pytest.mark.asyncio
    async def test_ascii_two_char_rejected(self, tmp_path, monkeypatch):
        """Two ASCII chars like '<>' should be rejected by emoji validation."""
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.dashboard.chat_folders import _generate_folder_icon

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "<>"
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()

        folder = {"id": "f1", "name": "Test"}
        state._folders = [folder]
        await _generate_folder_icon(state, folder)

        assert "icon" not in folder
        state.save_folders.assert_not_called()

    @pytest.mark.asyncio
    async def test_redaction_applied(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock, patch

        from personalclaw.dashboard.chat_folders import _generate_folder_icon

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "🔥"
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()
        state.push_sessions_update = MagicMock()

        with (
            patch(
                "personalclaw.dashboard.chat_folders.redact_exfiltration_urls",
                return_value=("🔥", False),
            ) as mock_url,
            patch(
                "personalclaw.dashboard.chat_folders.redact_credentials", return_value=("🔥", False)
            ) as mock_cred,
        ):
            folder = {"id": "f1", "name": "Oncall"}
            state._folders = [folder]
            await _generate_folder_icon(state, folder)
            mock_url.assert_called_once()
            mock_cred.assert_called_once()

    @pytest.mark.asyncio
    async def test_variation_selector_emoji_accepted(self, tmp_path, monkeypatch):
        """Emoji with U+FE0F variation selector (e.g. ❤️) should be accepted."""
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.dashboard.chat_folders import _generate_folder_icon

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "\u2764\ufe0f"  # ❤️
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()
        state.push_sessions_update = MagicMock()

        folder = {"id": "f1", "name": "Love"}
        state._folders = [folder]
        await _generate_folder_icon(state, folder)

        assert folder["icon"] == "\u2764\ufe0f"
        state.save_folders.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_background_session(self, tmp_path, monkeypatch):
        """Folder icon generation should use the shared background session."""
        from unittest.mock import AsyncMock, MagicMock

        from personalclaw.dashboard.chat_folders import _generate_folder_icon
        from personalclaw.session import BACKGROUND_KEY

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        mock_event = MagicMock()
        mock_event.kind = "text_chunk"
        mock_event.text = "🔥"
        done_event = MagicMock()
        done_event.kind = "complete"
        monkeypatch.setattr("personalclaw.llm.base.EVENT_TEXT_CHUNK", "text_chunk")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_COMPLETE", "complete")
        monkeypatch.setattr("personalclaw.llm.base.EVENT_PERMISSION_REQUEST", "permission")

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=AsyncIterator([mock_event, done_event]))
        state.sessions.get_or_create = AsyncMock(return_value=(mock_client, False, False))
        state.sessions.release = MagicMock()
        state.save_folders = MagicMock()
        state.push_sessions_update = MagicMock()

        folder = {"id": "abc123", "name": "Test"}
        state._folders = [folder]
        await _generate_folder_icon(state, folder)

        state.sessions.get_or_create.assert_called_once_with(BACKGROUND_KEY)
        state.sessions.release.assert_called_once_with(BACKGROUND_KEY)


class TestFolderAssignmentPersistence:
    @pytest.mark.asyncio
    async def test_folder_assignment_saves_to_history(self, tmp_path, monkeypatch):
        """api_chat_session_folder should call _save_session_to_history for new sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("mysession")
        session.append("user", "hello")
        session.drain()
        state._folders = [{"id": "f1", "name": "Test", "order": 0, "collapsed": False}]
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            await client.patch("/api/chat/sessions/mysession/folder", json={"folder_id": "f1"})
            path = tmp_path / "dashboard_mysession.jsonl"
            assert path.exists()
            import json

            meta = json.loads(path.read_text().split("\n")[0])
            assert meta["folder_id"] == "f1"

    @pytest.mark.asyncio
    async def test_folder_assignment_persists_on_resumed_session(self, tmp_path, monkeypatch):
        """Regression: folder_id must reach disk even when session is a resumed
        session with no new messages.

        Root cause: _save_session_to_history had an early-return guard that
        skipped disk writes when ``session._resumed_count > 0 and
        len(messages) <= session._resumed_count``. Metadata-only changes like
        folder assignment don't grow the message count past the resumed
        marker, so the save was silently dropped — folder_id never reached
        disk and the move was lost on the next gateway restart.

        Fix: folder endpoint passes ``force=True`` which bypasses the guard.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("resumedsession")
        session.append("user", "old message from before restart")
        session.drain()
        # Mark session as a resumed session (simulates being restored from disk).
        # The guard fires when _resumed_count >= len(messages).
        session._resumed_count = len(session.messages)
        state._folders = [{"id": "f-resumed", "name": "Build", "order": 0, "collapsed": False}]
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/chat/sessions/resumedsession/folder",
                json={"folder_id": "f-resumed"},
            )
            assert resp.status == 200
            path = tmp_path / "dashboard_resumedsession.jsonl"
            assert path.exists(), "folder_id save must reach disk on resumed session"
            import json

            meta = json.loads(path.read_text().split("\n")[0])
            assert meta.get("folder_id") == "f-resumed", (
                "folder_id was silently dropped on resumed session — "
                "force=True must bypass the _resumed_count guard"
            )

    @pytest.mark.asyncio
    async def test_pin_toggle_persists_on_resumed_session(self, tmp_path, monkeypatch):
        """Regression: pinned flag must reach disk on resumed sessions.

        Same root cause as the folder regression — the resumed-count guard
        in _save_session_to_history was blocking metadata-only writes. Pin
        endpoint now passes ``force=True``.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("pinsession")
        session.append("user", "old message")
        session.drain()
        session._resumed_count = len(session.messages)
        app = _make_folder_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/chat/sessions/pinsession/pin", json={"pinned": True})
            assert resp.status == 200
            path = tmp_path / "dashboard_pinsession.jsonl"
            assert path.exists(), "pinned save must reach disk on resumed session"
            import json

            meta = json.loads(path.read_text().split("\n")[0])
            assert meta.get("pinned") is True, (
                "pinned was silently dropped on resumed session — "
                "force=True must bypass the _resumed_count guard"
            )

    def test_save_session_force_bypasses_resumed_guard(self, tmp_path, monkeypatch):
        """Unit test: ``force=True`` must bypass the resumed-session guard.

        Without force, resumed sessions with no new messages skip the write.
        With force, the metadata-only mutation reaches disk regardless.
        """
        from personalclaw.dashboard.chat import _save_session_to_history

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("forcesession")
        session.append("user", "hello")
        session.drain()
        session._resumed_count = len(session.messages)
        session.folder_id = "f-force"

        # Without force — save is skipped by the guard, no file written.
        _save_session_to_history(state, session)
        path = tmp_path / "dashboard_forcesession.jsonl"
        assert not path.exists(), "guard must skip save when not forced"

        # With force — save bypasses the guard, file is written with folder_id.
        _save_session_to_history(state, session, force=True)
        assert path.exists(), "force=True must bypass the guard"
        import json

        meta = json.loads(path.read_text().split("\n")[0])
        assert meta.get("folder_id") == "f-force"


class TestNewPlanResetsAutoRun:
    """Regression: _auto_run must reset when a new plan is detected."""

    def test_has_plan_resets_auto_run(self):
        """When LLM generates a new plan mid-execution, auto_run must be cleared."""
        from personalclaw.dashboard.chat import _reset_auto_run_for_new_plan

        session = _ChatSession("plan-reset")
        session._auto_run = True
        session._orch_tracker = MagicMock()

        _reset_auto_run_for_new_plan(session)

        assert session._auto_run is False, "_auto_run must be reset for new plan"
        assert session._orch_tracker is None


# ── Regenerate + variant switching ──


class TestRegenerateAndVariants:
    @pytest.mark.asyncio
    async def test_regenerate_truncates_and_stashes_variant(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "hello v1")
        session.drain()
        captured = []

        async def _capture(*a, **kw):
            captured.extend(list(session._pending_variants))

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)
        assert [m["role"] for m in session.messages] == ["user"]
        assert len(captured) == 1
        assert captured[0]["content"] == "hello v1"

    @pytest.mark.asyncio
    async def test_regenerate_rejects_when_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "hello")

        # Simulate running task
        async def _noop():
            await asyncio.sleep(10)

        session.task = asyncio.create_task(_noop())
        try:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 409
        finally:
            session.task.cancel()

    @pytest.mark.asyncio
    async def test_regenerate_requires_prior_assistant(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "only user")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_switch_variant_updates_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "v2")
        session.messages[-1]["variants"] = [
            {"content": "v1", "ts": "t1"},
            {"content": "v2", "ts": "t2"},
        ]
        session.messages[-1]["variant_idx"] = 1
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 0})
            assert resp.status == 200
            assert session.messages[-1]["content"] == "v1"
            assert session.messages[-1]["variant_idx"] == 0

    @pytest.mark.asyncio
    async def test_switch_variant_index_out_of_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [{"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 5})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_regenerate_passes_hint_to_run_chat(self, tmp_path, monkeypatch):
        """_run_chat should receive a non-empty regenerate_hint kwarg."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "reply")
        session.drain()
        mock_run = AsyncMock()
        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=mock_run):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                # Let the scheduled task actually run so the mock records args
                await asyncio.sleep(0)
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("regenerate_hint"), "regenerate_hint must be non-empty"

    @pytest.mark.asyncio
    async def test_regenerate_preserves_existing_variants(self, tmp_path, monkeypatch):
        """When assistant already has variants[], regenerate keeps them and adds current."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "v2")
        session.messages[-1]["variants"] = [
            {"content": "v1", "ts": "t1"},
            {"content": "v2", "ts": "t2"},
        ]
        session.messages[-1]["variant_idx"] = 1
        session.drain()
        captured = []

        async def _capture(*a, **kw):
            captured.extend(list(session._pending_variants))

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)
        assert [v["content"] for v in captured] == ["v1", "v2"]

    @pytest.mark.asyncio
    async def test_regenerate_when_active_is_old_variant_no_dup(self, tmp_path, monkeypatch):
        """If user switched back to v1 then regenerates, v1 should not be appended twice."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [
            {"content": "v1", "ts": "t1"},
            {"content": "v2", "ts": "t2"},
        ]
        session.messages[-1]["variant_idx"] = 0
        session.drain()
        captured = []

        async def _capture(*a, **kw):
            captured.extend(list(session._pending_variants))

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)
        assert [v["content"] for v in captured] == ["v1", "v2"]

    @pytest.mark.asyncio
    async def test_regenerate_caps_variants(self, tmp_path, monkeypatch):
        """Variant list is capped; oldest entries drop when over _MAX_VARIANTS."""
        from personalclaw.dashboard.chat import _MAX_VARIANTS

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "newest")
        existing = [{"content": f"v{i}", "ts": f"t{i}"} for i in range(_MAX_VARIANTS)]
        session.messages[-1]["variants"] = existing
        session.messages[-1]["variant_idx"] = len(existing) - 1
        session.drain()
        captured = []

        async def _capture(*a, **kw):
            captured.extend(list(session._pending_variants))

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_capture):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                await asyncio.sleep(0)
        assert len(captured) <= _MAX_VARIANTS
        assert captured[-1]["content"] == "newest"

    @pytest.mark.asyncio
    async def test_regenerate_rejects_missing_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/nonexistent/regenerate")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_regenerate_rejects_empty_user_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "")
        session.append("assistant", "reply")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_regenerate_persists_to_disk(self, tmp_path, monkeypatch):
        """After regenerate, on-disk history should reflect the truncation."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "old")
        session.drain()
        # Save first so a file exists
        from personalclaw.dashboard.chat import _history_key_for, _save_session_to_history

        _save_session_to_history(state, session)
        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
        # File should now only contain the user message (assistant truncated)
        key = _history_key_for(session.key)
        persisted = state.conversation_log.read_messages(key)
        roles = [m.get("role") for m in persisted]
        assert roles == ["user"]

    @pytest.mark.asyncio
    async def test_save_session_redacts_variants(self, tmp_path, monkeypatch):
        """Variants written to disk must have credentials/exfil URLs redacted."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "safe content")
        # Plant a fake credential inside a variant
        session.messages[-1]["variants"] = [
            {"content": "AKIAIOSFODNN7EXAMPLE secret stuff", "ts": "t1"},
            {"content": "safe content", "ts": "t2"},
        ]
        session.messages[-1]["variant_idx"] = 1
        session.drain()
        from personalclaw.dashboard.chat import _history_key_for, _save_session_to_history

        _save_session_to_history(state, session)
        key = _history_key_for(session.key)
        persisted = state.conversation_log.read_messages(key)
        ai = [m for m in persisted if m.get("role") == "assistant"][0]
        assert "variants" in ai
        # The AKIA key must not appear in either variant after redaction
        for v in ai["variants"]:
            assert "AKIAIOSFODNN7EXAMPLE" not in v.get("content", "")

    @pytest.mark.asyncio
    async def test_switch_variant_rejects_when_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v2")
        session.messages[-1]["variants"] = [
            {"content": "v1", "ts": "t1"},
            {"content": "v2", "ts": "t2"},
        ]

        async def _noop():
            await asyncio.sleep(10)

        session.task = asyncio.create_task(_noop())
        try:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 0})
                assert resp.status == 409
        finally:
            session.task.cancel()

    @pytest.mark.asyncio
    async def test_switch_variant_missing_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/none/switch-variant", json={"index": 0})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_switch_variant_no_variants(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "plain")  # no variants[]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 0})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_switch_variant_invalid_json_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [{"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", data="not-json")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_switch_variant_non_int_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [{"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": "abc"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_regenerate_clears_pending_on_task_error(self, tmp_path, monkeypatch):
        """If _run_chat raises, _pending_variants must be cleared to prevent leak."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "reply")
        session.drain()

        async def _boom(*a, **kw):
            raise RuntimeError("llm blew up")

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_boom):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                # Let the failing task propagate through done_callback
                for _ in range(5):
                    await asyncio.sleep(0)
        assert session._pending_variants == [], "pending variants must be cleared when task errors"

    @pytest.mark.asyncio
    async def test_flush_segment_attaches_pending_variants(self, tmp_path, monkeypatch):
        """_flush_segment should attach _pending_variants to the new assistant message."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        # Simulate pending variants from a regenerate
        session._pending_variants = [
            {"content": "old v1", "ts": "t1"},
            {"content": "old v2", "ts": "t2"},
        ]
        from personalclaw.dashboard.chat import _flush_segment

        _flush_segment(state, session, "new reply", broadcast=False)
        last = session.messages[-1]
        assert last["role"] == "assistant"
        assert last["content"] == "new reply"
        assert len(last["variants"]) == 3  # old v1, old v2, new reply
        assert last["variant_idx"] == 2
        assert session._pending_variants == []

    @pytest.mark.asyncio
    async def test_switch_variant_negative_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [{"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": -1})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_regenerate_only_system_and_assistant(self, tmp_path, monkeypatch):
        """Regenerate should fail if there's no user message (only system + assistant)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("system", "you are helpful")
        session.append("assistant", "hello")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_flush_segment_no_pending_no_variants(self, tmp_path, monkeypatch):
        """Normal flush without pending variants should not add variants field."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        from personalclaw.dashboard.chat import _flush_segment

        _flush_segment(state, session, "reply", broadcast=False)
        last = session.messages[-1]
        assert "variants" not in last

    @pytest.mark.asyncio
    async def test_switch_variant_missing_index_key(self, tmp_path, monkeypatch):
        """Request body without 'index' key should return 400."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = [{"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_restore_preserves_variants(self, tmp_path, monkeypatch):
        """Variants written to disk should be restored via production code path."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "v2")
        session.messages[-1]["variants"] = [
            {"content": "v1", "ts": "t1"},
            {"content": "v2", "ts": "t2"},
        ]
        session.messages[-1]["variant_idx"] = 1
        session.drain()
        from personalclaw.dashboard.chat import _save_session_to_history, restore_recent_sessions

        _save_session_to_history(state, session)
        # Clear in-memory state and restore via production path
        state._sessions.clear()
        restore_recent_sessions(state, window_minutes=9999)
        restored_session = state._sessions.get("s1")
        assert restored_session is not None
        ai = [m for m in restored_session.messages if m.get("role") == "assistant"][0]
        assert "variants" in ai
        assert len(ai["variants"]) == 2
        assert ai["variant_idx"] == 1

    @pytest.mark.asyncio
    async def test_regenerate_clears_pending_on_cancel(self, tmp_path, monkeypatch):
        """If user stops a regeneration (cancel), _pending_variants must be cleared."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "reply")
        session.drain()

        async def _hang(*a, **kw):
            await asyncio.sleep(999)

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_hang):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post("/api/chat/sessions/s1/regenerate")
                assert resp.status == 200
                assert session._pending_variants != []
                # Cancel the task (simulates user clicking Stop)
                session.task.cancel()
                for _ in range(5):
                    await asyncio.sleep(0)
        assert (
            session._pending_variants == []
        ), "pending variants must be cleared when task is cancelled"

    @pytest.mark.asyncio
    async def test_prepare_messages_redacts_variant_content(self, tmp_path, monkeypatch):
        """Variant content exposed via API must have credentials redacted."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "safe")
        session.messages[-1]["variants"] = [
            {"content": "AKIAIOSFODNN7EXAMPLE leaked key", "ts": "t1"},
            {"content": "safe", "ts": "t2"},
        ]
        from personalclaw.dashboard.chat import _prepare_messages

        prepared = _prepare_messages(session.messages, False)
        ai = [m for m in prepared if m.get("role") == "assistant"][0]
        for v in ai["variants"]:
            assert "AKIAIOSFODNN7EXAMPLE" not in v.get("content", "")

    @pytest.mark.asyncio
    async def test_switch_variant_corrupt_entry(self, tmp_path, monkeypatch):
        """If a variant entry is not a dict, switch-variant should return 400."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("assistant", "v1")
        session.messages[-1]["variants"] = ["not-a-dict", {"content": "v1"}]
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/switch-variant", json={"index": 0})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_concurrent_regenerate_one_succeeds_one_409(self, tmp_path, monkeypatch):
        """Two simultaneous regenerate requests: one gets 200, the other gets 409."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi")
        session.append("assistant", "reply")
        session.drain()

        async def _hang(*a, **kw):
            await asyncio.sleep(999)

        with patch("personalclaw.dashboard.chat_regenerate._run_chat", new=_hang):
            async with TestClient(TestServer(_make_app(state))) as client:
                r1, r2 = await asyncio.gather(
                    client.post("/api/chat/sessions/s1/regenerate"),
                    client.post("/api/chat/sessions/s1/regenerate"),
                )
                statuses = sorted([r1.status, r2.status])
                assert statuses == [200, 409], f"Expected one 200 and one 409, got {statuses}"
        # Cleanup
        if session.task:
            session.task.cancel()


class TestForkSession:
    """Tests for POST /api/chat/sessions/{session}/fork."""

    @pytest.mark.asyncio
    async def test_fork_copies_all_messages(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.title = "My Chat"
        session._titled = True
        session.append("user", "hello", "msg msg-u")
        session.append("assistant", "hi there", "msg msg-a")
        session.append("user", "how are you", "msg msg-u")
        session.append("assistant", "good", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["messages"] == 4
            assert data["title"] == "Fork of My Chat"

        new_session = state._sessions.get(data["key"])
        assert new_session is not None
        assert new_session.forked_from == "dashboard:src"
        visible = [m for m in new_session.messages if m["role"] in ("user", "assistant")]
        assert len(visible) == 4

    @pytest.mark.asyncio
    async def test_fork_at_index(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "msg1", "msg msg-u")
        session.append("assistant", "reply1", "msg msg-a")
        session.append("user", "msg2", "msg msg-u")
        session.append("assistant", "reply2", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 1})
            assert resp.status == 200
            data = await resp.json()
            assert data["messages"] == 2

        new_session = state._sessions.get(data["key"])
        visible = [m for m in new_session.messages if m["role"] in ("user", "assistant")]
        assert len(visible) == 2
        assert visible[-1]["content"] == "reply1"

    @pytest.mark.asyncio
    async def test_fork_not_found(self, tmp_path):
        state = _make_state(tmp_path)
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/nope/fork", json={})
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_fork_empty_session(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("empty")
        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/empty/fork", json={})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_fork_inherits_agent_and_workspace(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src", agent="my-agent", workspace_dir="/tmp/my-ws")
        session.model = "custom-model"
        session.mode = "custom-mode"
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()

        new_session = state._sessions.get(data["key"])
        assert new_session.agent == "my-agent"
        assert new_session.workspace_dir == "/tmp/my-ws"
        assert new_session.model == "custom-model"
        assert new_session.mode == "custom-mode"

    @pytest.mark.asyncio
    async def test_fork_inherits_folder(self, tmp_path):
        """Fork must land in the same project folder as the source session."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.folder_id = "proj-abc"
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()

        new_session = state._sessions.get(data["key"])
        assert new_session is not None
        assert new_session.folder_id == "proj-abc"

    @pytest.mark.asyncio
    async def test_fork_inherits_empty_folder(self, tmp_path):
        """Fork of an unfoldered session stays unfoldered (root)."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()

        new_session = state._sessions.get(data["key"])
        assert new_session.folder_id == ""

    @pytest.mark.asyncio
    async def test_fork_with_prompt(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "context", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/chat/sessions/src/fork",
                json={"prompt": "fix the bug"},
            )
            data = await resp.json()
            assert data["ok"] is True
            assert data["prompt"] == "fix the bug"
            assert data["messages"] == 1

        # Prompt is returned for frontend to send separately — must NOT be
        # injected into the forked session server-side.
        new_session = state._sessions.get(data["key"])
        assert all(m["content"] != "fix the bug" for m in new_session.messages)

    @pytest.mark.asyncio
    async def test_fork_redacts_credentials_in_assistant_messages(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "show me the key", "msg msg-u")
        session.append("assistant", "Here: AKIAIOSFODNN7EXAMPLE", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()
            assert data["ok"] is True

        new_session = state._sessions.get(data["key"])
        assistant_msgs = [m for m in new_session.messages if m["role"] == "assistant"]
        assert "AKIAIOSFODNN7EXAMPLE" not in assistant_msgs[0]["content"]
        assert "[REDACTED" in assistant_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_fork_redacts_credentials_in_llm_generated_title(self, tmp_path):
        """Parent title is LLM-generated (via /api/chat/generate-title) and
        flows into the new session's title + API response + dashboard JSON.
        Must be redacted like any other LLM output.
        """
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.title = "Leaked AKIAIOSFODNN7EXAMPLE key"
        session._titled = True
        session.append("user", "hi", "msg msg-u")
        session.append("assistant", "ok", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()

        assert "AKIAIOSFODNN7EXAMPLE" not in data["title"]
        assert "[REDACTED" in data["title"]
        assert data["title"].startswith("Fork of ")
        new_session = state._sessions.get(data["key"])
        assert "AKIAIOSFODNN7EXAMPLE" not in new_session.title

    @pytest.mark.asyncio
    async def test_fork_rejects_bool_index(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": True})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_fork_rejects_negative_index(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": -1})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_fork_excludes_system_messages(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("system", "you are helpful", "msg msg-s")
        session.append("user", "hello", "msg msg-u")
        session.append("assistant", "hi", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()
            assert data["messages"] == 2

        new_session = state._sessions.get(data["key"])
        roles = [m["role"] for m in new_session.messages]
        assert "system" not in roles

    @pytest.mark.asyncio
    async def test_fork_persists_to_disk(self, tmp_path):
        """Forked session (and forked_from metadata) must survive a save/restore cycle."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.append("assistant", "hello", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()
            new_key = data["key"]

        # Simulate a gateway restart by reading messages + metadata from disk
        from personalclaw.dashboard.chat import _history_key_for

        hk = _history_key_for(new_key)
        meta = state.conversation_log.get_metadata(hk)
        disk_msgs = state.conversation_log.read_messages(hk)
        assert meta.get("forked_from") == "dashboard:src", f"forked_from not persisted; meta={meta}"
        assert len(disk_msgs) == 2, f"forked messages not persisted (got {len(disk_msgs)})"

    @pytest.mark.asyncio
    async def test_fork_rejects_oversized_prompt(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/chat/sessions/src/fork",
                json={"prompt": "x" * 40_000},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_fork_rejects_out_of_range_index(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.append("assistant", "hello", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={"at_message_index": 5})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_fork_succeeds_while_streaming(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.append("assistant", "done reply", "msg msg-a")
        session.drain()

        # Simulate a running session: task attribute non-None + not done
        class _FakeTask:
            def done(self):
                return False

        session.task = _FakeTask()  # type: ignore[assignment]
        assert session.running is True

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()
            assert data["messages"] == 2

    @pytest.mark.asyncio
    async def test_fork_emits_sel_audit_event(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        mock_sel = MagicMock()
        monkeypatch.setattr("personalclaw.dashboard.chat_fork.sel", lambda: mock_sel)

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()

        mock_sel.log_api_access.assert_called_once()
        kw = mock_sel.log_api_access.call_args[1]
        assert kw["operation"] == "chat.session_fork"
        assert kw["outcome"] == "allowed"
        assert "from=src" in kw["resources"]
        assert f"to={data['key']}" in kw["resources"]
        # L5 audit enrichment: at_index + prompt_len present
        assert "at_index=last" in kw["resources"]
        assert "prompt_len=0" in kw["resources"]

    @pytest.mark.asyncio
    async def test_fork_rejects_ephemeral_session(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.memory_mode = "incognito"
        session.append("user", "secret", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 400
            data = await resp.json()
            assert "persistent" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_fork_history_visible_to_new_agent_via_context_builder(self, tmp_path):
        """Forked JSONL is the source build_session_context reads for the new session.

        Guarantees the fresh ACP agent process in the forked tab receives the
        copied user/assistant turns as thread-history context.
        """
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "parent question", "msg msg-u")
        session.append("assistant", "parent answer", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()
            new_key = data["key"]

        # conversation_log.recent(forked_key) is what ContextBuilder.build_session_context
        # calls to assemble the thread-history section for the new agent process.
        from personalclaw.dashboard.chat import _history_key_for

        recent = state.conversation_log.recent(_history_key_for(new_key))
        visible = [m for m in recent if m.get("role") in ("user", "assistant")]
        assert [m["content"] for m in visible] == [
            "parent question",
            "parent answer",
        ], f"fork history not readable as new-session context: {visible}"

    @pytest.mark.asyncio
    async def test_fork_does_not_clone_parent_agent_session_id(self, tmp_path, monkeypatch):
        """Parent's ACP agent session id (session_map sid) must NOT carry to fork.

        Cloning the sid would make both tabs share one agent process state and
        corrupt each other's view. Fork creates a FRESH agent session on first
        prompt by leaving session_map unset for the new key.
        """
        from personalclaw.session import SessionMap

        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        session_map = SessionMap()
        session_map.set("dashboard:src", "parent-agent-sid-abc123")

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            data = await resp.json()
            new_key = data["key"]

        # Re-read from disk so we're not trusting an in-process cache.
        # Inspect _data directly to skip SessionMap.get()'s agent-session file
        # existence check (we don't spawn real agent processes in unit tests).
        reloaded = SessionMap()
        assert (
            reloaded._data.get("dashboard:src", {}).get("sid") == "parent-agent-sid-abc123"
        ), "parent's agent sid should survive fork unchanged"
        assert (
            f"dashboard:{new_key}" not in reloaded._data
        ), "forked session must NOT inherit parent's agent sid"

    @pytest.mark.asyncio
    async def test_fork_of_fork_chains_forked_from(self, tmp_path):
        """M10: fork of a fork titles correctly and `forked_from` points to intermediate, not root."""  # noqa: E501
        state = _make_state(tmp_path)
        root = state.get_or_create_session("root")
        root.title = "Original"
        root._titled = True
        root.append("user", "q1", "msg msg-u")
        root.append("assistant", "a1", "msg msg-a")
        root.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r1 = await client.post("/api/chat/sessions/root/fork", json={})
            d1 = await r1.json()
            mid_key = d1["key"]
            assert d1["title"] == "Fork of Original"

            mid = state._sessions.get(mid_key)
            mid.append("user", "q2", "msg msg-u")
            mid.append("assistant", "a2", "msg msg-a")
            mid.drain()

            r2 = await client.post(f"/api/chat/sessions/{mid_key}/fork", json={})
            d2 = await r2.json()

        leaf = state._sessions.get(d2["key"])
        assert d2["title"] == "Fork of Fork of Original"
        assert (
            leaf.forked_from == f"dashboard:{mid_key}"
        ), f"leaf forked_from should point to intermediate, got {leaf.forked_from}"
        assert leaf.forked_from != "dashboard:root"
        visible = [m for m in leaf.messages if m["role"] in ("user", "assistant")]
        assert [m["content"] for m in visible] == ["q1", "a1", "q2", "a2"]

    @pytest.mark.asyncio
    async def test_fork_reads_full_history_from_disk_when_memory_capped(self, tmp_path):
        """M12: when in-memory snapshot is smaller than full history, fork reads from disk."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        for i in range(250):
            session.append("user" if i % 2 == 0 else "assistant", f"m{i}", "msg")
        session.drain()
        from personalclaw.dashboard.chat import _save_session_to_history

        _save_session_to_history(state, session)
        # Simulate restore cap: keep only last 50 in memory.
        # Clear _dirty so the endpoint's flush-if-dirty path doesn't overwrite disk.
        session.messages = session.messages[-50:]
        session._dirty = False

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()

        assert (
            data["messages"] == 250
        ), f"fork should read full history from disk, got {data['messages']}"
        new_session = state._sessions.get(data["key"])
        visible = [m for m in new_session.messages if m["role"] in ("user", "assistant")]
        assert len(visible) == 250
        assert visible[0]["content"] == "m0"
        assert visible[-1]["content"] == "m249"

    @pytest.mark.asyncio
    async def test_fork_preserves_full_history_when_dirty_and_capped(self, tmp_path):
        """A1 regression: _dirty=True + capped in-memory must NOT truncate disk history."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        for i in range(250):
            session.append("user" if i % 2 == 0 else "assistant", f"m{i}", "msg")
        session.drain()
        from personalclaw.dashboard.chat import _save_session_to_history

        _save_session_to_history(state, session)
        # Simulate restore with cap: real path caps messages then sets
        # _resumed_count to the capped length. User then sends new messages.
        session.messages = session.messages[-50:]
        session._resumed_count = len(session.messages)
        session.append("user", "new1", "msg")
        session.append("assistant", "new2", "msg")
        session.drain()
        assert session._dirty is True

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()

        # Full 250 on disk + 2 new dirty messages = 252 total.
        assert (
            data["messages"] == 252
        ), f"fork must preserve full disk history + dirty tail, got {data['messages']}"
        new_session = state._sessions.get(data["key"])
        visible = [m for m in new_session.messages if m["role"] in ("user", "assistant")]
        assert visible[0]["content"] == "m0"
        assert visible[-2]["content"] == "new1"
        assert visible[-1]["content"] == "new2"

    @pytest.mark.asyncio
    async def test_fork_concurrent_requests_both_succeed(self, tmp_path):
        """R2-7: two rapid fork requests on the same session both return 200 with
        identical visible-message counts. Each fork produces an independent new
        session; no messages lost or duplicated."""
        import asyncio

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "q1", "msg msg-u")
        session.append("assistant", "a1", "msg msg-a")
        session.append("user", "q2", "msg msg-u")
        session.append("assistant", "a2", "msg msg-a")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            r1, r2 = await asyncio.gather(
                client.post("/api/chat/sessions/src/fork", json={}),
                client.post("/api/chat/sessions/src/fork", json={}),
            )
            assert r1.status == 200 and r2.status == 200
            d1, d2 = await r1.json(), await r2.json()

        assert d1["key"] != d2["key"], "concurrent forks must produce distinct session keys"
        assert (
            d1["messages"] == d2["messages"] == 4
        ), f"both forks must copy all 4 visible messages, got {d1['messages']}/{d2['messages']}"
        for key in (d1["key"], d2["key"]):
            new_session = state._sessions.get(key)
            visible = [m for m in new_session.messages if m["role"] in ("user", "assistant")]
            assert [m["content"] for m in visible] == ["q1", "a1", "q2", "a2"]

    @pytest.mark.asyncio
    async def test_fork_audits_denied_on_ephemeral(self, tmp_path, monkeypatch):
        """M-1 regression: ephemeral rejection must emit a denied SEL event."""
        from unittest.mock import MagicMock

        mock_sel = MagicMock()
        monkeypatch.setattr("personalclaw.dashboard.chat_fork.sel", lambda: mock_sel)

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.memory_mode = "incognito"
        session.append("user", "hi", "msg msg-u")
        session.drain()

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 400

        mock_sel.log_api_access.assert_called_once()
        kw = mock_sel.log_api_access.call_args[1]
        assert kw["operation"] == "chat.session_fork"
        assert kw["outcome"] == "denied"
        assert "memory_mode=incognito" in kw["resources"]

    @pytest.mark.asyncio
    async def test_fork_app_isolation_rejects_cross_app(self, tmp_path, monkeypatch):
        """M-2 regression: app A cannot fork a session owned by app B."""
        from unittest.mock import MagicMock

        mock_sel = MagicMock()
        monkeypatch.setattr("personalclaw.dashboard.chat_fork.sel", lambda: mock_sel)

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src", app="app-B")
        session.append("user", "secret", "msg msg-u")
        session.drain()

        # aiohttp middleware populates request["app"]; test injects via middleware.
        @web.middleware
        async def inject_app(request, handler):
            request["app"] = "app-A"
            return await handler(request)

        app_obj = _make_app(state)
        app_obj.middlewares.insert(0, inject_app)

        async with TestClient(TestServer(app_obj)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 403
            data = await resp.json()
            assert "does not own" in data["error"]

        # denied event logged
        denied_calls = [
            c for c in mock_sel.log_api_access.call_args_list if c[1].get("outcome") == "denied"
        ]
        assert len(denied_calls) == 1
        assert denied_calls[0][1]["source"] == "app_isolation"

    @pytest.mark.asyncio
    async def test_fork_inherits_app_ownership(self, tmp_path):
        """I-1 regression: new_session._app is the requesting app (or empty for dashboard)."""
        state = _make_state(tmp_path)
        session = state.get_or_create_session("src", app="app-X")
        session.append("user", "hi", "msg msg-u")
        session.drain()

        @web.middleware
        async def inject_app(request, handler):
            request["app"] = "app-X"
            return await handler(request)

        app_obj = _make_app(state)
        app_obj.middlewares.insert(0, inject_app)

        async with TestClient(TestServer(app_obj)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 200
            data = await resp.json()

        new_session = state._sessions.get(data["key"])
        assert (
            new_session._app == "app-X"
        ), f"forked session must inherit caller's app, got {new_session._app!r}"

    @pytest.mark.asyncio
    async def test_fork_rejects_when_session_cap_reached(self, tmp_path, monkeypatch):
        """zejiangg rev 3 #46: fork must return 429 + denied audit when session cap hit."""
        from unittest.mock import MagicMock

        mock_sel = MagicMock()
        monkeypatch.setattr("personalclaw.dashboard.chat_fork.sel", lambda: mock_sel)
        # Lower the cap so we don't need to create hundreds of sessions.
        monkeypatch.setattr("personalclaw.dashboard.chat_fork._MAX_SESSIONS_FOR_FORK", 3)

        state = _make_state(tmp_path)
        session = state.get_or_create_session("src")
        session.append("user", "hi", "msg msg-u")
        session.drain()
        # Pre-populate to hit the cap (src + 2 dummies = 3).
        state.get_or_create_session("dummy1")
        state.get_or_create_session("dummy2")
        assert len(state._sessions) == 3

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/src/fork", json={})
            assert resp.status == 429
            data = await resp.json()
            assert "cap" in data["error"].lower()

        denied = [
            c for c in mock_sel.log_api_access.call_args_list if c[1].get("outcome") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0][1]["source"] == "rate_limit"


# ── Color theme & persona injection tests ──


class TestColorTheme:
    """Tests for color_theme validation, session assignment, and Lumon persona injection."""

    @pytest.mark.asyncio
    async def test_color_theme_set_on_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "theme-session", "color_theme": "lumon"},
                )
                assert resp.status == 200
                assert state._sessions["theme-session"].color_theme == "lumon"

    @pytest.mark.asyncio
    async def test_color_theme_cleared_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("theme-session")
        session.color_theme = "lumon"
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "theme-session", "color_theme": ""},
                )
                assert resp.status == 200
                assert session.color_theme == ""

    @pytest.mark.asyncio
    async def test_color_theme_not_cleared_when_absent(self, tmp_path, monkeypatch):
        """Omitting color_theme from body must not reset an existing theme."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("theme-session")
        session.color_theme = "lumon"
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "theme-session"},
                )
                assert resp.status == 200
                assert session.color_theme == "lumon"

    @pytest.mark.asyncio
    async def test_invalid_color_theme_coerced_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "theme-session", "color_theme": "evil"},
                )
                assert resp.status == 200
                assert state._sessions["theme-session"].color_theme == ""

    @pytest.mark.asyncio
    async def test_non_string_color_theme_coerced(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        with patch("personalclaw.dashboard.chat_handlers._run_chat", new=AsyncMock()):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat?ws=1",
                    json={"message": "hi", "session": "theme-session", "color_theme": 42},
                )
                assert resp.status == 200
                assert state._sessions["theme-session"].color_theme == ""


class TestLumonPersonaInjection:
    """Tests for _maybe_inject_persona helper function."""

    def setup_method(self):
        from personalclaw.dashboard import chat

        if hasattr(chat, "_cached_lumon_persona"):
            chat._cached_lumon_persona.cache_clear()

    def test_persona_appended_when_lumon(self, tmp_path):
        from personalclaw.dashboard.chat import _maybe_inject_persona

        fake_persona = "Use a light Lumon-inspired persona."
        with patch(
            "personalclaw.dashboard.chat_utils._cached_lumon_persona", return_value=fake_persona
        ):
            result = _maybe_inject_persona("hello", "lumon", True)

        assert "[LUMON PERSONA]" in result
        assert fake_persona in result

    def test_persona_not_appended_without_lumon(self):
        from personalclaw.dashboard.chat import _maybe_inject_persona

        result = _maybe_inject_persona("hello", "", True)
        assert result == "hello"

    def test_persona_not_appended_on_followup(self):
        from personalclaw.dashboard.chat import _maybe_inject_persona

        result = _maybe_inject_persona("hello", "lumon", False)
        assert result == "hello"

    def test_persona_survives_cache_error(self):
        from personalclaw.dashboard.chat import _maybe_inject_persona

        with patch(
            "personalclaw.dashboard.chat_utils._cached_lumon_persona",
            side_effect=ImportError("boom"),
        ):
            result = _maybe_inject_persona("hello", "lumon", True)
        assert result == "hello"

    def test_persona_empty_cache_returns_original(self):
        from personalclaw.dashboard.chat import _maybe_inject_persona

        with patch("personalclaw.dashboard.chat_utils._cached_lumon_persona", return_value=""):
            result = _maybe_inject_persona("hello", "lumon", True)
        assert result == "hello"


class TestStopReasonCancelled:
    """Phase 4: handler response to stopReason='cancelled'."""

    @staticmethod
    def _make_mock_client(events):
        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)

        async def _stream(msg):
            for ev in events:
                yield ev

        client.stream = _stream
        client.stream_command = _stream
        return client

    @staticmethod
    def _make_state_for_run_chat(tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.context_builder = None
        state.consolidator = MagicMock()
        state._hook_store = None
        import personalclaw.trust_mode as _tm

        _tm.disable_yolo()
        return state

    @pytest.mark.asyncio
    async def test_handler_stop_reason_cancelled_skips_record_success(self, tmp_path, monkeypatch):
        """When EVENT_COMPLETE carries stop_reason='cancelled', neither
        record_success nor record_failure should be called."""
        from personalclaw.acp.types import STOP_REASON_CANCELLED
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="partial"),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED),
        ]
        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
        state.sessions.record_success = MagicMock()
        state.sessions.record_failure = AsyncMock()

        await _run_chat(state, session, "hello")

        state.sessions.record_success.assert_not_called()
        state.sessions.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_stop_reason_cancelled_skips_consolidation(self, tmp_path, monkeypatch):
        """When cancelled, maybe_consolidate must not be called."""
        from personalclaw.acp.types import STOP_REASON_CANCELLED
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="partial"),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED),
        ]
        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        await _run_chat(state, session, "hello")

        state.consolidator.maybe_consolidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_stop_reason_end_turn_preserves_existing_behavior(
        self, tmp_path, monkeypatch
    ):
        """When stop_reason='end_turn', record_success and maybe_consolidate fire."""
        from personalclaw.acp.types import STOP_REASON_END_TURN
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="done"),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_END_TURN),
        ]
        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
        state.sessions.record_success = MagicMock()

        await _run_chat(state, session, "hello")

        state.sessions.record_success.assert_called_once()
        state.consolidator.maybe_consolidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_stop_reason_cancelled_flushes_partial_text(self, tmp_path, monkeypatch):
        """Partial text chunks before cancel must be flushed to the session."""
        from personalclaw.acp.types import STOP_REASON_CANCELLED
        from personalclaw.dashboard.chat import _run_chat
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        events = [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="partial output here"),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED),
        ]
        state = self._make_state_for_run_chat(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        client = self._make_mock_client(events)
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        await _run_chat(state, session, "hello")

        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert any("partial output here" in m["content"] for m in assistant_msgs)


# ── Phase 5: Soft-stop dashboard backend tests ──


class TestStopTurnSessionState:
    """Tests for api_chat_session_stop soft/hard state transitions."""

    def _make_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        sessions = MagicMock(count=0)
        sessions.stop_turn = AsyncMock(return_value="soft")
        sessions.reset = AsyncMock()
        sessions.get_pid = MagicMock(return_value=None)
        return DashboardState(
            sessions=sessions,
            crons=MagicMock(
                list_jobs=MagicMock(return_value=[]),
                status=MagicMock(return_value={}),
            ),
            lessons=MagicMock(load_all=MagicMock(return_value=[])),
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path),
        )

    @pytest.mark.asyncio
    async def test_stop_turn_session_state_transitions_soft(self, tmp_path, monkeypatch):
        """POST stop → idle→soft_pending; after on_soft → idle."""
        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))

        captured_states: list[str] = []

        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            captured_states.append(session._stop_state)
            if on_soft:
                await on_soft()
            return "soft"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200

        assert captured_states == ["soft_pending"]
        assert session._stop_state == "idle"
        session.task.cancel()

    @pytest.mark.asyncio
    async def test_stop_turn_session_state_transitions_hard(self, tmp_path, monkeypatch):
        """POST stop with hard outcome → idle→soft_pending→idle after on_hard."""
        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))

        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            if on_hard:
                await on_hard()
            return "hard"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200

        assert session._stop_state == "idle"
        session.task.cancel()

    @pytest.mark.asyncio
    async def test_stop_turn_force_query_param(self, tmp_path, monkeypatch):
        """POST stop?force=true when soft_pending → skips cancel, hard kill."""
        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))
        session._stop_state = "soft_pending"

        force_called = []

        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            force_called.append(force)
            if on_hard:
                await on_hard()
            return "hard"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/stop?force=true")
            assert resp.status == 200

        assert force_called == [True]
        assert session._stop_state == "idle"
        session.task.cancel()

    @pytest.mark.asyncio
    async def test_stop_turn_first_press_clears_queue(self, tmp_path, monkeypatch):
        """Queue populated; POST stop; queue empty (via stop_turn side effect)."""
        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))
        session._queue.extend(["msg1", "msg2"])

        # stop_turn clears queue internally; verify session._queue is cleared
        # by the time stop_turn is called (api_chat_session_stop sets state
        # before calling stop_turn, and stop_turn calls clear_queue)
        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            if on_soft:
                await on_soft()
            return "soft"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200
        assert len(session._queue) == 0
        session.task.cancel()

    @pytest.mark.asyncio
    async def test_stop_event_appears_in_transcript(self, tmp_path, monkeypatch):
        """After stop, session messages contain a stop_event entry."""
        import json

        def _is_stop_event(m: dict) -> bool:
            cls = m.get("cls", "")
            if not isinstance(cls, str) or not cls.startswith("{"):
                return False
            try:
                return json.loads(cls).get("kind") == "stop_event"
            except (json.JSONDecodeError, TypeError):
                return False

        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))

        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            if on_soft:
                await on_soft()
            return "soft"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/sessions/s1/stop")
            assert resp.status == 200

        stop_msgs = [m for m in session.messages if _is_stop_event(m)]
        assert len(stop_msgs) == 1
        data = json.loads(stop_msgs[0]["content"])
        assert data["kind"] == "stop_event"
        assert data["state"] == "stopped"
        assert data["outcome"] == "soft"
        session.task.cancel()

    @pytest.mark.asyncio
    async def test_stop_event_replace_in_place(self, tmp_path, monkeypatch):
        """Stop event has stable id across state transitions (one entry)."""
        import json

        def _is_stop_event(m: dict) -> bool:
            cls = m.get("cls", "")
            if not isinstance(cls, str) or not cls.startswith("{"):
                return False
            try:
                return json.loads(cls).get("kind") == "stop_event"
            except (json.JSONDecodeError, TypeError):
                return False

        state = self._make_state(tmp_path, monkeypatch)
        session = state.get_or_create_session("s1")
        session.task = asyncio.ensure_future(asyncio.sleep(999))

        async def fake_stop_turn(key, *, force=False, on_soft=None, on_hard=None):
            # Verify the stop_event was inserted before callbacks
            stop_msgs = [m for m in session.messages if _is_stop_event(m)]
            assert len(stop_msgs) == 1
            pre_data = json.loads(stop_msgs[0]["content"])
            assert pre_data["state"] == "stopping"
            if on_soft:
                await on_soft()
            return "soft"

        state.sessions.stop_turn = fake_stop_turn

        app = _make_app(state)
        async with TestClient(TestServer(app)) as client:
            await client.post("/api/chat/sessions/s1/stop")

        # Still only one stop_event message
        stop_msgs = [m for m in session.messages if _is_stop_event(m)]
        assert len(stop_msgs) == 1
        data = json.loads(stop_msgs[0]["content"])
        assert data["state"] == "stopped"
        session.task.cancel()


class TestStopHistoryBanner:
    """Tests for history re-injection banner skip on soft stop."""

    @staticmethod
    def _last_stop_soft(session: _ChatSession) -> bool:
        """Replicates the detection logic in chat.py:_run_chat."""
        import json

        for m in reversed(session.messages):
            cls_val = m.get("cls", "")
            if not isinstance(cls_val, str) or not cls_val.startswith("{"):
                continue
            try:
                _cls = json.loads(cls_val)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(_cls, dict) or _cls.get("kind") != "stop_event":
                continue
            return _cls.get("outcome") == "soft"
        return False

    def test_soft_stop_preserves_session_no_history_banner(self):
        """After a soft stop, _build_history_prefix is skipped."""
        import json

        session = _ChatSession("s1")
        session.append("user", "hello")
        session.append("assistant", "hi there")
        # cls must be a JSON-encoded dict (same format api_chat_session_stop uses)
        cls_json = json.dumps(
            {
                "kind": "stop_event",
                "id": "stop-abc",
                "state": "stopped",
                "outcome": "soft",
            }
        )
        session.append("system", cls_json, cls_json)
        assert self._last_stop_soft(session) is True

    def test_hard_stop_still_injects_history_banner(self):
        """After a hard stop, the banner detection returns False."""
        import json

        session = _ChatSession("s1")
        session.append("user", "hello")
        session.append("assistant", "hi there")
        cls_json = json.dumps(
            {
                "kind": "stop_event",
                "id": "stop-abc",
                "state": "stop_failed_reset",
                "outcome": "hard",
            }
        )
        session.append("system", cls_json, cls_json)
        assert self._last_stop_soft(session) is False

    def test_plain_string_cls_does_not_match(self):
        """Plain-string cls (legacy format) is ignored — no false positive."""
        session = _ChatSession("s1")
        session.append("user", "hello")
        session.append("system", "{}", "stop_event")  # plain string cls
        assert self._last_stop_soft(session) is False


# ── Tests: AcpProcessDied handler in _run_chat ──


class TestAcpProcessDiedRecovery:
    """Verify _run_chat handles AcpProcessDied with retry logic, redaction, and session reset."""

    def _make_state_and_session(self, tmp_path):
        from personalclaw.dashboard.chat_runner import _run_chat

        state = _make_state(tmp_path)
        state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), False, False))
        state.sessions.release = MagicMock()
        state.sessions.reset = AsyncMock()
        state.sessions.set_approval_policy = MagicMock()
        state.sessions.check_context_usage = MagicMock()
        state.sessions.get_channel_link = MagicMock(return_value=(None, None))
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.is_yolo_active = MagicMock(return_value=False)
        state._background_tasks = set()

        session = state.get_or_create_session("pipe-death-session")
        session.append("user", "hello", "msg msg-u")

        mock_client = state.sessions.get_or_create.return_value[0]
        mock_client.shutdown = AsyncMock()
        return state, session, mock_client, _run_chat

    def _make_stream_raise(self, mock_client, exc):
        async def _raise(msg):
            raise exc
            yield  # noqa: E501

        mock_client.stream = _raise
        mock_client.stream_command = _raise

    @pytest.mark.asyncio
    async def test_retry_at_depth_0_requeues_message(self, tmp_path: Path) -> None:
        """First pipe death at depth 0 → message re-queued, retrying shown."""
        from personalclaw.acp.client import AcpProcessDied

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)
        self._make_stream_raise(client, AcpProcessDied("pipe broken"))

        await _run_chat(state, session, "test message")

        state.sessions.reset.assert_awaited_once()
        assert session._acp_pipe_death_retries == 1
        error_msgs = [m for m in session.messages if m.get("role") == "error"]
        assert any("retrying" in m.get("content", "") for m in error_msgs)

    @pytest.mark.asyncio
    async def test_budget_exhaustion_shows_stuck(self, tmp_path: Path) -> None:
        """4th pipe death → 'Session stuck' shown, no re-queue."""
        from personalclaw.acp.client import AcpProcessDied

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)
        session._acp_pipe_death_retries = 3  # already exhausted
        self._make_stream_raise(client, AcpProcessDied("pipe broken"))

        await _run_chat(state, session, "test message")

        assert session._acp_pipe_death_retries == 4
        error_msgs = [m for m in session.messages if m.get("role") == "error"]
        assert any("stuck" in m.get("content", "").lower() for m in error_msgs)

    @pytest.mark.asyncio
    async def test_nested_depth_shows_please_retry(self, tmp_path: Path) -> None:
        """Pipe death at depth > 0 → 'please retry' shown, no re-queue."""
        from personalclaw.acp.client import AcpProcessDied

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)
        self._make_stream_raise(client, AcpProcessDied("pipe broken"))

        await _run_chat(state, session, "test message", _prompt_depth=1)

        assert session._acp_pipe_death_retries == 1
        error_msgs = [m for m in session.messages if m.get("role") == "error"]
        assert any("please retry" in m.get("content", "").lower() for m in error_msgs)

    @pytest.mark.asyncio
    async def test_partial_assistant_text_redacted(self, tmp_path: Path) -> None:
        """Pipe death mid-stream → partial output redacted before display."""
        from personalclaw.acp.client import AcpProcessDied
        from personalclaw.llm.base import EVENT_TEXT_CHUNK, LLMEvent

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)

        async def _stream_then_die(msg):
            yield LLMEvent(
                kind=EVENT_TEXT_CHUNK, text="partial output with AKIA1234567890ABCDEF secret"
            )
            raise AcpProcessDied("pipe broken")

        client.stream = _stream_then_die
        client.stream_command = _stream_then_die

        await _run_chat(state, session, "test message")

        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert assistant_msgs, "Expected at least one assistant message with redacted content"
        for m in assistant_msgs:
            assert "AKIA1234567890ABCDEF" not in m.get("content", "")

    @pytest.mark.asyncio
    async def test_session_reset_propagated(self, tmp_path: Path) -> None:
        """Verify the finally block resets the session after AcpProcessDied."""
        from personalclaw.acp.client import AcpProcessDied

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)
        self._make_stream_raise(client, AcpProcessDied("pipe broken"))

        await _run_chat(state, session, "test message")

        state.sessions.reset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_redacts_partial_text(self, tmp_path: Path) -> None:
        """CancelledError mid-stream → partial output redacted before display."""
        from personalclaw.llm.base import EVENT_TEXT_CHUNK, LLMEvent

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)

        async def _stream_then_cancel(msg):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="partial with AKIA1234567890ABCDEF key")
            raise asyncio.CancelledError()

        client.stream = _stream_then_cancel
        client.stream_command = _stream_then_cancel

        await _run_chat(state, session, "test message")

        assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
        assert assistant_msgs, "Expected at least one assistant message with redacted content"
        for m in assistant_msgs:
            assert "AKIA1234567890ABCDEF" not in m.get("content", "")

    @pytest.mark.asyncio
    async def test_retry_requeues_via_queue_insert(self, tmp_path: Path) -> None:
        """First pipe death at depth 0 → queue_insert is called."""
        from unittest.mock import patch as _patch

        from personalclaw.acp.client import AcpProcessDied
        from personalclaw.dashboard.state import _ChatSession

        state, session, client, _run_chat = self._make_state_and_session(tmp_path)
        self._make_stream_raise(client, AcpProcessDied("pipe broken"))

        calls = []
        orig = _ChatSession.queue_insert

        def spy(self_session, *a, **kw):
            calls.append(a)
            return orig(self_session, *a, **kw)

        with _patch.object(_ChatSession, "queue_insert", spy):
            await _run_chat(state, session, "test message")

        assert (0, "test message") in calls
