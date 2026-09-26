"""An MCP server's secrets live in the credential store; ``mcp.json`` holds references.

#3607 moved provider keys and app secrets behind ``{{secret:…}}`` references and disclosed the
rest in ``docs/security/limitations.md`` §6: an MCP server's ``env`` block (and a remote server's
``headers``) stayed in ``mcp.json`` exactly as typed, the rebuild copied it into
``agents/personalclaw.json``, and a snapshot and an export carried both — so Settings →
Portability's "Credentials are never included." was false for them.

Every value is stored unless the server marks a variable plain (``plainEnv``); the spawn resolves
the reference, so the server still receives the real value.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import tarfile
import textwrap
import zipfile

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.config.secret_refs import make_ref, migrate_plaintext_secrets, ref_key
from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs, mcp_sdk_available

TOKEN = "ghp_fixtureMcpServerToken0123456789abcdef"
OLD_TOKEN = "ghp_fixtureStaleAgentConfigCopy99887766"
HEADER_TOKEN = "Bearer fixture-remote-header-token-5a4b3c2d"

needs_sdk = pytest.mark.skipif(not mcp_sdk_available(), reason="requires the 'mcp' SDK extra")

# A stdio MCP server that reports its own environment: `read_env` returns a variable, and the one
# tool's DESCRIPTION carries a digest of GITHUB_TOKEN, so a probe (initialize + tools/list only)
# can see what the child received without the value appearing in a tool listing.
_ECHO_SERVER = textwrap.dedent("""
    import hashlib, os
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("echo")
    _seen = hashlib.sha256(os.environ.get("GITHUB_TOKEN", "").encode()).hexdigest()[:16]

    @mcp.tool(description=f"token digest {_seen}")
    def read_env(name: str) -> str:
        return os.environ.get(name, "<unset>")

    if __name__ == "__main__":
        mcp.run()
    """)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    home = config_loader.config_dir()
    monkeypatch.setattr("personalclaw.agent._USER_DIR", home)
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []})
    )
    return home


@pytest.fixture
def echo_server(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER)
    return str(script)


def _put(name: str, body: dict):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("PUT", f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _delete(name: str):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request("DELETE", f"/api/mcp/servers/{name}", match_info={"name": name})
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _prefix(server: str) -> str:
    from personalclaw.config.secret_refs import mcp_server_prefix

    return mcp_server_prefix(server)


def _mcp_keys(server: str) -> list[str]:
    return [k for k in credential_names() if k.startswith(_prefix(server))]


def _stored_ref(server: str, part: str, name: str, value: str) -> str:
    """The reference the product writes for ``server``'s ``part`` value ``name``: the value is
    stored under a key the SERVER owns, exactly as an edit through the MCP form stores it. A
    server resolves only its own keys (and core's), so a hand-built key would be refused."""
    from personalclaw.config.secret_refs import store_mcp_spec

    return store_mcp_spec(server, {part: {name: value}}, strict=True)[part][name]


async def _read_env(reg: McpClientRegistry, server: str, var: str) -> str:
    """Reload ``reg`` from mcp.json exactly as the native loop does, and ask ``server`` for
    ``var`` through its live connection."""
    reg.load_from_specs(_personalclaw_mcp_specs())
    conn = reg.get(server)
    assert conn is not None
    ok, out = await conn.call_tool("read_env", {"name": var})
    assert ok, out
    return out


async def _call_read_env(server: str, var: str) -> str:
    reg = McpClientRegistry()
    try:
        return await _read_env(reg, server, var)
    finally:
        await reg.shutdown_all()


# ── mcp.json holds only a reference ─────────────────────────────────────────


def test_a_server_added_in_the_ui_leaves_only_a_reference_in_mcp_json(home):
    body = {
        "command": "npx",
        "args": ["-y", "some-server"],
        "env": {"GITHUB_TOKEN": TOKEN, "LOG_LEVEL": "debug"},
        "plainEnv": ["LOG_LEVEL"],
    }
    assert _put("gh", body).status == 200

    spec = _read(home / "mcp.json")["mcpServers"]["gh"]
    key = ref_key(spec["env"]["GITHUB_TOKEN"])
    assert key is not None and key.startswith(_prefix("gh")), spec
    assert TOKEN not in (home / "mcp.json").read_text()
    # The plain value is a setting: it stays readable, and travels with an export.
    assert spec["env"]["LOG_LEVEL"] == "debug"
    assert spec["plainEnv"] == ["LOG_LEVEL"]
    assert get_credential(key) == TOKEN
    # The enable path copies the spec into the agent config — as the reference.
    agent_spec = _read(home / "agents" / "personalclaw.json")["mcpServers"]["gh"]
    assert agent_spec["env"]["GITHUB_TOKEN"] == spec["env"]["GITHUB_TOKEN"]
    assert TOKEN not in (home / "agents" / "personalclaw.json").read_text()


def test_a_variable_marked_plain_is_still_stored_when_its_name_is_a_credential(home):
    body = {"command": "npx", "env": {"API_KEY": TOKEN}, "plainEnv": ["API_KEY"]}
    assert _put("floor", body).status == 200
    spec = _read(home / "mcp.json")["mcpServers"]["floor"]
    assert ref_key(spec["env"]["API_KEY"]) is not None
    assert TOKEN not in (home / "mcp.json").read_text()


def test_a_value_no_credential_can_hold_is_refused_and_nothing_is_written(home):
    # A multi-line value is stored now (`test_multiline_secret_is_kept_in_the_credential_store`);
    # NUL is the one character refused, and refused before anything is stored.
    resp = _put("nul", {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN, "BAD": "a\x00b"}})
    assert resp.status == 400
    assert "NUL" in json.loads(resp.body)["error"]["message"]
    assert not (home / "mcp.json").exists()
    assert _mcp_keys("nul") == []


