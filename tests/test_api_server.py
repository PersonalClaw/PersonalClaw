"""Tests for start_api_server and _register_mcp_routes.

Ensures --headless mode has working MCP tool endpoints (spawn, lessons,
crons, send-message, notifications).
"""

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.server import _register_mcp_routes
from personalclaw.dashboard.state import DashboardState


def _make_state(tmp_path, **kwargs):
    """DashboardState with mocked services (mirrors --headless init)."""
    monkeypatch_dir = tmp_path
    import personalclaw.dashboard.state as _st

    orig = _st.config_dir
    _st.config_dir = lambda: monkeypatch_dir
    try:
        state = DashboardState(
            sessions=MagicMock(count=0),
            crons=MagicMock(
                list_jobs=MagicMock(return_value=[]),
                status=MagicMock(return_value={}),
            ),
            lessons=MagicMock(load_all=MagicMock(return_value=[])),
            start_time=0.0,
            **kwargs,
        )
    finally:
        _st.config_dir = orig
    return state


def _make_api_app(state: DashboardState) -> web.Application:
    """Minimal app using only _register_mcp_routes (same as start_api_server)."""
    app = web.Application()
    app["state"] = state
    app["port"] = 7777
    _register_mcp_routes(app)
    return app


class TestRegisterMcpRoutes:
    """Verify _register_mcp_routes registers all expected endpoints."""

    def test_all_mcp_routes_registered(self, tmp_path):
        state = _make_state(tmp_path)
        app = _make_api_app(state)
        routes = {(r.method, r.resource.canonical) for r in app.router.routes()}
        expected = {
            ("POST", "/api/spawn"),
            ("GET", "/api/spawn"),
            ("GET", "/api/spawn/{agent_id}"),
            ("DELETE", "/api/spawn/{agent_id}"),
            ("DELETE", "/api/spawn"),
            ("GET", "/api/lessons"),
            ("POST", "/api/lessons"),
            ("DELETE", "/api/lessons"),
            ("GET", "/api/triggers"),
            ("POST", "/api/triggers"),
            ("DELETE", "/api/triggers/{id}"),
            ("POST", "/api/triggers/{id}/toggle"),
            ("POST", "/api/triggers/{id}/ack"),
            ("POST", "/api/send-message"),
            ("GET", "/api/notifications"),
            ("POST", "/api/notifications/clear"),
        }
        assert expected.issubset(routes), f"Missing routes: {expected - routes}"


