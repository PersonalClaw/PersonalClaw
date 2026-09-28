""" "Try it" runs a tool from an external MCP server: the same tool, reached the same way, with the
same checks an agent's call to it gets.

🔴 TWO DEFECTS, stacked (both measured on ``origin/main``).

1. The Tools page lists an external server's tool as ``{"name": "mcp/<server>/<tool>",
   "provider": "<server>"}``, and "Try it" posts exactly that to ``POST /api/tools/invoke``. The
   route looked ``<server>`` up in the tool-provider registry, which never holds a server, and
   answered ``404 unknown tool provider: <server>`` for every external server, stdio and remote.
2. An agent reaches those tools only through the ``mcp`` provider of the MCP Tool Servers app
   (``mcp-tools``), and enabling that app registered nothing. It is ``multiInstance`` for its
   settings card, whose instances are the servers in ``mcp.json``; the tool handler built one
   provider per instance from the GENERIC instance store, which that card never writes. So no
   agent could call an external tool either, while the Tools page listed them all (it reads the
   client registry directly).

The route now resolves a name the way an agent turn does, over the one surface
(``tool_providers.registry.tool_surface``) an agent's tools are built from, and the app's one
provider, which serves every configured server, is built once. There is one way to reach an
external tool, and no second lookup into the client registry.

Every call here reaches a real MCP server: the SDK's ``FastMCP``, over stdio and over Streamable
HTTP. The ``mcp-tools`` app is the one PersonalClaw ships (``apps/native/mcp-tools``), registered
the way the gateway registers it, and a spy on its provider's ``invoke`` records what it was asked
to run.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import allow_configured

from personalclaw import mcp_client
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.tool_providers import registry as tool_registry
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

SERVER = "fixture"

# A real MCP server. `delete_stack` writes a marker file when it runs, so a test can tell a call
# that was refused from one that ran and whose answer was lost. It DECLARES itself destructive
# (`destructiveHint`), which is what makes it one: a tool's name says nothing.
_FIXTURE = textwrap.dedent("""
    import os, socket, sys

    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    TRANSPORT, MARKER, PORT_FILE = sys.argv[1:4]
    mcp = FastMCP("fixture")


    @mcp.tool(description="greet someone")
    def hello(name: str) -> str:
        return f"hello {name}"


    @mcp.tool(
        description="tear down a stack",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    def delete_stack(name: str) -> str:
        with open(MARKER, "a") as f:
            f.write(name + "\\n")
        return f"deleted {name}"


    if TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        with open(PORT_FILE + ".tmp", "w") as f:
            f.write(str(sock.getsockname()[1]))
        os.replace(PORT_FILE + ".tmp", PORT_FILE)
        app = mcp.streamable_http_app()
        uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)

# The MCP Tool Servers app PersonalClaw ships: its own manifest and its own provider.
_BUNDLE = NATIVE_DIR / "mcp-tools"


@dataclass
class Fixture:
    transport: str
    spec: dict[str, Any]
    marker: Path

    def ran_delete_stack(self) -> bool:
        return self.marker.exists()


@pytest.fixture(params=["stdio", "http"])
def fixture_server(request, tmp_path):
    """The fixture server's ``mcp.json`` spec, over stdio or over Streamable HTTP."""
    transport = request.param
    script = tmp_path / "fixture_server.py"
    script.write_text(_FIXTURE, encoding="utf-8")
    marker = tmp_path / f"delete_stack.{transport}"
    port_file = tmp_path / f"port.{transport}"
    if transport == "stdio":
        # The native client spawns it, as it does any stdio server a user adds.
        yield Fixture(
            transport,
            {"command": sys.executable, "args": [str(script), "stdio", str(marker), ""]},
            marker,
        )
        return
    with (tmp_path / "server.stderr").open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(script), "http", str(marker), str(port_file)],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the fixture server did not start (exit {proc.poll()})")
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port_file.read_text(encoding='utf-8').strip()}/mcp"
        yield Fixture(transport, {"type": "http", "url": url}, marker)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def register_directly(app_dir: Path) -> None:
    """Register the app's provider without the enable path: the route is the subject."""
    module = load_bundle_module(app_dir, "mcp-tools", "provider")
    tool_registry.register_provider(module.create_mcp_provider({}), app="mcp-tools")


