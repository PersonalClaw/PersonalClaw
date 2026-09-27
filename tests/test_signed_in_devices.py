"""A multi-device owner is never silently signed out, and every sign-in is listed (ledger 255).

Measured on main before this change: the gateway kept FIVE live sessions in total and minted every
token into the same pool — the startup link, the harness token, a password sign-in, a paired
phone, every ``personalclaw token``, every ``personalclaw run``, and an app-scoped token for every
request an app's backend proxied. The sixth mint silently signed out the least recently used
session, wherever it was: three ``personalclaw token`` calls signed the owner's browser AND her
paired phone out, and both then read ``403 {"error": "token superseded"}`` — no sentence, and a
Settings → Devices that listed only paired phones, so the evicted browser was never even visible.

These tests drive the real middleware and routes with one cookie jar per device, the way the
persona uses the gateway: a desktop browser, a second browser, a paired phone, the CLI and a
script that mints tokens. They assert what each device SEES, not how the store is laid out.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from personalclaw.auth import credentials as creds
from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import core as core_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
SECRET = "synthetic-local-secret"
COOKIE = f"pc_token_{PORT}"
PASSWORD = "correct-horse-battery-staple"

#: The decided limit for each kind of sign-in (browsers, paired devices, tokens). Stated here as a
#: number, not read from the code, so the test says what the owner was promised.
LIMIT = 20

CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:140.0) Gecko/20100101 Firefox/140.0"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
CURL = "curl/8.7.1"

#: The owner's consent to "Sign out all other devices" — what the panel sends from its dialog.
CONFIRM = {"confirm": True}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches lives in *tmp_path*; the last-seen throttle is off.

    The throttle is a write-rate knob (one stamp per device per minute). These tests run a
    device's whole day in a second, so with it on, "least recently used" would be decided by a
    stamp up to a minute stale — the behaviour under test is the ordering, not the knob.
    """
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(ss, "LAST_SEEN_THROTTLE_SECS", 0.0)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    """Every SEL row, through the one ``sel()`` accessor every writer calls."""
    import personalclaw.sel as sel_mod
    from personalclaw.dashboard import token_auth as ta

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    monkeypatch.setattr(ta, "_sel_fn", lambda: recorder)
    return events


async def _status(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "user": request.get("user", "")})


async def _page(request: web.Request) -> web.Response:
    return web.Response(text="<html>the dashboard</html>", content_type="text/html")


def _gateway_app() -> web.Application:
    """The real token middleware in front of the real sign-in, token and device routes."""
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["local_secret"] = SECRET
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    app.router.add_get("/", _page)
    app.router.add_get("/api/status", _status)
    app.router.add_get("/api/token/local", core_h.api_token_local)
    app.router.add_post("/api/logout", core_h.api_logout)
    app.router.add_get("/login", auth_h.login_page)
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    app.router.add_post("/api/auth/logout", auth_h.api_auth_logout)
    devices_h.register_device_routes(app)
    return app


class Device:
    """One device: its own cookie jar and its own User-Agent, like a real browser or phone."""

    def __init__(self, server: TestServer, user_agent: str) -> None:
        self.server = server
        self.user_agent = user_agent
        self.http = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True), headers={"User-Agent": user_agent}
        )

    async def get(self, path: str, **kw: Any) -> aiohttp.ClientResponse:
        resp = await self.http.get(self.server.make_url(path), allow_redirects=False, **kw)
        await resp.read()
        return resp

    async def post(self, path: str, body: Any = None, **kw: Any) -> aiohttp.ClientResponse:
        resp = await self.http.post(
            self.server.make_url(path), json=body if body is not None else {}, **kw
        )
        await resp.read()
        return resp

    async def signed_in(self) -> bool:
        return (await self.get("/api/status")).status == 200