def test_removing_a_server_deletes_its_stored_secrets(home):
    assert _put("gone", {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN}}).status == 200
    assert _mcp_keys("gone")
    assert _delete("gone").status == 200
    assert _mcp_keys("gone") == []
    assert TOKEN not in (home / ".env").read_text()


# ── the spawned server receives the real value ──────────────────────────────


@needs_sdk
def test_the_spawned_server_receives_the_value_behind_the_reference(home, echo_server):
    ref = _stored_ref("echo", "env", "GITHUB_TOKEN", TOKEN)
    (home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo": {
                        "command": sys.executable,
                        "args": [echo_server],
                        "env": {
                            "GITHUB_TOKEN": ref,
                            "PERSONALCLAW_HOME": str(home),
                        },
                    }
                }
            }
        )
    )
    assert asyncio.run(_call_read_env("echo", "GITHUB_TOKEN")) == TOKEN


@needs_sdk
def test_a_server_added_through_the_api_starts_with_its_token(home, echo_server):
    body = {
        "command": sys.executable,
        "args": [echo_server],
        "env": {"GITHUB_TOKEN": TOKEN, "PERSONALCLAW_HOME": str(home)},
        "plainEnv": ["PERSONALCLAW_HOME"],
    }
    assert _put("echo", body).status == 200
    assert TOKEN not in (home / "mcp.json").read_text()

    async def run() -> tuple[str, str]:
        reg = McpClientRegistry()
        try:
            first = await _read_env(reg, "echo", "GITHUB_TOKEN")
            # Rotation: a new value behind the SAME reference. The live connection is keyed by
            # the spec's resolved content, so the next reload drops it and spawns afresh.
            rotated = {**body, "env": {**body["env"], "GITHUB_TOKEN": OLD_TOKEN}}
            assert (await asyncio.to_thread(_put, "echo", rotated)).status == 200
            return first, await _read_env(reg, "echo", "GITHUB_TOKEN")
        finally:
            await reg.shutdown_all()

    first, after_rotation = asyncio.run(run())
    assert first == TOKEN
    assert after_rotation == OLD_TOKEN
    assert OLD_TOKEN not in (home / "mcp.json").read_text()


@needs_sdk
def test_the_discovery_probe_spawns_with_the_resolved_value(home, echo_server):
    from personalclaw.mcp_discovery import McpServerInfo, probe_server

    server = McpServerInfo(
        name="echo",
        command=sys.executable,
        args=[echo_server],
        env={
            "GITHUB_TOKEN": _stored_ref("echo", "env", "GITHUB_TOKEN", TOKEN),
            "PERSONALCLAW_HOME": str(home),
        },
    )
    probed = asyncio.run(probe_server(server))
    assert probed.status == "ok", probed.error
    [tool] = probed.tools
    assert tool["description"] == f"token digest {_digest(TOKEN)}"


