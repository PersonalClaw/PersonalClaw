"""Tests for POST /api/chat/sessions/{session}/workspace-dir endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.dashboard.chat import api_chat_session_agent, api_chat_session_workspace_dir
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


class TestAnOmittedKeyIsNotAClear:
    """A body without ``workspace_dir`` must be refused, not read as "clear it".

    Measured live during the `AAP-3` sweep: `POST …/workspace-dir` with a mistyped key
    (``{"dir": "/private/tmp/aap3-ws/scratch"}``) answered
    ``{"ok": true, "workspace_dir": ""}`` and left the session with **no** workspace.
    The caller had every reason to believe it had set one. For an ACP session that
    binding is where the agent's CLI runs, so the silent clear lands the agent in
    whatever directory the host resolves instead — the same user-visible failure as the
    `G39` profile-bound cwd escape, reached through an ordinary typo.

    Clearing on purpose is still supported and still tested above
    (`test_clear_workspace_dir`, an explicit empty string).
    """

    @pytest.mark.asyncio
    async def test_a_mistyped_key_is_refused_and_changes_nothing(self, tmp_path):
        session = _ChatSession("test")
        session.workspace_dir = str(tmp_path)
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/sessions/test/workspace-dir",
                json={"dir": str(tmp_path)},
            )
            assert resp.status == 400
            body = await resp.json()
            assert "workspace_dir" in body["error"]
        # The pre-existing binding survived the rejected request.
        assert session.workspace_dir == str(tmp_path)

    @pytest.mark.asyncio
    async def test_an_empty_body_is_refused(self, tmp_path):
        session = _ChatSession("test")
        session.workspace_dir = str(tmp_path)
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/test/workspace-dir", json={})
            assert resp.status == 400
        assert session.workspace_dir == str(tmp_path)

    @pytest.mark.asyncio
    async def test_the_error_names_how_to_clear(self, tmp_path):
        """The refusal has to teach the one legitimate way to unset it."""
        session = _ChatSession("test")
        state = _mock_state(session)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/test/workspace-dir", json={})
            body = await resp.json()
            assert "empty string" in body["error"]


class TestAgentBindingKeepsTheBoundWorkspace:
    """G39 — binding an agent PROFILE must obey ``default_dir``'s declared contract.

    *"Empty inherits the workspace root. Overridable per-session."* — so a profile that
    declared no directory must NOT displace a workspace the user bound through
    ``POST …/workspace-dir``. It used to overwrite it with the resolved fallback, which
    silently relocated the session (and, on the ACP spawn path, out of the configured home).
    """

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        """Real-home containment: this suite reads config and resolves a workspace root."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))

    @staticmethod
    def _app(state: DashboardState) -> web.Application:
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/sessions/{session}/agent", api_chat_session_agent)
        return app

    @staticmethod
    def _state_for(session: _ChatSession) -> DashboardState:
        state = _mock_state(session)
        state.sessions = MagicMock()
        state.sessions.reset = AsyncMock()
        state.conversation_log = None
        return state

    async def _bind(self, session, cfg):
        state = self._state_for(session)
        with (
            patch("personalclaw.dashboard.chat_handlers.AppConfig") as app_cfg,
            patch("personalclaw.dashboard.chat_handlers._sync_dashboard_sessions", MagicMock()),
        ):
            app_cfg.load.return_value = cfg
            async with TestClient(TestServer(self._app(state))) as client:
                resp = await client.post(
                    "/api/chat/sessions/test/agent", json={"agent": "bound-agent"}
                )
                assert resp.status == 200
                return await resp.json()

    @pytest.mark.asyncio
    async def test_profile_without_default_dir_keeps_the_bound_workspace(self, tmp_path):
        session = _ChatSession("test")
        session.workspace_dir = str(tmp_path)
        cfg = AppConfig.load()
        cfg.agents = {"bound-agent": AgentProfile()}

        data = await self._bind(session, cfg)

        assert session.workspace_dir == str(tmp_path)
        assert data["workspace_dir"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_profile_with_a_default_dir_still_wins(self, tmp_path):
        """The other half of the contract — a DECLARED directory remains the profile's opinion."""
        session = _ChatSession("test")
        session.workspace_dir = str(tmp_path / "bound")
        cfg = AppConfig.load()
        cfg.agents = {"bound-agent": AgentProfile(default_dir=str(tmp_path / "opinion"))}

        await self._bind(session, cfg)

        assert session.workspace_dir == str(tmp_path / "opinion")
