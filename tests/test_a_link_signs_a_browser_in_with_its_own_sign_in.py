"""Opening a sign-in link signs the browser in with a sign-in of its own (``link_exchange``).

The link itself never becomes the browser's cookie. Where the dashboard exchanges it (a shell
navigation, or its own fetch of ``/api/auth/session``), the gateway mints the browser a bound
sign-in that lasts what is left of the link, hands the page its pair, and moves the browser's
pushes onto it. A startup link signs exactly one browser in and ends; a ``personalclaw token`` or
ready-line link stays the token it is. Anywhere else, a link authorizes that one request.

Driven over the real token middleware and the real sign-in routes on a real ``TestServer``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from browser_sign_in import pair_of, shell

from personalclaw.dashboard import owner_presence, session_store, token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.session_store import (
    END_LINK_OPENED,
    END_REASONS,
    ISSUER_LOGIN,
    ISSUER_READY,
    ISSUER_STARTUP,
    ISSUER_TOKEN,
    ISSUER_WINDOW,
)
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, bound

PORT = 10000
COOKIE = f"pc_token_{PORT}"
CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:131.0) Gecko/20100101 Firefox/131.0"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"a-link-signs-a-browser-in")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _probe(request: web.Request) -> web.Response:
    return web.json_response({"session": request.get("session_nonce", "")})


async def _client() -> TestClient:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app.router.add_get("/", shell)
    app.router.add_get("/api/probe", _probe)
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _link(issuer: str = ISSUER_STARTUP, ttl: int = 3600, **kw: Any) -> token_auth.MintedSession:
    return token_auth.mint_session("owner", ttl, issuer=issuer, **kw)


async def _open(client: TestClient, link: str, *, agent: str = CHROME, **headers: str) -> Any:
    return await client.get("/", params={"token": link}, headers={"User-Agent": agent, **headers})


def _records() -> dict[str, Any]:
    return session_store.load_session_records()


@pytest.mark.asyncio
async def test_a_startup_link_signs_one_browser_in_and_ends() -> None:
    """One exchange leaves one live session: the browser's own. The link, presented again as a
    Bearer, is refused, and a second browser opening it is told it already signed one in."""
    client = await _client()
    try:
        link = _link()
        first = await _open(client, link.token)
        assert first.status == 200, await first.text()
        cookie = first.cookies[COOKIE].value
        assert cookie != link.token and bound(cookie)
        assert first.headers[PAIR_HEADER] == pair_of(cookie)
        assert f'data-pair="{pair_of(cookie)}"' in await first.text()
        assert link.nonce not in _records(), "the link's own session outlived its exchange"
        assert session_store.ended_session(link.nonce).reason == END_LINK_OPENED
        as_bearer = await client.get(
            "/api/probe", headers={"Authorization": f"Bearer {link.token}"}
        )
        assert as_bearer.status == 403

        client.session.cookie_jar.clear()
        second = await _open(client, link.token, agent=FIREFOX)
        assert second.status == 403
        assert COOKIE not in second.cookies
        page = await second.text()
        assert "already signed a browser in" in page and "personalclaw token" in page
        api = await client.get("/api/probe", params={"token": link.token})
        assert (await api.json())["error"]["detail"]["reason"] == "link_opened"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("issuer", [ISSUER_TOKEN, ISSUER_READY])
async def test_a_token_or_ready_link_stays_a_token_beside_the_browser(issuer: str) -> None:
    """The browser counts once, as a row of its own, and the token is still a Bearer."""
    client = await _client()
    try:
        link = _link(issuer)
        opened = await _open(client, link.token)
        assert opened.status == 200
        browser = token_auth.token_nonce(opened.cookies[COOKIE].value)
        rows = _records()
        assert rows[browser].device.kind == "browser"
        assert session_store.pool_of(rows[link.nonce]) == session_store.POOL_TOKEN
        assert rows[browser].issuer == issuer, "the browser's row names the door it came through"
        bearer = await client.get("/api/probe", headers={"Authorization": f"Bearer {link.token}"})
        assert bearer.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_browsers_sign_in_lasts_what_is_left_of_the_link() -> None:
    """A channel's one-hour link gives a one-hour sign-in, never `auth.session_ttl`'s 30 days, and
    keeps the link's sign-in time, so the presence clock runs from it."""
    client = await _client()
    try:
        link = _link(ISSUER_TOKEN, ttl=3600)
        minted_at = time.time() - 1200
        records = _records()
        records[link.nonce].device.minted_at = minted_at
        session_store.save_session_records(records)
        opened = await _open(client, link.token)
        cookie = opened.cookies[COOKIE]
        row = _records()[token_auth.token_nonce(cookie.value)]
        assert row.expiry == pytest.approx(link.expires_at, abs=2)
        assert int(cookie["max-age"]) <= 3600 + token_auth.SIGNED_OUT_NOTICE_GRACE_SECS
        assert row.device.minted_at == pytest.approx(minted_at)
        # So a link older than the presence window still asks to confirm after its exchange.
        later = make_mocked_request("POST", "/api/devices/pair/start")
        later["session_nonce"] = token_auth.token_nonce(cookie.value)
        assert owner_presence.presence_proof(later) == ""
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_only_the_exchange_ever_hands_the_page_its_pair() -> None:
    """A plain load of the shell, signed in, carries no pair: it is handed over once, by the
    answer that signed the browser in."""
    client = await _client()
    try:
        first = await _open(client, _link().token)
        cookie = first.cookies[COOKIE].value
        plain = await client.get("/", headers={PAIR_HEADER: pair_of(cookie)})
        assert plain.status == 200
        assert "data-pair" not in await plain.text()
        assert PAIR_HEADER not in plain.headers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_apps_link_signs_no_browser_in() -> None:
    client = await _client()
    try:
        app_link = token_auth.generate_token("owner", ttl_seconds=600, app="notes")
        opened = await _open(client, app_link)
        assert opened.status == 403
        assert COOKIE not in opened.cookies
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_anywhere_else_a_link_authorizes_one_request_and_sets_no_cookie() -> None:
    client = await _client()
    try:
        link = _link(ISSUER_TOKEN)
        probed = await client.get("/api/probe", params={"token": link.token})
        assert probed.status == 200
        assert COOKIE not in probed.cookies
        assert PAIR_HEADER not in probed.headers
        assert (await probed.json())["session"] == link.nonce
        assert _records()[link.nonce].device.kind != "browser", "a link's row took a browser kind"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/api/auth/session"])