# ── snapshots and exports carry no value ────────────────────────────────────


def _members_of_snapshot(tmp_path) -> dict[str, bytes]:
    from personalclaw.snapshot import snapshot_main

    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = list(out.glob("personalclaw-snapshot-*.tar.gz"))
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar.getmembers():
            if info.isfile():
                members[info.name] = tar.extractfile(info).read()
    return members


def _members_of_export() -> dict[str, bytes]:
    from personalclaw.portability import create_export_zip

    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}


def test_a_snapshot_and_an_export_carry_no_mcp_secret(home, tmp_path):
    assert _put("gh", {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN}}).status == 200

    for members in (_members_of_snapshot(tmp_path), _members_of_export()):
        assert [n for n, data in members.items() if TOKEN.encode() in data] == []
        # mcp.json still travels — as the reference a restore resolves on this machine.
        mcp_member = next(n for n in members if n.endswith("mcp.json"))
        assert b"{{secret:PCSECRET_MCP_" in members[mcp_member]


# ── the one-time move at boot ───────────────────────────────────────────────


def test_the_boot_move_converts_plaintext_and_keeps_the_live_value(home):
    (home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gh": {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN}},
                    "remote": {
                        "url": "https://mcp.invalid/x",
                        "headers": {"Authorization": HEADER_TOKEN},
                    },
                }
            }
        )
    )
    # The agent config's copy is OLDER — an earlier release copied it before a rotation.
    (home / "agents" / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {"gh": {"command": "npx", "env": {"GITHUB_TOKEN": OLD_TOKEN}}}})
    )

    moved = migrate_plaintext_secrets()
    assert "mcp.json" in moved and "agents/personalclaw.json" in moved

    live = _read(home / "mcp.json")["mcpServers"]
    copy = _read(home / "agents" / "personalclaw.json")["mcpServers"]
    gh_ref = live["gh"]["env"]["GITHUB_TOKEN"]
    assert ref_key(gh_ref) and ref_key(live["remote"]["headers"]["Authorization"])
    # The copy points at mcp.json's key; its stale value is neither stored nor kept.
    assert copy["gh"]["env"]["GITHUB_TOKEN"] == gh_ref
    assert get_credential(ref_key(gh_ref)) == TOKEN
    everything = "".join(
        p.read_text() for p in (home / "mcp.json", home / "agents" / "personalclaw.json")
    )
    for secret in (TOKEN, OLD_TOKEN, HEADER_TOKEN):
        assert secret not in everything
    assert OLD_TOKEN not in (home / ".env").read_text()
    # Idempotent: nothing left to move.
    assert migrate_plaintext_secrets() == []


@needs_sdk
def test_a_server_moved_at_boot_still_starts_with_its_token(home, echo_server):
    (home / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo": {
                        "command": sys.executable,
                        "args": [echo_server],
                        "env": {"GITHUB_TOKEN": TOKEN, "PERSONALCLAW_HOME": str(home)},
                    }
                }
            }
        )
    )
    migrate_plaintext_secrets()
    assert TOKEN not in (home / "mcp.json").read_text()
    assert asyncio.run(_call_read_env("echo", "GITHUB_TOKEN")) == TOKEN


def test_a_value_no_credential_can_hold_stays_inline_at_boot_and_the_rest_still_moves(home):
    bad = "abc\x00def"
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"g": {"command": "x", "env": {"BAD": bad, "API": TOKEN}}}})
    )
    migrate_plaintext_secrets()
    env = _read(home / "mcp.json")["mcpServers"]["g"]["env"]
    assert env["BAD"] == bad
    assert ref_key(env["API"]) and TOKEN not in (home / "mcp.json").read_text()


# ── every other writer of mcp.json ──────────────────────────────────────────


def test_importing_a_claude_code_server_stores_its_values(home):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    cc_spec = {"command": "npx", "env": {"NOTION_INTEGRATION": TOKEN}}
    assert mcp_mod._set_personalclaw_entry("notion", enabled=True, spec=cc_spec) == "added"
    spec = _read(home / "mcp.json")["mcpServers"]["notion"]
    assert ref_key(spec["env"]["NOTION_INTEGRATION"])
    assert TOKEN not in (home / "mcp.json").read_text()


