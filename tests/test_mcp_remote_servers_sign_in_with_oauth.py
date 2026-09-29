"""A remote MCP server that authenticates with OAuth signs in from the Tools page, and stays in.

🔴 THE GAP (measured on ``origin/main``). A server at a URL got only the static ``headers`` its
spec declares (#3629). A server that authenticates with OAuth (Sentry's, Linear's, Notion's)
answers every request with ``401`` and ``WWW-Authenticate: Bearer resource_metadata=…``: the probe
read ``HTTP 401 Unauthorized``, there was no route to sign in, no code under ``src/`` read a
``WWW-Authenticate`` header or made a PKCE challenge, and so no such server could ever connect.

Every connection here is to a real MCP server — the SDK's ``FastMCP`` behind uvicorn, over
Streamable HTTP and over SSE — guarded by a real (fake) OAuth authorization server in the same
process: resource metadata (RFC 9728), authorization server metadata (RFC 8414), dynamic client
registration (RFC 7591), the authorization code flow with PKCE (RFC 7636) and ``resource``
(RFC 8707), and refresh-token rotation. The authorization endpoint consents at once and
redirects, which is what a person clicking "Allow" does; the "browser" follows that redirect to
the gateway's own callback route.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import allow_configured, confirmed

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential, save_credential
from personalclaw.config.secret_refs import (
    foreign_mcp_spec,
    ref_key,
    remove_mcp_servers,
    write_mcp_document,
)
from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

NAME = "oauth-fixture"
#: The spec key a sign-in is stored under — a persisted shape, so pinned here as the literal.
MCP_SIGN_IN = "signIn"

# The resource server and its authorization server, in one uvicorn process. Its state (clients,
# codes, tokens) lives in memory; a CONTROL file it re-reads on every request lets a test switch
# dynamic registration off, revoke a token, or refuse every refresh; an EVENTS file logs what it
# saw.
_FAKE_SERVER = textwrap.dedent("""
    import base64, hashlib, json, os, secrets, socket, sys, time
    from urllib.parse import urlencode

    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

    TRANSPORT, PORT_FILE, EVENTS, CONTROL = sys.argv[1:5]
    mcp = FastMCP("fake-oauth-remote")


    @mcp.tool(description="greet someone")
    def hello(name: str) -> str:
        return f"hello {name}"


    inner = mcp.streamable_http_app() if TRANSPORT == "http" else mcp.sse_app()
    MCP_PATH = "/mcp" if TRANSPORT == "http" else "/sse"
    BASE = ""
    clients, codes, access, refresh = {}, {}, {}, {}


    def log(event, **kw):
        with open(EVENTS, "a") as f:
            f.write(json.dumps({"event": event, **kw}) + "\\n")


    def control():
        with open(CONTROL) as f:
            return json.load(f)


    def refused(error, status=400):
        log("refused", error=error)
        return JSONResponse({"error": error, "error_description": error}, status_code=status)


    async def app(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        request = Request(scope, receive)
        path, c = scope["path"], control()
        for pre in c.get("preregistered", []):
            clients.setdefault(pre["client_id"], {"redirect_uris": pre["redirect_uris"],
                                                  "secret": pre.get("secret")})
        issuer = BASE + "/auth"
        if path == "/.well-known/oauth-protected-resource" + MCP_PATH:
            resp = JSONResponse({"resource": BASE + MCP_PATH, "authorization_servers": [issuer],
                                 "scopes_supported": ["tools:read"]})
        elif path == "/.well-known/oauth-authorization-server/auth":
            meta = {"issuer": issuer, "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token", "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                    "authorization_response_iss_parameter_supported": True}
            if c.get("dcr", True):
                meta["registration_endpoint"] = issuer + "/register"
            resp = JSONResponse(meta)
        elif path == "/auth/register" and scope["method"] == "POST":
            body = await request.json()
            cid = "dcr-" + secrets.token_hex(6)
            clients[cid] = {"redirect_uris": body["redirect_uris"], "secret": None}
            log("register", client_id=cid, body=body)
            resp = JSONResponse({"client_id": cid, "redirect_uris": body["redirect_uris"],
                                 "token_endpoint_auth_method": "none"}, status_code=201)
        elif path == "/auth/authorize":
            q = dict(request.query_params)
            log("authorize", query=q)
            client = clients.get(q.get("client_id"))
            problems = [p for p, bad in (
                ("unknown client", client is None),
                ("redirect_uri not registered",
                 client is not None and q.get("redirect_uri") not in client["redirect_uris"]),
                ("response_type", q.get("response_type") != "code"),
                ("pkce", q.get("code_challenge_method") != "S256" or not q.get("code_challenge")),
                ("resource", q.get("resource") != BASE + MCP_PATH),
                ("state", not q.get("state")),
            ) if bad]
            if problems:
                resp = PlainTextResponse("; ".join(problems), status_code=400)
                log("authorize_refused", problems=problems)
            else:
                code = secrets.token_urlsafe(16)
                codes[code] = {"client_id": q["client_id"], "redirect_uri": q["redirect_uri"],
                               "challenge": q["code_challenge"], "resource": q["resource"]}
                answer = {"code": code, "state": q["state"],
                          "iss": c.get("answer_iss") or issuer}
                resp = RedirectResponse(q["redirect_uri"] + "?" + urlencode(answer),
                                        status_code=302)
        elif path == "/auth/token" and scope["method"] == "POST":
            form = dict(await request.form())
            log("token", grant=form.get("grant_type"), form=sorted(form))
            client_id = form.get("client_id", "")
            client = clients.get(client_id)
            if client is None:
                resp = refused("invalid_client", 401)
            elif client["secret"] and form.get("client_secret") != client["secret"]:
                resp = refused("invalid_client", 401)
            elif form.get("resource") != BASE + MCP_PATH:
                resp = refused("invalid_target")
            elif form.get("grant_type") == "authorization_code":
                entry = codes.pop(form.get("code", ""), None)
                verifier = form.get("code_verifier", "")
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if entry is None or entry["client_id"] != client_id:
                    resp = refused("invalid_grant")
                elif entry["redirect_uri"] != form.get("redirect_uri"):
                    resp = refused("invalid_grant")
                elif challenge != entry["challenge"]:
                    resp = refused("invalid_grant")
                else:
                    resp = None
            elif form.get("grant_type") == "refresh_token":
                if c.get("refuse_refresh") or refresh.pop(form.get("refresh_token"), None) is None:
                    resp = refused("invalid_grant")
                else:
                    resp = None
            else:
                resp = refused("unsupported_grant_type")
            if resp is None:
                ttl = c.get("access_ttl", 3600)
                at, rt = "at-" + secrets.token_hex(12), "rt-" + secrets.token_hex(12)
                access[at] = time.time() + ttl
                refresh[rt] = client_id
                log("issued", access_token=at, refresh_token=rt)
                resp = JSONResponse({"access_token": at, "token_type": "Bearer",
                                     "expires_in": ttl, "refresh_token": rt,
                                     "scope": "tools:read"})
        else:
            auth = dict((k.decode().lower(), v.decode()) for k, v in scope["headers"]).get(
                "authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            ok = (token in access and access[token] > time.time()
                  and token not in c.get("revoked", []))
            log("mcp", method=scope["method"], path=path, authorization=auth, ok=ok)
            if not ok:
                challenge = ('Bearer resource_metadata="' + BASE
                             + '/.well-known/oauth-protected-resource' + MCP_PATH
                             + '", scope="tools:read"')
                resp = PlainTextResponse("unauthorized", status_code=401,
                                         headers={"WWW-Authenticate": challenge})
            else:
                await inner(scope, receive, send)
                return
        await resp(scope, receive, send)


    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    BASE = f"http://127.0.0.1:{sock.getsockname()[1]}"
    with open(PORT_FILE + ".tmp", "w") as f:
        f.write(str(sock.getsockname()[1]))
    os.replace(PORT_FILE + ".tmp", PORT_FILE)
    uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)


@dataclass
class OAuthRemote:
    transport: str
    base: str
    url: str
    events_file: Path
    control_file: Path

    @property
    def issuer(self) -> str:
        return self.base + "/auth"

    def control(self, **settings) -> None:
        current = json.loads(self.control_file.read_text(encoding="utf-8"))
        current.update(settings)
        self.control_file.write_text(json.dumps(current), encoding="utf-8")

    def events(self, kind: str | None = None) -> list[dict]:
        if not self.events_file.exists():
            return []
        rows = [json.loads(ln) for ln in self.events_file.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if kind is None or r["event"] == kind]

    def forget(self) -> None:
        self.events_file.unlink(missing_ok=True)


def _start_remote(tmp_path: Path, transport: str):
    script = tmp_path / "fake_oauth_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    port_file = tmp_path / f"port.{transport}"
    control = tmp_path / f"control.{transport}.json"
    control.write_text("{}", encoding="utf-8")
    events = tmp_path / f"events.{transport}.jsonl"
    stderr = tmp_path / f"server.{transport}.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(script), transport, str(port_file), str(events), str(control)],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    deadline = time.monotonic() + 30
    while not port_file.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            raise RuntimeError(f"the fake server did not start: {stderr.read_text()!r}")
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port_file.read_text(encoding='utf-8').strip()}"
    path = "/mcp" if transport == "http" else "/sse"
    return proc, OAuthRemote(transport, base, base + path, events, control)


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture(params=["http", "sse"])
def remote(request, tmp_path):
    proc, served = _start_remote(tmp_path, request.param)
    try:
        yield served
    finally:
        _stop(proc)


