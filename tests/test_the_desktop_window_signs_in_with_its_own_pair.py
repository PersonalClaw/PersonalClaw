"""The desktop app's window signs in with a sign-in of its own, and a logout ends the one it sent.

``GET /api/token/local?for=window`` (loopback plus the home's local secret) mints the window a
browser's bound sign-in under its own door, ``window``, and answers its pair, which the app's main
process adds to the window's requests. Its started-by proof lasts the gateway start that minted it,
no renewal replaces it, and no link or pairing in the window ends it. ``POST /api/auth/logout`` ends
the session that authorized its request, on whichever carrier brought it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from browser_sign_in import browser_token, pair_of

from personalclaw.dashboard import owner_presence, session_store, token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import core as core_h
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.pair_refusals import MIN_DESKTOP_VERSION
from personalclaw.dashboard.session_store import ISSUER_WINDOW
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, bound

PORT = 10000
COOKIE = f"pc_token_{PORT}"
SECRET = "the-local-secret-of-this-start"
_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "PersonalClaw/{} Chrome/140.0 Electron/38.0 Safari/537.36"
)
#: A current desktop app's window: the first version with the window door, wherever that moves.
DESKTOP = _AGENT.format(MIN_DESKTOP_VERSION)
#: The last desktop app from before the window door.
OLDER_DESKTOP = _AGENT.format("0.3.6")
#: A release candidate of the first app with the window door.
CANDIDATE_DESKTOP = _AGENT.format(f"{MIN_DESKTOP_VERSION}-rc.1")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"the-desktop-window")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _whoami(request: web.Request) -> web.Response:
    return web.json_response({"session": request.get("session_nonce", "")})


async def _client(secret: str = SECRET) -> TestClient:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["local_secret"] = secret
    app["allowed_origins"] = {f"http://127.0.0.1:{PORT}"}
    app.router.add_get("/api/token/local", core_h.api_token_local)
    app.router.add_get("/api/whoami", _whoami)
    app.router.add_post("/api/auth/logout", auth_h.api_auth_logout)
    app.router.add_post("/api/auth/confirm", auth_h.api_auth_confirm)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _window(client: TestClient, query: str = "") -> dict[str, Any]:
    # What the desktop's main process sends, and nothing else: no agent of a window's.
    resp = await client.get(
        f"/api/token/local?for=window{query}",
        headers={"X-Local-Secret": SECRET, "Accept": "application/json"},
    )
    assert resp.status == 200, await resp.text()
    assert resp.headers["Cache-Control"] == "no-store"
    return await resp.json()


@pytest.mark.asyncio
async def test_the_window_door_mints_a_bound_sign_in_and_answers_its_pair() -> None:
    client = await _client()
    try:
        answer = await _window(client)
        token, pair = answer["token"], answer["pair"]
        assert bound(token) and pair == pair_of(token)
        record = session_store.load_session_records()[token_auth.token_nonce(token)]
        assert record.issuer == ISSUER_WINDOW and record.device.kind == "desktop"
        assert session_store.pool_of(record) == session_store.POOL_BROWSER
        assert answer["expires_in"] == token_auth.browser_session_ttl()
        seen = await client.get(
            "/api/whoami", headers={"Cookie": f"{COOKIE}={token}", PAIR_HEADER: pair}
        )
        assert seen.status == 200
        assert (
            await client.get("/api/whoami", headers={"Cookie": f"{COOKIE}={token}"})
        ).status == 403
        without = await client.get("/api/token/local?for=window")
        assert without.status == 403, "the window door opened without the local secret"
    finally:
        await client.close()


def _request_for(token: str, *, secret: str, remote: str = "127.0.0.1") -> web.Request:
    app = web.Application()
    app["local_secret"] = secret
    request = make_mocked_request("POST", "/api/devices/pair/start", app=app)
    request["session_nonce"] = token_auth.token_nonce(token)
    request._transport_peername = (remote, 1)  # noqa: SLF001 — where the request came from
    return request


@pytest.mark.asyncio
async def test_the_windows_started_by_proof_lasts_the_start_that_minted_it() -> None:
    client = await _client()
    try:
        token = (await _window(client))["token"]
    finally:
        await client.close()
    proof = owner_presence.presence_proof
    assert proof(_request_for(token, secret=SECRET)) == owner_presence.PROOF_STARTED_BY
    record = session_store.load_session_records()[token_auth.token_nonce(token)]
    assert SECRET not in record.started_by, "the store holds the local secret itself"
    assert proof(_request_for(token, secret="the-next-start")) != owner_presence.PROOF_STARTED_BY
    assert proof(_request_for(token, secret=SECRET, remote="10.0.0.2")) != (
        owner_presence.PROOF_STARTED_BY
    )


@pytest.mark.asyncio
async def test_nothing_in_the_window_replaces_its_own_sign_in() -> None:
    client = await _client()
    try:
        token = (await _window(client))["token"]
    finally:
        await client.close()
    request = make_mocked_request("POST", "/api/auth/confirm", headers={"User-Agent": DESKTOP})
    nonce = token_auth.token_nonce(token)
    assert token_auth.renew_sign_in(request, nonce, 3600, owner="owner") is None
    paired = make_mocked_request(
        "POST", "/api/devices/pair/complete", headers={"Cookie": f"{COOKIE}={token}"}
    )
    paired.app["port"] = PORT
    assert devices_h._replaced_sign_in(paired) == ""
    assert token_auth.token_nonce(token) in session_store.load_session_records()


@pytest.mark.asyncio
async def test_a_logout_ends_the_session_that_authorized_it_on_each_carrier() -> None:
    client = await _client()
    try:
        page = browser_token("owner", 600)
        await client.post(
            "/api/auth/logout", headers={"Cookie": f"{COOKIE}={page}", PAIR_HEADER: pair_of(page)}
        )
        script = token_auth.generate_token("owner", ttl_seconds=600)
        await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {script}"})
        window = await _window(client)
        beside = browser_token("owner", 600)
        out = await client.post(
            "/api/auth/logout",
            headers={
                "Authorization": f"Bearer {window['token']}",
                PAIR_HEADER: window["pair"],
                "Cookie": f"{COOKIE}={beside}",
            },
        )
        assert (await out.json())["revoked"] is True
        live = session_store.load_session_records()
        for ended in (page, script, window["token"]):
            assert token_auth.token_nonce(ended) not in live, ended[:12]
        assert token_auth.token_nonce(beside) in live, "a logout ended a session it merely carried"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("navigation", [False, True], ids=["a request", "the window's page"])
async def test_a_desktop_app_older_than_the_window_door_is_told_to_update_the_app(
    navigation: bool,
) -> None:
    """A desktop app without the window door holds a cookie no pair goes with. Nothing in its
    window can sign it back in, so it is told to update the app, from where, and to which version.
    A current app, and a browser, holding such a cookie are told what a browser is."""
    from personalclaw.dashboard.pair_refusals import DESKTOP_RELEASES

    client = await _client()
    try:
        old = token_auth.mint_session("owner", 600, issuer=session_store.ISSUER_TOKEN).token
        nav = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"} if navigation else {}
        agents = (
            (OLDER_DESKTOP, True),
            (CANDIDATE_DESKTOP, False),
            (DESKTOP, False),
            ("Mozilla/5.0 Chrome/140.0 Safari/537.36", False),
        )
        for agent, told in agents:
            refused = await client.get(
                "/" if navigation else "/api/whoami",
                headers={"Cookie": f"{COOKIE}={old}", "User-Agent": agent, **nav},
            )
            assert refused.status == 403
            assert refused.content_type == ("text/html" if navigation else "application/json")
            assert refused.headers.get("X-Auth-Required") == "true"
            text = await refused.text()
            said = "Update the PersonalClaw app" in text
            assert said is told, (agent, text)
            if told:
                assert DESKTOP_RELEASES in text and MIN_DESKTOP_VERSION in text
                assert "quit" not in text.lower() and "reopen" not in text.lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_window_the_update_signed_out_is_told_the_way_back_its_app_has() -> None:
    """A window of an app older than the window door is told to update the app, never to reopen
    it, which would sign nothing in. A current app's window is told to quit and open it again."""
    client = await _client()
    try:
        window = token_auth.mint_session(
            "owner",
            600,
            issuer=ISSUER_WINDOW,
            device=session_store.DeviceInfo(id="w", kind="desktop"),
        )
        token_auth.sign_out([window.nonce], session_store.END_UPDATED, actor="update")
        for agent, update in ((OLDER_DESKTOP, True), (CANDIDATE_DESKTOP, False), (DESKTOP, False)):
            refused = await client.get(
                "/api/whoami", headers={"Cookie": f"{COOKIE}={window.token}", "User-Agent": agent}
            )
            assert refused.status in (401, 403)
            text = await refused.text()
            assert "when PersonalClaw was updated" in text, text
            assert ("Update the PersonalClaw app" in text) is update, (agent, text)
            assert ("quit PersonalClaw" in text) is not update, (agent, text)
    finally:
        await client.close()