def test_the_claude_code_scope_gets_the_value_and_the_implicit_copy_does_not(
    home, tmp_path, monkeypatch
):
    from personalclaw.dashboard.handlers import mcp as mcp_mod
    from personalclaw.mcp_discovery import discover_servers_to_sync, register_servers_for_cc

    cc_json = tmp_path / "claude.json"
    monkeypatch.setattr(mcp_mod, "_cc_global_json", lambda: cc_json)
    body = {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN, "LOG_LEVEL": "info"}}
    assert _put("gh", {**body, "plainEnv": ["LOG_LEVEL"]}).status == 200

    # Putting a server into Claude Code's own scope hands it over: that tool reads only its own
    # file, so the value goes with it, in its form.
    spec = mcp_mod._find_server_spec_anywhere("gh")
    assert mcp_mod._set_scope_entry(cc_json, "gh", enabled=True, spec=spec) == "added"
    cc = _read(cc_json)["mcpServers"]["gh"]
    assert cc["env"] == {"GITHUB_TOKEN": TOKEN, "LOG_LEVEL": "info"}
    assert "plainEnv" not in cc

    # The copy nobody asked for — `~/.mcp.json`, at the umask mode — carries plain values only.
    (home / "agents" / "personalclaw.json").write_text(json.dumps({"mcpServers": {}}))
    dot_mcp = tmp_path / "dot.mcp.json"
    register_servers_for_cc(discover_servers_to_sync(), mcp_json_path=dot_mcp)
    assert _read(dot_mcp)["mcpServers"]["gh"]["env"] == {"LOG_LEVEL": "info"}
    assert TOKEN not in dot_mcp.read_text()


def test_a_pack_connector_references_its_credential_and_the_spawn_resolves_it(
    monkeypatch, tmp_path
):
    from personalclaw.packs.connectors import resolve_connector, seed_catalog

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    seed_catalog(home)
    resolve_connector(
        {"name": "web-search", "category": "search"},
        mode="configure",
        credentials={"SEARCH_API_KEY": TOKEN},
        home=home,
    )
    spec = _read(home / "mcp.json")["mcpServers"]["web-search"]
    assert spec["env"]["SEARCH_API_KEY"] == make_ref("SEARCH_API_KEY")
    # It was the literal "${SEARCH_API_KEY}", which no spawn expanded.
    assert _personalclaw_mcp_specs()["web-search"]["env"]["SEARCH_API_KEY"] == TOKEN


def test_the_provider_card_edit_keeps_the_reference_and_the_plain_marking(home):
    from personalclaw.providers import mcp_instances

    body = {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN, "LOG_LEVEL": "info"}}
    assert _put("gh", {**body, "plainEnv": ["LOG_LEVEL"]}).status == 200
    before = _read(home / "mcp.json")["mcpServers"]["gh"]

    mcp_instances.update_instance("gh", config={"transport": "stdio", "command": "uvx", "args": ""})
    after = _read(home / "mcp.json")["mcpServers"]["gh"]
    assert after["command"] == "uvx"
    assert after["env"] == before["env"] and after["plainEnv"] == ["LOG_LEVEL"]
    assert TOKEN not in (home / "mcp.json").read_text()


def test_the_agent_config_editor_cannot_write_a_plaintext_env_value(home, monkeypatch):
    from personalclaw.dashboard.handlers import agents as agents_mod

    async def _no_reset(_request):
        return 0

    monkeypatch.setattr("personalclaw.dashboard.handlers._reset_all_sessions", _no_reset)
    config = {"mcpServers": {"gh": {"command": "npx", "env": {"GITHUB_TOKEN": TOKEN}}}}
    req = make_mocked_request("PUT", "/api/agent/config")

    async def _json():
        return {"config": config}

    req.json = _json
    assert asyncio.run(agents_mod.api_agent_config(req)).status == 200
    agent_config = home / "agents" / "personalclaw.json"
    assert TOKEN not in agent_config.read_text()
    assert ref_key(_read(agent_config)["mcpServers"]["gh"]["env"]["GITHUB_TOKEN"])
