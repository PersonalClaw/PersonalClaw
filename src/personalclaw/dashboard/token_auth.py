"""Dashboard token authentication.

HMAC-SHA256 token generation, validation, IP binding, consumption
tracking, and aiohttp middleware for channel-gated dashboard access.

``auth_middleware`` is the primary entry point for callers that have an
``AuthConfig``.  It dispatches by ``AuthMode``:

* ``NONE``        — passes all requests through (loopback enforced by
                    ``effective_bind`` before the server starts).
* ``LOCAL_TOKEN`` — delegates to ``token_auth_middleware``.
* ``API_KEY``     — validates ``Authorization: Bearer <key>`` against
                    ``os.environ[auth_cfg.api_key_env]``.
* ``OAUTH2``      — verifies a bearer JWT via :mod:`personalclaw.auth.oidc`.

On any authentication failure the middleware returns HTTP 401 with a
JSON body that does NOT echo request headers, cookies, or tokens.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.config.loader import _DEFAULT_PORT
from personalclaw.dashboard.origin import is_loopback, is_private_network
from personalclaw.sel import sel as _sel_fn

logger = logging.getLogger(__name__)

_SECRET = os.urandom(32)


class TokenStateManager:
    """Thread-safe manager for token authentication state.

    Encapsulates all mutable token state (nonces, IP bindings, consumption)
    with consistent locking. Uses OrderedDict for O(1) nonce eviction.

    Threading model: This class uses threading.Lock (not asyncio.Lock) because
    token operations are called from both async contexts (aiohttp middleware)
    and sync contexts (CLI commands like `personalclaw token`). The lock hold time
    is minimal (dict operations only), so blocking the event loop is negligible.
    """

    def __init__(self, max_concurrent_nonces: int = 5) -> None:
        self._lock = threading.Lock()
        self._max_nonces = max_concurrent_nonces
        # OrderedDict maintains insertion order for O(1) oldest eviction
        self._nonces: OrderedDict[str, float] = OrderedDict()
        self._ip_bindings: dict[str, tuple[str, float]] = {}  # token → (ip, exp)
        self._consumed: dict[str, float] = {}  # token → exp

    def register_nonce(self, nonce: str, expiry: float) -> str | None:
        """Register a nonce with its expiry time, evicting oldest if over limit."""
        with self._lock:
            self._nonces[nonce] = expiry
            self._nonces.move_to_end(nonce)  # Most recent at end
            if len(self._nonces) > self._max_nonces:
                evicted, _ = self._nonces.popitem(last=False)
                return evicted
            return None

    def is_nonce_valid(self, nonce: str) -> tuple[bool, str]:
        """Check if nonce is valid. Returns (valid, reason).

        Deny-by-default: rejects if no nonces registered or nonce not in set.
        Refreshes the nonce's eviction position on each successful check so
        that actively-used sessions are not evicted by newer token grants.
        """
        with self._lock:
            if not self._nonces:
                return False, "no active sessions"
            if nonce not in self._nonces:
                return False, "token superseded"
            self._nonces.move_to_end(nonce)
            return True, ""

    def bind_ip(self, token: str, ip: str, session_exp: float) -> None:
        """Bind a token to a client IP address."""
        with self._lock:
            self._ip_bindings[token] = (ip, session_exp)

    def check_ip(self, token: str, ip: str) -> bool:
        """Check if token is bound to the given IP (or unbound)."""
        with self._lock:
            entry = self._ip_bindings.get(token)
            return entry is None or entry[0] == ip

    def mark_consumed(self, token: str, session_exp: float) -> None:
        """Mark a token as consumed (used for one-time token patterns)."""
        with self._lock:
            self._consumed[token] = session_exp

    def is_consumed(self, token: str) -> bool:
        """Check if a token has been consumed."""
        with self._lock:
            return token in self._consumed

    def try_consume(self, token: str, session_exp: float) -> bool:
        """Atomically mark token consumed if not already.

        Returns True if this call consumed it, False if already consumed.
        """
        with self._lock:
            if token in self._consumed:
                return False
            self._consumed[token] = session_exp
            return True

    def evict_expired(self, now: float) -> None:
        """Remove all expired entries from all state stores."""
        with self._lock:
            # Evict expired IP bindings
            expired_tokens = [t for t, (_, exp) in self._ip_bindings.items() if exp < now]
            for t in expired_tokens:
                self._ip_bindings.pop(t, None)
            # Evict consumed tokens independently using their own expiry
            expired_consumed = [t for t, exp in self._consumed.items() if exp < now]
            for t in expired_consumed:
                self._consumed.pop(t, None)
            # Evict expired nonces
            expired_nonces = [n for n, exp in self._nonces.items() if exp < now]
            for n in expired_nonces:
                self._nonces.pop(n, None)

    def clear_all(self) -> None:
        """Clear all token state (nonces, IP bindings, consumed tokens)."""
        with self._lock:
            self._nonces.clear()
            self._ip_bindings.clear()
            self._consumed.clear()


# Maximum concurrent valid tokens before oldest is evicted
MAX_CONCURRENT_NONCES = 5

# Module-level singleton instance
_state: TokenStateManager = TokenStateManager(max_concurrent_nonces=MAX_CONCURRENT_NONCES)

_BYPASS_PREFIXES = ("/assets/", "/fonts/", "/sprites/", "/vendor/")
_BYPASS_EXACT = {"/claw.svg", "/api/token/local", "/api/healthz"}

# Link click window — URL must be opened within this time.
# 24 hours for local installs; the URL only works on loopback anyway.
LINK_WINDOW_SECS = 24 * 3600
# Maximum session TTL — sessions effectively never expire for local installs.
# The cookie is re-issued on every page load via the session renewal path so
# the clock only matters for completely idle browsers.
MAX_SESSION_TTL_SECS = 365 * 24 * 3600  # 1 year

_403_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' "
    "content='width=device-width,initial-scale=1'><title>Connect — PersonalClaw</title>"
    "<style>"
    # Theme token mirror (dark default; .light via prefers-color-scheme).
    # Standalone gate served before the React bundle, so values are inlined rather
    # than imported from tokens.css — but the palette/shape/motion match NE exactly.
    ":root{{--canvas:#0f0f0f;--surface:#1e1f20;--surface-high:#282a2c;"
    "--ink:#e3e3e3;--ink-low:#9a9b9c;--outline:#444746;"
    "--primary:#9d8bff;--on-primary:#21134f;--primary-emphasis:#b6bdff;"
    "--danger:#f55e57;--radius-card:28px;--radius-field:12px;"
    "--ease:cubic-bezier(0.2,0,0,1);"
    "--font:'Google Sans Flex','Google Sans',system-ui,-apple-system,sans-serif;"
    "--mono:'Google Sans Code',ui-monospace,'SF Mono',monospace}}"
    "*{{margin:0;padding:0;box-sizing:border-box}}"
    "body{{font-family:var(--font);display:flex;align-items:center;"
    "justify-content:center;min-height:100vh;background:var(--canvas);"
    "color:var(--ink);-webkit-font-smoothing:antialiased;overflow:hidden}}"
    # expressive lavender→pink bloom behind the card (NE signature glow)
    "body::before{{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;"
    "background:radial-gradient(60% 55% at 50% 38%,"
    "color-mix(in srgb,var(--primary) 22%,transparent),transparent 70%);"
    "filter:blur(8px)}}"
    ".c{{position:relative;z-index:1;text-align:center;width:100%;max-width:420px;"
    "margin:24px;padding:40px 32px;background:var(--surface);"
    "border:1px solid var(--outline);border-radius:var(--radius-card);"
    "box-shadow:0 16px 40px rgb(0 0 0 / 0.42)}}"
    ".logo{{margin-bottom:20px}}.logo svg{{width:60px;height:60px;display:inline-block}}"
    "h1{{font-size:26px;line-height:1.15;margin-bottom:10px;"
    "font-variation-settings:'wght' 360;letter-spacing:-0.01em}}"
    "p{{color:var(--ink-low);font-size:14px;line-height:1.6;margin-bottom:24px}}"
    "code{{font-family:var(--mono);background:var(--surface-high);padding:2px 7px;"
    "border-radius:6px;color:var(--primary-emphasis);font-size:13px}}"
    "input{{width:100%;padding:13px 15px;border-radius:var(--radius-field);"
    "border:1px solid var(--outline);background:var(--canvas);color:var(--ink);"
    "font-family:var(--font);font-size:14px;margin-bottom:12px;outline:none;"
    "transition:border-color .2s var(--ease),box-shadow .2s var(--ease)}}"
    "input::placeholder{{color:var(--ink-low)}}"
    "input:focus{{border-color:var(--primary);"
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 28%,transparent)}}"
    "button{{width:100%;padding:13px 24px;border-radius:9999px;border:none;"
    "cursor:pointer;background:var(--primary);color:var(--on-primary);"
    "font-family:var(--font);font-size:15px;font-variation-settings:'wght' 600;"
    "transition:background .2s var(--ease),transform .1s var(--ease),"
    "box-shadow .2s var(--ease)}}"
    "button:hover{{background:var(--primary-emphasis);"
    "box-shadow:0 0 28px -6px color-mix(in srgb,var(--primary) 55%,transparent)}}"
    "button:active{{transform:scale(0.985)}}"
    ".err{{color:var(--danger);font-size:13px;margin-top:14px;display:none}}"
    "@media(prefers-color-scheme:light){{:root{{--canvas:#f0f4f8;--surface:#ffffff;"
    "--surface-high:#e6eaef;--ink:#1f1f1f;--ink-low:#5f6368;--outline:#e1e3e1;"
    "--primary:#6a4fd0;--on-primary:#ffffff;--primary-emphasis:#563bbf}}"
    ".c{{box-shadow:0 16px 40px rgb(96 110 130 / 0.22)}}"
    "input:focus{{box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 18%,transparent)}}}}"
    "@media(prefers-reduced-motion:reduce){{*{{transition-duration:.001ms!important}}}}"
    "</style></head><body>"
    "<div class='c'>"
    "<div class='logo'><svg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg' aria-label='PersonalClaw'>"
    "<defs><linearGradient id='cg' x1='0' y1='0' x2='512' y2='512' gradientUnits='userSpaceOnUse'>"
    "<stop stop-color='#8e75b2'/><stop offset='0.45' stop-color='#9d8bff'/>"
    "<stop offset='0.75' stop-color='#c597ff'/><stop offset='1' stop-color='#d8627e'/>"
    "</linearGradient></defs>"
    "<path fill='url(#cg)' d='M256 16C106 76 46 226 46 226c0 45 60 90 90 90 90 0 180-195 135-285l-15-15zm45 15c30 60 0 135 0 135 120 30 120 180 75 330 75-75 90-150 90-210 0-90-15-225-165-255z'/></svg></div>"
    "<h1>403 — {reason}</h1>"
    "<p>Run <code>personalclaw token</code> in your terminal, then paste the URL below.</p>"
    "<input id='u' type='text' placeholder='Paste token URL or raw token…' autofocus>"
    "<button onclick='go()'>Connect</button>"
    "<div class='err' id='e'>Invalid URL</div>"
    "</div>"
    "<script>"
    "function go(){{var v=document.getElementById('u').value.trim();if(!v)return;"
    "var t;try{{var u=new URL(v);t=u.searchParams.get('token')}}"
    "catch(_){{t=v}}if(t){{window.location.href="
    "window.location.protocol+'//'+window.location.host+'?token='+encodeURIComponent(t)}}"
    "else{{document.getElementById('e').style.display='block'}}}}"
    "document.getElementById('u').addEventListener('keydown',"
    "function(e){{if(e.key==='Enter')go()}});"
    "</script>"
    "</body></html>"
)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))


def _sign(payload: bytes) -> str:
    return _b64url_encode(hmac.new(_SECRET, payload, hashlib.sha256).digest())


def generate_token(user_id: str, ttl_seconds: int = 3600, *, app: str = "") -> str:
    """Return ``base64url(payload).base64url(signature)``.

    The token carries two expiry times:
    - ``exp``: link click window (5 minutes) — URL must be opened before this
    - ``session_exp``: cookie session TTL (capped at 20 hours)

    When *app* is provided, the token payload includes ``"app": app`` so
    downstream middleware can extract the verified app identity.

    Up to ``_MAX_CONCURRENT_NONCES`` tokens can be valid concurrently.
    When the limit is exceeded, the oldest nonce is evicted (O(1) via OrderedDict).
    """
    _evict_expired()
    now = time.time()
    nonce = os.urandom(8).hex()
    session_ttl = min(ttl_seconds, MAX_SESSION_TTL_SECS)

    evicted = _state.register_nonce(nonce, now + session_ttl)
    if evicted:
        _sel_fn().log_api_access(
            caller=user_id,
            operation="nonce_evicted",
            outcome="ok",
            source="token_auth",
            resources=f"evicted_nonce={evicted}",
        )

    payload_dict: dict[str, object] = {
        "sub": user_id,
        "exp": now + LINK_WINDOW_SECS,
        "session_exp": now + session_ttl,
        "iat": now,
        "nonce": nonce,
    }
    if app:
        payload_dict["app"] = app
    payload = json.dumps(payload_dict, separators=(",", ":")).encode()
    encoded_payload = _b64url_encode(payload)
    signature = _sign(payload)
    return f"{encoded_payload}.{signature}"


def validate_token(token: str, *, use_session_exp: bool = False) -> tuple[bool, str, str]:
    """Return ``(valid, user_id, reason)``.

    When *use_session_exp* is ``True`` (cookie-based access), validates
    against ``session_exp`` instead of ``exp`` (link click window).
    """
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False, "", "malformed token"
    encoded_payload, sig = parts
    try:
        payload_bytes = _b64url_decode(encoded_payload)
    except Exception:
        return False, "", "invalid encoding"
    expected = _sign(payload_bytes)
    if not hmac.compare_digest(sig, expected):
        return False, "", "invalid signature"
    try:
        data = json.loads(payload_bytes)
    except Exception:
        return False, "", "invalid payload"
    exp_field = "session_exp" if use_session_exp else "exp"
    if time.time() > data.get(exp_field, data.get("exp", 0)):
        return False, "", "token expired"
    # Validate nonce is still in the valid set (not evicted due to limit)
    token_nonce = data.get("nonce", "")
    valid, reason = _state.is_nonce_valid(token_nonce)
    if not valid:
        return False, "", reason
    return True, data.get("sub", ""), ""


def validate_token_with_app(
    token: str, *, use_session_exp: bool = False
) -> tuple[bool, str, str, str]:
    """Return ``(valid, user_id, reason, app_name)``.

    Extends :func:`validate_token` by also extracting the ``app`` field
    from the token payload.  This avoids changing the existing
    ``validate_token`` signature.
    """
    valid, user_id, reason = validate_token(token, use_session_exp=use_session_exp)
    if not valid:
        return False, user_id, reason, ""
    # Extract app from payload
    app_name = ""
    try:
        payload_bytes = _b64url_decode(token.split(".")[0])
        data = json.loads(payload_bytes)
        app_name = data.get("app", "")
    except Exception:
        pass
    return valid, user_id, reason, app_name


def _evict_expired() -> None:
    """Remove token state entries whose session has expired."""
    _state.evict_expired(time.time())


def bind_token_ip(token: str, ip: str, session_exp: float = 0.0) -> None:
    """Bind a token to a client IP for session validation."""
    _state.bind_ip(token, ip, session_exp or time.time() + MAX_SESSION_TTL_SECS)


def check_token_ip(token: str, ip: str) -> bool:
    """Check if token is bound to the given IP (or unbound)."""
    return _state.check_ip(token, ip)


def mark_consumed(token: str, session_exp: float = 0.0) -> None:
    """Mark a token as consumed."""
    _state.mark_consumed(token, session_exp or time.time() + MAX_SESSION_TTL_SECS)


def is_consumed(token: str) -> bool:
    """Check if a token has been consumed."""
    return _state.is_consumed(token)


def try_consume(token: str, session_exp: float = 0.0) -> bool:
    """Atomically consume a token if not already consumed.

    Returns True if this call consumed it, False if already consumed.
    """
    return _state.try_consume(token, session_exp or time.time() + MAX_SESSION_TTL_SECS)


def revoke_all_sessions() -> None:
    """Revoke all active dashboard sessions (also used for test isolation).

    Emits a SEL audit event before clearing state so the revocation is recorded.
    """
    _sel_fn().log_api_access(
        caller="system",
        operation="dashboard_sessions_revoked",
        outcome="ok",
        source="token_auth",
        resources="action=revoke_all",
    )
    _state.clear_all()


def parse_duration(s: str) -> int | None:
    """Parse ``'<int>h'`` or ``'<int>m'`` into seconds, or *None*.

    Returns *None* for invalid input. Caps at ``MAX_SESSION_TTL_SECS``.
    """
    m = re.fullmatch(r"(\d+)(h|m)", s)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    secs = value * 3600 if unit == "h" else value * 60
    return min(secs, MAX_SESSION_TTL_SECS)


def token_auth_middleware(
    *,
    internal_paths: frozenset[str] = frozenset(),
    mixed_internal_paths: frozenset[str] = frozenset(),
    internal_secret: str = "",
    port: int = _DEFAULT_PORT,
    local_only: bool = True,
) -> Callable[..., Any]:
    """Factory returning aiohttp middleware for token-based dashboard auth.

    ALL requests require a valid token — loopback is not exempt, because
    local port forwarders (socat, ssh -R, custom scripts) make remote
    traffic appear as 127.0.0.1, which would otherwise bypass auth entirely.

    *internal_paths* are exact paths that internal processes (mcp-core,
    doctor) call — these require loopback AND a matching
    ``X-Internal-Secret`` header (read from ``~/.personalclaw/.local_secret``).
    Non-loopback access to these paths is always denied.

    *mixed_internal_paths* are paths called by BOTH internal processes
    (loopback + secret) AND the browser (cookie auth).  On non-loopback
    they perform explicit cookie validation (deny-by-default) instead
    of hard-denying, so DCV/SSH-forwarded browsers polling these routes
    (e.g. ``/api/spawn`` every 5s) don't trigger false session-expired
    banners.  Use this for any internal-path that the browser polls.

    """

    def _resolved_client_ip(request: web.Request) -> str:
        """Return the browser's IP, preferring trusted X-Real-IP over remote.

        nginx (or any reverse proxy in the same compose network) sees the
        gateway's container IP as the TCP remote, not the actual client.
        When the TCP remote is itself on a loopback or private subnet
        (i.e. came from a trusted internal proxy) we trust X-Real-IP.
        """
        raw = request.remote or "unknown"
        forwarded = request.headers.get("X-Real-IP", "").strip()
        is_proxy = raw.startswith(
            ("127.", "10.", "172.1", "172.2", "172.3", "192.168.", "::1", "fc", "fd")
        )
        return forwarded if (forwarded and is_proxy) else raw

    def _extract_and_validate_token(
        request: web.Request, _port: int
    ) -> tuple[bool, str, str]:
        """Extract token from query param or cookie and validate it.

        Used by internal-path browser auth (no secret header).  The main
        auth flow has its own extraction with IP-binding and from_cookie
        tracking that this helper intentionally does not replicate.
        """
        cookie_name = f"pc_token_{_port}"
        token = request.query.get("token") or request.cookies.get(cookie_name, "")
        if not token:
            return False, "", "no token"
        return validate_token(token, use_session_exp=True)

    @web.middleware
    async def middleware(request: web.Request, handler: object) -> web.StreamResponse:
        if os.environ.get("PERSONALCLAW_DEV_NO_AUTH") == "1":
            request["user"] = request.get("user") or "dev-local"
            return await handler(request)  # type: ignore[operator]

        # Local-network bypass — opt-in, IP-gated.
        # When PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1, requests from loopback,
        # RFC1918, link-local, or ULA addresses skip token validation.
        # Off by default; intended for trusted home/dev LANs.
        if os.environ.get("PERSONALCLAW_BYPASS_LOCAL_NETWORKS") == "1":
            client_ip = _resolved_client_ip(request)
            if is_private_network(client_ip):
                request["user"] = request.get("user") or f"local-net:{client_ip}"
                _log_auth(request, request["user"], "ok", "local-network bypass")
                return await handler(request)  # type: ignore[operator]

        path = request.path

        # Internal API paths: loopback + secret grants immediate access.
        # If the secret is missing (browser request), fall through to
        # normal cookie auth so dashboard pages can call these routes.
        _matches_strict = internal_paths and (
            path in internal_paths
            or any(path.startswith(p + "/") for p in internal_paths)
        )
        _matches_mixed = mixed_internal_paths and (
            path in mixed_internal_paths
            or any(path.startswith(p + "/") for p in mixed_internal_paths)
        )
        # local_only=False: treat ALL internal paths as mixed (the user has
        # opted into remote access).
        if not local_only and _matches_strict and not _matches_mixed:
            _matches_mixed = True
            _matches_strict = False
        _matches_internal = _matches_strict or _matches_mixed
        if _matches_internal and is_loopback(request.remote or ""):
            _has_secret_header = "X-Internal-Secret" in request.headers
            if _has_secret_header:
                _provided_secret = request.headers["X-Internal-Secret"]
                # Secret header present — validate it strictly
                if not internal_secret:
                    _sel = _sel_fn()
                    _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error="no internal secret configured")
                    _log_auth(request, "internal", "denied", "no internal secret configured")
                    return _deny(request, "Forbidden")
                if hmac.compare_digest(internal_secret, _provided_secret):
                    _sel = _sel_fn()
                    _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="granted", source="token_auth", resources=path)
                    _log_auth(request, "internal", "granted", "")
                    return await handler(request)  # type: ignore[operator]
                # Wrong secret → deny (don't fall through)
                _sel = _sel_fn()
                _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error="wrong secret")
                _log_auth(request, "internal", "denied", "wrong secret")
                return _deny(request, "Forbidden")
            # No secret header (browser request) → verify cookie/query-param auth
            # inline to satisfy deny-by-default: positively confirm auth
            # at the decision point rather than deferring to downstream.
            # NOTE: uses _extract_and_validate_token helper (defined above)
            # for cookie/query-param validation.
            _valid, _uid, _reason = _extract_and_validate_token(request, port)
            if not _valid:
                _sel = _sel_fn()
                _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error=f"cookie auth failed: {_reason}")
                _log_auth(request, "internal", "denied", f"cookie auth failed: {_reason}")
                return _deny(request, "Forbidden")
            _sel = _sel_fn()
            _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="granted", source="token_auth", resources=path, error="cookie auth (no secret header)")
            _log_auth(request, "internal", "granted", f"cookie auth for {_uid}")
            return await handler(request)  # type: ignore[operator]
        elif _matches_internal:
            if _matches_mixed:
                # Mixed paths on non-loopback (DCV/SSH-forwarded browsers):
                # explicit cookie validation, mirroring the loopback
                # no-secret-header branch above.  Deny-by-default —
                # positively confirm auth at this decision point rather
                # than relying on downstream fall-through.
                # If X-Internal-Secret header is present, validate it first
                # (defense-in-depth: wrong secret = deny, even with valid cookie)
                if "X-Internal-Secret" in request.headers:
                    if not internal_secret or not hmac.compare_digest(internal_secret, request.headers["X-Internal-Secret"]):
                        _sel = _sel_fn()
                        _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error="wrong secret (non-loopback mixed)")
                        _log_auth(request, "internal", "denied", "wrong secret (non-loopback mixed)")
                        return _deny(request, "Forbidden")
                _valid, _uid, _reason = _extract_and_validate_token(request, port)
                if not _valid:
                    _sel = _sel_fn()
                    _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error=f"mixed non-loopback cookie auth failed: {_reason}")
                    _log_auth(request, "internal", "denied", f"mixed non-loopback cookie auth failed: {_reason}")
                    return _deny(request, "Forbidden")
                _sel = _sel_fn()
                _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="granted", source="token_auth", resources=path, error="mixed non-loopback cookie auth")
                _log_auth(request, "internal", "granted", f"mixed non-loopback cookie auth for {_uid}")
                return await handler(request)  # type: ignore[operator]
            else:
                # INVARIANT: non-loopback access to strict internal paths is
                # ALWAYS denied.  Do NOT remove this branch — without it,
                # non-loopback requests would silently fall through to
                # normal cookie auth, defeating the machine-to-machine
                # isolation that the internal-secret design provides.
                _sel = _sel_fn()
                _sel.log_api_access(caller=request.remote or "", operation="internal_auth", outcome="denied", source="token_auth", resources=path, error="non-loopback source")
                _log_auth(request, "internal", "denied", "non-loopback source")
                return _deny(request, "Forbidden")

        # Bypass static assets
        if any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await handler(request)  # type: ignore[operator]
        if path in _BYPASS_EXACT:
            return await handler(request)  # type: ignore[operator]
        # Extract token from query param or cookie
        cookie_name = f"pc_token_{port}"
        token = request.query.get("token") or ""
        from_cookie = False
        if not token:
            token = request.cookies.get(cookie_name, "")
            from_cookie = bool(token)

        if not token:
            _log_auth(request, "", "denied", "Token required")
            return _deny(request, "Token required")

        valid, user_id, reason, app_name = validate_token_with_app(token, use_session_exp=from_cookie)
        if not valid:
            _log_auth(request, "", "denied", reason)
            return _deny(request, reason)

        # Prefer X-Real-IP set by a trusted reverse proxy (nginx) over the
        # TCP remote address. See _resolved_client_ip for the trust rules.
        client_ip = _resolved_client_ip(request)

        # IP binding only applies on the initial query-param token exchange.
        # Cookie-based requests skip IP checks — the cookie itself is the
        # credential, and IP validation behind a proxy is unreliable.
        if not from_cookie and not check_token_ip(token, client_ip):
            _log_auth(request, user_id, "denied", "IP mismatch")
            return _deny(request, "IP mismatch")

        # Extract session_exp for cookie and IP binding on first query-param use
        session_exp = 0.0
        if not from_cookie:
            try:
                payload_bytes = _b64url_decode(token.split(".")[0])
                data = json.loads(payload_bytes)
                session_exp = data.get("session_exp", 0.0)
            except Exception:
                pass
            bind_token_ip(token, client_ip, session_exp)

        # Expose authenticated identity to handlers (deny-by-default)
        request["user"] = user_id
        request["app"] = app_name

        # Layered app identity (untrusted-app sandbox, P1): an app's SDK sends the
        # owner cookie (browser-attached) PLUS an app-scoped token — as an
        # ``Authorization: Bearer`` header (fetch) or a ``?app_token=`` query param
        # (the /api/ws handshake, which can't set headers). When present and valid
        # for the SAME owner user, adopt its ``app`` claim so the app-permission
        # middleware + WS event filter scope this request to that app. Owner auth
        # (above) is unchanged — the app token only NARROWS, never widens.
        if not app_name:
            app_token = ""
            _auth = request.headers.get("Authorization", "")
            if _auth.startswith("Bearer "):
                app_token = _auth[7:].strip()
            if not app_token:
                app_token = request.query.get("app_token", "")
            if app_token and app_token != token:
                a_valid, a_user, _reason, a_app = validate_token_with_app(app_token)
                if a_valid and a_app and a_user == user_id:
                    request["app"] = a_app

        # Proceed to handler
        resp = await handler(request)  # type: ignore[operator]

        # Set cookie after handler (needs response object)
        if not from_cookie:
            cookie_max_age = MAX_SESSION_TTL_SECS
            if session_exp:
                remaining = int(session_exp - time.time())
                if 0 < remaining <= MAX_SESSION_TTL_SECS:
                    cookie_max_age = remaining
            resp.set_cookie(
                cookie_name,
                token,
                httponly=True,
                samesite="Lax",
                path="/",
                max_age=cookie_max_age,
            )
            # Clear the non-port-specific cookie so only pc_token_{port} is used.
            resp.set_cookie("pc_token", "", max_age=0, path="/")

        _log_auth(request, user_id, "ok", "")
        return resp  # type: ignore[return-value]

    middleware._is_token_auth = True  # type: ignore[attr-defined]  # sentinel for server.py security gate
    return middleware


def auth_middleware(
    auth_cfg: Any,  # personalclaw.auth.modes.AuthConfig — typed as Any to avoid circular import
    *,
    internal_paths: frozenset[str] = frozenset(),
    mixed_internal_paths: frozenset[str] = frozenset(),
    internal_secret: str = "",
    port: int = _DEFAULT_PORT,
    local_only: bool = True,
) -> Callable[..., Any]:
    """Factory returning aiohttp middleware dispatched by ``auth_cfg.mode``.

    Dispatches to the appropriate auth strategy based on ``AuthMode``:

    * ``NONE``        — passthrough (loopback invariant enforced at bind time).
    * ``LOCAL_TOKEN`` — delegates to :func:`token_auth_middleware`.
    * ``API_KEY``     — validates ``Authorization: Bearer`` against
                        ``os.environ[auth_cfg.api_key_env]``.
    * ``OAUTH2``      — verifies bearer JWT via :mod:`personalclaw.auth.oidc`.

    Failures always return HTTP 401 JSON with a generic message — request
    headers, cookies, and tokens are never echoed.

    The returned middleware carries the ``_is_token_auth = True`` sentinel
    so the ``server.py`` security invariant check still passes for all modes
    except ``NONE`` (where auth is intentionally absent).
    """
    from personalclaw.auth.modes import AuthMode

    mode: AuthMode = auth_cfg.mode

    if mode == AuthMode.NONE:
        @web.middleware
        async def _passthrough(request: web.Request, handler: object) -> web.StreamResponse:
            return await handler(request)  # type: ignore[operator]

        _passthrough._is_token_auth = False  # type: ignore[attr-defined]
        return _passthrough

    if mode == AuthMode.LOCAL_TOKEN:
        return token_auth_middleware(
            internal_paths=internal_paths,
            mixed_internal_paths=mixed_internal_paths,
            internal_secret=internal_secret,
            port=port,
            local_only=local_only,
        )

    if mode == AuthMode.API_KEY:
        api_key_env: str = auth_cfg.api_key_env or ""

        @web.middleware
        async def _api_key_mw(request: web.Request, handler: object) -> web.StreamResponse:
            path = request.path
            # Static assets bypass (same set as LOCAL_TOKEN for consistency)
            if any(path.startswith(p) for p in _BYPASS_PREFIXES):
                return await handler(request)  # type: ignore[operator]
            if path in _BYPASS_EXACT:
                return await handler(request)  # type: ignore[operator]

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                logger.debug("api_key_mw: missing Bearer header for %s", path)
                return _deny_401(request, "Unauthorized")
            provided = auth_header[len("Bearer "):]
            if not api_key_env:
                logger.warning("api_key_mw: api_key_env not configured")
                return _deny_401(request, "Unauthorized")
            expected = os.environ.get(api_key_env, "")
            if not expected:
                logger.warning("api_key_mw: env var %r is not set", api_key_env)
                return _deny_401(request, "Unauthorized")
            if not hmac.compare_digest(provided, expected):
                logger.debug("api_key_mw: invalid API key for %s", path)
                return _deny_401(request, "Unauthorized")
            request["user"] = "api_key"
            return await handler(request)  # type: ignore[operator]

        _api_key_mw._is_token_auth = True  # type: ignore[attr-defined]
        return _api_key_mw

    if mode == AuthMode.OAUTH2:
        oauth2_issuer: str = auth_cfg.oauth2_issuer or ""
        oauth2_audience: str = auth_cfg.oauth2_audience or ""
        oauth2_client_id: str | None = auth_cfg.oauth2_client_id

        from personalclaw.auth.oidc import OidcVerificationError, OidcVerifier

        _verifier = OidcVerifier(
            oauth2_issuer,
            oauth2_audience,
            client_id=oauth2_client_id,
        )

        @web.middleware
        async def _oauth2_mw(request: web.Request, handler: object) -> web.StreamResponse:
            path = request.path
            if any(path.startswith(p) for p in _BYPASS_PREFIXES):
                return await handler(request)  # type: ignore[operator]
            if path in _BYPASS_EXACT:
                return await handler(request)  # type: ignore[operator]

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                logger.debug("oauth2_mw: missing Bearer header for %s", path)
                return _deny_401(request, "Unauthorized")
            token = auth_header[len("Bearer "):]
            try:
                claims = _verifier.verify(token)
            except OidcVerificationError as exc:
                logger.debug("oauth2_mw: JWT verification failed for %s: %s", path, exc)
                return _deny_401(request, "Unauthorized")
            request["user"] = claims.get("sub", "")
            return await handler(request)  # type: ignore[operator]

        _oauth2_mw._is_token_auth = True  # type: ignore[attr-defined]
        return _oauth2_mw

    # Unknown mode — fail closed
    logger.error("auth_middleware: unknown AuthMode %r; denying all requests", mode)

    @web.middleware
    async def _deny_all(request: web.Request, handler: object) -> web.StreamResponse:
        return _deny_401(request, "Unauthorized")

    _deny_all._is_token_auth = True  # type: ignore[attr-defined]
    return _deny_all


def _deny_401(request: web.Request, reason: str) -> web.Response:
    """Return HTTP 401 with a generic JSON body; never echoes request data."""
    return web.json_response({"error": reason}, status=401)


def _deny(request: web.Request, reason: str) -> web.Response:
    headers = {"X-Auth-Required": "true"}
    if request.path.startswith("/api/"):
        return web.json_response({"error": reason}, status=403, headers=headers)
    return web.Response(
        text=_403_HTML.format(reason=reason),
        status=403,
        content_type="text/html",
        headers=headers,
    )


def _log_auth(request: web.Request, user_id: str, outcome: str, error: str) -> None:
    try:
        _sel_fn().log_api_access(
            caller=user_id or request.remote or "unknown",
            operation="dashboard.token_auth",
            outcome=outcome,
            resources=request.path,
            error=error,
        )
    except Exception:
        logger.warning("Failed to log auth event to SEL", exc_info=True)
