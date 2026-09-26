"""Tests for the MCP-config apply handlers — per-scope entry writes and name validation.

Covers ``_set_personalclaw_entry`` / ``_set_scope_entry`` (writing an MCP server
entry into PersonalClaw's mcp.json or Claude Code's config), the ``/api/mcp/apply``
endpoint, and rejection of hostile server names (path traversal, argv/shell
injection, length cap).
"""

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web


def _make_request(body: dict) -> MagicMock:
    """Build a fake aiohttp request for the api_mcp_apply handler."""
    state = MagicMock()
    state._background_tasks = set()
    request = MagicMock(spec=web.Request)
    request.app = {"state": state}

    async def _json() -> dict:
        return body

    request.json = _json
    return request


# ---------------------------------------------------------------------------
# Scope helpers: _set_personalclaw_entry, _set_scope_entry
# ---------------------------------------------------------------------------


class TestSetPersonalclawEntry:
    def test_adds_entry_when_enabling_with_spec(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "personalclaw.mcp.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_personalclaw_entry("srv", enabled=True, spec={"command": "x"})
        assert action == "added"
        assert json.loads(mc_path.read_text())["mcpServers"]["srv"] == {"command": "x"}

    def test_disables_existing(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "personalclaw.mcp.json"
        mc_path.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_personalclaw_entry("srv", enabled=False)
        assert action == "disabled"
        assert json.loads(mc_path.read_text())["mcpServers"]["srv"]["disabled"] is True

    def test_enabling_disabled_removes_flag(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "personalclaw.mcp.json"
        mc_path.write_text(json.dumps({"mcpServers": {"srv": {"command": "x", "disabled": True}}}))
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_personalclaw_entry("srv", enabled=True)
        assert action == "enabled"
        assert "disabled" not in json.loads(mc_path.read_text())["mcpServers"]["srv"]

    def test_disabling_missing_with_spec_seeds_entry(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "personalclaw.mcp.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_personalclaw_entry("srv", enabled=False, spec={"command": "x"})
        assert action == "disabled"
        entry = json.loads(mc_path.read_text())["mcpServers"]["srv"]
        assert entry == {"command": "x", "disabled": True}


class TestSetScopeEntry:
    def test_adds_when_enabling_absent(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "claude.json"
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=True, spec={"command": "c"})
        assert action == "added"
        assert json.loads(cfg_path.read_text())["mcpServers"]["srv"] == {"command": "c"}

    def test_removes_when_disabling_present(self, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "claude.json"
        cfg_path.write_text(
            json.dumps({"mcpServers": {"srv": {"command": "c"}, "other": {"command": "y"}}})
        )
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=False)
        assert action == "removed"
        servers = json.loads(cfg_path.read_text())["mcpServers"]
        assert "srv" not in servers
        assert "other" in servers  # untouched

    def test_enabling_already_present_noop(self, tmp_path):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "claude.json"
        cfg_path.write_text(json.dumps({"mcpServers": {"srv": {"command": "c"}}}))
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=True, spec={"command": "c"})
        assert action == "noop"

    def test_disabling_absent_noop(self, tmp_path):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "claude.json"
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=False)
        assert action == "noop"

    def test_enabling_without_spec_missing(self, tmp_path, monkeypatch):
        """When no spec can be found anywhere, the helper returns missing_spec."""
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "claude.json"
        monkeypatch.setattr(mcp_mod, "_find_server_spec_anywhere", lambda name: None)
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=True)
        assert action == "missing_spec"
        assert not cfg_path.exists()


# ---------------------------------------------------------------------------
# api_mcp_apply: preservation rule + batched writes
# ---------------------------------------------------------------------------


class _NoLock:
    async def __aenter__(self):
        pass

    async def __aexit__(self, *a):
        pass


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    """PersonalClaw's mcp.json and Claude Code's config, the two scopes apply writes — and
    nothing else: there is no third file for a scope name to point at."""
    import personalclaw.agent
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    mc_path = tmp_path / "personalclaw.mcp.json"
    cc_path = tmp_path / "cc_global.json"
    monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
    monkeypatch.setattr(mcp_mod, "_cc_global_json", lambda: cc_path)
    monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())
    rebuild = MagicMock()
    monkeypatch.setattr(personalclaw.agent, "rebuild_agent_config", rebuild)
    return mc_path, cc_path, rebuild


