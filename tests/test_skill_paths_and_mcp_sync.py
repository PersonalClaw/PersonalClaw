"""Tests for api_sessions_restart MCP sync and _inject_skill_paths flag handling.

api_sessions_restart re-syncs MCP servers before restarting; _inject_skill_paths
appends ``--skill-paths`` to the build-message argv while preserving existing
flag-with-value pairs.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

# ---------------------------------------------------------------------------
# api_sessions_restart: syncs MCP servers before restarting
# ---------------------------------------------------------------------------


def _make_restart_request():
    """Build a minimal request for api_sessions_restart."""
    state = MagicMock()
    state.sessions = MagicMock()
    state.sessions.count = 0
    state.sessions._lock = asyncio.Lock()
    state.sessions._sessions = {}
    state.sessions._pool_started = False
    state.sessions.drain_all_providers = AsyncMock(return_value=[])
    state.sessions.start_pool = AsyncMock()
    state.broadcast_ws = MagicMock()
    state.push_refresh = MagicMock()
    state.push_slots_update = MagicMock()
    state._background_tasks = set()
    request = MagicMock(spec=web.Request)
    request.app = {"state": state}
    return request


class TestApiSessionsRestartMcpSync:
    """Verify MCP sync runs before session restart."""

    @pytest.mark.asyncio
    async def test_syncs_new_servers_before_restart(self):
        """discover + sync should run and count should appear in response."""
        from personalclaw.dashboard.handlers.sessions import api_sessions_restart

        fake_server = MagicMock()
        request = _make_restart_request()

        with (
            patch(
                "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.discover_servers_to_sync",
                return_value=[fake_server],
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.sync_to_agent_config", return_value=True
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.register_servers_for_cc",
                return_value=True,
            ),
        ):
            resp = await api_sessions_restart(request)

        body = json.loads(resp.body)
        assert body["mcp_synced"] == 1
        assert body["sessions_reset"] == 2

    @pytest.mark.asyncio
    async def test_sync_failure_does_not_block_restart(self):
        """If MCP sync raises, restart must still proceed."""
        from personalclaw.dashboard.handlers.sessions import api_sessions_restart

        request = _make_restart_request()

        with (
            patch(
                "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.discover_servers_to_sync",
                side_effect=RuntimeError("boom"),
            ),
        ):
            resp = await api_sessions_restart(request)

        body = json.loads(resp.body)
        assert body["sessions_reset"] == 1
        assert body["mcp_synced"] == 0

    @pytest.mark.asyncio
    async def test_no_servers_to_sync(self):
        """When discover returns empty list, synced count is 0."""
        from personalclaw.dashboard.handlers.sessions import api_sessions_restart

        request = _make_restart_request()

        with (
            patch(
                "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.discover_servers_to_sync", return_value=[]
            ),
            patch("personalclaw.dashboard.handlers.sessions.sync_to_agent_config") as mock_sync,
        ):
            resp = await api_sessions_restart(request)

        body = json.loads(resp.body)
        assert body["mcp_synced"] == 0
        mock_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_returns_false(self):
        """When sync_to_agent_config returns False, synced count stays 0."""
        from personalclaw.dashboard.handlers.sessions import api_sessions_restart

        request = _make_restart_request()

        with (
            patch(
                "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.discover_servers_to_sync",
                return_value=[MagicMock()],
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.sync_to_agent_config", return_value=False
            ),
        ):
            resp = await api_sessions_restart(request)

        body = json.loads(resp.body)
        assert body["mcp_synced"] == 0
        assert body["sessions_reset"] == 1

    @pytest.mark.asyncio
    async def test_multiple_servers_synced(self):
        """Multiple discovered servers should all be counted."""
        from personalclaw.dashboard.handlers.sessions import api_sessions_restart

        request = _make_restart_request()
        servers = [MagicMock(), MagicMock(), MagicMock()]

        with (
            patch(
                "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.discover_servers_to_sync",
                return_value=servers,
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.sync_to_agent_config", return_value=True
            ),
            patch(
                "personalclaw.dashboard.handlers.sessions.register_servers_for_cc",
                return_value=True,
            ),
        ):
            resp = await api_sessions_restart(request)

        body = json.loads(resp.body)
        assert body["mcp_synced"] == 3


# ---------------------------------------------------------------------------
# _inject_skill_paths: must preserve flag-with-value args
# ---------------------------------------------------------------------------


class TestInjectSkillPathsPreservesFlagValues:
    """Regression test for flag-with-value arg preservation in my-mcp-server args."""

    def test_include_tool_tags_value_preserved(self):
        """Flag-with-value pairs must survive --skill-paths injection."""
        from personalclaw.agent import _inject_skill_paths

        bm: dict = {"args": ["--include-tool-tags", "default,code-review"]}
        with patch("personalclaw.agent.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda p: MagicMock(
                is_dir=MagicMock(return_value=p == "/real/skills")
            )
            _inject_skill_paths(bm, ["/real/skills"])
        result = bm["args"]
        assert "--include-tool-tags" in result
        assert "default,code-review" in result
        assert result.count("--skill-paths") == 1
        assert "/real/skills" in result