def register_by_enabling(app_dir: Path) -> None:
    """Enable the app the way the gateway does: its manifest, through the tool type handler."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers.registry import RegisteredProvider, ToolTypeHandler

    manifest = AppManifest.from_json_file(app_dir / "app.json")
    ext = RegisteredProvider(
        name=manifest.name, manifest=manifest, provider_config=manifest.all_providers()[0]
    )
    handler = ToolTypeHandler()
    instance = handler.create(ext)
    if instance is not None:
        handler.register(ext, instance)


class _OtherProvider(ToolProvider):
    """A registered provider that serves none of the fixture's tools."""

    def __init__(self) -> None:
        self.invoked: list[str] = []

    @property
    def name(self) -> str:
        return "personalclaw-other"

    @property
    def display_name(self) -> str:
        return "Other"

    async def list_tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="other_tool", description="d", provider=self.name)]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.invoked.append(tool_name)
        return ToolResult(success=True, output="the wrong provider ran it")


class _Sel:
    """Every SEL row the route writes."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def log_tool_invocation(self, **kw: Any) -> None:
        self.rows.append(("tool_invocation", kw))

    def log_api_access(self, **kw: Any) -> None:
        self.rows.append(("api_access", kw))

    def invocations(self) -> list[dict]:
        return [kw for kind, kw in self.rows if kind == "tool_invocation"]


@dataclass
class World:
    http: TestClient
    other: _OtherProvider
    sel: _Sel
    server: Fixture
    invoked: list[str]

    @property
    def mcp_invoked(self) -> list[str]:
        """What the app's provider was asked to run (empty when it was never registered)."""
        return list(self.invoked)


@contextlib.asynccontextmanager
async def _world(
    server: Fixture,
    tmp_path: Path,
    monkeypatch,
    register: Callable[[Path], None] = register_directly,
) -> AsyncIterator[World]:
    """A home with the fixture server in ``mcp.json``, the app installed and registered by
    *register*, and the two routes the Tools page uses served for real."""
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard.handlers.tools import api_tool_invoke, api_tools_list

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    home = config_loader.config_dir()
    write_mcp_document(home / "mcp.json", {"mcpServers": {SERVER: server.spec}})
    # The owner's yes to what it runs (`mcp_grants`): the subject here is Try it.
    allow_configured(SERVER)
    # One client registry per test, drained at the end: a spawned stdio server must not outlive it.
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    other = _OtherProvider()
    tool_registry.register_provider(other)
    register(_BUNDLE)
    # A spy on the provider the registration put on the surface: what it was asked to run.
    invoked: list[str] = []
    provider = tool_registry.get_provider("mcp")
    if provider is not None:
        real_invoke = provider.invoke

        async def recording_invoke(tool_name: str, arguments: dict[str, Any]) -> Any:
            invoked.append(tool_name)
            return await real_invoke(tool_name, arguments)

        monkeypatch.setattr(provider, "invoke", recording_invoke)
    sel = _Sel()
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: sel)

    app = web.Application()
    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    try:
        async with TestClient(TestServer(app)) as http:
            yield World(http, other, sel, server, invoked)
    finally:
        registry = mcp_client._registry
        if registry is not None:
            await registry.shutdown_all()


async def _listed(w: World, name: str) -> dict:
    """The row the Tools page shows for *name*: what "Try it" sends back."""
    resp = await w.http.get("/api/tools")
    assert resp.status == 200
    rows = [t for t in (await resp.json())["tools"] if t["name"] == name]
    assert len(rows) == 1, f"the Tools page lists {len(rows)} rows for {name}"
    return rows[0]