@pytest.fixture
def http_remote(tmp_path):
    proc, served = _start_remote(tmp_path, "http")
    try:
        yield served
    finally:
        _stop(proc)


@pytest.fixture
def home(monkeypatch, tmp_path):
    from personalclaw import mcp_client, mcp_discovery
    from personalclaw.dashboard.handlers import mcp as mcp_handlers

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # Each test's own probe cache and connections. The probes a read of `GET /api/mcp` starts are
    # stubbed: the rebuild adds PersonalClaw's own stdio server to the agent config, and probing it
    # would spawn a child in no isolated home. The one-server re-probe a sign-in or a save starts,
    # which is what these tests read, runs for real.
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_discovery, "_probing", {})
    monkeypatch.setattr(mcp_handlers, "_start_probes", lambda request, servers: None)
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _servers(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})


def _add(home: Path, remote: OAuthRemote) -> None:
    write_mcp_document(
        home / "mcp.json", {"mcpServers": {NAME: {"type": remote.transport, "url": remote.url}}}
    )
    # The owner's yes to what it connects to (`mcp_grants`): the subject here is the sign-in.
    allow_configured(NAME)


def _gateway_app() -> web.Application:
    from personalclaw.dashboard.handlers import mcp as h
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware()])
    app["state"] = type("State", (), {"_background_tasks": set()})()
    app.router.add_get("/api/mcp", h.api_mcp_servers)
    app.router.add_get("/api/mcp/servers/{name}", h.api_mcp_server_detail)
    app.router.add_put("/api/mcp/servers/{name}", h.api_mcp_server_detail)
    app.router.add_post("/api/mcp/servers/{name}/sign-in", h.api_mcp_server_sign_in)
    app.router.add_delete("/api/mcp/servers/{name}/sign-in", h.api_mcp_server_sign_in)
    app.router.add_get("/api/mcp/oauth/callback", h.api_mcp_oauth_callback)
    return app


