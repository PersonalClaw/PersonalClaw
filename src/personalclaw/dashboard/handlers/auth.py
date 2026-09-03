"""The login front door (REMOTE-USER-AUTH C3 / S3).

**This is one more ISSUER of the existing session token, not a second way to be authorized.**
`POST /api/auth/login` verifies a password and then calls the same `generate_token` the
`?token=` link and `personalclaw token` already call, and sets the same `pc_token_{port}`
cookie. Downstream, the middleware cannot tell a login-minted session from a link-minted one
— there is exactly one validation path, which is the point: a second path is a second place
for an authorization bug to hide.

**Login never becomes the only way in.** Every deny here leaves the local `?token=` / loopback
routes untouched, so a forgotten password, a corrupt credential file, or `require_totp` with no
enrolled secret cannot brick the box.

**Failure posture.** Enumeration is the thing being avoided: a wrong username and a wrong
password return the same `auth_invalid_credentials`, and the credential layer runs the argon2
verify either way so they take the same time. Lockout is per-IP-and-window, counted in memory,
and is deliberately **fail-open on bookkeeping errors** — a counter that breaks must not lock
the owner out of their own dashboard, since the password check itself is still fail-closed.

Error codes are Tier-S stable and must never be reworded: `auth_invalid_credentials`,
`auth_totp_required`, `auth_locked_out`, `auth_not_enabled`, `auth_origin_not_allowed`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from personalclaw.auth import credentials as creds
from personalclaw.dashboard.handlers.page_shell import page_document
from personalclaw.dashboard.origin import check_origin
from personalclaw.dashboard.token_auth import (
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    generate_token,
    parse_config_duration,
    secure_cookies,
    validate_token,
)
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

#: Stable error codes (C3). Never reword — clients and docs match on these strings.
ERR_INVALID = "auth_invalid_credentials"
# Origin rejection is a MISCONFIGURATION signal, not a secret: the origin is the caller's
# own address, and telling them "wrong password" for it sends the owner to reset a
# password that was never wrong (the same distinctness reasoning as ERR_NOT_ENABLED
# below). Mirrors device_pair_origin_rejected on the pairing routes.
ERR_ORIGIN = "auth_origin_not_allowed"
ERR_TOTP_REQUIRED = "auth_totp_required"
ERR_LOCKED_OUT = "auth_locked_out"
ERR_NOT_ENABLED = "auth_not_enabled"

#: Failed attempts, keyed by client IP → list of monotonic timestamps. In memory on purpose:
#: a lockout is a speed bump against online guessing, not durable state, and persisting it
#: would hand an attacker a way to write to disk from an unauthenticated endpoint.
_FAILURES: dict[str, list[float]] = {}

#: Cap on tracked IPs, so an attacker rotating source addresses cannot grow this without
#: bound. When full, the oldest-touched entry is dropped — that IP simply starts over, which
#: is the same position it would be in with no lockout at all.
_MAX_TRACKED_IPS = 4096


def _sel() -> Any:
    from personalclaw.sel import sel

    return sel()


def _auth_cfg() -> Any:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().auth


def _client_ip(request: web.Request) -> str:
    """The client address for lockout accounting.

    Uses the TCP remote ONLY. `X-Forwarded-For` is deliberately ignored here: an untrusted
    peer can set it to anything, so trusting it would let an attacker reset their own failure
    counter every request while also letting them lock out an arbitrary victim address. S4
    introduces trusted-proxy handling; until then the honest value is the connection's.
    """
    return request.remote or "unknown"


def _lockout_remaining(ip: str, cfg: Any) -> int:
    """Seconds until *ip* may try again, or 0 when it is not locked out."""
    try:
        threshold = max(1, int(cfg.lockout_threshold))
        window = parse_config_duration(cfg.lockout_window, default_secs=900)
        attempts = _FAILURES.get(ip, [])
        cutoff = time.monotonic() - window
        recent = [t for t in attempts if t > cutoff]
        if recent:
            _FAILURES[ip] = recent
        else:
            _FAILURES.pop(ip, None)
        if len(recent) < threshold:
            return 0
        # Locked until the window clears from the OLDEST counted failure.
        return max(1, int(recent[0] + window - time.monotonic()))
    except Exception:  # noqa: BLE001
        # Fail OPEN on bookkeeping: the password check is still fail-closed, and a broken
        # counter must not be able to lock the owner out of their own box.
        logger.warning("lockout accounting failed — allowing the attempt", exc_info=True)
        return 0


def _record_failure(ip: str) -> None:
    try:
        if ip not in _FAILURES and len(_FAILURES) >= _MAX_TRACKED_IPS:
            _FAILURES.pop(next(iter(_FAILURES)), None)
        _FAILURES.setdefault(ip, []).append(time.monotonic())
    except Exception:  # noqa: BLE001
        logger.debug("could not record a failed login attempt", exc_info=True)


def _clear_failures(ip: str) -> None:
    _FAILURES.pop(ip, None)


def reset_lockouts() -> None:
    """Clear all lockout state (test isolation, and `auth` CLI recovery)."""
    _FAILURES.clear()


async def api_auth_login(request: web.Request) -> web.Response:
    """POST /api/auth/login — verify the owner credential and mint a session cookie.

    Exempt from token auth (it is how you GET a token), so it carries its own guards:
    CSRF origin check, per-IP lockout, and a fail-closed password verify.
    """
    ip = _client_ip(request)
    cfg = _auth_cfg()

    if not check_origin(request):
        _sel().log_api_access(
            caller=ip,
            operation="login_origin_rejected",
            outcome="denied",
            source="auth",
            error="origin rejected",
        )
        return json_error(ERR_ORIGIN, status=403)

    if not bool(cfg.login_enabled):
        # Explicitly distinct from bad credentials: "this door does not exist here" is not
        # secret (the config is the owner's own), and conflating it would make a
        # misconfiguration indistinguishable from a typo.
        return json_error(ERR_NOT_ENABLED, status=403)

    remaining = _lockout_remaining(ip, cfg)
    if remaining:
        _sel().log_api_access(
            caller=ip,
            operation="login_locked_out",
            outcome="denied",
            source="auth",
            error=f"retry_after={remaining}s",
        )
        return json_error(ERR_LOCKED_OUT, status=429, headers={"Retry-After": str(remaining)})

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    code = str(body.get("totp") or "")

    if not creds.verify_password(username, password):
        _record_failure(ip)
        _sel().log_api_access(caller=ip, operation="login_failed", outcome="denied", source="auth")
        return json_error(ERR_INVALID, status=401)

    # Password is right. The second factor is checked AFTER, so a valid password with a
    # missing code cannot be distinguished from an invalid one by timing alone.
    if bool(cfg.require_totp):
        from personalclaw.auth import totp as totp_mod

        secret = creds.totp_secret()
        if not secret:
            # Required but never enrolled: refuse rather than silently skipping the factor
            # the owner asked for. `auth status` warns about this exact state, and the local
            # token path is still available to fix it.
            _sel().log_api_access(
                caller=ip,
                operation="login_failed",
                outcome="denied",
                source="auth",
                error="require_totp set but no secret enrolled",
            )
            return json_error(ERR_TOTP_REQUIRED, status=401)
        if not code:
            return json_error(ERR_TOTP_REQUIRED, status=401)
        if not totp_mod.verify_code(secret, code):
            _record_failure(ip)
            _sel().log_api_access(
                caller=ip,
                operation="login_failed",
                outcome="denied",
                source="auth",
                error="invalid totp code",
            )
            return json_error(ERR_INVALID, status=401)

    ttl = parse_config_duration(cfg.session_ttl, default_secs=DEFAULT_BROWSER_SESSION_TTL_SECS)
    token = generate_token(username.strip() or "owner", ttl_seconds=ttl)
    _clear_failures(ip)
    _sel().log_api_access(
        caller=username.strip() or "owner",
        operation="login_success",
        outcome="granted",
        source="auth",
    )

    resp = web.json_response({"ok": True, "expires_in": ttl})
    _set_session_cookie(request, resp, token, ttl)
    return resp


def _set_session_cookie(request: web.Request, resp: web.Response, token: str, ttl: int) -> None:
    """Set the session cookie the middleware already reads.

    Same name, same flags as the middleware's own mint, so the two are indistinguishable —
    including `Secure`, which comes from the SAME `secure_cookies()` the middleware uses
    (T4.1). Sharing that one resolver is the point: a login cookie that was `Secure` while a
    link cookie was not would be two different security postures for one session model.
    """
    port = _cookie_port(request)
    resp.set_cookie(
        f"pc_token_{port}",
        token,
        httponly=True,
        samesite="Lax",
        path="/",
        max_age=ttl,
        secure=secure_cookies(),
    )
    # Clear the legacy non-port-specific cookie, mirroring the middleware.
    resp.set_cookie("pc_token", "", max_age=0, path="/")


def _cookie_port(request: web.Request) -> int:
    from personalclaw.dashboard.token_auth import _DEFAULT_PORT

    port = request.app.get("port")
    try:
        return int(port) if port else _DEFAULT_PORT
    except (TypeError, ValueError):
        return _DEFAULT_PORT


async def api_auth_logout(request: web.Request) -> web.Response:
    """POST /api/auth/logout — clear the cookie AND revoke the session behind it.

    Clearing the cookie alone would be theatre: the token remains valid, so anyone holding a
    copy (a synced browser profile, a shell history, a proxy log) still has a live session.
    Revoking the nonce is what actually ends it, durably — the session store is on disk, so
    it stays revoked across a restart.
    """
    if not check_origin(request):
        return json_error(ERR_ORIGIN, status=403)

    port = _cookie_port(request)
    token = request.cookies.get(f"pc_token_{port}", "") or request.query.get("token", "")
    revoked = False
    if token:
        try:
            from personalclaw.dashboard.token_auth import revoke_token

            revoked = revoke_token(token)
        except Exception:  # noqa: BLE001
            logger.warning("could not revoke the session on logout", exc_info=True)

    _sel().log_api_access(
        caller=request.get("user") or (request.remote or "unknown"),
        operation="session_revoked",
        outcome="ok" if revoked else "partial",
        source="auth",
        error="" if revoked else "cookie cleared, nonce not revoked",
    )

    resp = web.json_response({"ok": True, "revoked": revoked})
    resp.set_cookie(f"pc_token_{port}", "", max_age=0, path="/")
    resp.set_cookie("pc_token", "", max_age=0, path="/")
    return resp


async def api_login_status(request: web.Request) -> web.Response:
    """GET /api/auth/status — what the login UI needs to render itself.

    Exempt from token auth because the /login page has no session yet, so it returns ONLY
    what an unauthenticated caller may see: whether a login form is offered and whether it
    will ask for a code. Never the username, never whether a credential exists — that would
    tell a stranger whether the box is worth guessing at.
    """
    cfg = _auth_cfg()
    return web.json_response(
        {
            "login_enabled": bool(cfg.login_enabled),
            "totp_required": bool(cfg.require_totp),
        }
    )


async def api_auth_session(request: web.Request) -> web.Response:
    """GET /api/auth/session — the authenticated account view (Settings → Account).

    Behind normal token auth, so this one MAY report the configured state: the caller already
    holds a valid session. Still never the hash or the TOTP secret.
    """
    cfg = _auth_cfg()
    st = creds.status()
    return web.json_response(
        {
            "login_enabled": bool(cfg.login_enabled),
            "credential_configured": bool(st["configured"]),
            "username": st["username"],
            "totp_enabled": bool(st["totp_enabled"]),
            "totp_required": bool(cfg.require_totp),
            "session_ttl": str(cfg.session_ttl),
            "lockout_threshold": int(cfg.lockout_threshold),
            "lockout_window": str(cfg.lockout_window),
            "user": request.get("user") or "",
        }
    )


async def api_auth_set_password(request: web.Request) -> web.Response:
    """POST /api/auth/password — set the owner password from an AUTHENTICATED session.

    This is the LAN/Settings path the plan calls for (T3.4), and it is not a contradiction of
    "a password never rides in an HTTP body": the caller already holds a valid session token,
    the request is same-origin, and the alternative is that a user who reaches their box only
    through the browser can never set a password at all. What stays true is that this cannot
    be reached WITHOUT a session — it is behind the normal middleware, unlike `login`.
    """
    if not check_origin(request):
        return json_error(ERR_ORIGIN, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username:
        username = creds.status()["username"] or str(request.get("user") or "owner")

    try:
        creds.set_password(username, password)
    except ValueError as exc:
        # The message names the floor, never the submitted value.
        return web.json_response({"error": str(exc)}, status=400)
    except creds.CredentialError as exc:
        return web.json_response({"error": str(exc)}, status=500)

    _sel().log_api_access(
        caller=request.get("user") or "dashboard",
        operation="password_set",
        outcome="ok",
        source="auth",
    )
    return web.json_response({"ok": True, "username": username})


ERR_ENROLL_INVALID = "auth_enroll_code_invalid"


async def api_auth_enroll_start(request: web.Request) -> web.Response:
    """POST /api/auth/enroll/start — mint a single-use device enrollment code.

    Behind the normal middleware (a live session), so this is the "I am already in, on my
    laptop, and want my phone in too" path. The code is returned ONCE; nothing can read it
    back, because the store holds only its hash.
    """
    if not check_origin(request):
        return json_error(ERR_ORIGIN, status=403)

    from personalclaw.auth import enrollment

    try:
        body = await request.json()
    except Exception:
        body = {}
    label = str((body or {}).get("label") or "") if isinstance(body, dict) else ""

    code, expires_at = enrollment.issue_code(label=label)
    return web.json_response(
        {
            "code": enrollment.format_code(code),
            "expires_at": expires_at,
            "expires_in": enrollment.CODE_TTL_SECS,
        }
    )


async def api_auth_enroll_complete(request: web.Request) -> web.Response:
    """POST /api/auth/enroll/complete — redeem a code for a device session.

    Exempt from token auth (the whole point is that the device has no session yet), so it
    carries the same guards as login: origin check and the per-IP lockout, because a code is a
    short credential and an unrated endpoint would let someone grind the 8-character space.
    """
    ip = _client_ip(request)
    cfg = _auth_cfg()
    if not check_origin(request):
        return json_error(ERR_ORIGIN, status=403)

    remaining = _lockout_remaining(ip, cfg)
    if remaining:
        _sel().log_api_access(
            caller=ip,
            operation="login_locked_out",
            outcome="denied",
            source="auth",
            error=f"enroll retry_after={remaining}s",
        )
        return json_error(ERR_LOCKED_OUT, status=429, headers={"Retry-After": str(remaining)})

    from personalclaw.auth import enrollment

    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str((body or {}).get("code") or "") if isinstance(body, dict) else ""

    if not enrollment.redeem_code(code):
        _record_failure(ip)
        return json_error(ERR_ENROLL_INVALID, status=401)

    # A device session, deliberately at the same TTL as a browser login rather than the
    # 1-year cap: a phone in a drawer should not hold a live session for a year.
    ttl = parse_config_duration(cfg.session_ttl, default_secs=DEFAULT_BROWSER_SESSION_TTL_SECS)
    token = generate_token("enrolled-device", ttl_seconds=ttl)
    _clear_failures(ip)
    _sel().log_api_access(caller=ip, operation="enroll_completed", outcome="granted", source="auth")

    resp = web.json_response({"ok": True, "expires_in": ttl})
    _set_session_cookie(request, resp, token, ttl)
    return resp


async def login_page(request: web.Request) -> web.Response:
    """GET /login — the login form.

    Served as a standalone HTML document rather than a React route: it has to render before
    any authenticated bundle fetch can succeed, exactly like the existing paste-token gate it
    replaces. When login is disabled it redirects to `/`, so the route cannot become a
    dead-end that implies a door which is not there.
    """
    cfg = _auth_cfg()
    if not bool(cfg.login_enabled):
        raise web.HTTPFound("/")
    return web.Response(
        text=_LOGIN_HTML.replace("__TOTP__", "true" if cfg.require_totp else "false"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def has_valid_session(request: web.Request, port: int) -> bool:
    """Whether *request* already carries a valid session (used by the redirect decision)."""
    token = request.query.get("token") or request.cookies.get(f"pc_token_{port}", "")
    if not token:
        return False
    valid, _uid, _reason = validate_token(token, use_session_exp=True)
    return bool(valid)


# The form deliberately mirrors the existing 403 gate's visual language (same tokens, same
# shapes) so it reads as the same product rather than a bolted-on login. The shell it is
# composed into is shared with `/pair` (see handlers/page_shell.py) — the tokens live in ONE
# place because two hand-written copies drift and neither page has a visual-regression test.
_LOGIN_BODY = """\
<h1>Sign in</h1>
<p>Your PersonalClaw dashboard is private. Sign in to continue.</p>
<form id='f' autocomplete='on'>
<input id='u' name='username' type='text' placeholder='Username' autocomplete='username'
autocapitalize='none' spellcheck='false' autofocus>
<input id='p' name='password' type='password' placeholder='Password'
autocomplete='current-password'>
<input id='t' name='totp' type='text' placeholder='2FA code' inputmode='numeric'
autocomplete='one-time-code' style='display:none'>
<button id='b' type='submit'>Sign in</button>
</form>
<div class='err' id='e' role='alert' aria-live='polite'></div>
<div class='hint'>
<a href='#' id='toggle'>Use a device code instead</a> &middot;
on your home network you can still use <code>personalclaw token</code>.
</div>
<form id='cf' style='display:none;margin-top:18px'>
<input id='c' name='code' type='text' placeholder='XXXX-XXXX' autocomplete='off'
autocapitalize='characters' spellcheck='false'>
<button id='cb' type='submit'>Pair this device</button>
</form>"""

_LOGIN_SCRIPT = """\
var NEEDS_TOTP = __TOTP__;
var MESSAGES = {
  auth_invalid_credentials: 'Wrong username or password.',
  auth_totp_required: 'Enter the code from your authenticator app.',
  auth_locked_out: 'Too many attempts. Wait a moment and try again.',
  auth_not_enabled: 'Password sign-in is not enabled on this instance.'
};
MESSAGES.auth_origin_not_allowed = 'This address (' + location.origin
  + ") isn't an allowed sign-in origin. Add it to PERSONALCLAW_CORS_ORIGINS"
  + ' (or set dashboard.url) on the gateway, then reload.';
