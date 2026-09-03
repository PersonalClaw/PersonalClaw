"""The login front door (REMOTE-USER-AUTH S3).

The properties that matter here are the ones an attacker probes and the ones that could brick
a local box:

* a login-minted session is **indistinguishable** from a link-minted one (one validation path);
* login **cannot** be the only way in — with login on and the credential file corrupt, the
  paste-token gate still answers;
* no enumeration — wrong user and wrong password return the same code;
* logout **revokes**, durably, rather than only clearing the cookie;
* lockout engages, reports `Retry-After`, and clears on a success.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth import credentials as creds
from personalclaw.config import credentials as cred_store
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.http_errors import HTTP_ERROR_CODES

GOOD_PASSWORD = "correct-horse-battery-staple"
PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """An isolated home + a durable signing key, and no leaked lockout/session state."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard import session_store

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


def _enable_login(home, **overrides) -> None:
    section = {"login_enabled": True}
    section.update(overrides)
    (home / "config.json").write_text(json.dumps({"auth": section}), encoding="utf-8")


def _app() -> web.Application:
    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {"http://localhost:10000"}
    app.router.add_get("/login", auth_h.login_page)
    app.router.add_post("/api/auth/login", auth_h.api_auth_login)
    app.router.add_get("/api/auth/status", auth_h.api_login_status)
    app.router.add_post("/api/auth/logout", auth_h.api_auth_logout)
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    app.router.add_post("/api/auth/password", auth_h.api_auth_set_password)
    return app


# ── The happy path, and what the cookie is ────────────────────────────────