class Gateway:
    """The dashboard routes under test, and a browser that follows an authorization redirect."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.origin = f"http://127.0.0.1:{client.server.port}"

    async def settle(self) -> None:
        tasks = self.client.server.app["state"]._background_tasks
        await asyncio.gather(*list(tasks), return_exceptions=True)

    async def row(self) -> dict:
        await self.settle()
        rows = await (await self.client.get("/api/mcp")).json()
        return next(r for r in rows if r["name"] == NAME)

    async def start(self, **body) -> tuple[int, dict]:
        resp = await self.client.post(
            f"/api/mcp/servers/{NAME}/sign-in", json=body, headers={"Origin": self.origin}
        )
        return resp.status, await resp.json()

    async def consent(self, authorization_url: str) -> tuple[int, str, str]:
        """What the owner's browser does: opens the authorization URL, is sent back to the
        gateway's callback, and lands on its page. Returns (status, page, callback URL)."""
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as browser:
            answer = await browser.get(authorization_url)
            assert answer.status_code == 302, f"the authorization server said: {answer.text}"
            callback = answer.headers["location"]
            assert callback.startswith(self.origin + "/api/mcp/oauth/callback?"), callback
            page = await browser.get(callback)
        await self.settle()
        return page.status_code, page.text, callback

    async def edit(self, body: dict):
        """The Tools page's edit form saving the server: the definition replaces the one the form
        read, so it names that read's revision (`If-Match`), and it is resent once the owner
        agreed to what the server connects to (`mcp_grants`)."""
        base = (await (await self.client.get(f"/api/mcp/servers/{NAME}")).json())["revision"]
        return await self.client.put(
            f"/api/mcp/servers/{NAME}", json=confirmed(body), headers={"If-Match": f'"{base}"'}
        )

    async def sign_in(self, **body) -> str:
        status, started = await self.start(**body)
        assert status == 200, started
        code, page, _ = await self.consent(started["authorizationUrl"])
        assert code == 200, page
        assert f"Signed in to {NAME}" in page, page
        return started["authorizationUrl"]