class Gateway:
    """A running gateway plus the ways the persona gets a device into it."""

    def __init__(self) -> None:
        self.server = TestServer(_gateway_app())
        self.devices: list[Device] = []

    async def __aenter__(self) -> "Gateway":
        await self.server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        for device in self.devices:
            await device.http.close()
        await self.server.close()

    def device(self, user_agent: str) -> Device:
        device = Device(self.server, user_agent)
        self.devices.append(device)
        return device

    async def mint_token(self, *, user_agent: str = CURL, query: str = "") -> str:
        """What `personalclaw token`, `personalclaw run` and a script all do: /api/token/local."""
        async with aiohttp.ClientSession(headers={"User-Agent": user_agent}) as http:
            resp = await http.get(
                self.server.make_url(f"/api/token/local{query}"), headers={"X-Local-Secret": SECRET}
            )
            assert resp.status == 200, await resp.text()
            return (await resp.json())["token"]

    async def token_response(self, query: str = "") -> dict[str, Any]:
        async with aiohttp.ClientSession(headers={"User-Agent": CURL}) as http:
            resp = await http.get(
                self.server.make_url(f"/api/token/local{query}"), headers={"X-Local-Secret": SECRET}
            )
            assert resp.status == 200, await resp.text()
            return await resp.json()

    async def sign_in_with_link(self, device: Device, token: str | None = None) -> str:
        """Open a `personalclaw token` link in *device*, the way the 403 gate tells you to."""
        token = token or await self.mint_token()
        resp = await device.get(f"/?token={token}")
        assert resp.status == 200, await resp.text()
        return token

    async def pair(self, phone: Device, *, using: Device) -> str:
        """Settings → Devices → Pair a device on *using*; the phone redeems the code."""
        start = await using.post("/api/devices/pair/start")
        assert start.status == 200, await start.text()
        code = (await start.json())["code"]
        done = await phone.post("/api/devices/pair/complete", {"code": code})
        assert done.status == 200, await done.text()
        return (await done.json())["device_id"]

    async def bearer_status(self, token: str, *, user_agent: str = CURL) -> int:
        async with aiohttp.ClientSession(headers={"User-Agent": user_agent}) as http:
            resp = await http.get(
                self.server.make_url("/api/status"), headers={"Authorization": f"Bearer {token}"}
            )
            return resp.status


async def _refusal(device: Device, path: str = "/api/status") -> dict[str, Any]:
    """The refusal *device* reads, asserted to be a signed-out answer the SPA can recognise."""
    resp = await device.get(path)
    assert resp.status == 403, f"expected a refusal, got {resp.status}"
    assert resp.headers.get("X-Auth-Required") == "true"
    body = await resp.json()
    assert isinstance(body.get("error"), dict), f"a bare refusal, no sentence: {body}"
    return body["error"]


# ── Nobody normal is signed out ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_script_and_cli_tokens_never_sign_out_a_browser_or_a_phone() -> None:
    """THE measured bug: three `personalclaw token` calls signed out a browser and a phone."""
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser_a)
        browser_b = gw.device(FIREFOX)
        await gw.sign_in_with_link(browser_b)

        for _ in range(3 * LIMIT):  # the CLI and a script, minting through a long day
            await gw.mint_token()

        assert await browser_a.signed_in(), "the owner's browser was signed out by script tokens"
        assert await phone.signed_in(), "the paired phone was signed out by script tokens"
        assert await browser_b.signed_in(), "the second browser was signed out by script tokens"


@pytest.mark.asyncio
async def test_app_tokens_never_sign_out_the_owner() -> None:
    """An app's backend got a fresh app-scoped token on every proxied request, in the same pool."""
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        await gw.sign_in_with_link(browser)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser)

        for _ in range(5 * LIMIT):
            token_auth.generate_token("owner", ttl_seconds=3600, app="notes")

        assert await browser.signed_in(), "app tokens signed the owner's browser out"
        assert await phone.signed_in(), "app tokens signed the owner's phone out"


@pytest.mark.asyncio
async def test_after_a_restart_the_limits_count_what_is_on_disk() -> None:
    """The old limit lived in memory, so after a restart it counted only what that process had
    seen: every token minted before the restart was invisible to it and stayed live on top of
    the limit, so the bound that justified the limit did not survive a reboot."""
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        await gw.sign_in_with_link(browser)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser)
        before = [await gw.mint_token() for _ in range(LIMIT)]

        token_auth._state.clear_all()  # the in-memory half of a restart; the disk is intact

        after = [await gw.mint_token() for _ in range(LIMIT)]
        live = [t for t in before + after if await gw.bearer_status(t) == 200]
        assert live == after, f"{len(live)} tokens are live after a restart; the limit is {LIMIT}"
        assert await browser.signed_in()
        assert await phone.signed_in()


# ── The security rationale still holds: each kind stays bounded ─────────


@pytest.mark.asyncio
async def test_each_kind_stays_bounded_and_the_idle_token_goes_first() -> None:
    """Why there was a limit at all: a token pasted into a terminal should not stay live forever
    just because newer ones keep being minted. That still holds, within each kind."""
    async with Gateway() as gw:
        active = await gw.mint_token()
        minted = []
        for _ in range(LIMIT + 10):
            minted.append(await gw.mint_token())
            assert await gw.bearer_status(active) == 200, "a token in use was signed out"

        live = [t for t in minted if await gw.bearer_status(t) == 200]
        assert len(live) + 1 == LIMIT, f"{len(live) + 1} tokens are live, the limit is {LIMIT}"
        assert live == minted[-(LIMIT - 1) :], "the idle, oldest tokens must be the ones that go"
    assert ss.POOL_CAPS[ss.POOL_TOKEN] == LIMIT


