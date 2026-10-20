"""Tests for _remove_session_for_history_key in handlers.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers import _remove_session_for_history_key


def _make_state(sessions: dict) -> MagicMock:
    state = MagicMock()
    state._sessions = dict(sessions)
    state.push_sessions_update = MagicMock()
    state.sessions = MagicMock()
    state.sessions.destroy = AsyncMock()
    return state


def _make_session(key: str, running: bool = False) -> MagicMock:
    session = MagicMock()
    session.key = key
    session.running = running
    if running:

        async def _hang():
            await asyncio.sleep(999)

        session.task = asyncio.ensure_future(_hang())
    else:
        session.task = None
    return session


class TestRemoveSessionForHistoryKey:
    @pytest.mark.asyncio
    async def test_exact_key_match(self):
        session = _make_session("dashboard_chat-1-100")
        state = _make_state({"dashboard_chat-1-100": session})
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert "dashboard_chat-1-100" not in state._sessions

    @pytest.mark.asyncio
    async def test_stripped_key_match(self):
        session = _make_session("chat-1-100")
        state = _make_state({"chat-1-100": session})
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert "chat-1-100" not in state._sessions

    @pytest.mark.asyncio
    async def test_colon_prefix_stripped(self):
        session = _make_session("chat-2-200")
        state = _make_state({"chat-2-200": session})
        await _remove_session_for_history_key(state, "dashboard:chat-2-200")
        assert "chat-2-200" not in state._sessions

    @pytest.mark.asyncio
    async def test_no_match_is_noop(self):
        state = _make_state({"chat-9-999": _make_session("chat-9-999")})
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert "chat-9-999" in state._sessions
        state.sessions.destroy.assert_not_called()

    @pytest.mark.asyncio
    async def test_running_task_cancelled(self):
        session = _make_session("dashboard_chat-1-100", running=True)
        state = _make_state({"dashboard_chat-1-100": session})
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert session.task.cancelled()
        state.sessions.destroy.assert_awaited_once_with("dashboard:chat-1-100")

    @pytest.mark.asyncio
    async def test_non_running_task_not_cancelled(self):
        session = _make_session("dashboard_chat-1-100", running=False)
        state = _make_state({"dashboard_chat-1-100": session})
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert session.task is None
        state.sessions.destroy.assert_awaited_once_with("dashboard:chat-1-100")

    @pytest.mark.asyncio
    async def test_stacked_dashboard_prefix(self):
        session = _make_session("chat-3-300")
        state = _make_state({"chat-3-300": session})
        await _remove_session_for_history_key(state, "dashboard_dashboard_chat-3-300")
        assert "chat-3-300" not in state._sessions

    @pytest.mark.asyncio
    async def test_batch_clear_removes_multiple_sessions(self):
        """Verify batch clear removes matched sessions and leaves unmatched."""
        session_a = _make_session("chat-1-100")
        session_b = _make_session("chat-2-200", running=True)
        session_c = _make_session("chat-9-999")
        state = _make_state(
            {
                "chat-1-100": session_a,
                "chat-2-200": session_b,
                "chat-9-999": session_c,
            }
        )
        # Simulate batch clear for two keys (one matched, one running)
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        await _remove_session_for_history_key(state, "dashboard_chat-2-200")
        assert "chat-1-100" not in state._sessions
        assert "chat-2-200" not in state._sessions
        assert "chat-9-999" in state._sessions  # unmatched stays
        assert state.sessions.destroy.await_count == 2

    @pytest.mark.asyncio
    async def test_reverse_prefix_lookup(self):
        """History key 'chat-1-100' finds session stored as 'dashboard_chat-1-100'."""
        session = _make_session("dashboard_chat-1-100")
        state = _make_state({"dashboard_chat-1-100": session})
        await _remove_session_for_history_key(state, "chat-1-100")
        assert "dashboard_chat-1-100" not in state._sessions
        state.sessions.destroy.assert_awaited_once_with("dashboard:chat-1-100")

    @pytest.mark.asyncio
    async def test_sessions_remove_exception_does_not_propagate(self):
        session = _make_session("dashboard_chat-1-100")
        state = _make_state({"dashboard_chat-1-100": session})
        state.sessions.destroy = AsyncMock(side_effect=RuntimeError("already gone"))
        await _remove_session_for_history_key(state, "dashboard_chat-1-100")
        assert "dashboard_chat-1-100" not in state._sessions
