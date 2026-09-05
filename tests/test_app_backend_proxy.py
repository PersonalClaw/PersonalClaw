"""App-backend inbound proxy-signature authentication (PHF-3 / SH2.1–2.4).

The boundary under test is FAIL-CLOSED: an app backend binds on loopback with no auth
of its own, so the ONLY thing proving a request came from the gateway proxy is the
``X-PersonalClaw-Proxy`` HMAC signature. These tests drive both halves of the contract:

- the per-app secret is minted 0600, read back, and never emitted by the value itself;
- the SDK middleware accepts a correctly-signed request and refuses (401) every
  degenerate signature — absent, malformed, stale (>±60s), wrong secret — with the route
  body never running;
- ``/health`` is exempt (the watchdog probes it directly, not through the signing proxy);
- the signer (proxy side) and verifier (backend side) agree on the wire string, so a
  request signed with :func:`sign_proxy_request` verifies under the middleware.
"""

from __future__ import annotations

import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_secret
from personalclaw.sdk.security import (
    APP_SECRET_ENV,
    PROXY_SIGNATURE_HEADER,
    build_signing_string,
    require_proxy_signature,
    sign_proxy_request,
)

_SECRET = "a" * 64


# --------------------------------------------------------------------------- #
# Secret minting (SH2.1)
# --------------------------------------------------------------------------- #
def test_ensure_app_secret_is_0600_and_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(app_secret, "app_dir", lambda name: tmp_path / name)
    (tmp_path / "growth").mkdir()

    s1 = app_secret.ensure_app_secret("growth")
    assert s1 and len(s1) == 64  # 256-bit hex
    path = app_secret.secret_path("growth")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    # value is a hex token, not a log line / not empty
    assert all(c in "0123456789abcdef" for c in s1)
    # idempotent: second call returns the same secret (does not re-mint)
    assert app_secret.ensure_app_secret("growth") == s1
    # a plain reader sees the same value; a reader for an unminted app sees None
    assert app_secret.read_app_secret("growth") == s1
    assert app_secret.read_app_secret("minutes") is None


def test_ensure_app_secret_fails_closed_when_unwritable(tmp_path, monkeypatch):
    # A path whose parent does not exist and cannot be created → mint returns None so the
    # supervisor declines to start an unprotected backend.
    monkeypatch.setattr(app_secret, "app_dir", lambda name: tmp_path / "nope" / name)
    assert app_secret.ensure_app_secret("growth") is None


def test_secret_value_never_logged_by_mint(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app_secret, "app_dir", lambda name: tmp_path / name)
    (tmp_path / "growth").mkdir()
    with caplog.at_level("DEBUG"):
        s = app_secret.ensure_app_secret("growth")
    assert s not in caplog.text


# --------------------------------------------------------------------------- #
# Middleware verification (SH2.2) — a fake app backend with one signed route.
# --------------------------------------------------------------------------- #
def _make_backend(secret: str | None = _SECRET) -> web.Application:
    async def echo(request: web.Request) -> web.Response:
        # Body must be readable inside the handler — the middleware stashes it.
        body = request.get("body_bytes")
        return web.json_response(
            {"ok": True, "path": request.raw_path, "body": (body or b"").decode()}
        )

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[require_proxy_signature(secret)])
    app.router.add_route("*", "/health", health)
    app.router.add_route("*", "/{tail:.*}", echo)
    return app


async def _client(secret: str | None = _SECRET) -> TestClient:
    c = TestClient(TestServer(_make_backend(secret)))
    await c.start_server()
    return c


@pytest.mark.asyncio
async def test_valid_signature_is_accepted():
    c = await _client()
    try:
        body = b'{"k":"v"}'
        # Sign the exact wire target the backend will see.
        sig = sign_proxy_request(_SECRET, "POST", "/artifacts?dimension=x", body)
        r = await c.post("/artifacts?dimension=x", data=body, headers={PROXY_SIGNATURE_HEADER: sig})
        assert r.status == 200
        got = await r.json()
        assert got["path"] == "/artifacts?dimension=x"
        assert got["body"] == '{"k":"v"}'  # handler read the stashed body
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_absent_signature_is_401():
    c = await _client()
    try:
        r = await c.get("/artifacts")
        assert r.status == 401
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_malformed_signature_is_401():
    c = await _client()
    try:
        r = await c.get("/artifacts", headers={PROXY_SIGNATURE_HEADER: "garbage-no-colon"})
        assert r.status == 401
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_stale_signature_is_401():
    c = await _client()
    try:
        old_ts = int(time.time()) - 120  # 2 minutes ago → outside ±60s
        sig = sign_proxy_request(_SECRET, "GET", "/artifacts", b"", ts=old_ts)
        r = await c.get("/artifacts", headers={PROXY_SIGNATURE_HEADER: sig})
        assert r.status == 401
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_wrong_secret_is_401():
    c = await _client()
    try:
        sig = sign_proxy_request("b" * 64, "GET", "/artifacts", b"")  # different secret
        r = await c.get("/artifacts", headers={PROXY_SIGNATURE_HEADER: sig})
        assert r.status == 401
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_tampered_body_is_401():
    c = await _client()
    try:
        sig = sign_proxy_request(_SECRET, "POST", "/artifacts", b'{"k":"v"}')
        # Forward a different body than what was signed → sha256 mismatch.
        r = await c.post(
            "/artifacts", data=b'{"k":"TAMPERED"}', headers={PROXY_SIGNATURE_HEADER: sig}
        )
        assert r.status == 401
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_health_is_exempt_without_signature():
    c = await _client()
    try:
        r = await c.get("/health")  # no signature header at all
        assert r.status == 200
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_no_secret_in_env_fails_closed():
    # A backend constructed with no secret refuses every non-exempt request, even a
    # correctly-formed-looking one — nothing to verify against ⇒ serve nothing.
    c = await _client(secret="")
    try:
        sig = sign_proxy_request(_SECRET, "GET", "/artifacts", b"")
        r = await c.get("/artifacts", headers={PROXY_SIGNATURE_HEADER: sig})
        assert r.status == 401
        # but /health still answers (watchdog probe)
        assert (await c.get("/health")).status == 200
    finally:
        await c.close()


