"""Every way in holds a browser's sign-in to its pair, and only a sign-in hands one out.

Each of these runs the real handler or middleware branch:
- the dashboard's own page (``handlers.core.index``) carries a pair only on the answer that
  exchanged a link, never for a cookie alone;
- a bound sign-in's value on ``?token=`` signs nothing in, at the fetch exchange or on the socket;
- no door hands a pair to a token alone: the pairing page, pairing's start, enrolling's start;
- enrolling a device mints a bound sign-in and answers its pair;
- the sign-in and pairing pages keep the pair the gateway answered;
- the internal routes' cookie branches, from loopback without the secret and the mixed routes from
  elsewhere, ask a browser's cookie for its pair like every other route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_headers, browser_token, pair_of

from personalclaw.auth import enrollment
from personalclaw.dashboard import owner_presence, session_store, token_auth
from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import core as core_h
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.session_store import ISSUER_STARTUP
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, bound

PORT = 10000
COOKIE = f"pc_token_{PORT}"
ORIGIN = f"http://localhost:{PORT}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(enrollment, "config_dir", lambda: tmp_path)
    for name in ("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "PERSONALCLAW_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    token_auth.use_ephemeral_secret(b"every-way-in")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _client(app: web.Application) -> TestClient:
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _gated(**kwargs: Any) -> web.Application:
    return web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT, **kwargs)])


# ── (a) the real dashboard page ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_page_carries_a_pair_only_on_the_answer_that_exchanged_a_link(
    tmp_path, monkeypatch
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>PersonalClaw</title></head><body></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(core_h, "_DIST_DIR", dist)
    app = _gated()
    app.router.add_get("/", core_h.index)
    client = await _client(app)
    try:
        signed_in = browser_token("owner", 600)
        alone = await client.get("/", headers={"Cookie": f"{COOKIE}={signed_in}"})
        assert alone.status == 200, "the cookie alone opens the page"
        page = await alone.text()
        assert 'data-pair="' not in page and pair_of(signed_in) not in page
        assert PAIR_HEADER not in alone.headers and COOKIE not in alone.cookies
        link = token_auth.mint_session("owner", 3600, issuer=ISSUER_STARTUP).token
        client.session.cookie_jar.clear()
        opened = await client.get("/", params={"token": link})
        assert opened.status == 200, await opened.text()
        assert f'data-pair="{opened.headers[PAIR_HEADER]}"' in await opened.text()
    finally:
        await client.close()


# ── (b) a bound value on ?token= ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bound_value_on_the_query_signs_nothing_in_at_the_fetch_or_the_socket() -> None:
    app = _gated()
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    app.router.add_get("/api/ws", ws_mod.api_ws)
    client = await _client(app)
    try:
        value = browser_token("owner", 600)
        assert bound(value)
        fetched = await client.get("/api/auth/session", params={"token": value})
        assert fetched.status == 403
        assert (await fetched.json())["error"]["detail"]["reason"] == "not_signed_in"
        assert COOKIE not in fetched.cookies and PAIR_HEADER not in fetched.headers
        # The upgrade, sent as a plain GET so its whole answer can be read.
        upgrade = await client.get(
            "/api/ws",
            params={"token": value},
            headers={
                "Origin": ORIGIN,
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
        )
        assert upgrade.status == 403
        assert (await upgrade.json())["error"]["detail"]["reason"] == "not_signed_in"
        assert COOKIE not in upgrade.cookies and PAIR_HEADER not in upgrade.headers
    finally:
        await client.close()


# ── (c) no door hands a pair to a token alone ─────────────────────────────


@pytest.mark.asyncio
async def test_no_door_hands_a_pair_to_a_token_alone(monkeypatch) -> None:
    monkeypatch.setattr(owner_presence, "require_owner_presence", lambda *a, **k: None)
    monkeypatch.setattr(auth_h, "require_owner_presence", lambda *a, **k: None, raising=False)
    app = _gated()
    devices_h.register_device_routes(app)
    app.router.add_post("/api/auth/enroll/start", auth_h.api_auth_enroll_start)
    client = await _client(app)
    try:
        script = token_auth.generate_token("owner", 600)
        bearer = {"Authorization": f"Bearer {script}", "Origin": ORIGIN}
        for method, path in (
            ("GET", "/pair"),
            ("POST", "/api/devices/pair/start"),
            ("POST", "/api/auth/enroll/start"),
        ):
            resp = await client.request(method, path, headers=bearer, json={})
            assert resp.status == 200, (path, await resp.text())
            assert PAIR_HEADER not in resp.headers, path
            assert COOKIE not in resp.cookies, path
            if resp.content_type == "application/json":
                assert "pair" not in await resp.json(), path
            else:
                assert 'data-pair="' not in await resp.text(), path
    finally:
        await client.close()


# ── (d) enrolling a device ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrolling_a_device_mints_a_bound_sign_in_and_answers_its_pair() -> None:
    app = _gated()
    app.router.add_post("/api/auth/enroll/complete", auth_h.api_auth_enroll_complete)
    client = await _client(app)
    try:
        code, _exp = enrollment.issue_code()
        resp = await client.post(
            "/api/auth/enroll/complete", json={"code": code}, headers={"Origin": ORIGIN}
        )
        assert resp.status == 200, await resp.text()
        token = resp.cookies[COOKIE].value
        assert bound(token), "an enrolled device's sign-in works on its cookie alone"
        assert (await resp.json())["pair"] == pair_of(token)
        assert token not in await resp.text(), "the answer carries the cookie's value"
    finally:
        await client.close()


# ── (e) the pages keep what the gateway answered ──────────────────────────


@pytest.mark.parametrize(
    "script",
    [auth_h._LOGIN_SCRIPT, devices_h._PAIR_SCRIPT],
    ids=["the sign-in page", "the pairing page"],
)
def test_each_sign_in_page_keeps_the_pair_its_answer_carries(script: str) -> None:
    kept = script.count("PersonalClawOwnerToken.keepPair(res.data && res.data.pair);")
    sign_ins = (
        script.count("fetch('/api/auth/login'")
        + script.count("fetch('/api/auth/enroll/complete'")
        + script.count("fetch('/api/devices/pair/complete'")
    )
    assert sign_ins and kept == sign_ins, "a sign-in on this page drops the pair it was answered"


# ── (f) the internal routes' cookie branches ──────────────────────────────


async def _ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["loopback, no secret", "mixed, from elsewhere"])
async def test_the_internal_routes_cookie_branches_ask_for_the_pair(monkeypatch, branch) -> None:
    if branch == "mixed, from elsewhere":
        monkeypatch.setattr(token_auth, "is_loopback", lambda host: False)
    app = _gated(
        internal_routes=frozenset({"GET /api/internal/only"}),
        mixed_internal_routes=frozenset({"GET /api/internal/mixed"}),
        internal_secret="the-internal-secret",
    )
    app.router.add_get("/api/internal/only", _ok)
    app.router.add_get("/api/internal/mixed", _ok)
    client = await _client(app)
    try:
        token = browser_token("owner", 600)
        path = "/api/internal/only" if branch.startswith("loopback") else "/api/internal/mixed"
        alone = await client.get(path, headers={"Cookie": f"{COOKIE}={token}"})
        assert alone.status == 403, f"{branch}: the cookie alone was admitted"
        assert (await alone.json())["error"]["detail"]["reason"] == "pair_missing"
        paired = await client.get(path, headers=browser_headers(token, port=PORT))
        assert paired.status == 200, f"{branch}: {await paired.text()}"
    finally:
        await client.close()
