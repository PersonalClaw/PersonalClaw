"""A tool you switch off on the Tools page is off everywhere: for an agent, for "Try it", and on the
page itself.

🔴 THE DEFECT (measured on ``origin/main``). An external MCP server's tool is switched off in that
server's ``disabledTools`` list in ``mcp.json``, which is what an ACP agent reads. Nothing on the
native path read it: the agent was still offered the tool and could call it, "Try it" ran it, and
the Tools page computed the switch's own state from ``tool_prefs.json`` instead, so it sprang back
to on. And the switch wrote the wrong thing: the page sends a row's name, ``mcp/<server>/<tool>``,
while the list holds the name the server gives its tool (``toolOverrides`` on import writes
``hello``, and so does an ACP agent's config), so even an ACP agent never saw it off.

Now ``tool_prefs.is_disabled``, the one check the runtime, ``POST /api/tools/invoke`` and the
catalog make, reads ``disabledTools`` too, the catalog row carries the server's own name for the
switch to send, and the MCP switch refuses a name in the ``mcp/<server>/`` form.

The external server here is a fake client registry (``mcp_client._registry``) holding one server,
``fixture``, with two tools. The MCP Tool Servers app is a stand-in registered the way the real one
is (the ``mcp`` provider, by the ``mcp-tools`` app), serving ``mcp/<server>/<tool>`` from that
registry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import mcp_client
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.mcp_client import McpToolSpec
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

SERVER = "fixture"


class _Conn:
    """One connected MCP server: its tools, and every call it received."""

    def __init__(self, tools: list[str]) -> None:
        self._tools = tools
        self.calls: list[str] = []

    async def list_tools(self) -> list[McpToolSpec]:
        return [
            McpToolSpec(name=t, description=f"{t} something", input_schema={"type": "object"})
            for t in self._tools
        ]

    async def call_tool(self, tool: str, arguments: dict) -> tuple[bool, str]:
        self.calls.append(tool)
        return True, f"the server ran {tool}"


class _Registry:
    """The in-process MCP client registry, holding the connected servers."""

    def __init__(self, servers: dict[str, _Conn]) -> None:
        self._servers = servers

    def items(self):
        return list(self._servers.items())

    def get(self, name: str, session_key: str = ""):
        return self._servers.get(name)


class _McpToolServers(ToolProvider):
    """The MCP Tool Servers app's provider: its name, its tag and its routing into the registry."""

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self) -> list[ToolDefinition]:
        out = []
        for server, conn in mcp_client.get_mcp_client_registry().items():
            for tool in await conn.list_tools():
                out.append(
                    ToolDefinition(
                        name=f"mcp/{server}/{tool.name}",
                        description=tool.description,
                        provider="mcp",
                        parameters=tool.input_schema,
                        requires_approval=True,
                        risk_level=RiskLevel.SAFE,
                    )
                )
        return out

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        _, server, tool = tool_name.split("/", 2)
        conn = mcp_client.get_mcp_client_registry().get(server)
        ok, output = await conn.call_tool(tool, arguments)
        return ToolResult(success=ok, output=output if ok else "", error="" if ok else output)


@dataclass
class World:
    http: TestClient
    conn: _Conn
    workspace: Path
    home: Path
    sel_rows: list = field(default_factory=list)

    def switch_off(self, *tools: str) -> None:
        """Write the server's switched-off list the way `mcp.json` holds it."""
        write_mcp_document(
            self.home / "mcp.json",
            {"mcpServers": {SERVER: {"command": "fixture", "disabledTools": list(tools)}}},
        )

    def disabled_tools(self) -> list[str]:
        spec = json.loads((self.home / "mcp.json").read_text())["mcpServers"][SERVER]
        return list(spec.get("disabledTools", []))

    async def row(self, name: str) -> dict:
        resp = await self.http.get("/api/tools")
        assert resp.status == 200
        [row] = [t for t in (await resp.json())["tools"] if t["name"] == name]
        return row

    async def invoke(self, name: str) -> tuple[int, dict]:
        resp = await self.http.post("/api/tools/invoke", json={"tool": name, "arguments": {}})
        return resp.status, await resp.json()


