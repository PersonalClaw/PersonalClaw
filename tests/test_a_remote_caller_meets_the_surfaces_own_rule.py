"""A program on another machine reaches an inbound surface only by that surface's own rule.

With ``allow_remote`` on for the MCP, OpenAI-compatible or A2A surface, the dashboard's origin check
used to refuse a state-changing call from another machine before the surface read it, so the
setting did nothing, while a call carrying a forged loopback ``Origin`` passed the check and reached
the surface anyway. Now such a call skips the origin check, and the surface decides it as it
decides every call: ``public_url`` set, the Host matching it, then its bearer token.

With ``allow_remote`` off, the default, every call from another machine is refused as before.
Capture and the webhook's doors stay on this machine whatever the setting, and a loopback call
meets the origin check exactly as it always did.

The rows run the dashboard's own pieces: ``check_origin``, ``server._origin_refusal_waived`` and
``origin_refusal``, in the order ``csrf_middleware`` calls them, in front of each surface's real
routes. The peer is the transport's, as ``inbound.auth`` reads it.
"""

from __future__ import annotations

import json
import os

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import server
from personalclaw.dashboard.origin import check_origin, origin_refusal
from personalclaw.inbound import a2a, auth, mcp_http

REMOTE = "192.0.2.10"
PUBLIC = "claw.example"
PORT = 10000


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home per test, and no surface token leaking out: `create_surface_token` mirrors
    the token into ``os.environ`` itself, a variable monkeypatch never recorded."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = [auth.token_env_key(surface) for surface in auth.surfaces()]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield
    for key in keys:
        os.environ.pop(key, None)


def _config(monkeypatch, *, remote: bool, public_url: str = f"https://{PUBLIC}") -> None:
    """External access on, with A2A, MCP and capture on and ``allow_remote`` set to *remote*."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=True,
        public_url=public_url,
        a2a=Surface(enabled=True, allow_remote=remote),
        mcp=Surface(enabled=True, allow_remote=remote),
        capture=Surface(enabled=True, allow_remote=remote),
    )
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))


def _peer(monkeypatch, peer: str) -> None:
    """The transport's peer, as both the origin check and the surfaces read it."""
    monkeypatch.setattr(auth, "_peer_host", lambda request: peer)
    monkeypatch.setitem(_PEER, "host", peer)


_PEER: dict[str, str] = {"host": "127.0.0.1"}


@web.middleware
async def _as_peer(request: web.Request, handler):
    return await handler(request.clone(remote=_PEER["host"]))


@web.middleware
async def _csrf(request: web.Request, handler):
    """``server.csrf_middleware``'s check, its own pieces in its own order."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not check_origin(request, require=True, fallback_header="Referer"):
            if not server._origin_refusal_waived(request, PORT):
                return origin_refusal(request)
    return await handler(request)


async def _client() -> TestClient:
    app = web.Application(middlewares=[_as_peer, _csrf])
    app["allowed_origins"] = {f"http://127.0.0.1:{PORT}"}
    a2a.register_routes(app)
    app.router.add_post("/mcp", mcp_http.handle_mcp)
    from personalclaw.inbound import capture_proxy

    capture_proxy.register_routes(app)
    client = TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar())
    await client.start_server()
    return client


def _bearer(surface: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth.create_surface_token(surface)}"}


async def _post(client: TestClient, path: str, *, headers: dict[str, str], body=b"{"):
    resp = await client.post(path, data=body, headers={"Host": PUBLIC, **headers})
    return resp.status, await resp.json()


#: Past every gate, a2a reads the body: a body that isn't JSON is its own 400.
ADMITTED = (400, "invalid_json")


@pytest.mark.asyncio
async def test_off_a_remote_call_is_refused_before_any_surface_reads_it(monkeypatch):
    """The default, unchanged: the origin check refuses it and says why."""
    _config(monkeypatch, remote=False)
    _peer(monkeypatch, REMOTE)
    client = await _client()
    try:
        status, body = await _post(client, "/a2a/tasks", headers=_bearer("a2a"))
    finally:
        await client.close()
    assert (status, body["error"]["code"]) == (403, "auth_origin_not_allowed")
    assert f"this one came from {REMOTE}" in body["error"]["message"]


@pytest.mark.asyncio
async def test_off_a_forged_loopback_origin_with_a_token_is_still_refused(monkeypatch):
    """A loopback ``Origin`` passes the origin check, as it always did, but never admits a call on
    its own: the surface's peer rule refuses a caller on another machine with the setting off."""
    _config(monkeypatch, remote=False)
    _peer(monkeypatch, REMOTE)
    client = await _client()
    try:
        status, body = await _post(
            client, "/a2a/tasks", headers={"Origin": "http://127.0.0.1", **_bearer("a2a")}
        )
    finally:
        await client.close()
    assert (status, body["error"]["code"]) == (403, "forbidden")


