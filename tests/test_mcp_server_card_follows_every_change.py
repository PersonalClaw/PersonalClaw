"""A tool server's card says what the server is now: after every add, edit, Allow, removal and
switch-on, whatever wrote it.

🔴 THE DEFECT (measured before this change). The status each card shows came from two caches
keyed by the server's NAME alone, and no write touched either: the probe's own
(``mcp_discovery._probe_cache``) and the Tools page handler's copy of every row
(``_mcp_probe_cache``), which it laid over the first whenever that one said "unknown". So:

* after an Allow, or a save that allowed an edit, the card read "unknown" beside the tools the
  server listed, until the owner pressed Re-probe;
* an edit that broke a server left it reading "ready";
* a server removed and added again under its name showed the removed one's error ("command not
  found: docker") beside the new one's tools.

The contract now: a probe's result is a result of one DEFINITION, and a server is only ever shown
a result for what it is now. Every write that changes a server probes it again, and until that
lands it reads ``probing``, which the page answers by reading again.

Every server here is a script this test wrote, or a command that cannot resolve.
"""

from __future__ import annotations

import contextlib
import json
import sys
import textwrap
import types
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import confirmed

from personalclaw import mcp_client, mcp_discovery
from personalclaw.cancellation import settle
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.dashboard.handlers import mcp as mcp_handlers
from personalclaw.tool_providers import registry as tool_registry

UNRESOLVABLE = "/nonexistent/pc-fixture-mcp"


class _Server:
    """A stdio MCP server this test wrote: it records each launch, then serves one tool."""

    def __init__(self, root: Path, name: str = "notes") -> None:
        self.name = name
        self.log = root / f"{name}.launches"
        self.script = root / f"{name}_server.py"
        self.script.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(f"""
                with open({str(self.log)!r}, "a", encoding="utf-8") as fh:
                    fh.write("launched\\n")

                from mcp.server.fastmcp import FastMCP

                mcp = FastMCP("fixture")


                @mcp.tool(description="say hello")
                def hello(name: str) -> str:
                    return "hello " + name


                mcp.run(transport="stdio")
                """),
            encoding="utf-8",
        )
        self.script.chmod(0o755)

    @property
    def launches(self) -> int:
        return len(self.log.read_text(encoding="utf-8").splitlines()) if self.log.exists() else 0

    def spec(self) -> dict[str, Any]:
        return {"command": str(self.script), "args": ["--fixture"]}


@contextlib.asynccontextmanager
async def _tools_page(monkeypatch) -> AsyncIterator[TestClient]:
    """The routes the Tools page calls, over a home with no server at all."""
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    # (`raising=False`: without the fix there is no count of probes in flight, and the test fails on
    # what the card says rather than on the patch.)
    monkeypatch.setattr(mcp_discovery, "_probing", {}, raising=False)
    # The last every-server probe is recent: the reads here start only what a change needs.
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_ts", float("inf"))
    # The agent config's rebuild adds PersonalClaw's own server, which a probe would start.
    monkeypatch.setattr(mcp_handlers, "_rebuild_agent_config_logged", lambda: None)
    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda **_: None)
    for registry_state in ("_providers", "_provider_app", "_registrations", "_claims"):
        monkeypatch.setattr(tool_registry, registry_state, {})
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    app.router.add_get("/api/mcp", mcp_handlers.api_mcp_servers)
    app.router.add_route("*", "/api/mcp/servers/{name}", mcp_handlers.api_mcp_server_detail)
    app.router.add_post("/api/mcp/servers/{name}/allow", mcp_handlers.api_mcp_server_allow)
    app.router.add_post("/api/mcp/toggle", mcp_handlers.api_mcp_toggle)
    try:
        async with TestClient(TestServer(app)) as http:
            yield http
            await _settled(http)
    finally:
        registry = mcp_client._registry
        if registry is not None:
            await registry.shutdown_all()


async def _settled(http: TestClient) -> None:
    """Every probe a write or a read started, finished."""
    await settle(http.app["state"]._background_tasks)


async def _row(http: TestClient, name: str) -> dict[str, Any]:
    resp = await http.get("/api/mcp")
    assert resp.status == 200, await resp.text()
    [row] = [r for r in await resp.json() if r["name"] == name]
    return row