@pytest.mark.asyncio
async def test_the_browser_signed_out_by_the_limit_is_told_why() -> None:
    async with Gateway() as gw:
        first = gw.device(CHROME)
        await gw.sign_in_with_link(first)
        for _ in range(LIMIT):
            await gw.sign_in_with_link(gw.device(FIREFOX))

        error = await _refusal(first)
        assert error["code"] == "session_signed_out"
        assert error["detail"]["reason"] == "limit"
        assert f"more than {LIMIT} browsers" in error["message"], error["message"]
        assert "personalclaw token" in error["message"], "the sentence must say how to get back in"


# ── A signed-out or expired device is told why, and how to sign back in ─


@pytest.mark.asyncio
async def test_a_device_signed_out_from_settings_is_told_why_and_how_to_sign_back_in() -> None:
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        await gw.sign_in_with_link(browser)
        phone = gw.device(IPHONE)
        device_id = await gw.pair(phone, using=browser)

        assert (await browser.post(f"/api/devices/{device_id}/revoke")).status == 200

        error = await _refusal(phone)
        assert error["code"] == "session_signed_out"
        assert error["detail"]["reason"] == "signed_out_elsewhere"
        message = error["message"]
        assert "signed out" in message and "Settings → Devices" in message, message
        assert "Pair a device" in message, "a paired phone signs back in by pairing again"


@pytest.mark.asyncio
async def test_an_expired_sign_in_is_told_how_long_it_lasted() -> None:
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        await gw.sign_in_with_link(browser, token_auth.generate_token("owner", ttl_seconds=1))
        await asyncio.sleep(1.5)

        error = await _refusal(browser)
        assert error["code"] == "session_expired"
        assert "lasted 1 second" in error["message"], error["message"]
        assert "personalclaw token" in error["message"]


@pytest.mark.asyncio
async def test_a_signed_out_browser_reads_the_sentence_on_the_page_it_opens() -> None:
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        browser_b = gw.device(FIREFOX)
        await gw.sign_in_with_link(browser_b)
        rows = (await (await browser_a.get("/api/devices")).json())["devices"]
        other = next((r for r in rows if r["name"] == "Firefox on Mac"), None)
        assert other is not None, f"the second browser is not listed: {rows}"
        assert (await browser_a.post(f"/api/devices/{other['id']}/revoke")).status == 200

        page = await browser_b.get("/")
        html = await page.text()
        assert page.status == 403
        assert "signed out" in html and "Settings → Devices" in html, html[-600:]
        assert "403 — no active sessions" not in html


