"""A tool server's card says what really happened when PersonalClaw started it, and a server that
keeps failing to start is stopped, not started again and again.

🔴 THE DEFECT (measured before this change). A stdio server whose command exited during its start
with a build error read "Not connected — timeout." on the Tools page: the probe had given up on it
while it was still building, and the agent's connections, which then saw it exit, kept what they
found to themselves ("Connection closed", in the log alone). Later the card read a raw traceback
line ("server exited: File …, line 184, in run_commands"): the probe kept the last line of the
first 4 KB its server wrote to its error output. And the spawn breaker did not hold: it was a 60 s
cooldown on one connection object, so every later turn, every new chat (a connection of its own)
and every look from the Tools page started the server again, each one rebuilding what it built.

The contract now, with every start recorded in one place (`mcp_discovery`, in the words of
`mcp_status`), by the probe and by an agent's connection alike:

* a server that exited before it answered says so, with its exit code and the one line of its
  error output that says why; the tail of that output is its ``detail``, which the page puts behind
  Details. "Did not answer" is said only of a server still running at the deadline;
* after ``mcp_status.STOP_AFTER`` failed starts in a row it is ``stopped``: nothing starts it again
  (no connection, no probe) until its owner presses Retry or its definition changes;
* every change of what a server's card says is announced (`mcp_status.subscribe`), which the
  dashboard turns into the ``refresh`` frame the Tools page re-reads on.

Every server here is a script this test wrote under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from personalclaw.dashboard.handlers import mcp as mcp_handlers
from personalclaw.dashboard.handlers import tools as tools_handlers
from personalclaw.tool_providers import registry as tool_registry

#: What the fake server writes to its error output before it exits: a traceback longer than the
#: 4 KB the probe used to read, whose last line is the plain cause.
_CAUSE = "RuntimeError: the fixture's native dependency did not build"
_FRAMES = "".join(
    f'  File "/home/user/.cache/build/step_{i}.py", line {i + 10}, in run_commands\n'
    f"    run_step({i})\n"
    for i in range(80)
)
_TRACEBACK = f"Traceback (most recent call last):\n{_FRAMES}{_CAUSE}\n"

#: How many failed starts in a row stop a server (`mcp_status.STOP_AFTER`), and the status it then
#: reads (`mcp_status.STOPPED`). Spelled out so the test reads the contract, not the constant.
STOP_AFTER = 3
STOPPED = "stopped"


class _Fixture:
    """A stdio server this test wrote. Each launch appends a line to a marker file; it then serves
    one tool once ``fixed`` exists, and otherwise does what *mode* says: write a long traceback and
    exit 3 (``exit``), or answer nothing and keep running (``hang``)."""

    def __init__(self, root: Path, name: str, mode: str) -> None:
        self.name = name
        self.marker = root / f"{name}.launches"
        self.fixed = root / f"{name}.fixed"
        self.script = root / f"{name}_server.py"
        self.script.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(f"""
                import os, sys, time

                with open({str(self.marker)!r}, "a", encoding="utf-8") as fh:
                    fh.write("launched\\n")

                if not os.path.exists({str(self.fixed)!r}):
                    if {mode!r} == "exit":
                        sys.stderr.write({_TRACEBACK!r})
                        sys.stderr.flush()
                        sys.exit(3)
                    time.sleep(120)
                    sys.exit(0)

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
        return (
            len(self.marker.read_text(encoding="utf-8").splitlines()) if self.marker.exists() else 0
        )

    def spec(self) -> dict[str, Any]:
        return {"command": str(self.script), "args": ["--fixture"]}


@contextlib.asynccontextmanager
async def _tools_page(monkeypatch) -> AsyncIterator[TestClient]:
    """The routes the Tools page calls, over a home with no server at all."""
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_discovery, "_probing", {})
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
    app.router.add_post("/api/mcp/probe/{name}", mcp_handlers.api_mcp_probe_one)
    app.router.add_post("/api/mcp/probe", mcp_handlers.api_mcp_probe)
    app.router.add_get("/api/tools", tools_handlers.api_tools_list)
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
    tasks = http.app["state"]._background_tasks
    while tasks:
        await asyncio.gather(*list(tasks), return_exceptions=True)


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