async def _invoke(w: World, body: dict) -> tuple[int, dict]:
    resp = await w.http.post("/api/tools/invoke", json=body)
    return resp.status, await resp.json()


async def _try_it(w: World, row: dict, arguments: dict, **extra: Any) -> tuple[int, dict]:
    """The inspector's call, verbatim: the row's own name and provider."""
    return await _invoke(
        w, {"tool": row["name"], "arguments": arguments, "provider": row["provider"], **extra}
    )


# ── the defect, end to end ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_the_app_enabled_try_it_runs_the_external_tool_the_page_lists(
    fixture_server, tmp_path, monkeypatch
):
    """The whole path a user has: servers in ``mcp.json``, the MCP Tool Servers app enabled, a
    tool picked from the Tools page."""
    async with _world(fixture_server, tmp_path, monkeypatch, register_by_enabling) as w:
        row = await _listed(w, f"mcp/{SERVER}/hello")
        assert row["provider"] == SERVER, "precondition: the page labels the tool with its server"

        status, body = await _try_it(w, row, {"name": "claw"})

        assert status == 200, body
        assert body == {"ok": True, "output": "hello claw", "error": ""}
        assert w.mcp_invoked == [f"mcp/{SERVER}/hello"], "it did not go through the app's provider"
        [audit] = w.sel.invocations()
        assert (audit["tool_name"], audit["tool_kind"], audit["outcome"]) == (
            f"mcp/{SERVER}/hello",
            "mcp",
            "completed",
        )


# ── defect 2: the app's provider never reached an agent's surface ─────────────────────────────


@pytest.mark.asyncio
async def test_enabling_the_app_puts_every_configured_server_on_an_agents_surface(
    fixture_server, tmp_path, monkeypatch
):
    async with _world(fixture_server, tmp_path, monkeypatch, register_by_enabling):
        provider = tool_registry.get_provider("mcp")
        assert provider is not None, "enabling the MCP Tool Servers app registered no provider"
        surface = tool_registry.tool_surface(None)
        assert provider in surface
        names = {t.name for t in await provider.list_tools()}
        assert {f"mcp/{SERVER}/hello", f"mcp/{SERVER}/delete_stack"} <= names


@pytest.mark.asyncio
async def test_an_agent_reaches_the_same_tool_through_the_same_provider(
    fixture_server, tmp_path, monkeypatch
):
    """The parity the route is held to: a native agent turn over the surface ``provider_bridge``
    builds calls the same tool through the same provider and gets the same answer."""
    from test_native_runtime import _defn, _drain, _ScriptedModel

    from personalclaw.agents.native.builtin_tools import create_platform_tools_provider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    async with _world(fixture_server, tmp_path, monkeypatch, register_by_enabling) as w:
        model = _ScriptedModel(
            [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c1",
                        title=f"mcp/{SERVER}/hello",
                        tool_input='{"name": "claw"}',
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
            ]
        )
        surface = [create_platform_tools_provider(), *tool_registry.list_providers()]
        runtime = NativeAgentRuntime(
            definition=_defn(), model_provider=model, tool_providers=surface
        )
        runtime.set_approval_policy("auto")
        await runtime.start()
        events = await _drain(runtime, "say hello to claw")

        results = [e.tool_output for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results and "hello claw" in results[0], results
        assert w.mcp_invoked == [f"mcp/{SERVER}/hello"]

        status, body = await _try_it(w, await _listed(w, f"mcp/{SERVER}/hello"), {"name": "claw"})
        assert (status, body.get("output")) == (200, "hello claw"), body
        assert w.mcp_invoked == [f"mcp/{SERVER}/hello"] * 2


# ── defect 1: the route, with the provider registered ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_try_it_resolves_the_tool_by_name_not_by_the_server_label(
    fixture_server, tmp_path, monkeypatch
):
    async with _world(fixture_server, tmp_path, monkeypatch) as w:
        status, body = await _try_it(w, await _listed(w, f"mcp/{SERVER}/hello"), {"name": "claw"})

        assert (status, body.get("output")) == (200, "hello claw"), body
        assert w.mcp_invoked == [f"mcp/{SERVER}/hello"]


