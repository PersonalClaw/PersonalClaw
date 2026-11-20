"""The browser connector pairs from its own origin, and that origin reaches its routes alone.

The connector is a browser extension, so its calls carry the extension's origin
(``chrome-extension://<id>``), which the origin check refused on ``pair/complete`` and on every
connector route: in the default sign-in mode the shipped connector did nothing. Now:

* ``pair/complete`` takes an extension origin with a valid single-use code, under the same attempt
  limit, pairs a *connector* device whose row keeps that exact origin, and hands the session token
  back in the body on that request alone (``Cache-Control: no-store``): the connector sends it as
  ``Authorization: Bearer``. It sets no cookie and ends no sign-in, since it pairs inside the
  owner's own browser, whose cookie is her dashboard's. A page's origin (``null``, ``file://``,
  http, https) is never recorded.
* The origin check takes that origin back on the connector's routes only, beside a session of the
  device that recorded it, compared whole (``connector_origin.admits``).
* The connector's session holds the connector scope, so the sign-in check admits it on the
  connector's routes and nowhere else, whatever origin a request carries.
* Revoking the device forgets the origin.

Each refusal has its row: another extension's origin, the recorded origin on another route, with no
session and after revoke, and a code made for an automation client.
"""

from __future__ import annotations

import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_token, pair_of

from personalclaw.auth import pairing
from personalclaw.browse import target as bt
from personalclaw.dashboard import connector_origin, device_scopes
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import browse_connector as bc
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.origin import check_origin, extension_origin, origin_refusal
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER

PORT = 10002
COOKIE = f"pc_token_{PORT}"
EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
OTHER_EXT = "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    monkeypatch.setattr(bt, "user_browser_enabled", lambda: True)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    bt.clear_connector()
    yield tmp_path
    bt.clear_connector()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@web.middleware
async def _csrf(request: web.Request, handler):
    """The dashboard's own origin check, as ``server.csrf_middleware`` runs it
    (``test_the_dashboard_takes_a_connectors_origin`` drives the gateway's own)."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_ok = check_origin(request, require=True, fallback_header="Referer")
        if not origin_ok and not connector_origin.admits(request, PORT):
            return origin_refusal(request)
    return await handler(request)


def _app() -> web.Application:
    app = web.Application(
        middlewares=[
            _csrf,
            token_auth.token_auth_middleware(port=PORT),
            device_scopes.device_scope_middleware,
        ]
    )
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices_h.register_device_routes(app)
    bc.register_browse_connector_routes(app)
    return app


def _client(server: TestServer) -> TestClient:
    return TestClient(server, cookie_jar=aiohttp.DummyCookieJar())


def _owner() -> dict:
    token = browser_token("owner", 3600)
    return {"cookies": {COOKIE: token}, "headers": {PAIR_HEADER: pair_of(token)}}


async def _code(owner: TestClient, **body) -> str:
    started = await owner.post("/api/devices/pair/start", json=body, **_owner())
    assert started.status == 200, await started.text()
    return (await started.json())["code"]


async def _pair_extension(owner: TestClient, ext: TestClient, origin: str = EXT):
    code = await _code(owner)
    return await ext.post(
        "/api/devices/pair/complete", json={"code": code}, headers={"Origin": origin}
    )


def _as(token: str, origin: str | None = EXT) -> dict:
    headers = {PAIR_HEADER: pair_of(token)}
    if origin:
        headers["Origin"] = origin
    return {"cookies": {COOKIE: token}, "headers": headers}


def _bearer(token: str, origin: str | None = EXT) -> dict:
    headers = {"Authorization": f"Bearer {token}", PAIR_HEADER: pair_of(token)}
    if origin:
        headers["Origin"] = origin
    return {"headers": headers}


# ── Pairing ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_extension_pairs_from_its_own_origin_and_its_device_keeps_that_origin():
    """🔴 Red before: ``pair/complete`` refused the extension's origin (403)."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        done = await _pair_extension(owner, ext)
        assert done.status == 200, await done.text()
        body = await done.json()
        assert done.headers["Cache-Control"] == "no-store"
        assert COOKIE not in done.cookies, "the cookie is her dashboard's"
        assert body["kind"] == ss.CONNECTOR_KIND
    ((nonce, record),) = ss.paired_sessions().items()
    assert nonce == token_auth.token_nonce(body["token"]), "the body's token is its session"
    assert record.device.origin == EXT
    assert record.device.kind == ss.CONNECTOR_KIND
    assert record.scope == (device_scopes.CONNECTOR_SCOPE,)


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "file://",
        "file:///Users/someone/page.html",
        "http://evil.example",
        "https://evil.example",
        "chrome-extension://",
        f"{EXT}/popup.html",
        "chrome-extension://abc def",
        "CHROME-EXTENSION://abc",
    ],
)
@pytest.mark.asyncio
async def test_an_origin_a_page_can_send_is_never_recorded(origin):
    """A sandboxed frame or a ``data:`` document sends ``null``, a local file ``null`` or
    ``file://``: neither, nor a web origin or a malformed one, pairs a connector, and the code is
    left unspent."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        code = await _code(owner)
        refused = await ext.post(
            "/api/devices/pair/complete", json={"code": code}, headers={"Origin": origin}
        )
        assert refused.status == 403
        assert ss.paired_sessions() == {}
        # Unspent: the same code still pairs from this computer.
        done = await ext.post("/api/devices/pair/complete", json={"code": code})
        assert done.status == 200
    (record,) = ss.paired_sessions().values()
    assert record.device.origin == "" and record.device.kind != ss.CONNECTOR_KIND


@pytest.mark.parametrize("origin", [f"http://localhost:{PORT}", None])
@pytest.mark.asyncio
async def test_a_pairing_from_a_page_or_this_computer_hands_back_no_token(origin):
    """The token is in the body only for the request that records an extension's origin."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        code = await _code(owner)
        done = await device.post(
            "/api/devices/pair/complete",
            json={"code": code},
            headers={"Origin": origin} if origin else {},
        )
        assert done.status == 200, await done.text()
        assert "token" not in await done.json()
    (record,) = ss.paired_sessions().values()
    assert record.device.origin == ""


