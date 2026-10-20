"""Tests for the dist-ROOT file handlers: /claw.svg, /manifest.webmanifest, /sw.js and the
licence notices."""

import re
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


# ── PWA dist-root files ──


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
    is an install prompt that never appears. Asserted against the source of the fallback
    (``dashboard/fallbacks.py``).
    """
    from pathlib import Path

    import personalclaw.dashboard.fallbacks as fallbacks_mod

    source = Path(fallbacks_mod.__file__).read_text(encoding="utf-8")
    assert '("/assets/", "/icons/", "/sprites/", "/vendor/")' in source


def test_unmatched_api_routes_answer_in_the_wire_envelope() -> None:
    """An unmatched ``/api/*`` route must return the coded JSON envelope, never
    aiohttp's text/plain default — a JSON client cannot parse plaintext, so it
    cannot tell "route gone" from "server broke". Asserted against the source
    (same closure as above): the ``/api/`` branch must precede the HTML fallback
    and answer via ``json_error("not_found", …)``.
    """
    from pathlib import Path

    import personalclaw.dashboard.fallbacks as fallbacks_mod

    source = Path(fallbacks_mod.__file__).read_text(encoding="utf-8")
    api_branch = source.find('if request.path.startswith("/api/"):')
    html_fallback = source.find("return await handlers.index(request)")
    assert api_branch != -1, "the spa_fallback /api branch is gone"
    assert html_fallback != -1
    assert api_branch < html_fallback, "the /api branch must precede the HTML fallback"
    assert 'return json_error("not_found", status=404)' in source


def test_the_pwa_files_are_served_at_the_origin_root() -> None:
    """A service worker's scope is its path: served from ``/assets/`` it could only
    control ``/assets/``, so the gateway serves ``/sw.js`` at the root, and the manifest beside
    it (its ``start_url`` and ``scope`` resolve against its own URL)."""
    from gateway_routes_shared import handler_for

    from personalclaw.dashboard import handlers

    assert handler_for("GET", "/sw.js") is handlers.service_worker
    assert handler_for("GET", "/manifest.webmanifest") is handlers.manifest_webmanifest


# ── Web fonts under /fonts/* (issue #2916) ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("dm-sans.woff2", "font/woff2"),
        ("legacy.woff", "font/woff"),
        ("fallback.ttf", "font/ttf"),
        ("fallback.otf", "font/otf"),
    ],
)
async def test_font_asset_states_its_content_type(
    tmp_path, filename: str, content_type: str
) -> None:
    """The Content-Type is declared, never guessed.

    aiohttp's ``FileResponse`` resolves the type from its own private ``mimetypes``
    table, which lacks the woff/woff2 entries — so ``add_static`` would send
    ``application/octet-stream`` for a first-party font asset (#2916).
    """
    from personalclaw.dashboard.handlers import core

    fonts = tmp_path / "dist" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / filename).write_bytes(b"\x00\x01")

    req = MagicMock()
    req.match_info = {"name": filename}
    with patch.object(core, "_DIST_DIR", tmp_path / "dist"):
        resp = await core.font_asset(req)
    assert isinstance(resp, web.FileResponse)
    assert resp.headers["Content-Type"] == content_type


@pytest.mark.asyncio
async def test_font_asset_missing_returns_404_and_does_not_raise(tmp_path) -> None:
    """A missing font must RETURN 404, not raise.

    ``spa_fallback`` turns a raised ``HTTPNotFound`` for a non-excluded GET into
    ``index.html``, and HTML decoded as a font is the "invalid sfntVersion" failure
    this route exists to prevent. ``/fonts/`` is deliberately NOT in the fallback
    exclusion tuple, so the handler itself must answer the status.
    """
    from personalclaw.dashboard.handlers import core

    (tmp_path / "dist" / "fonts").mkdir(parents=True)

    req = MagicMock()
    req.match_info = {"name": "does-not-exist.woff2"}
    with patch.object(core, "_DIST_DIR", tmp_path / "dist"):
        resp = await core.font_asset(req)
    assert resp.status == 404
    assert resp.content_type == "text/plain"


@pytest.mark.asyncio
async def test_font_asset_unsupported_extension_404s(tmp_path) -> None:
    """A present-but-non-font file is not served with a guessed type — 404 instead."""
    from personalclaw.dashboard.handlers import core

    fonts = tmp_path / "dist" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "notes.txt").write_text("secret-ish")

    req = MagicMock()
    req.match_info = {"name": "notes.txt"}
    with patch.object(core, "_DIST_DIR", tmp_path / "dist"):
        resp = await core.font_asset(req)
    assert resp.status == 404


@pytest.mark.asyncio
async def test_font_asset_rejects_path_traversal(tmp_path) -> None:
    """A ``name`` that resolves outside the fonts directory must 404, never serve."""
    from personalclaw.dashboard.handlers import core

    dist = tmp_path / "dist"
    (dist / "fonts").mkdir(parents=True)
    (dist / "sw.js").write_text("// service worker")

    req = MagicMock()
    req.match_info = {"name": "../sw.js"}
    with patch.object(core, "_DIST_DIR", dist):
        resp = await core.font_asset(req)
    assert resp.status == 404
    assert not isinstance(resp, web.FileResponse)


@pytest.mark.asyncio
async def test_font_asset_serves_through_symlinked_dist(tmp_path) -> None:
    """In dev, static/dist is a symlink to web/dist — the handler must follow it."""
    from personalclaw.dashboard.handlers import core

    real_dist = tmp_path / "real-dist"
    (real_dist / "fonts").mkdir(parents=True)
    (real_dist / "fonts" / "dm-sans.woff2").write_bytes(b"\x00")

    link = tmp_path / "linked-dist"
    link.symlink_to(real_dist)

    req = MagicMock()
    req.match_info = {"name": "dm-sans.woff2"}
    with patch.object(core, "_DIST_DIR", link):
        resp = await core.font_asset(req)
    assert isinstance(resp, web.FileResponse)
    assert resp.headers["Content-Type"] == "font/woff2"


#: What the booted gateway answers under ``/fonts/``: each font the build ships under its stated
#: type (a static mount sends a ``.woff2`` as ``application/octet-stream``); a font the build did
#: not write as a plain 404 (a static mount raises one, and the SPA fallback answers it with the
#: dashboard's index.html: the "invalid sfntVersion" failure); and a file there that is no font as
#: a 404 too (a static mount serves anything in the folder).
_FONT_DOORS = (
    ("dm-sans.woff2", 200, "font/woff2"),
    ("legacy.woff", 200, "font/woff"),
    ("fallback.ttf", 200, "font/ttf"),
    ("fallback.otf", 200, "font/otf"),
    ("does-not-exist.woff2", 404, "text/plain"),
    ("notes.txt", 404, "text/plain"),
)


@pytest.mark.asyncio
async def test_the_gateway_serves_each_font_under_its_own_type(tmp_path, monkeypatch) -> None:
    """``fonts.css`` names the fonts at ``/fonts/*``. The real gateway (``start_dashboard``), over
    a build that ships four fonts, a file that is no font and the dashboard's index.html, answers
    each request under ``/fonts/`` with the font and its stated type, or a plain 404."""
    import aiohttp

    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    dist = _dist_with_index(tmp_path)
    (dist / "fonts").mkdir()
    shipped = {
        name: f"bytes of {name}".encode() for name, status, _ in _FONT_DOORS if status == 200
    }
    for name, data in {**shipped, "notes.txt": b"not a font"}.items():
        (dist / "fonts" / name).write_bytes(data)
    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)

    runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        port = runner.addresses[0][1]
        async with aiohttp.ClientSession() as http:
            for name, status, content_type in _FONT_DOORS:
                async with http.get(f"http://127.0.0.1:{port}/fonts/{name}") as resp:
                    body = await resp.read()
                    assert resp.status == status, name
                    assert resp.content_type == content_type, (name, resp.content_type)
                    if status == 200:
                        assert body == shipped[name], name
                    else:
                        assert b"<!doctype" not in body.lower(), name
    finally:
        await runner.cleanup()


# ── The licence notices, /THIRD_PARTY_NOTICES.txt and /THIRD_PARTY_NOTICES_NPM.txt ──
#
# Settings → Updates links both. They are third-party text, so they are driven here through the
# real security-headers middleware and SPA fallback, as the gateway stacks them.

_NOTICES = (
    ("/THIRD_PARTY_NOTICES.txt", "Third-party notices for the PersonalClaw dashboard\n"),
    ("/THIRD_PARTY_NOTICES_NPM.txt", "Third-party notices for the PersonalClaw dashboard's code\n"),
)


def _notices_app() -> web.Application:
    from personalclaw.dashboard import handlers
    from personalclaw.dashboard.fallbacks import spa_fallback
    from personalclaw.dashboard.server import _security_headers_middleware

    app = web.Application(middlewares=[_security_headers_middleware, spa_fallback])
    app.router.add_get("/THIRD_PARTY_NOTICES.txt", handlers.third_party_notices)
    app.router.add_get("/THIRD_PARTY_NOTICES_NPM.txt", handlers.third_party_notices_npm)
    return app


def _dist_with_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>PersonalClaw</title>")
    return dist


@pytest.mark.asyncio
async def test_the_licence_notices_are_served_as_plain_text_a_browser_cannot_sniff(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import core

    dist = _dist_with_index(tmp_path)
    for path, body in _NOTICES:
        (dist / path.lstrip("/")).write_text(body)
    with patch.object(core, "_DIST_DIR", dist):
        async with TestClient(TestServer(_notices_app())) as client:
            for path, body in _NOTICES:
                resp = await client.get(path)
                assert resp.status == 200, path
                assert resp.headers["Content-Type"] == "text/plain; charset=utf-8", path
                assert resp.headers["X-Content-Type-Options"] == "nosniff", path
                assert await resp.text() == body


@pytest.mark.asyncio
async def test_a_notice_the_build_did_not_write_is_a_404_not_the_dashboard(tmp_path):
    """The SPA fallback turns a raised 404 into index.html, which a user opening the licence
    link would read as the dashboard itself. The handler answers the 404 instead."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import core

    dist = _dist_with_index(tmp_path)
    with patch.object(core, "_DIST_DIR", dist):
        async with TestClient(TestServer(_notices_app())) as client:
            for path, _body in _NOTICES:
                resp = await client.get(path)
                assert resp.status == 404, path
                assert resp.content_type == "text/plain", path
                assert "<!doctype" not in (await resp.text()).lower(), path


def test_the_licence_notices_are_served_at_the_paths_settings_links() -> None:
    """The gateway serves each notice at the origin root by its own handler, and Settings →
    Updates links those same two paths (``updatesPanelLicences.test.tsx`` holds the links
    themselves)."""
    from pathlib import Path

    from gateway_routes_shared import handler_for

    from personalclaw.dashboard import handlers

    served = {
        "/THIRD_PARTY_NOTICES.txt": handlers.third_party_notices,
        "/THIRD_PARTY_NOTICES_NPM.txt": handlers.third_party_notices_npm,
    }
    for path, handler in served.items():
        assert handler_for("GET", path) is handler, path
    panel = Path(__file__).resolve().parents[1] / "web/src/pages/settings/UpdatesPanel.tsx"
    linked = set(
        re.findall(r'href="(/THIRD_PARTY_NOTICES[^"]*)"', panel.read_text(encoding="utf-8"))
    )
    assert linked == set(served) == {path for path, _body in _NOTICES}
