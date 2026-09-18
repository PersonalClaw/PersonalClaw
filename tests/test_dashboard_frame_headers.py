"""Rails for #2735: the dashboard declared no framing policy at all.

``no_cache_middleware`` (``dashboard/server.py``, renamed here to
``_security_headers_middleware`` now that the headers are its main job) set a detailed
``Content-Security-Policy`` — ``default-src``, ``script-src``, ``frame-src`` and six
more — but no ``frame-ancestors``, and ``X-Frame-Options`` appeared nowhere in
``src/``. ``frame-ancestors`` is one of the CSP directives that does **not** fall back
to ``default-src`` (CSP L3 §6.1), and ``frame-src`` is the opposite control: it governs
what this page may frame, not who may frame this page. So nothing restricted embedding
of an authenticated PersonalClaw instance.

The project already held the standard on the *other* surface it serves:
``artifacts/deploy.py`` sets ``frame-ancestors 'self'`` with the reasoning written out
("embeddable in the dashboard's own pane, nowhere else"). These rails hold the dashboard
to the SAME standard, and :class:`TestOneStandardForBothSurfaces` asserts that as an
equality rather than as two independent literals, so the two cannot drift apart again.

Why the value is ``'self'`` / ``SAMEORIGIN`` and not ``'none'`` / ``DENY``: the dashboard
legitimately frames its own artifact pane and its own blob: widget iframes, and behind a
reverse proxy the page's origin IS ``dashboard.public_url``, so ``'self'`` already names
the public host without an allowlist. ``DENY`` would break the in-app artifact open.

Every assertion here reads the header off a REAL HTTP response from a REAL booted
gateway (the ``test_static_asset_cache_headers.py`` pattern), never off the middleware's
source constant — a header this middleware *computes* but a later layer strips would
otherwise pass a source-level rail. The middleware has two branches (the immutable-asset
path and everything else) and the headers are set outside both, so the branches are
covered as separate doors rather than assumed equivalent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

#: Promoted from ad-hoc per-response headers to a global default by #2735. ARCC's
#: "Secure HTTP Headers" guidance lists all three among the headers to set for ALL
#: responses; before this change they appeared only on specific artifact/file responses.
EXPECTED_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
}

#: Doors through ``_security_headers_middleware``. ``/assets/`` takes the OTHER branch of
#: one ``if`` in that middleware, and ``/api/healthz`` is a JSON response rather than a
#: document, so a fix applied to the HTML path alone reds here.
DOORS = (
    "/",
    "/api/healthz",
    "/assets/app-deadbeef12.js",
    "/definitely-not-a-route",
)


def _parse_csp(header: str) -> dict[str, list[str]]:
    """``"default-src 'none'; frame-ancestors 'self'"`` → ``{"default-src": ["'none'"], …}``."""
    out: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            out[parts[0].lower()] = parts[1:]
    return out


async def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, int]:
    """Boot the real gateway on an ephemeral port against a minimal fake ``dist/``.

    Returns ``(runner, port)``; the caller must ``await runner.cleanup()``.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")

    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>fake spa</html>", encoding="utf-8")
    (dist_dir / "assets" / "app-deadbeef12.js").write_text("console.log('hi')", encoding="utf-8")

    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist_dir)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist_dir)

    runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    return runner, runner.addresses[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DOORS)
