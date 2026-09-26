"""A server at a URL imports, saves, edits and connects over its own transport, with its headers.

🔴 THE DEFECT (measured on ``origin/main``). A remote MCP server never worked. Claude Code writes
one as ``{"type": "http" | "sse", "url": …, "headers": {…}}``, and the import copied that into
``mcp.json`` and moved the header values into the credential store. Then:

* the native client read only ``transport`` (``mcp_client._open_transport``), so every
  ``"type": "http"`` server was opened with the SSE client, which a Streamable HTTP endpoint
  refuses;
* neither transport client was given the headers (``streamablehttp_client(url)``,
  ``sse_client(url)``), so a server that authenticates by header answered 401 over both;
* the probe posted a hand-written Streamable HTTP exchange to every URL: an SSE server always read
  as an error, and a stateful Streamable HTTP server read ``ok`` with no tools, because the session
  id was dropped between ``initialize`` and ``tools/list``;
* the edit form refused every server at a URL, and ``PUT /api/mcp/servers/{name}`` required a
  ``command``, so the Tools page could neither add nor edit one; and
* the copy for Claude Code (``~/.mcp.json``) wrote a remote server as ``{"command": "", "type":
  "stdio"}``, and the provider card turned a Streamable HTTP server into an SSE one on save.

Every connection here is to a real MCP server — the SDK's ``FastMCP`` behind uvicorn, over
Streamable HTTP and over SSE — which records the headers of every request it gets and refuses one
without the ``Authorization`` it currently accepts.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.config.secret_refs import mcp_server_prefix, ref_key, write_mcp_document
from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs, mcp_sdk_available

pytestmark = pytest.mark.skipif(not mcp_sdk_available(), reason="requires the 'mcp' SDK extra")

NAME = "remote-fixture"
AUTH = "Bearer fixture-remote-auth-0f1e2d3c4b5a69788796a5b4"
NEW_AUTH = "Bearer fixture-rotated-auth-11223344556677889900aabb"
KEY = "fixture-api-key-9a8b7c6d5e4f3a2b1c0d"

# A real MCP server at a URL. It re-reads the one `Authorization` value it accepts on every request,
# so a test can rotate the token, and logs each request's method, path and the two headers.
_FAKE_SERVER = textwrap.dedent("""
    import json, os, socket, sys

    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import PlainTextResponse

    TRANSPORT, PORT_FILE, LOG_FILE, ACCEPT_FILE = sys.argv[1:5]
    mcp = FastMCP("fake-remote")


    @mcp.tool(description="greet someone")
    def hello(name: str) -> str:
        return f"hello {name}"


    inner = mcp.streamable_http_app() if TRANSPORT == "http" else mcp.sse_app()


    async def app(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps({
                    "method": scope["method"],
                    "path": scope["path"],
                    "authorization": headers.get("authorization"),
                    "x-api-key": headers.get("x-api-key"),
                }) + "\\n")
            with open(ACCEPT_FILE) as f:
                accepted = f.read().strip()
            if headers.get("authorization") != accepted:
                await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
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


@dataclass
class Remote:
    transport: str
    url: str
    log: Path
    accept_file: Path

    def accept(self, value: str) -> None:
        self.accept_file.write_text(value, encoding="utf-8")

    def requests(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def forget(self) -> None:
        self.log.unlink(missing_ok=True)


@pytest.fixture(params=["http", "sse"])
def remote(request, tmp_path):
    transport = request.param
    script = tmp_path / "fake_remote_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    port_file = tmp_path / f"port.{transport}"
    served = Remote(
        transport, "", tmp_path / f"requests.{transport}.jsonl", tmp_path / f"accept.{transport}"
    )
    served.accept(AUTH)
    stderr = tmp_path / f"server.{transport}.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(script),
                transport,
                str(port_file),
                str(served.log),
                str(served.accept_file),
            ],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the fake server did not start: {stderr.read_text()!r}")
            time.sleep(0.05)
        port = port_file.read_text(encoding="utf-8").strip()
        served.url = f"http://127.0.0.1:{port}{'/mcp' if transport == 'http' else '/sse'}"
        yield served
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    # Claude Code's own file is never the real one, whatever a code path under test reads.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    home = config_loader.config_dir()
    # `rebuild_agent_config` reads `agent._USER_DIR / "mcp.json"`, frozen at import.
    monkeypatch.setattr("personalclaw.agent._USER_DIR", home)
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _servers(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})


