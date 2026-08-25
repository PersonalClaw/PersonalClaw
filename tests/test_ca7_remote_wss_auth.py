"""A native client's device session over `wss://` (COMPANION-APPS T3.2 / `CA-7`).

**What was measured before any of this was written**, because the task row's verb is *verify*
and the verification failed:

* A native client — a desktop or mobile shell that opens the socket itself instead of loading
  the SPA into a WebView — sends **no `Origin` header**, because it has no document. Against a
  non-loopback peer `check_origin(require=True)` answered *False*, so `/api/ws` refused it with
  `403 WebSocket origin not allowed`. The old docstring said so out loud: *"including missing
  Origin (non-browser clients are not expected)"*.
* That refusal protected nothing, and this suite pins the reason: any caller that controls its
  own headers walks past it by sending `Origin: http://localhost:{port}`, which is in
  `build_allowed_origins` unconditionally. The rule only ever constrained clients that
  *cannot* choose their headers — i.e. honest ones.

So the admission is keyed on something a header cannot forge: a session the owner deliberately
**paired**. Every test here asserts the CALL SITE — a real handshake against the real
`api_ws` route behind the real token middleware — not the predicate in isolation, and every
guard is shown to be able to fail (a guard that cannot fail is not pinning anything).

**Fail-closed is the default and it is asserted**, not assumed: no nonce, an unknown nonce, a
non-device session and an unreadable registry each refuse.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest
from aiohttp import ClientSession, WSServerHandshakeError, web
from aiohttp.test_utils import TestServer

from personalclaw.dashboard import origin as origin_mod
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.origin import build_allowed_origins

PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Both `config_dir` bindings, and a proof the redirect actually took.

    `session_store` does `from personalclaw.config.loader import config_dir` at import time, so
    patching only the loader would leave the store writing to the REAL home. The assertion below
    is the point of the fixture: without it a silent miss looks exactly like a passing test.
    """
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    assert ss.sessions_path().parent == tmp_path, "session store still points at the real home"
    yield tmp_path
    token_auth.revoke_all_sessions()


# ── the app under test: the real route, the real middleware ──────────────────────────────────


def _app(allowed: set[str] | None = None) -> web.Application:
    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT, local_only=False)]
    )
    app["allowed_origins"] = allowed if allowed is not None else build_allowed_origins(PORT, False)
    state = mock.MagicMock()
    state._sessions = {}
    state.is_yolo_active.return_value = False
    app["state"] = state
    app.router.add_get("/api/ws", ws_mod.api_ws)
    return app


def _paired_token(*, device: bool = True) -> str:
    """A real signed token whose session row optionally carries a paired device."""
    token = token_auth.generate_token("owner")
    if device:
        nonce = token_auth.token_nonce(token)
        assert nonce, "the minted token must carry a nonce for the test to mean anything"
        assert ss.attach_device(nonce, ss.DeviceInfo(id="dev-1", name="Phone", kind="mobile"))
    return token


async def _upgrade(app: web.Application, token: str, *, origin: str | None) -> int:
    """Attempt a real `/api/ws` handshake. Returns the HTTP status (101 on success).

    `is_loopback` is forced False for the duration so the peer looks like a client arriving
    through the owner's tunnel rather than the test server's own 127.0.0.1. It is patched on
    `origin` only — `token_auth` bound the name at import, so authentication is untouched.
    """
    server = TestServer(app)
    await server.start_server()
    try:
        headers = {} if origin is None else {"Origin": origin}
        url = server.make_url(f"/api/ws?token={token}")
        with mock.patch.object(origin_mod, "is_loopback", return_value=False):
            async with ClientSession() as sess:
                try:
                    async with sess.ws_connect(url, headers=headers) as sock:
                        await sock.close()
                        return 101
                except WSServerHandshakeError as exc:
                    return int(exc.status)
    finally:
        await server.close()


# ── the admission, at its call site ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_native_client_with_a_device_session_completes_the_upgrade() -> None:
    """The atom's headline: no Origin, remote peer, paired device session → the socket opens."""
    assert await _upgrade(_app(), _paired_token(), origin=None) == 101


@pytest.mark.asyncio
async def test_an_ordinary_session_without_an_origin_is_still_refused() -> None:
    """VACUITY for the device guard: same request, same absent Origin, no device row → 403.

    If this passed, the suite above would be measuring nothing but "no Origin is fine now".
    """
    assert await _upgrade(_app(), _paired_token(device=False), origin=None) == 403


@pytest.mark.asyncio
async def test_a_disallowed_origin_is_refused_even_with_a_device_session() -> None:
    """VACUITY for the origin-absence guard: a device session buys no help forging an origin."""
    assert await _upgrade(_app(), _paired_token(), origin="https://evil.test") == 403


@pytest.mark.asyncio
async def test_the_public_origin_is_refused_with_a_device_session_too() -> None:
    """The WebView gap is deliberately NOT closed here — see the plan's execution log.

    `dashboard.public_url` puts `wss://host` in the CSP but nothing puts `https://host` in the
    allowlist, so a WebView loading the SPA over the tunnel is still refused. Pinning it keeps
    the next reader from assuming this atom fixed it.
    """
    assert await _upgrade(_app(), _paired_token(), origin="https://pc.example.com") == 403