@pytest.mark.asyncio
async def test_pairing_the_connector_in_her_signed_in_browser_keeps_her_sign_in():
    """🔴 Red before (CORE F1): the connector pairs inside the owner's browser, which sends her
    dashboard's cookie with it, and the pairing ended that sign-in and set the connector's session
    under her cookie's name, so her tab could reach only the connector's routes."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        mine = _owner()
        code = await _code(owner)
        done = await ext.post(
            "/api/devices/pair/complete",
            json={"code": code},
            cookies=mine["cookies"],
            headers={"Origin": EXT, **mine["headers"]},
        )
        assert done.status == 200, await done.text()
        set_cookies = done.headers.getall("Set-Cookie", [])
        assert not [c for c in set_cookies if c.startswith("pc_token")], set_cookies
        listed = await owner.get("/api/devices", **mine)
        assert listed.status == 200, await listed.text()


@pytest.mark.asyncio
async def test_a_code_made_for_an_automation_client_pairs_no_connector():
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        code = await _code(owner, scope=["status"], days=7)
        refused = await ext.post(
            "/api/devices/pair/complete", json={"code": code}, headers={"Origin": EXT}
        )
        assert refused.status == 400
        assert (await refused.json())["error"]["code"] == devices_h.ERR_CONNECTOR_CODE_SCOPED
    assert ss.paired_sessions() == {}


@pytest.mark.asyncio
async def test_a_wrong_code_from_an_extension_counts_toward_the_same_limit():
    """It adds no guessing: the extension's attempts and a page's lock out together."""
    server = TestServer(_app())
    async with _client(server) as ext:
        statuses = []
        for _ in range(12):
            answer = await ext.post(
                "/api/devices/pair/complete", json={"code": "WRNG-CODE"}, headers={"Origin": EXT}
            )
            statuses.append(answer.status)
            if answer.status == 429:
                break
        assert statuses[-1] == 429, statuses
        locked = await ext.post("/api/devices/pair/complete", json={"code": "WRNG-CODE"})
        assert locked.status == 429, "the limit is the address's, whichever origin asked"


# ── The connector's routes, and nothing else ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_connector_attaches_from_its_origin_on_its_own_session():
    """🔴 Red before: the origin check refused the extension's attach."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        token = (await (await _pair_extension(owner, ext)).json())["token"]
        attached = await ext.post("/api/browse/connector", json={}, **_bearer(token))
        assert attached.status == 200, await attached.text()
        assert bt.connector_status().connected is True
        detached = await ext.delete("/api/browse/connector", **_bearer(token))
        assert detached.status == 200, await detached.text()