@asynccontextmanager
async def _world(tmp_path: Path, monkeypatch) -> AsyncIterator[World]:
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard.handlers import mcp as mcp_handlers
    from personalclaw.dashboard.handlers.tools import (
        api_tool_invoke,
        api_tools_list,
        api_tools_toggle,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    home = config_loader.config_dir()
    conn = _Conn(["hello", "goodbye"])
    monkeypatch.setattr(mcp_client, "_registry", _Registry({SERVER: conn}))
    monkeypatch.setattr(mcp_client, "get_mcp_client_registry", lambda: mcp_client._registry)
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    tool_registry.register_provider(_McpToolServers(), app="mcp-tools")
    write_mcp_document(home / "mcp.json", {"mcpServers": {SERVER: {"command": "fixture"}}})

    app = web.Application()
    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    app.router.add_post("/api/tools/toggle", api_tools_toggle)
    app.router.add_post("/api/mcp/toggle-tool", mcp_handlers.api_mcp_toggle_tool)
    async with TestClient(TestServer(app)) as http:
        yield World(http, conn, workspace, home)


class Turn(NamedTuple):
    offered: list[str]  # the tool names the model was offered
    outputs: list[str]  # each tool result, as the model read it
    metas: list[dict]  # each tool result's tool_meta, as the tool card reads it
    asked: list[str]  # the tools the turn asked you to approve


async def _agent(world: World, call: str, *, policy: str = "auto") -> Turn:
    """One native agent turn over the surface an agent is built from, calling *call* once.

    *policy* is the session's approval policy; ``""`` asks before each tool that asks first, as
    a chat does. Every approval asked for is recorded and allowed, as you would in the chat.
    """
    from test_native_runtime import _defn, _ScriptedModel

    from personalclaw.agents.native.builtin_tools import create_platform_tools_provider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    model = _ScriptedModel(
        [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=call, tool_input="{}"),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=tool_registry.tool_surface(
            create_platform_tools_provider(cwd=world.workspace)
        ),
        cwd=str(world.workspace),
    )
    runtime.set_approval_policy(policy)
    await runtime.start()
    events: list[AgentEvent] = []
    asked: list[str] = []

    async def pump() -> None:
        async for ev in runtime.stream(f"call {call}"):
            events.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asked.append(str(ev.title))
                await runtime.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    offered = []
    for t in model.last_tools or []:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        offered.append(str(fn.get("name", "")))
    results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    return Turn(
        offered,
        [str(e.tool_output) for e in results],
        [dict(e.tool_meta or {}) for e in results],
        asked,
    )


# ── a tool switched off on its server ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_is_not_offered_a_tool_switched_off_and_cannot_call_it(
    tmp_path, monkeypatch
):
    async with _world(tmp_path, monkeypatch) as w:
        w.switch_off("hello")

        turn = await _agent(w, f"mcp/{SERVER}/hello")

        assert f"mcp/{SERVER}/hello" not in turn.offered, "the model was offered a tool that is off"
        assert f"mcp/{SERVER}/goodbye" in turn.offered
        assert w.conn.calls == [], "the server received a call to a tool that is off"
        assert turn.metas[0].get("ok") is False, turn


@pytest.mark.asyncio
async def test_a_call_to_a_tool_switched_off_is_refused_without_asking_you(tmp_path, monkeypatch):
    """A chat asks before an MCP server's tool runs. It asked for this one too, and after you
    allowed it answered ``unknown tool``: an approval for a call that could run nothing."""
    async with _world(tmp_path, monkeypatch) as w:
        w.switch_off("hello")

        turn = await _agent(w, f"mcp/{SERVER}/hello", policy="")

        assert turn.asked == [], "it asked you to approve a tool you switched off"
        assert turn.outputs == [f"Error: unknown tool 'mcp/{SERVER}/hello'"]
        assert turn.metas[0].get("ok") is False
        assert w.conn.calls == []


