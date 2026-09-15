"""An unguarded request-shape fault on an /api/* route answers the wire envelope, not a
bare ``500 text/plain`` (#2861, systemic; #2855 tasks; #554 comments body).

Fourteen-plus handlers read ``body.get(...)`` on a body that may not be an object, or
``int(request.query.get(...))`` on a value that may not be numeric. A slightly-off
request therefore raised an unhandled ``AttributeError``/``ValueError`` and aiohttp
answered its ``text/plain`` "Server got itself in trouble" — the very default a JSON
client cannot parse, so it cannot tell "I sent bad input" from "the server broke".
``request_boundary_middleware`` maps that whole family to the one ``bad_request``
envelope in a single place, the peer of ``spa_fallback``'s 404/405 normalization
(``test_api_405_wire_envelope``) and ``invalid_id_gate``'s id normalization.

The ``without_the_guard`` test is the anti-fabrication proof: the SAME request through
the SAME handler answers ``500 text/plain`` when the middleware is absent, and the
coded JSON envelope when it is present.
"""

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.dashboard.server import spa_fallback

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


# ── handlers reproducing the two documented fault shapes ─────────────────────


async def _reads_body_as_object(request: web.Request) -> web.Response:
    # The #2861/#2855 body shape: .get() on a body that parsed to a non-object.
    body = await request.json()
    return web.json_response({"title": body.get("title")})


async def _parses_int_query(request: web.Request) -> web.Response:
    # The #2861/#2855 query shape: int() on a value that may not be numeric.
    return web.json_response({"limit": int(request.query.get("limit", "20"))})


async def _raises_runtime_error(_request: web.Request) -> web.Response:
    # A genuine server fault, NOT a request-shape one — must stay a 5xx.
    raise RuntimeError("something genuinely broke inside the handler")


async def _raises_http_not_found(_request: web.Request) -> web.Response:
    # A handler answering a deliberate HTTP status by raising — must pass through.
    raise web.HTTPNotFound()


def _make_app(*, with_guard: bool) -> web.Application:
    mws: list = [request_boundary_middleware()] if with_guard else []
    mws.append(spa_fallback)  # innermost, exactly as server.py orders them
    app = web.Application(middlewares=mws)
    app.router.add_post("/api/thing", _reads_body_as_object)
    app.router.add_get("/api/thing", _parses_int_query)
    app.router.add_get("/api/boom", _raises_runtime_error)
    app.router.add_get("/api/gone", _raises_http_not_found)
    # A sibling OFF the /api surface, reachable with the same bad body.
    app.router.add_post("/notapi/thing", _reads_body_as_object)
    return app


async def _envelope(resp) -> dict:
    assert resp.content_type == "application/json"
    return (await resp.json())["error"]


# ── the guard maps the fault family to the one 400 envelope ──────────────────


@pytest.mark.asyncio
async def test_non_object_body_answers_the_wire_envelope() -> None:
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.post("/api/thing", json=[])  # a list, not an object
        assert resp.status == 400
        err = await _envelope(resp)
        assert err["code"] == "bad_request"
        assert err["message"]  # a human sentence, not empty


@pytest.mark.asyncio
async def test_non_numeric_query_answers_the_wire_envelope() -> None:
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.get("/api/thing", params={"limit": "abc"})
        assert resp.status == 400
        assert (await _envelope(resp))["code"] == "bad_request"


@pytest.mark.asyncio
async def test_without_the_guard_the_same_request_500s_text_plain() -> None:
    # Anti-fabrication proof: absent the middleware, the identical request answers the
    # bare text/plain 500 the guard exists to prevent.
    async with TestClient(TestServer(_make_app(with_guard=False))) as client:
        resp = await client.post("/api/thing", json=[])
        assert resp.status == 500
        assert resp.content_type == "text/plain"


@pytest.mark.asyncio
async def test_valid_request_is_untouched() -> None:
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.post("/api/thing", json={"title": "hello"})
        assert resp.status == 200
        assert (await resp.json())["title"] == "hello"
        resp2 = await client.get("/api/thing", params={"limit": "5"})
        assert resp2.status == 200
        assert (await resp2.json())["limit"] == 5


@pytest.mark.asyncio
async def test_genuine_server_error_is_not_masked_as_a_client_400() -> None:
    # Doctrine (see invalid_id_gate): a real bug must keep surfacing as a 5xx, never be
    # relabelled a client error. A RuntimeError is not the request-shape family.
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.get("/api/boom")
        assert resp.status >= 500


@pytest.mark.asyncio
async def test_deliberate_httpexception_passes_through() -> None:
    # A handler that raises an HTTP status on purpose is an already-formed response, not
    # a fault; spa_fallback (inner) turns the /api 404 into its own envelope, untouched.
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.get("/api/gone")
        assert resp.status == 404
        assert (await _envelope(resp))["code"] == "not_found"


@pytest.mark.asyncio
async def test_off_api_route_fault_is_left_as_the_aiohttp_default() -> None:
    # Off the /api surface the envelope does not apply, exactly like the 405 gate: the
    # default 500 stands rather than a JSON blob a browser cannot use.
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.post("/notapi/thing", json=[])
        assert resp.status == 500
        assert resp.content_type == "text/plain"


# ── the gate is actually installed, not merely importable ────────────────────


class TestGateIsInstalled:
    def test_factory_stamps_the_marker(self) -> None:
        mw = request_boundary_middleware()
        assert getattr(mw, "_is_request_boundary_gate", False) is True

    def test_server_installs_it_just_outside_invalid_id(self) -> None:
        src = (SRC / "dashboard" / "server.py").read_text(encoding="utf-8")
        assert "request_boundary_middleware()" in src
        # Ordering: boundary guard OUTSIDE invalid_id (so invalid_id, whose
        # UnsafeRecordId is not a ValueError, still runs closest to the handler and is
        # never shadowed), and both precede spa_fallback.
        boundary = src.index("request_boundary_middleware()")
        invalid_id = src.index("invalid_id_middleware()")
        fallback = src.index("spa_fallback,\n    ]")
        assert boundary < invalid_id < fallback