class TestApplyEndpoint:
    @pytest.mark.asyncio
    async def test_import_from_claude_code_copies_spec_into_pclaw(self, scopes):
        """The Tools-page Import action (personalclaw=True + ccGlobal=True) copies a
        Claude-Code server's spec into ~/.personalclaw/mcp.json while leaving the
        Claude Code entry intact — so the native loop can run it."""
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path, cc_path, _ = scopes
        # Server exists ONLY in Claude Code's config.
        cc_path.write_text(
            json.dumps({"mcpServers": {"cc-srv": {"command": "npx", "args": ["cc-mcp"]}}})
        )

        request = _make_request(
            {"changes": [{"name": "cc-srv", "personalclaw": True, "ccGlobal": True}]}
        )
        body = json.loads((await mcp_mod.api_mcp_apply(request)).body)
        assert body["ok"] is True
        assert "error" not in body["results"][0], body

        # PClaw scope now owns a runnable copy of the spec.
        pc = json.loads(mc_path.read_text())["mcpServers"]
        assert pc["cc-srv"]["command"] == "npx"
        assert pc["cc-srv"].get("disabled") is not True
        # Claude Code entry left intact (import is additive, not a move).
        cc = json.loads(cc_path.read_text())["mcpServers"]
        assert "cc-srv" in cc

    @pytest.mark.asyncio
    async def test_a_change_without_ccGlobal_leaves_claude_codes_file_alone(self, scopes):
        """``ccGlobal`` absent used to mean "remove from ~/.claude.json", so an import sent
        without it MOVED the server out of the user's Claude Code config."""
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path, cc_path, _ = scopes
        cc_path.write_text(
            json.dumps({"mcpServers": {"cc-srv": {"command": "npx"}, "other": {"command": "y"}}})
        )
        before = cc_path.read_bytes()
        request = _make_request({"changes": [{"name": "cc-srv", "personalclaw": True}]})
        body = json.loads((await mcp_mod.api_mcp_apply(request)).body)
        assert "ccGlobal" not in body["results"][0]["actions"]
        assert cc_path.read_bytes() == before, "apply rewrote Claude Code's file uninvited"
        assert "cc-srv" in json.loads(mc_path.read_text())["mcpServers"]

    @pytest.mark.asyncio
    async def test_a_server_found_nowhere_is_an_error_not_a_silent_noop(self, scopes):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path, _, _ = scopes
        request = _make_request({"changes": [{"name": "ghost", "personalclaw": True}]})
        body = json.loads((await mcp_mod.api_mcp_apply(request)).body)
        assert body["results"][0]["error"] == "No MCP server named 'ghost' was found to add."
        assert not mc_path.exists()

    @pytest.mark.asyncio
    async def test_an_uninstall_change_is_refused_and_touches_nothing(self, scopes):
        """``uninstall`` was the fourth of four delete paths (it also deleted the server from
        Claude Code's own file). Ignored, ``personalclaw`` would default on and ADD the server."""
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path, cc_path, _ = scopes
        mc_path.write_text(json.dumps({"mcpServers": {"foo": {"command": "f"}}}))
        cc_path.write_text(json.dumps({"mcpServers": {"foo": {"command": "f"}}}))
        before = (mc_path.read_bytes(), cc_path.read_bytes())
        request = _make_request({"changes": [{"name": "foo", "uninstall": True}]})
        body = json.loads((await mcp_mod.api_mcp_apply(request)).body)
        assert "DELETE /api/mcp/servers/{name}" in body["results"][0]["error"]
        assert (mc_path.read_bytes(), cc_path.read_bytes()) == before

    @pytest.mark.asyncio
    async def test_calls_rebuild_agent_config_once(self, scopes, monkeypatch):
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        _, _, rebuild = scopes
        monkeypatch.setattr(mcp_mod, "_find_server_spec_anywhere", lambda n: {"command": "x"})
        request = _make_request(
            {
                "changes": [
                    {"name": "a", "personalclaw": True},
                    {"name": "b", "personalclaw": True, "ccGlobal": True},
                    {"name": "c", "personalclaw": False},
                ]
            }
        )
        body = json.loads((await mcp_mod.api_mcp_apply(request)).body)
        assert body["ok"] is True
        assert body["applied"] == 3
        assert rebuild.call_count == 1  # rebuild called ONCE after all edits
        assert body["rebuild"]["ok"] is True