@pytest.mark.asyncio
async def test_try_it_refuses_a_tool_switched_off_on_its_server(tmp_path, monkeypatch):
    async with _world(tmp_path, monkeypatch) as w:
        w.switch_off("hello")

        status, body = await w.invoke(f"mcp/{SERVER}/hello")

        assert status == 403, body
        assert body["error"]["code"] == "tool_disabled"
        assert w.conn.calls == []


@pytest.mark.asyncio
async def test_the_tools_page_shows_it_off(tmp_path, monkeypatch):
    async with _world(tmp_path, monkeypatch) as w:
        w.switch_off("hello")

        assert (await w.row(f"mcp/{SERVER}/hello"))["disabled"] is True
        assert (await w.row(f"mcp/{SERVER}/goodbye"))["disabled"] is False


# ── the switch ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_switch_writes_the_name_the_server_gives_its_tool(tmp_path, monkeypatch):
    """The page sends the row's ``serverTool`` to ``POST /api/mcp/toggle-tool``, the route the
    Tools page's switch calls for an MCP server's tool, and every surface then reads it off."""
    async with _world(tmp_path, monkeypatch) as w:
        row = await w.row(f"mcp/{SERVER}/hello")
        assert row.get("serverTool") == "hello", row

        resp = await w.http.post(
            "/api/mcp/toggle-tool",
            json={"server": SERVER, "tool": row["serverTool"], "enabled": False},
        )

        assert resp.status == 200, await resp.json()
        assert w.disabled_tools() == ["hello"]
        assert (await w.row(f"mcp/{SERVER}/hello"))["disabled"] is True
        assert (await w.invoke(f"mcp/{SERVER}/hello"))[0] == 403
        assert f"mcp/{SERVER}/hello" not in (await _agent(w, f"mcp/{SERVER}/goodbye")).offered


@pytest.mark.asyncio
async def test_the_mcp_switch_refuses_a_name_in_the_agents_form(tmp_path, monkeypatch):
    """``mcp/fixture/hello`` in ``disabledTools`` matches nothing, for an ACP agent or here, so a
    switch that stored it would read off and switch nothing."""
    async with _world(tmp_path, monkeypatch) as w:
        resp = await w.http.post(
            "/api/mcp/toggle-tool",
            json={"server": SERVER, "tool": f"mcp/{SERVER}/hello", "enabled": False},
        )

        assert resp.status == 400, await resp.json()
        assert w.disabled_tools() == []


@pytest.mark.asyncio
async def test_the_native_tool_switch_does_not_take_an_mcp_servers_tool(tmp_path, monkeypatch):
    """``POST /api/tools/toggle`` writes ``tool_prefs.json``, which no ACP agent reads, so it would
    be a second switch for one tool. An MCP server's tool has one, its server's list."""
    from personalclaw.tool_providers import tool_prefs

    async with _world(tmp_path, monkeypatch) as w:
        resp = await w.http.post(
            "/api/tools/toggle",
            json={"provider": SERVER, "name": f"mcp/{SERVER}/hello", "enabled": False},
        )

        assert resp.status == 409, await resp.json()
        assert "/api/mcp/toggle-tool" in (await resp.json())["error"]
        assert tool_prefs.load_disabled() == set()


# ── what stays on ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_left_on_is_offered_callable_and_shown_on(tmp_path, monkeypatch):
    """BASELINE (passes before and after): the switch turns off the tool it names, nothing else,
    and a tool that is on still asks before it runs."""
    async with _world(tmp_path, monkeypatch) as w:
        w.switch_off("hello")

        turn = await _agent(w, f"mcp/{SERVER}/goodbye", policy="")
        status, body = await w.invoke(f"mcp/{SERVER}/goodbye")

        assert f"mcp/{SERVER}/goodbye" in turn.offered
        assert turn.asked == [f"mcp/{SERVER}/goodbye"]
        assert turn.outputs[0] == "the server ran goodbye" and "ok" not in turn.metas[0]
        assert (status, body.get("output")) == (200, "the server ran goodbye"), body
        assert (await w.row(f"mcp/{SERVER}/goodbye"))["disabled"] is False
