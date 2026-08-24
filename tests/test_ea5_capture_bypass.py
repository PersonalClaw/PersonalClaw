"""EA-5 — the two `/capture/v1` routes must reach their OWN admission gate.

Measured before the fix: neither `/capture/v1/chat/completions` nor `/capture/v1/messages`
was on `_BYPASS_EXACT` or `_BYPASS_PREFIXES`, while `/mcp` was. So the dashboard's
`token_auth` middleware ran first and denied every request carrying no `?token=` query
param and no `pc_token_<port>` cookie — which is exactly the shape an external coding agent
sends: it points `OPENAI_BASE_URL` at the proxy and presents the capture bearer in
`Authorization`, a header the dashboard middleware never reads. Both routes shipped
UNREACHABLE. Nothing was wrong with either half on its own; the defect only exists in the
union, which is why the tests here drive the real middleware and the real handlers together.

Two properties, and the second is the one that makes the first safe:

* the paths bypass the dashboard's cookie auth in every mode `token_auth.py` implements,
  with a vacuity floor beside it (an unrelated path is still denied) so "bypassed" is a
  property of these paths and not of a middleware that stopped denying anything;
* bypassing opens nothing — `capture_proxy._admit` still answers 404 for a disabled
  surface, 403 for a non-loopback peer and 401 for a bad bearer, each asserted *through*
  the middleware rather than trusted from the comment.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth.modes import AuthConfig, AuthMode
from personalclaw.dashboard import token_auth
from personalclaw.inbound import auth
from personalclaw.inbound import capture_proxy as proxy

PORT = 10000
CAPTURE_PATHS = (proxy.ROUTE_OPENAI, proxy.ROUTE_ANTHROPIC)
# The modes `token_auth.py` actually implements a bypass list for. AuthMode.NONE is
# deliberately absent: it is a passthrough by construction, so it can neither exempt nor
# deny anything and a "bypass" assertion under it would be vacuous.
GATED_MODES = (AuthMode.LOCAL_TOKEN, AuthMode.API_KEY, AuthMode.OAUTH2)
_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Private home, no surface token leaking, and both blanket bypasses OFF.

    Clearing `PERSONALCLAW_DEV_NO_AUTH` / `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` is the
    vacuity floor for the floor: either one makes `token_auth` pass EVERYTHING through
    from a private address, so with one set in the environment the `/api/status` denial
    below would silently stop being a denial and the whole file would prove nothing.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def _middleware(mode: AuthMode):
    """The real middleware for `mode`, built the way `server.py` builds it."""
    cfg = AuthConfig(
        mode=mode,
        api_key_env="PERSONALCLAW_TEST_API_KEY",
        oauth2_issuer="https://issuer.invalid",
        oauth2_audience="personalclaw",
    )
    return token_auth.auth_middleware(cfg, port=PORT)


def _request(path: str):
    """A request with NO dashboard credential of any kind — an external agent's shape."""
    req = MagicMock(spec=web.Request)
    req.path = path
    req.method = "POST"
    req.query = {}
    req.cookies = {}
    req.headers = {}
    req.remote = "127.0.0.1"
    return req


def _enable_capture(monkeypatch, *, enabled=True, allow_remote=False, allowlist=()):
    """Point `AppConfig.load()` at an external-access config without writing config.json."""
    from personalclaw.config.loader import AppConfig, ExternalAccessConfig
    from personalclaw.config.loader import ExternalAccessSurfaceConfig as Surface

    cfg = AppConfig()
    surface = Surface(enabled=enabled, allow_remote=allow_remote)
    cfg.external_access = ExternalAccessConfig(enabled=True, capture=surface)
    setattr(cfg.external_access, "capture_upstream_allowlist", list(allowlist))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    return cfg


async def _gated_capture_client(mode: AuthMode = AuthMode.LOCAL_TOKEN) -> TestClient:
    """The real capture routes behind the real dashboard middleware — the union."""
    app = web.Application(middlewares=[_middleware(mode)])
    proxy.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── 1. The exemption exists, in every gated mode, for both routes ─────────────


