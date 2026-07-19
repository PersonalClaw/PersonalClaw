"""MCP import-suggestions discovery (#43).

PersonalClaw no longer silently reads ``~/.claude.json`` as an MCP discovery
source — a Claude-Code-only server isn't reachable by the native loop. Instead
those servers are offered as explicit *import suggestions* via
``discover_importable_servers`` + ``GET /api/mcp/importable``; importing one
copies its spec into ``~/.personalclaw/mcp.json`` (the store the native client
reads) through ``/api/mcp/apply``.
"""

from __future__ import annotations

import json

import personalclaw.mcp_discovery as disc


def _write(path, servers: dict) -> None:
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_claude_json_not_a_silent_discovery_source(tmp_path, monkeypatch):
    """A server living only in ~/.claude.json must NOT appear in list_servers()."""
    cc = tmp_path / ".claude.json"
    _write(cc, {"cc-only": {"command": "npx", "args": ["cc-mcp"]}})
    # Only PClaw scopes are discovery sources now (no claude.json).
    monkeypatch.setattr(disc, "_MCP_JSON_PATHS", (tmp_path / "mcp.json",))
    monkeypatch.setattr(disc, "_load_agent_config", lambda: {})
    names = {s.name for s in disc.list_servers()}
    assert "cc-only" not in names


def test_discover_importable_returns_cc_servers_not_in_pclaw(tmp_path, monkeypatch):
    cc = tmp_path / ".claude.json"
    _write(cc, {
        "cc-only": {"command": "npx", "args": ["cc-mcp"]},
        "remote": {"url": "https://example.com/sse"},
        "bogus": {"description": "no command or url"},
    })
    monkeypatch.setattr(disc, "_IMPORT_JSON_PATHS", ((cc, "Claude Code"),))
    # No PClaw-scope servers configured.
    monkeypatch.setattr(disc, "_MCP_JSON_PATHS", (tmp_path / "nope.json",))
    monkeypatch.setattr(disc, "_load_agent_config", lambda: {})

    out = disc.discover_importable_servers()
    by_name = {s["name"]: s for s in out}
    assert set(by_name) == {"cc-only", "remote"}  # bogus dropped (no command/url)
    assert by_name["cc-only"]["backend"] == "Claude Code"
    assert by_name["cc-only"]["command"] == "npx"
    assert by_name["remote"]["url"] == "https://example.com/sse"


def test_discover_importable_excludes_already_known(tmp_path, monkeypatch):
    """A server already in PClaw scope is not offered as importable."""
    cc = tmp_path / ".claude.json"
    _write(cc, {"shared": {"command": "npx"}})
    pclaw = tmp_path / "mcp.json"
    _write(pclaw, {"shared": {"command": "npx"}})
    monkeypatch.setattr(disc, "_IMPORT_JSON_PATHS", ((cc, "Claude Code"),))
    monkeypatch.setattr(disc, "_MCP_JSON_PATHS", (pclaw,))
    monkeypatch.setattr(disc, "_load_agent_config", lambda: {})

    assert disc.discover_importable_servers() == []


def test_discover_importable_no_source_file(tmp_path, monkeypatch):
    monkeypatch.setattr(disc, "_IMPORT_JSON_PATHS", ((tmp_path / "absent.json", "Claude Code"),))
    monkeypatch.setattr(disc, "_MCP_JSON_PATHS", (tmp_path / "nope.json",))
    monkeypatch.setattr(disc, "_load_agent_config", lambda: {})
    assert disc.discover_importable_servers() == []
