"""An MCP call that never gets an answer frees its connection: the next call is sent and answered.

A connection to an MCP server sends one call at a time (``McpServerConn._serve``). When a server
never answered a call (it dropped the response stream, which the SDK's Streamable HTTP client never
reports), its caller was told after the deadline that the call may have gone through, but the
connection's worker went on waiting, with no deadline of its own. Every later call queued behind it
and, at its own deadline, was dropped as "not sent", for as long as calls kept coming, and each one
kept the connection fresh so the idle sweep never reaped it. Only a Reconnect, a change to the
server or a restart freed it.

Now the worker waits for a call at most as long as its caller does (the SDK's read timeout). A
call that timed out may have gone through, as before, and is never read as the server's refusal;
the connection is kept, so a stateful server keeps its session; and the next call is sent on it.

Driven against a real MCP server over Streamable HTTP, on this machine's loopback, through the
native client the way an agent's call reaches it.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time

import port_guard
import pytest
from mcp_owner_allowed import allow_configured
from test_mcp_remote_servers_connect_with_their_headers import home  # noqa: F401 - a fixture

from personalclaw import mcp_client
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

NAME = "hangs-once"

# A real MCP server at a URL: ``hang`` never answers, ``hello`` answers at once, ``slow`` answers
# after half a second.
_SERVER = textwrap.dedent("""
    import asyncio, os, socket, sys

    import uvicorn
    from mcp.server.fastmcp import FastMCP

    PORT_FILE = sys.argv[1]
    mcp = FastMCP("hangs-once")


    @mcp.tool(description="never answers")
    async def hang() -> str:
        await asyncio.Event().wait()
        return "never"


    @mcp.tool(description="greet someone")
    def hello(name: str) -> str:
        return f"hello {name}"


    @mcp.tool(description="answers after half a second")
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "slow but here"


    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    with open(PORT_FILE + ".tmp", "w") as f:
        f.write(str(sock.getsockname()[1]))
    os.replace(PORT_FILE + ".tmp", PORT_FILE)
    app = mcp.streamable_http_app()
    uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)


@pytest.fixture
def server(tmp_path, home):  # noqa: F811 - the imported fixture
    script = tmp_path / "hangs_once_server.py"
    script.write_text(_SERVER, encoding="utf-8")
    port_file = tmp_path / "port"
    stderr = tmp_path / "server.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(script), str(port_file)],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the server did not start: {stderr.read_text()!r}")
            time.sleep(0.05)
        port = int(port_file.read_text(encoding="utf-8").strip())
        port_guard.GUARD.own(port)  # the server this test started chose it
        write_mcp_document(
            home / "mcp.json",
            {"mcpServers": {NAME: {"type": "http", "url": f"http://127.0.0.1:{port}/mcp"}}},
        )
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _calls(*calls: tuple[str, dict]) -> list[tuple[bool, str, float]]:
    """Each call in turn on one connection, the way an agent's calls reach it: its answer, and
    how long it took."""

    async def run() -> list[tuple[bool, str, float]]:
        reg = McpClientRegistry()
        try:
            allow_configured(NAME)
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get(NAME)
            assert conn is not None
            out = []
            for tool, args in calls:
                began = time.monotonic()
                ok, text = await conn.call_tool(tool, args)
                out.append((ok, text, time.monotonic() - began))
            return out
        finally:
            await reg.shutdown_all()

    return asyncio.run(run())


@pytest.fixture
def one_second(monkeypatch):
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", 1.0)


def test_the_call_after_one_that_got_no_answer_is_sent_and_answered(server, one_second):
    """🔴 Red before: the second call queued behind the first and was dropped as not sent."""
    (hung_ok, hung, _), (ok, hello, took) = _calls(("hang", {}), ("hello", {"name": "claw"}))

    assert hung_ok is True and "may have gone through" in hung, hung
    assert (ok, hello) == (True, "hello claw"), hello
    assert took < 2.0, f"the next call took {took:.1f}s"


def test_the_connection_is_kept_after_a_call_that_got_no_answer(server, one_second, caplog):
    """The next calls go on the same connection, and the timed-out call is logged once."""
    import logging

    with caplog.at_level(logging.WARNING, logger="personalclaw.mcp_client"):
        answers = _calls(("hang", {}), ("hello", {"name": "a"}), ("hello", {"name": "b"}))

    assert [text for _ok, text, _t in answers[1:]] == ["hello a", "hello b"]
    said = [r.getMessage() for r in caplog.records if "did not answer 'hang'" in r.getMessage()]
    assert len(said) == 1, said
    assert not any("starts it again" in r.getMessage() for r in caplog.records)


def test_a_slow_answer_inside_the_deadline_is_the_answer(server, one_second):
    ((ok, text, _),) = _calls(("slow", {}))

    assert (ok, text) == (True, "slow but here")


def test_a_timed_out_call_is_never_read_as_the_servers_refusal():
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    timed_out = McpError(ErrorData(code=408, message="Timed out while waiting for response"))
    refused = McpError(ErrorData(code=-32602, message="bad arguments"))

    assert mcp_client._read_timed_out(timed_out)
    assert not mcp_client._read_timed_out(refused)
