"""The dashboard's Content-Security-Policy allows what the app needs and no more.

Two allowances were wider than anything the app does:

* ``connect-src`` named ``ws://localhost:*`` and ``ws://127.0.0.1:*`` — a WebSocket to EVERY
  local port, when the app only ever opens one, to the host and port the page came from
  (``location.host``). A script in the page could have reached any other local service's socket.
* there was no ``form-action``, which does not fall back to ``default-src`` (CSP L3), so a form
  in the page could post to any origin. The app posts no form anywhere.

Read off REAL responses from a booted gateway, as ``test_dashboard_frame_headers.py`` does, so a
header the middleware computes but something later drops cannot pass.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

DOORS = ("/", "/api/healthz", "/assets/app-deadbeef12.js", "/definitely-not-a-route")


def _parse_csp(header: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            out[parts[0].lower()] = parts[1:]
    return out


async def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, int]:
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


async def _csp(tmp_path, monkeypatch, path: str) -> tuple[dict[str, list[str]], int]:
    import aiohttp

    runner, port = await _boot(tmp_path, monkeypatch)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}{path}") as resp,
        ):
            return _parse_csp(resp.headers["Content-Security-Policy"]), port
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DOORS)
async def test_the_page_opens_sockets_only_to_its_own_port(path, tmp_path, monkeypatch) -> None:
    directives, port = await _csp(tmp_path, monkeypatch, path)
    connect = directives["connect-src"]
    assert "'self'" in connect, "the app's own socket and its API calls"
    wildcards = [s for s in connect if s.endswith(":*")]
    assert wildcards == [], f"{path} lets the page reach every local port: {wildcards}"
    assert (
        f"ws://127.0.0.1:{port}" in connect and f"ws://localhost:{port}" in connect
    ), "the loopback socket is named at the port the page was served on"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DOORS)
async def test_no_form_in_the_page_posts_to_another_origin(path, tmp_path, monkeypatch) -> None:
    directives, _port = await _csp(tmp_path, monkeypatch, path)
    assert directives.get("form-action") == [
        "'self'"
    ], f"{path}: form-action does not fall back to default-src, so without it any origin"


def test_the_policy_with_no_port_known_is_tighter_not_looser() -> None:
    """A request whose listening port cannot be read gets `'self'` alone for its sockets —
    never a wildcard back."""
    from personalclaw.dashboard.server import dashboard_csp

    connect = _parse_csp(dashboard_csp())["connect-src"]
    assert connect == ["'self'"]
    assert _parse_csp(dashboard_csp(10000))["connect-src"] == [
        "'self'",
        "ws://localhost:10000",
        "ws://127.0.0.1:10000",
    ]