@pytest.mark.asyncio
async def test_login_mints_a_working_session_cookie(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True and body["expires_in"] > 0

        cookie = resp.cookies.get(f"pc_token_{PORT}")
        assert cookie is not None, "no session cookie was set"
        token = cookie.value
        # THE contract: the login-minted token validates through the SAME path as a
        # link-minted one. If this ever needs its own validator, the plan's "one token
        # model" criterion has been broken.
        valid, user, _reason = token_auth.validate_token(token, use_session_exp=True)
        assert valid is True and user == "jordan"


@pytest.mark.asyncio
async def test_the_cookie_is_httponly_and_lax(_isolated) -> None:
    """A session cookie readable by JS is one an XSS can exfiltrate."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        morsel = resp.cookies[f"pc_token_{PORT}"]
        assert morsel["httponly"]
        assert morsel["samesite"].lower() == "lax"
        assert morsel["path"] == "/"


@pytest.mark.asyncio
async def test_session_ttl_comes_from_config(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, session_ttl="12h")
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert (await resp.json())["expires_in"] == 12 * 3600


@pytest.mark.asyncio
async def test_a_bad_ttl_in_config_falls_back_rather_than_failing(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, session_ttl="not-a-duration")
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 200
        assert (await resp.json())["expires_in"] == token_auth.DEFAULT_BROWSER_SESSION_TTL_SECS


# ── Refusals, and the absence of an oracle ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("jordan", "wrong-password-entirely"),
        ("nobody", GOOD_PASSWORD),
        ("", ""),
        ("jordan", ""),
    ],
)
async def test_bad_credentials_all_return_the_same_code(_isolated, username, password) -> None:
    """Wrong user and wrong password must be indistinguishable to the caller."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert resp.status == 401
        assert await resp.json() == {
            "error": {
                "code": "auth_invalid_credentials",
                # The message comes from the REGISTRY, never from the request — that is
                # what makes every rejection byte-identical and unusable to enumerate.
                "message": HTTP_ERROR_CODES["auth_invalid_credentials"],
            }
        }
        assert f"pc_token_{PORT}" not in resp.cookies


@pytest.mark.asyncio
async def test_login_refused_when_not_enabled(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)  # credential exists, feature off
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 403
        assert (await resp.json())["error"]["code"] == "auth_not_enabled"


@pytest.mark.asyncio
async def test_login_refused_when_no_credential_is_configured(_isolated) -> None:
    """Login on, credential absent: refuse, never mint."""
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_invalid_credentials"


@pytest.mark.asyncio
async def test_a_corrupt_credential_file_refuses_rather_than_falling_open(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.credentials_path().write_text("{ garbage", encoding="utf-8")
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_a_malformed_body_is_a_refusal_not_a_crash(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        for payload in (b"not json", b"[1,2,3]", b"null"):
            resp = await client.post(
                "/api/auth/login", data=payload, headers={"Content-Type": "application/json"}
            )
            assert resp.status == 401


@pytest.mark.asyncio
async def test_a_cross_origin_login_is_rejected(_isolated) -> None:
    """CSRF guard: a form on evil.example must not be able to log anyone in."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"username": "jordan", "password": GOOD_PASSWORD},
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status == 403
        assert f"pc_token_{PORT}" not in resp.cookies
        # The rejection must be DISTINGUISHABLE from bad credentials (#963): a LAN/tunnel
        # user with a CORRECT password was told "Wrong username or password." because the
        # origin branch returned the credentials code. Same distinctness reasoning as
        # auth_not_enabled — the origin is the caller's own address, not a secret.
        body = await resp.json()
        assert body["error"]["code"] == "auth_origin_not_allowed"
        assert body["error"]["code"] != "auth_invalid_credentials"


def test_the_login_page_never_defaults_an_unknown_error_to_bad_credentials() -> None:
    """#963's second layer: the page JS turned ANY unparseable/unmodelled error (the
    middleware's old plain-text 403, a proxy 502) into 'Wrong username or password.'
    via the `|| 'auth_invalid_credentials'` fallback. The fallback must be the honest
    generic ('Sign-in failed (HTTP N).'), and the origin rejection must have a real
    message that names the caller's own origin and the config that fixes it."""
    script = auth_h._LOGIN_SCRIPT
    assert "|| 'auth_invalid_credentials'" not in script
    assert "|| 'auth_enroll_code_invalid'" not in script  # same fallback family
    assert "auth_origin_not_allowed" in script
    assert "PERSONALCLAW_CORS_ORIGINS" in script
    assert "location.origin" in script  # the message names the address that was rejected


def test_the_csrf_middleware_emits_the_wire_envelope_not_plain_text() -> None:
    """#963's third layer: the server-level CSRF middleware answered with a plain-text
    403 body, which the login page parsed as {} and mislabelled. It must emit the one
    wire envelope with the same origin code the auth routes use. Source-level rail:
    the middleware is a closure inside start_dashboard, so this pins the emitted shape
    where a unit harness cannot reach."""
    import inspect

    from personalclaw.dashboard import server as dash_server

    src = inspect.getsource(dash_server)
    assert 'text="CSRF check failed' not in src
    at = src.find("async def csrf_middleware")
    assert at != -1
    body = src[at : at + 1200]
    assert 'json_error("auth_origin_not_allowed", status=403)' in body


# ── Lockout ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lockout_after_the_threshold_with_retry_after(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, lockout_threshold=3, lockout_window="15m")
    async with TestClient(TestServer(_app())) as client:
        for _ in range(3):
            resp = await client.post(
                "/api/auth/login", json={"username": "jordan", "password": "nope-nope-nope"}
            )
            assert resp.status == 401

        # The 4th attempt is refused before the password is even considered.
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": "nope-nope-nope"}
        )
        assert resp.status == 429
        assert (await resp.json())["error"]["code"] == "auth_locked_out"
        assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_lockout_refuses_even_the_correct_password(_isolated) -> None:
    """Otherwise the lockout is decorative — guessing could continue until it hits."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, lockout_threshold=2)
    async with TestClient(TestServer(_app())) as client:
        for _ in range(2):
            await client.post(
                "/api/auth/login", json={"username": "jordan", "password": "wrong-wrong-wrong"}
            )
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 429


@pytest.mark.asyncio
async def test_a_successful_login_clears_the_failure_count(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, lockout_threshold=3)
    async with TestClient(TestServer(_app())) as client:
        for _ in range(2):
            await client.post(
                "/api/auth/login", json={"username": "jordan", "password": "wrong-wrong-wrong"}
            )
        ok = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert ok.status == 200
        # Two more failures must NOT now trip the (3-attempt) threshold.
        for _ in range(2):
            resp = await client.post(
                "/api/auth/login", json={"username": "jordan", "password": "wrong-wrong-wrong"}
            )
            assert resp.status == 401


@pytest.mark.asyncio
async def test_the_failure_window_expires(_isolated, monkeypatch) -> None:
    """Old failures must age out, or one bad day would lock you out permanently."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, lockout_threshold=2, lockout_window="15m")
    async with TestClient(TestServer(_app())) as client:
        for _ in range(2):
            await client.post(
                "/api/auth/login", json={"username": "jordan", "password": "wrong-wrong-wrong"}
            )
        locked = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert locked.status == 429

        # Jump past the window.
        real_monotonic = auth_h.time.monotonic
        monkeypatch.setattr(
            auth_h.time, "monotonic", lambda: real_monotonic() + 16 * 60, raising=False
        )
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 200


def test_lockout_bookkeeping_failure_fails_open(monkeypatch) -> None:
    """A broken counter must not lock the owner out — the password check still guards."""

    class _Boom:
        lockout_threshold = "not-an-int"
        lockout_window = "15m"

    assert auth_h._lockout_remaining("1.2.3.4", _Boom()) == 0


def test_the_tracked_ip_table_is_capped() -> None:
    auth_h.reset_lockouts()
    for i in range(auth_h._MAX_TRACKED_IPS + 50):
        auth_h._record_failure(f"10.0.{i // 256}.{i % 256}")
    assert len(auth_h._FAILURES) <= auth_h._MAX_TRACKED_IPS
    auth_h.reset_lockouts()


# ── TOTP at login ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_totp_required_but_missing_returns_its_own_code(_isolated, monkeypatch) -> None:

    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: None)
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret("JBSWY3DPEHPK3PXP")
    monkeypatch.setenv(creds.TOTP_SECRET_KEY, "JBSWY3DPEHPK3PXP")
    _enable_login(_isolated, require_totp=True)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_totp_required"
        assert f"pc_token_{PORT}" not in resp.cookies


@pytest.mark.asyncio
async def test_a_valid_totp_code_completes_the_login(_isolated, monkeypatch) -> None:
    from personalclaw.auth import totp

    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: None)
    secret = totp.new_secret()
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret(secret)
    monkeypatch.setenv(creds.TOTP_SECRET_KEY, secret)
    _enable_login(_isolated, require_totp=True)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "jordan",
                "password": GOOD_PASSWORD,
                "totp": totp.code_now(secret),
            },
        )
        assert resp.status == 200
        assert resp.cookies.get(f"pc_token_{PORT}") is not None