def _drive(scenario) -> None:
    async def run() -> None:
        async with TestClient(TestServer(_gateway_app())) as client:
            await scenario(Gateway(client))

    asyncio.run(run())


async def _say_hello() -> tuple[bool, str]:
    """A tool call the way an agent makes one: the native client, from ``mcp.json``."""
    reg = McpClientRegistry()
    try:
        reg.load_from_specs(_personalclaw_mcp_specs())
        conn = reg.get(NAME)
        assert conn is not None, "the native client has no connection for the server"
        return await conn.call_tool("hello", {"name": "claw"})
    finally:
        await reg.shutdown_all()


def _tokens(home: Path) -> dict:
    block = _servers(home / "mcp.json")[NAME][MCP_SIGN_IN]
    return json.loads(get_credential(ref_key(block["tokens"])))


def _set_expiry(home: Path, when: float) -> None:
    block = _servers(home / "mcp.json")[NAME][MCP_SIGN_IN]
    record = _tokens(home)
    record["expires_at"] = when
    save_credential(ref_key(block["tokens"]), json.dumps(record))


def _owned_sign_in_keys() -> set[str]:
    from personalclaw.config.secret_refs import mcp_sign_in_owner

    prefix = mcp_sign_in_owner(NAME).prefix
    return {k for k in credential_names() if k.startswith(prefix)}


# ── the whole path ──────────────────────────────────────────────────────────


