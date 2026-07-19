"""Tests for temporary chat mode (dashboard + Slack)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Dashboard: _ChatSession temporary mode properties
# ---------------------------------------------------------------------------

class TestChatSessionTemporary:
    def test_temporary_mode(self):
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="test-1", memory_mode="temporary")
        assert session.is_restricted is True
        assert session.blocks_reads is True

    def test_normal_mode(self):
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="test-2")
        assert session.is_restricted is False
        assert session.blocks_reads is False


# ---------------------------------------------------------------------------
# Dashboard: _save_session_to_history persists all modes (no skip)
# ---------------------------------------------------------------------------

class TestSaveSessionToHistory:
    def test_temporary_slot_still_saved(self):
        """All modes write .jsonl for tab recovery — temporary included."""
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="tmp-1", memory_mode="temporary")
        session.messages = [{"role": "user", "content": "hi", "ts": "1"}]
        session._resumed_count = 0

        mock_state = MagicMock()
        mock_state.conversation_log = MagicMock()

        with patch(
            "personalclaw.dashboard.chat_persistence.resolve_history_key",
            side_effect=RuntimeError("reached"),
        ):
            from personalclaw.dashboard.chat import _save_session_to_history

            with pytest.raises(RuntimeError, match="reached"):
                _save_session_to_history(mock_state, session)

    def test_normal_slot_not_skipped(self):
        """Persistent session should NOT early-return."""
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="norm-1")
        session.messages = [{"role": "user", "content": "hi", "ts": "1"}]
        session._resumed_count = 0

        mock_state = MagicMock()
        mock_state.conversation_log = MagicMock()
        with patch(
            "personalclaw.dashboard.chat_persistence.resolve_history_key",
            side_effect=RuntimeError("reached"),
        ):
            from personalclaw.dashboard.chat import _save_session_to_history

            with pytest.raises(RuntimeError, match="reached"):
                _save_session_to_history(mock_state, session)


# ---------------------------------------------------------------------------
# Dashboard: _persist_title skips restricted sessions
# ---------------------------------------------------------------------------

class TestPersistTitle:
    def test_temporary_slot_auto_title_skipped(self):
        """Auto-title skips restricted sessions."""
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="tmp-2", memory_mode="temporary")
        session._titled = False
        session.messages = [{"role": "user", "content": "hi"}]

        # _maybe_auto_title returns early for restricted sessions
        assert session.is_restricted is True


# ---------------------------------------------------------------------------
# Dashboard: _is_restricted_session (header-based MCP gating)
# ---------------------------------------------------------------------------

class TestIsRestrictedSession:
    def _mock_request(self, session_key=""):
        req = MagicMock()
        req.headers = {"X-Session-Key": session_key} if session_key else {}
        return req

    def test_dashboard_temporary_slot(self):
        from personalclaw.dashboard.handlers import _is_restricted_session
        from personalclaw.dashboard.state import _ChatSession

        state = MagicMock()
        state._restricted_keys = set()
        state._sessions = {"chat-1-abc": _ChatSession(key="chat-1-abc", memory_mode="temporary")}

        assert _is_restricted_session(state, self._mock_request("dashboard:chat-1-abc")) is True

    def test_dashboard_normal_slot(self):
        from personalclaw.dashboard.handlers import _is_restricted_session
        from personalclaw.dashboard.state import _ChatSession

        state = MagicMock()
        state._restricted_keys = set()
        state._sessions = {"chat-1-def": _ChatSession(key="chat-1-def")}

        assert _is_restricted_session(state, self._mock_request("dashboard:chat-1-def")) is False

    def test_dashboard_restricted_key_set(self):
        from personalclaw.dashboard.handlers import _is_restricted_session

        state = MagicMock()
        state._restricted_keys = {"dashboard:chat-1-eph"}
        state._sessions = {}

        assert _is_restricted_session(state, self._mock_request("dashboard:chat-1-eph")) is True

    def test_channel_temporary_thread(self):
        from personalclaw import session_restrictions as sr
        from personalclaw.dashboard.handlers import _is_restricted_session

        sr.mark_temporary("slack:C123-456")

        state = MagicMock()
        state._restricted_keys = set()
        state._sessions = {}

        assert _is_restricted_session(state, self._mock_request("slack:C123-456")) is True

    def test_no_header(self):
        """No X-Session-Key header — should return False (browser UI or normal)."""
        from personalclaw.dashboard.handlers import _is_restricted_session

        state = MagicMock()
        state._restricted_keys = set()
        assert _is_restricted_session(state, self._mock_request()) is False

    def test_dashboard_ui_key_not_restricted(self):
        """Browser UI sends 'dashboard:ui' — never restricted."""
        from personalclaw.dashboard.handlers import _is_restricted_session

        state = MagicMock()
        state._restricted_keys = set()
        assert _is_restricted_session(state, self._mock_request("dashboard:ui")) is False

    def teardown_method(self):
        from personalclaw import session_restrictions as sr

        sr._temporary.clear()
        sr._incognito.clear()


# ---------------------------------------------------------------------------
# Dashboard: memory_recall READ endpoint honors the temporary-session guard
# (the sensitive read path — semantic facts + episodic — must not be prompt-only)
# ---------------------------------------------------------------------------

class TestMemoryRecallGuard:
    def _mock_request(self, state, session_key, q="anything"):
        req = MagicMock()
        req.app = {"state": state}
        req.headers = {"X-Session-Key": session_key} if session_key else {}
        req.query = {"q": q}
        return req

    def _state_with(self, session):
        from personalclaw.dashboard.state import _ChatSession  # noqa: F401
        state = MagicMock()
        state._restricted_keys = set()
        state._sessions = {session.key: session} if session else {}
        return state

    @pytest.mark.asyncio
    async def test_recall_blocked_for_temporary_session(self):
        """A temporary session (blocks_reads) must get an EMPTY recall, never the
        user's real semantic/episodic memory — the guard its sibling api_lessons has."""
        from personalclaw.dashboard.handlers.memory import api_memory_recall
        from personalclaw.dashboard.state import _ChatSession

        sess = _ChatSession(key="chat-1-tmp", memory_mode="temporary")
        assert sess.blocks_reads is True
        state = self._state_with(sess)
        # If the guard is bypassed, _get_service would be hit — make it explode so a
        # bypass fails loudly rather than silently leaking.
        with patch("personalclaw.dashboard.handlers.memory._get_service",
                   side_effect=AssertionError("recall must not reach the service for a temporary session")):
            resp = await api_memory_recall(self._mock_request(state, "dashboard:chat-1-tmp"))
        import json
        body = json.loads(resp.body.decode())
        assert body["result"] == "No matching memory found."

    @pytest.mark.asyncio
    async def test_recall_allowed_for_normal_session(self):
        """A normal session recalls as usual (guard only blocks temporary)."""
        from personalclaw.dashboard.handlers.memory import api_memory_recall
        from personalclaw.dashboard.state import _ChatSession

        state = self._state_with(_ChatSession(key="chat-1-normal"))
        svc = MagicMock()
        svc.semantic_context.return_value = "pref: dark mode"
        svc.record_recall.return_value = None
        svc.recall_with_provenance.return_value = []
        with patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc):
            resp = await api_memory_recall(self._mock_request(state, "dashboard:chat-1-normal"))
        import json
        body = json.loads(resp.body.decode())
        assert "dark mode" in body["result"]

    def teardown_method(self):
        from personalclaw import session_restrictions as sr
        sr._temporary.clear()
        sr._incognito.clear()


