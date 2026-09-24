"""Native MCP client — real stdio round-trip + graceful no-SDK / no-server paths.

Spawns a tiny FastMCP server over stdio and proves the long-lived client can
``list_tools`` and ``call_tool`` against it — the acceptance criterion for
external MCP tools being callable by the native loop. Skips when the optional
``mcp`` SDK isn't installed, so a no-extra install still collects cleanly.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from personalclaw.mcp_client import (
    McpClientRegistry,
    get_mcp_client_registry,
    mcp_sdk_available,
)

pytestmark = pytest.mark.skipif(
    not mcp_sdk_available(), reason="requires the optional 'mcp' SDK extra"
)


# A minimal stdio MCP server: one tool that echoes its argument.
_FIXTURE_SERVER = textwrap.dedent("""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fixture")

    @mcp.tool()
    def shout(text: str) -> str:
        '''Uppercase the given text.'''
        return text.upper()

    if __name__ == "__main__":
        mcp.run()
    """)


@pytest.fixture()
def fixture_server(tmp_path):
    server = tmp_path / "fixture_server.py"
    server.write_text(_FIXTURE_SERVER)
    return {"command": sys.executable, "args": [str(server)]}


@pytest.mark.asyncio
async def test_stdio_list_and_call_round_trip(fixture_server):
    reg = McpClientRegistry()
    reg.load_from_specs({"fixture": fixture_server})
    conn = reg.get("fixture")
    assert conn is not None
    try:
        tools = await conn.list_tools()
        names = {t.name for t in tools}
        assert "shout" in names
        shout = next(t for t in tools if t.name == "shout")
        assert "Uppercase" in shout.description
        assert shout.input_schema.get("type") == "object"

        ok, output = await conn.call_tool("shout", {"text": "hi there"})
        assert ok is True
        assert "HI THERE" in output
    finally:
        await reg.shutdown_all()


@pytest.mark.asyncio
async def test_disabled_server_is_skipped(fixture_server):
    reg = McpClientRegistry()
    reg.load_from_specs({"fixture": {**fixture_server, "disabled": True}})
    assert reg.get("fixture") is None


@pytest.mark.asyncio
async def test_missing_command_reports_error_not_raise():
    reg = McpClientRegistry()
    reg.load_from_specs({"broken": {"command": "/nonexistent/mcp-server-xyz"}})
    conn = reg.get("broken")
    assert conn is not None
    try:
        tools = await conn.list_tools()
        assert tools == []  # failed handshake → empty, never raises
        ok, err = await conn.call_tool("anything", {})
        assert ok is False
        assert "not connected" in err or "broken" in err
    finally:
        await reg.shutdown_all()


def test_registry_reconciles_added_and_removed(fixture_server):
    reg = McpClientRegistry()
    reg.load_from_specs({"a": fixture_server, "b": fixture_server})
    assert {n for n, _ in reg.items()} == {"a", "b"}
    # Reconcile to a different set → "b" dropped, "c" added.
    reg.load_from_specs({"a": fixture_server, "c": fixture_server})
    assert {n for n, _ in reg.items()} == {"a", "c"}


def test_registry_singleton_present_with_sdk():
    # SDK is available (module-level skip otherwise), so the registry is a real
    # object, never None.
    assert get_mcp_client_registry() is not None