async def _agent_lists(name: str, session: str) -> list[Any]:
    """What an agent's turn in chat *session* does with the server: open its connection and list
    its tools."""
    conn = mcp_client.get_mcp_client_registry().get(name, session)
    assert conn is not None
    return await conn.list_tools()


@pytest.mark.asyncio
async def test_a_server_that_exited_says_it_exited_with_its_code_and_cause(
    tmp_path, monkeypatch
) -> None:
    server = _Fixture(tmp_path, "builds", "exit")
    async with _tools_page(monkeypatch) as http:
        await _save(http, "builds", server.spec())
        await _settled(http)
        row = await _row(http, "builds")
        assert row["status"] == "error", row
        assert "exited with code 3" in row["error"], row["error"]
        assert _CAUSE in row["error"], row["error"]
        assert "timeout" not in row["error"].lower()
        # The headline is one line; the traceback is the detail, behind Details.
        assert "\n" not in row["error"] and 'File "' not in row["error"], row["error"]
        assert _CAUSE in row["detail"] and "run_commands" in row["detail"], row
        assert server.launches == 1

        # An agent's connection that finds it exited says the same, and the card says what it found.
        assert await _agent_lists("builds", "chat-1") == []
        conn = mcp_client.get_mcp_client_registry().get("builds", "chat-1")
        assert "exited with code 3" in conn.error and _CAUSE in conn.error, conn.error
        row = await _row(http, "builds")
        assert "exited with code 3" in row["error"] and _CAUSE in row["error"], row


@pytest.mark.asyncio
async def test_did_not_answer_is_said_only_of_a_server_still_running_at_the_deadline(
    tmp_path, monkeypatch
) -> None:
    server = _Fixture(tmp_path, "slow", "hang")
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: 2)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "slow", server.spec())
        await _settled(http)
        row = await _row(http, "slow")
        assert row["status"] == "error", row
        assert "did not answer within 2 seconds" in row["error"], row["error"]
        assert "exited" not in row["error"]
        assert server.launches == 1


@pytest.mark.asyncio
async def test_a_server_that_keeps_failing_to_start_is_stopped_until_its_owner_retries(
    tmp_path, monkeypatch
) -> None:
    server = _Fixture(tmp_path, "builds", "exit")
    async with _tools_page(monkeypatch) as http:
        await _save(http, "builds", server.spec())  # the probe after the save: start 1
        await _settled(http)
        await _agent_lists("builds", "chat-1")  # an agent's turn: start 2
        await _agent_lists("builds", "chat-2")  # a turn in another chat: start 3
        assert server.launches == STOP_AFTER

        # Stopped: nothing starts it again. Not another turn in either chat, not a new chat, not
        # the Tools page's own periodic look.
        for session in ("chat-1", "chat-2", "chat-3", ""):
            assert await _agent_lists("builds", session) == []
        recheck = mcp_discovery.recheck(
            ["builds"], hold=http.app["state"]._background_tasks, forget=False
        )
        assert recheck is not None
        await recheck
        await _settled(http)
        assert server.launches == STOP_AFTER, "a stopped server was started again"

        row = await _row(http, "builds")
        assert row["status"] == STOPPED, row
        assert "stopped trying" in row["error"] and "Retry" in row["error"], row["error"]
        assert "exited with code 3" in row["error"], "the stop says what the last start found"
        assert _CAUSE in row["detail"], row
        conn = mcp_client.get_mcp_client_registry().get("builds", "chat-4")
        ok, said = await conn.call_tool("hello", {"name": "x"})
        assert not ok and "stopped trying" in said, said
        assert server.launches == STOP_AFTER

        # Retry starts it again: once, and it is fixed now.
        server.fixed.touch()
        retried = await http.post("/api/mcp/probe/builds")
        assert retried.status == 200, await retried.text()
        assert server.launches == STOP_AFTER + 1
        row = await _row(http, "builds")
        assert row["status"] == mcp_discovery.UNSERVED, row  # connected; this test serves no agent
        assert await _agent_lists("builds", "chat-1") != []