def test_middleware_reads_secret_from_env(monkeypatch):
    # Construction-time env read: the supervisor injects PERSONALCLAW_APP_SECRET.
    monkeypatch.setenv(APP_SECRET_ENV, _SECRET)
    mw = require_proxy_signature()  # no explicit secret → reads env
    assert callable(mw)


def test_signing_string_is_canonical():
    # Lock the exact wire string so the apps-side middleware (separate repo) matches.
    s = build_signing_string(1700000000, "POST", "/artifacts?x=1", b"hi")
    import hashlib

    assert s == f"1700000000:POST:/artifacts?x=1:{hashlib.sha256(b'hi').hexdigest()}"


# --------------------------------------------------------------------------- #
# End-to-end (SH2.1+SH2.2 composed): real proxy → real subprocess backend that
# installs the verifying middleware. Proves the signer and verifier agree across
# the process boundary, and that a DIRECT (unsigned) hit to the backend port is
# refused — the whole point of the boundary.
# --------------------------------------------------------------------------- #
_BACKEND_SRC = """
import os
from aiohttp import web
from personalclaw.sdk.security import require_proxy_signature


async def ping(request):
    body = request.get("body_bytes")
    return web.json_response({"path": request.raw_path, "body": (body or b"").decode()})


async def health(request):
    return web.json_response({"ok": True})


def make_app():
    app = web.Application(middlewares=[require_proxy_signature()])
    app.router.add_route("*", "/health", health)
    app.router.add_route("*", "/{tail:.*}", ping)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="127.0.0.1", port=int(os.environ["PORT"]), print=None)
"""


@pytest.mark.asyncio
async def test_end_to_end_proxy_signed_and_direct_refused(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    import aiohttp

    from personalclaw.apps import backend_runtime, manager
    from personalclaw.dashboard.handlers.apps import register_app_routes

    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)

    @asynccontextmanager
    async def _proxy_client():
        with (
            patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
            patch.object(manager, "config_dir", return_value=tmp_path),
        ):
            backend_runtime._supervisor = backend_runtime.BackendSupervisor()
            app = web.Application()
            register_app_routes(app)
            async with TestClient(TestServer(app)) as client:
                try:
                    yield client
                finally:
                    backend_runtime.get_backend_supervisor().stop_all()

    # Install a real app whose backend uses the verifying middleware.
    d = tmp_path / "src" / "svc"
    (d / "backend").mkdir(parents=True)
    (d / "app.json").write_text(
        '{"name":"svc","version":"1.0.0","displayName":"Svc","description":"x",'
        '"backend":{"entryPoint":"backend/server.py","type":"python","healthCheck":"/health"}}',
        encoding="utf-8",
    )
    (d / "backend" / "server.py").write_text(_BACKEND_SRC, encoding="utf-8")

    async with _proxy_client() as client:
        assert (await client.post("/api/apps", json={"source": str(d)})).status == 201

        # Through the proxy: the signed request gets through.
        got = None
        for _ in range(50):
            resp = await client.get("/apps/svc/api/ping?q=1")
            if resp.status == 200:
                got = await resp.json()
                break
            await asyncio.sleep(0.1)
        assert got is not None, "signed proxied request never got through"
        assert got["path"] == "/ping?q=1"

        # Directly to the backend port (bypassing the proxy → no signature): refused.
        rb = backend_runtime.get_backend_supervisor().get("svc")
        assert rb is not None
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{rb.base_url}/ping") as direct:
                assert direct.status == 401  # fail-closed: no signature, no service
            # /health is exempt so the watchdog convention still works directly.
            async with sess.get(f"{rb.base_url}/health") as h:
                assert h.status == 200


