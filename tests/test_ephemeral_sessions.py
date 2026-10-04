"""Tests for incognito/temporary session support.

Non-persistent sessions disable memory consolidation. An incognito chat keeps its
conversation log for tab recovery and gateway restart; a temporary chat keeps it only while
its session runs (tests/test_a_temporary_chat_is_forgotten_when_its_session_ends.py).
"""

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import signed_in

from personalclaw import memory_writes
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog

# ── Helpers ──


def _make_state(tmp_path, **kwargs):
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        **kwargs,
    )


def _make_app(state):
    from personalclaw.dashboard.chat import (
        api_chat_session_create,
        api_chat_session_delete,
        api_chat_session_resume,
        api_chat_sessions,
    )
    from personalclaw.dashboard.handlers import api_lessons_create

    # The owner's signed-in page: the session a request names in X-Session-Key is its work only
    # when a sign-in stands behind it (approval_answer.work_of_request).
    app = web.Application(middlewares=[signed_in])
    app["state"] = state
    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    app.router.add_post("/api/chat/sessions", api_chat_session_create)
    app.router.add_delete("/api/chat/sessions/{session}", api_chat_session_delete)
    app.router.add_post("/api/chat/sessions/{session}/resume", api_chat_session_resume)
    app.router.add_post("/api/lessons", api_lessons_create)
    return app


