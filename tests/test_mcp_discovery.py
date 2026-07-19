"""Tests for MCP discovery module."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.mcp_discovery import (
    McpServerInfo,
    _cache_probe,
    _get_cached,
    _probe_cache,
    _probe_remote,
    _read_jsonrpc_response,
    discover_servers_to_sync,
    list_servers,
    probe_server,
    sync_to_agent_config,
)


def _clear_cache() -> None:
    _probe_cache.clear()


class TestMcpServerInfo:
    def test_to_dict(self) -> None:
        info = McpServerInfo(
            name="test-mcp",
            command="/usr/bin/test",
            args=["--foo"],
            status="ok",
            tools=["tool_a", "tool_b"],
            source="agent",
        )
        d = info.to_dict()
        assert d["name"] == "test-mcp"
        assert d["command"] == "/usr/bin/test"
        assert d["args"] == ["--foo"]
        assert d["status"] == "ok"
        assert d["tools"] == ["tool_a", "tool_b"]
        assert d["source"] == "agent"
        assert "url" not in d

    def test_defaults(self) -> None:
        info = McpServerInfo(name="x")
        assert info.command == ""
        assert info.args is None
        assert info.env == {}
        assert info.url == ""
        assert info.headers == {}
        assert info.status == "unknown"
        assert info.tools == []
        assert info.error == ""
        assert info.source == "agent"

    def test_remote_server_fields(self) -> None:
        info = McpServerInfo(
            name="deepwiki",
            url="https://mcp.deepwiki.com/mcp",
            headers={"Authorization": "Bearer tok"},
        )
        assert info.is_remote is True
        assert info.command == ""
        d = info.to_dict()
        assert d["url"] == "https://mcp.deepwiki.com/mcp"
        assert d["headers"] == {"Authorization": "Bearer tok"}

    def test_is_remote_false_for_local(self) -> None:
        info = McpServerInfo(name="x", command="cmd")
        assert info.is_remote is False

    def test_cwd_round_trips_from_spec(self) -> None:
        # An app-shipped stdio server carries a cwd (its app dir) so relative
        # args resolve on probe. Parse from spec + expose in to_dict.
        from personalclaw.mcp_discovery import _server_from_spec

        info = _server_from_spec(
            "app:local",
            {"command": "python3", "args": ["backend/mcp_server.py"], "cwd": "/opt/app"},
            "mcp.json",
        )
        assert info.cwd == "/opt/app"
        assert info.to_dict()["cwd"] == "/opt/app"
        # No cwd → field stays empty and is omitted from to_dict.
        bare = _server_from_spec("x", {"command": "c"}, "mcp.json")
        assert bare.cwd == ""
        assert "cwd" not in bare.to_dict()

    def test_is_remote_false_when_both(self) -> None:
        """If both url and command are set, treat as local (command takes precedence)."""
        info = McpServerInfo(name="x", command="cmd", url="http://localhost")
        assert info.is_remote is False


class TestListServers:
    def setup_method(self) -> None:
        _clear_cache()

    def test_list_merges_installed_config(self, tmp_path, monkeypatch) -> None:
        """defaults.json has no mcpServers; installed personalclaw.json does."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        installed = {"mcpServers": {"personalclaw-schedule": {"command": "personalclaw", "args": ["mcp-schedule"]}}}
        (agents_dir / "personalclaw.json").write_text(json.dumps(installed))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (tmp_path / "nope.json",))
        servers = list_servers()
        names = {s.name for s in servers}
        assert "personalclaw-schedule" in names

    def test_list_from_agent_config(self, tmp_path, monkeypatch) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {
            "mcpServers": {
                "my-server": {"command": "/usr/bin/srv", "args": ["run"]},
                "other-srv": {"command": "other"},
            }
        }
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        servers = list_servers()
        names = {s.name for s in servers}
        assert "my-server" in names
        assert "other-srv" in names

    def test_list_empty_no_config(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (tmp_path / "nope.json",))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        servers = list_servers()
        assert servers == []

    def test_mcp_json_servers_merged(self, tmp_path, monkeypatch) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"agent-srv": {"command": "a"}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps({"mcpServers": {"ext-srv": {"command": "b", "args": ["--x"]}}})
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        servers = list_servers()
        names = {s.name for s in servers}
        assert "agent-srv" in names
        assert "ext-srv" in names
        ext = [s for s in servers if s.name == "ext-srv"][0]
        assert ext.source == "mcp.json"

    def test_mcp_json_no_duplicate(self, tmp_path, monkeypatch) -> None:
        """mcp.json server with same name as agent config is NOT duplicated."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"shared": {"command": "agent-cmd"}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"mcpServers": {"shared": {"command": "mcp-cmd"}}}))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        servers = list_servers()
        shared = [s for s in servers if s.name == "shared"]
        assert len(shared) == 1
        assert shared[0].command == "agent-cmd"

    def test_list_skips_disabled_servers(self, tmp_path, monkeypatch) -> None:
        """Servers with disabled=true are excluded from listing."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {
            "mcpServers": {
                "enabled-srv": {"command": "a"},
                "disabled-srv": {"command": "b", "disabled": True},
            }
        }
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (tmp_path / "x",))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        servers = list_servers()
        names = {s.name for s in servers}
        assert "enabled-srv" in names
        assert "disabled-srv" not in names

    def test_list_skips_disabled_mcp_json_servers(self, tmp_path, monkeypatch) -> None:
        """Disabled servers in mcp.json are also excluded."""
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "active": {"command": "a"},
                        "inactive": {"command": "b", "disabled": True},
                    }
                }
            )
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        servers = list_servers()
        names = {s.name for s in servers}
        assert "active" in names
        assert "inactive" not in names

    def test_disabled_in_agent_blocks_mcp_json(self, tmp_path, monkeypatch) -> None:
        """Server disabled in agent config is not re-added from mcp.json."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "a", "disabled": True}}})
        )
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"mcpServers": {"srv": {"command": "b"}}}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        assert not any(s.name == "srv" for s in list_servers())

    def test_disabled_mcp_json_still_carries_disabled_tools(self, tmp_path, monkeypatch) -> None:
        """disabledTools from a disabled mcp.json entry are applied to an existing agent server."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "a"}}})
        )
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps({"mcpServers": {"srv": {"disabled": True, "disabledTools": ["t1"]}}})
        )
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        servers = list_servers()
        assert len(servers) == 1
        assert servers[0].disabled_tools == ["t1"]

    def test_list_remote_server(self, tmp_path, monkeypatch) -> None:
        """Remote (url-based) servers are listed with url and headers."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {
            "mcpServers": {
                "deepwiki": {
                    "url": "https://mcp.deepwiki.com/mcp",
                    "headers": {"X-Key": "val"},
                }
            }
        }
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (tmp_path / "x",))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        servers = list_servers()
        assert len(servers) == 1
        s = servers[0]
        assert s.name == "deepwiki"
        assert s.url == "https://mcp.deepwiki.com/mcp"
        assert s.headers == {"X-Key": "val"}
        assert s.command == ""
        assert s.is_remote is True

    def test_mcp_json_merges_multiple_files(self, tmp_path, monkeypatch) -> None:
        """Both mcp.json files are read and merged; first path wins on conflict."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"mcpServers": {}}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        _clear_cache()

        legacy_mcp = tmp_path / "legacy_mcp.json"
        legacy_mcp.write_text(
            json.dumps({"mcpServers": {"shared": {"command": "legacy-cmd"}, "legacy-only": {"command": "k"}}})
        )
        personalclaw_mcp = tmp_path / "personalclaw_mcp.json"
        personalclaw_mcp.write_text(
            json.dumps(
                {"mcpServers": {"shared": {"command": "personalclaw"}, "mc-only": {"command": "m"}}}
            )
        )
        monkeypatch.setattr(
            "personalclaw.mcp_discovery._MCP_JSON_PATHS", (legacy_mcp, personalclaw_mcp)
        )

        servers = list_servers()
        names = {s.name for s in servers}
        assert "legacy-only" in names
        assert "mc-only" in names
        assert "shared" in names
        shared = [s for s in servers if s.name == "shared"][0]
        assert shared.command == "legacy-cmd"  # first path wins

    def test_mcp_json_malformed_file_skipped(self, tmp_path, monkeypatch) -> None:
        """A malformed mcp.json is skipped; valid file still loads."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"mcpServers": {}}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        _clear_cache()

        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (bad, good))

        servers = list_servers()
        assert any(s.name == "srv" for s in servers)

    def test_mcp_json_non_dict_servers_skipped(self, tmp_path, monkeypatch) -> None:
        """Non-dict mcpServers value is skipped; other file still loads."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"mcpServers": {}}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        _clear_cache()

        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"mcpServers": ["not", "a", "dict"]}))
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (bad, good))

        servers = list_servers()
        assert any(s.name == "srv" for s in servers)

    def test_mcp_json_permission_error_skipped(self, tmp_path, monkeypatch) -> None:
        """PermissionError from safe_read_file is caught; other file loads."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"mcpServers": {}}))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        _clear_cache()

        blocked = tmp_path / "blocked.json"
        blocked.write_text("{}")
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (blocked, good))

        original = __import__("personalclaw.hooks", fromlist=["safe_read_file"]).safe_read_file

        def _mock_safe_read(path: str) -> str:
            if "blocked" in path:
                raise PermissionError("Blocked: sensitive path")
            return original(path)

        monkeypatch.setattr("personalclaw.mcp_discovery.safe_read_file", _mock_safe_read)

        servers = list_servers()
        assert any(s.name == "srv" for s in servers)


class TestDiscoverNew:
    def test_discover_new(self, tmp_path, monkeypatch) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"existing": {"command": "a"}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "existing": {"command": "a"},
                        "brand-new": {"command": "b"},
                    }
                }
            )
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        new = discover_servers_to_sync()
        assert len(new) == 1
        assert new[0].name == "brand-new"
        assert new[0].source == "discovered"

    def test_discover_none_when_all_known(self, tmp_path, monkeypatch) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"srv": {"command": "a"}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"mcpServers": {"srv": {"command": "a"}}}))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        new = discover_servers_to_sync()
        assert new == []

    def test_discover_includes_existing_with_divergent_env(
        self, tmp_path, monkeypatch
    ) -> None:
        """Existing servers with new env keys in mcp.json are included."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"srv": {"command": "a", "env": {}}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {"mcpServers": {"srv": {"command": "a", "env": {"KEY": "val"}}}}
            )
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        result = discover_servers_to_sync()
        assert len(result) == 1
        assert result[0].name == "srv"
        assert result[0].env == {"KEY": "val"}

    def test_discover_skips_existing_with_identical_env(
        self, tmp_path, monkeypatch
    ) -> None:
        """Existing servers with identical env are not flagged for sync."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"srv": {"command": "a", "env": {"KEY": "val"}}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {"mcpServers": {"srv": {"command": "a", "env": {"KEY": "val"}}}}
            )
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        result = discover_servers_to_sync()
        assert result == []

    def test_discover_skips_existing_when_source_env_is_subset(
        self, tmp_path, monkeypatch
    ) -> None:
        """Server not flagged when all mcp.json env keys already exist in agent config."""
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {
            "mcpServers": {
                "srv": {"command": "a", "env": {"EXISTING": "keep", "NEW": "val"}}
            }
        }
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {"mcpServers": {"srv": {"command": "a", "env": {"NEW": "val"}}}}
            )
        )
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (mcp_json,))
        result = discover_servers_to_sync()
        assert result == []


class TestSyncToAgentConfig:
    def test_sync_delegates_to_rebuild_agent_config(self, tmp_path, monkeypatch) -> None:
        """sync_to_agent_config delegates the merge to rebuild_agent_config() for new servers.

        Registration is no longer done by shelling out to a CLI — rebuild_agent_config()
        is the single authoritative merge function that reads the source files and
        writes personalclaw.json.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}))

        install_called: list[bool] = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        new_srv = McpServerInfo(name="new-srv", command="b", args=["--x"])
        ok = sync_to_agent_config([new_srv])
        assert ok is True
        assert install_called, "rebuild_agent_config() should be called to merge the config"

    def test_sync_fallback_writes_json(self, tmp_path, monkeypatch) -> None:
        """Without personalclaw-cli, delegates to rebuild_agent_config() for config merge."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg = {
            "mcpServers": {"existing": {"command": "a"}},
            "tools": ["execute_bash"],
            "allowedTools": [],
        }
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        new_srv = McpServerInfo(name="new-srv", command="b", args=["--x"])
        ok = sync_to_agent_config([new_srv])
        assert ok is True
        assert install_called, "rebuild_agent_config() should be called"

    def test_sync_no_installed_config(self, tmp_path, monkeypatch) -> None:
        """Works even when no config exists yet — rebuild_agent_config creates it."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        config_path = agents_dir / "personalclaw.json"
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(name="srv", command="x")
        ok = sync_to_agent_config([srv])
        assert ok is True
        assert install_called

    def test_sync_remote_server_writes_url(self, tmp_path, monkeypatch) -> None:
        """Remote servers are handled by rebuild_agent_config() via source file merge."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg: dict = {"mcpServers": {}, "tools": [], "allowedTools": []}
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(
            name="deepwiki",
            url="https://mcp.deepwiki.com/mcp",
            headers={"X-Key": "val"},
        )
        ok = sync_to_agent_config([srv])
        assert ok is True
        assert install_called

    def test_sync_handles_remote_and_local_via_rebuild_agent_config(self, tmp_path, monkeypatch) -> None:
        """Both remote (url) and local (command) servers are merged via rebuild_agent_config()."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(
            json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []})
        )

        install_called: list[bool] = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        remote = McpServerInfo(name="deepwiki", url="https://mcp.deepwiki.com/mcp")
        local = McpServerInfo(name="local-srv", command="some-cmd")
        ok = sync_to_agent_config([remote, local])

        assert ok is True
        assert install_called, "rebuild_agent_config() should be called once to merge both servers"

    def test_sync_merges_env_for_existing_local_server(self, tmp_path, monkeypatch) -> None:
        """Existing server env changes are handled by rebuild_agent_config() re-merge."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg = {
            "mcpServers": {
                "email-mcp": {"command": "node", "args": ["server.js"], "env": {}}
            },
            "tools": ["@email-mcp"],
            "allowedTools": ["@email-mcp"],
        }
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(
            name="email-mcp",
            command="node",
            args=["server.js"],
            env={"OUTLOOK_MCP_ENABLE_WRITES": "true"},
        )
        sync_to_agent_config([srv])
        assert install_called, "rebuild_agent_config() handles env merge"

    def test_sync_preserves_existing_env_keys(self, tmp_path, monkeypatch) -> None:
        """Env merge is handled by rebuild_agent_config() reading source files."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg = {
            "mcpServers": {
                "my-mcp": {
                    "command": "node",
                    "args": [],
                    "env": {"EXISTING_KEY": "keep"},
                }
            },
            "tools": ["@my-mcp"],
            "allowedTools": ["@my-mcp"],
        }
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(
            name="my-mcp",
            command="node",
            args=[],
            env={"NEW_KEY": "val"},
        )
        sync_to_agent_config([srv])
        assert install_called, "rebuild_agent_config() handles env merge"

    def test_sync_updates_command_for_existing_local_server(
        self, tmp_path, monkeypatch
    ) -> None:
        """Existing servers are refreshed via rebuild_agent_config() which reads source files."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg = {
            "mcpServers": {
                "my-mcp": {"command": "old-cmd", "args": ["--old"], "env": {}}
            },
            "tools": ["@my-mcp"],
            "allowedTools": ["@my-mcp"],
        }
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        # rebuild_agent_config() is called internally — mock it to verify delegation
        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(name="my-mcp", command="new-cmd", args=["--new"])
        result = sync_to_agent_config([srv])
        assert result is True
        assert install_called, "rebuild_agent_config() should be called to re-merge config"

    def test_sync_source_env_overrides_existing_on_conflict(
        self, tmp_path, monkeypatch
    ) -> None:
        """Config changes are handled by rebuild_agent_config() re-merge, not direct edit."""
        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        cfg = {
            "mcpServers": {
                "my-mcp": {
                    "command": "node",
                    "args": [],
                    "env": {"SHARED": "old", "ONLY_EXISTING": "keep"},
                }
            },
            "tools": ["@my-mcp"],
            "allowedTools": ["@my-mcp"],
        }
        config_path = agents_dir / "personalclaw.json"
        config_path.write_text(json.dumps(cfg))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda x, **kw: None)

        install_called = []
        monkeypatch.setattr(
            "personalclaw.agent.rebuild_agent_config",
            lambda **kw: install_called.append(True) or config_path,
        )

        srv = McpServerInfo(
            name="my-mcp",
            command="node",
            args=[],
            env={"SHARED": "new", "ONLY_SOURCE": "added"},
        )
        result = sync_to_agent_config([srv])
        assert result is True
        assert install_called, "rebuild_agent_config() should be called to re-merge config"


class TestProbeCache:
    def setup_method(self) -> None:
        _clear_cache()

    def teardown_method(self) -> None:
        _clear_cache()

    def test_cache_miss_returns_unknown(self) -> None:
        status, tools, error = _get_cached("nonexistent")
        assert status == "unknown"
        assert tools == []
        assert error == ""

    def test_cache_hit_within_ttl(self) -> None:
        server = McpServerInfo(
            name="test-srv", command="x", status="ok", tools=["t1", "t2"], error=""
        )
        _cache_probe(server)
        status, tools, error = _get_cached("test-srv")
        assert status == "ok"
        assert tools == ["t1", "t2"]
        assert error == ""

    def test_cache_expired_returns_outdated_with_tools(self, monkeypatch) -> None:
        server = McpServerInfo(
            name="test-srv", command="x", status="ok", tools=["t1", "t2"], error=""
        )
        _cache_probe(server)
        # Simulate expiry by backdating probed_at
        _probe_cache["test-srv"].probed_at = time.monotonic() - 2000
        status, tools, error = _get_cached("test-srv")
        assert status == "outdated"
        assert tools == ["t1", "t2"]
        assert error == ""

    def test_cache_error_preserved(self) -> None:
        server = McpServerInfo(
            name="err-srv", command="x", status="error", tools=[], error="timeout"
        )
        _cache_probe(server)
        status, tools, error = _get_cached("err-srv")
        assert status == "error"
        assert error == "timeout"

    def test_list_servers_merges_cache(self, tmp_path, monkeypatch) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        cfg = {"mcpServers": {"my-srv": {"command": "cmd"}}}
        (agent_dir / "defaults.json").write_text(json.dumps(cfg))
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        monkeypatch.setattr("personalclaw.mcp_discovery._MCP_JSON_PATHS", (tmp_path / "x",))
        monkeypatch.setattr("personalclaw.mcp_discovery.Path.home", lambda: tmp_path)

        # Before probe: unknown
        servers = list_servers()
        assert servers[0].status == "unknown"

        # Cache a probe result
        _cache_probe(McpServerInfo(name="my-srv", command="cmd", status="ok", tools=["a"]))

        # After probe: cached status and tools merged
        servers = list_servers()
        assert servers[0].status == "ok"
        assert servers[0].tools == ["a"]


class TestReadJsonrpcResponse:
    @pytest.mark.asyncio
    async def test_json_content_type(self) -> None:
        resp = MagicMock()
        resp.content_type = "application/json"
        resp.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {}})
        result = await _read_jsonrpc_response(resp)
        assert result == {"jsonrpc": "2.0", "id": 1, "result": {}}

    @pytest.mark.asyncio
    async def test_sse_content_type(self) -> None:
        sse_body = (
            "event: message\n" 'data: {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}\n' "\n"
        )
        resp = MagicMock()
        resp.content_type = "text/event-stream"
        resp.text = AsyncMock(return_value=sse_body)
        result = await _read_jsonrpc_response(resp)
        assert result["id"] == 1
        assert result["result"] == {"tools": []}

    @pytest.mark.asyncio
    async def test_sse_picks_last_response(self) -> None:
        """Multiple data lines — picks the last one with an id."""
        sse_body = (
            'data: {"jsonrpc": "2.0", "method": "log"}\n'
            'data: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n'
        )
        resp = MagicMock()
        resp.content_type = "text/event-stream"
        resp.text = AsyncMock(return_value=sse_body)
        result = await _read_jsonrpc_response(resp)
        assert result["result"] == {"ok": True}

    @pytest.mark.asyncio
    async def test_sse_empty_returns_empty_dict(self) -> None:
        resp = MagicMock()
        resp.content_type = "text/event-stream"
        resp.text = AsyncMock(return_value="")
        result = await _read_jsonrpc_response(resp)
        assert result == {}


class TestProbeRemote:
    def setup_method(self) -> None:
        _probe_cache.clear()

    def teardown_method(self) -> None:
        _probe_cache.clear()

    @pytest.mark.asyncio
    async def test_probe_remote_ok(self) -> None:
        """Successful HTTP probe returns ok status and tools."""
        server = McpServerInfo(name="remote", url="https://example.com/mcp")

        init_resp = MagicMock()
        init_resp.status = 200
        init_resp.content_type = "application/json"
        init_resp.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {}})
        init_resp.__aenter__ = AsyncMock(return_value=init_resp)
        init_resp.__aexit__ = AsyncMock(return_value=False)

        tools_resp = MagicMock()
        tools_resp.status = 200
        tools_resp.content_type = "application/json"
        tools_resp.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "search"}, {"name": "read"}]},
            }
        )
        tools_resp.__aenter__ = AsyncMock(return_value=tools_resp)
        tools_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=[init_resp, tools_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("personalclaw.mcp_discovery.aiohttp.ClientSession", return_value=mock_session):
            result = await _probe_remote(server)

        assert result.status == "ok"
        # _probe_remote now returns rich tool descriptors (name/description/
        # inputSchema), not bare name strings.
        assert [t["name"] for t in result.tools] == ["search", "read"]

    @pytest.mark.asyncio
    async def test_probe_remote_http_error(self) -> None:
        """Non-200 response sets error status."""
        server = McpServerInfo(name="remote", url="https://example.com/mcp")

        resp = MagicMock()
        resp.status = 500
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("personalclaw.mcp_discovery.aiohttp.ClientSession", return_value=mock_session):
            result = await _probe_remote(server)

        assert result.status == "error"
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_probe_remote_connection_error(self) -> None:
        """Connection failure sets error status."""
        server = McpServerInfo(name="remote", url="https://unreachable.example.com/mcp")

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=ConnectionError("refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("personalclaw.mcp_discovery.aiohttp.ClientSession", return_value=mock_session):
            result = await _probe_remote(server)

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_probe_dispatches_to_remote(self) -> None:
        """probe_server dispatches to _probe_remote for url-based servers."""
        server = McpServerInfo(name="remote", url="https://example.com/mcp")

        with patch("personalclaw.mcp_discovery._probe_remote", new_callable=AsyncMock) as mock_remote:
            mock_remote.return_value = server
            result = await probe_server(server)

        mock_remote.assert_awaited_once_with(server)
        assert result is server

    @pytest.mark.asyncio
    async def test_probe_local_not_dispatched_to_remote(self) -> None:
        """probe_server does NOT dispatch to _probe_remote for command-based servers."""
        server = McpServerInfo(name="local", command="nonexistent-cmd-xyz")

        with patch("personalclaw.mcp_discovery._probe_remote", new_callable=AsyncMock) as mock_remote:
            result = await probe_server(server)

        mock_remote.assert_not_awaited()
        assert result.status == "error"


class TestProbeServerProcessCleanup:
    """Tests for the finally block that tears down the probed subprocess."""

    def _make_mock_proc(self, *, wait_side_effect=None):
        proc = AsyncMock()
        proc.returncode = None  # process still running
        proc.stdin = MagicMock()
        proc.stdin.close = MagicMock()
        proc.kill = MagicMock()
        if wait_side_effect:
            proc.wait = AsyncMock(side_effect=wait_side_effect)
        else:
            proc.wait = AsyncMock(return_value=0)
        return proc

    @pytest.mark.asyncio
    async def test_graceful_stdin_close(self) -> None:
        """Closing stdin causes process to exit within timeout."""
        proc = self._make_mock_proc()
        server = McpServerInfo(name="test", command="echo")

        with patch(
            "personalclaw.mcp_discovery.asyncio.create_subprocess_exec", return_value=proc
        ), patch("personalclaw.mcp_discovery.shutil.which", return_value="/usr/bin/echo"):
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            await probe_server(server)

        proc.stdin.close.assert_called_once()
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_kill_on_timeout(self) -> None:
        """When graceful shutdown times out, falls back to proc.kill()."""
        proc = self._make_mock_proc(
            wait_side_effect=[asyncio.TimeoutError(), AsyncMock(return_value=0)()]
        )
        server = McpServerInfo(name="test", command="echo")

        with patch(
            "personalclaw.mcp_discovery.asyncio.create_subprocess_exec", return_value=proc
        ), patch("personalclaw.mcp_discovery.shutil.which", return_value="/usr/bin/echo"):
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            await probe_server(server)

        proc.stdin.close.assert_called_once()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_kill_also_fails(self) -> None:
        """When both graceful and forceful shutdown fail, exception is swallowed."""
        proc = self._make_mock_proc(
            wait_side_effect=[asyncio.TimeoutError(), OSError("kill failed")]
        )
        server = McpServerInfo(name="test", command="echo")

        with patch(
            "personalclaw.mcp_discovery.asyncio.create_subprocess_exec", return_value=proc
        ), patch("personalclaw.mcp_discovery.shutil.which", return_value="/usr/bin/echo"):
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            await probe_server(server)

        # Should not raise — the exception is caught and swallowed
        proc.stdin.close.assert_called_once()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stdin_none_skips_close(self) -> None:
        """When proc.stdin is None, close is skipped without error."""
        proc = self._make_mock_proc()
        proc.stdin = None
        server = McpServerInfo(name="test", command="echo")

        with patch(
            "personalclaw.mcp_discovery.asyncio.create_subprocess_exec", return_value=proc
        ), patch("personalclaw.mcp_discovery.shutil.which", return_value="/usr/bin/echo"):
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            await probe_server(server)

        # Should not raise — stdin None is handled gracefully
        proc.kill.assert_not_called()


class TestRebuildAgentConfigRemote:
    """Test that rebuild_agent_config preserves remote url-based MCP servers."""

    def test_install_preserves_remote_server(self, tmp_path, monkeypatch) -> None:
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        existing = {
            "mcpServers": {
                "deepwiki": {"url": "https://mcp.deepwiki.com/mcp"},
                "local-srv": {"command": "nonexistent-cmd-xyz"},
            },
            "tools": [],
            "allowedTools": [],
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        # Single user MCP source: ~/.personalclaw/mcp.json (none here).
        user_dir = tmp_path / ".personalclaw"
        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: None)

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "deepwiki" in data["mcpServers"]
        assert data["mcpServers"]["deepwiki"]["url"] == "https://mcp.deepwiki.com/mcp"
        assert "local-srv" not in data["mcpServers"]

    def test_install_merges_user_mcp_json(self, tmp_path, monkeypatch) -> None:
        """rebuild_agent_config picks up servers from ~/.personalclaw/mcp.json."""
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)

        # Single consolidated user MCP source: ~/.personalclaw/mcp.json
        user_dir = tmp_path / ".personalclaw"
        (user_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"deepwiki": {"url": "https://mcp.deepwiki.com/mcp"}}})
        )

        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: None)

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "deepwiki" in data["mcpServers"]
        assert data["mcpServers"]["deepwiki"]["url"] == "https://mcp.deepwiki.com/mcp"


class TestGetProbeTimeout:
    """Tests for the config-aware _get_probe_timeout() getter."""

    def test_get_probe_timeout_reads_config(self) -> None:
        """_get_probe_timeout() returns the config value when available."""
        from personalclaw.mcp_discovery import _get_probe_timeout

        mock_cfg = MagicMock()
        mock_cfg.dashboard.mcp_probe_timeout_secs = 45
        mock_cls = MagicMock()
        mock_cls.load.return_value = mock_cfg

        with patch("personalclaw.config.loader.AppConfig", mock_cls):
            result = _get_probe_timeout()
        assert result == 45

    def test_get_probe_timeout_fallback(self) -> None:
        """_get_probe_timeout() returns 15 when config is unavailable."""
        from personalclaw.mcp_discovery import _PROBE_TIMEOUT_SECS, _get_probe_timeout

        mock_cls = MagicMock()
        mock_cls.load.side_effect = RuntimeError("no config")

        with patch("personalclaw.config.loader.AppConfig", mock_cls):
            result = _get_probe_timeout()
        assert result == _PROBE_TIMEOUT_SECS
        assert result == 15


class TestProbeServerTimeout:
    """Tests that probe_server uses _get_probe_timeout() and handles timeout."""

    @pytest.mark.asyncio
    async def test_probe_server_timeout_on_tools_list(self) -> None:
        """probe_server times out on tools/list (second readline), covering L456."""
        server = McpServerInfo(name="slow-server", command="sleep", args=["999"])

        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"

        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            side_effect=[init_resp, asyncio.TimeoutError]
        )
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("personalclaw.config.loader.AppConfig") as mock_cls:
            mock_cfg = MagicMock()
            mock_cfg.dashboard.mcp_probe_timeout_secs = 42
            mock_cls.load.return_value = mock_cfg

            result = await probe_server(server)

        assert result.status == "error"
        assert result.error == "timeout"

    @pytest.mark.asyncio
    async def test_probe_server_config_fallback_on_error(self) -> None:
        """probe_server falls back to 15s when config loading fails."""
        server = McpServerInfo(name="test", command="echo")

        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("personalclaw.config.loader.AppConfig") as mock_cls:
            mock_cls.load.side_effect = RuntimeError("corrupt config")

            result = await probe_server(server)

        assert result.status == "error"
        assert result.error == "timeout"


class TestProbeRemoteTimeout:
    """Test that _probe_remote uses _get_probe_timeout() for HTTP timeout."""

    @pytest.mark.asyncio
    async def test_probe_remote_timeout_uses_config(self) -> None:
        """Remote probe uses _get_probe_timeout() for aiohttp timeout."""
        server = McpServerInfo(name="remote", url="https://example.com/mcp")

        with patch("personalclaw.config.loader.AppConfig") as mock_cls, \
             patch("aiohttp.ClientSession") as mock_session_cls:
            mock_cfg = MagicMock()
            mock_cfg.dashboard.mcp_probe_timeout_secs = 60
            mock_cls.load.return_value = mock_cfg

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(side_effect=asyncio.TimeoutError)
            mock_session_cls.return_value = mock_session

            result = await _probe_remote(server)

        assert result.status == "error"
        assert result.error == "timeout"
        # Verify the configured timeout was actually used
        timeout_used = mock_session_cls.call_args.kwargs.get("timeout")
        assert timeout_used is not None
        assert timeout_used.total == 60


class TestFixStaleManagedCommand:
    """Tests for _fix_stale_managed_command."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        import personalclaw.mcp_discovery as _d
        _d._resolved_managed_bin = None
        yield
        _d._resolved_managed_bin = None

    def test_resolves_to_running_binary_via_which(self):
        """Resolve managed-server command to the personalclaw binary on PATH."""
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        spec = {"command": "/stale/build/bin/personalclaw", "args": ["mcp-core"]}
        with patch("personalclaw.mcp_discovery.shutil.which", return_value="/runtime/bin/personalclaw"):
            _fix_stale_managed_command("personalclaw-core", spec)
        assert spec["command"] == "/runtime/bin/personalclaw"

    def test_no_resolution_leaves_command_unchanged(self):
        """When `personalclaw` is not on PATH, the stored command is left as-is."""
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        spec = {"command": "/old/personalclaw", "args": ["mcp-core"]}
        with patch("personalclaw.mcp_discovery.shutil.which", return_value=None):
            _fix_stale_managed_command("personalclaw-core", spec)
        assert spec["command"] == "/old/personalclaw"

    def test_skips_non_managed_server(self):
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        spec = {"command": "/nonexistent/path/other", "args": []}
        _fix_stale_managed_command("other-server", spec)
        assert spec["command"] == "/nonexistent/path/other"

    def test_always_re_resolves_to_running_binary(self, tmp_path):
        """Even if the stored path exists, re-resolve to the running binary."""
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        real = tmp_path / "personalclaw"
        real.write_text("#!/bin/sh")
        spec = {"command": str(real), "args": ["mcp-schedule"]}
        with patch("personalclaw.mcp_discovery.shutil.which", return_value="/new/path/personalclaw"):
            _fix_stale_managed_command("personalclaw-schedule", spec)
        assert spec["command"] == "/new/path/personalclaw"

    def test_no_change_when_already_correct(self):
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        spec = {"command": "/current/bin/personalclaw", "args": ["mcp-core"]}
        with patch("personalclaw.mcp_discovery.shutil.which", return_value="/current/bin/personalclaw"):
            _fix_stale_managed_command("personalclaw-core", spec)
        assert spec["command"] == "/current/bin/personalclaw"

    def test_resolution_is_cached_across_calls(self):
        """The resolved binary is cached so later managed servers reuse it."""
        from personalclaw.mcp_discovery import _fix_stale_managed_command

        spec1 = {"command": "/old/personalclaw", "args": ["mcp-core"]}
        spec2 = {"command": "/old/personalclaw", "args": ["mcp-schedule"]}
        with patch(
            "personalclaw.mcp_discovery.shutil.which", return_value="/resolved/personalclaw"
        ) as mock_which:
            _fix_stale_managed_command("personalclaw-core", spec1)
            _fix_stale_managed_command("personalclaw-schedule", spec2)
        assert spec1["command"] == "/resolved/personalclaw"
        assert spec2["command"] == "/resolved/personalclaw"
        # Second call uses the cache — which() is only invoked once.
        assert mock_which.call_count == 1