async def test_every_door_declares_who_may_frame_it(path, tmp_path, monkeypatch) -> None:
    """The defect itself: no response declared a framing policy.

    Asserted per door rather than once, because the middleware branches on the path and
    a per-route fix is exactly how one of these stays broken.
    """
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}{path}") as resp,
        ):
            directives = _parse_csp(resp.headers["Content-Security-Policy"])
            assert directives["frame-ancestors"] == ["'self'"], (
                f"{path} publishes a CSP with no usable frame-ancestors; "
                "frame-ancestors does NOT inherit from default-src (CSP L3 §6.1)"
            )
            # The legacy fallback, for user agents that predate frame-ancestors.
            assert resp.headers["X-Frame-Options"] == "SAMEORIGIN", path
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DOORS)
async def test_every_door_carries_the_arcc_baseline_headers(path, tmp_path, monkeypatch) -> None:
    """``Referrer-Policy`` + ``X-Content-Type-Options`` were per-response, now global."""
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}{path}") as resp,
        ):
            for name, value in EXPECTED_HEADERS.items():
                assert resp.headers.get(name) == value, f"{path} is missing {name}: {value}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_no_existing_csp_directive_was_weakened(tmp_path, monkeypatch) -> None:
    """Adding a directive must not relax one. Pins the values #2735 found in place.

    Named individually rather than by comparing the whole string, so this reds on a
    *weakening* (``object-src 'none'`` → ``'self'``) and not merely on any edit.
    """
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/") as resp,
        ):
            directives = _parse_csp(resp.headers["Content-Security-Policy"])
            assert directives["default-src"] == ["'self'"]
            assert directives["object-src"] == ["'none'"]
            assert directives["base-uri"] == ["'self'"]
            # The in-page controls, untouched: `frame-src` is what the dashboard MAY
            # frame (its artifact pane + blob: widget iframes) and is a different
            # question from `frame-ancestors`. Both must survive together.
            assert directives["frame-src"] == ["'self'", "blob:"]
            assert directives["worker-src"] == ["'self'", "blob:"]
            assert "'self'" in directives["script-src"]
            assert "'self'" in directives["connect-src"]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_a_stricter_per_response_header_still_wins(tmp_path, monkeypatch) -> None:
    """The middleware must ``setdefault``, never overwrite.

    Artifact responses are served through THIS app, so they pass through this
    middleware, and ``artifacts/deploy.py`` deliberately sets a STRICTER
    ``Referrer-Policy: no-referrer`` plus its own fence CSP. A middleware that assigned
    instead of defaulting would silently downgrade the artifact fence to the dashboard's
    policy — a weakening introduced by a hardening change, which is the failure shape
    worth a rail of its own.
    """
    from personalclaw.artifacts.deploy import SERVE_HEADERS

    assert SERVE_HEADERS["Referrer-Policy"] == "no-referrer"
    assert SERVE_HEADERS["Referrer-Policy"] != EXPECTED_HEADERS["Referrer-Policy"]

    from aiohttp import web

    import personalclaw.dashboard.server as server_mod

    # Drive the real middleware over a handler that pre-sets the artifact's headers.
    async def handler(_request):
        return web.Response(text="artifact", headers=dict(SERVE_HEADERS))

    app = web.Application()
    app.router.add_get("/x", handler)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    middleware = server_mod._security_headers_middleware
    app.middlewares.append(middleware)

    from aiohttp.test_utils import TestClient, TestServer

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/x")
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        # Its own fence CSP survives intact — the dashboard's policy did not replace it.
        assert _parse_csp(resp.headers["Content-Security-Policy"])["default-src"] == ["'none'"]
    finally:
        await client.close()


class TestOneStandardForBothSurfaces:
    """The two surfaces this project serves must not drift on framing again.

    #2735's root observation was that one surface had the control and the other did not,
    so the rail is an EQUALITY between them rather than two independent literals.
    """

    def test_the_dashboard_and_the_artifact_server_agree(self) -> None:
        from personalclaw.artifacts.deploy import ARTIFACT_SERVE_CSP
        from personalclaw.dashboard.server import dashboard_csp

        artifact = _parse_csp(ARTIFACT_SERVE_CSP)
        dashboard = _parse_csp(dashboard_csp())
        assert artifact["frame-ancestors"] == dashboard["frame-ancestors"] == ["'self'"]

    def test_the_csp_parser_is_not_vacuous(self) -> None:
        """Detection direction: a parser that returned ``{}`` would make every
        ``directives[...]`` above raise rather than pass, but a parser that silently
        dropped only ``frame-ancestors`` would make them all vacuous. Prove it sees a
        planted directive and prove it reports a MISSING one as absent."""
        planted = _parse_csp("default-src 'none'; frame-ancestors 'self' https://a.example")
        assert planted["frame-ancestors"] == ["'self'", "https://a.example"]
        assert "frame-ancestors" not in _parse_csp("default-src 'none'; frame-src 'self'")