# ---------------------------------------------------------------------------
# Name validation: malicious / malformed server names are rejected before any
# scope mutation or subprocess call.  These lock the _is_valid_mcp_name
# contract in so a future regex change can't silently weaken it.
# ---------------------------------------------------------------------------


class TestHostileNameRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_name",
        [
            "../../etc/passwd",  # classic path traversal
            "./local",  # leading . (not alphanumeric)
            "/abs/path",  # leading / (not alphanumeric)
            "-rf",  # leading dash looks like an argv flag
            "a b",  # whitespace — shouldn't smuggle into argv
            "a\nb",  # newline injection
            "a;rm -rf /",  # command-sep chars
            "a|whoami",  # pipe
            "$(echo pwn)",  # command substitution shape
            "`echo pwn`",  # backtick command substitution
            "a\x00b",  # NUL byte
            "",  # empty
            "a" * 200,  # too long (> _MAX_MCP_NAME_LEN = 128)
            "foo/../bar",  # embedded .. even with alphanumerics around
        ],
    )
    async def test_rejects_hostile_names(self, tmp_path, monkeypatch, bad_name):
        """Each hostile name should short-circuit with ``error: invalid name``.

        The scope files must NOT be created/touched.
        """
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "mc.json"
        cc_path = tmp_path / "cc.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_cc_global_json", lambda: cc_path)

        import personalclaw.agent

        rebuild = MagicMock()
        monkeypatch.setattr(personalclaw.agent, "rebuild_agent_config", rebuild)
        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request({"changes": [{"name": bad_name, "personalclaw": True}]})
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)

        assert body["ok"] is True
        assert len(body["results"]) == 1
        # Either "invalid name" (regex/len reject) or "empty name" (empty string).
        err = body["results"][0].get("error", "")
        assert err in {
            "invalid name",
            "empty name",
        }, f"expected invalid/empty name error for {bad_name!r}, got {body['results'][0]}"
        # No file was created by the scope helpers.
        assert not mc_path.exists()
        assert not cc_path.exists()

    @pytest.mark.asyncio
    async def test_hostile_tool_name_filtered_server_kept(self, tmp_path, monkeypatch):
        """Invalid tool-override names are dropped; the server's scope
        changes still apply, and the handler reports ``tools_rejected``.
        """
        from personalclaw.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "mc.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_cc_global_json", lambda: tmp_path / "cc.json")
        monkeypatch.setattr(mcp_mod, "_find_server_spec_anywhere", lambda n: {"command": "x"})

        import personalclaw.agent

        monkeypatch.setattr(personalclaw.agent, "rebuild_agent_config", lambda: None)
        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request(
            {
                "changes": [
                    {
                        "name": "generic-mcp",
                        "personalclaw": True,
                        "toolOverrides": {
                            "../evil": False,  # rejected
                            "legit-tool": False,  # accepted
                        },
                    }
                ]
            }
        )
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)

        assert body["ok"] is True
        actions = body["results"][0]["actions"]
        assert actions.get("tools_rejected") == ["../evil"]
        # The good tool went through
        assert "tools" in actions
        assert "legit-tool" in actions["tools"]