class TestSharedServerToolsRegistration:
    """Tests for shared MCP servers being added to tools/allowedTools."""

    def test_shared_servers_added_to_tools_and_allowedtools(self, tmp_path, monkeypatch) -> None:
        """Enabled shared servers appear in both tools and allowedTools."""
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)

        user_dir = tmp_path / ".personalclaw"
        (user_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "my-srv": {"command": "srv"},
        }}))

        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: "/usr/bin/srv")

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "my-srv" in data["mcpServers"]
        assert "@my-srv" in data.get("tools", [])
        assert "@my-srv" in data.get("allowedTools", [])

    def test_disabled_shared_server_removed_from_tools(self, tmp_path, monkeypatch) -> None:
        """Disabled shared server is removed from tools/allowedTools."""
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "personalclaw.json").write_text(json.dumps({
            "mcpServers": {"my-srv": {"command": "srv"}},
            "tools": ["@my-srv"],
            "allowedTools": ["@my-srv"],
        }))

        user_dir = tmp_path / ".personalclaw"
        (user_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "my-srv": {"command": "srv", "disabled": True},
        }}))

        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: "/usr/bin/srv")

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "@my-srv" not in data.get("tools", [])
        assert "@my-srv" not in data.get("allowedTools", [])

    def test_reenabled_server_added_back(self, tmp_path, monkeypatch) -> None:
        """Server re-enabled in mcp.json gets added back to tools/allowedTools."""
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "personalclaw.json").write_text(json.dumps({
            "mcpServers": {"my-srv": {"command": "srv", "disabled": True}},
            "tools": [],
            "allowedTools": [],
        }))

        user_dir = tmp_path / ".personalclaw"
        (user_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "my-srv": {"command": "srv"},
        }}))

        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: "/usr/bin/srv")

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "@my-srv" in data.get("tools", [])
        assert "@my-srv" in data.get("allowedTools", [])
        assert "disabled" not in data["mcpServers"]["my-srv"]

    def test_disabled_removal_no_tools_key(self, tmp_path, monkeypatch) -> None:
        """Disabled removal doesn't crash when config has no tools key."""
        from personalclaw.agent import rebuild_agent_config

        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        (agent_dir / "defaults.json").write_text(json.dumps({"name": "personalclaw"}))
        (agent_dir / "prompt.md").write_text("prompt")
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

        agents_dir = tmp_path / ".personalclaw" / "agents"
        agents_dir.mkdir(parents=True)

        user_dir = tmp_path / ".personalclaw"
        (user_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "disabled-srv": {"command": "srv", "disabled": True},
        }}))

        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        monkeypatch.setattr("personalclaw.agent._USER_DIR", user_dir)
        monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", "/usr/bin/personalclaw")
        monkeypatch.setattr("shutil.which", lambda cmd, path=None: None)

        rebuild_agent_config()

        data = json.loads((agents_dir / "personalclaw.json").read_text())
        assert "@disabled-srv" not in data.get("tools", [])
        assert "@disabled-srv" not in data.get("allowedTools", [])
