"""App-provided MCP servers register into the live MCP config (#31).

An app that ships its own MCP server (manifest.mcpServers) must have it wired
into ~/.personalclaw/mcp.json on install/enable (namespaced {app}:{server}) and
removed on disable/uninstall — so the server is actually reachable, not just
declared.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager, mcp_bridge


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_bridge, "config_dir", lambda: tmp_path)
    return tmp_path


def _app(tmp_path: Path, name: str, *, subdir="src", servers=None) -> Path:
    d = tmp_path / subdir / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if servers:
        mani["mcpServers"] = servers
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    return d


def _live_servers(tmp_path):
    p = tmp_path / "mcp.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text()).get("mcpServers", {})


def test_install_registers_mcp_servers(tmp_path):
    app_manager.install(
        _app(
            tmp_path,
            "notesync",
            servers={
                "gdrive": {"command": "gdrive-mcp", "args": []},
            },
        )
    )
    servers = _live_servers(tmp_path)
    assert "notesync:gdrive" in servers
    assert servers["notesync:gdrive"]["command"] == "gdrive-mcp"


def test_disable_enable_toggles_mcp(tmp_path):
    app_manager.install(_app(tmp_path, "notesync", servers={"gdrive": {"url": "http://x"}}))
    assert mcp_bridge.app_mcp_server_keys("notesync") == ["notesync:gdrive"]
    app_manager.disable("notesync")
    assert mcp_bridge.app_mcp_server_keys("notesync") == []
    app_manager.enable("notesync")
    assert mcp_bridge.app_mcp_server_keys("notesync") == ["notesync:gdrive"]


def test_uninstall_removes_mcp(tmp_path):
    app_manager.install(_app(tmp_path, "notesync", servers={"gdrive": {"url": "http://x"}}))
    app_manager.uninstall("notesync")
    assert mcp_bridge.app_mcp_server_keys("notesync") == []


def test_namespacing_no_collision(tmp_path):
    # Two apps each ship a server named "gdrive" — both coexist, namespaced.
    app_manager.install(_app(tmp_path, "app-a", servers={"gdrive": {"url": "http://a"}}))
    app_manager.install(
        _app(tmp_path, "app-b", subdir="s2", servers={"gdrive": {"url": "http://b"}})
    )
    servers = _live_servers(tmp_path)
    assert "app-a:gdrive" in servers and "app-b:gdrive" in servers
    assert servers["app-a:gdrive"]["url"] == "http://a"
    assert servers["app-b:gdrive"]["url"] == "http://b"
    # uninstalling one leaves the other
    app_manager.uninstall("app-a")
    servers = _live_servers(tmp_path)
    assert "app-a:gdrive" not in servers and "app-b:gdrive" in servers


def test_no_mcp_servers_is_noop(tmp_path):
    app_manager.install(_app(tmp_path, "plain"))
    assert mcp_bridge.app_mcp_server_keys("plain") == []


def test_stdio_server_gets_app_dir_cwd(tmp_path):
    # A stdio server shipped INSIDE the app (relative command/args) must be
    # registered with cwd=<app dir> so the MCP client can actually spawn it —
    # the client doesn't chdir per server, so a relative path otherwise resolves
    # against the gateway cwd and never starts.
    app_manager.install(
        _app(
            tmp_path,
            "selfhosted",
            servers={
                "local": {"command": "python3", "args": ["backend/mcp_server.py"]},
            },
        )
    )
    servers = _live_servers(tmp_path)
    spec = servers["selfhosted:local"]
    assert spec["cwd"] == str(manager.app_dir("selfhosted"))
    assert spec["command"] == "python3"


def test_remote_and_absolute_cwd_servers_untouched(tmp_path):
    # A url (remote) server gets no cwd; a server that already set an absolute
    # cwd keeps its own.
    app_manager.install(
        _app(
            tmp_path,
            "mixed",
            servers={
                "remote": {"url": "http://x"},
                "pinned": {"command": "foo", "cwd": "/opt/custom"},
            },
        )
    )
    servers = _live_servers(tmp_path)
    assert "cwd" not in servers["mixed:remote"]
    assert servers["mixed:pinned"]["cwd"] == "/opt/custom"