def test_a_server_that_asks_for_oauth_signs_in_connects_renews_and_says_when_it_ends(
    home, remote
) -> None:
    _add(home, remote)

    async def scenario(gw: Gateway) -> None:
        from personalclaw.mcp_discovery import probe_one

        # Before: the probe names the sign-in, and the Tools page row offers one.
        info = await probe_one(NAME)
        assert info is not None and info.status == "signin", (
            info and info.status,
            info and info.error,
        )
        assert "needs you to sign in" in info.error, info.error
        assert (await gw.row())["auth"] == {"method": "oauth", "state": "required"}

        # Start: discovery through the challenge, registration, and a PKCE authorization URL.
        status, started = await gw.start()
        assert status == 200, started
        url = urlsplit(started["authorizationUrl"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == remote.issuer + "/authorize"
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256"
        assert len(query["code_challenge"]) == 43, "an S256 challenge is 43 base64url characters"
        assert query["resource"] == remote.url, "the token must be asked for this server alone"
        assert query["scope"] == "tools:read", "the challenge's scope is the one asked for"
        assert query["redirect_uri"] == gw.origin + "/api/mcp/oauth/callback"
        assert len(query["state"]) >= 43
        registered = remote.events("register")
        assert len(registered) == 1 and registered[0]["client_id"] == query["client_id"]
        assert registered[0]["body"]["token_endpoint_auth_method"] == "none"
        assert registered[0]["body"]["redirect_uris"] == [query["redirect_uri"]]

        # Consent, and the gateway's callback finishes it.
        code, page, callback = await gw.consent(started["authorizationUrl"])
        assert code == 200 and f"Signed in to {NAME}" in page, page
        issued = remote.events("issued")
        assert len(issued) == 1
        first_access, first_refresh = issued[0]["access_token"], issued[0]["refresh_token"]
        for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
            text = doc.read_text(encoding="utf-8")
            assert first_access not in text and first_refresh not in text, f"a token is in {doc}"
            block = _servers(doc)[NAME][MCP_SIGN_IN]
            assert ref_key(block["tokens"]) in credential_names(), block
            assert block["issuer"] == remote.issuer and block["resource"] == remote.url
            assert block["clientId"] == query["client_id"] and block["registered"] is True
        assert _tokens(home)["access_token"] == first_access
        assert (await gw.row())["auth"] == {"method": "oauth", "state": "signed_in"}

        # The same answer twice is refused: a state is single-use.
        async with httpx.AsyncClient() as browser:
            replay = await browser.get(callback)
        assert replay.status_code == 400 and "expired or was already finished" in replay.text

        # An agent's tool call goes through, with the sign-in's bearer token on every request.
        remote.forget()
        assert await _say_hello() == (True, "hello claw")
        seen = remote.events("mcp")
        assert seen and all(r["authorization"] == f"Bearer {first_access}" for r in seen), seen

        # A token known to have expired is renewed BEFORE it is sent.
        _set_expiry(home, time.time() - 5)
        remote.forget()
        assert await _say_hello() == (True, "hello claw")
        assert [e["grant"] for e in remote.events("token")] == ["refresh_token"]
        renewed = _tokens(home)
        assert renewed["access_token"] != first_access, "the renewed token was not stored"
        assert renewed["refresh_token"] != first_refresh, "a rotated refresh token was not kept"
        seen = remote.events("mcp")
        assert all(r["authorization"] == f"Bearer {renewed['access_token']}" for r in seen), seen

        # A token the server stops accepting is renewed when it answers 401, and the call retried.
        remote.control(revoked=[renewed["access_token"]])
        remote.forget()
        assert await _say_hello() == (True, "hello claw")
        assert [e["grant"] for e in remote.events("token")] == ["refresh_token"]
        seen = remote.events("mcp")
        assert seen[0]["ok"] is False and all(r["ok"] for r in seen[1:]), seen

        # The authorization server refuses to renew it: the sign-in ENDS, and it says so.
        remote.control(revoked=[_tokens(home)["access_token"]], refuse_refresh=True)
        ok, said = await _say_hello()
        assert not ok and f"sign-in to {NAME} has ended" in said and "Sign in again" in said, said
        assert _owned_sign_in_keys() == set(), "the dead tokens were kept"
        assert MCP_SIGN_IN in _servers(home / "mcp.json")[NAME], "the sign-in's record went too"
        assert (await gw.row())["auth"] == {"method": "oauth", "state": "signed_out"}
        info = await probe_one(NAME)
        assert info is not None and info.status == "signin", info and info.error

        # Signing in again reuses PersonalClaw's registration and works again.
        remote.control(revoked=[], refuse_refresh=False)
        remote.forget()
        await gw.sign_in()
        assert remote.events("register") == [], "a second sign-in registered again"
        assert await _say_hello() == (True, "hello claw")
        assert (await gw.row())["auth"] == {"method": "oauth", "state": "signed_in"}

        # Signing out removes the sign-in from both documents and its values from the store.
        resp = await gw.client.delete(f"/api/mcp/servers/{NAME}/sign-in")
        assert resp.status == 200 and (await resp.json())["signedOut"] is True
        for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
            assert MCP_SIGN_IN not in _servers(doc)[NAME], doc
        assert _owned_sign_in_keys() == set()
        assert (await gw.row())["auth"] == {"method": "oauth", "state": "required"}

    _drive(scenario)


def test_an_authorization_server_without_registration_takes_a_typed_client_id(
    home, http_remote
) -> None:
    _add(home, http_remote)
    http_remote.control(dcr=False)

    async def scenario(gw: Gateway) -> None:
        status, refused = await gw.start()
        assert status == 400, refused
        error = refused["error"]
        assert error["code"] == "mcp_sign_in_needs_client_id", error
        redirect = gw.origin + "/api/mcp/oauth/callback"
        assert error["detail"]["redirectUri"] == redirect and redirect in error["message"], error
        assert error["detail"]["issuer"] == http_remote.issuer
        assert http_remote.events("register") == []

        http_remote.control(
            preregistered=[
                {"client_id": "typed-client", "redirect_uris": [redirect], "secret": "s3cr3t-typed"}
            ]
        )
        await gw.sign_in(clientId="typed-client", clientSecret="s3cr3t-typed")
        block = _servers(home / "mcp.json")[NAME][MCP_SIGN_IN]
        assert block["clientId"] == "typed-client" and block["registered"] is False
        assert block["tokenAuthMethod"] == "client_secret_post", block
        assert ref_key(block["clientSecret"]) in credential_names()
        assert "s3cr3t-typed" not in (home / "mcp.json").read_text(encoding="utf-8")
        assert await _say_hello() == (True, "hello claw")

    _drive(scenario)


@pytest.mark.parametrize(
    ("raw", "code"), [(b'{"clientId": "x"', "invalid_json"), (b"[]", "invalid_body")]
)
def test_a_sign_in_body_that_is_not_a_json_object_is_refused_and_starts_nothing(
    home, monkeypatch, raw, code
) -> None:
    """The route used to read the body itself and answer its own ``invalid_sign_in``. It reads
    it through ``json_object_body`` now, so the refusal is the one every other route gives."""
    from personalclaw import mcp_oauth

    write_mcp_document(
        home / "mcp.json", {"mcpServers": {NAME: {"type": "http", "url": "https://mcp.invalid/"}}}
    )
    started: list[str] = []

    async def _start(name, *args, **kwargs):
        started.append(name)
        raise AssertionError("a refused body must not start a sign-in")

    monkeypatch.setattr(mcp_oauth, "start_sign_in", _start)

    async def scenario(gw: Gateway) -> None:
        resp = await gw.client.post(
            f"/api/mcp/servers/{NAME}/sign-in",
            data=raw,
            headers={"Origin": gw.origin, "Content-Type": "application/json"},
        )
        body = await resp.json()
        assert resp.status == 400 and body["error"]["code"] == code, body

    _drive(scenario)
    assert started == []


def test_the_callback_refuses_what_no_sign_in_it_started_can_account_for(home, http_remote) -> None:
    _add(home, http_remote)

    async def scenario(gw: Gateway) -> None:
        async with httpx.AsyncClient() as browser:
            stray = await browser.get(f"{gw.origin}/api/mcp/oauth/callback?code=x&state=nothing")
        assert stray.status_code == 400 and "expired or was already finished" in stray.text

        # An answer whose `iss` names another authorization server (a mix-up) is not used.
        http_remote.control(answer_iss="https://attacker.example.invalid")
        status, started = await gw.start()
        assert status == 200, started
        code, page, _ = await gw.consent(started["authorizationUrl"])
        assert code == 400 and "did not come from" in page, page

        # The owner declines at the authorization server.
        http_remote.control(answer_iss=None)
        status, started = await gw.start()
        assert status == 200, started
        state = parse_qs(urlsplit(started["authorizationUrl"]).query)["state"][0]
        async with httpx.AsyncClient() as browser:
            declined = await browser.get(
                f"{gw.origin}/api/mcp/oauth/callback",
                params={"error": "access_denied", "error_description": "no thanks", "state": state},
            )
        assert declined.status_code == 400 and "did not sign you in" in declined.text

        assert [e for e in http_remote.events("token")] == [], "a refused answer was exchanged"
        assert _owned_sign_in_keys() == set()
        assert MCP_SIGN_IN not in _servers(home / "mcp.json")[NAME]

    _drive(scenario)


def test_a_sign_in_is_never_sent_to_an_address_it_was_not_granted_for(home, http_remote) -> None:
    _add(home, http_remote)

    async def scenario(gw: Gateway) -> None:
        await gw.sign_in()
        token = _tokens(home)["access_token"]

        # Editing the server to another address drops the sign-in and deletes its tokens.
        moved = http_remote.base + "/elsewhere/mcp"
        resp = await gw.edit({"transport": "http", "url": moved})
        assert resp.status == 200, await resp.text()
        for doc in (home / "mcp.json", home / "agents" / "personalclaw.json"):
            assert MCP_SIGN_IN not in _servers(doc)[NAME], doc
        assert _owned_sign_in_keys() == set()

        # A sign-in written for one address and a URL changed by hand: the token is not sent.
        await gw.edit({"transport": "http", "url": http_remote.url})
        await gw.sign_in()
        token = _tokens(home)["access_token"]
        doc = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
        doc["mcpServers"][NAME]["url"] = moved
        (home / "mcp.json").write_text(json.dumps(doc), encoding="utf-8")
        http_remote.forget()
        # A changed address is a new question: nothing connects until the owner allows it
        # (`mcp_grants`). Once they do, the sign-in is still not sent to it.
        assert NAME not in _personalclaw_mcp_specs()
        allow_configured(NAME)
        ok, said = await _say_hello()
        assert not ok and "its URL has changed" in said, said
        assert all(token not in (r["authorization"] or "") for r in http_remote.events("mcp"))

    _drive(scenario)


# ── the pieces ──────────────────────────────────────────────────────────────


def test_the_pkce_challenge_is_the_s256_of_its_verifier() -> None:
    from personalclaw.mcp_oauth import _pkce

    verifier, challenge = _pkce()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert _pkce()[0] != verifier


@pytest.mark.parametrize(
    "origin, expected",
    [
        ("http://localhost:3100", "http://127.0.0.1:3100/api/mcp/oauth/callback"),
        ("http://127.0.0.1:10000", "http://127.0.0.1:10000/api/mcp/oauth/callback"),
        ("http://[::1]:19100", "http://127.0.0.1:19100/api/mcp/oauth/callback"),
        ("https://claw.example.com", "https://claw.example.com/api/mcp/oauth/callback"),
        ("http://192.168.1.20:10000", None),
        ("http://claw.example.com", None),
    ],
)
def test_the_browser_is_sent_back_only_to_a_loopback_or_https_address(origin, expected) -> None:
    from personalclaw.mcp_oauth import redirect_uri_for

    assert redirect_uri_for(origin) == expected


def test_a_challenge_header_with_several_schemes_is_read_per_scheme() -> None:
    from personalclaw.mcp_oauth import challenges

    got = challenges(
        'Basic realm="corp", Bearer resource_metadata="https://mcp.example.com/.well-known/'
        'oauth-protected-resource/mcp", scope="read write", error="invalid_token"'
    )
    assert got["basic"] == {"realm": "corp"}
    assert got["bearer"] == {
        "resource_metadata": "https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
        "scope": "read write",
        "error": "invalid_token",
    }


@pytest.mark.parametrize(
    "resource, url, allowed",
    [
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp", True),
        ("https://mcp.example.com", "https://MCP.example.com/mcp", True),
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp/tools", True),
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcpx", False),
        ("https://mcp.example.com/mcp", "https://evil.example.com/mcp", False),
        ("https://mcp.example.com/mcp", "http://mcp.example.com/mcp", False),
        ("", "https://mcp.example.com/mcp", False),
    ],
)
def test_a_token_covers_its_resource_and_nothing_beside_it(resource, url, allowed) -> None:
    from personalclaw.mcp_oauth import covers

    assert covers(resource, url) is allowed


def test_an_authorization_server_off_https_is_refused_for_a_server_on_https() -> None:
    from personalclaw.mcp_oauth import _endpoint_problem

    server = "https://mcp.example.com/mcp"
    assert _endpoint_problem("https://auth.example.com/token", server, "token endpoint") is None
    problem = _endpoint_problem("http://auth.example.com/token", server, "token endpoint")
    assert problem is not None and "HTTPS" in problem
    # A local server under development may have a local authorization server beside it.
    assert _endpoint_problem("http://127.0.0.1:9/token", "http://127.0.0.1:8/mcp", "t") is None
    assert _endpoint_problem("http://127.0.0.1:9/token", server, "t") is not None


def test_the_sign_in_never_goes_into_another_tools_copy_and_leaves_with_its_server(home) -> None:
    from personalclaw.config import secret_refs
    from personalclaw.config.credentials import save_credential as keep
    from personalclaw.config.secret_refs import mcp_sign_in_owner

    assert secret_refs.MCP_SIGN_IN == MCP_SIGN_IN
    owner = mcp_sign_in_owner(NAME)
    tokens_key, secret_key = owner.key("tokens"), owner.key("clientSecret")
    keep(tokens_key, json.dumps({"access_token": "at-leaves-with-server"}))
    keep(secret_key, "client-secret-leaves-with-server")
    spec = {
        "type": "http",
        "url": "https://mcp.example.invalid/mcp",
        MCP_SIGN_IN: {
            "resource": "https://mcp.example.invalid/mcp",
            "tokens": "{{secret:" + tokens_key + "}}",
            "clientSecret": "{{secret:" + secret_key + "}}",
        },
    }
    write_mcp_document(home / "mcp.json", {"mcpServers": {NAME: dict(spec)}})
    for with_secrets in (True, False):
        assert MCP_SIGN_IN not in foreign_mcp_spec(NAME, spec, with_secrets=with_secrets)
    assert {tokens_key, secret_key} <= set(credential_names())
    assert remove_mcp_servers([NAME]) == [NAME]
    assert not {tokens_key, secret_key} & set(
        credential_names()
    ), "a removed server kept its sign-in"


def test_the_callback_is_reachable_without_a_session_and_nothing_else_of_it_is() -> None:
    from personalclaw.dashboard.token_auth import _BYPASS_EXACT

    assert "/api/mcp/oauth/callback" in _BYPASS_EXACT
    assert not any(p.startswith("/api/mcp/servers") for p in _BYPASS_EXACT)
