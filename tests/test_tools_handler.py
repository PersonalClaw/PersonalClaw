"""GET /api/tools catalog handler — a slow/dead MCP server must not stall it.

Regression: Source 2 once awaited ``conn.list_tools()`` sequentially across the
registry, so each unreachable server blocked the whole catalog for its full
connect timeout (and with it the Tools page). The handler now probes servers
concurrently under a short per-server cap; one slow server is skipped this round
without delaying the rest.
"""

from __future__ import annotations

import asyncio

import pytest

import personalclaw.dashboard.handlers.tools as tools_mod


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} desc"
        self.input_schema = {"type": "object", "properties": {}}


class _FastConn:
    async def list_tools(self):
        return [_FakeTool("fast_tool")]


class _SlowConn:
    """Models a dead server: never returns within the per-server budget."""

    async def list_tools(self):
        await asyncio.sleep(3600)
        return []


class _FakeRegistry:
    def __init__(self, conns: dict) -> None:
        self._conns = conns

    def items(self):
        return self._conns.items()


@pytest.mark.asyncio
async def test_slow_mcp_server_does_not_block_catalog(monkeypatch):
    monkeypatch.setattr(tools_mod, "_MCP_LIST_TIMEOUT_SECS", 0.1)
    # The handler imports these names locally inside the function, so patch the
    # source modules (not tools_mod) for the patched callables to take effect.
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry",
        lambda: _FakeRegistry({"fast": _FastConn(), "dead": _SlowConn()}),
        raising=False,
    )
    # Silence the unrelated sources — this test is about Source 2 only.
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools",
        _noop_list_all_tools,
        raising=False,
    )

    resp = await asyncio.wait_for(tools_mod.api_tools_list(_DummyRequest()), timeout=5.0)

    import json

    payload = json.loads(resp.body.decode())
    names = {t["name"] for t in payload["tools"]}
    # Fast server's tool is present; the dead server contributed nothing and did
    # not stall the call (the outer wait_for would have fired otherwise).
    assert "mcp/fast/fast_tool" in names
    assert not any(n.startswith("mcp/dead/") for n in names)


async def _noop_list_all_tools():
    return []


class _DummyRequest:
    """Minimal stand-in — the handler reads nothing off the request."""


# ── Load-failure surfacing ───────────────────────────────────────────────────


def test_registry_records_and_dedups_failures():
    from personalclaw.tool_providers import registry as reg

    reg.clear_load_failures()
    reg.record_failure("prov-a", "boom")
    reg.record_failure("prov-b", "kaboom")
    reg.record_failure("prov-a", "boom-again")  # same provider → replaces, not duplicates
    failures = reg.get_load_failures()
    by_provider = {f["provider"]: f["error"] for f in failures}
    assert by_provider == {"prov-a": "boom-again", "prov-b": "kaboom"}
    reg.clear_load_failures()
    assert reg.get_load_failures() == []


@pytest.mark.asyncio
async def test_handler_surfaces_provider_load_failure(monkeypatch):
    """A tool provider that raises while listing is reported in load_failures."""
    from personalclaw.tool_providers import registry as reg

    class _BrokenProvider:
        name = "broken-prov"

        async def list_tools(self):
            raise RuntimeError("could not connect")

    # Real list_all_tools over a broken provider → records the failure.
    reg.clear_load_failures()
    monkeypatch.setattr(reg, "_providers", {"broken-prov": _BrokenProvider()})
    # No MCP registry for this test.
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: None, raising=False
    )

    resp = await tools_mod.api_tools_list(_DummyRequest())

    import json

    payload = json.loads(resp.body.decode())
    providers_failed = {f["provider"] for f in payload.get("load_failures", [])}
    assert "broken-prov" in providers_failed
    msg = next(f["error"] for f in payload["load_failures"] if f["provider"] == "broken-prov")
    assert "could not connect" in msg


@pytest.mark.asyncio
async def test_handler_no_failures_when_all_load(monkeypatch):
    """A clean catalog build reports an empty load_failures list."""
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_all_tools",
        _noop_list_all_tools,
        raising=False,
    )
    monkeypatch.setattr(
        "personalclaw.mcp_client.get_mcp_client_registry", lambda: None, raising=False
    )
    from personalclaw.tool_providers import registry as reg

    reg.clear_load_failures()

    resp = await tools_mod.api_tools_list(_DummyRequest())

    import json

    payload = json.loads(resp.body.decode())
    assert payload.get("load_failures") == []


# ── GET /api/tools/savings (Context Economy §1.3) ─────────────────────────────


