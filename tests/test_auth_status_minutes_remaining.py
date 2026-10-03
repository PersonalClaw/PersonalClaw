"""`GET /api/auth-status` says how long this browser's sign-in has left.

`minutes_remaining` read `request["token_state"].session_exp`, and nothing ever set
`token_state`: measured across `src/`, the handler was the only place the key appeared. So the
field was omitted on every response, and the status card's "29d left" beside the sign-in row never
rendered for anyone. The token middleware now records the session's end on the request
(`session_expires_at`), and the handler reports it.

Driven through the real middleware with a real minted session, because the defect lived between
the two: each half was fine on its own.
"""

from __future__ import annotations

import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth.modes import AuthConfig, AuthMode
from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers_system import api_auth_status

PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Sessions and the signing key under *tmp_path*; neither blanket bypass set."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"auth-status-minutes-key-000001")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _status(headers: dict[str, str], mode: AuthMode = AuthMode.LOCAL_TOKEN) -> dict:
    cfg = AuthConfig(mode=mode)
    app = web.Application(middlewares=[token_auth.auth_middleware(cfg, port=PORT)])
    app["auth_cfg"] = cfg
    app.router.add_get("/api/auth-status", api_auth_status)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/auth-status", headers=headers)
        assert resp.status == 200, await resp.text()
        return await resp.json()


@pytest.mark.parametrize("carrier", ["cookie", "header"])
@pytest.mark.asyncio
async def test_a_signed_in_browser_is_told_how_long_its_sign_in_has_left(carrier):
    """🔴 Red on main: `minutes_remaining` was absent for every request."""
    three_days = 3 * 86400
    minted = token_auth.mint_session("owner", three_days, issuer=token_auth.ISSUER_LOGIN)
    headers = (
        {"Cookie": f"pc_token_{PORT}={minted.token}"}
        if carrier == "cookie"
        else {"Authorization": f"Bearer {minted.token}"}
    )
    body = await _status(headers)
    assert "minutes_remaining" in body, body
    expected = (minted.expires_at - time.time()) / 60
    assert abs(body["minutes_remaining"] - expected) <= 1, (body, expected)
    # And it is THIS session's end, not the 90-day limit or some other constant.
    assert body["minutes_remaining"] < 3 * 24 * 60 + 1


@pytest.mark.asyncio
async def test_an_old_long_token_reports_the_end_validation_enforces():
    """The number is the end the gateway ENFORCES (`_session_deadline`): a token signed before the
    90-day limit existed, with a year on its `session_exp`, ends 90 days after it was issued — so
    that is what the browser is told, not the year it carries."""
    import json

    now = time.time()
    nonce = "a1b2c3d4e5f60718"
    token_auth._state.register_nonce(nonce, now + 365 * 86400)
    payload = json.dumps(
        {
            "sub": "owner",
            "exp": now + 3600,
            "session_exp": now + 365 * 86400,
            "iat": now,
            "nonce": nonce,
        },
        separators=(",", ":"),
    ).encode()
    token = f"{token_auth._b64url_encode(payload)}.{token_auth._sign(payload)}"
    body = await _status({"Authorization": f"Bearer {token}"})
    assert abs(body["minutes_remaining"] - 90 * 24 * 60) <= 1, body


@pytest.mark.asyncio
async def test_no_sign_in_means_no_remaining_minutes():
    """`none` mode has no session to end, so the field is omitted rather than invented."""
    body = await _status({}, mode=AuthMode.NONE)
    assert body["mode"] == "none"
    assert "minutes_remaining" not in body, body