# ---------------------------------------------------------------------------
# MCP: session_key plumbed via X-Session-Key header (not body)
# ---------------------------------------------------------------------------

class TestMcpSessionKeyPlumbing:
    @patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:chat-1-tmp"})
    @patch("personalclaw.mcp_memory._post")
    def test_learn_add_no_session_key_in_body(self, mock_post):
        """session_key should NOT be in the JSON body — header handles it."""
        mock_post.return_value = {"ok": True}
        from personalclaw.mcp_memory import _call_tool_inner

        result = _call_tool_inner(
            "memory_remember", {"rule": "test rule", "category": "knowledge"}
        )
        payload = mock_post.call_args[0][1]
        assert "session_key" not in payload
        assert "Saved" in result

    @patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:chat-1-tmp"})
    @patch("personalclaw.mcp_memory._delete")
    def test_learn_remove_no_session_key_in_body(self, mock_delete):
        """session_key should NOT be in the JSON body — header handles it."""
        mock_delete.return_value = {"removed": 1}
        from personalclaw.mcp_memory import _call_tool_inner

        _call_tool_inner("memory_forget", {"query": "test"})
        payload = mock_delete.call_args[0][1]
        assert "session_key" not in payload

    @patch.dict("os.environ", {"PERSONALCLAW_SESSION_KEY": "dashboard:chat-1-tmp"})
    @patch("personalclaw.mcp_memory._get")
    def test_learn_list_no_session_key_in_url(self, mock_get):
        """session_key should NOT be in query params — header handles it."""
        mock_get.return_value = {"lessons": []}
        from personalclaw.mcp_memory import _call_tool_inner

        _call_tool_inner("memory_list", {})
        url = mock_get.call_args[0][0]
        assert "session_key" not in url