@pytest.mark.asyncio
async def test_the_connector_attaches_with_its_bearer_beside_her_cookie():
    """🔴 Red before (CORE F2): in her browser a connector's fetch can carry her cookie beside its
    own bearer. The sign-in check takes the bearer, but the origin check read her cookie, whose
    session records no origin, and refused. Both now read the same credential."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        token = (await (await _pair_extension(owner, ext)).json())["token"]
        attached = await ext.post(
            "/api/browse/connector",
            json={},
            cookies=_owner()["cookies"],
            headers=_bearer(token)["headers"],
        )
        assert attached.status == 200, await attached.text()
        assert bt.connector_status().connected is True


@pytest.mark.parametrize("origin", [EXT, None])
@pytest.mark.asyncio
async def test_the_connectors_session_reaches_no_other_route(origin):
    """Its session is the connector's alone: any other route is refused, with its own origin and
    without one (curl), so a token held in an extension's storage only drives the connector."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        token = (await (await _pair_extension(owner, ext)).json())["token"]
        listed = await ext.get("/api/devices", **_bearer(token, origin))
        assert listed.status == 403
        assert (await listed.json())["error"]["code"] == device_scopes.ERR_SCOPE_REFUSED
        started = await ext.post("/api/devices/pair/start", json={}, **_bearer(token, origin))
        assert started.status == 403


@pytest.mark.asyncio
async def test_another_extensions_origin_is_refused_with_the_connectors_session():
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        token = (await (await _pair_extension(owner, ext)).json())["token"]
        refused = await ext.post("/api/browse/connector", json={}, **_bearer(token, OTHER_EXT))
        assert refused.status == 403
        assert (await refused.json())["error"]["code"] == "auth_origin_not_allowed"
        assert bt.connector_status().connected is False


@pytest.mark.asyncio
async def test_the_recorded_origin_with_no_session_or_another_devices_is_refused():
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext, _client(server) as phone:
        await _pair_extension(owner, ext)
        bare = await ext.post("/api/browse/connector", json={}, headers={"Origin": EXT})
        assert bare.status == 403
        # A phone's session, a paired device but not this connector, with the connector's origin.
        code = await _code(owner)
        phone_token = (
            (await phone.post("/api/devices/pair/complete", json={"code": code}))
            .cookies[COOKIE]
            .value
        )
        borrowed = await phone.post("/api/browse/connector", json={}, **_as(phone_token))
        assert borrowed.status == 403
        assert (await borrowed.json())["error"]["code"] == "auth_origin_not_allowed"


@pytest.mark.asyncio
async def test_revoking_the_connector_forgets_its_origin():
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as ext:
        done = await (await _pair_extension(owner, ext)).json()
        token, device_id = done["token"], done["device_id"]
        revoked = await owner.post(f"/api/devices/{device_id}/revoke", json={}, **_owner())
        assert revoked.status == 200, await revoked.text()
        assert ss.paired_sessions() == {}
        refused = await ext.post("/api/browse/connector", json={}, **_bearer(token))
        assert refused.status == 403
        assert (await refused.json())["error"]["code"] == "auth_origin_not_allowed"


# ── The rule, and its one caller ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        (EXT, True),
        ("moz-extension://1b9c6f2e-4a5d-4c3b-9a8e-7f6d5c4b3a21", True),
        ("null", False),
        ("", False),
        ("file://", False),
        ("http://127.0.0.1:10002", False),
        ("https://claw.example", False),
        ("chrome-extension://", False),
        (f"{EXT}/", False),
        (f" {EXT}", False),
        ("chrome-extension://a@b", False),
    ],
)
def test_what_counts_as_an_extensions_origin(value, kept):
    assert extension_origin(value) == (value if kept else "")