def _call(method: str, body: dict | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request(method, f"/api/mcp/servers/{NAME}", match_info={"name": NAME})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def _say_hello() -> tuple[bool, str]:
    """A tool call the way an agent makes one: the native client, from ``mcp.json``."""

    async def run() -> tuple[bool, str]:
        reg = McpClientRegistry()
        try:
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get(NAME)
            assert conn is not None, "the native client has no connection for the server"
            return await conn.call_tool("hello", {"name": "claw"})
        finally:
            await reg.shutdown_all()

    return asyncio.run(run())


def _probe():
    from personalclaw.mcp_discovery import probe_one

    return asyncio.run(probe_one(NAME))


def _assert_every_request_carried(remote: Remote, auth: str, key: str | None = KEY) -> None:
    seen = remote.requests()
    assert seen, "the server was never reached"
    for request in seen:
        assert request["authorization"] == auth, f"a request went without the header: {request}"
        if key is not None:
            assert request["x-api-key"] == key, f"a request went without X-Api-Key: {request}"


def test_a_remote_server_connects_over_its_transport_with_its_headers(home, remote) -> None:
    """Stored as Import and Add store it — ``type``, ``url``, header values in the credential
    store — the server connects for an agent, and the probe the Tools page shows agrees."""
    write_mcp_document(
        home / "mcp.json",
        {
            "mcpServers": {
                NAME: {
                    "type": remote.transport,
                    "url": remote.url,
                    "headers": {"Authorization": AUTH, "X-Api-Key": KEY},
                }
            }
        },
    )
    raw = (home / "mcp.json").read_text(encoding="utf-8")
    assert AUTH not in raw and KEY not in raw, "a header value stayed in mcp.json"

    assert _say_hello() == (True, "hello claw")
    _assert_every_request_carried(remote, AUTH)

    remote.forget()
    info = _probe()
    assert info is not None and info.status == "ok", f"the probe says {info and info.error!r}"
    assert [t["name"] for t in info.tools] == ["hello"]
    _assert_every_request_carried(remote, AUTH)


def test_a_refused_connection_says_why_in_a_sentence(home, remote) -> None:
    remote.accept("Bearer some-other-token-the-server-wants")
    write_mcp_document(
        home / "mcp.json",
        {"mcpServers": {NAME: {"type": remote.transport, "url": remote.url + "?token=abc123XYZ"}}},
    )
    info = _probe()
    assert info is not None and info.status == "error"
    assert info.error == "HTTP 401 Unauthorized", info.error


def test_the_tools_page_adds_edits_and_rotates_a_remote_server(home, remote) -> None:
    resp = _call(
        "PUT",
        {
            "transport": remote.transport,
            "url": remote.url,
            "headers": {"Authorization": AUTH, "X-Api-Key": KEY},
        },
    )
    assert resp.status == 200, resp.text
    spec = _servers(home / "mcp.json")[NAME]
    assert spec["type"] == remote.transport and spec["url"] == remote.url
    assert set(spec["headers"]) == {"Authorization", "X-Api-Key"}
    for name, value in spec["headers"].items():
        assert ref_key(value), f"{name} is not a credential-store reference: {spec}"
    for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
        text = doc.read_text(encoding="utf-8")
        assert AUTH not in text and KEY not in text, f"a header value reached {doc.name}"
    assert _servers(home / "agents" / "personalclaw.json")[NAME]["headers"] == spec["headers"]

    # The edit form reads the server — names and whether a value is saved, never a value.
    resp = _call("GET")
    assert resp.status == 200, resp.text
    assert AUTH not in resp.text and KEY not in resp.text, "a stored value reached the browser"
    assert json.loads(resp.text) == {
        "name": NAME,
        "editable": True,
        "transport": remote.transport,
        "url": remote.url,
        "headers": [
            {"name": "Authorization", "hasValue": True},
            {"name": "X-Api-Key", "hasValue": True},
        ],
    }

    assert _say_hello() == (True, "hello claw")
    _assert_every_request_carried(remote, AUTH)

    # Rotate the token from the edit form: type over one mask, leave the other as it was.
    auth_key = ref_key(spec["headers"]["Authorization"])
    key_ref = spec["headers"]["X-Api-Key"]
    remote.accept(NEW_AUTH)
    remote.forget()
    resp = _call(
        "PUT",
        {
            "transport": remote.transport,
            "url": remote.url,
            "headers": {"Authorization": NEW_AUTH},
            "keepHeaders": ["X-Api-Key"],
        },
    )
    assert resp.status == 200, resp.text
    spec = _servers(home / "mcp.json")[NAME]
    assert spec["headers"]["X-Api-Key"] == key_ref, "keeping a header changed it"
    assert get_credential(ref_key(key_ref)) == KEY, "keeping a header lost its value"
    assert get_credential(auth_key) == NEW_AUTH
    assert AUTH not in (home / ".env").read_text(encoding="utf-8"), "the old token stayed stored"

    assert _say_hello() == (True, "hello claw"), "the rotated token did not reach the server"
    _assert_every_request_carried(remote, NEW_AUTH)


def test_switching_a_servers_transport_leaves_nothing_of_the_old_one(home) -> None:
    def owned() -> set[str]:
        return {k for k in credential_names() if k.startswith(mcp_server_prefix(NAME))}

    resp = _call("PUT", {"command": "echo", "env": {"GITHUB_TOKEN": "ghp_fixtureSwitch0123456789"}})
    assert resp.status == 200, resp.text
    env_keys = owned()
    assert env_keys, "precondition: the variable was stored"

    resp = _call(
        "PUT",
        {"transport": "http", "url": "https://mcp.example.invalid/mcp", "headers": {"X-A": "b1"}},
    )
    assert resp.status == 200, resp.text
    for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
        spec = _servers(doc)[NAME]
        assert "command" not in spec and "env" not in spec and "args" not in spec, spec
        assert spec["type"] == "http"
    assert not owned() & env_keys, "the stdio server's stored variable outlived the switch"

    header_keys = owned()
    resp = _call("PUT", {"transport": "stdio", "command": "echo"})
    assert resp.status == 200, resp.text
    for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
        spec = _servers(doc)[NAME]
        assert "url" not in spec and "headers" not in spec and "type" not in spec, spec
    assert not owned() & header_keys, "the remote server's stored header outlived the switch"


@pytest.mark.parametrize(
    "body, code, says",
    [
        ({"transport": "ws", "url": "https://x.example.invalid"}, "invalid_transport", "http"),
        ({"transport": "http", "url": "ftp://x.example.invalid/mcp"}, "invalid_url", "http"),
        ({"transport": "http", "url": "https:///no-host"}, "invalid_url", "host"),
        ({"transport": "http", "url": ""}, "invalid_url", "URL"),
        (
            {"transport": "http", "url": "https://x.example.invalid", "command": "npx"},
            "invalid_transport",
            "command",
        ),
        (
            {"transport": "stdio", "command": "npx", "url": "https://x.example.invalid"},
            "invalid_transport",
            "url",
        ),
        (
            {
                "transport": "http",
                "url": "https://x.example.invalid",
                "headers": {"X-A": "one\r\nX-Injected: two"},
            },
            "invalid_headers",
            "line break",
        ),
        (
            {"transport": "sse", "url": "https://x.example.invalid", "headers": {"Bad Name": "v"}},
            "invalid_headers",
            "Bad Name",
        ),
        (
            {
                "transport": "http",
                "url": "https://x.example.invalid",
                "headers": {"Authorization": "a"},
                "keepHeaders": ["authorization"],
            },
            "invalid_headers",
            "sent twice",
        ),
        (
            {"transport": "http", "url": "https://x.example.invalid", "headers": {"X-A": "  "}},
            "invalid_headers",
            "enter a value",
        ),
        (
            {"transport": "http", "url": "https://x.example.invalid", "keepHeaders": ["X-Never"]},
            "invalid_headers",
            "X-Never",
        ),
    ],
)
def test_a_remote_definition_that_cannot_be_saved_is_refused_and_writes_nothing(
    home, body, code, says
) -> None:
    resp = _call("PUT", body)
    assert resp.status == 400, resp.text
    error = json.loads(resp.text)["error"]
    assert error["code"] == code, error
    assert says in error["message"], error
    assert not (home / "mcp.json").exists(), "a refused save wrote mcp.json"


def test_the_claude_code_copy_of_a_remote_server_is_one_claude_code_reads(home, tmp_path) -> None:
    from personalclaw.mcp_discovery import discover_servers_to_sync, register_servers_for_cc

    write_mcp_document(
        home / "mcp.json",
        {
            "mcpServers": {
                NAME: {"type": "http", "url": "https://mcp.example.invalid/mcp"},
                "sse-one": {"type": "sse", "url": "https://mcp.example.invalid/sse"},
            }
        },
    )
    dot_mcp = tmp_path / "dot.mcp.json"
    register_servers_for_cc(discover_servers_to_sync(), mcp_json_path=dot_mcp)
    written = _servers(dot_mcp)
    # Claude Code's own shapes: `type` is `http` or `sse` (it refuses `streamable-http`), and a
    # server at a URL is never written as a stdio one with an empty command.
    assert written[NAME] == {"type": "http", "url": "https://mcp.example.invalid/mcp"}
    assert written["sse-one"] == {"type": "sse", "url": "https://mcp.example.invalid/sse"}


def test_the_provider_card_keeps_a_remote_servers_transport(home) -> None:
    from personalclaw.providers import mcp_instances

    write_mcp_document(
        home / "mcp.json",
        {"mcpServers": {NAME: {"type": "http", "url": "https://mcp.example.invalid/mcp"}}},
    )
    card = mcp_instances.get_instance(NAME)
    assert card is not None and card.config["transport"] == "http"
    # The card saves what it read: an unchanged save must not turn the server into an SSE one.
    mcp_instances.update_instance(NAME, config=dict(card.config))
    assert _servers(home / "mcp.json")[NAME] == {
        "type": "http",
        "url": "https://mcp.example.invalid/mcp",
    }


# ── the whole path: import from Claude Code, then connect ───────────────────

_IMPORT_DRIVER = textwrap.dedent("""
    import asyncio, json, sys

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    NAME = sys.argv[1]


    async def main():
        from personalclaw.dashboard.handlers import mcp as h
        from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs
        from personalclaw.mcp_discovery import probe_one

        app = web.Application()
        app["state"] = type("State", (), {"_background_tasks": set()})()
        app.router.add_get("/api/mcp", h.api_mcp_servers)
        app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
        app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
        out = {}
        async with TestClient(TestServer(app)) as c:
            out["importable"] = await (await c.get("/api/mcp/importable")).text()
            change = {"name": NAME, "personalclaw": True, "ccGlobal": True}
            resp = await c.post("/api/mcp/apply", json={"changes": [change]})
            out["apply"] = await resp.json()
            out["listed"] = await (await c.get("/api/mcp")).text()
            await asyncio.gather(*app["state"]._background_tasks, return_exceptions=True)

        reg = McpClientRegistry()
        try:
            reg.load_from_specs(_personalclaw_mcp_specs())
            conn = reg.get(NAME)
            out["call"] = list(await conn.call_tool("hello", {"name": "claw"})) if conn else None
        finally:
            await reg.shutdown_all()
        info = await probe_one(NAME)
        out["probe"] = {"status": info.status, "error": info.error,
                        "tools": [t["name"] for t in info.tools]} if info else None
        print("RESULT " + json.dumps(out))


    asyncio.run(main())
    """)


def test_a_server_imported_from_claude_code_connects_with_its_headers(tmp_path, remote) -> None:
    """Tools → Discovered in other tools → Import, in a fresh gateway process whose Claude Code
    config lives in ``$CLAUDE_CONFIG_DIR``, then an agent's tool call and the probe."""
    pclaw_home, user_home, cc_dir = tmp_path / "pclaw", tmp_path / "user", tmp_path / "cc"
    for d in (pclaw_home, user_home, cc_dir):
        d.mkdir()
    claude = cc_dir / ".claude.json"
    claude.write_text(
        json.dumps(
            {
                "numStartups": 3,
                "mcpServers": {
                    NAME: {
                        "type": remote.transport,
                        "url": remote.url,
                        "headers": {"Authorization": AUTH, "X-Api-Key": KEY},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    claude_before = claude.read_bytes()
    driver = tmp_path / "driver.py"
    driver.write_text(_IMPORT_DRIVER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    env.update(
        PERSONALCLAW_HOME=str(pclaw_home),
        HOME=str(user_home),
        CLAUDE_CONFIG_DIR=str(cc_dir),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    proc = subprocess.run(
        [sys.executable, str(driver), NAME],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=240,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, f"exit {proc.returncode}:\n{proc.stderr[-4000:]}"
    out = json.loads(lines[-1][len("RESULT ") :])

    assert NAME in out["importable"], "the server in $CLAUDE_CONFIG_DIR was not offered"
    for secret in (AUTH, KEY):
        assert secret not in out["importable"] and secret not in out["listed"]
    assert not any(c.get("error") for c in out["apply"]["results"]), out["apply"]
    spec = _servers(pclaw_home / "mcp.json")[NAME]
    assert spec["type"] == remote.transport and spec["url"] == remote.url
    assert AUTH not in (pclaw_home / "mcp.json").read_text(encoding="utf-8")
    assert claude.read_bytes() == claude_before, "importing modified Claude Code's file"

    assert out["call"] == [True, "hello claw"], f"the imported server did not answer: {out}"
    assert out["probe"] == {"status": "ok", "error": "", "tools": ["hello"]}, out["probe"]
    _assert_every_request_carried(remote, AUTH)