@pytest.mark.asyncio
async def test_a_wrong_totp_code_is_refused_and_counted(_isolated, monkeypatch) -> None:
    from personalclaw.auth import totp

    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: None)
    secret = totp.new_secret()
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.set_totp_secret(secret)
    monkeypatch.setenv(creds.TOTP_SECRET_KEY, secret)
    _enable_login(_isolated, require_totp=True, lockout_threshold=2)
    async with TestClient(TestServer(_app())) as client:
        for _ in range(2):
            resp = await client.post(
                "/api/auth/login",
                json={"username": "jordan", "password": GOOD_PASSWORD, "totp": "000000"},
            )
            assert resp.status == 401
            assert (await resp.json())["error"]["code"] == "auth_invalid_credentials"
        # A wrong code counts toward lockout — otherwise the second factor is brute-forceable.
        resp = await client.post(
            "/api/auth/login",
            json={"username": "jordan", "password": GOOD_PASSWORD, "totp": "000000"},
        )
        assert resp.status == 429


@pytest.mark.asyncio
async def test_totp_required_with_no_secret_enrolled_refuses(_isolated, monkeypatch) -> None:
    """`require_totp` with nothing enrolled must refuse, not silently skip the factor."""
    monkeypatch.delenv(creds.TOTP_SECRET_KEY, raising=False)
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated, require_totp=True)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_totp_required"


