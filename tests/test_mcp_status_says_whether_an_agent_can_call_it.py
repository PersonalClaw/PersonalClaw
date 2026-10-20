"""A server's status reads "ok" only when an agent can call its tools.

🔴 Two ways the Tools page drew "ready" for a server no agent could call, both measured on
``origin/main``:

1. Every external server's tools reach an agent through ONE registered provider, ``mcp``, which the
   MCP Tool Servers app registers. That app is a Store install, not part of a default install, and
   nothing said so: a server added on a fresh install connected, probed ``ok`` and showed green, and
   no agent's surface carried one of its tools.
2. The probe and the agent's connection built a stdio server's environment two ways. The probe put
   the server's own ``PATH`` in front of the gateway's; the connection let it REPLACE the gateway's.
   A server that set ``PATH`` probed ``ok`` and failed every call, its command not found.

Every server here is real: the SDK's ``FastMCP`` over stdio, spawned the way a user's server is. The
provider is a stand-in with the real one's name and routing (core's tests cannot import the apps
repository), and the routes are the ones the Tools page reads.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import allow_configured

from personalclaw import mcp_client, mcp_discovery
from personalclaw.config import loader as config_loader
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.dashboard.handlers import mcp as mcp_handlers
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers import tool_prefs
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

SERVER = "notes"

_FIXTURE = textwrap.dedent("""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("notes")


    @mcp.tool(description="say hello")
    def hello(name: str) -> str:
        return f"hello {name}"


    mcp.run(transport="stdio")
    """)


class _ExternalMcpTools(ToolProvider):
    """The MCP Tool Servers app's provider, as far as core can tell: its name, and every configured
    server's tools as ``mcp/<server>/<tool>``, each call routed back to its server."""

    @property
    def name(self) -> str:
        return tool_registry.EXTERNAL_MCP_PROVIDER

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self) -> list[ToolDefinition]:
        out = []
        for server, conn in mcp_client.get_mcp_client_registry().items():
            for tool in await conn.list_tools():
                out.append(
                    ToolDefinition(
                        name=f"mcp/{server}/{tool.name}", description="", provider=self.name
                    )
                )
        return out

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        _, server, tool = tool_name.split("/", 2)
        conn = mcp_client.get_mcp_client_registry().get(server, "")
        assert conn is not None
        ok, output = await conn.call_tool(tool, arguments)
        return ToolResult(success=ok, output=output if ok else "", error="" if ok else output)


@contextlib.asynccontextmanager
async def _tools_page(spec: dict[str, Any], monkeypatch) -> AsyncIterator[TestClient]:
    """A home with *spec* as the one server in ``mcp.json`` and the routes the Tools page reads."""
    write_mcp_document(config_loader.config_dir() / "mcp.json", {"mcpServers": {SERVER: spec}})
    allow_configured(SERVER)
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_discovery, "_probing", {})
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_ts", 0.0)
    for registry_state in ("_providers", "_provider_app", "_registrations", "_claims"):
        monkeypatch.setattr(tool_registry, registry_state, {})
    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    app.router.add_get("/api/mcp", mcp_handlers.api_mcp_servers)
    app.router.add_post("/api/mcp/probe", mcp_handlers.api_mcp_probe)
    app.router.add_get("/api/mcp/probe", mcp_handlers.api_mcp_probe_cached)
    app.router.add_post("/api/mcp/probe/{name}", mcp_handlers.api_mcp_probe_one)
    try:
        async with TestClient(TestServer(app)) as http:
            yield http
            await asyncio.gather(*app["state"]._background_tasks, return_exceptions=True)
    finally:
        registry = mcp_client._registry
        if registry is not None:
            await registry.shutdown_all()


async def _row(http: TestClient, method: str, path: str) -> dict[str, Any]:
    resp = await http.request(method, path)
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    rows = body if isinstance(body, list) else [body]
    [row] = [r for r in rows if r["name"] == SERVER]
    return row


_EVERY_READ = [
    ("POST", f"/api/mcp/probe/{SERVER}"),
    ("GET", "/api/mcp"),
    ("GET", "/api/mcp/probe"),
    ("POST", "/api/mcp/probe"),
]


@pytest.fixture
def server_spec(tmp_path: Path) -> dict[str, Any]:
    script = tmp_path / "notes_server.py"
    script.write_text(_FIXTURE, encoding="utf-8")
    return {"command": sys.executable, "args": [str(script)]}


@pytest.mark.asyncio
async def test_a_server_no_agent_can_call_does_not_read_ok_on_any_route(server_spec, monkeypatch):
    """Nothing serves external MCP tools, which is a fresh install: every route the page reads says
    so, and names what is missing. The connection itself worked, so its tools are still listed."""
    async with _tools_page(server_spec, monkeypatch) as http:
        for method, path in _EVERY_READ:
            row = await _row(http, method, path)
            assert (
                row["status"] == mcp_discovery.UNSERVED
            ), f"{method} {path} said {row['status']!r}"
            assert row["error"] == mcp_discovery.UNSERVED_REASON
            assert [t["name"] for t in row["tools"]] == ["hello"], "the probe found no tools"