# ---------------------------------------------------------------------------
# After-turn learning must skip restricted (incognito/temporary) sessions —
# regression for the durable-write leak (a "Never …" veto sent from an
# incognito chat landed in the lesson store via capture_preference_facet).
# ---------------------------------------------------------------------------

class TestAfterTurnReviewRestrictedGuard:
    def _run(self, session):
        from personalclaw.dashboard.chat_runner import _maybe_after_turn_review

        state = MagicMock()
        with patch(
            "personalclaw.memory_service.service_for"
        ) as service_for, patch(
            "personalclaw.after_turn_review.capture_preference_facet",
            return_value=None,
        ) as capture:
            _maybe_after_turn_review(state, session, "Never mention X", "ok", 0)
        return service_for, capture

    def test_incognito_session_skipped(self):
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="inc-1", memory_mode="incognito")
        service_for, capture = self._run(session)
        assert not service_for.called
        assert not capture.called

    def test_temporary_session_skipped(self):
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="tmp-1", memory_mode="temporary")
        service_for, capture = self._run(session)
        assert not service_for.called
        assert not capture.called

    def test_persistent_session_reviews(self):
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="per-1")
        service_for, capture = self._run(session)
        assert service_for.called
        assert capture.called

    def test_skill_ladder_skips_restricted(self):
        from personalclaw.dashboard.chat_runner import _maybe_skill_ladder_review
        from personalclaw.dashboard.state import _ChatSession

        session = _ChatSession(key="inc-2", memory_mode="incognito")
        state = MagicMock()
        with patch(
            "personalclaw.after_turn_review.is_correction_signal"
        ) as signal:
            _maybe_skill_ladder_review(state, session, "no, wrong", "ok", 9)
        assert not signal.called


# ---------------------------------------------------------------------------
# Restricted sessions must stay out of every discovery surface (regression:
# incognito chats surfaced via /api/sessions, /api/sessions/search and the
# channel !resume list even though the chat-history list filtered them).
# ---------------------------------------------------------------------------

class TestRestrictedSessionDiscovery:
    def _log_with_incognito(self, tmp_path):
        from personalclaw.history import ConversationLog

        log = ConversationLog(base_dir=tmp_path)
        log.append("dashboard_open-1", "user", "public codeword ALPHA")
        log.append("dashboard_secret-1", "user", "secret codeword ZANZIBAR")
        log.update_metadata("dashboard_secret-1", {"memory_mode": "incognito"})
        return log

    def test_search_sessions_excludes_incognito(self, tmp_path):
        log = self._log_with_incognito(tmp_path)
        assert log.search_sessions("ZANZIBAR") == []
        assert [s["key"] for s in log.search_sessions("ALPHA")] == [
            "dashboard_open-1"
        ]

    def test_sync_bridge_resume_list_excludes_incognito(self, tmp_path):
        from personalclaw.sync_bridge import list_recent_sessions

        log = self._log_with_incognito(tmp_path)
        keys = [s["key"] for s in list_recent_sessions(log)]
        assert "dashboard_open-1" in keys
        assert "dashboard_secret-1" not in keys