@pytest.mark.asyncio
async def test_savings_endpoint_returns_summary(tmp_path, monkeypatch):
    import json

    import personalclaw.config.loader as cfg
    import personalclaw.tool_providers.savings as sv

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "config_dir", lambda: tmp_path)
    sv.record_saving(
        month="2026-07", model="unknown", compressor="log", chars_in=4000, chars_out=400
    )

    resp = await tools_mod.api_tools_savings(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert payload["saved_chars"] == 3600
    assert payload["top_compressor"] == "log"
    assert payload["estimated"] is True


@pytest.mark.asyncio
async def test_savings_endpoint_empty_is_safe(tmp_path, monkeypatch):
    import json

    import personalclaw.config.loader as cfg
    import personalclaw.tool_providers.savings as sv

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "config_dir", lambda: tmp_path)

    resp = await tools_mod.api_tools_savings(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert payload["saved_chars"] == 0 and payload["top_compressor"] is None


@pytest.mark.asyncio
async def test_groups_endpoint_reports_the_partition(monkeypatch):
    """GET /api/tools/groups reports the SAME provider-grain partition the runtime
    assembles, with core always-on and offerability resolved."""
    import json

    from personalclaw.tool_providers.base import ToolDefinition

    async def _tools():
        return [
            ToolDefinition(name="schedule_add", description="d", provider="personalclaw-schedule"),
            ToolDefinition(name="subagent_run", description="d", provider="personalclaw-subagents"),
        ]

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _tools)
    # No model resolvable → the subagents group is not offerable (§5.5).
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda _u: False
    )

    resp = await tools_mod.api_tool_groups(_DummyRequest())
    payload = json.loads(resp.body.decode())
    by_name = {g["name"]: g for g in payload["groups"]}
    assert by_name["schedule"]["toolCount"] == 1
    assert by_name["schedule"]["offerable"] is True
    assert by_name["subagents"]["offerable"] is False  # capability unmet
    assert by_name["subagents"]["capability"] == "model:orchestration"
    # core is present (the platform provider is enumerated separately) and always-on.
    assert by_name["core"]["alwaysOn"] is True
    # Per-surface defaults are reported; chat's empty list means "every group".
    assert "chat" in payload["surfaceDefaults"]
    assert isinstance(payload["enabled"], bool)


@pytest.mark.asyncio
async def test_groups_endpoint_survives_a_broken_registry(monkeypatch):
    """A provider that explodes must not 500 the page — the endpoint degrades."""
    import json

    async def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _boom)
    resp = await tools_mod.api_tool_groups(_DummyRequest())
    payload = json.loads(resp.body.decode())
    assert "groups" in payload  # still a well-formed answer


# ── POST /api/tools/invoke — request type guards ──────────────────────────────


class _InvokeRequest:
    """Minimal stand-in for api_tool_invoke: supplies the JSON body and the
    optional app identity the handler reads off the request mapping."""

    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self):
        return self._body

    def get(self, key, default=None):
        # No app identity → the owner/internal caller path (no permission gate).
        return default


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_provider", [["a", "b"], {"x": 1}])
async def test_invoke_rejects_non_string_provider(bad_provider):
    """A non-string ``provider`` is a 400, mirroring the ``arguments`` guard.

    Regression: an unhashable ``provider`` (list/dict) flowed straight into
    ``get_provider(provider_name)`` — a dict lookup — which raised and surfaced
    as HTTP 500 instead of a clean 400.
    """
    import json

    resp = await tools_mod.api_tool_invoke(
        _InvokeRequest({"tool": "artifact_list", "provider": bad_provider})
    )
    assert resp.status == 400
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert payload["error"] == "provider must be a string"


# ── #444 gaps #2/#3: tools toggle validation ────────────────────────────────
async def _one_tool():
    from personalclaw.tool_providers.base import ToolDefinition

    return [ToolDefinition(name="artifact_list", description="d", provider="personalclaw-core")]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_enabled", ["false", "true", 0, 1, "0"])
async def test_toggle_rejects_non_bool_enabled(bad_enabled, monkeypatch):
    """#444 gap #3: a non-bool ``enabled`` (e.g. the JSON string "false", which is
    truthy under bool()) must be a 400, not a silent inversion of the toggle."""
    import json

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest(
            {"provider": "personalclaw-core", "name": "artifact_list", "enabled": bad_enabled}
        )
    )
    assert resp.status == 400
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert payload["error"] == "enabled must be a boolean"


@pytest.mark.asyncio
async def test_toggle_rejects_unknown_tool_name(monkeypatch):
    """#444 gap #2: toggling a tool no registered provider exposes is a 404, so a
    dead key is never persisted to tool_prefs.json."""
    import json

    calls = []
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.set_enabled",
        lambda *a, **k: calls.append((a, k)) or {"ok": True},
    )
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest(
            {"provider": "personalclaw-core", "name": "zz-not-a-real-tool", "enabled": False}
        )
    )
    assert resp.status == 404
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert "unknown tool" in payload["error"]
    assert calls == []  # set_enabled never reached → nothing persisted


@pytest.mark.asyncio
async def test_toggle_accepts_a_real_bool_and_known_tool(monkeypatch):
    """The guards don't over-block: a real bool + a known tool still toggles."""
    import json

    monkeypatch.setattr("personalclaw.tool_providers.registry.list_all_tools", _one_tool)
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.set_enabled",
        lambda provider, name, enabled: {
            "ok": True,
            "provider": provider,
            "tool": name,
            "enabled": enabled,
        },
    )
    resp = await tools_mod.api_tools_toggle(
        _InvokeRequest({"provider": "personalclaw-core", "name": "artifact_list", "enabled": False})
    )
    assert resp.status == 200
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is True