@pytest.mark.asyncio
async def test_re_probing_every_server_is_a_retry_of_each(tmp_path, monkeypatch) -> None:
    """The page's "Re-probe MCP servers" is its owner asking about every server: a stopped one
    is started once more, as its own Retry would."""
    server = _Fixture(tmp_path, "builds", "exit")
    async with _tools_page(monkeypatch) as http:
        await _save(http, "builds", server.spec())
        await _settled(http)
        await _agent_lists("builds", "chat-1")
        await _agent_lists("builds", "chat-2")
        assert server.launches == STOP_AFTER
        assert (await _row(http, "builds"))["status"] == STOPPED

        reprobed = await http.post("/api/mcp/probe")
        assert reprobed.status == 200, await reprobed.text()
        assert server.launches == STOP_AFTER + 1
        assert (await _row(http, "builds"))["status"] == "error"


@pytest.mark.asyncio
async def test_a_changed_definition_is_started_again(tmp_path, monkeypatch) -> None:
    server = _Fixture(tmp_path, "builds", "exit")
    async with _tools_page(monkeypatch) as http:
        await _save(http, "builds", server.spec())
        await _settled(http)
        await _agent_lists("builds", "chat-1")
        await _agent_lists("builds", "chat-2")
        assert server.launches == STOP_AFTER
        assert (await _row(http, "builds"))["status"] == STOPPED

        await _save(http, "builds", {**server.spec(), "args": ["--fixture", "--verbose"]})
        await _settled(http)
        assert server.launches == STOP_AFTER + 1, "an edited server is a new one, and is started"
        assert (await _row(http, "builds"))["status"] == "error"


@pytest.mark.asyncio
async def test_every_change_of_what_a_card_says_is_announced(tmp_path, monkeypatch) -> None:
    from personalclaw import mcp_status

    heard: list[str] = []
    mcp_status.subscribe(heard.append)
    try:
        server = _Fixture(tmp_path, "builds", "exit")
        async with _tools_page(monkeypatch) as http:
            await _save(http, "builds", server.spec())
            assert "builds" in heard, "a server being checked is announced"
            heard.clear()
            await _settled(http)
            assert "builds" in heard, "the probe's result is announced"
            heard.clear()
            await _agent_lists("builds", "chat-1")
            assert heard == ["builds"], "a connection's failure is announced once"
    finally:
        mcp_status.unsubscribe(heard.append)


@pytest.mark.asyncio
async def test_the_tool_list_is_not_held_by_a_server_that_is_still_starting(
    tmp_path, monkeypatch
) -> None:
    """After an Allow the Tools page reads its servers and its tools together. The tool list asked
    the MCP Tool Servers provider for every external tool — which starts each server and waits for
    it to answer — and then threw that answer away, so the page sat on its old cards for as long as
    a server took to start (31 s, measured)."""
    import time

    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    server = _Fixture(tmp_path, "slow", "hang")
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: 1)
    monkeypatch.setattr(mcp_client, "_CONNECT_TIMEOUT_SECS", 8.0)
    monkeypatch.setattr(tools_handlers, "_MCP_LIST_TIMEOUT_SECS", 0.5)
    async with _tools_page(monkeypatch) as http:
        module = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")
        tool_registry.register_provider(module.create_mcp_provider({}), app="mcp-tools")
        await _save(http, "slow", server.spec())
        await _settled(http)

        began = time.monotonic()
        resp = await http.get("/api/tools")
        took = time.monotonic() - began
        assert resp.status == 200, await resp.text()
        assert took < 4.0, f"the tool list waited {took:.1f}s on a server that had not answered"
