"""A browser's sign-in cookie is not enough on its own: every request on it carries its pair.

A browser's session is bound (``dashboard/sign_in_pair.py``): its pair is handed to its page as it
signs in, kept in the page's own storage, and sent as ``X-PersonalClaw-Pair`` (or, on an upgrade, as
the ``pc-pair.<pair>`` subprotocol). Its cookie's value without the pair opens nothing: not as the
cookie, as a Bearer, as a link, or on the WebSocket upgrade. These drive the REAL token middleware
over a real ``TestServer``, with the real ``/api/sessions``, ``/api/ws`` and sign-in routes behind
it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_token, pair_of, shell
from shared_app_rows import recording_sel

from personalclaw.auth import credentials as creds
from personalclaw.dashboard import fallbacks, session_store, token_auth
from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import sessions as sessions_h
from personalclaw.dashboard.origin import build_allowed_origins
from personalclaw.dashboard.server import _security_headers_middleware
from personalclaw.dashboard.session_store import ISSUER_STARTUP
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, bound
from personalclaw.history import ConversationLog

PORT = 10000
COOKIE = f"pc_token_{PORT}"
#: The gateway's own origin, which the WebSocket's origin check admits, so a refusal of the
#: upgrade below can only be the sign-in's, never the origin's.
OWN_ORIGIN = f"http://127.0.0.1:{PORT}"
OWNERS_CHAT = "something only the owner should read"
#: What a browser sends, so the sign-in a link makes is a browser's, as it is in use.
CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
#: One unmatched path in every family of routes ``sign_in_pair._ROUTE_FAMILIES`` names.
FAMILY_PATHS = {
    "api": "/api/no-such-route",
    "ws": "/ws/terminal/1",
    "assets": "/assets/no-such.js",
    "icons": "/icons/no-such.png",
    "sprites": "/sprites/no-such.svg",
    "vendor": "/vendor/no-such.js",
    "fonts": "/fonts/no-such.woff2",
    "v1": "/v1/no-such-route",
    "a2a": "/a2a/no-such-route",
    "capture": "/capture/no-such-route",
    "mcp": "/mcp/x",
    "artifacts/serve": "/artifacts/serve/no-such",
    "apps/*/api": "/apps/notes/api/no-such-route",
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Sessions, the signing key and the chats live under *tmp_path*; no auth-skipping env."""
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    # 🪤 The local-network bypass admits every request without looking at a credential, and the
    # file would then read as a pass for a middleware it never exercised.
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"a-browser-sign-in-needs-its-pair")
    token_auth.revoke_all_sessions()
    assert session_store.sessions_path().parent == tmp_path, "the store still points at the home"
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _built(request: web.Request) -> web.Response:
    """A data-less build file, standing in for the worker, the manifest, an icon or a notice."""
    return web.Response(text="build output")


async def _app_backend(request: web.Request) -> web.Response:
    return web.json_response({"app": "data"})


async def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The shipped middleware with the shipped `/api/sessions`, `/api/ws` and sign-in routes
    behind it, over one chat the session list would show, and the files a browser loads by
    itself. Any page path no route matches is the shell, as ``fallbacks.spa_fallback`` serves it."""
    monkeypatch.setattr(fallbacks.handlers, "index", shell)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.append("dashboard_a-chat", "user", OWNERS_CHAT)
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(sessions=mock.MagicMock(count=0), start_time=0.0, conversation_log=log)
    state._sessions = {}
    app = web.Application(
        middlewares=[
            _security_headers_middleware,
            token_auth.token_auth_middleware(port=PORT),
            fallbacks.spa_fallback,
        ]
    )
    app["state"] = state
    app["port"] = PORT
    app["allowed_origins"] = build_allowed_origins(PORT, True) | {"http://localhost:10000"}
    app.router.add_get("/", shell)
    for path in (
        "/sw.js",
        "/manifest.webmanifest",
        "/icons/icon-192.png",
        "/THIRD_PARTY_NOTICES.txt",
        "/THIRD_PARTY_NOTICES_NPM.txt",
        "/apps/notes/ui/index.html",
    ):
        app.router.add_get(path, _built)
    app.router.add_get("/apps/notes/api/items", _app_backend)
    app.router.add_get("/api/sessions", sessions_h.api_sessions)
    app.router.add_get("/api/ws", ws_mod.api_ws)
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _signed_in(client: TestClient) -> tuple[str, str]:
    """Sign a browser in through the entry link the gateway opens, and return what its cookie was
    set to and the pair its page kept. The client's own jar is emptied, so every request after
    this carries only what the caller puts on it."""
    link = token_auth.mint_session("owner", 3600, issuer=ISSUER_STARTUP).token
    entered = await client.get("/", params={"token": link}, headers={"User-Agent": CHROME})
    assert entered.status == 200, await entered.text()
    value = entered.cookies[COOKIE].value
    assert value and value != link, "the link itself became the browser's cookie"
    assert f'data-pair="{entered.headers[PAIR_HEADER]}"' in await entered.text()
    client.session.cookie_jar.clear()
    return value, entered.headers[PAIR_HEADER]


def _signed_out(response: Any) -> None:
    """A refusal every client reads as signed out: the page's sign-in is over."""
    assert response.status == 403, f"admitted with {response.status}"
    assert response.headers.get("X-Auth-Required") == "true"