@pytest.mark.asyncio
async def test_ok_is_said_once_an_agent_can_call_it_and_taken_back_when_it_cannot(
    server_spec, monkeypatch
):
    async with _tools_page(server_spec, monkeypatch) as http:
        first = await _row(http, "POST", f"/api/mcp/probe/{SERVER}")
        assert first["status"] == mcp_discovery.UNSERVED

        # The app is installed after the probe: no re-probe is needed for the status to follow it.
        tool_registry.register_provider(_ExternalMcpTools(), app="mcp-tools")
        for method, path in (("GET", "/api/mcp"), ("GET", "/api/mcp/probe")):
            row = await _row(http, method, path)
            assert (row["status"], row["error"]) == ("ok", ""), f"{method} {path}: {row}"

        # And "ok" means exactly that: an agent's surface serves the tool and the call runs.
        served = await tool_registry.resolve(tool_registry.tool_surface(None), "mcp/notes/hello")
        assert served is not None, "no provider on an agent's surface serves mcp/notes/hello"
        result = await served[0].invoke("mcp/notes/hello", {"name": "claw"})
        assert (result.success, result.output) == (True, "hello claw"), result.error

        # Switched off, its tools leave every agent turn, and the status says so again.
        tool_prefs.set_provider_enabled(tool_registry.EXTERNAL_MCP_PROVIDER, False)
        row = await _row(http, "GET", "/api/mcp")
        assert row["status"] == mcp_discovery.UNSERVED


@pytest.mark.asyncio
async def test_a_server_that_sets_its_own_path_is_started_the_way_it_was_probed(
    server_spec, tmp_path, monkeypatch
):
    """The server's command is found on the gateway's PATH and its own ``PATH`` names a directory
    of its own — the shape of any server that adds a tool directory. The probe found the command;
    the agent's call must find the same one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "notes-mcp"
    launcher.write_text(
        "#!/bin/sh\nexec "
        + " ".join(shlex.quote(a) for a in (server_spec["command"], *server_spec["args"]))
        + "\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    own = tmp_path / "server-tools"
    own.mkdir()
    spec = {"command": "notes-mcp", "args": [], "env": {"PATH": str(own)}}

    async with _tools_page(spec, monkeypatch) as http:
        tool_registry.register_provider(_ExternalMcpTools(), app="mcp-tools")
        probed = await _row(http, "POST", f"/api/mcp/probe/{SERVER}")
        assert probed["status"] == "ok", probed

        served = await tool_registry.resolve(tool_registry.tool_surface(None), "mcp/notes/hello")
        assert served is not None, "no provider on an agent's surface serves mcp/notes/hello"
        result = await served[0].invoke("mcp/notes/hello", {"name": "claw"})
        assert (result.success, result.output) == (
            True,
            "hello claw",
        ), f"probed ok, but the agent's call failed: {result.error}"


def test_the_spawn_environment_puts_a_servers_path_first_and_keeps_the_gateways(monkeypatch):
    monkeypatch.setenv("PATH", "/gateway/bin")
    env = mcp_discovery.stdio_spawn_env(
        {"PATH": "/server/bin", "LOG_LEVEL": "debug"}, server="my-server"
    )
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/server/bin"
    assert "/gateway/bin" in parts
    assert env["LOG_LEVEL"] == "debug"


def test_personalclaws_own_server_keeps_its_ok_without_the_provider(monkeypatch):
    """``personalclaw-core`` is not in ``mcp.json`` and never reaches an agent through the
    provider: a native agent has its tools in-process and an ACP session is handed it. Measured on
    the first drive of this change, the Tools page marked it "agents can't call it" — a false
    alarm on the one server every agent does call."""
    for registry_state in ("_providers", "_provider_app", "_registrations", "_claims"):
        monkeypatch.setattr(tool_registry, registry_state, {})
    row = {"name": "personalclaw-core", "status": "ok", "error": "", "tools": [{"name": "notify"}]}
    assert mcp_discovery.as_agents_see_it(row) == row


@pytest.mark.asyncio
async def test_a_provider_another_app_names_mcp_does_not_count(server_spec, monkeypatch):
    """Only the MCP Tool Servers app may serve a name under ``mcp/`` (the registry's rule 2), so a
    provider some other app registers under the same name serves none of these tools, and the
    status must not read as if it did."""
    async with _tools_page(server_spec, monkeypatch) as http:
        tool_registry.register_provider(_ExternalMcpTools(), app="some-other-app")
        row = await _row(http, "POST", f"/api/mcp/probe/{SERVER}")
        assert row["status"] == mcp_discovery.UNSERVED
