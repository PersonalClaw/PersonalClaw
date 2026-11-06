"""A sign-in's own Bearer can end that sign-in, with or without its pair.

``POST /api/auth/logout`` ends only the session that authorized the request, so letting a bound
sign-in's Bearer through without its pair there ends that one session and grants nothing. The
desktop app needs it when it holds a window's sign-in whose pair it no longer has. Everywhere else
the Bearer still needs its pair, and a logout carried by the cookie still does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_token

from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER

PORT = 10000
COOKIE = f"pc_token_{PORT}"
WRONG_PAIR = "W" * 43


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    for name in ("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "PERSONALCLAW_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    token_auth.use_ephemeral_secret(b"a-sign-ins-own-bearer")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _client() -> TestClient:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["allowed_origins"] = {f"http://127.0.0.1:{PORT}"}
    app.router.add_post("/api/auth/logout", auth_h.api_auth_logout)
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    app.router.add_post("/api/something/else", _ok)
    # Two other POSTs under /api/auth/: a waiver widened past the sign-out would open them.
    app.router.add_post("/api/auth/enroll/start", _ok)
    app.router.add_post("/api/auth/confirm", _ok)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _reason(resp: Any) -> str:
    return str(((await resp.json()).get("error") or {}).get("detail", {}).get("reason", ""))


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", ["", WRONG_PAIR], ids=["no pair", "a wrong pair"])
async def test_a_sign_ins_own_bearer_ends_it_and_is_then_refused(pair: str) -> None:
    client = await _client()
    try:
        token = browser_token("owner", 600)
        headers = {"Authorization": f"Bearer {token}", **({PAIR_HEADER: pair} if pair else {})}
        ended = await client.post("/api/auth/logout", headers=headers)
        assert ended.status == 200, await ended.text()
        assert (await ended.json())["revoked"] is True
        assert token_auth.token_nonce(token) not in session_store.load_session_records()
        after = await client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
        assert after.status == 403
        assert (await after.json())["error"]["code"] == "auth_bearer_invalid"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_everywhere_else_the_bearer_still_needs_its_pair() -> None:
    client = await _client()
    try:
        token = browser_token("owner", 600)
        bearer = {"Authorization": f"Bearer {token}"}
        read = await client.get("/api/auth/session", headers=bearer)
        assert read.status == 403 and await _reason(read) == "pair_missing"
        other = await client.post("/api/something/else", headers=bearer)
        assert other.status == 403 and await _reason(other) == "pair_missing"
        assert token_auth.token_nonce(token) in session_store.load_session_records()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/auth/enroll/start", "/api/auth/confirm"])
async def test_no_other_sign_in_route_waives_the_pair(path: str) -> None:
    client = await _client()
    try:
        token = browser_token("owner", 600)
        refused = await client.post(path, headers={"Authorization": f"Bearer {token}"})
        assert refused.status == 403 and await _reason(refused) == "pair_missing", path
        assert token_auth.token_nonce(token) in session_store.load_session_records()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_waived_sign_out_still_checks_its_origin() -> None:
    client = await _client()
    try:
        token = browser_token("owner", 600)
        refused = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://evil.example"},
        )
        assert refused.status == 403
        assert (await refused.json())["error"]["code"] == auth_h.ERR_ORIGIN
        assert token_auth.token_nonce(token) in session_store.load_session_records()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_logout_the_cookie_carries_still_needs_its_pair() -> None:
    client = await _client()
    try:
        token = browser_token("owner", 600)
        refused = await client.post("/api/auth/logout", headers={"Cookie": f"{COOKIE}={token}"})
        assert refused.status == 403 and await _reason(refused) == "pair_missing"
        assert token_auth.token_nonce(token) in session_store.load_session_records()
    finally:
        await client.close()