@pytest.mark.asyncio
async def test_with_password_sign_in_the_sign_in_page_says_why(_isolated) -> None:
    creds.set_password("jordan", PASSWORD)
    (_isolated / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    async with Gateway() as gw:
        laptop = gw.device(CHROME)
        phone = gw.device(IPHONE)
        for device in (laptop, phone):
            resp = await device.post(
                "/api/auth/login", {"username": "jordan", "password": PASSWORD}
            )
            assert resp.status == 200, await resp.text()
        assert (await laptop.post("/api/devices/revoke-others", CONFIRM)).status == 200

        redirect = await phone.get("/")
        assert redirect.status == 302 and redirect.headers["Location"] == "/login"
        html = await (await phone.get("/login")).text()
        assert "signed out" in html and "Sign out all other devices" in html, html[:800]
        assert "password" in html.lower()


@pytest.mark.asyncio
async def test_a_forged_token_learns_nothing() -> None:
    """The sentence is for a device that really held the session. A token signed by another key
    gets the same bare refusal it always did — no reason, no timestamp."""
    async with Gateway() as gw:
        stranger = gw.device(CHROME)
        token_auth.use_ephemeral_secret(b"k" * 32)
        forged = token_auth.generate_token("owner", ttl_seconds=3600)
        token_auth.use_persistent_secret()
        stranger.http.cookie_jar.update_cookies({COOKIE: forged}, gw.server.make_url("/"))
        resp = await stranger.get("/api/status")
        body = await resp.json()
        assert resp.status == 403
        assert not isinstance(body.get("error"), dict), body
        assert "signed out" not in json.dumps(body)


@pytest.mark.asyncio
async def test_the_session_cookie_outlives_its_session_so_the_end_can_be_explained() -> None:
    """A browser drops a cookie the moment its Max-Age runs out — and a gateway that never sees
    the expired cookie cannot say it expired. It still refuses it: the expiry is signed."""
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        resp = await browser.get(f"/?token={token_auth.generate_token('owner', ttl_seconds=3600)}")
        max_age = int(resp.cookies[COOKIE]["max-age"])
        assert max_age == pytest.approx(3600 + 7 * 86400, abs=5), "no grace to explain the end"
    assert token_auth.SIGNED_OUT_NOTICE_GRACE_SECS == 7 * 86400


# ── Settings → Devices lists every sign-in, truthfully ──────────────────


@pytest.mark.asyncio
async def test_the_list_names_every_signed_in_device_its_kind_first_and_last_seen_and_where() -> (
    None
):
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser_a)
        browser_b = gw.device(FIREFOX)
        await gw.sign_in_with_link(browser_b)
        script = await gw.mint_token()
        assert await gw.bearer_status(script) == 200
        token_auth.generate_token("owner", ttl_seconds=3600, app="notes")
        await phone.get("/api/status")
        await browser_b.get("/api/status")

        payload = await (await browser_a.get("/api/devices")).json()
        rows = payload["devices"]
        by_name = {row["name"]: row for row in rows}
        assert {"Chrome on Mac", "Firefox on Mac", "curl"} <= set(by_name), sorted(by_name)
        assert any(row["kind"] == "mobile" for row in rows), "the paired phone is missing"
        assert by_name["Chrome on Mac"]["kind"] == "browser"
        assert by_name["curl"]["kind"] == "cli"
        assert all(row["issuer"] != "app" for row in rows), "an app token is not a device"
        assert len(rows) == 4, [row["name"] for row in rows]
        now = time.time()
        for row in rows:
            assert 0 < row["minted_at"] <= now, row
            assert row["last_seen"] >= row["minted_at"], row
            assert row["ip"] == "127.0.0.1", row
            assert row["expires_at"] > now, row
        assert [row["name"] for row in rows if row["current"]] == ["Chrome on Mac"]
        assert payload["limits"] == {"device": LIMIT, "browser": LIMIT, "token": LIMIT}


@pytest.mark.asyncio
async def test_signing_one_device_out_ends_exactly_that_one() -> None:
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser_a)
        browser_b = gw.device(FIREFOX)
        await gw.sign_in_with_link(browser_b)

        rows = (await (await browser_a.get("/api/devices")).json())["devices"]
        firefox = next((r for r in rows if r["name"] == "Firefox on Mac"), None)
        assert firefox is not None, f"the second browser is not listed: {rows}"
        assert (await browser_a.post(f"/api/devices/{firefox['id']}/revoke")).status == 200

        assert not await browser_b.signed_in()
        assert await browser_a.signed_in() and await phone.signed_in()
        rows = (await (await browser_a.get("/api/devices")).json())["devices"]
        assert "Firefox on Mac" not in {r["name"] for r in rows}


@pytest.mark.asyncio
async def test_sign_out_all_other_devices_keeps_only_this_one() -> None:
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser_a)
        browser_b = gw.device(FIREFOX)
        await gw.sign_in_with_link(browser_b)
        script = await gw.mint_token()

        resp = await browser_a.post("/api/devices/revoke-others", CONFIRM)
        assert resp.status == 200
        assert (await resp.json())["revoked"] == 3

        assert await browser_a.signed_in()
        for device in (phone, browser_b):
            error = await _refusal(device)
            assert error["detail"]["reason"] == "signed_out_others"
        assert await gw.bearer_status(script) == 403
        rows = (await (await browser_a.get("/api/devices")).json())["devices"]
        assert [r["name"] for r in rows] == ["Chrome on Mac"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"confirm": "true"}, {"confirm": False}])
async def test_sign_out_all_other_devices_without_consent_signs_nobody_out(body) -> None:
    """It reaches every paired phone, and each must be paired again — so a bare POST (a
    script's, an agent's, a stray one) is refused, and only the JSON literal `true` consents."""
    async with Gateway() as gw:
        browser_a = gw.device(CHROME)
        await gw.sign_in_with_link(browser_a)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser_a)

        resp = await browser_a.post("/api/devices/revoke-others", body)
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "confirmation_required"
        assert await phone.signed_in() and await browser_a.signed_in()


@pytest.mark.asyncio
async def test_a_browser_that_opens_a_new_link_is_still_one_sign_in() -> None:
    """Each gateway start opens a fresh link in the default browser. Without this, every
    restart left the previous one behind as a live session no browser held — a list of
    identical "Chrome on Mac" rows, and a live credential nobody would ever sign out."""
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        first = await gw.sign_in_with_link(browser)
        await gw.sign_in_with_link(browser)

        rows = (await (await browser.get("/api/devices")).json())["devices"]
        assert [r["name"] for r in rows] == ["Chrome on Mac"], rows
        assert await gw.bearer_status(first) == 403, "the replaced sign-in is still live"