async def _told(client: TestClient, token: str, agent: str) -> str:
    refused = await client.get(
        "/api/whoami", headers={"Cookie": f"{COOKIE}={token}", "User-Agent": agent}
    )
    assert refused.status in (401, 403)
    return await refused.text()


@pytest.mark.asyncio
async def test_an_older_apps_paired_window_is_told_to_update_then_pair_again() -> None:
    """A paired device in an older app updates the app first, then pairs again, in that order."""
    client = await _client()
    try:
        paired = token_auth.mint_session(
            "owner",
            600,
            issuer=session_store.ISSUER_PAIR,
            device=session_store.DeviceInfo(id="p", kind="desktop"),
        )
        token_auth.sign_out([paired.nonce], session_store.END_UPDATED, actor="update")
        text = await _told(client, paired.token, OLDER_DESKTOP)
        steps = [
            "Update the PersonalClaw app",
            "sign in on the computer running PersonalClaw",
            "pair this device again",
            "notifications back on",
        ]
        at = [text.find(step) for step in steps]
        assert -1 not in at and at == sorted(at), text
        assert "quit" not in text.lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_every_ending_an_older_app_is_told_of_says_update_never_quit() -> None:
    """Not only the update's: a window signed out from Settings, in an older app, is told to
    update the app. A current app's is told to quit and open it again."""
    client = await _client()
    try:
        window = token_auth.mint_session(
            "owner",
            600,
            issuer=session_store.ISSUER_READY,
            device=session_store.DeviceInfo(id="r", kind="desktop"),
        )
        token_auth.sign_out([window.nonce], session_store.END_SIGNED_OUT_ELSEWHERE, actor="owner")
        older = await _told(client, window.token, OLDER_DESKTOP)
        assert "Update the PersonalClaw app" in older and "quit" not in older.lower(), older
        current = await _told(client, window.token, DESKTOP)
        assert "quit PersonalClaw" in current and "Update the PersonalClaw app" not in current
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_confirm_renews_the_session_that_authorized_it_and_tells_a_stale_window_why(
    monkeypatch,
) -> None:
    """Confirm renews the sign-in its request was admitted on, whichever carrier brought it, not
    what the cookie holds. A window an earlier start signed in can't show the owner is here, and
    no password changes that: it is told the window's way back, never answered ok."""
    from personalclaw.dashboard.sign_in_pair import started_by_digest

    monkeypatch.setattr(auth_h, "require_owner_presence", lambda *a, **k: None)
    monkeypatch.setattr(auth_h, "check_origin", lambda request: True, raising=False)
    monkeypatch.setattr(auth_h, "_login_offered", lambda: True)
    client = await _client()
    try:
        bearer = browser_token("owner", 600)
        other = browser_token("owner", 600)
        renewed = await client.post(
            "/api/auth/confirm",
            json={"password": "x"},
            headers={
                "Authorization": f"Bearer {bearer}",
                PAIR_HEADER: pair_of(bearer),
                "Cookie": f"{COOKIE}={other}",
            },
        )
        assert renewed.status == 200, await renewed.text()
        live = session_store.load_session_records()
        assert token_auth.token_nonce(bearer) not in live, "the authorizing session wasn't renewed"
        assert token_auth.token_nonce(other) in live, "a cookie the request didn't use was ended"
        stale = token_auth.mint_session(
            "owner", 600, issuer=ISSUER_WINDOW, bound=True, started_by=started_by_digest("before")
        ).token
        client.session.cookie_jar.clear()
        told = await client.post(
            "/api/auth/confirm",
            json={"password": "x"},
            headers={"Cookie": f"{COOKIE}={stale}", PAIR_HEADER: pair_of(stale)},
        )
        assert told.status == 401, await told.text()
        assert "earlier start" in (await told.json())["error"]["message"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_window_door_lasts_what_the_app_asks_up_to_thirty_days() -> None:
    from personalclaw.dashboard.handlers.core import WINDOW_MAX_TTL_SECS

    client = await _client()
    try:
        asked = await _window(client, "&ttl=7d")
        assert asked["expires_in"] == 7 * 24 * 3600
        longest = await _window(client, "&ttl=30d")
        assert longest["expires_in"] == WINDOW_MAX_TTL_SECS == 30 * 24 * 3600
        too_long = await client.get(
            "/api/token/local?for=window&ttl=31d", headers={"X-Local-Secret": SECRET}
        )
        assert too_long.status == 400
        assert (await too_long.json())["error"]["code"] == "token_ttl_too_long"
        plain = await _window(client)
        assert plain["expires_in"] == token_auth.browser_session_ttl()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_settings_devices_lists_the_window_as_the_desktop_app_whatever_it_sends() -> None:
    client = await _client()
    try:
        token = (await _window(client))["token"]
        nonce = token_auth.token_nonce(token)
        assert session_store.load_session_records()[nonce].device.kind == "desktop"
        chrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140.0 Safari/537.36"
        seen = await client.get(
            "/api/whoami",
            headers={
                "Cookie": f"{COOKIE}={token}",
                PAIR_HEADER: pair_of(token),
                "User-Agent": chrome,
            },
        )
        assert seen.status == 200
        row = session_store.load_session_records()[nonce].device
        assert (row.kind, row.name) == ("desktop", "PersonalClaw desktop app")
    finally:
        await client.close()
