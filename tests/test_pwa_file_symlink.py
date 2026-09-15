"""Tests for the dist-ROOT file handlers: /claw.svg, /manifest.webmanifest, /sw.js."""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_favicon_serves_through_symlinked_dist(tmp_path):
    """In dev, static/dist is a symlink to web/dist (via
    ensure_dev_dist_symlink). The favicon handler must serve the real file
    through the symlink (dist-root files have no static route; without this
    handler the request falls through to the SPA fallback, which serves
    index.html as text/html — a broken favicon)."""
    from personalclaw.dashboard.handlers import core

    real_dist = tmp_path / "real-dist"
    real_dist.mkdir()
    (real_dist / "claw.svg").write_text("<svg/>")

    link = tmp_path / "linked-dist"
    link.symlink_to(real_dist)

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", link):
        resp = await core.favicon(req)
    assert isinstance(resp, web.FileResponse)


@pytest.mark.asyncio
async def test_favicon_404_when_missing(tmp_path):
    """No claw.svg in dist → clean 404 (not a SPA-fallback HTML response)."""
    from personalclaw.dashboard.handlers import core

    dist = tmp_path / "dist"
    dist.mkdir()

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", dist):
        with pytest.raises(web.HTTPNotFound):
            await core.favicon(req)


# ── PWA dist-root files (MOBILE-COMPANION T3.1) ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "filename", "content_type"),
    [
        ("manifest_webmanifest", "manifest.webmanifest", "application/manifest+json"),
        ("service_worker", "sw.js", "text/javascript"),
    ],
)
async def test_pwa_root_file_states_its_content_type(
    tmp_path, handler_name: str, filename: str, content_type: str
) -> None:
    """The content type is declared, never guessed.

    ``.webmanifest`` is absent from Python's ``mimetypes`` table on a stock install,
    so ``FileResponse`` alone would send ``application/octet-stream`` and the browser
    would discard the manifest — no install prompt, no error anywhere.
    """
    from personalclaw.dashboard.handlers import core

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / filename).write_text("x")

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", dist):
        resp = await getattr(core, handler_name)(req)
    assert isinstance(resp, web.FileResponse)
    assert resp.headers["Content-Type"] == content_type


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["manifest_webmanifest", "service_worker"])
async def test_pwa_root_file_missing_returns_404_and_does_not_raise(
    tmp_path, handler_name: str
) -> None:
    """A missing file must RETURN 404, not raise HTTPNotFound.

    ``spa_fallback`` converts a raised ``HTTPNotFound`` into ``index.html``. HTML
    served for ``/sw.js`` fails service-worker registration on its MIME check, and
    HTML served for the manifest fails to parse — both silently, with the dashboard
    still looking perfectly fine. Returning the status directly skips that
    middleware, so the failure is diagnosable.
    """
    from personalclaw.dashboard.handlers import core

    dist = tmp_path / "dist"
    dist.mkdir()

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", dist):
        resp = await getattr(core, handler_name)(req)
    assert resp.status == 404
    assert resp.content_type == "text/plain"


@pytest.mark.asyncio
async def test_pwa_root_files_serve_through_symlinked_dist(tmp_path) -> None:
    """In dev, static/dist is a symlink to web/dist — both handlers must follow it."""
    from personalclaw.dashboard.handlers import core

    real_dist = tmp_path / "real-dist"
    real_dist.mkdir()
    (real_dist / "manifest.webmanifest").write_text("{}")
    (real_dist / "sw.js").write_text("//")

    link = tmp_path / "linked-dist"
    link.symlink_to(real_dist)

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", link):
        assert isinstance(await core.manifest_webmanifest(req), web.FileResponse)
        assert isinstance(await core.service_worker(req), web.FileResponse)


def test_icons_and_pwa_roots_are_excluded_from_the_spa_fallback() -> None:
    """``/icons/`` must 404 rather than fall back to index.html.

    A manifest icon that resolves to HTML is an invalid icon, and the only symptom
    is an install prompt that never appears. Asserted against the source because the
    exclusion tuple is inside a closure in ``create_app``.
    """
    from pathlib import Path

    import personalclaw.dashboard.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert '("/assets/", "/icons/", "/sprites/", "/vendor/")' in source


def test_unmatched_api_routes_answer_in_the_wire_envelope() -> None:
    """An unmatched ``/api/*`` route must return the coded JSON envelope, never
    aiohttp's text/plain default — a JSON client cannot parse plaintext, so it
    cannot tell "route gone" from "server broke". Asserted against the source
    (same closure as above): the ``/api/`` branch must precede the HTML fallback
    and answer via ``json_error("not_found", …)``.
    """
    from pathlib import Path

    import personalclaw.dashboard.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    api_branch = source.find('if request.path.startswith("/api/"):')
    html_fallback = source.find("return await handlers.index(request)")
    assert api_branch != -1, "the spa_fallback /api branch is gone"
    assert html_fallback != -1
    assert api_branch < html_fallback, "the /api branch must precede the HTML fallback"
    assert 'return json_error("not_found", status=404)' in source


@pytest.mark.asyncio
async def test_wrong_method_on_api_route_answers_in_the_wire_envelope() -> None:
    """A wrong method on an ``/api/*`` route raises ``HTTPMethodNotAllowed`` from the
    ROUTER (not a handler), which aiohttp answers ``text/plain`` by default — illegible
    to the JSON clients that read this surface, exactly like the raised-404 above.
    ``spa_fallback`` must convert it into the SAME coded wire envelope, preserving the
    405 status and the ``Allow`` header the router set (it names the methods the route
    DOES accept, so the client can correct its request).

    Driven through a real ``TestServer`` so the router is the one that raises — a
    handler called directly never can, which is why the static census cannot see it.
    """
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.server import spa_fallback

    async def _chat_post(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[spa_fallback])
    app.router.add_post("/api/chat", _chat_post)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/chat")  # POST-only route, wrong method
        assert resp.status == 405
        assert resp.content_type == "application/json"
        assert "POST" in resp.headers.get("Allow", "")
        body = await resp.json()
        assert set(body) == {"error"}
        assert body["error"]["code"] == "method_not_allowed"
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]


@pytest.mark.asyncio
async def test_wrong_method_on_non_api_route_keeps_the_default_405() -> None:
    """The rewrite is scoped to ``/api/*``. A wrong method on a non-API route falls
    through untouched — aiohttp's default ``text/plain`` 405 — so page routes keep
    their normal behavior."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.server import spa_fallback

    async def _page_post(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(middlewares=[spa_fallback])
    app.router.add_post("/page", _page_post)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/page")  # wrong method, non-API
        assert resp.status == 405
        assert resp.content_type == "text/plain"


def test_pwa_routes_are_registered_at_the_origin_root() -> None:
    """A service worker's scope is its path: served from ``/assets/`` it could only
    control ``/assets/``, so ``/sw.js`` must be registered at the root."""
    from pathlib import Path

    import personalclaw.dashboard.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'add_get("/sw.js", handlers.service_worker)' in source
    assert 'add_get("/manifest.webmanifest", handlers.manifest_webmanifest)' in source