# ── Logout revokes ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_revokes_the_session_not_just_the_cookie(_isolated) -> None:
    """Clearing the cookie alone leaves the token live for anyone holding a copy."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        login = await client.post(
            "/api/auth/login", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        token = login.cookies[f"pc_token_{PORT}"].value
        assert token_auth.validate_token(token, use_session_exp=True)[0] is True

        out = await client.post("/api/auth/logout")
        assert out.status == 200
        assert (await out.json())["revoked"] is True

        # The token itself is dead, not merely forgotten by this browser.
        valid, _u, _r = token_auth.validate_token(token, use_session_exp=True)
        assert valid is False
        assert out.cookies[f"pc_token_{PORT}"].value == ""


@pytest.mark.asyncio
async def test_logout_without_a_session_is_not_an_error(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/auth/logout")
        assert resp.status == 200
        assert (await resp.json())["revoked"] is False


def test_revoke_token_clears_the_durable_store(_isolated) -> None:
    """The restart property: a revoked session must not come back after a reboot."""
    from personalclaw.dashboard import session_store

    token = token_auth.generate_token("jordan", ttl_seconds=3600)
    nonce = json.loads(token_auth._b64url_decode(token.split(".")[0]))["nonce"]
    assert nonce in session_store.load_sessions()

    assert token_auth.revoke_token(token) is True
    assert nonce not in session_store.load_sessions()

    # Simulate a restart: memory is empty, only the durable store speaks.
    token_auth._state.clear_all()
    assert token_auth.validate_token(token, use_session_exp=True)[0] is False


def test_revoke_token_on_a_malformed_token_is_false_not_a_crash() -> None:
    assert token_auth.revoke_token("not-a-token") is False
    assert token_auth.revoke_token("") is False


# ── The /login page and the redirect ──────────────────────────────────────


@pytest.mark.asyncio
async def test_login_page_renders_when_enabled(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/login")
        assert resp.status == 200
        html = await resp.text()
        assert "Sign in" in html
        assert "/api/auth/login" in html
        # The local escape hatch stays discoverable on the page itself.
        assert "personalclaw token" in html


@pytest.mark.asyncio
async def test_login_page_hides_the_totp_field_unless_required(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        assert "var NEEDS_TOTP = false" in await (await client.get("/login")).text()
    _enable_login(_isolated, require_totp=True)
    async with TestClient(TestServer(_app())) as client:
        assert "var NEEDS_TOTP = true" in await (await client.get("/login")).text()


@pytest.mark.asyncio
async def test_login_page_redirects_home_when_login_is_off(_isolated) -> None:
    """The route must not imply a door that is not there."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/login", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/"


