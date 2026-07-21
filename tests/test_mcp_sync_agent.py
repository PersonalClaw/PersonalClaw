"""Tests for _sync_mcp_to_agent and _sync_mcp_to_agent_batch in mcp.py."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def mcp_env(tmp_path: Path):
    """Set up agent config and global mcp.json in tmp_path."""
    agent_cfg = tmp_path / "personalclaw.json"
    mcp_json = tmp_path / "mcp.json"

    agent_cfg.write_text(
        json.dumps(
            {
                "name": "personalclaw",
                "mcpServers": {"my-mcp-server": {"command": "my-mcp-server"}},
                "tools": ["@my-mcp-server"],
                "allowedTools": ["@my-mcp-server"],
            }
        )
    )
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "my-mcp-server": {"command": "my-mcp-server"},
                    "generic-mcp": {"command": "generic-mcp", "args": []},
                    "email-mcp": {"command": "email-mcp", "env": {"WRITES": "true"}},
                }
            }
        )
    )

    with (
        patch("personalclaw.dashboard.handlers.mcp._GLOBAL_MCP_JSON", mcp_json),
        patch(
            "personalclaw.dashboard.handlers.agents._installed_agent_config", return_value=agent_cfg
        ),
    ):
        yield agent_cfg, mcp_json


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


class TestSyncMcpToAgent:
    def test_enable_adds_server_and_tool_refs(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("generic-mcp", enabled=True)
        cfg = _load(agent_cfg)
        assert "generic-mcp" in cfg["mcpServers"]
        assert "@generic-mcp" in cfg["tools"]
        assert "@generic-mcp" in cfg["allowedTools"]

    def test_enable_preserves_existing_server(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("my-mcp-server", enabled=True)
        cfg = _load(agent_cfg)
        assert cfg["mcpServers"]["my-mcp-server"] == {"command": "my-mcp-server"}

    def test_enable_strips_disabled_key(self, mcp_env):
        agent_cfg, mcp_json = mcp_env
        d = json.loads(mcp_json.read_text())
        d["mcpServers"]["generic-mcp"]["disabled"] = True
        mcp_json.write_text(json.dumps(d))
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("generic-mcp", enabled=True)
        cfg = _load(agent_cfg)
        assert "disabled" not in cfg["mcpServers"]["generic-mcp"]

    def test_enable_noop_when_already_present(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("my-mcp-server", enabled=True)
        cfg = _load(agent_cfg)
        assert cfg["tools"].count("@my-mcp-server") == 1

    def test_disable_removes_tool_refs(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("my-mcp-server", enabled=False)
        cfg = _load(agent_cfg)
        assert "@my-mcp-server" not in cfg["tools"]
        assert "@my-mcp-server" not in cfg["allowedTools"]

    def test_remove_deletes_server_entry(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("my-mcp-server", enabled=False, remove=True)
        cfg = _load(agent_cfg)
        assert "my-mcp-server" not in cfg["mcpServers"]

    def test_enable_returns_early_on_missing_mcp_json(self, mcp_env):
        agent_cfg, mcp_json = mcp_env
        mcp_json.unlink()
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent

        _sync_mcp_to_agent("generic-mcp", enabled=True)
        cfg = _load(agent_cfg)
        assert "generic-mcp" not in cfg.get("mcpServers", {})


class TestSyncMcpToAgentBatch:
    def test_enable_adds_multiple_servers(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent_batch

        _sync_mcp_to_agent_batch(["generic-mcp", "email-mcp"], enabled=True)
        cfg = _load(agent_cfg)
        assert "generic-mcp" in cfg["mcpServers"]
        assert "email-mcp" in cfg["mcpServers"]
        assert "@generic-mcp" in cfg["tools"]
        assert "@email-mcp" in cfg["allowedTools"]

    def test_disable_removes_multiple_tool_refs(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent_batch

        _sync_mcp_to_agent_batch(["my-mcp-server"], enabled=False)
        cfg = _load(agent_cfg)
        assert "@my-mcp-server" not in cfg["tools"]

    def test_enable_with_missing_mcp_json_still_adds_tool_refs(self, mcp_env):
        """Post #15 fix: existing servers get tool refs even when mcp.json missing."""
        agent_cfg, mcp_json = mcp_env
        mcp_json.unlink()
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent_batch

        _sync_mcp_to_agent_batch(["my-mcp-server"], enabled=True)
        cfg = _load(agent_cfg)
        # my-mcp-server already in mcpServers, should still get tool ref
        assert "@my-mcp-server" in cfg["tools"]

    def test_enable_skips_invalid_spec(self, mcp_env):
        agent_cfg, mcp_json = mcp_env
        d = json.loads(mcp_json.read_text())
        d["mcpServers"]["bad-server"] = "not-a-dict"
        mcp_json.write_text(json.dumps(d))
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent_batch

        _sync_mcp_to_agent_batch(["bad-server"], enabled=True)
        cfg = _load(agent_cfg)
        assert "bad-server" not in cfg["mcpServers"]

    def test_noop_returns_without_write(self, mcp_env):
        agent_cfg, _ = mcp_env
        from personalclaw.dashboard.handlers.mcp import _sync_mcp_to_agent_batch

        _sync_mcp_to_agent_batch(["my-mcp-server"], enabled=True)
        cfg = _load(agent_cfg)
        assert "@my-mcp-server" in cfg["tools"]
