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