@pytest.mark.asyncio
async def test_auth_status_exposes_only_two_booleans(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        body = await (await client.get("/api/auth/status")).json()
        assert set(body) == {"login_enabled", "totp_required"}
        # An unauthenticated caller learns nothing about WHO or WHETHER a credential exists.
        assert "jordan" not in json.dumps(body)


# ── The deny path: redirect vs the paste-token gate ───────────────────────


def _deny_for(path: str, method: str = "GET"):
    from unittest.mock import MagicMock

    req = MagicMock(spec=web.Request)
    req.path = path
    req.method = method
    return token_auth._deny(req, "Token required")


def test_the_paste_token_gate_is_served_when_login_is_not_offered(_isolated) -> None:
    """Default behavior, byte-for-byte: no login configured → today's gate."""
    resp = _deny_for("/")
    assert resp.status == 403
    assert "personalclaw token" in resp.text


def test_a_page_request_redirects_to_login_when_offered(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    resp = _deny_for("/")
    assert resp.status == 302
    assert resp.headers["Location"] == "/login"


def test_api_requests_still_get_json_not_a_redirect(_isolated) -> None:
    """A fetch() must receive a 403 it can handle, not an HTML redirect."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    resp = _deny_for("/api/sessions")
    assert resp.status == 403
    assert resp.content_type == "application/json"


def test_the_login_path_itself_never_redirects(_isolated) -> None:
    """Otherwise an unauthenticated /login would loop forever."""
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    resp = _deny_for("/login")
    assert resp.status == 403


def test_non_get_requests_do_not_redirect(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    resp = _deny_for("/", method="POST")
    assert resp.status == 403


def test_login_enabled_without_a_credential_keeps_the_gate(_isolated) -> None:
    """THE anti-brick property: enabling login without a password must not hide the gate."""
    _enable_login(_isolated)
    assert creds.has_credentials() is False
    resp = _deny_for("/")
    assert resp.status == 403
    assert "personalclaw token" in resp.text


def test_a_corrupt_credential_file_keeps_the_gate(_isolated) -> None:
    """Success Criterion 3: with login on and credentials corrupt, the owner can still get in."""
    creds.set_password("jordan", GOOD_PASSWORD)
    creds.credentials_path().write_text("{ corrupt", encoding="utf-8")
    _enable_login(_isolated)
    resp = _deny_for("/")
    assert resp.status == 403
    assert "personalclaw token" in resp.text


def test_a_corrupt_config_keeps_the_gate(_isolated) -> None:
    """Any failure deciding "is login offered" must fall back to the path that always works."""
    creds.set_password("jordan", GOOD_PASSWORD)
    (_isolated / "config.json").write_text("{ not json", encoding="utf-8")
    resp = _deny_for("/")
    assert resp.status == 403
    assert "personalclaw token" in resp.text


# ── Exemptions ────────────────────────────────────────────────────────────


def test_only_the_three_login_routes_are_exempt() -> None:
    """A too-broad exemption is how an auth surface springs a hole."""
    exact = token_auth._BYPASS_EXACT
    assert {"/login", "/api/auth/login", "/api/auth/status"} <= exact
    for guarded in ("/api/auth/logout", "/api/auth/session", "/api/auth/password"):
        assert guarded not in exact, f"{guarded} must stay behind token auth"


@pytest.mark.asyncio
async def test_the_guarded_auth_routes_require_a_session() -> None:
    """Drive them through the REAL middleware, not just the exemption list."""
    from unittest.mock import MagicMock

    mw = token_auth.token_auth_middleware(port=PORT)

    async def _handler(_req):
        return web.Response(text="ok")

    for path in ("/api/auth/logout", "/api/auth/session", "/api/auth/password"):
        req = MagicMock(spec=web.Request)
        req.path = path
        req.method = "POST"
        req.query = {}
        req.cookies = {}
        req.headers = {}
        req.remote = "10.0.0.5"
        resp = await mw(req, _handler)
        assert resp.status == 403, f"{path} was reachable without a session"


# ── Settings → Account (T3.4) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_view_reports_configured_state(_isolated) -> None:
    creds.set_password("jordan", GOOD_PASSWORD)
    _enable_login(_isolated)
    async with TestClient(TestServer(_app())) as client:
        body = await (await client.get("/api/auth/session")).json()
        assert body["credential_configured"] is True
        assert body["username"] == "jordan"
        assert body["login_enabled"] is True
        # Still never the material itself.
        blob = json.dumps(body)
        assert "argon2" not in blob and GOOD_PASSWORD not in blob


@pytest.mark.asyncio
async def test_setting_a_password_from_an_authenticated_session(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/password", json={"username": "jordan", "password": GOOD_PASSWORD}
        )
        assert resp.status == 200
        assert creds.verify_password("jordan", GOOD_PASSWORD) is True


@pytest.mark.asyncio
async def test_setting_a_short_password_is_a_400_naming_the_floor(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/password", json={"username": "jordan", "password": "short"}
        )
        assert resp.status == 400
        body = await resp.json()
        assert "at least" in body["error"]
        # The rejected value must not be echoed back.
        assert "short" not in body["error"].replace("at least", "")


@pytest.mark.asyncio
async def test_password_change_rejects_a_cross_origin_request(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/auth/password",
            json={"username": "jordan", "password": GOOD_PASSWORD},
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status == 403
        assert creds.has_credentials() is False