@pytest.mark.parametrize("surface,path", [("a2a", "/a2a/tasks"), ("mcp", "/mcp")])
@pytest.mark.asyncio
async def test_on_a_remote_call_with_the_surfaces_token_is_admitted(monkeypatch, surface, path):
    """🔴 Red before: the origin check refused it first, so ``allow_remote`` did nothing."""
    _config(monkeypatch, remote=True)
    _peer(monkeypatch, REMOTE)
    client = await _client()
    try:
        status, body = await _post(client, path, headers=_bearer(surface))
    finally:
        await client.close()
    assert status not in (401, 403, 404), body
    if surface == "a2a":
        assert (status, body["error"]["code"]) == ADMITTED


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-the-token"}, {"Origin": "http://127.0.0.1"}],
    ids=["no-token", "wrong-token", "forged-origin-no-token"],
)
@pytest.mark.asyncio
async def test_on_a_remote_call_without_the_token_is_refused_with_no_detail(monkeypatch, headers):
    """🔴 Red before for the first two: they were refused by the origin check, which named the
    caller's address. Now the surface refuses them, with its bare 401."""
    _config(monkeypatch, remote=True)
    _peer(monkeypatch, REMOTE)
    auth.create_surface_token("a2a")
    client = await _client()
    try:
        status, body = await _post(client, "/a2a/tasks", headers=headers)
    finally:
        await client.close()
    assert status == 401
    assert body["error"]["code"] == "unauthorized"
    assert REMOTE not in json.dumps(body)


@pytest.mark.asyncio
async def test_on_a_remote_call_to_another_host_is_refused_with_no_detail(monkeypatch):
    """The Host must be the declared public URL's, so a DNS-rebinding page can't use a token."""
    _config(monkeypatch, remote=True)
    _peer(monkeypatch, REMOTE)
    client = await _client()
    try:
        resp = await client.post(
            "/a2a/tasks", data=b"{", headers={"Host": "rebound.example", **_bearer("a2a")}
        )
        status, body = resp.status, await resp.json()
    finally:
        await client.close()
    assert (status, body["error"]["code"]) == (403, "forbidden")
    assert "rebound.example" not in json.dumps(body)


@pytest.mark.asyncio
async def test_on_with_no_public_url_a_remote_call_is_refused(monkeypatch):
    _config(monkeypatch, remote=True, public_url="")
    _peer(monkeypatch, REMOTE)
    client = await _client()
    try:
        status, body = await _post(client, "/a2a/tasks", headers=_bearer("a2a"))
    finally:
        await client.close()
    assert (status, body["error"]["code"]) == (403, "forbidden")


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Origin": "https://elsewhere.example"}, (403, "auth_origin_not_allowed")),
        ({}, ADMITTED),
    ],
    ids=["a-foreign-page", "a-program-with-the-token"],
)
@pytest.mark.asyncio
async def test_a_loopback_call_meets_the_origin_check_as_it_always_did(
    monkeypatch, headers, expected
):
    _config(monkeypatch, remote=True)
    _peer(monkeypatch, "127.0.0.1")
    client = await _client()
    try:
        status, body = await _post(client, "/a2a/tasks", headers={**headers, **_bearer("a2a")})
    finally:
        await client.close()
    assert (status, body["error"]["code"]) == expected


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1"], ids=["no-origin", "forged-origin"])
@pytest.mark.asyncio
async def test_capture_takes_no_remote_caller_whatever_allow_remote_says(monkeypatch, origin):
    _config(monkeypatch, remote=True)
    _peer(monkeypatch, REMOTE)
    headers = {**_bearer("capture"), **({"Origin": origin} if origin else {})}
    client = await _client()
    try:
        status, _ = await _post(
            client, "/capture/v1/chat/completions", headers=headers, body=b'{"model": "m"}'
        )
    finally:
        await client.close()
    assert status == 403


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mcp", True),
        ("/v1/chat/completions", True),
        ("/a2a/tasks", True),
        ("/capture/v1/messages", False),
        ("/api/hooks/agent", False),
        ("/api/triggers/store:webhook:build-finished/fire", False),
        ("/api/config/personalclaw", False),
    ],
)
def test_only_the_three_surfaces_ever_take_a_remote_caller(monkeypatch, path, expected):
    from unittest.mock import MagicMock

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    on = {name: Surface(enabled=True, allow_remote=True) for name in auth.surfaces()}
    cfg.external_access = ExternalAccessConfig(enabled=True, public_url=f"https://{PUBLIC}", **on)
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        (REMOTE, 52100) if name == "peername" else default
    )
    request = make_mocked_request("POST", path, transport=transport)
    assert auth.takes_remote(request) is expected


def test_an_unreadable_config_takes_no_remote_caller(monkeypatch):
    from unittest.mock import MagicMock

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.config.loader import AppConfig

    def _broken(*a, **k):
        raise OSError("config.json unreadable")

    monkeypatch.setattr(AppConfig, "load", staticmethod(_broken))
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        (REMOTE, 52100) if name == "peername" else default
    )
    assert auth.takes_remote(make_mocked_request("POST", "/mcp", transport=transport)) is False


def test_the_panel_reports_capture_and_the_bridge_as_loopback_only(monkeypatch):
    from personalclaw.dashboard.handlers import external_access

    _config(monkeypatch, remote=True)
    rows = {row["surface"]: row for row in external_access._surface_rows()}
    assert {name for name, row in rows.items() if row["loopback_only"]} == {"capture", "bridge"}