@pytest.mark.asyncio
async def test_a_browser_session_is_not_a_paired_device() -> None:
    """Listing every session must not widen what only a PAIRED device may do: the origin-less
    websocket upgrade and the browser connector both still require a pairing."""
    from personalclaw.dashboard import ws as ws_mod
    from personalclaw.dashboard.handlers import browse_connector

    async with Gateway() as gw:
        browser = gw.device(CHROME)
        await gw.sign_in_with_link(browser)
        phone = gw.device(IPHONE)
        await gw.pair(phone, using=browser)

    records = ss.load_session_records()
    browser_request = {
        "session_nonce": next(n for n, r in records.items() if r.issuer != ss.ISSUER_PAIR)
    }
    phone_request = {
        "session_nonce": next(n for n, r in records.items() if r.issuer == ss.ISSUER_PAIR)
    }
    assert ws_mod._paired_device_session(browser_request) == ""
    assert browse_connector._paired_device(browser_request) is None
    assert ws_mod._paired_device_session(phone_request) != ""
    assert browse_connector._paired_device(phone_request) is not None


# ── Every sign-in and sign-out is in the security log ───────────────────


@pytest.mark.asyncio
async def test_every_sign_in_and_sign_out_is_in_the_security_log(sel_events) -> None:
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        link = await gw.sign_in_with_link(browser)
        phone = gw.device(IPHONE)
        device_id = await gw.pair(phone, using=browser)
        second = gw.device(FIREFOX)
        await gw.sign_in_with_link(second)
        nonces = set(ss.load_session_records())
        await browser.post(f"/api/devices/{device_id}/revoke")
        await browser.post("/api/devices/revoke-others", CONFIRM)
        await browser.post("/api/auth/logout")

    signed_in = [e for e in sel_events if e["operation"] == "session_signed_in"]
    signed_out = [e for e in sel_events if e["operation"] == "session_signed_out"]
    assert {e["metadata"]["issuer"] for e in signed_in} >= {"token", "pair"}
    assert all(e["metadata"]["lifetime_secs"] > 0 for e in signed_in)
    reasons = [e["metadata"]["reason"] for e in signed_out]
    assert reasons.count("signed_out_elsewhere") == 1, reasons
    assert "signed_out_others" in reasons and "signed_out" in reasons, reasons
    dumped = json.dumps(sel_events, default=str)
    assert link not in dumped, "a token in the security log is a token in every log shipper"
    assert len(nonces) == 3
    for nonce in nonces:
        assert nonce not in dumped, "the eviction row used to carry the whole nonce"


# ── Lifetimes: what a token is, and how long it lasts ──────────────────


@pytest.mark.asyncio
async def test_a_token_asked_for_without_a_lifetime_lasts_twenty_hours_not_a_year() -> None:
    """The owner ruling keeps a year reachable only when a caller ASKS for it; the route handed
    one out to any caller that did not say."""
    async with Gateway() as gw:
        body = await gw.token_response()
    assert body["expires_in"] == 20 * 3600
    payload = json.loads(token_auth._b64url_decode(body["token"].split(".")[0]))
    assert payload["session_exp"] - payload["iat"] == pytest.approx(20 * 3600, abs=5)


@pytest.mark.asyncio
async def test_the_token_route_says_when_it_stops_working() -> None:
    async with Gateway() as gw:
        short = await gw.token_response("?ttl=2h")
        long = await gw.token_response("?ttl=720h")
    now = time.time()
    assert short["expires_at"] == pytest.approx(now + 2 * 3600, abs=10)
    assert short["open_within"] == 2 * 3600, "a 2-hour link cannot be opened for 24"
    assert long["expires_at"] == pytest.approx(now + 720 * 3600, abs=10)
    assert long["open_within"] == token_auth.LINK_WINDOW_SECS


@pytest.mark.asyncio
async def test_a_link_cannot_sign_a_browser_in_once_its_session_has_ended() -> None:
    """The `?token=` path checked only the 24-hour click window, so a 1-hour token opened as a
    link after its hour still signed a browser in (for as long as this process remembered it)."""
    async with Gateway() as gw:
        browser = gw.device(CHROME)
        token = token_auth.generate_token("owner", ttl_seconds=1)
        await asyncio.sleep(1.5)
        resp = await browser.get(f"/?token={token}")
        assert resp.status == 403