def _write_session(log, key, messages, *, memory_mode="persistent"):
    """Write a JSONL session file with optional memory_mode metadata."""
    path = log._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"_type": "metadata", "created_at": "2026-01-01T00:00:00"}
    if memory_mode != "persistent":
        meta["memory_mode"] = memory_mode
    lines = [_json.dumps(meta)]
    for role, content in messages:
        lines.append(_json.dumps({"role": role, "content": content, "ts": "2026-01-01T00:00:01"}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Session model tests ──


class TestSessionMemoryMode:
    def test_default_persistent(self):
        session = _ChatSession("s1")
        assert session.memory_mode == "persistent"
        assert not session.is_restricted
        assert not session.blocks_reads

    def test_incognito(self):
        session = _ChatSession("s1", memory_mode="incognito")
        assert session.memory_mode == "incognito"
        assert session.is_restricted
        assert not session.blocks_reads

    def test_temporary(self):
        session = _ChatSession("s1", memory_mode="temporary")
        assert session.memory_mode == "temporary"
        assert session.is_restricted
        assert session.blocks_reads

    def test_to_dict_includes_memory_mode(self):
        session = _ChatSession("s1", memory_mode="incognito")
        d = session.to_dict()
        assert d["memory_mode"] == "incognito"

    def test_to_dict_persistent(self):
        session = _ChatSession("s1")
        d = session.to_dict()
        assert d["memory_mode"] == "persistent"


class TestSessionCreation:
    def test_get_or_create_session_incognito(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("e1", memory_mode="incognito")
        assert session.memory_mode == "incognito"
        assert memory_writes.blocks_memory_writes("dashboard:e1", state=state)

    def test_get_or_create_session_temporary(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("t1", memory_mode="temporary")
        assert session.memory_mode == "temporary"
        assert memory_writes.blocks_memory_writes("dashboard:t1", state=state)

    def test_get_or_create_session_persistent(self, tmp_path):
        state = _make_state(tmp_path)
        session = state.get_or_create_session("n1")
        assert session.memory_mode == "persistent"
        assert not memory_writes.blocks_memory_writes("dashboard:n1", state=state)

    @pytest.mark.asyncio
    async def test_restricted_mode_cleaned_on_session_delete(self, tmp_path, monkeypatch):
        """A deleted Incognito chat leaves nothing that restricts a new chat of the same name."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("reuse", memory_mode="incognito")
        assert memory_writes.blocks_memory_writes("dashboard:reuse", state=state)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.delete("/api/chat/sessions/reuse")
            assert resp.status == 200

        session = state.get_or_create_session("reuse")
        assert session.memory_mode == "persistent"
        assert not memory_writes.blocks_memory_writes("dashboard:reuse", state=state)

    def test_get_or_create_session_memory_mode_mismatch_raises(self, tmp_path):
        state = _make_state(tmp_path)
        state.get_or_create_session("x")
        with pytest.raises(ValueError, match="memory_mode="):
            state.get_or_create_session("x", memory_mode="incognito")


# ── Conversation log persistence ──


class TestHistoryPersistence:
    def test_restricted_session_still_saves_conversation_log(self, tmp_path, monkeypatch):
        """All memory modes write conversation log for tab recovery."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import save_session_to_history

        state = _make_state(tmp_path)
        session = state.get_or_create_session("e1", memory_mode="temporary")
        session.append("user", "secret tax info")
        session.append("assistant", "noted")

        save_session_to_history(state, session)

        msgs = state.conversation_log.read_messages("dashboard:e1")
        assert len(msgs) == 2

    def test_restricted_metadata_flag_persisted(self, tmp_path, monkeypatch):
        """Conversation log metadata includes memory_mode for restricted sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import save_session_to_history

        state = _make_state(tmp_path)
        session = state.get_or_create_session("e1", memory_mode="incognito")
        session.append("user", "hello")

        save_session_to_history(state, session)

        meta = state.conversation_log.get_metadata("dashboard:e1")
        assert meta.get("memory_mode") == "incognito"

    def test_persistent_session_no_memory_mode_metadata(self, tmp_path, monkeypatch):
        """Persistent sessions don't have memory_mode in metadata."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import save_session_to_history

        state = _make_state(tmp_path)
        session = state.get_or_create_session("n1")
        session.append("user", "hello")

        save_session_to_history(state, session)

        meta = state.conversation_log.get_metadata("dashboard:n1")
        assert "memory_mode" not in meta or meta.get("memory_mode") == "persistent"


# ── Restore on gateway restart ──


class TestRestore:
    def test_restore_rebuilds_memory_mode(self, tmp_path, monkeypatch):
        """Gateway restart restores restricted sessions with memory_mode intact."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import restore_recent_sessions, save_session_to_history

        state1 = _make_state(tmp_path)
        session = state1.get_or_create_session("e1", memory_mode="incognito")
        session.append("user", "private stuff")
        session.append("assistant", "ok")
        save_session_to_history(state1, session)

        state2 = _make_state(tmp_path)
        restored = restore_recent_sessions(state2, window_minutes=0)

        assert restored >= 1
        assert "e1" in state2._sessions
        assert state2._sessions["e1"].memory_mode == "incognito"
        assert memory_writes.blocks_memory_writes("dashboard:e1", state=state2)


# ── User-initiated resume from History tab ──


class TestResumeFromHistory:
    """Resume endpoint (POST /api/chat/sessions/{session}/resume) must restore memory_mode.

    Regression test: prior to the fix, this path restored agent/workspace/mode/folder_id
    etc. but not memory_mode, causing reloaded incognito/temporary sessions to become
    persistent and allow memory writes.
    """

    @pytest.mark.asyncio
    async def test_resume_restores_incognito(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _write_session(state.conversation_log, "e1", [("user", "hi")], memory_mode="incognito")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/e1/resume", json={"key": "e1"})
            data = await resp.json()

        assert data["ok"] is True
        assert data["memory_mode"] == "incognito"
        assert state._sessions["e1"].memory_mode == "incognito"
        assert memory_writes.blocks_memory_writes("dashboard:e1", state=state)

    @pytest.mark.asyncio
    async def test_a_temporary_chat_whose_session_ended_is_not_resumed(self, tmp_path, monkeypatch):
        """Not resumed at all, so it can never come back as a chat that reads or writes memory:
        a temporary chat not running in this gateway has ended, and is forgotten."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _write_session(state.conversation_log, "t1", [("user", "hi")], memory_mode="temporary")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/t1/resume", json={"key": "t1"})

        assert resp.status == 404
        assert "t1" not in state._sessions
        # Nothing records it any more, so its work keeps nothing, as an ended Temporary chat's.
        assert memory_writes.blocks_memory_writes("dashboard:t1", state=state)
        assert not state.conversation_log.has_log("t1")

    @pytest.mark.asyncio
    async def test_resume_persistent_keeps_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _write_session(state.conversation_log, "p1", [("user", "hi")])

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/p1/resume", json={"key": "p1"})
            data = await resp.json()

        assert data["memory_mode"] == "persistent"
        assert state._sessions["p1"].memory_mode == "persistent"
        assert not memory_writes.blocks_memory_writes("dashboard:p1", state=state)

    @pytest.mark.asyncio
    async def test_resume_missing_memory_mode_defaults_persistent(self, tmp_path, monkeypatch):
        """Legacy sessions (pre-) without memory_mode metadata default to persistent."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _write_session(state.conversation_log, "legacy", [("user", "hi")])

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/legacy/resume", json={"key": "legacy"})
            data = await resp.json()

        assert data["memory_mode"] == "persistent"

    @pytest.mark.asyncio
    async def test_learn_add_blocked_after_resume_incognito(self, tmp_path, monkeypatch):
        """Core regression: memory_remember must be blocked on a resumed incognito session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _write_session(state.conversation_log, "e1", [("user", "hi")], memory_mode="incognito")

        async with TestClient(TestServer(_make_app(state))) as client:
            await client.post("/api/chat/sessions/e1/resume", json={"key": "e1"})
            resp = await client.post(
                "/api/lessons",
                json={"rule": "secret", "category": "preference"},
                headers={"X-Session-Key": "dashboard:e1"},
            )

        assert resp.status == 403


# ── Consolidation gate ──


class TestConsolidation:
    def test_consolidation_not_triggered_for_restricted(self, tmp_path):
        """maybe_consolidate must not be called for restricted sessions."""
        from personalclaw.dashboard.chat import _maybe_consolidate

        state = _make_state(tmp_path)
        state.consolidator = MagicMock()
        session = state.get_or_create_session("e1", memory_mode="incognito")

        with patch("personalclaw.dashboard.chat_utils.sel") as mock_sel:
            _maybe_consolidate(state, session)

        state.consolidator.maybe_consolidate.assert_not_called()
        # Consolidation is the SESSION_END cadence, now routed through the LearningGate: a denial
        # is audited with the gate's specific reason (`gate:<reason>`) rather than the old bespoke
        # `restricted_session_block` string, so the audit trail is uniform with every other cadence.
        mock_sel().log_api_access.assert_called_once_with(
            caller="dashboard:e1",
            operation="consolidate",
            outcome="denied",
            source="dashboard",
            resources="gate:restricted_session",
        )

    def test_consolidation_triggered_for_persistent(self, tmp_path):
        """maybe_consolidate must be called for persistent sessions."""
        from personalclaw.dashboard.chat import _maybe_consolidate

        state = _make_state(tmp_path)
        state.consolidator = MagicMock()
        session = state.get_or_create_session("n1")

        _maybe_consolidate(state, session)

        state.consolidator.maybe_consolidate.assert_called_once()


# ── API: create session with memory_mode ──


class TestSessionAPI:
    @pytest.mark.asyncio
    async def test_create_incognito_session_via_api(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_persistence.AppConfig.load",
            MagicMock(return_value=MagicMock(agents={})),
        )
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions",
                json={"memory_mode": "incognito"},
            )
            data = await resp.json()

        assert data["memory_mode"] == "incognito"
        session_key = data["key"]
        assert state._sessions[session_key].memory_mode == "incognito"
        assert memory_writes.blocks_memory_writes(f"dashboard:{session_key}", state=state)

    @pytest.mark.asyncio
    async def test_create_persistent_session_via_api(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_persistence.AppConfig.load",
            MagicMock(return_value=MagicMock(agents={})),
        )
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions", json={})
            data = await resp.json()

        assert data["memory_mode"] == "persistent"

    @pytest.mark.asyncio
    async def test_create_session_memory_mode_mismatch_returns_409(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.chat_persistence.AppConfig.load",
            MagicMock(return_value=MagicMock(agents={})),
        )
        state = _make_state(tmp_path)
        state.get_or_create_session("conflict")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions",
                json={"name": "conflict", "memory_mode": "incognito"},
            )
            assert resp.status == 409


# ── API: lessons blocked for restricted sessions ──


class TestLessonsGate:
    @pytest.mark.asyncio
    async def test_learn_add_blocked_for_restricted_session(self, tmp_path, monkeypatch):
        """POST /api/lessons returns 403 when X-Session-Key is restricted."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("e1", memory_mode="incognito")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:e1"},
            )
            assert resp.status == 403
            data = await resp.json()
            assert "not allowed" in data["error"]

    @pytest.mark.asyncio
    async def test_learn_add_allowed_for_persistent_session(self, tmp_path, monkeypatch):
        """POST /api/lessons succeeds for persistent sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)
        state.get_or_create_session("n1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:n1"},
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_learn_add_rejected_without_session_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_learn_add_refused_for_unknown_session(self, tmp_path, monkeypatch):
        """A chat the gateway does not hold and nothing records is one whose mode nothing can
        say (a Temporary chat that has ended): its work keeps nothing, so it is refused as any
        write such work makes is."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:deleted-session"},
            )
            assert resp.status == 403
            assert (await resp.json())["error"] == memory_writes.REFUSAL

    @pytest.mark.asyncio
    async def test_learn_add_allowed_for_browser_ui_despite_restricted_session(
        self, tmp_path, monkeypatch
    ):
        """Browser Memory page sends 'dashboard:ui' — allowed even when restricted sessions exist."""  # noqa: E501
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)
        state.get_or_create_session("e1", memory_mode="incognito")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:ui"},
            )
            assert resp.status == 200


# ── MCP core: session_key passthrough ──


class TestMcpCoreSessionKeyPassthrough:
    """``PERSONALCLAW_PORT`` is set here because ``mcp_core`` now REFUSES rather than
    assuming the default port when it cannot resolve this instance's gateway (#2539) — so a
    test that wants to inspect the outgoing request must first give it a gateway to address.
    The same holds for the internal credential that gateway writes to its home: with none,
    ``mcp_core`` sends nothing.
    """

    @pytest.fixture(autouse=True)
    def _the_credential_the_gateway_wrote(self):
        home = config_loader.resolve_config_dir()
        home.mkdir(parents=True, exist_ok=True)
        (home / ".local_secret").write_text("a-gateway-credential", encoding="utf-8")

    def test_learn_add_sends_session_key_header(self):
        with (
            patch("personalclaw.mcp_core.urllib.request.urlopen") as mock_urlopen,
            patch.dict(
                "os.environ",
                {"PERSONALCLAW_SESSION_KEY": "dashboard:e1", "PERSONALCLAW_PORT": "7777"},
            ),
        ):
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"error": "Incognito mode"}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            from personalclaw.mcp_core import _post

            _post("/api/lessons", {"rule": "test", "category": "knowledge"})

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-session-key") == "dashboard:e1"
        assert req.get_header("X-internal-secret") == "a-gateway-credential"

    def test_learn_add_no_session_key_header_when_unset(self):
        with (
            patch("personalclaw.mcp_core.urllib.request.urlopen") as mock_urlopen,
            patch("personalclaw.mcp_core._resolve_session_key", return_value=""),
            patch.dict("os.environ", {"PERSONALCLAW_PORT": "7777"}),
        ):
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            from personalclaw.mcp_core import _post

            _post("/api/lessons", {"rule": "test", "category": "knowledge"})

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-session-key") is None


# ── Soft gate: incognito prompt prefix (chat.py) ──


class TestSoftGatePrompt:
    # The session-mode prefix is now a bundled snippet (``session-incognito`` /
    # ``session-temporary``) rendered from PERSONALCLAW_HOME; the global autouse
    # ``_isolate_personalclaw_home`` fixture (tests/conftest.py) seeds it into a
    # throwaway home.
    def test_incognito_prefix_injected(self):
        from personalclaw.dashboard.chat import _apply_incognito_prefix

        session = _ChatSession("e1", memory_mode="incognito")
        result = _apply_incognito_prefix(session, "Hello world")
        assert result.startswith("[INCOGNITO SESSION]")
        assert "Hello world" in result

    def test_temporary_prefix_injected(self):
        from personalclaw.dashboard.chat import _apply_incognito_prefix

        session = _ChatSession("t1", memory_mode="temporary")
        result = _apply_incognito_prefix(session, "Hello world")
        assert "[TEMPORARY SESSION]" in result or "[INCOGNITO" in result
        assert "Hello world" in result

    def test_no_prefix_for_persistent_session(self):
        from personalclaw.dashboard.chat import _apply_incognito_prefix

        session = _ChatSession("n1")
        result = _apply_incognito_prefix(session, "Hello world")
        assert result == "Hello world"

    def test_prefix_injected_for_resumed_restricted(self):
        from personalclaw.dashboard.chat import _apply_incognito_prefix

        session = _ChatSession("e1", memory_mode="incognito")
        result = _apply_incognito_prefix(session, "Follow-up question")
        assert result.startswith("[INCOGNITO SESSION]")
        assert "Follow-up question" in result


# ── History file integrity ──


class TestHistoryFileIntegrity:
    """list_sessions() must surface memory_mode."""

    def test_list_sessions_includes_memory_mode(self, tmp_path):
        log = ConversationLog(base_dir=tmp_path)
        _write_session(log, "e1", [("user", "hi")], memory_mode="incognito")
        _write_session(log, "t1", [("user", "hi")], memory_mode="temporary")
        _write_session(log, "p1", [("user", "hi")])

        by_key = {s["key"]: s for s in log.list_sessions()}

        assert by_key["e1"].get("memory_mode") == "incognito"
        assert by_key["t1"].get("memory_mode") == "temporary"
        assert by_key["p1"].get("memory_mode") == "persistent"


# ── Context builder: blocks_reads skips memory ──


class TestBlocksReadsContext:
    def test_blocks_reads_skips_memory_and_lessons(self, tmp_path, monkeypatch):
        """build_session_context(blocks_reads=True) must not inject memory or lessons."""
        from personalclaw.context import ContextBuilder
        from personalclaw.memory import MemoryStore

        ws_dir = tmp_path / "workspace"
        mem_dir = ws_dir / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "preferences.md").write_text("# User Preferences\n\n- Likes pizza\n")

        mem = MemoryStore(workspace=ws_dir)
        cb = ContextBuilder(memory=mem)
        # Memory is resolved per-cwd via the static get_memory_for; pin it to the
        # test's store so the assertion targets the blocks_reads gate, not the
        # cwd→partition mapping.
        monkeypatch.setattr(
            ContextBuilder, "get_memory_for", staticmethod(lambda cwd=None, memory_store=None: mem)
        )

        ctx_normal = cb.build_session_context(
            session_key="dashboard:test-normal",
        )
        ctx_blocked = cb.build_session_context(
            session_key="dashboard:test-blocked",
            blocks_reads=True,
        )

        assert "Likes pizza" in ctx_normal
        assert "Likes pizza" not in ctx_blocked


# ── Session session recovery via persisted JSONL ──


class TestSessionRecovery:
    """memory_remember must accept keys whose session was evicted from memory but whose
    JSONL file still exists in ~/.personalclaw/sessions/ — this covers the long-lived
    Slack thread / reopened dashboard tab cases where the MCP subprocess holds a
    stale PERSONALCLAW_SESSION_KEY env var that maps to a swept session. The transcript is
    one of the records the one reader of a session's mode reads, so it says the chat keeps
    memory when the gateway no longer holds the chat.
    """

    def _write_sessions_jsonl(self, tmp_path, stem: str) -> None:
        sess_dir = tmp_path / ".personalclaw" / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        (sess_dir / f"{stem}.jsonl").write_text(
            '{"_type": "metadata", "created_at": "2026-01-01T00:00:00"}\n',
            encoding="utf-8",
        )

    @pytest.mark.asyncio
    async def test_learn_add_allowed_when_session_evicted_but_jsonl_persists(
        self, tmp_path, monkeypatch
    ):
        """Core fix: evicted session + existing JSONL → memory_remember proceeds."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)
        # Session was evicted — state._sessions is empty — but JSONL exists.
        self._write_sessions_jsonl(tmp_path, "1776000000.123456")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:1776000000.123456"},
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_learn_add_allowed_when_session_evicted_dashboard_prefix_jsonl(
        self, tmp_path, monkeypatch
    ):
        """dashboard_{stem}.jsonl fallback path from slack/interactions.py."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)
        self._write_sessions_jsonl(tmp_path, "dashboard_chat-1-1776000000")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:chat-1-1776000000"},
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_learn_add_still_refused_when_no_jsonl_exists(self, tmp_path, monkeypatch):
        """Forged/stale keys with no backing JSONL are still refused: nothing records such a
        chat, so nothing can say its mode, and its work keeps nothing."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        state = _make_state(tmp_path)
        # Create an empty sessions dir so the path-exists check is meaningful.
        (tmp_path / ".personalclaw" / "sessions").mkdir(parents=True, exist_ok=True)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": "remember this", "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:forged-key"},
            )
            assert resp.status == 403
            data = await resp.json()
            assert data["error"] == memory_writes.REFUSAL

    @pytest.mark.asyncio
    async def test_learn_add_rejects_path_traversal_in_session_name(self, tmp_path, monkeypatch):
        """Defence-in-depth: a session name with path separators or a null byte never makes the
        route read a file outside the sessions folder, even when a transcript that would let the
        write through exists at the resolved traversal target. Every reader of a session's mode
        names its transcript through ``history.session_path``, which maps any key to a file
        directly in that folder, so such a key names a chat nothing records, and is refused.
        Without the seeded targets the test would pass even if a reader followed the path."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        state = _make_state(tmp_path)
        sess_dir = tmp_path / ".personalclaw" / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)

        # Seed a transcript that records no restricted mode (one a write would pass on) at every
        # resolved traversal target, so that only never reading it refuses each request.
        # "../escape" → sess_dir/../escape.jsonl → ~/.personalclaw/escape.jsonl
        (tmp_path / ".personalclaw" / "escape.jsonl").write_text("{}\n")
        # "a/b" → sess_dir/a/b.jsonl
        sub = sess_dir / "a"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "b.jsonl").write_text("{}\n")
        # "a\\b" (Windows path separator) → nominally sess_dir/a\b.jsonl.
        # Create a literal single-filename entry with an embedded backslash
        # so that, on Linux, a JSONL with that exact name exists — proving
        # the reader never names it, independent of platform behaviour.
        (sess_dir / "a\\b.jsonl").write_text("{}\n")

        async with TestClient(TestServer(_make_app(state))) as client:
            # These keys must be *refused* — by the route (403: no record of the chat) or by
            # aiohttp's own header validation before the request ever reaches the server
            # (ValueError on newline / CR / null byte). Both outcomes prove the traversal
            # attempt is blocked end-to-end.
            for bad_key in (
                "dashboard:../escape",
                "dashboard:a/b",
                "dashboard:a\\b",
            ):
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "x", "category": "knowledge"},
                    headers={"X-Session-Key": bad_key},
                )
                assert resp.status == 403, f"path-traversal attempt passed: {bad_key!r}"

            # Null byte: blocked at transport level. Older aiohttp raises
            # ValueError client-side; newer versions reject at the HTTP parser
            # (ServerDisconnectedError or similar). Either way, the request
            # cannot reach the handler — verify it doesn't succeed.
            import aiohttp

            try:
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "x", "category": "knowledge"},
                    headers={"X-Session-Key": "dashboard:bad\x00key"},
                )
                assert resp.status == 403, "null byte header reached handler"
            except (ValueError, aiohttp.ServerDisconnectedError, aiohttp.ClientConnectionError):
                pass  # transport-level rejection — acceptable

    # ── Audit events on positive-match paths (security-controls rule) ──

    @pytest.mark.asyncio
    async def test_learn_add_audits_live_session_allow_path(self, tmp_path, monkeypatch):
        """Live in-memory session → audit event naming the chat the lesson is filed under."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)
        # Seed a live session so in_sessions=True on the guard.
        state.get_or_create_session("live1")

        with patch("personalclaw.dashboard.handlers.schedule._sel") as mock_sel:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "remember this", "category": "knowledge"},
                    headers={"X-Session-Key": "dashboard:live1"},
                )
                assert resp.status == 200

        mock_sel().log_api_access.assert_any_call(
            caller="dashboard:live1",
            operation="memory_remember",
            outcome="allowed",
            source="dashboard",
            resources="dashboard:live1",
        )

    @pytest.mark.asyncio
    async def test_learn_add_audits_restricted_session_deny_path(self, tmp_path, monkeypatch):
        """A restricted session's lesson → one decision, audited as a denial
        ('restricted_session_block', with the reason the 403 gives), and no allow row: the
        security-controls rule asks that every permission decision emits a SEL event, and the
        route makes this one exactly once.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("r1", memory_mode="incognito")

        with patch("personalclaw.dashboard.handlers.schedule._sel") as mock_sel:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "x", "category": "knowledge"},
                    headers={"X-Session-Key": "dashboard:r1"},
                )
                assert resp.status == 403

        mock_sel().log_api_access.assert_any_call(
            caller="dashboard:r1",
            operation="memory_remember",
            outcome="denied",
            source="dashboard",
            resources="restricted_session_block",
            error=memory_writes.REFUSAL,
        )
        outcomes = [c.kwargs.get("outcome") for c in mock_sel().log_api_access.call_args_list]
        assert "allowed" not in outcomes

    @pytest.mark.asyncio
    async def test_learn_add_audits_channel_namespace_allow_path(self, tmp_path, monkeypatch):
        """Key in the ``channel:`` namespace → audit event naming the thread the lesson is filed
        under."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)

        with patch("personalclaw.dashboard.handlers.schedule._sel") as mock_sel:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "x", "category": "knowledge"},
                    headers={"X-Session-Key": "channel:C123:1777000000.000000"},
                )
                assert resp.status == 200

        mock_sel().log_api_access.assert_any_call(
            caller="channel:C123:1777000000.000000",
            operation="memory_remember",
            outcome="allowed",
            source="dashboard",
            resources="channel:C123:1777000000.000000",
        )

    @pytest.mark.asyncio
    async def test_learn_add_audits_dashboard_ui_allow_path(self, tmp_path, monkeypatch):
        """Browser UI's static ``dashboard:ui`` key → audit event with
        resources='dashboard_ui'. This key bypasses the session-scope block
        entirely; the allow decision still needs its own SEL event.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers._get_memory",
            MagicMock(return_value=MagicMock(vector_store=None)),
        )
        state = _make_state(tmp_path)

        with patch("personalclaw.dashboard.handlers.schedule._sel") as mock_sel:
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/lessons",
                    json={"rule": "x", "category": "knowledge"},
                    headers={"X-Session-Key": "dashboard:ui"},
                )
                assert resp.status == 200

        mock_sel().log_api_access.assert_any_call(
            caller="dashboard:ui",
            operation="memory_remember",
            outcome="allowed",
            source="dashboard",
            resources="dashboard_ui",
        )


class TestRestrictedListExclusion:
    """Live (in-memory) restricted sessions must be excluded from the chat
    list exactly like disk-only ones — regression for the gap where an
    incognito session appeared in /api/chat/sessions until gateway restart."""

    @pytest.mark.asyncio
    async def test_live_incognito_session_hidden_from_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.get_or_create_session("spy-1", memory_mode="incognito")
        state.get_or_create_session("tmp-1", memory_mode="temporary")
        state.get_or_create_session("pub-1")
        async with TestClient(TestServer(_make_app(state))) as client:
            data = await (await client.get("/api/chat/sessions")).json()
            keys = {s["key"] for s in data}
            assert "pub-1" in keys
            assert "spy-1" not in keys
            assert "tmp-1" not in keys
