"""The mcp-tools settings card reads/writes ~/.personalclaw/mcp.json (#43).

Store reconcile: the generic ``mcp-tools`` multi-instance provider card no
longer writes the dead ``extensions/mcp-tools/instances/*.json`` store. Its
instance CRUD is repointed to ``~/.personalclaw/mcp.json`` — the ONE store the
native MCP client consumes — so a server added via the card is genuinely
spawnable by the native loop.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.config import loader as config_loader
from personalclaw.providers import mcp_instances as mi


@pytest.fixture(autouse=True)
def _home(monkeypatch):
    """The per-test home (conftest redirects it): the card writes its ``mcp.json`` through the
    real document writer, and a delete reaches both documents through the one delete, which
    resolves them there too — a card store pointed somewhere else would test a wiring
    production does not have."""
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    return config_loader.config_dir()


def _read(tmp_path) -> dict:
    return json.loads((tmp_path / "mcp.json").read_text())


def test_create_stdio_writes_mcp_json(_home):
    inst = mi.create_instance(
        "filesystem-mcp",
        {
            "transport": "stdio",
            "command": "npx",
            "args": "-y server-fs /tmp",
            "endpoint": "",
        },
    )
    assert inst.id == "filesystem-mcp"
    data = _read(_home)["mcpServers"]["filesystem-mcp"]
    assert data == {"command": "npx", "args": ["-y", "server-fs", "/tmp"]}


def test_create_sse_writes_url(_home):
    mi.create_instance(
        "remote", {"transport": "sse", "endpoint": "https://x/sse", "command": "", "args": ""}
    )
    assert _read(_home)["mcpServers"]["remote"] == {"type": "sse", "url": "https://x/sse"}


def test_create_rejects_bad_name(_home):
    with pytest.raises(ValueError):
        mi.create_instance("bad name!", {"transport": "stdio", "command": "npx"})


def test_create_duplicate_rejected(_home):
    mi.create_instance("dup", {"transport": "stdio", "command": "npx"})
    with pytest.raises(ValueError):
        mi.create_instance("dup", {"transport": "stdio", "command": "node"})


def test_list_maps_servers_to_instances(_home):
    (_home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "a": {"command": "npx", "args": ["x"]},
                    "b": {"url": "https://b/sse"},
                    "c": {"command": "node", "disabled": True},
                }
            }
        ),
        encoding="utf-8",
    )
    insts = {i.id: i for i in mi.list_instances()}
    assert insts["a"].config["transport"] == "stdio"
    assert insts["a"].config["args"] == "x"
    assert insts["b"].config["transport"] == "sse"
    assert insts["b"].config["endpoint"] == "https://b/sse"
    assert insts["c"].enabled is False  # disabled flag → not enabled


def test_update_preserves_env(_home):
    (_home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "s": {"command": "npx", "args": ["old"], "env": {"API_KEY": "secret"}},
                }
            }
        ),
        encoding="utf-8",
    )
    mi.update_instance(
        "s", config={"transport": "stdio", "command": "npx", "args": "new", "endpoint": ""}
    )
    from personalclaw.config.secret_refs import resolve_mcp_spec

    spec = _read(_home)["mcpServers"]["s"]
    assert spec["args"] == ["new"]
    # Preserved across the edit — as a reference in the file, the value in the credential store.
    assert spec["env"]["API_KEY"].startswith("{{secret:")
    assert resolve_mcp_spec("s", spec)["env"] == {"API_KEY": "secret"}


def test_update_toggle_enabled(_home):
    mi.create_instance("t", {"transport": "stdio", "command": "npx"})
    mi.update_instance("t", enabled=False)
    assert _read(_home)["mcpServers"]["t"]["disabled"] is True
    mi.update_instance("t", enabled=True)
    assert "disabled" not in _read(_home)["mcpServers"]["t"]


def test_delete(_home):
    mi.create_instance("d", {"transport": "stdio", "command": "npx"})
    assert mi.delete_instance("d") is True
    assert "d" not in _read(_home).get("mcpServers", {})
    assert mi.delete_instance("d") is False