@pytest.mark.asyncio
async def test_a_named_provider_that_does_not_serve_the_tool_never_runs_it(
    fixture_server, tmp_path, monkeypatch
):
    """A ``provider`` is a preference among the providers that serve the name, never a second way
    to reach a provider. Naming one that does not serve the tool used to hand it the call anyway,
    past the tool's own declared risk and its own disable key."""
    async with _world(fixture_server, tmp_path, monkeypatch) as w:
        status, body = await _invoke(
            w,
            {
                "tool": f"mcp/{SERVER}/hello",
                "arguments": {"name": "claw"},
                "provider": w.other.name,
            },
        )
        assert w.other.invoked == [], "a provider that does not serve the tool was handed the call"
        assert (status, body.get("output")) == (200, "hello claw"), body


@pytest.mark.asyncio
async def test_a_disabled_server_is_out_of_reach_for_both(fixture_server, tmp_path, monkeypatch):
    """BASELINE (passes before and after): a server switched off in ``mcp.json`` offers no tools to
    an agent, so "Try it" cannot run them either."""
    from personalclaw.config import loader as config_loader

    async with _world(fixture_server, tmp_path, monkeypatch) as w:
        write_mcp_document(
            config_loader.config_dir() / "mcp.json",
            {"mcpServers": {SERVER: dict(w.server.spec, disabled=True)}},
        )
        status, body = await _invoke(
            w, {"tool": f"mcp/{SERVER}/hello", "arguments": {"name": "x"}, "provider": SERVER}
        )
        assert status == 404, body
        assert w.mcp_invoked == []


# ── the checks an agent's call gets ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_what_the_agent_is_refused_try_it_is_refused(fixture_server, tmp_path, monkeypatch):
    """The agent's hard deny-list (``security.is_denied``) is by tool NAME, and external servers are
    where those names live. This route never checked it, so a cron script's call (no provider, and
    the typed confirmation given) ran a ``delete_stack`` the agent is refused."""
    from personalclaw import security

    name = f"mcp/{SERVER}/delete_stack"
    assert security.is_denied(name), "precondition: the agent refuses this name"
    async with _world(fixture_server, tmp_path, monkeypatch) as w:
        status, body = await _invoke(
            w, {"tool": name, "arguments": {"name": "prod"}, "confirm_risk": "destructive"}
        )

        assert status == 403, body
        assert body["error"]["code"] == "tool_denied_by_policy"
        assert not w.server.ran_delete_stack(), "the server ran a call the agent is refused"
        [audit] = w.sel.invocations()
        assert (audit["tool_name"], audit["outcome"]) == (name, "denied")


@pytest.mark.asyncio
async def test_try_it_still_needs_the_typed_confirmation_for_a_destructive_external_tool(
    fixture_server, tmp_path, monkeypatch
):
    """The owner's own run keeps the destructive ceremony: the tool's declared risk comes from the
    provider that serves it, as the agent's approval card reads it."""
    import personalclaw.security as security_mod

    # Isolate the risk gate from the deny-list, which refuses `delete_stack` first.
    monkeypatch.setattr(
        security_mod,
        "BUILTIN_DENY_PATTERNS",
        [p for p in security_mod.BUILTIN_DENY_PATTERNS if p != "*delete_stack*"],
    )
    async with _world(fixture_server, tmp_path, monkeypatch) as w:
        row = await _listed(w, f"mcp/{SERVER}/delete_stack")
        assert row["risk_level"] == "destructive"
        status, body = await _try_it(w, row, {"name": "prod"})
        assert status == 403, body
        assert body["error"]["code"] == "risk_confirmation_required"
        assert not w.server.ran_delete_stack()

        status, body = await _try_it(w, row, {"name": "prod"}, confirm_risk="destructive")
        assert (status, body.get("output")) == (200, "deleted prod"), body
        assert w.server.ran_delete_stack()
