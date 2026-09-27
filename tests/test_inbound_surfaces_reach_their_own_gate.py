"""Every route of `/v1/*`, `/a2a/*` and `/capture/*` reaches its OWN admission gate.

Measured before the fix: `/capture/v1/chat/completions` and `/capture/v1/messages` were on
`token_auth._BYPASS_EXACT`, and nothing else of these three surfaces was. So the dashboard
middleware answered every other route first — `/v1/models`, `/v1/chat/completions`, the whole of
`/a2a/*`, `/capture/import` — with its sign-in refusal (`403` + `X-Auth-Required`, the paste-token
page for these non-`/api/` paths), to exactly the clients the surfaces exist for: they present the
surface's bearer in `Authorization` and carry no dashboard cookie. The surfaces were unreachable in
the default auth mode, and each one's own `_admit` never ran.

Three properties, each driven through the real middleware AND the real handlers together, because
the defect only exists in their union:

* with a valid surface token, every route answers for itself — never the middleware's refusal;
* without one, every route answers with its surface's own `401` (its gate ran and refused);
* nothing else opens: a sibling path under the same prefixes is still the middleware's to refuse.

The routes are READ from each surface's `register_routes`, so a route added later is covered on the
day it lands — and reds here until its author decides whether it authenticates itself.
"""

from __future__ import annotations

import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth.modes import AuthConfig, AuthMode
from personalclaw.dashboard import token_auth
from personalclaw.inbound import a2a, auth
from personalclaw.inbound import capture_proxy as capture
from personalclaw.inbound import openai_dialect as openai

PORT = 10000
_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home, no surface token leaking in or out, and both blanket bypasses OFF — either
    one passes everything through, which would make every refusal below vacuous."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)


def _enable_all(monkeypatch):
    """All three surfaces on, loopback-only, through `AppConfig.load()` (no config.json)."""
    from personalclaw.config.external_access import (
        CaptureSurfaceConfig,
        ExternalAccessConfig,
        ExternalAccessSurfaceConfig,
    )
    from personalclaw.config.loader import AgentConfig, AppConfig

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(
        enabled=True,
        openai=ExternalAccessSurfaceConfig(enabled=True),
        a2a=ExternalAccessSurfaceConfig(enabled=True),
        capture=CaptureSurfaceConfig(enabled=True),
    )
    cfg.agents = {"researcher": AgentConfig()}
    cfg.default_agent = "researcher"
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))


async def _refuse_to_run_a_turn(*_a, **_k):  # noqa: ANN002, ANN003
    raise AssertionError("a request in this file must never start an agent turn")


def _register(app: web.Application) -> None:
    openai.register_routes(app, turn_runner=_refuse_to_run_a_turn)
    a2a.register_routes(app)
    capture.register_routes(app)


#: The surface each route prefix belongs to, and so whose token opens it.
_OWNER = {"/v1/": openai.OPENAI_SURFACE, "/a2a/": a2a.SURFACE, "/capture/": capture.CAPTURE_SURFACE}

#: A body each POST route refuses on its own terms once it is reached — enough to prove the
#: handler read it, never enough to run a turn, a task or an upstream call.
_BODIES = {
    openai.ROUTE_CHAT: {"model": "personalclaw/nobody", "messages": []},
    openai.ROUTE_SPEECH: {},
    a2a.ROUTE_TASKS: {},
    capture.ROUTE_OPENAI: {},
    capture.ROUTE_ANTHROPIC: {},
    capture.ROUTE_IMPORT: {"file": "never-staged.jsonl"},
}


def _routes() -> list[tuple[str, str]]:
    """``(method, canonical path)`` for every route the three surfaces register."""
    app = web.Application()
    _register(app)
    out: list[tuple[str, str]] = []
    for route in app.router.routes():
        if route.method == "HEAD":
            continue
        canonical = route.resource.canonical if route.resource is not None else ""
        out.append((route.method, canonical))
    return sorted(set(out))


def _concrete(path: str) -> str:
    return path.replace("{task_id}", "run-that-does-not-exist")


