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

The same three types are also what PersonalClaw's own code raises when IT breaks, and the
boundary used to read them all as her malformed request. Deactivating an app once torch was
loaded raised a ``TypeError`` four calls beneath the route, in the scan of the loaded modules,
and the Apps page said "The request was malformed or carried an unusable parameter." So a
``TypeError`` or ``AttributeError`` is her request's only when it was raised while the route
read the request; raised beneath it, in PersonalClaw's code or an app's, it is a 500 that says
PersonalClaw failed, and the log keeps its traceback.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import app_code
from personalclaw.apps.native_contract import load_bundle_module
from personalclaw.dashboard.fallbacks import spa_fallback
from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.http_errors import json_error

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


async def _strips_a_field(request: web.Request) -> web.Response:
    # `.strip()` on a field that is not a string: an AttributeError in the route's own code.
    body = await request.json()
    return web.json_response({"name": body["name"].strip()})


async def _coerces_a_ratio(request: web.Request) -> web.Response:
    # `float()` on a field that is an object: a TypeError in the route's own code.
    body = await request.json()
    return web.json_response({"ratio": float(body["ratio"])})


async def _reads_an_id(request: web.Request) -> web.Response:
    # An id that is not text, parsed by the standard library the route called: the
    # AttributeError is raised inside `uuid`, with nothing of PersonalClaw's beneath the route.
    body = await request.json()
    return web.json_response({"id": str(uuid.UUID(body["id"]))})


async def _echoes_the_body(request: web.Request) -> web.Response:
    # A body that is not JSON: the ValueError is raised in the libraries the route called to
    # read it (aiohttp, json), with nothing of PersonalClaw's beneath the route.
    return web.json_response(await request.json())


async def _personalclaw_fails_inside(_request: web.Request) -> web.Response:
    # PersonalClaw's own helper handed a value it cannot take: a TypeError raised inside it,
    # beneath the route, with nothing to do with the request.
    return json_error("bad_request", status=400, error_extra=7)


async def _personalclaw_fails_inside_a_worker_thread(_request: web.Request) -> web.Response:
    # The same off the event loop, as the app routes run their work: the thread's frames are in
    # the fault's traceback, beneath the route.
    return await asyncio.to_thread(json_error, "bad_request", status=400, error_extra=7)


async def _personalclaw_fails_inside_with_an_attribute(_request: web.Request) -> web.Response:
    # The same with an AttributeError: a folder handed to PersonalClaw as text.
    app_code.claim("boundary-probe", "a/folder/given/as/text")
    return web.json_response({})


async def _an_apps_code_fails_inside(request: web.Request) -> web.Response:
    # An app's code, called by the route, breaks: that is not her request either.
    return web.json_response({"items": request.app["app_provider"].items(None)})


def _make_app(*, with_guard: bool) -> web.Application:
    mws: list = [request_boundary_middleware()] if with_guard else []
    mws.append(spa_fallback)  # innermost, exactly as server.py orders them
    app = web.Application(middlewares=mws)
    app.router.add_post("/api/thing", _reads_body_as_object)
    app.router.add_get("/api/thing", _parses_int_query)
    app.router.add_get("/api/boom", _raises_runtime_error)
    app.router.add_get("/api/gone", _raises_http_not_found)
    app.router.add_post("/api/name", _strips_a_field)
    app.router.add_post("/api/ratio", _coerces_a_ratio)
    app.router.add_post("/api/echo", _echoes_the_body)
    app.router.add_post("/api/id", _reads_an_id)
    app.router.add_get("/api/inside", _personalclaw_fails_inside)
    app.router.add_get("/api/inside-a-thread", _personalclaw_fails_inside_a_worker_thread)
    app.router.add_get("/api/inside-attribute", _personalclaw_fails_inside_with_an_attribute)
    app.router.add_get("/api/an-apps-code", _an_apps_code_fails_inside)
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


# ── what is her request's, and what is PersonalClaw failing ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/name", {"name": 5}),
        ("/api/ratio", {"ratio": {"a": 1}}),
        ("/api/id", {"id": 5}),
        ("/api/echo", "{not json"),
    ],
    ids=["strip-a-number", "float-an-object", "an-id-a-library-cannot-read", "not-json"],
)
async def test_a_request_the_route_cannot_read_is_still_a_400(path, body, caplog) -> None:
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        if isinstance(body, str):
            resp = await client.post(path, data=body, headers={"Content-Type": "application/json"})
        else:
            resp = await client.post(path, json=body)
        assert resp.status == 400
        assert (await _envelope(resp))["code"] == "bad_request"
    assert not [r for r in caplog.records if r.levelname == "ERROR"], "her request is no fault"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/inside", "/api/inside-a-thread", "/api/inside-attribute"],
    ids=["type-error", "type-error-in-a-worker-thread", "attribute-error"],
)
async def test_a_fault_raised_inside_personalclaw_is_a_500_that_says_it_failed(
    path, caplog, monkeypatch
) -> None:
    monkeypatch.setattr(app_code, "_roots", {})
    async with TestClient(TestServer(_make_app(with_guard=True))) as client:
        resp = await client.get(path)
        assert resp.status == 500, "PersonalClaw's own fault was blamed on the request"
        err = await _envelope(resp)
        assert err["code"] == "internal_error"
        assert "PersonalClaw" in err["message"] and "malformed" not in err["message"]
    logged = [r for r in caplog.records if r.name == "personalclaw.dashboard.request_boundary"]
    assert [r.levelname for r in logged] == ["ERROR"]
    assert logged[0].exc_info is not None, "the log does not keep the traceback"
    assert path in logged[0].getMessage()


@pytest.mark.asyncio
async def test_an_apps_code_failing_beneath_the_route_is_a_500(tmp_path, monkeypatch) -> None:
    for attr, empty in (("_roots", {}), ("_undo", {}), ("_parked", {}), ("_released", set())):
        monkeypatch.setattr(app_code, attr, empty)
    monkeypatch.setattr(app_code, "_loaded", ())
    folder = tmp_path / "apps" / "boundary-probe"
    folder.mkdir(parents=True)
    (folder / "provider.py").write_text(
        "class Catalogue:\n"
        "    def items(self, filters):\n"
        "        return [item for item in filters]\n"
        "\n"
        "\n"
        "def create_provider(config=None):\n"
        "    return Catalogue()\n"
    )
    module = load_bundle_module(folder, "boundary-probe", "provider")
    try:
        app = _make_app(with_guard=True)
        app["app_provider"] = module.create_provider()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/an-apps-code")
            assert resp.status == 500
            assert (await _envelope(resp))["code"] == "internal_error"
    finally:
        app_code.release("boundary-probe")


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
        # never shadowed), and both precede the fallback.
        boundary = src.index("request_boundary_middleware()")
        invalid_id = src.index("invalid_id_middleware()")
        fallback = src.index("spa_fallback if web_app else api_fallback,\n    ]")
        assert boundary < invalid_id < fallback