# ---------------------------------------------------------------------------
# Failure copy (AUD-A12): what the proxy answers when the app backend fails.
#
# The raw aiohttp text ("Cannot connect to host 127.0.0.1:41733 ssl:default
# [...]") used to travel to the app UI verbatim — appSdk's fetch helper
# surfaces the JSON `error` field directly in an error toast. Each failure
# class now answers ONE owned sentence on the wire; the raw exception text
# goes to the log (the caller's job, per providers/failure_copy's contract).
# Upstream 5xx bodies still pass through untouched — that is the app's own
# answer, and rewrapping it would hide the app's error copy.
# ---------------------------------------------------------------------------

_RAW = "Cannot connect to host 127.0.0.1:41733 ssl:default [LEAK_MARKER]"

_UNREACHABLE_COPY = "The app's backend could not be reached. Check the app's logs and try again."
_TIMEOUT_COPY = "The app's backend timed out. Check the app's logs and try again."
_UNEXPECTED_COPY = (
    "The request to the app's backend failed unexpectedly. Check the app's logs and try again."
)
_NOT_RUNNING_COPY = "The app's backend is not running. Check the app's logs and try again."


def _patch_proxy_world(
    monkeypatch, *, running: bool = True, raise_exc: BaseException | None = None
) -> None:
    """Point api_app_proxy at an installed, enabled app; optionally make the session raise."""
    from types import SimpleNamespace

    import aiohttp

    from personalclaw.apps import app_secret as app_secret_mod
    from personalclaw.apps import backend_runtime as rt_mod
    from personalclaw.apps import manager as manager_mod
    from personalclaw.dashboard import token_auth as token_mod

    monkeypatch.setattr(manager_mod, "_read_installed", lambda name: SimpleNamespace(enabled=True))
    rb = SimpleNamespace(base_url="http://127.0.0.1:1") if running else None
    monkeypatch.setattr(
        rt_mod, "get_backend_supervisor", lambda: SimpleNamespace(get=lambda name: rb)
    )
    monkeypatch.setattr(app_secret_mod, "read_app_secret", lambda name: _SECRET)
    monkeypatch.setattr(token_mod, "generate_token", lambda *a, **k: "tok")

    if raise_exc is not None:

        class _RaisingSession:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def request(self, *a, **k):
                raise raise_exc

        monkeypatch.setattr(aiohttp, "ClientSession", _RaisingSession)


async def _proxy_call() -> web.StreamResponse:
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.apps import api_app_proxy

    req = make_mocked_request(
        "GET", "/apps/demo/api/things", match_info={"name": "demo", "tail": "things"}
    )
    return await api_app_proxy(req)


@pytest.mark.asyncio
async def test_proxy_unreachable_backend_speaks_owned_copy(monkeypatch, caplog):
    import json
    import logging

    import aiohttp

    _patch_proxy_world(monkeypatch, raise_exc=aiohttp.ClientError(_RAW))
    with caplog.at_level(logging.WARNING):
        resp = await _proxy_call()
    assert resp.status == 502
    body = json.loads(resp.text)
    assert body["error"] == _UNREACHABLE_COPY
    # The raw aiohttp text is the log's, never the wire's.
    assert "LEAK_MARKER" not in resp.text
    assert "LEAK_MARKER" in caplog.text


@pytest.mark.asyncio
async def test_proxy_timeout_speaks_timeout_copy(monkeypatch, caplog):
    import json
    import logging

    # A total-timeout raises the BUILTIN TimeoutError (asyncio.TimeoutError is its
    # alias on the supported Pythons), which is NOT an aiohttp.ClientError — while a
    # connect/read-phase ServerTimeoutError IS one. The handler's isinstance check
    # must catch both shapes as "timed out".
    _patch_proxy_world(monkeypatch, raise_exc=TimeoutError(_RAW))
    with caplog.at_level(logging.WARNING):
        resp = await _proxy_call()
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == _TIMEOUT_COPY
    assert "LEAK_MARKER" not in resp.text
    assert "LEAK_MARKER" in caplog.text


@pytest.mark.asyncio
async def test_proxy_internal_crash_speaks_unexpected_copy(monkeypatch, caplog):
    import json
    import logging

    _patch_proxy_world(monkeypatch, raise_exc=RuntimeError(_RAW))
    with caplog.at_level(logging.WARNING):
        resp = await _proxy_call()
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == _UNEXPECTED_COPY
    assert "LEAK_MARKER" not in resp.text
    assert "LEAK_MARKER" in caplog.text


@pytest.mark.asyncio
async def test_proxy_backend_not_running_speaks_owned_copy(monkeypatch):
    import json

    _patch_proxy_world(monkeypatch, running=False)
    resp = await _proxy_call()
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == _NOT_RUNNING_COPY