def _surface_of(path: str) -> str:
    return next(surface for prefix, surface in _OWNER.items() if path.startswith(prefix))


ROUTES = _routes()


def test_the_route_census_found_all_three_surfaces():
    """VACUITY. An empty or short census would make every parametrized case below vanish."""
    prefixes = {next(p for p in _OWNER if path.startswith(p)) for _m, path in ROUTES}
    assert prefixes == set(_OWNER), ROUTES
    assert len(ROUTES) >= 11, ROUTES
    assert ("GET", a2a.ROUTE_TASK) in ROUTES, "the templated task poll must be in the census"


async def _client() -> TestClient:
    app = web.Application(
        middlewares=[token_auth.auth_middleware(AuthConfig(mode=AuthMode.LOCAL_TOKEN), port=PORT)]
    )
    _register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _send(client: TestClient, method: str, path: str, headers: dict[str, str]):
    url = _concrete(path)
    if method == "POST":
        return await client.post(url, json=_BODIES.get(path, {}), headers=headers)
    return await client.get(url, headers=headers)


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
@pytest.mark.asyncio
async def test_a_valid_surface_token_reaches_the_route(monkeypatch, method, path):
    """🔴 Red on main for every route but the two capture proxies: the middleware refused first."""
    _enable_all(monkeypatch)
    token = auth.create_surface_token(_surface_of(path))
    client = await _client()
    try:
        resp = await _send(client, method, path, {"Authorization": f"Bearer {token}"})
        body = await resp.text()
    finally:
        await client.close()
    assert resp.headers.get("X-Auth-Required") is None, (
        f"{method} {path} was refused by the dashboard's sign-in check before its own gate ran "
        f"({resp.status}): {body[:200]}"
    )
    assert resp.status not in (401, 403), f"{method} {path} refused a valid token: {body[:300]}"


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
@pytest.mark.asyncio
async def test_without_a_token_each_route_refuses_on_its_own_terms(monkeypatch, method, path):
    """Opening the dashboard's door opened nothing: the surface's own gate refuses a request with
    no bearer, with its own 401 — not the dashboard's sign-in refusal."""
    _enable_all(monkeypatch)
    auth.create_surface_token(_surface_of(path))  # a valid token EXISTS; this request lacks it
    client = await _client()
    try:
        resp = await _send(client, method, path, {})
        payload = await resp.json(content_type=None)
    finally:
        await client.close()
    assert resp.headers.get("X-Auth-Required") is None, f"{method} {path}: the middleware answered"
    assert resp.status == 401, (method, path, resp.status, payload)
    assert payload["error"]["code"] == "unauthorized", payload


@pytest.mark.parametrize(
    "path",
    [
        "/v1/embeddings",
        "/v1/models/extra",
        "/a2a/tasks/one/two",
        "/a2a/admin",
        "/capture/other",
        "/capture/v1/files",
    ],
)
@pytest.mark.asyncio
async def test_a_sibling_path_is_still_the_dashboards_to_refuse(path):
    """Nothing else under the three prefixes inherits the exemption: exact paths and one
    single-segment template, never a prefix."""
    reached: list[str] = []

    async def _handler(request):  # noqa: ANN001
        reached.append(request.path)
        return web.Response(text="reached")

    app = web.Application(
        middlewares=[token_auth.auth_middleware(AuthConfig(mode=AuthMode.LOCAL_TOKEN), port=PORT)]
    )
    app.router.add_route("*", path, _handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(path)
    assert reached == [], f"{path} reached a handler with no credential"
    assert resp.status == 403 and resp.headers.get("X-Auth-Required") == "true"


def test_no_inbound_surface_is_exempted_by_prefix():
    """The exemption grain: `_BYPASS_PREFIXES` holds static-asset trees only."""
    assert not any(
        p.startswith(("/v1", "/a2a", "/capture")) for p in token_auth._BYPASS_PREFIXES
    ), token_auth._BYPASS_PREFIXES