async def _asked_again(response: Any, reason: str) -> None:
    """A refusal that ends nothing: no `X-Auth-Required`, so the page keeps what it holds."""
    assert response.status == 403, f"admitted with {response.status}"
    assert "X-Auth-Required" not in response.headers
    error = (await response.json())["error"]
    assert (error["code"], error["detail"]["reason"]) == ("session_required", reason), error
    assert "pair" not in error["message"].lower(), "the copy says pair, which means pairing"


# ── the cookie's value alone, on every carrier ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cookies_value_replayed_as_a_bearer_is_refused(tmp_path, monkeypatch) -> None:
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        replayed = await client.get("/api/sessions", headers={"Authorization": f"Bearer {value}"})
        await _asked_again(replayed, "pair_missing")
        assert OWNERS_CHAT not in await replayed.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_cookie_alone_does_not_open_the_session_list(tmp_path, monkeypatch) -> None:
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        replayed = await client.get("/api/sessions", headers={"Cookie": f"{COOKIE}={value}"})
        await _asked_again(replayed, "pair_missing")
        assert "dashboard_a-chat" not in await replayed.text()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [OWN_ORIGIN, "http://127.0.0.1:8123"])
async def test_the_cookie_alone_does_not_open_the_websocket(tmp_path, monkeypatch, origin) -> None:
    """The live socket's upgrade, from either of the origins the test admits: the refusal is the
    sign-in's, never the origin check's."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        with pytest.raises(WSServerHandshakeError) as refused:
            ws = await client.ws_connect(
                "/api/ws", headers={"Cookie": f"{COOKIE}={value}", "Origin": origin}
            )
            await ws.close()
        assert refused.value.status == 403
        # The same upgrade read as a plain request says which refusal it is.
        upgrade = {
            "Cookie": f"{COOKIE}={value}",
            "Origin": origin,
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }
        await _asked_again(await client.get("/api/ws", headers=upgrade), "pair_missing")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_cookies_value_as_a_link_signs_nothing_in(tmp_path, monkeypatch) -> None:
    """A browser's sign-in is never a link: refused on `?token=` before anything is exchanged,
    with the sentence anything this gateway can't use gets."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        for path in ("/", "/api/sessions"):
            opened = await client.get(path, params={"token": value})
            assert opened.status == 403, path
            assert COOKIE not in opened.cookies, path
        # Refused as a sign-in, never judged as a link whose window has passed.
        error = (await opened.json())["error"]
        assert error["detail"]["reason"] == "not_signed_in", error
        assert bound(value)
    finally:
        await client.close()


