"""The owner token travels in an ``Authorization: Bearer`` header (local-token mode).

Before this, the dashboard middleware read primary owner auth from ``?token=`` or the
``pc_token_<port>`` cookie only; its Bearer branch was the app-token NARROWING path and never
authenticated on its own. So every API client had to put the owner token in the URL, and the
product's own did: ``personalclaw run`` and ``spawn`` appended ``?token=`` to every request and
the app template taught ``curl …?token=$PERSONALCLAW_TOKEN``. A URL is the one place a
credential is copied everywhere it goes — shell history, the process list, a proxy's access
log, an exception's repr.

The header is a second CARRIER for the credential the cookie already carries, not a second
credential: it accepts exactly the sessions the cookie accepts, with the same checks (signature,
session lifetime, a live nonce, revocation), and it is stateless — it sets no cookie and binds no
address, because both belong to the ``?token=`` browser exchange. An app-scoped token in the
header keeps the one job it had there: it narrows an owner session and never stands in for one.

Every test drives the REAL middleware over a real ``TestServer``, with the real outermost
security-headers middleware in front of it, so a response is asserted the way a client sees it.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.server import _security_headers_middleware

PORT = 10000
COOKIE = f"pc_token_{PORT}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Sessions and the signing key live under *tmp_path*; no auth-skipping env leaks in."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    # 🪤 Either variable admits every request below without looking at a credential, and the
    # suite would then read as a pass for a middleware it never exercised.
    for var in ("PERSONALCLAW_DEV_NO_AUTH", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"):
        monkeypatch.delenv(var, raising=False)
    token_auth.use_ephemeral_secret(b"owner-bearer-contract-key-0001")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _identity(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "user": request["user"],
            "app": request.get("app", ""),
            "session_nonce": request.get("session_nonce", ""),
        }
    )


async def _ws_identity(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"user": request["user"], "session_nonce": request["session_nonce"]})
    await ws.close()
    return ws


async def _client(middleware=None) -> TestClient:
    auth = middleware or token_auth.token_auth_middleware(
        port=PORT,
        internal_paths=frozenset({"/api/internal-probe"}),
        mixed_internal_paths=frozenset({"/api/mixed-probe"}),
    )
    app = web.Application(middlewares=[_security_headers_middleware, auth])
    for path in ("/api/probe", "/api/internal-probe", "/api/mixed-probe"):
        app.router.add_get(path, _identity)
    app.router.add_get("/api/ws", _ws_identity)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _refused_with(response, code: str) -> None:
    """The one refusal a header credential gets: 403, the stable code, and nothing cached."""
    assert response.status == 403, await response.text()
    assert (await response.json())["error"]["code"] == code
    assert "no-store" in response.headers["Cache-Control"]
    assert "Set-Cookie" not in response.headers


# ── the header authenticates ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_owner_token_in_the_header_authenticates_and_sets_no_cookie() -> None:
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        response = await client.get("/api/probe", headers=_bearer(token))
        assert response.status == 200, await response.text()
        assert await response.json() == {
            "user": "owner",
            "app": "",
            "session_nonce": token_auth.token_nonce(token),
        }
        # Stateless: the cookie is the browser exchange's, and a script never asked for one.
        assert "Set-Cookie" not in response.headers
    finally:
        await client.close()


@pytest.mark.parametrize("path", ["/api/internal-probe", "/api/mixed-probe"])
@pytest.mark.asyncio
async def test_the_header_reaches_the_internal_routes_a_browser_session_reaches(path) -> None:
    """Loopback without the internal secret falls to session auth, and the header is session
    auth. ``spawn`` polls ``/api/spawn``, a mixed internal route, so this is its path."""
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        response = await client.get(path, headers=_bearer(token))
        assert response.status == 200, await response.text()
        assert (await response.json())["user"] == "owner"
        assert "Set-Cookie" not in response.headers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_header_opens_the_websocket() -> None:
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        ws = await client.ws_connect("/api/ws", headers=_bearer(token))
        message = await ws.receive()
        assert message.type is WSMsgType.TEXT
        assert json.loads(message.data) == {
            "user": "owner",
            "session_nonce": token_auth.token_nonce(token),
        }
        await ws.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_device_session_rides_the_header_exactly_as_it_rides_the_cookie() -> None:
    """One credential, two carriers: whatever the cookie admits, the header admits, as the SAME
    session — so every paired-device distinction (keyed on the session nonce) still applies."""
    token = token_auth.generate_token(devices_h.PAIRED_DEVICE_USER, ttl_seconds=300)
    client = await _client()
    try:
        by_cookie = await client.get("/api/probe", cookies={COOKIE: token})
        by_header = await client.get("/api/probe", headers=_bearer(token))
        assert by_cookie.status == by_header.status == 200
        assert await by_header.json() == await by_cookie.json()
        assert (await by_header.json())["session_nonce"] == token_auth.token_nonce(token)
    finally:
        await client.close()


# ── the browser exchange is unchanged ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_query_token_still_exchanges_for_the_cookie() -> None:
    """Control: ``?token=`` is the browser entry link, and it still sets the session cookie."""
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        entered = await client.get("/api/probe", params={"token": token})
        assert entered.status == 200
        assert entered.cookies[COOKIE].value == token
        again = await client.get("/api/probe", cookies={COOKIE: token})
        assert await again.json() == await entered.json()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_same_token_in_the_header_and_the_query_keeps_the_exchange() -> None:
    token = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        response = await client.get("/api/probe", params={"token": token}, headers=_bearer(token))
        assert response.status == 200
        assert response.cookies[COOKIE].value == token
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_two_different_owner_tokens_in_the_header_and_the_query_are_refused() -> None:
    """Two live owner credentials naming two sessions: there is no right one to pick."""
    in_query = token_auth.generate_token("owner", ttl_seconds=300)
    in_header = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        response = await client.get(
            "/api/probe", params={"token": in_query}, headers=_bearer(in_header)
        )
        await _refused_with(response, "auth_credential_conflict")
        body = await response.text()
        assert in_query not in body and in_header not in body
    finally:
        await client.close()


# ── an app token only narrows ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_app_token_alone_in_the_header_authorizes_nothing() -> None:
    app_token = token_auth.generate_token("owner", ttl_seconds=300, app="notes")
    client = await _client()
    try:
        await _refused_with(
            await client.get("/api/probe", headers=_bearer(app_token)), "auth_bearer_invalid"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_app_token_in_the_header_still_narrows_the_owner_cookie() -> None:
    """Control: the app SDK's shape — the owner cookie plus the app's token — is unchanged."""
    owner = token_auth.generate_token("owner", ttl_seconds=300)
    app_token = token_auth.generate_token("owner", ttl_seconds=300, app="notes")
    client = await _client()
    try:
        response = await client.get(
            "/api/probe", cookies={COOKIE: owner}, headers=_bearer(app_token)
        )
        assert response.status == 200
        assert await response.json() == {
            "user": "owner",
            "app": "notes",
            "session_nonce": token_auth.token_nonce(owner),
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_owner_header_is_narrowed_by_an_app_token_on_the_websocket_query() -> None:
    """``/api/ws?app_token=`` narrows whichever owner credential authorized the upgrade."""
    owner = token_auth.generate_token("owner", ttl_seconds=300)
    app_token = token_auth.generate_token("owner", ttl_seconds=300, app="notes")
    client = await _client()
    try:
        response = await client.get(
            "/api/probe", params={"app_token": app_token}, headers=_bearer(owner)
        )
        assert response.status == 200
        assert (await response.json())["app"] == "notes"
    finally:
        await client.close()


# ── one refusal for every header that cannot stand on its own ──────────────────────────


@pytest.mark.asyncio
async def test_an_expired_owner_token_in_the_header_is_refused() -> None:
    with patch("personalclaw.dashboard.token_auth.time") as clock:
        clock.time.return_value = 1_000.0
        token = token_auth.generate_token("owner", ttl_seconds=1)
    client = await _client()
    try:
        with patch("personalclaw.dashboard.token_auth.time") as clock:
            clock.time.return_value = 1_002.0
            response = await client.get("/api/probe", headers=_bearer(token))
        await _refused_with(response, "auth_bearer_invalid")
    finally:
        await client.close()


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer two tokens", "Bearer a,b", "Bearer not-a-signed-token"],
)
@pytest.mark.asyncio
async def test_a_malformed_or_unknown_bearer_is_refused_without_being_echoed(
    authorization: str,
) -> None:
    client = await _client()
    try:
        response = await client.get("/api/probe", headers={"Authorization": authorization})
        await _refused_with(response, "auth_bearer_invalid")
        assert authorization.split(" ", 1)[-1] not in json.dumps(await response.json())
    finally:
        await client.close()


@pytest.mark.parametrize("carrier", ["header", "query"])
@pytest.mark.asyncio
async def test_a_non_ascii_signature_is_a_refusal_not_a_server_error(carrier: str) -> None:
    """``hmac.compare_digest`` RAISES on a non-ASCII str, so a forged signature carrying one
    reached the auth middleware as a TypeError — a 500 — instead of a refusal. The header
    makes that input one more request away, so both carriers are pinned."""
    payload = token_auth.generate_token("owner", ttl_seconds=300).split(".", 1)[0]
    forged = f"{payload}.signaturé"
    client = await _client()
    try:
        if carrier == "header":
            response = await client.get("/api/probe", headers=_bearer(forged))
            await _refused_with(response, "auth_bearer_invalid")
        else:
            response = await client.get("/api/probe", params={"token": forged})
            assert response.status == 403, await response.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_token_signed_by_another_gateway_is_refused() -> None:
    token_auth.use_ephemeral_secret(b"foreign-gateway-contract-key-001")
    foreign = token_auth.generate_token("owner", ttl_seconds=300)
    token_auth.use_ephemeral_secret(b"owner-bearer-contract-key-0001")
    client = await _client()
    try:
        await _refused_with(
            await client.get("/api/probe", headers=_bearer(foreign)), "auth_bearer_invalid"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_inbound_mcp_token_cannot_authenticate_an_owner_route(monkeypatch) -> None:
    """``/mcp``'s dedicated bearer and the owner token share a header, never a meaning."""
    from personalclaw.config import credentials
    from personalclaw.inbound import auth as inbound_auth

    monkeypatch.setattr(credentials, "save_credential", lambda k, v: monkeypatch.setenv(k, v))
    mcp_token = inbound_auth.create_surface_token("mcp")
    assert inbound_auth.verify_bearer("mcp", mcp_token), "the fixture must mint a real token"
    client = await _client()
    try:
        response = await client.get("/api/probe", headers=_bearer(mcp_token))
        await _refused_with(response, "auth_bearer_invalid")
        assert mcp_token not in await response.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_another_schemes_authorization_header_is_not_this_gateways() -> None:
    """A reverse proxy's own ``Basic`` login rides the same header beside the session cookie.
    It is the proxy's credential, not a malformed one of ours: ignored, never refused."""
    owner = token_auth.generate_token("owner", ttl_seconds=300)
    client = await _client()
    try:
        response = await client.get(
            "/api/probe", cookies={COOKIE: owner}, headers={"Authorization": "Basic b3duZXI6cHc="}
        )
        assert response.status == 200, await response.text()
        assert (await response.json())["user"] == "owner"
    finally:
        await client.close()


