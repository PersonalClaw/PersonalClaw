"""Tests for POST /api/chat/sessions/{session}/workspace-dir endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.chat import api_chat_session_workspace_dir
from personalclaw.dashboard.state import DashboardState, _ChatSession


def _make_app(state: DashboardState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
    )
    return app


def _mock_state(session: _ChatSession | None = None) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._sessions = {}
    if session:
        state._sessions[session.key] = session
    state.push_sessions_update = MagicMock()
    state.file_indexes = MagicMock()
    state.file_indexes.acquire = AsyncMock()
    state.file_indexes.release = AsyncMock()
    return state


class TestChatSessionWorkspaceDir:
    @pytest.mark.asyncio
    async def test_set_workspace_dir(self, tmp_path):
        session = _ChatSession("test")
        state = _mock_state(session)
        with patch("personalclaw.dashboard.chat_handlers._save_recent_project"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/test/workspace-dir",
                    json={"workspace_dir": str(tmp_path)},
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True
                assert data["workspace_dir"] == str(tmp_path)
                assert session.workspace_dir == str(tmp_path)

    @pytest.mark.asyncio
    async def test_clear_workspace_dir(self, tmp_path):
        session = _ChatSession("test")
        session.workspace_dir = str(tmp_path)
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/workspace-dir",
                json={"workspace_dir": ""},
            )
            assert resp.status == 200
            assert session.workspace_dir == ""

    @pytest.mark.asyncio
    async def test_nonexistent_dir_returns_400(self):
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/workspace-dir",
                json={"workspace_dir": "/nonexistent_xyz_123"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_sensitive_path_returns_403(self, tmp_path):
        session = _ChatSession("test")
        state = _mock_state(session)
        with patch("personalclaw.dashboard.chat_handlers.is_sensitive_path", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/test/workspace-dir",
                    json={"workspace_dir": str(tmp_path)},
                )
                assert resp.status == 403

    @pytest.mark.asyncio
    async def test_can_change_mid_session(self, tmp_path):
        """The working directory can be changed after messages are sent."""
        session = _ChatSession("test")
        session.total_messages = 5
        state = _mock_state(session)
        with patch("personalclaw.dashboard.chat_handlers._save_recent_project"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/test/workspace-dir",
                    json={"workspace_dir": str(tmp_path)},
                )
                assert resp.status == 200
                assert session.workspace_dir == str(tmp_path)

    @pytest.mark.asyncio
    async def test_session_not_found(self):
        state = _mock_state()
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/missing/workspace-dir",
                json={"workspace_dir": "/tmp"},
            )
            assert resp.status == 404