class TestApiServerSpawn:
    """Spawn endpoints work through the API-only server."""

    @pytest.mark.asyncio
    async def test_spawn_returns_503_without_subagent_mgr(self, tmp_path):
        state = _make_state(tmp_path, subagents=None)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.post("/api/spawn", json={"task": "hello"})
            assert resp.status == 503
            data = await resp.json()
            assert "not available" in data["error"]

    @pytest.mark.asyncio
    async def test_spawn_succeeds_with_subagent_mgr(self, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.spawn.return_value = MagicMock(id="test-123", done=False, error="")
        state = _make_state(tmp_path, subagents=mock_mgr)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.post("/api/spawn", json={"task": "say hello"})
            assert resp.status == 200
            data = await resp.json()
            assert data["id"] == "test-123"
            assert data["status"] == "spawned"

    @pytest.mark.asyncio
    async def test_spawn_passes_max_turns(self, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.spawn.return_value = MagicMock(id="test-456", done=False, error="")
        state = _make_state(tmp_path, subagents=mock_mgr)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.post("/api/spawn", json={"task": "hi", "max_turns": 50})
            assert resp.status == 200
            assert mock_mgr.spawn.call_args.kwargs.get("max_turns") == 50

    @pytest.mark.asyncio
    async def test_spawn_list_empty(self, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.list.return_value = []
        state = _make_state(tmp_path, subagents=mock_mgr)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/api/spawn")
            assert resp.status == 200


class TestApiServerLessons:
    """Lesson endpoints work through the API-only server."""

    @pytest.mark.asyncio
    async def test_lessons_get(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/api/lessons")
            assert resp.status == 200


class TestApiServerCrons:
    """Trigger endpoints work through the API-only server."""

    @pytest.mark.asyncio
    async def test_triggers_get(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/api/triggers")
            assert resp.status == 200


class TestApiServerSendMessage:
    """send-message endpoint works through the API-only server."""

    @pytest.mark.asyncio
    async def test_send_message_without_channel(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        # No channel connected → channel_delivery is None (default); delivery falls
        # back to a dashboard notification, never a hard failure.
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.post("/api/send-message", json={"text": "hello"})
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["channel"] is False


class TestApiServerNoUiRoutes:
    """API-only server must NOT have dashboard UI routes."""

    @pytest.mark.asyncio
    async def test_no_index_route(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_static_route(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/static/foo.js")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_websocket_route(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.get("/api/ws")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_chat_route(self, tmp_path):
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_api_app(state))) as client:
            resp = await client.post("/api/chat", json={})
            assert resp.status == 404


class TestStartApiServerWiring:
    """Integration test: start_api_server installs middleware and hook store."""

    @pytest.mark.asyncio
    async def test_server_has_audit_middleware_and_hook_store(self, tmp_path, monkeypatch):
        import personalclaw.dashboard.state as _st

        monkeypatch.setattr(_st, "config_dir", lambda: tmp_path)

        from personalclaw.dashboard.server import start_api_server

        runner, state = await start_api_server(
            sessions=MagicMock(count=0),
            crons=MagicMock(
                list_jobs=MagicMock(return_value=[]),
                status=MagicMock(return_value={}),
            ),
            lessons=MagicMock(load_all=MagicMock(return_value=[])),
            port=0,
        )
        try:
            assert state._hook_store is not None
            assert len(runner.app.middlewares) > 0
        finally:
            await runner.cleanup()


class TestApiPersonalclawConfig:
    """Tests for PUT /api/config/personalclaw inline validation."""

    @staticmethod
    def _make_app(tmp_path):
        from personalclaw.dashboard import handlers

        app = web.Application()
        app.router.add_get("/api/config/personalclaw", handlers.api_personalclaw_config)
        app.router.add_put("/api/config/personalclaw", handlers.api_personalclaw_config)
        return app

    @pytest.mark.asyncio
    async def test_put_happy_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text('{"agent": {"max_subagents": 3}}')
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put(
                "/api/config/personalclaw", json={"agent": {"subagent_max_turns": 50}}
            )
            assert resp.status == 200
            import json

            saved = json.loads((tmp_path / "config.json").read_text())
            assert saved["agent"]["subagent_max_turns"] == 50
            assert saved["agent"]["max_subagents"] == 3  # preserved

    @pytest.mark.asyncio
    async def test_put_rejects_bool(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text('{"agent": {}}')
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put(
                "/api/config/personalclaw", json={"agent": {"subagent_max_turns": True}}
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_rejects_out_of_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text('{"agent": {}}')
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put("/api/config/personalclaw", json={"agent": {"max_subagents": 17}})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_accepts_zero_max_subagents_as_auto(self, tmp_path, monkeypatch):
        """max_subagents=0 is the 'auto-size from host' sentinel — accepted + saved."""
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text('{"agent": {}}')
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put("/api/config/personalclaw", json={"agent": {"max_subagents": 0}})
            assert resp.status == 200
            import json

            saved = json.loads((tmp_path / "config.json").read_text())
            assert saved["agent"]["max_subagents"] == 0

    @pytest.mark.asyncio
    async def test_put_rejects_non_dict_agent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put("/api/config/personalclaw", json={"agent": "not a dict"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_corrupt_config_returns_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text("NOT JSON{{{")
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put(
                "/api/config/personalclaw", json={"agent": {"subagent_max_turns": 50}}
            )
            assert resp.status == 500

    @pytest.mark.asyncio
    async def test_put_rejects_unrecognized_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
        (tmp_path / "config.json").write_text('{"agent": {}}')
        async with TestClient(TestServer(self._make_app(tmp_path))) as c:
            resp = await c.put("/api/config/personalclaw", json={"agent": {"unknown_key": 42}})
            assert resp.status == 400