async def test_a_head_request_never_exchanges_a_link(path: str) -> None:
    """Only a GET exchanges a link. A HEAD, which the router answers like a GET, sets no cookie,
    hands out no pair and leaves the startup link to sign the browser in after it."""
    client = await _client()
    try:
        link = _link()
        head = await client.head(path, params={"token": link.token}, headers={"User-Agent": CHROME})
        assert COOKIE not in head.cookies
        assert PAIR_HEADER not in head.headers
        assert set(_records()) == {link.nonce}, "a HEAD spent the startup link"
        opened = await _open(client, link.token)
        assert opened.status == 200, await opened.text()
        assert bound(opened.cookies[COOKIE].value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_fetch_exchange_signs_the_browser_in_too() -> None:
    client = await _client()
    try:
        link = _link(ISSUER_TOKEN)
        entered = await client.get("/api/auth/session", params={"token": link.token})
        assert entered.status == 200
        cookie = entered.cookies[COOKIE].value
        assert bound(cookie) and entered.headers[PAIR_HEADER] == pair_of(cookie)
        assert entered.headers["Cache-Control"] == "no-store"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_browser_opening_a_newer_link_replaces_its_own_sign_in_and_keeps_its_pushes() -> (
    None
):
    client = await _client()
    try:
        first = await _open(client, _link().token)
        old = token_auth.token_nonce(first.cookies[COOKIE].value)
        with mock.patch("personalclaw.push.move_to_session") as moved:
            second = await _open(client, _link().token)
        new = token_auth.token_nonce(second.cookies[COOKIE].value)
        assert new != old
        assert old not in _records(), "the replaced sign-in is still live"
        old_id = moved.call_args.args[0]
        assert moved.call_args.args[1] == _records()[new].device.id
        assert old_id != _records()[new].device.id
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_failed_answer_ends_only_the_minted_sign_in() -> None:
    """Nothing ends until the answer stands. A failed one delivered no sign-in, so it ends the
    minted one alone: the browser keeps the sign-in it had and its pushes, and a startup link it
    spent can be opened again."""
    broken = True

    async def shell_or_not(request: web.Request) -> web.Response:
        if broken:
            return web.Response(status=503, text="the dashboard is not built")
        return await shell(request)

    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app.router.add_get("/", shell_or_not)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        old = _link(ISSUER_LOGIN, bound=True)
        client.session.cookie_jar.update_cookies({COOKIE: old.token})
        startup = _link(ISSUER_STARTUP)
        before = set(_records())
        with mock.patch("personalclaw.push.move_to_session") as moved:
            failed = await _open(client, startup.token)
        assert failed.status == 503
        assert COOKIE not in failed.cookies
        moved.assert_not_called()
        # The browser's own sign-in and the startup link are live, and the minted one is gone.
        assert set(_records()) == before == {old.nonce, startup.nonce}
        broken = False
        with mock.patch("personalclaw.push.move_to_session") as moved:
            opened = await _open(client, startup.token)
        assert opened.status == 200, "the spent startup link can't be opened again"
        assert COOKIE in opened.cookies, "the re-open signed no browser in"
        assert bound(opened.cookies[COOKIE].value)
        minted = token_auth.token_nonce(opened.cookies[COOKIE].value)
        # The pushes go to the sign-in the browser now holds, which is live.
        assert moved.call_args.args[1] == _records()[minted].device.id
        assert old.nonce not in _records() and startup.nonce not in _records()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_same_signed_in_browser_reopening_the_printed_link_stays_signed_in() -> None:
    """A spent link on a shell navigation, beside a live bound cookie, is answered as if the query
    weren't there: the shell, its URL scrubbed. The shell holds no data, so this opens nothing."""
    client = await _client()
    try:
        link = _link()
        first = await _open(client, link.token)
        cookie = first.cookies[COOKIE].value
        again = await _open(client, link.token)
        assert again.status == 200
        assert "data-owner-token-scrub" in await again.text()
        assert token_auth.token_nonce(cookie) in _records()
        api = await client.get("/api/probe", params={"token": link.token})
        assert api.status == 403, "only the shell answers as if the query weren't there"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_link_opened_in_the_desktop_window_leaves_the_windows_sign_in_alone() -> None:
    client = await _client()
    try:
        window = _link(ISSUER_WINDOW, bound=True)
        client.session.cookie_jar.update_cookies({COOKIE: window.token})
        opened = await _open(client, _link(ISSUER_TOKEN).token)
        assert opened.status == 200
        assert COOKIE not in opened.cookies, "a link replaced the window's own sign-in"
        assert window.nonce in _records()
    finally:
        await client.close()


def test_every_reason_a_sign_in_ends_has_a_sentence() -> None:
    """A reason registered without its sentence would answer a device with a KeyError."""
    for reason in END_REASONS:
        if reason == session_store.END_EXPIRED:
            continue
        minted = _link(ISSUER_TOKEN)
        session_store.end_sessions([minted.nonce], reason)
        notice = token_auth.signed_out_notice(minted.token)
        assert notice is not None and notice.message.strip(), reason