if (NEEDS_TOTP) { document.getElementById('t').style.display = 'block'; }
MESSAGES.auth_enroll_code_invalid = 'That code is not valid, or has already been used.';
document.getElementById('toggle').addEventListener('click', function (ev) {
  ev.preventDefault();
  var pw = document.getElementById('f'), cf = document.getElementById('cf');
  var showingCode = cf.style.display === 'none';
  cf.style.display = showingCode ? 'block' : 'none';
  pw.style.display = showingCode ? 'none' : 'block';
  ev.target.textContent = showingCode ? 'Use a password instead' : 'Use a device code instead';
  document.getElementById('e').textContent = '';
  if (showingCode) { document.getElementById('c').focus(); }
});
document.getElementById('cf').addEventListener('submit', function (ev) {
  ev.preventDefault();
  var btn = document.getElementById('cb'), err = document.getElementById('e');
  btn.disabled = true; err.textContent = '';
  fetch('/api/auth/enroll/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ code: document.getElementById('c').value })
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      return { ok: r.ok, status: r.status, data: d };
    });
  }).then(function (res) {
    if (res.ok) { window.location.href = '/'; return; }
    var code = (res.data && res.data.error && res.data.error.code) || '';
    err.textContent = MESSAGES[code] || ('Pairing failed (HTTP ' + res.status + ').');
    btn.disabled = false;
  }).catch(function () {
    err.textContent = 'Could not reach the gateway.';
    btn.disabled = false;
  });
});
document.getElementById('f').addEventListener('submit', function (ev) {
  ev.preventDefault();
  var btn = document.getElementById('b'), err = document.getElementById('e');
  var body = {
    username: document.getElementById('u').value,
    password: document.getElementById('p').value,
    totp: document.getElementById('t').value
  };
  btn.disabled = true; err.textContent = '';
  fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body)
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      return { ok: r.ok, status: r.status, data: d };
    });
  }).then(function (res) {
    if (res.ok) { window.location.href = '/'; return; }
    // `json_error` emits {"error": {"code", "message"}} (PL-8's one wire envelope). Reading
    // `res.data.error` as a bare string made every MESSAGES lookup miss, so this page only ever
    // said "Sign-in failed." and the auth_totp_required branch below could never fire.
    // An UNMODELLED error (unparseable body, proxy 502) must NOT default to the credentials
    // code: that told a user with a CSRF-rejected origin to fix a password that was never
    // wrong. Report the status instead.
    var code = (res.data && res.data.error && res.data.error.code) || '';
    if (code === 'auth_totp_required') {
      document.getElementById('t').style.display = 'block';
      document.getElementById('t').focus();
    }
    err.textContent = MESSAGES[code] || ('Sign-in failed (HTTP ' + res.status + ').');
    btn.disabled = false;
  }).catch(function () {
    err.textContent = 'Could not reach the gateway.';
    btn.disabled = false;
  });
});
"""

_LOGIN_HTML = page_document(title="Sign in — PersonalClaw", body=_LOGIN_BODY, script=_LOGIN_SCRIPT)
