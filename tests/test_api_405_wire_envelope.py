"""A 405 on an /api/* route answers in the one wire error envelope (#2846).

``spa_fallback`` rewrites the router's *unmatched-route* refusal (``HTTPNotFound``)
into the coded JSON envelope for ``/api/*`` so a JSON client can parse it. But a
REAL ``/api/*`` route hit with the wrong method raises ``HTTPMethodNotAllowed``,
which used to sail past that branch and answer aiohttp's ``text/plain`` default —
the very ``405: Method Not Allowed`` body a branching client cannot read. This
drives a wrong method through the actual router + middleware (``test_wire_error_
envelope_census`` covers handler-emitted envelopes, not middleware-level 405s).
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.server import spa_fallback


def _make_app() -> web.Application:
    app = web.Application(middlewares=[spa_fallback])

    async def _post_only(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    # A POST-only route on the /api surface, and a sibling off it, so the same
    # wrong-method request can be asserted to differ by path.
    app.router.add_post("/api/thing", _post_only)
    app.router.add_post("/notapi/thing", _post_only)
    return app


@pytest.mark.asyncio
async def test_api_405_answers_in_the_wire_envelope() -> None:
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/api/thing")  # POST-only route, wrong method
        assert resp.status == 405
        assert resp.content_type == "application/json"
        body = await resp.json()
        assert body["error"]["code"] == "method_not_allowed"
        assert body["error"]["message"]  # a human sentence, not empty
        # The router's Allow header (the methods that WOULD work) is preserved.
        assert resp.headers.get("Allow") == "POST"


@pytest.mark.asyncio
async def test_non_api_405_is_left_as_the_aiohttp_default() -> None:
    # Off the /api surface the envelope does not apply; the default 405 stands.
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/notapi/thing")
        assert resp.status == 405
        assert resp.content_type == "text/plain"


@pytest.mark.asyncio
async def test_api_404_still_answers_in_the_wire_envelope() -> None:
    # The sibling 404 branch (which this fix sits beside) is unchanged.
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/api/does-not-exist")
        assert resp.status == 404
        assert resp.content_type == "application/json"
        body = await resp.json()
        assert body["error"]["code"] == "not_found"