# ── the credential never reaches the audit log or a response ──────────────────────────


@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.asyncio
async def test_neither_the_audit_log_nor_the_response_carries_the_bearer(
    accepted: bool, monkeypatch, caplog
) -> None:
    token = (
        token_auth.generate_token("owner", ttl_seconds=300)
        if accepted
        else "credential-material-that-must-stay-private"
    )
    events: list[dict] = []

    class _Audit:
        def log_api_access(self, **kwargs):
            events.append(kwargs)

    monkeypatch.setattr(token_auth, "_sel_fn", lambda: _Audit())
    client = await _client()
    try:
        response = await client.get("/api/probe", headers=_bearer(token))
        assert response.status == (200 if accepted else 403)
        token_auth.flush_success_tally()
        assert events, "the auth decision must be audited, or 'nothing leaked' proves nothing"
        rendered = json.dumps(events, default=str) + caplog.text + await response.text()
        assert token not in rendered
    finally:
        await client.close()


# ── the skip modes name a header session too ───────────────────────────────────────────


def test_the_skip_modes_name_the_session_a_header_presents() -> None:
    """``AuthMode.NONE`` and the local-network bypass grant without the strict path, so they
    name the session through ``presented_session_nonce`` — which must read the header as well,
    or a script's session is anonymous in exactly those modes."""

    class _Req:
        def __init__(self, headers: dict[str, str]) -> None:
            self.query: dict[str, str] = {}
            self.cookies: dict[str, str] = {}
            self.headers = headers

    token = token_auth.generate_token("owner", ttl_seconds=300)
    app_token = token_auth.generate_token("owner", ttl_seconds=300, app="notes")
    assert token_auth.presented_session_nonce(_Req(_bearer(token)), PORT) == token_auth.token_nonce(
        token
    )
    # An app token names no owner session, and a forged one names nothing.
    assert token_auth.presented_session_nonce(_Req(_bearer(app_token)), PORT) == ""
    assert token_auth.presented_session_nonce(_Req(_bearer(token + "x")), PORT) == ""
