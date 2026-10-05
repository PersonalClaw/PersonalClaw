"""Sign in is offered only where a server has one; a server that takes a token says what it wants.

🔴 THE DEFECT (measured before this change). A remote server that authenticates with a static
token (a personal access token, an API key) answers a request without one with ``401`` and
``WWW-Authenticate: Bearer error="invalid_token"`` — the same answer an OAuth server gives. The
probe read every such answer as "sign-in needed", so the card said "asks you to sign in before its
tools can be used" and offered Sign in. Pressing it made the gateway look for the server's OAuth
metadata, find none, and refuse with ``409 mcp_sign_in_not_offered`` ("…does not publish how to
sign in… If it takes an API key, add it to the server as a header instead."). That sentence went to
a toast at most; the card kept offering the same Sign in, and an agent's failed call was told to
"Use Sign in on its card".

The contract now: the probe asks the server how it signs in the way Sign in asks
(`mcp_oauth.discover`), and only a server that says reads ``signin``. One that does not reads
``error`` with the refusal's own sentence, so the card offers nothing that cannot start and says
what to do. A Sign in the server refuses anyway probes it again, so the card follows. And the
connection's own sentence claims no sign-in its answer did not advertise.

The server here is the SDK's ``FastMCP`` over Streamable HTTP behind a token gate, in a child this
test starts on the loopback address.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import port_guard
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import allow_configured

from personalclaw import mcp_client, mcp_discovery
from personalclaw.cancellation import settle
from personalclaw.config import loader as config_loader
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.dashboard.handlers import mcp as mcp_handlers

NAME = "tracker"
PAT = "fixture-static-token-5e6f7a8b9c0d1e2f"

# A server that takes a static token only: every request without it answers 401 with a bare
# Bearer challenge, and no OAuth metadata is served anywhere.
_TOKEN_SERVER = textwrap.dedent("""
    import os, socket, sys

    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse

    PORT_FILE, TOKEN = sys.argv[1:3]
    mcp = FastMCP("token-only")


    @mcp.tool(description="get an issue")
    def get_issue(number: int) -> str:
        return f"issue {number}"


    inner = mcp.streamable_http_app()


    async def app(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if scope["path"].rstrip("/") != "/mcp":
                await JSONResponse({"message": "Not Found"}, status_code=404)(scope, receive, send)
                return
            if headers.get("authorization") != "Bearer " + TOKEN:
                await JSONResponse(
                    {"message": "Bad credentials"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )(scope, receive, send)
                return
        await inner(scope, receive, send)


    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    with open(PORT_FILE + ".tmp", "w") as f:
        f.write(str(sock.getsockname()[1]))
    os.replace(PORT_FILE + ".tmp", PORT_FILE)
    uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)


@pytest.fixture
def token_server(tmp_path: Path):
    script = tmp_path / "token_server.py"
    script.write_text(_TOKEN_SERVER, encoding="utf-8")
    port_file = tmp_path / "port"
    stderr = tmp_path / "server.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(script), str(port_file), PAT],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the token server did not start: {stderr.read_text()!r}")
            time.sleep(0.05)
        port = int(port_file.read_text(encoding="utf-8").strip())
        port_guard.GUARD.own(port)  # the server this test started chose it
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _configure(home: Path, url: str, **extra) -> None:
    write_mcp_document(
        home / "mcp.json", {"mcpServers": {NAME: {"type": "http", "url": url, **extra}}}
    )
    allow_configured(NAME)  # the owner's yes (`mcp_grants`): the subject is the probe


def _probe() -> mcp_discovery.McpServerInfo:
    info = asyncio.run(mcp_discovery.probe_one(NAME))
    assert info is not None
    return info


def test_a_server_that_takes_a_token_reads_what_it_wants_and_offers_no_sign_in(
    home, token_server
) -> None:
    _configure(home, token_server)
    info = _probe()
    assert info.status == "error", (info.status, info.error)
    assert "does not publish how to sign in" in info.error, info.error
    assert "add it to the server as a header" in info.error, info.error
    # The card draws a Sign in only for a server whose row carries a sign-in to do.
    assert "auth" not in mcp_handlers._with_sign_in(info.to_dict(), info)


def test_with_its_token_as_a_header_the_same_server_connects(home, token_server) -> None:
    _configure(home, token_server, headers={"Authorization": f"Bearer {PAT}"})
    info = _probe()
    assert info.status == "ok", (info.status, info.error)
    assert [t["name"] for t in info.tools] == ["get_issue"]


def test_an_agent_refused_by_it_is_not_told_to_use_a_sign_in(home, token_server) -> None:
    """What an agent's failed call says: a sign-in OR a token, since the answer did not say."""
    _configure(home, token_server)

    async def call() -> tuple[bool, str]:
        reg = mcp_client.McpClientRegistry()
        try:
            reg.load_from_specs(mcp_client._personalclaw_mcp_specs())
            conn = reg.get(NAME)
            assert conn is not None
            return await conn.call_tool("get_issue", {"number": 412})
        finally:
            await reg.shutdown_all()

    ok, said = asyncio.run(call())
    assert not ok
    assert "Use Sign in" not in said, said
    assert "wants a sign-in or a token" in said, said


@pytest.mark.asyncio
async def test_a_sign_in_it_refuses_probes_it_again_so_the_card_says_why(
    home, token_server, monkeypatch
) -> None:
    """A card that still offers Sign in (a probe from before, or one that could not finish its
    look) is corrected by the refusal: the server is probed again, and the card follows."""
    _configure(home, token_server)
    [listed] = [s for s in mcp_discovery.list_servers() if s.name == NAME]
    listed.status, listed.error = "signin", "tracker needs you to sign in."
    mcp_discovery._cache_probe(listed)
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_ts", float("inf"))
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    app.router.add_get("/api/mcp", mcp_handlers.api_mcp_servers)
    app.router.add_post("/api/mcp/servers/{name}/sign-in", mcp_handlers.api_mcp_server_sign_in)

    async def row(http: TestClient) -> dict:
        [found] = [r for r in await (await http.get("/api/mcp")).json() if r["name"] == NAME]
        return found

    async with TestClient(TestServer(app)) as http:
        assert (await row(http))["auth"] == {"method": "oauth", "state": "required"}
        origin = f"http://127.0.0.1:{http.server.port}"
        resp = await http.post(
            f"/api/mcp/servers/{NAME}/sign-in", json={}, headers={"Origin": origin}
        )
        assert resp.status == 409, await resp.text()
        refusal = (await resp.json())["error"]
        assert refusal["code"] == "mcp_sign_in_not_offered"
        await settle(app["state"]._background_tasks)
        after = await row(http)
        assert after["status"] == "error" and "auth" not in after, after
        assert after["error"] == refusal["message"], "the card says what the refusal said"
    registry = mcp_client._registry
    if registry is not None:
        await registry.shutdown_all()