# ── every carrier, with the right pair, none, another session's, and a malformed one ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["cookie", "bearer"])
async def test_each_carrier_needs_its_own_pair(tmp_path, monkeypatch, carrier) -> None:
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, pair = await _signed_in(client)
        other = pair_of(browser_token("owner", 600))
        sent = (
            {"Cookie": f"{COOKIE}={value}"}
            if carrier == "cookie"
            else {"Authorization": f"Bearer {value}"}
        )
        right = await client.get("/api/sessions", headers={**sent, PAIR_HEADER: pair})
        assert right.status == 200, await right.text()
        assert "dashboard_a-chat" in await right.text()
        await _asked_again(await client.get("/api/sessions", headers=sent), "pair_missing")
        for wrong in (other, "x" * 43, "not-a-pair"):
            refused = await client.get("/api/sessions", headers={**sent, PAIR_HEADER: wrong})
            _signed_out(refused)
            assert (await refused.json())["error"]["detail"]["reason"] == "pair_wrong"
        again = await client.get("/api/sessions", headers={**sent, PAIR_HEADER: pair})
        assert again.status == 200, "a wrong pair ended the session it was sent beside"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_socket_carries_its_pair_as_a_subprotocol(tmp_path, monkeypatch) -> None:
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, pair = await _signed_in(client)
        headers = {"Cookie": f"{COOKIE}={value}", "Origin": OWN_ORIGIN}
        sock = await client.ws_connect(
            "/api/ws", headers=headers, protocols=("personalclaw", f"pc-pair.{pair}")
        )
        assert sock.protocol == "personalclaw"
        await sock.close()
        other = pair_of(browser_token("owner", 600))
        with pytest.raises(WSServerHandshakeError) as refused:
            ws = await client.ws_connect(
                "/api/ws", headers=headers, protocols=("personalclaw", f"pc-pair.{other}")
            )
            await ws.close()
        assert refused.value.status == 403
        assert refused.value.headers is not None
        assert refused.value.headers.get("X-Auth-Required") == "true"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_upgrade_carrying_the_header_is_judged_by_the_header(
    tmp_path, monkeypatch
) -> None:
    """The desktop app sends the pair it holds as the header on its socket's upgrade, and leaves
    the page's offered subprotocol in place. The header is read first: the right pair there
    admits the upgrade whatever the subprotocol offers, and a wrong one refuses it, even beside the
    right subprotocol."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, pair = await _signed_in(client)
        other = pair_of(browser_token("owner", 600))
        right_header = {"Cookie": f"{COOKIE}={value}", "Origin": OWN_ORIGIN, PAIR_HEADER: pair}
        sock = await client.ws_connect(
            "/api/ws", headers=right_header, protocols=("personalclaw", f"pc-pair.{other}")
        )
        assert sock.protocol == "personalclaw"
        await sock.close()
        wrong_header = {**right_header, PAIR_HEADER: other}
        with pytest.raises(WSServerHandshakeError) as refused:
            ws = await client.ws_connect(
                "/api/ws", headers=wrong_header, protocols=("personalclaw", f"pc-pair.{pair}")
            )
            await ws.close()
        assert refused.value.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_cookie_holding_a_session_that_is_not_bound_is_refused(
    tmp_path, monkeypatch
) -> None:
    """A `personalclaw token` session is a Bearer's: set as a browser's cookie, it signs the page
    out. As the Bearer it is, it still works."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        script = token_auth.generate_token("owner", ttl_seconds=600)
        as_cookie = await client.get("/api/sessions", headers={"Cookie": f"{COOKIE}={script}"})
        _signed_out(as_cookie)
        assert (await as_cookie.json())["error"]["detail"]["reason"] == "unbound_cookie"
        as_bearer = await client.get("/api/sessions", headers={"Authorization": f"Bearer {script}"})
        assert as_bearer.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_navigation_to_an_api_route_without_its_pair_is_told_to_open_it_again(
    tmp_path, monkeypatch
) -> None:
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        opened = await client.get(
            "/api/sessions",
            headers={"Cookie": f"{COOKIE}={value}", "Sec-Fetch-Mode": "navigate"},
        )
        await _asked_again(opened, "navigation_unpaired")
        assert "Open it again from PersonalClaw" in (await opened.json())["error"]["message"]
    finally:
        await client.close()