@pytest.mark.parametrize("mode", GATED_MODES, ids=lambda m: m.value)
@pytest.mark.parametrize("path", CAPTURE_PATHS)
@pytest.mark.asyncio
async def test_a_capture_path_reaches_the_handler_without_a_dashboard_credential(mode, path):
    reached: list[str] = []

    async def _handler(request):  # noqa: ANN001
        reached.append(request.path)
        return web.Response(text="reached")

    resp = await _middleware(mode)(_request(path), _handler)
    assert reached == [path], f"{mode.value} denied {path} before its own admission ran"
    assert resp.status == 200


@pytest.mark.parametrize("mode", GATED_MODES, ids=lambda m: m.value)
@pytest.mark.asyncio
async def test_an_unrelated_path_is_still_denied_without_a_credential(mode):
    """VACUITY FLOOR. Without this, a middleware that stopped gating anything at all would
    make every assertion above pass, and "the capture paths are exempt" would be a claim
    about the middleware being broken rather than about the capture paths."""
    reached: list[str] = []

    async def _handler(request):  # noqa: ANN001
        reached.append(request.path)
        return web.Response(text="reached")

    resp = await _middleware(mode)(_request("/api/status"), _handler)
    assert reached == [], f"{mode.value} let /api/status through with no credential"
    assert resp.status in (401, 403), resp.status


def test_the_capture_routes_are_exempted_EXACTLY_not_by_prefix():
    """Enumerated, and deliberately not `/capture/v1/` as a prefix.

    A prefix would hand the exemption to any route added under `/capture/v1/` later,
    whichever admission it does or does not run. Two exact entries mean a future route
    has to opt in on purpose. It also matches the file's grain: `_BYPASS_PREFIXES` holds
    static-asset trees only, while every self-authenticating API surface (`/mcp`,
    `/api/logout`, `/api/token/local`) is an exact entry.
    """
    for path in CAPTURE_PATHS:
        assert path in token_auth._BYPASS_EXACT, f"{path} is not exempt"
    assert not any(
        p.startswith("/capture") for p in token_auth._BYPASS_PREFIXES
    ), "the capture surface must not be prefix-exempt"
    # Beside the OTHER self-authenticating surface, which is the precedent it follows.
    assert "/mcp" in token_auth._BYPASS_EXACT


# ── 2. Exempting them opens nothing: _admit still refuses ─────────────────────


@pytest.mark.asyncio
async def test_a_bypassed_request_is_404_when_the_surface_is_disabled(monkeypatch):
    _enable_capture(monkeypatch, enabled=False)
    token = auth.create_surface_token(proxy.CAPTURE_SURFACE)
    client = await _gated_capture_client()
    try:
        for path in CAPTURE_PATHS:
            resp = await client.post(
                path,
                data=json.dumps({"model": "m", "messages": []}),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == 404, path
            # 404 is the handler's own refusal, not a middleware denial (403/401).
            assert (await resp.json())["error"] == "not available"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_bypassed_request_is_403_when_the_peer_is_remote(monkeypatch):
    """`allow_remote=True` on purpose: capture is loopback-only by construction, and the
    dashboard bypass must not have moved that decision anywhere."""
    _enable_capture(monkeypatch, enabled=True, allow_remote=True, allowlist=("example.invalid",))
    token = auth.create_surface_token(proxy.CAPTURE_SURFACE)
    monkeypatch.setattr(auth, "_peer_host", lambda request: "203.0.113.9")
    client = await _gated_capture_client()
    try:
        for path in CAPTURE_PATHS:
            resp = await client.post(
                path,
                data=json.dumps({"model": "m", "messages": []}),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == 403, path
            assert (await resp.json())["error"] == "forbidden"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_bypassed_request_is_401_when_the_bearer_is_wrong(monkeypatch):
    _enable_capture(monkeypatch, enabled=True, allowlist=("example.invalid",))
    auth.create_surface_token(proxy.CAPTURE_SURFACE)  # a valid token EXISTS
    client = await _gated_capture_client()
    try:
        for path in CAPTURE_PATHS:
            resp = await client.post(
                path,
                data=json.dumps({"model": "m", "messages": []}),
                headers={"Authorization": "Bearer not-the-token"},
            )
            assert resp.status == 401, path
            assert (await resp.json())["error"] == "unauthorized"
    finally:
        await client.close()