@pytest.mark.asyncio
async def test_an_allowed_origin_still_works_unchanged() -> None:
    """The browser path is byte-identical — the whole point of gating on Origin ABSENCE."""
    assert (
        await _upgrade(_app(), _paired_token(device=False), origin="http://localhost:10000") == 101
    )


@pytest.mark.asyncio
async def test_the_refusal_it_replaces_was_bypassable_by_forging_an_origin() -> None:
    """Why relaxing this removes no protection, as a measurement rather than an argument.

    A caller with NO device session — refused when honest about having no origin — is admitted
    the moment it lies and claims the loopback origin. That is the same non-browser client in
    both halves; only the header changed.
    """
    honest = await _upgrade(_app(), _paired_token(device=False), origin=None)
    lying = await _upgrade(_app(), _paired_token(device=False), origin=f"http://localhost:{PORT}")
    assert (honest, lying) == (403, 101)


# ── fail-closed on every unknown ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreadable_device_registry_fails_closed() -> None:
    with mock.patch.object(ss, "device_sessions", side_effect=OSError("boom")):
        assert await _upgrade(_app(), _paired_token(), origin=None) == 403


@pytest.mark.asyncio
async def test_a_session_nonce_the_registry_does_not_know_fails_closed() -> None:
    with mock.patch.object(ss, "device_sessions", return_value={"someone-else": object()}):
        assert await _upgrade(_app(), _paired_token(), origin=None) == 403


def test_the_predicate_refuses_when_no_middleware_published_a_nonce() -> None:
    """No `session_nonce` on the request at all — the shape an unauthenticated path would have."""
    request = mock.MagicMock()
    request.get.return_value = None
    assert ws_mod._paired_device_session(request) == ""


@pytest.mark.parametrize("nonce", ["", None, 123, b"bytes"])
def test_the_predicate_refuses_a_nonce_that_is_not_a_non_empty_string(nonce: Any) -> None:
    request = mock.MagicMock()
    request.get.return_value = nonce
    assert ws_mod._paired_device_session(request) == ""


# ── the nonce plumbing (the new middleware call site) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_token_middleware_publishes_the_session_nonce() -> None:
    """The mechanism above is inert unless something SETS `session_nonce`. This is that writer."""
    seen: dict[str, Any] = {}

    async def spy(request: web.Request) -> web.Response:
        seen["nonce"] = request.get("session_nonce")
        seen["user"] = request.get("user")
        return web.Response(text="ok")

    app = web.Application(
        middlewares=[token_auth.token_auth_middleware(port=PORT, local_only=False)]
    )
    app["allowed_origins"] = build_allowed_origins(PORT, False)
    app.router.add_get("/api/probe", spy)
    token = token_auth.generate_token("owner")

    server = TestServer(app)
    await server.start_server()
    try:
        async with ClientSession() as sess:
            async with sess.get(server.make_url(f"/api/probe?token={token}")) as resp:
                assert resp.status == 200
    finally:
        await server.close()

    assert seen["user"] == "owner"
    assert seen["nonce"] == token_auth.token_nonce(token)
    assert seen["nonce"], "an empty nonce would make every session unidentifiable"


def test_token_nonce_reads_the_claim() -> None:
    token = token_auth.generate_token("owner")
    payload = json.loads(token_auth._b64url_decode(token.split(".")[0]))
    assert token_auth.token_nonce(token) == payload["nonce"]


@pytest.mark.parametrize(
    "bad", ["", "no-dot", "!!!.sig", "e30.sig", "W10.sig"]  # e30 == {}, W10 == []
)
def test_token_nonce_fails_closed_on_anything_unreadable(bad: str) -> None:
    assert token_auth.token_nonce(bad) == ""


# ── "no new origin exemption" (the atom's own clause) ────────────────────────────────────────


def test_the_allowed_origin_set_is_byte_identical(monkeypatch) -> None:
    """The clause is structural, so assert it structurally: nothing was added to the allowlist.

    Pinned as a LITERAL set — not derived from the function's own output, which would pin
    nothing — so that ANY future widening, whatever host it names, has to come here and say so.
    The two environment-driven entries (`PERSONALCLAW_HOME` → `:3000`, `PERSONALCLAW_CORS_ORIGINS`)
    and the machine hostname (`local_only=False`) are excluded by construction so the literal is
    the same on a laptop and in CI.
    """
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.delenv("PERSONALCLAW_CORS_ORIGINS", raising=False)
    assert build_allowed_origins(PORT, local_only=True, configured_host="") == {
        f"http://127.0.0.1:{PORT}",
        f"http://localhost:{PORT}",
        f"http://personalclaw.localhost:{PORT}",
    }


def test_a_paired_device_does_not_widen_the_allowlist() -> None:
    baseline = build_allowed_origins(PORT, local_only=False)
    token = _paired_token()
    assert token_auth.token_nonce(token) in ss.device_sessions()
    assert build_allowed_origins(PORT, local_only=False) == baseline
