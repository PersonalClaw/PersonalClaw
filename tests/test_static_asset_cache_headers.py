"""Rails for #2933: ``_security_headers_middleware`` (``dashboard/server.py``, named
``no_cache_middleware`` when this was filed) used to set
``Cache-Control: no-store, no-cache, must-revalidate, max-age=0`` on EVERY response
via ``resp.headers.setdefault(...)``, including the content-hashed Vite bundles
served under ``/assets/*``. Those URLs change whenever their content does (that is
the whole point of the hash), so they are safe to cache forever — ``no-store``
just forced a full re-download of the SPA's JS/CSS on every page load and every
in-app navigation.

The fix narrows the middleware to treat only the literal ``/assets/`` prefix as
immutable; every other response class it touches — the HTML entry point, API
routes, and the other UI-transport static mounts (``/fonts``, ``/sprites``,
``/vendor``, ``/icons``) — keeps the original ``no-store`` policy untouched. These
rails boot the REAL gateway on an ephemeral port and assert the header over actual
HTTP, the same pattern ``test_gateway_boot_app_source_seed.py`` uses, because
nothing short of standing up ``start_dashboard()`` exercises the real middleware
chain (order matters: ``_security_headers_middleware`` is the outermost one, so it
sees the static handler's response before anything else can react to it).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


async def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, int]:
    """Boot the real gateway on an ephemeral port against a minimal fake ``dist/``,
    so ``/assets/*`` and ``/`` resolve to real files instead of the "unbundled"
    fallback page. Returns ``(runner, port)``; caller must ``await runner.cleanup()``.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")

    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>fake spa</html>", encoding="utf-8")
    # A stand-in for a Vite content-hashed chunk, e.g. AgentsSection-CJTRDiYf.js.
    (dist_dir / "assets" / "app-deadbeef12.js").write_text("console.log('hi')", encoding="utf-8")

    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    # Two independent `_DIST_DIR` module constants read the same on-disk layout:
    # server.py's controls route REGISTRATION (whether `/assets` mounts at all),
    # handlers/core.py's controls what `index()` reads. Both must point at the
    # fake dist for `/` to serve real HTML instead of the "unbundled" 503 page.
    monkeypatch.setattr(server_mod, "_DIST_DIR", dist_dir)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist_dir)

    runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    return runner, runner.addresses[0][1]


@pytest.mark.asyncio
async def test_hashed_asset_is_cacheable_immutable(tmp_path, monkeypatch) -> None:
    """The bug itself: a content-hashed `/assets/*` bundle must come back long-lived
    + immutable, not `no-store`. FAILS before the fix (asserts the OLD no-store
    value was never on this path) and PASSES after."""
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/assets/app-deadbeef12.js") as resp,
        ):
            assert resp.status == 200
            cache_control = resp.headers["Cache-Control"]
            assert cache_control == "public, max-age=31536000, immutable"
            assert "no-store" not in cache_control
            # Pragma/Expires are set ONLY in the no-store branch — a cacheable
            # response carrying them would be self-contradictory.
            assert "Pragma" not in resp.headers
            assert "Expires" not in resp.headers
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_html_entry_point_stays_no_store(tmp_path, monkeypatch) -> None:
    """index.html is not content-addressed: caching it would pin a browser to a
    stale build indefinitely. It must keep the pre-fix no-store policy exactly."""
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/") as resp,
        ):
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            assert resp.headers["Pragma"] == "no-cache"
            assert resp.headers["Expires"] == "0"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_api_route_stays_no_store(tmp_path, monkeypatch) -> None:
    """The security-sensitive half of the fix: only the literal `/assets/` prefix
    is exempted, so an API response is never accidentally made cacheable."""
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/api/healthz") as resp,
        ):
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
    finally:
        await runner.cleanup()