# ── the files a browser loads by itself need only the cookie ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_cookie_alone_opens_exactly_the_files_a_browser_loads_by_itself(
    tmp_path, monkeypatch
) -> None:
    """The shell (``/`` and any page path the web app's router owns), the worker, the manifest,
    the icons, the notices and an app's static page files: data-less build output, still behind
    a valid cookie. Every other route, an app's backend included, needs the pair."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        cookie = {"Cookie": f"{COOKIE}={value}"}
        for path in (
            "/",
            "/chat/a-chat",
            "/sw.js",
            "/manifest.webmanifest",
            "/icons/icon-192.png",
            "/THIRD_PARTY_NOTICES.txt",
            "/THIRD_PARTY_NOTICES_NPM.txt",
            "/apps/notes/ui/index.html",
        ):
            assert (await client.get(path, headers=cookie)).status == 200, path
            assert (await client.get(path)).status in (302, 403), f"{path} opened with nothing"
        for path in ("/api/sessions", "/apps/notes/api/items"):
            assert (await client.get(path, headers=cookie)).status == 403, path
        assert (await client.post("/", headers=cookie)).status in (403, 405)
    finally:
        await client.close()


def test_every_route_family_has_a_path_below() -> None:
    from personalclaw.dashboard.sign_in_pair import _ROUTE_FAMILIES

    named = re.findall(r"\(\?:([^)]*)\)", _ROUTE_FAMILIES.pattern)[0].split("|")
    assert set(named) | {"apps/*/api"} == set(FAMILY_PATHS), "a route family has no path here"


@pytest.mark.asyncio
@pytest.mark.parametrize("family", list(FAMILY_PATHS), ids=list(FAMILY_PATHS))
async def test_an_unmatched_path_a_route_family_owns_is_never_the_shell(
    tmp_path, monkeypatch, family
) -> None:
    """Only a page path is the shell on the cookie alone. An unmatched path in a family of routes
    gets that family's answer, never the shell, and outside the build files the cookie alone may
    open, the pair refusal. The shell the cookie alone opens carries no cookie of its own."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        value, _pair = await _signed_in(client)
        cookie = {"Cookie": f"{COOKIE}={value}"}
        refused = await client.get(FAMILY_PATHS[family], headers=cookie)
        assert "the dashboard" not in await refused.text(), f"{family} answered the shell"
        if family in ("assets", "sprites", "vendor", "fonts", "icons"):
            # Build files, open as they are: a missing one is a 404.
            assert refused.status == 404, family
        else:
            await _asked_again(refused, "pair_missing")
        shell = await client.get("/chat/a-chat", headers=cookie)
        assert shell.status == 200
        assert "Set-Cookie" not in shell.headers
        assert PAIR_HEADER not in shell.headers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_no_answer_hands_a_pair_to_a_bearer_alone(tmp_path, monkeypatch) -> None:
    """A Bearer with no pair learns only that its token is live (`pair_missing`): no route mints
    or reveals a pair for it. A script's token reads the session view and gets no pair and no
    cookie; a browser's token without its pair is refused before any route."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        script = token_auth.generate_token("owner", ttl_seconds=600)
        read = await client.get("/api/auth/session", headers={"Authorization": f"Bearer {script}"})
        assert read.status == 200
        assert PAIR_HEADER not in read.headers and "Set-Cookie" not in read.headers
        assert "pair" not in await read.text()
        value, _pair = await _signed_in(client)
        replayed = await client.get(
            "/api/auth/session", headers={"Authorization": f"Bearer {value}"}
        )
        await _asked_again(replayed, "pair_missing")
        assert PAIR_HEADER not in replayed.headers
    finally:
        await client.close()


# ── every door a browser signs in through answers its pair ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_password_sign_in_answers_its_pair_and_never_puts_it_in_the_cookie(
    tmp_path, monkeypatch
) -> None:
    creds.set_password("jordan", "a long enough password 1")
    (tmp_path / "config.json").write_text('{"auth": {"login_enabled": true}}', encoding="utf-8")
    client = await _gateway(tmp_path, monkeypatch)
    try:
        signed = await client.post(
            "/api/auth/login",
            json={"username": "jordan", "password": "a long enough password 1"},
            headers={"Origin": "http://localhost:10000"},
        )
        assert signed.status == 200, await signed.text()
        cookie = signed.cookies[COOKIE].value
        pair = (await signed.json())["pair"]
        assert bound(cookie), "a password sign-in minted a session that isn't bound"
        assert pair == pair_of(cookie)
        assert pair not in cookie
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_fetch_exchange_hands_the_page_its_pair(tmp_path, monkeypatch) -> None:
    """``freshSignIn.withLink``: a link exchanged by the dashboard's own fetch answers the pair in
    its header, and the page signed in by it reads its sessions with it."""
    client = await _gateway(tmp_path, monkeypatch)
    try:
        link = token_auth.generate_token("owner", ttl_seconds=600)
        entered = await client.get("/api/auth/session", params={"token": link})
        assert entered.status == 200, await entered.text()
        cookie, pair = entered.cookies[COOKIE].value, entered.headers[PAIR_HEADER]
        assert entered.headers["Cache-Control"] == "no-store"
        client.session.cookie_jar.clear()
        listed = await client.get(
            "/api/sessions", headers={"Cookie": f"{COOKIE}={cookie}", PAIR_HEADER: pair}
        )
        assert listed.status == 200
    finally:
        await client.close()


# ── the pair is in no log line and no security event ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_log_line_or_security_event_ever_holds_the_pair(
    tmp_path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = MagicMock()
    client = await _gateway(tmp_path, monkeypatch)
    try:
        with recording_sel(recorder), caplog.at_level(logging.DEBUG):
            value, pair = await _signed_in(client)
            sent = {"Cookie": f"{COOKIE}={value}"}
            await client.get("/api/sessions", headers=sent)
            await client.get("/api/sessions", headers={**sent, PAIR_HEADER: "y" * 43})
            await client.get("/api/sessions", headers={**sent, PAIR_HEADER: pair})
        rows = [repr(call) for call in recorder.mock_calls]
        assert any("session_pair_refused" in row for row in rows), rows
        assert all(pair not in row and "y" * 43 not in row for row in rows)
        assert all(pair not in record.getMessage() for record in caplog.records)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_fallback_answers_no_build_prefix_with_the_shell(monkeypatch) -> None:
    """Every static prefix the sign-in leaves open is one the page fallback never answers with the
    shell, so the shell never opens without a sign-in under one of them."""
    monkeypatch.setattr(fallbacks.handlers, "index", shell)
    app = web.Application(middlewares=[fallbacks.spa_fallback])
    async with TestClient(TestServer(app)) as client:
        for prefix in token_auth._BYPASS_PREFIXES:
            missing = await client.get(f"{prefix}no-such-file")
            assert missing.status == 404, f"{prefix} falls back to the shell"
        assert "the dashboard" in await (await client.get("/chat/a-page")).text()