async def _save(http: TestClient, name: str, body: dict[str, Any]) -> None:
    """The edit form's save: over the revision it read, once its owner agreed."""
    read = await http.get(f"/api/mcp/servers/{name}")
    headers = {"If-Match": (await read.json())["revision"]} if read.status == 200 else {}
    resp = await http.put(f"/api/mcp/servers/{name}", json=confirmed(body), headers=headers)
    assert resp.status == 200, await resp.text()


def _tool_names(row: dict[str, Any]) -> list[str]:
    return [t["name"] if isinstance(t, dict) else t for t in row["tools"]]


#: What a server this test runs reads once its probe found it: it connected, and nothing on this
#: test's agent surface serves external MCP tools (`mcp_discovery.as_agents_see_it`).
CONNECTED = mcp_discovery.UNSERVED


@pytest.mark.asyncio
async def test_a_saved_server_reads_probing_and_then_what_its_probe_found(
    tmp_path, monkeypatch
) -> None:
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "notes", server.spec())
        assert (await _row(http, "notes"))["status"] == "probing"
        await _settled(http)
        row = await _row(http, "notes")
        assert row["status"] == CONNECTED and _tool_names(row) == ["hello"], row
        assert server.launches == 1


@pytest.mark.asyncio
async def test_an_edit_that_breaks_a_server_does_not_leave_it_reading_ready(
    tmp_path, monkeypatch
) -> None:
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "notes", server.spec())
        await _settled(http)
        assert (await _row(http, "notes"))["status"] == CONNECTED

        await _save(http, "notes", {"command": UNRESOLVABLE, "args": ["--fixture"]})
        await _settled(http)
        row = await _row(http, "notes")
        assert row["status"] == "error", row
        assert row["error"] == f"command not found: {UNRESOLVABLE}"
        assert row["tools"] == [], "the tools of the definition before the edit"


@pytest.mark.asyncio
async def test_a_server_added_again_under_a_removed_ones_name_never_shows_its_state(
    tmp_path, monkeypatch
) -> None:
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "gh", {"command": UNRESOLVABLE})
        await _settled(http)
        failed = await _row(http, "gh")
        assert failed["error"] == f"command not found: {UNRESOLVABLE}"
        # A failed probe is kept like any other: the next read says it, and starts no probe.
        again = await _row(http, "gh")
        assert again["error"] == failed["error"] and not http.app["state"]._background_tasks

        assert (await http.delete("/api/mcp/servers/gh")).status == 200
        await _save(http, "gh", server.spec())
        row = await _row(http, "gh")
        assert row["status"] == "probing" and row["error"] == "", row
        await _settled(http)
        row = await _row(http, "gh")
        assert row["status"] == CONNECTED and _tool_names(row) == ["hello"], row
        assert UNRESOLVABLE not in json.dumps(row)


@pytest.mark.asyncio
async def test_allow_and_switch_on_read_probing_until_the_probe_lands(
    tmp_path, monkeypatch
) -> None:
    from personalclaw.config import loader as config_loader

    server = _Server(tmp_path)
    # Written by something other than the owner (a hand edit): it waits for Allow.
    write_mcp_document(
        config_loader.config_dir() / "mcp.json", {"mcpServers": {"notes": server.spec()}}
    )
    async with _tools_page(monkeypatch) as http:
        waiting = await _row(http, "notes")
        assert waiting["status"] == "waiting"
        allowed = await http.post(
            "/api/mcp/servers/notes/allow",
            json={"revision": waiting["allowRevision"], "confirm": True},
        )
        assert allowed.status == 200, await allowed.text()
        assert (await _row(http, "notes"))["status"] == "probing"
        await _settled(http)
        assert (await _row(http, "notes"))["status"] == CONNECTED

        off = await http.post("/api/mcp/toggle", json={"name": "notes", "enabled": False})
        assert off.status == 200, await off.text()
        assert (await _row(http, "notes"))["status"] == "disabled"
        on = await http.post("/api/mcp/toggle", json={"name": "notes", "enabled": True})
        assert on.status == 200, await on.text()
        assert (await _row(http, "notes"))["status"] == "probing"
        await _settled(http)
        row = await _row(http, "notes")
        assert row["status"] == CONNECTED and _tool_names(row) == ["hello"], row
