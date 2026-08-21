"""Public-exposure hardening + device enrollment (REMOTE-USER-AUTH S4).

Two halves, both of which fail in ways nobody notices until it matters:

* **Exposure hardening** must be inert on a normal local install (a `Secure` cookie over plain
  http is an unusable dashboard) and active the moment the operator declares a public URL.
  The forwarded-header rule is the sharp edge: trusting `X-Real-IP` by the SHAPE of the peer
  address means any container neighbour or LAN device can move a bound session.
* **Enrollment codes** are short strings that mint sessions, so single-use, expiry, scarcity,
  hashed-at-rest and fail-closed reads are each asserted rather than assumed.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth import credentials as creds
from personalclaw.auth import enrollment
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard import exposure, token_auth
from personalclaw.dashboard.handlers import auth as auth_h

GOOD_PASSWORD = "correct-horse-battery-staple"
PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    from personalclaw.dashboard import session_store

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(enrollment, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


def _write_config(home, **dashboard) -> None:
    (home / "config.json").write_text(
        json.dumps({"dashboard": dashboard, "auth": {"login_enabled": True}}), encoding="utf-8"
    )


# ── The exposure signal ───────────────────────────────────────────────────


def test_a_local_install_is_not_exposed() -> None:
    assert exposure.is_exposed(AppConfig()) is False
    assert exposure.public_host(AppConfig()) == ""
    assert exposure.is_https(AppConfig()) is False


def test_dashboard_public_url_declares_exposure(_isolated) -> None:
    _write_config(_isolated, public_url="https://pc.example.com")
    assert exposure.is_exposed() is True
    assert exposure.public_host() == "pc.example.com"
    assert exposure.is_https() is True


def test_inbound_public_url_is_honored_as_a_fallback(_isolated) -> None:
    """An operator who already declared exposure for the inbound surface has declared it."""
    (_isolated / "config.json").write_text(
        json.dumps({"inbound": {"public_url": "https://pc.example.com"}}), encoding="utf-8"
    )
    assert exposure.is_exposed() is True
    assert exposure.public_host() == "pc.example.com"


def test_dashboard_url_alone_does_NOT_declare_exposure(_isolated) -> None:
    """`dashboard.url` means "a URL for links" and is often a LAN/http address.

    Deriving `Secure` from it would set a flag that makes the cookie undeliverable over plain
    http — a silently unusable login.
    """
    _write_config(_isolated, url="http://192.168.1.50:10000")
    assert exposure.is_exposed() is False
    assert token_auth.secure_cookies() is False


def test_a_bare_host_is_assumed_https(_isolated) -> None:
    _write_config(_isolated, public_url="pc.example.com")
    assert exposure.is_https() is True
    assert exposure.public_host() == "pc.example.com"


def test_an_http_public_url_does_not_get_a_secure_cookie(_isolated) -> None:
    """Insecure by nature, but NOT broken: `Secure` there would block the cookie entirely."""
    _write_config(_isolated, public_url="http://pc.example.com")
    assert exposure.is_exposed() is True
    assert exposure.is_https() is False
    assert token_auth.secure_cookies() is False


def test_a_corrupt_config_reports_not_exposed(_isolated) -> None:
    (_isolated / "config.json").write_text("{ not json", encoding="utf-8")
    assert exposure.is_exposed() is False
    assert token_auth.secure_cookies() is False


# ── The Secure cookie ─────────────────────────────────────────────────────


def test_secure_is_off_by_default() -> None:
    """The default install runs on plain http — this must stay off."""
    assert token_auth.secure_cookies() is False


def test_secure_is_on_for_an_https_public_url(_isolated) -> None:
    _write_config(_isolated, public_url="https://pc.example.com")
    assert token_auth.secure_cookies() is True


@pytest.mark.asyncio
async def test_the_login_cookie_carries_secure_when_exposed(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _write_config(_isolated, public_url="https://pc.example.com")

    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 200
        morsel = resp.cookies[f"pc_token_{PORT}"]
        assert morsel["secure"], "an exposed instance must set Secure on the session cookie"
        assert morsel["httponly"]


@pytest.mark.asyncio
async def test_the_login_cookie_omits_secure_locally(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    (_isolated / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert not resp.cookies[f"pc_token_{PORT}"]["secure"]


# ── The WS CSP ────────────────────────────────────────────────────────────


def test_the_csp_is_unchanged_for_a_local_install() -> None:
    from personalclaw.dashboard.server import _ws_csp_sources

    assert _ws_csp_sources() == ""


def test_the_csp_names_the_public_host_when_exposed(_isolated) -> None:
    """Without this the dashboard renders and then silently receives no events."""
    from personalclaw.dashboard.server import _ws_csp_sources

    _write_config(_isolated, public_url="https://pc.example.com")
    sources = _ws_csp_sources()
    assert "wss://pc.example.com" in sources
    assert "https://pc.example.com" in sources


def test_the_csp_keeps_the_port_when_one_is_given(_isolated) -> None:
    from personalclaw.dashboard.server import _ws_csp_sources

    _write_config(_isolated, public_url="https://pc.example.com:8443")
    assert "wss://pc.example.com:8443" in _ws_csp_sources()


# ── Trusted proxies (the sharp edge) ──────────────────────────────────────


def test_nothing_is_trusted_by_default(_isolated) -> None:
    """Empty list means trust nothing — it must never read as "trust everyone"."""
    _write_config(_isolated, public_url="https://pc.example.com")
    assert exposure.trusted_proxies() == []
    for peer in ("10.0.0.9", "127.0.0.1", "192.168.1.5", "203.0.113.7"):
        assert exposure.is_trusted_proxy(peer) is False


def test_a_configured_proxy_is_trusted(_isolated) -> None:
    _write_config(_isolated, public_url="https://pc.example.com", trusted_proxies=["10.0.0.9"])
    assert exposure.is_trusted_proxy("10.0.0.9") is True
    assert exposure.is_trusted_proxy("10.0.0.10") is False


def test_a_cidr_block_is_supported(_isolated) -> None:
    _write_config(_isolated, public_url="https://pc.example.com", trusted_proxies=["172.18.0.0/16"])
    assert exposure.is_trusted_proxy("172.18.4.7") is True
    assert exposure.is_trusted_proxy("172.19.4.7") is False


def test_an_unparseable_proxy_entry_is_skipped_not_fatal(_isolated) -> None:
    _write_config(
        _isolated, public_url="https://pc.example.com", trusted_proxies=["nonsense", "10.0.0.9"]
    )
    assert exposure.is_trusted_proxy("10.0.0.9") is True
    assert exposure.is_trusted_proxy("10.0.0.8") is False


def test_a_non_ip_peer_is_never_trusted(_isolated) -> None:
    _write_config(_isolated, public_url="https://pc.example.com", trusted_proxies=["10.0.0.9"])
    assert exposure.is_trusted_proxy("unknown") is False
    assert exposure.is_trusted_proxy("") is False


# ── Forwarded headers through the real middleware ─────────────────────────


@pytest.mark.asyncio
async def test_forwarded_header_ignored_from_an_untrusted_peer_when_exposed(_isolated) -> None:
    """THE T4.1 property: a spoofed X-Real-IP must not set the session's bound address.

    Asserted through IP binding, which is what the header actually influences: bind a token
    from a forged header, then present it from the real address. If the forgery had been
    believed, the binding would be to the forged value and the second request would be
    refused — so a 200 here proves the header was ignored.
    """
    _write_config(_isolated, public_url="https://pc.example.com")  # exposed, no trusted proxies
    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(_req):
        return web.Response(text="ok")

    token = token_auth.generate_token("jordan", ttl_seconds=300)

    def _req(remote: str, forwarded: str | None):
        r = MagicMock(spec=web.Request)
        r.path = "/"
        r.method = "GET"
        r.query = {"token": token}
        r.cookies = {}
        r.headers = {"X-Real-IP": forwarded} if forwarded else {}
        r.remote = remote
        return r

    first = await mw(_req("10.0.0.5", "203.0.113.99"), _handler)
    assert first.status == 200
    # Same real peer, no header: must still be accepted (bound to 10.0.0.5, not the forgery).
    second = await mw(_req("10.0.0.5", None), _handler)
    assert second.status == 200


@pytest.mark.asyncio
async def test_forwarded_header_honored_from_a_trusted_proxy(_isolated) -> None:
    """The legitimate case must keep working, or nobody can run behind a tunnel."""
    _write_config(_isolated, public_url="https://pc.example.com", trusted_proxies=["10.0.0.5"])
    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(_req):
        return web.Response(text="ok")

    token = token_auth.generate_token("jordan", ttl_seconds=300)

    def _req(remote: str, forwarded: str):
        r = MagicMock(spec=web.Request)
        r.path = "/"
        r.method = "GET"
        r.query = {"token": token}
        r.cookies = {}
        r.headers = {"X-Real-IP": forwarded}
        r.remote = remote
        return r

    assert (await mw(_req("10.0.0.5", "203.0.113.99"), _handler)).status == 200
    # The binding followed the FORWARDED address, so the same real client keeps working…
    assert (await mw(_req("10.0.0.5", "203.0.113.99"), _handler)).status == 200
    # …and a different forwarded client on the same proxy is refused by IP binding.
    assert (await mw(_req("10.0.0.5", "203.0.113.1"), _handler)).status == 403


@pytest.mark.asyncio
async def test_local_installs_keep_the_legacy_proxy_heuristic(_isolated) -> None:
    """Not exposed ⇒ unchanged behavior. Breaking every compose/nginx user would be worse."""
    (_isolated / "config.json").write_text(json.dumps({"dashboard": {}}), encoding="utf-8")
    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(_req):
        return web.Response(text="ok")

    token = token_auth.generate_token("jordan", ttl_seconds=300)

    def _req(remote: str, forwarded: str):
        r = MagicMock(spec=web.Request)
        r.path = "/"
        r.method = "GET"
        r.query = {"token": token}
        r.cookies = {}
        r.headers = {"X-Real-IP": forwarded}
        r.remote = remote
        return r

    assert (await mw(_req("172.18.0.2", "203.0.113.99"), _handler)).status == 200
    assert (await mw(_req("172.18.0.2", "203.0.113.1"), _handler)).status == 403


# ── Enrollment codes ──────────────────────────────────────────────────────


def test_a_code_round_trips_exactly_once(_isolated) -> None:
    code, _exp = enrollment.issue_code()
    assert enrollment.redeem_code(code) is True
    assert enrollment.redeem_code(code) is False, "a code must be single-use"


def test_the_code_is_hashed_at_rest(_isolated) -> None:
    """Reading the store must not yield a redeemable credential."""
    code, _exp = enrollment.issue_code()
    on_disk = enrollment.codes_path().read_text(encoding="utf-8")
    assert code not in on_disk
    assert enrollment.format_code(code) not in on_disk


def test_the_code_store_is_0600(_isolated) -> None:
    enrollment.issue_code()
    assert oct(enrollment.codes_path().stat().st_mode)[-3:] == "600"


def test_codes_use_an_unambiguous_alphabet(_isolated) -> None:
    """Read off one screen, typed into another — I/O/0/1 would produce failed pairings."""
    for _ in range(30):
        code, _exp = enrollment.issue_code()
        assert not (set(code) & set("IO01"))
        assert len(code) == 8


def test_a_formatted_code_is_accepted(_isolated) -> None:
    code, _exp = enrollment.issue_code()
    assert enrollment.redeem_code(enrollment.format_code(code)) is True


def test_a_lowercase_code_is_accepted(_isolated) -> None:
    """Phone keyboards autocapitalize inconsistently; case must not be the failure."""
    code, _exp = enrollment.issue_code()
    assert enrollment.redeem_code(code.lower()) is True


@pytest.mark.parametrize("bad", ["", "SHORT", "TOOLONGACODE", "!!!!!!!!", None])
def test_a_malformed_code_is_refused(_isolated, bad) -> None:  # noqa: ANN001
    enrollment.issue_code()
    assert enrollment.redeem_code(bad) is False


def test_an_expired_code_is_refused(_isolated, monkeypatch) -> None:
    code, _exp = enrollment.issue_code()
    real = time.time
    monkeypatch.setattr(
        enrollment.time, "time", lambda: real() + enrollment.CODE_TTL_SECS + 1, raising=False
    )
    assert enrollment.redeem_code(code) is False


def test_outstanding_codes_are_capped(_isolated) -> None:
    """A large live pool would widen the guess space."""
    for _ in range(enrollment._MAX_ACTIVE + 5):
        enrollment.issue_code()
    assert enrollment.active_codes() <= enrollment._MAX_ACTIVE


def test_the_newest_code_always_works_at_the_cap(_isolated) -> None:
    """Eviction must drop the OLDEST, not refuse the mint the user just asked for."""
    for _ in range(enrollment._MAX_ACTIVE):
        enrollment.issue_code()
    newest, _exp = enrollment.issue_code()
    assert enrollment.redeem_code(newest) is True


def test_an_unreadable_store_refuses_rather_than_accepting(_isolated) -> None:
    code, _exp = enrollment.issue_code()
    enrollment.codes_path().write_text("{ corrupt", encoding="utf-8")
    assert enrollment.redeem_code(code) is False
    assert enrollment.active_codes() == 0


def test_clear_invalidates_everything(_isolated) -> None:
    code, _exp = enrollment.issue_code()
    enrollment.clear_codes()
    assert enrollment.redeem_code(code) is False


def test_active_codes_never_returns_the_codes(_isolated) -> None:
    enrollment.issue_code()
    assert isinstance(enrollment.active_codes(), int)


# ── The enrollment endpoints ──────────────────────────────────────────────


def _enroll_app() -> web.Application:
    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_post("/api/auth/enroll/start", auth_h.api_auth_enroll_start)
    app.router.add_post("/api/auth/enroll/complete", auth_h.api_auth_enroll_complete)
    return app


@pytest.mark.asyncio
async def test_enroll_start_then_complete_yields_a_session(_isolated) -> None:
    _write_config(_isolated)
    async with TestClient(TestServer(_enroll_app())) as client:
        started = await client.post("/api/auth/enroll/start", json={"label": "phone"})
        assert started.status == 200
        code = (await started.json())["code"]

        done = await client.post("/api/auth/enroll/complete", json={"code": code})
        assert done.status == 200
        cookie = done.cookies.get(f"pc_token_{PORT}")
        assert cookie is not None
        valid, user, _reason = token_auth.validate_token(cookie.value, use_session_exp=True)
        assert valid is True and user == "enrolled-device"


@pytest.mark.asyncio
async def test_a_reused_code_is_refused_by_the_endpoint(_isolated) -> None:
    _write_config(_isolated)
    async with TestClient(TestServer(_enroll_app())) as client:
        code = (await (await client.post("/api/auth/enroll/start", json={})).json())["code"]
        assert (await client.post("/api/auth/enroll/complete", json={"code": code})).status == 200
        second = await client.post("/api/auth/enroll/complete", json={"code": code})
        assert second.status == 401
        assert (await second.json())["error"]["code"] == "auth_enroll_code_invalid"


@pytest.mark.asyncio
async def test_a_wrong_code_counts_toward_lockout(_isolated) -> None:
    """A short credential on an unrated endpoint would be grindable."""
    (_isolated / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True, "lockout_threshold": 2}}), encoding="utf-8"
    )
    async with TestClient(TestServer(_enroll_app())) as client:
        for _ in range(2):
            resp = await client.post("/api/auth/enroll/complete", json={"code": "AAAABBBB"})
            assert resp.status == 401
        locked = await client.post("/api/auth/enroll/complete", json={"code": "AAAABBBB"})
        assert locked.status == 429
        assert int(locked.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_enroll_complete_rejects_a_cross_origin_request(_isolated) -> None:
    _write_config(_isolated)
    async with TestClient(TestServer(_enroll_app())) as client:
        code = (await (await client.post("/api/auth/enroll/start", json={})).json())["code"]
        resp = await client.post(
            "/api/auth/enroll/complete",
            json={"code": code},
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status == 403


def test_only_enroll_complete_is_exempt() -> None:
    """Minting a code requires a session; redeeming one cannot."""
    assert "/api/auth/enroll/complete" in token_auth._BYPASS_EXACT
    assert "/api/auth/enroll/start" not in token_auth._BYPASS_EXACT


@pytest.mark.asyncio
async def test_enroll_start_requires_a_session() -> None:
    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(_req):
        return web.Response(text="ok")

    req = MagicMock(spec=web.Request)
    req.path = "/api/auth/enroll/start"
    req.method = "POST"
    req.query = {}
    req.cookies = {}
    req.headers = {}
    req.remote = "10.0.0.5"
    assert (await mw(req, _handler)).status == 403


# ── Session revocation reaches the LIVE gateway (bug found in validation) ─


def test_logout_is_reachable_without_a_dashboard_session() -> None:
    """`/api/logout` authenticates itself, so the dashboard middleware must not gate it.

    It was absent from `_BYPASS_EXACT`, so the middleware demanded a session token before
    `api_logout`'s own loopback + `X-Local-Secret` check could run: every
    `personalclaw logout` and `auth revoke --all` got 403 while the CLI printed success, and
    the "revoked" session kept working. Caught only by driving the real CLI against a real
    gateway — a two-process state bug is invisible to in-process tests.
    """
    assert "/api/logout" in token_auth._BYPASS_EXACT
    # It must sit beside the OTHER self-authenticating route, not among the login exemptions.
    assert "/api/token/local" in token_auth._BYPASS_EXACT


@pytest.mark.asyncio
async def test_logout_passes_the_middleware_then_enforces_its_own_secret() -> None:
    """Exempting it opens nothing: without the secret the handler still refuses."""
    from personalclaw.dashboard.handlers import api_logout

    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(request):  # noqa: ANN001
        return await api_logout(request)

    req = MagicMock(spec=web.Request)
    req.path = "/api/logout"
    req.method = "POST"
    req.query = {}
    req.cookies = {}
    req.headers = {}
    req.remote = "127.0.0.1"
    req.app = {"local_secret": "the-real-secret"}
    resp = await mw(req, _handler)
    # Reached the handler (not a middleware 403 for a missing token) and was refused there.
    assert resp.status == 403
    assert "secret" in (await _body_text(resp)).lower()


async def _body_text(resp) -> str:  # noqa: ANN001
    body = getattr(resp, "text", "") or ""
    return body if isinstance(body, str) else ""


def test_revoke_cli_prefers_the_running_gateway(monkeypatch, _isolated) -> None:
    """The CLI must ASK the gateway, not clear the file behind its back.

    Clearing `sessions.json` from another process leaves the live gateway's in-memory nonce
    set intact, so it keeps honoring the sessions the user just revoked.
    """
    from personalclaw.auth import cli as auth_cli

    called: list[int] = []
    monkeypatch.setattr(auth_cli, "_revoke_via_gateway", lambda port: (called.append(port) or True))

    class _Args:
        all = True
        port = 12345

    assert auth_cli._revoke_cmd(_Args()) == 0
    assert called == [12345], "the CLI did not route the revoke through the gateway"


def test_revoke_cli_falls_back_when_no_gateway_is_running(monkeypatch, _isolated) -> None:
    """Offline, nothing holds contradictory state — refusing would leave no way to revoke."""
    from personalclaw.auth import cli as auth_cli

    monkeypatch.setattr(auth_cli, "_revoke_via_gateway", lambda port: False)
    cleared: list[bool] = []
    import personalclaw.dashboard.token_auth as ta

    monkeypatch.setattr(ta, "revoke_all_sessions", lambda: cleared.append(True))

    class _Args:
        all = True
        port = 0

    assert auth_cli._revoke_cmd(_Args()) == 0
    assert cleared == [True]


def test_revoke_requires_the_all_flag(_isolated) -> None:
    """No per-nonce form: printing live nonces to pick one would print credentials."""
    from personalclaw.auth import cli as auth_cli

    class _Args:
        all = False
        port = 0

    assert auth_cli._revoke_cmd(_Args()) == 2


# ── The login page offers the device-code path ────────────────────────────


@pytest.mark.asyncio
async def test_the_login_page_offers_a_device_code(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _write_config(_isolated)
    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_get("/login", auth_h.login_page)
    async with TestClient(TestServer(app)) as client:
        html = await (await client.get("/login")).text()
        assert "/api/auth/enroll/complete" in html
        assert "Use a device code instead" in html
        assert "XXXX-XXXX" in html
