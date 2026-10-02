"""Dashboard token authentication.

HMAC-SHA256 token generation, validation, IP binding, consumption
tracking, and aiohttp middleware for channel-gated dashboard access.

``auth_middleware`` is the primary entry point for callers that have an
``AuthConfig``.  It dispatches by ``AuthMode``:

* ``NONE``        — passes all requests through (loopback enforced by
                    ``effective_bind`` before the server starts).
* ``LOCAL_TOKEN`` — delegates to ``token_auth_middleware``.

A refusal never echoes request headers, cookies, or tokens.
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
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from personalclaw.auth.lifetimes import (  # noqa: F401 — duration_words is re-exported
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    MAX_LIFETIME_SECS,
    configured_lifetime,
    duration_words,
    lifetime_seconds,
    too_long,
    when_words,
)
from personalclaw.config.loader import _DEFAULT_PORT
from personalclaw.dashboard.origin import is_loopback, is_private_network
from personalclaw.dashboard.owner_token_url import script_tag as owner_token_script

# The doors a session is issued through and the reasons one ends, re-exported so every mint
# site and sign-out names them from this module. Constants and classes only: the store's
# FUNCTIONS are imported where they are called, so a test that patches one is honoured.
from personalclaw.dashboard.session_store import (  # noqa: F401 — re-exported doors, see above
    END_EXPIRED,
    END_KEY_REPLACED,
    END_LIMIT,
    END_REASONS,
    END_REPLACED,
    END_SIGNED_OUT,
    END_SIGNED_OUT_ELSEWHERE,
    END_SIGNED_OUT_EVERYWHERE,
    END_SIGNED_OUT_OTHERS,
    END_SUPERSEDED,
    ISSUER_APP,
    ISSUER_ENROLL,
    ISSUER_LOGIN,
    ISSUER_PAIR,
    ISSUER_READY,
    ISSUER_STARTUP,
    ISSUER_TOKEN,
    DeviceInfo,
    SessionRecord,
)
from personalclaw.sel import AUDIT_OUTCOME_SUCCESS
from personalclaw.sel import sel as _sel_fn

logger = logging.getLogger(__name__)


def _sel_reason_kwargs(outcome: str, reason: str) -> dict[str, Any]:
    """Route an auth decision's explanatory text to the SEL field it belongs in.

    A denied outcome's text IS the failure, so it belongs in ``error``. A
    granted/ok outcome's text is context for WHY access was allowed, not a
    failure — putting it in ``error`` is what made ``personalclaw security
    events`` print "error: local-network bypass" under an `-> ok` row (#2948).
    Centralizing the routing here (keyed off the same
    :data:`personalclaw.sel.AUDIT_OUTCOME_SUCCESS` vocabulary the audit surface
    itself uses) means a future call site cannot reintroduce the bug just by
    passing a reason string positionally.
    """
    if not reason:
        return {}
    if outcome in AUDIT_OUTCOME_SUCCESS:
        return {"metadata": {"reason": reason}}
    return {"error": reason}


# The signing key is loaded LAZILY, not at import: this module is imported long before
# PERSONALCLAW_HOME is necessarily settled (CLI parsing, test collection), and reading the key
# at import time would bind it to whichever home happened to be current — the same class of bug
# as the SEL singleton pinning its directory to the first caller's home.
#
# `None` means "not yet loaded". `_ephemeral_secret` is the deliberate opt-out for tests and
# `--test-mode`, where persisting a key to a real home would be a side effect.
_SECRET: bytes | None = None
_EPHEMERAL_SECRET: bytes | None = None


def _secret() -> bytes:
    """The HMAC signing key — persistent across restarts (REMOTE-USER-AUTH S1).

    This used to be `os.urandom(32)` at module scope, so **every gateway restart invalidated
    every token**: on a local box you re-ran `personalclaw token`, and off-network you were
    locked out entirely because minting a URL requires being on the machine.
    """
    global _SECRET
    if _EPHEMERAL_SECRET is not None:
        return _EPHEMERAL_SECRET
    if _SECRET is None:
        from personalclaw.dashboard.session_store import load_or_create_key

        _SECRET = load_or_create_key()
    return _SECRET


def use_ephemeral_secret(value: bytes | None = None) -> None:
    """Sign with a fresh in-memory key instead of the persisted one.

    For tests and `--test-mode`, where writing a key into a real home would be a side effect.
    Explicit rather than a swallowed failure, so nothing accidentally runs on an ephemeral key
    and silently re-introduces the logged-out-on-restart bug.

    Pass a key to use it; pass nothing to generate one. Call `use_persistent_secret()` to go
    back to the on-disk key — deliberately a SEPARATE function, because overloading ``None``
    to mean both "generate one for me" and "turn this off" is how the first version of this
    got it backwards, enabling ephemeral mode when a test asked to disable it.
    """
    global _EPHEMERAL_SECRET, _SECRET
    _EPHEMERAL_SECRET = value if value is not None else os.urandom(32)
    _SECRET = None


def use_persistent_secret() -> None:
    """Go back to the on-disk signing key, dropping any ephemeral override."""
    global _EPHEMERAL_SECRET, _SECRET
    _EPHEMERAL_SECRET = None
    _SECRET = None


def reset_secret_cache() -> None:
    """Drop the cached key so the next sign/verify re-reads it (rotation, tests)."""
    global _SECRET
    _SECRET = None


#: Cap on the in-memory `last_seen` throttle map. Larger than the concurrent-nonce cap on
#: purpose: the map also holds nonces that have since been evicted or revoked, and over-trimming
#: it only costs a redundant file read, never a wrong verdict.
_MAX_LAST_SEEN_TRACKED = 64


class TokenStateManager:
    """Thread-safe manager for token authentication state.

    Encapsulates all mutable token state (nonces, IP bindings, consumption) with consistent
    locking. The nonce set is a CACHE of the durable session store, not a policy: it answers
    "is this session live?" without a file read, and it has no limit of its own beyond a
    backstop. How many sessions of each kind may be signed in is decided in ONE place, over
    the store (``session_store.POOL_CAPS``) — it used to be decided here, as five sessions
    in total, which is how a script's sixth token signed the owner's phone out (ledger 255).

    Threading model: This class uses threading.Lock (not asyncio.Lock) because
    token operations are called from both async contexts (aiohttp middleware)
    and sync contexts (CLI commands like `personalclaw token`). The lock hold time
    is minimal (dict operations only), so blocking the event loop is negligible.
    """

    def __init__(self, max_cached_sessions: int) -> None:
        self._lock = threading.Lock()
        self._max_nonces = max_cached_sessions
        # Least recently used first, so the backstop forgets the idlest. Forgetting one is a
        # cache miss (one file read on its next request), never a sign-out.
        self._nonces: OrderedDict[str, float] = OrderedDict()
        self._ip_bindings: dict[str, tuple[str, float]] = {}  # token → (ip, exp)
        self._consumed: dict[str, float] = {}  # token → exp
        # nonce → when we last ATTEMPTED a `last_seen` stamp. In-memory so the common request
        # is a dict lookup and never a file read; see `_touch_last_seen`.
        self._last_seen_touched: dict[str, float] = {}
        # nonce → the (ip, user agent, browser carrier) last noted, so an unchanged client
        # costs a dict lookup rather than a file read; see `note_session_client`.
        self._client_noted: dict[str, tuple[str, str, bool]] = {}

    def register_nonce(self, nonce: str, expiry: float) -> None:
        """Cache a live nonce with its expiry."""
        with self._lock:
            self._nonces[nonce] = expiry
            self._nonces.move_to_end(nonce)
            while len(self._nonces) > self._max_nonces:
                self._nonces.popitem(last=False)

    def is_nonce_valid(self, nonce: str) -> tuple[bool, str]:
        """Check if nonce is valid. Returns (valid, reason).

        Deny-by-default: rejects if the nonce is live in neither the in-memory cache nor the
        durable store.

        **The durable fallback is what makes a persisted signing key useful** (S1). With the
        key alone, a token minted before a restart would verify its signature and then be
        rejected here as "no active sessions" — the user would still be logged out, just with
        a more confusing reason. A signature check without a live session record is not
        enough to authorize; a session record is the second half of the same fix.
        """
        now = time.time()
        with self._lock:
            expiry = self._nonces.get(nonce)
            live = expiry is not None and expiry > now
            if live:
                self._nonces.move_to_end(nonce)
            elif expiry is not None:
                self._nonces.pop(nonce, None)
        if live:
            # Outside the lock: the stamp takes it again, and it must never gate the verdict.
            self._touch_last_seen(nonce)
            return True, ""

        # Not in memory: consult the durable store before refusing. A hit means this process
        # restarted (or never saw the mint), NOT that the session is invalid.
        try:
            from personalclaw.dashboard.session_store import ended_session, load_sessions

            stored = load_sessions()
            ended = ended_session(nonce) if nonce not in stored else None
        except Exception:  # noqa: BLE001 — an unreadable store means "no session", fail closed
            logger.debug("session store unreadable during nonce check", exc_info=True)
            stored, ended = {}, None
        expiry = stored.get(nonce)
        if expiry is None:
            if ended is not None:
                return False, "session expired" if ended.reason == END_EXPIRED else "signed out"
            with self._lock:
                return False, "no active sessions" if not self._nonces else "session not found"
        if expiry <= now:
            return False, "session expired"
        # Adopt it into memory so subsequent checks are lock-only, and so eviction ordering
        # treats a restored session like any other active one.
        with self._lock:
            self._nonces[nonce] = expiry
            self._nonces.move_to_end(nonce)
        self._touch_last_seen(nonce)
        return True, ""

    def _touch_last_seen(self, nonce: str, now: float | None = None) -> None:
        """Stamp ``last_seen`` for an authorized device session. NEVER affects the verdict.

        Called on both success paths of :meth:`is_nonce_valid` — an adopted-from-store session
        is as authorized as an in-memory one, and skipping it would make every device look
        "never seen" for the first request after a restart.

        Two layers of throttle, for two different costs. This in-memory map suppresses the
        **file read**, so a device polling every second costs one dict lookup; the store's own
        staleness check (:data:`LAST_SEEN_THROTTLE_SECS`) suppresses the **write**, and is what
        holds when several processes share one home. The attempt time is recorded BEFORE the
        write, so a store that is failing is retried once a minute rather than once a request.

        Non-device sessions fall through to a no-op inside the store; this layer deliberately
        does not know which sessions carry a device, because that answer lives in the file.

        The ENTIRE body is guarded, import included, because the caller has already decided to
        authorize by the time this runs.
        """
        if not nonce:
            return
        try:
            from personalclaw.dashboard.session_store import (
                LAST_SEEN_THROTTLE_SECS,
                touch_device_last_seen,
            )

            stamp = time.time() if now is None else now
            with self._lock:
                if stamp - self._last_seen_touched.get(nonce, 0.0) < LAST_SEEN_THROTTLE_SECS:
                    return
                self._last_seen_touched[nonce] = stamp
                if len(self._last_seen_touched) > _MAX_LAST_SEEN_TRACKED:
                    # Bounded: a revoked or expired nonce never comes back, so the oldest
                    # attempt is always the safest to forget, and forgetting one costs a
                    # single extra file read rather than correctness.
                    oldest = min(self._last_seen_touched, key=self._last_seen_touched.__getitem__)
                    self._last_seen_touched.pop(oldest, None)
            touch_device_last_seen(nonce, now=stamp)
        except Exception:  # noqa: BLE001 — a failed stamp must not deny a valid session
            logger.debug("could not stamp last_seen for an authorized session", exc_info=True)

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

    def revoke_nonce(self, nonce: str, token: str = "") -> bool:
        """Drop ONE nonce (single-session logout). Returns whether it was present.

        Also drops the token's IP binding and consumed marker so nothing about the dead
        session lingers to be matched against a future token.
        """
        with self._lock:
            existed = self._nonces.pop(nonce, None) is not None
            self._client_noted.pop(nonce, None)
            if token:
                self._ip_bindings.pop(token, None)
                self._consumed.pop(token, None)
            return existed

    def client_already_noted(self, nonce: str, client: tuple[str, str, bool]) -> bool:
        """True when *client* is what was last noted for *nonce*; records it otherwise."""
        with self._lock:
            if self._client_noted.get(nonce) == client:
                return True
            self._client_noted[nonce] = client
            if len(self._client_noted) > _MAX_LAST_SEEN_TRACKED:
                self._client_noted.pop(next(iter(self._client_noted)), None)
            return False

    def clear_all(self) -> None:
        """Clear all token state (nonces, IP bindings, consumed tokens, both throttles)."""
        with self._lock:
            self._nonces.clear()
            self._ip_bindings.clear()
            self._consumed.clear()
            # The throttles are in-memory state like the rest: a restart must forget them, so
            # the first authorized request after one stamps instead of being suppressed by a
            # timestamp from the previous process.
            self._last_seen_touched.clear()
            self._client_noted.clear()


def _cache_bound() -> int:
    from personalclaw.dashboard.session_store import MAX_SESSIONS

    return MAX_SESSIONS


# Module-level singleton instance
_state: TokenStateManager = TokenStateManager(max_cached_sessions=_cache_bound())

_BYPASS_PREFIXES = ("/assets/", "/fonts/", "/sprites/", "/vendor/")
_BYPASS_EXACT = {"/claw.svg", "/api/token/local", "/api/healthz"}
# `/api/logout` authenticates ITSELF exactly like `/api/token/local` above: loopback rail plus a
# constant-time `X-Local-Secret` check inside `api_logout`. It was missing here, so the dashboard
# middleware demanded a session token first and `personalclaw logout` / `auth revoke --all`
# always got 403 — a revoke-everything command that could never revoke anything, while the CLI's
# own fallback reported success. Found in live validation: the "revoked" session kept working.
# Exempting it opens nothing; it lets the request reach the check that actually guards it.
_BYPASS_EXACT.add("/api/logout")
# The inbound MCP surface authenticates ITSELF: it
# carries a dedicated bearer token plus its own loopback rail, so it must bypass
# the DASHBOARD's cookie auth rather than be reachable with a dashboard session.
# Exempting it here does not make it open — inbound/mcp_http.py refuses every
# request that fails enablement, peer, or token checks, GET as well as POST.
_BYPASS_EXACT.add("/mcp")
# The Dialect-5 capture proxy authenticates ITSELF for exactly the same reason
# (EXTERNAL-ACCESS §7.1): each route runs `capture_proxy._admit` — surface enablement,
# then an unconditional loopback rail, then a constant-time bearer check against the
# capture surface token or a registered per-client token — before it reads a body. The
# external agent pointing `OPENAI_BASE_URL` here presents that bearer in `Authorization`
# and has no dashboard cookie, so without these two entries the middleware demanded a
# session token first and both routes were unreachable: an inert surface.
# Enumerated EXACTLY rather than prefix-exempting `/capture/v1/`, so a route added under
# that prefix later does not inherit the exemption without its author choosing it.
# `/capture/import` is the third capture route, and it runs the same `_admit`: it was missing
# here, so the import an agent that cannot be proxied is told to use answered every request
# with the dashboard's sign-in refusal.
_BYPASS_EXACT.update({"/capture/v1/chat/completions", "/capture/v1/messages", "/capture/import"})
# The two other inbound surfaces that authenticate THEMSELVES, and they were not here either, so
# both were unreachable from any client they exist for: every request was refused before its own
# gate ran. `/v1/*` (inbound/openai_dialect.py) and `/a2a/*` (inbound/a2a.py) each run
# `_admit` — surface enablement (404), the peer rail (403), then a constant-time check of the
# surface's own bearer or a registered client's (401) — before they read a body, and they read
# no dashboard cookie. Every route exactly, for the same reason as capture's.
_BYPASS_EXACT.update(
    {
        "/v1/chat/completions",
        "/v1/models",
        "/v1/audio/speech",
        "/v1/audio/transcriptions",
        "/v1/audio/voices",
        "/a2a/agent-card",
        "/a2a/tasks",
    }
)
#: Self-authenticating routes with a path parameter, which no exact entry can name: matched whole,
#: one segment where the route has one. Only `/a2a/tasks/{task_id}` (a task poll), matched the way
#: aiohttp matches ``{task_id}`` itself, so a deeper path or a sibling does not inherit it.
_BYPASS_TEMPLATES: tuple[re.Pattern[str], ...] = (re.compile(r"/a2a/tasks/[^{}/]+"),)

# The login front door. These three MUST be reachable without a
# session, because they are how a remote browser gets one — gating them behind the session
# they exist to mint would be circular. Exempting them does not make them open:
#   * `/login` renders a form and redirects to `/` when login is disabled;
#   * `/api/auth/login` verifies argon2 fail-closed, refuses unless `login_enabled`, checks
#     the CSRF origin, and rate-limits per IP with lockout;
#   * `/api/auth/status` returns two booleans an unauthenticated caller may already infer
#     from being served a login page at all — never the username or whether a credential
#     exists.
# NOTHING else auth-related is exempt: logout, the session view, and setting a password all
# stay behind the normal middleware.
_BYPASS_EXACT.update({"/login", "/api/auth/login", "/api/auth/status"})
# Device enrollment COMPLETION is exempt for the same reason login is: the device redeeming a
# code has no session yet — that is the point. `enroll/start` is NOT exempt (you must already be
# authenticated to mint a code), and completion is origin-checked and rate-limited like login.
_BYPASS_EXACT.add("/api/auth/enroll/complete")
# Device pairing COMPLETION, exempt for the same reason and with the same compensating guards
# (origin check, per-IP lockout, single-use hashed short-TTL code). `pair/start` is NOT exempt:
# minting a pairing code requires already being the owner. See handlers/devices.py.
_BYPASS_EXACT.add("/api/devices/pair/complete")
# The pairing device's PAGE, exempt for the same reason `/login` the page is exempt alongside
# `/api/auth/login`: the browser that must open it has no session, so gating it behind one is
# circular — and without it the URL `pair/start` hands out 403s and tells the joining device to
# run `personalclaw token` in a terminal it does not have. Exempting it opens nothing: the
# document is a CONSTANT with no secret on it (the code is read from `location.search` in the
# browser, never interpolated server-side), and every grant still happens at
# `/api/devices/pair/complete` behind that route's own guards. See handlers/devices.py.
_BYPASS_EXACT.add("/pair")
# The page an authorization server sends the browser back to when the owner signs in to a remote
# MCP server (handlers/mcp.api_mcp_oauth_callback). Exempt because the browser coming back may carry
# no session for this address: the redirect is to 127.0.0.1 (RFC 8252), and a dashboard opened at
# `localhost` holds its cookie for that name only. Exempting it opens nothing: a request is matched
# only to a sign-in the owner started, by its single-use 256-bit `state`, within ten minutes, and
# its code is exchanged only with that sign-in's PKCE verifier, which never leaves the gateway's
# memory.
# Any other request gets a page saying the sign-in expired, and changes nothing. See mcp_oauth.py.
_BYPASS_EXACT.add("/api/mcp/oauth/callback")

# Link click window — URL must be opened within this time.
# 24 hours for local installs; the URL only works on loopback anyway.
LINK_WINDOW_SECS = 24 * 3600

#: Query params this module CONSUMES as credentials, so they are not the handler's arguments.
#:
#: ``?token=`` is the query-token auth path and ``?app_token=`` the app-narrowing token of the
#: ``/api/ws`` handshake (which cannot set a header): both read here (``token`` also in
#: ``handlers/auth.py``) and deliberately **not stripped** from ``request.query``, so they
#: arrive at every handler along with the caller's real parameters. A route with a strict,
#: fail-closed query allowlist must therefore subtract this set before diffing, or it refuses
#: the very callers the auth mode requires — which is exactly what ``GET /api/security/audit``
#: did (issue 2927): a catch-22 where no token meant 403 from auth and a token meant 400
#: ``unknown_filter`` from the handler, so a query-token client could never read the audit
#: trail at all. And anything that FORWARDS a request's query (the app reverse proxy) must
#: drop this set, or it hands the credential to whoever it forwards to.
#:
#: Subtracting is the fix, not widening the route's own allowlist: a credential is not a
#: filter, and it must never reach a SEL query — or an app backend — as one.
RESERVED_QUERY_PARAMS: frozenset[str] = frozenset({"token", "app_token"})

#: The stable wire codes of a refused ``Authorization: Bearer`` (registered in
#: ``http_errors.HTTP_ERROR_CODES``). One code for every Bearer that cannot stand on its own —
#: malformed, expired, revoked, forged, another gateway's, an app's, another surface's — so the
#: answer says nothing about which of those it was.
ERR_BEARER_INVALID = "auth_bearer_invalid"
#: Two different owner credentials on one request (the header and ``?token=``): there is no
#: right one to pick, so neither is.
ERR_CREDENTIAL_CONFLICT = "auth_credential_conflict"

#: The longest any session, link or token may last: 90 days, the most a long-lived credential may
#: live before it is replaced (the reasoning is in :mod:`personalclaw.auth.lifetimes`).
#: A request for longer is REFUSED with a sentence saying so — :func:`mint_session` raises — and
#: never clamped. It used to be a year, reachable by asking (`--ttl 8760h`) and by default from
#: `/api/token/local`. Published to apps as ``personalclaw.sdk.channel.MAX_SESSION_TTL_SECS``.
MAX_SESSION_TTL_SECS = MAX_LIFETIME_SECS

#: What a token lasts when its caller names no lifetime: `personalclaw token`'s documented
#: `--ttl 20h`, and `/api/token/local` without `?ttl=` (which used to hand out a YEAR).
DEFAULT_TOKEN_TTL_SECS = 20 * 3600

#: An app-scoped token's lifetime. Short, so a leaked one has a small blast radius.
APP_TOKEN_TTL_SECS = 3600

#: How long a session cookie outlives its session. The gateway refuses the cookie the moment
#: the signed ``session_exp`` passes — this only keeps the browser SENDING it, because a
#: browser drops a cookie at its Max-Age, and a gateway that never sees the expired cookie
#: cannot tell that device its sign-in ended, or when, or how to sign back in.
SIGNED_OUT_NOTICE_GRACE_SECS = 7 * 86400


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
    "<div class='logo'><svg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg' aria-label='PersonalClaw'>"  # noqa: E501
    "<defs><linearGradient id='cg' x1='0' y1='0' x2='512' y2='512' gradientUnits='userSpaceOnUse'>"
    "<stop stop-color='#8e75b2'/><stop offset='0.45' stop-color='#9d8bff'/>"
    "<stop offset='0.75' stop-color='#c597ff'/><stop offset='1' stop-color='#d8627e'/>"
    "</linearGradient></defs>"
    "<path fill='url(#cg)' d='M256 16C106 76 46 226 46 226c0 45 60 90 90 90 90 0 180-195 135-285l-15-15zm45 15c30 60 0 135 0 135 120 30 120 180 75 330 75-75 90-150 90-210 0-90-15-225-165-255z'/></svg></div>"  # noqa: E501
    "<h1>{heading}</h1>"
    "<p>{explanation}</p>"
    "<input id='u' type='text' placeholder='Paste token URL or raw token…' autofocus>"
    "<button onclick='go()'>Connect</button>"
    "<div class='err' id='e'>Invalid URL</div>"
    "</div>"
    # The owner-token helpers (owner_token_url.js), inlined — this gate renders before any
    # authenticated asset can load. They scrub a refused token out of the URL, and build the
    # Connect target: this origin, the pasted token, and the dashboard route this gate was
    # opened at. It used to be `origin + '?token='`, which dropped the route, so every deep
    # link landed on the dashboard once the token was accepted.
    "{owner_token_script}"
    "<script>"
    "function go(){{var v=document.getElementById('u').value.trim();if(!v)return;"
    "var t=PersonalClawOwnerToken.connectUrl(v);"
    "if(t){{window.location.assign(t)}}"
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
    return _b64url_encode(hmac.new(_secret(), payload, hashlib.sha256).digest())


@dataclass(frozen=True)
class MintedSession:
    """One freshly minted session: the token, and the facts a caller states about it."""

    token: str
    nonce: str
    #: The public handle Settings → Devices lists it under (never the nonce).
    session_id: str
    #: When it was minted — the signed ``iat``.
    issued_at: float
    #: When the session ends — the signed ``session_exp``.
    expires_at: float
    #: Until when the token can be OPENED as a ``?token=`` link to sign a browser in.
    open_until: float
    #: Whether the session reached the durable store (and so the list, and a restart).
    persisted: bool

    @property
    def lifetime_secs(self) -> int:
        return round(self.expires_at - self.issued_at)

    @property
    def open_within_secs(self) -> int:
        return round(self.open_until - self.issued_at)


def mint_session(
    user_id: str,
    ttl_seconds: int,
    *,
    issuer: str,
    device: DeviceInfo | None = None,
    app: str = "",
) -> MintedSession:
    """Mint a session through the door *issuer* names, and record it.

    Every door that hands out a session calls this, naming itself — the gateway's startup link
    and harness token, ``/api/token/local`` (``personalclaw token``, ``personalclaw run``,
    scripts), a password sign-in, a device code, a pairing, and an app's scoped token. The door
    decides the limit the session counts against (``session_store.pool_of``), and *device* is
    the client when the door knows it (a sign-in, a pairing); a link learns its client the
    first time something opens it.

    The token carries two expiry times, both signed:

    * ``exp`` — until when it can be opened as a ``?token=`` link: the link window
      (:data:`LINK_WINDOW_SECS`, 24 hours) or its whole lifetime, whichever is sooner;
    * ``session_exp`` — when the session ends.

    A lifetime longer than :data:`MAX_SESSION_TTL_SECS` (90 days) raises ``ValueError`` whose
    message is the sentence to show whoever asked (``personalclaw.auth.lifetimes.too_long``):
    nothing is minted, and nothing is shortened behind the caller's back.

    Recording the session enforces its kind's limit, and every session that limit signs out
    is dropped from memory and written to the SEL as a sign-out, naming the reason. Every
    sign-in but an app token's is written to the SEL too (an app token only narrows a session
    that already signed in; one row per app request was the log flood of ledger 67).
    Persistence is best-effort — a token whose row could not be written still works for this
    process's lifetime, which is strictly better than refusing to issue one — and a caller for
    which it is not (pairing) reads ``persisted``.
    """
    from personalclaw.dashboard.session_store import new_device_id, remember_session

    if int(ttl_seconds) > MAX_SESSION_TTL_SECS:
        raise ValueError(too_long(int(ttl_seconds)))
    _evict_expired()
    now = time.time()
    nonce = os.urandom(8).hex()
    session_ttl = max(1, int(ttl_seconds))
    expires_at = now + session_ttl
    open_until = now + min(LINK_WINDOW_SECS, session_ttl)
    device = device or DeviceInfo(id=new_device_id(), minted_at=now)
    if not device.minted_at:
        device.minted_at = now

    _state.register_nonce(nonce, expires_at)
    persisted = False
    try:
        remembered = remember_session(nonce, expires_at, issuer=issuer, device=device, app=app)
        persisted = remembered.persisted
        _signed_out(remembered.evicted, END_LIMIT, actor=user_id)
    except Exception:  # noqa: BLE001
        logger.debug("could not persist the minted session", exc_info=True)

    payload_dict: dict[str, object] = {
        "sub": user_id,
        "exp": open_until,
        "session_exp": expires_at,
        "iat": now,
        "nonce": nonce,
    }
    if app:
        payload_dict["app"] = app
    payload = json.dumps(payload_dict, separators=(",", ":")).encode()
    token = f"{_b64url_encode(payload)}.{_sign(payload)}"

    if issuer != ISSUER_APP:
        _audit_session(
            "session_signed_in",
            caller=user_id,
            session_id=device.id,
            issuer=issuer,
            kind=device.kind,
            extra={"expires_at": expires_at, "lifetime_secs": session_ttl},
        )
    return MintedSession(
        token=token,
        nonce=nonce,
        session_id=device.id,
        issued_at=now,
        expires_at=expires_at,
        open_until=open_until,
        persisted=persisted,
    )


def client_of(request: Any) -> DeviceInfo:
    """The client behind *request*, for a door that mints a session in answer to it.

    Named and kinded from the User-Agent (``session_store.describe_user_agent``), placed at
    the connection's address; ``last_seen`` stays 0.0 until it makes an authorized request.
    """
    from personalclaw.dashboard.session_store import (
        describe_user_agent,
        new_device_id,
        sanitize_device_kind,
        sanitize_device_name,
    )

    headers = getattr(request, "headers", None) or {}
    name, kind = describe_user_agent(str(headers.get("User-Agent") or ""))
    return DeviceInfo(
        id=new_device_id(),
        name=sanitize_device_name(name),
        kind=sanitize_device_kind(kind),
        ip=str(getattr(request, "remote", "") or "")[:64],
    )


def generate_token(user_id: str, ttl_seconds: int = 3600, *, app: str = "") -> str:
    """Return ``base64url(payload).base64url(signature)`` — a token for the ``token`` door.

    The published mint (``personalclaw.sdk.channel``): an app's "open the dashboard" link
    and every caller that is not one of the gateway's own doors. With *app*, the token is
    app-scoped (its ``app`` claim narrows a session to that app's permissions). See
    :func:`mint_session` for the two expiry times and the per-kind limit.

    Raises ``ValueError`` for a lifetime over :data:`MAX_SESSION_TTL_SECS` (90 days); its message
    is the sentence to relay to the person who asked, naming the limit and why.
    """
    return mint_session(
        user_id, ttl_seconds, issuer=ISSUER_APP if app else ISSUER_TOKEN, app=app
    ).token


#: What a channel says to anyone but its owner who asks for a dashboard link.
NOT_THE_OWNER_SENTENCE = (
    "Only this channel's owner can get a dashboard link: the link signs in as the owner, so "
    "nobody else is sent one."
)


def owner_sign_in_token(provider: str, user_id: str, ttl_seconds: int = 3600) -> str:
    """A dashboard sign-in token for *user_id*, minted only when that is *provider*'s owner.

    The one mint a channel's "open the dashboard" link uses. A token opens the whole dashboard as
    the owner, whatever id it names, so it may reach nobody else: *user_id* must be the owner id
    this channel keeps (``owner_id_for(provider)``, the id its owner pairing stored). Anyone else
    — an allowed correspondent, a group member, a stranger — is refused with ``ValueError``
    carrying :data:`NOT_THE_OWNER_SENTENCE`, which is what the channel tells them, and so is a
    channel that knows no owner. A lifetime over :data:`MAX_SESSION_TTL_SECS` is refused with
    :func:`generate_token`'s own sentence.
    """
    from personalclaw.config.credentials import owner_id_for

    owner = owner_id_for(provider)
    if not owner or str(user_id or "") != owner:
        raise ValueError(NOT_THE_OWNER_SENTENCE)
    return generate_token(owner, ttl_seconds)


#: ``(user, app)`` → ``(token, nonce, expires_at)``: the app-scoped token each app is using.
_APP_TOKENS: dict[tuple[str, str], tuple[str, str, float]] = {}
_APP_TOKENS_LOCK = threading.Lock()


def app_session_token(user_id: str, app: str) -> tuple[str, float]:
    """``(token, expires_at)`` — the app-scoped token *app* uses for *user_id*, re-minted at
    half its life.

    Its three consumers — the app SDK's mount (``POST /api/apps/{name}/token``), the reverse
    proxy in front of an app's backend, and an agent's call to an app route — each used to
    mint a FRESH token, the proxy on every request. Every mint is a session, so an app page
    making a handful of backend calls signed the owner's other devices out (ledger 255); with
    a limit per app it would instead push out the app's OWN earlier tokens, including the one
    its SDK holds. One live token per user and app, reused while it has more than half its
    hour left, is the same identity and the same narrowing without either.
    """
    now = time.time()
    key = (user_id, app)
    with _APP_TOKENS_LOCK:
        cached = _APP_TOKENS.get(key)
    if cached is not None:
        token, nonce, expires_at = cached
        if expires_at - now > APP_TOKEN_TTL_SECS / 2 and _state.is_nonce_valid(nonce)[0]:
            return token, expires_at
    minted = mint_session(user_id, APP_TOKEN_TTL_SECS, issuer=ISSUER_APP, app=app)
    with _APP_TOKENS_LOCK:
        _APP_TOKENS[key] = (minted.token, minted.nonce, minted.expires_at)
    return minted.token, minted.expires_at


def browser_session_ttl(auth_cfg: Any = None) -> int:
    """How long a BROWSER sign-in lasts: ``auth.session_ttl``, 30 days by default.

    One answer for every door a browser signs in through — a password, a device code, a
    pairing, and the link the gateway prints and opens at startup (and its harness twin), which
    used to hard-code 30 days and ignore the setting.
    """
    if auth_cfg is None:
        try:
            from personalclaw.config.loader import AppConfig

            auth_cfg = AppConfig.load().auth
        except Exception:  # noqa: BLE001 — an unreadable config gets the documented default
            logger.debug("could not read auth.session_ttl", exc_info=True)
            return DEFAULT_BROWSER_SESSION_TTL_SECS
    configured = str(getattr(auth_cfg, "session_ttl", "") or "")
    if not configured:
        return DEFAULT_BROWSER_SESSION_TTL_SECS
    return parse_config_duration(configured, default_secs=DEFAULT_BROWSER_SESSION_TTL_SECS)


def _session_deadline(claims: dict[str, Any]) -> float:
    """When the session *claims* describe ends: its signed ``session_exp``, and never later than
    :data:`MAX_SESSION_TTL_SECS` after its ``iat``.

    The second half is what ends a token minted with a longer lifetime before the 90-day limit
    existed (ledger 285) — a year-long ``/api/token/local`` token, say — 90 days after it was
    issued, rather than honouring it for the rest of its year.
    """
    end = float(claims.get("session_exp", claims.get("exp", 0)) or 0)
    issued = float(claims.get("iat") or 0)
    return min(end, issued + MAX_SESSION_TTL_SECS) if issued else end


def validate_token(token: str, *, use_session_exp: bool = False) -> tuple[bool, str, str]:
    """Return ``(valid, user_id, reason)``.

    When *use_session_exp* is ``True`` (cookie-based access), validates
    against ``session_exp`` instead of ``exp`` (link click window). Either way no session is
    honoured past :data:`MAX_SESSION_TTL_SECS` from when it was issued (:func:`_session_deadline`).
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
    # A signature is base64url, so a non-ASCII one is simply wrong — and it must be refused as
    # one: `compare_digest` RAISES on a non-ASCII str, which turned a forged credential into a
    # 500 from the auth middleware instead of a refusal.
    if not sig.isascii() or not hmac.compare_digest(sig, expected):
        return False, "", "invalid signature"
    try:
        data = json.loads(payload_bytes)
    except Exception:
        return False, "", "invalid payload"
    if not isinstance(data, dict):
        return False, "", "invalid payload"
    session_exp = _session_deadline(data)
    # A link (`?token=`) must be opened inside its click window AND inside its session: this
    # checked only the 24-hour window, so a 1-hour token opened as a link after its hour still
    # signed a browser in for as long as this process remembered the nonce.
    deadline = session_exp if use_session_exp else min(data.get("exp", 0), session_exp)
    if time.time() > deadline:
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


@dataclass(frozen=True)
class SignedOutNotice:
    """What a device whose request carries no usable sign-in is told: why, and how to sign in.

    ``message`` is product copy composed here, where the facts are; every surface that shows it
    (the SPA, the paste-token gate, the sign-in page, the desktop app's Gateways window) shows it
    verbatim rather than keeping a second wording of its own. ``heading`` is what the gateway's
    own page titles it.

    WHY-and-WHEN (``session_signed_out`` / ``session_expired``) is only ever composed for a token
    whose SIGNATURE verified, so it goes only to a device that really held the session. Anything
    else — no token, garbage, a token another key signed — gets the one ``session_required``
    sentence, identical for all of them, so a forger learns nothing a stranger does not.
    """

    code: str  # the wire code: `session_signed_out`, `session_expired` or `session_required`
    reason: str  # an `END_*` reason, or `ended` / `link_expired` / `link_used` / `not_signed_in`
    at: float  # when it ended (for the refusals with no ending: when it was refused)
    message: str
    heading: str = "You’re signed out"


#: The registered wire codes of a sign-in refusal (``http_errors.HTTP_ERROR_CODES``).
ERR_SESSION_SIGNED_OUT = "session_signed_out"
ERR_SESSION_EXPIRED = "session_expired"
ERR_SESSION_REQUIRED = "session_required"

#: What each limit counts, as the sentence names it.
_POOL_NOUNS = {"device": "paired devices", "browser": "browsers", "token": "tokens"}

#: The `detail.reason` of the refusals that are not an ending the store recorded.
REFUSED_NOT_SIGNED_IN = "not_signed_in"
REFUSED_LINK_EXPIRED = "link_expired"
REFUSED_LINK_USED = "link_used"
REFUSED_ENDED = "ended"


#: How a device that has no sign-in gets one, when nothing is known about what it was.
_PAIR_THIS_DEVICE = "pair this device from Settings → Devices on a device that is signed in"


def _how_to_sign_back_in(via: str, kind: str = "") -> str:
    """The door back in for a session that came through *via* on a *kind* of device, given what
    this gateway offers. A door nobody recorded (*via* empty) names both the link and pairing."""
    password = _login_offered()
    if via in (ISSUER_PAIR, ISSUER_ENROLL):
        if kind == "desktop":
            # The desktop app redeems a pairing link in its own Gateways window, not in a
            # browser — "choose Pair a device" alone left it holding a code with nowhere to go.
            pair = (
                "To sign it back in, choose Pair a device in Settings → Devices on a device that "
                "is still signed in, then paste the pairing link into this app’s Gateway → "
                "Gateways… window"
            )
        else:
            pair = (
                "To sign it back in, open Settings → Devices on a device that is still signed "
                "in and choose Pair a device"
            )
        return f"{pair}, or sign in with your password." if password else f"{pair}."
    if password:
        return "Sign in again with your password."
    if not via:
        return (
            "To sign back in, run `personalclaw token` on the computer running PersonalClaw and "
            f"open the link it prints here, or {_PAIR_THIS_DEVICE}."
        )
    return (
        "To sign back in, run `personalclaw token` on the computer running PersonalClaw and "
        "open the link it prints here."
    )


def _how_to_sign_in_with_a_new_link() -> str:
    """What a device holding a link that cannot sign it in does instead."""
    if _login_offered():
        return (
            "Sign in with your password, or run `personalclaw token` on the computer running "
            "PersonalClaw for a new link."
        )
    return (
        "To sign in, run `personalclaw token` on the computer running PersonalClaw and open the "
        "new link it prints."
    )


def _verified_claims(token: str) -> dict[str, Any] | None:
    """*token*'s claims when THIS gateway signed it — with its key, or with the key its last
    rotation replaced — else *None*. Never raises.

    For EXPLAINING a refusal only (:func:`signed_out_notice`, :func:`refusal_notice`), which is
    why the replaced key may answer here: every session it signed was ended when it was replaced
    (``session_store.retire_key``), so recognising one of its tokens can only ever find out why
    that session ended. Nothing that admits a request calls this.
    """
    parts = (token or "").split(".", 1)
    if len(parts) != 2:
        return None
    encoded_payload, sig = parts
    try:
        payload_bytes = _b64url_decode(encoded_payload)
        if not sig.isascii():
            return None
        if not hmac.compare_digest(sig, _sign(payload_bytes)) and not _signed_by_replaced_key(
            payload_bytes, sig
        ):
            return None
        data = json.loads(payload_bytes)
    except Exception:  # noqa: BLE001 — anything unverifiable gets no explanation
        return None
    return data if isinstance(data, dict) else None


def _signed_by_replaced_key(payload: bytes, sig: str) -> bool:
    """Whether *sig* is the signature the key the last rotation replaced gave *payload*."""
    from personalclaw.dashboard.session_store import retired_key

    key = retired_key()
    if not key:
        return False
    expected = _b64url_encode(hmac.new(key, payload, hashlib.sha256).digest())
    return hmac.compare_digest(sig, expected)


def not_signed_in_notice() -> SignedOutNotice:
    """The one sentence for a request with no usable sign-in: none presented, garbage, or a
    token this gateway did not sign. Identical for all three, on purpose."""
    if _login_offered():
        how = f"Sign in with your password, or {_PAIR_THIS_DEVICE}."
    else:
        how = (
            "To sign in, run `personalclaw token` on the computer running PersonalClaw and open "
            f"the link it prints here, or {_PAIR_THIS_DEVICE}."
        )
    return SignedOutNotice(
        code=ERR_SESSION_REQUIRED,
        reason=REFUSED_NOT_SIGNED_IN,
        at=time.time(),
        message=f"This device isn’t signed in to PersonalClaw. {how}",
        heading="Sign in to PersonalClaw",
    )


def link_used_notice() -> SignedOutNotice:
    """A sign-in link already bound to another device's address, opened here. The other
    device's address is not this one's to learn."""
    return SignedOutNotice(
        code=ERR_SESSION_REQUIRED,
        reason=REFUSED_LINK_USED,
        at=time.time(),
        message=(
            "This sign-in link has already signed in another device, so it can’t sign in this "
            f"one. {_how_to_sign_in_with_a_new_link()}"
        ),
        heading="This link can’t sign you in",
    )


def _ended_notice(claims: dict[str, Any]) -> SignedOutNotice | None:
    """Why the verified session *claims* name is over — signed out, or expired — or *None*
    when neither is known."""
    from personalclaw.dashboard.session_store import POOL_CAPS, ended_session

    nonce = str(claims.get("nonce") or "")
    ended = ended_session(nonce)
    via = ended.issuer if ended is not None else ""
    kind = ended.kind if ended is not None else ""
    device = "This browser" if kind == "browser" else "This device"
    if ended is not None and ended.reason != END_EXPIRED:
        when = when_words(ended.at)
        pool = _pool_of_ended(ended)
        why = {
            END_SIGNED_OUT: f"{device} signed out {when}.",
            END_SIGNED_OUT_ELSEWHERE: (
                f"{device} was signed out {when} from Settings → Devices on another device."
            ),
            END_SIGNED_OUT_OTHERS: (
                f"{device} was signed out {when}, when another device chose "
                "“Sign out all other devices”."
            ),
            END_SIGNED_OUT_EVERYWHERE: (
                f"Every device was signed out {when}, from the computer running PersonalClaw."
            ),
            END_KEY_REPLACED: (
                f"Every device was signed out {when}, when the key PersonalClaw signs sign-ins "
                "with was replaced."
            ),
            END_REPLACED: (
                f"This browser signed in again {when} with a newer link, which ended this "
                "earlier sign-in."
            ),
            END_SUPERSEDED: (
                f"This sign-in link was replaced {when}, when PersonalClaw started again and "
                "made a new one."
            ),
            END_LIMIT: (
                f"{device} was signed out {when} because more than {POOL_CAPS.get(pool, 0)} "
                f"{_POOL_NOUNS.get(pool, 'sessions')} were signed in, and it was the one used "
                "least recently."
            ),
        }[ended.reason]
        return SignedOutNotice(
            code=ERR_SESSION_SIGNED_OUT,
            reason=ended.reason,
            at=ended.at,
            message=f"{why} {_how_to_sign_back_in(via, kind)}",
        )
    deadline = _session_deadline(claims)
    if deadline and deadline <= time.time():
        issued = float(claims.get("iat") or deadline)
        return SignedOutNotice(
            code=ERR_SESSION_EXPIRED,
            reason=END_EXPIRED,
            at=deadline,
            message=(
                f"Your sign-in on this device lasted {duration_words(deadline - issued)} and "
                f"ended {when_words(deadline)}. {_how_to_sign_back_in(via, kind)}"
            ),
        )
    return None


def signed_out_notice(token: str) -> SignedOutNotice | None:
    """Why the session *token* names is no longer signed in — or *None* when that is not
    something this token's holder may be told.

    *None* for anything that is not a genuine token of this gateway (a bad signature, a
    malformed or empty string) and for a session nobody remembers ending. The sign-in page
    uses this: it explains an ending, and greets everyone else as the sign-in it is.
    """
    claims = _verified_claims(token)
    return _ended_notice(claims) if claims is not None else None


def refusal_notice(token: str, *, source: str) -> SignedOutNotice:
    """What a browser carrier (*source*: the ``?token=`` link or the cookie) whose *token* was
    refused is told — always something.

    In order: an ending the store recorded or an expiry (:func:`signed_out_notice`); a genuine
    session nobody remembers ending (``ended``); a genuine link past its window, while its
    session lives on (``link_expired``); and for anything this gateway did not sign, the same
    sentence as presenting nothing at all (:func:`not_signed_in_notice`).
    """
    claims = _verified_claims(token)
    if claims is None:
        return not_signed_in_notice()
    notice = _ended_notice(claims)
    if notice is not None:
        return notice
    from personalclaw.dashboard.session_store import load_sessions

    now = time.time()
    if str(claims.get("nonce") or "") not in load_sessions():
        return SignedOutNotice(
            code=ERR_SESSION_SIGNED_OUT,
            reason=REFUSED_ENDED,
            at=now,
            message=f"This device’s sign-in has ended. {_how_to_sign_back_in('')}",
        )
    open_until = float(claims.get("exp") or 0.0)
    if source == "query" and open_until and open_until <= now:
        return SignedOutNotice(
            code=ERR_SESSION_EXPIRED,
            reason=REFUSED_LINK_EXPIRED,
            at=open_until,
            message=(
                f"This sign-in link could be opened until {when_words(open_until)}, and that "
                f"has passed. {_how_to_sign_in_with_a_new_link()}"
            ),
            heading="This link has expired",
        )
    return not_signed_in_notice()


def _pool_of_ended(ended: Any) -> str:
    """The limit an ended session counted against, from what its ending recorded."""
    from personalclaw.dashboard.session_store import pool_of

    record = SessionRecord(
        expiry=ended.expiry, issuer=ended.issuer, device=DeviceInfo(id="", kind=ended.kind)
    )
    return pool_of(record)


def token_nonce(token: str) -> str:
    """The ``nonce`` claim of *token*, or ``""`` when it cannot be read.

    **It NAMES a session; it never proves one.** The payload is decoded WITHOUT checking the
    signature, so a caller passes a token it has validated (the middleware, which has just run
    :func:`validate_token_with_app` over the same string), minted itself (device pairing, which
    annotates the fresh session's row) or is only revoking (:func:`revoke_token`, behind the
    owner's own authentication). Re-verifying here would be a second copy of the validation
    rules, which is worse than stating the contract — and every reader of the claim outside
    :func:`validate_token` goes through here, so there is no second decoder to drift from it.

    Returning ``""`` on any malformed input is deliberate: every consumer treats an empty nonce
    as "this session cannot be identified" and falls back to the stricter branch, so a decode
    failure fails CLOSED rather than producing a nonce that matches nothing by accident.
    """
    try:
        data = json.loads(_b64url_decode(token.split(".")[0]))
    except Exception:  # noqa: BLE001 — an unreadable payload is an unidentifiable session
        return ""
    nonce = data.get("nonce", "") if isinstance(data, dict) else ""
    return nonce if isinstance(nonce, str) else ""


def _issued_at(token: str) -> float:
    """The ``iat`` claim of a token the caller has VALIDATED (like :func:`token_nonce`), or 0."""
    try:
        data = json.loads(_b64url_decode(token.split(".")[0]))
    except Exception:  # noqa: BLE001 — an unreadable payload has no issue time to adopt
        return 0.0
    try:
        return float(data.get("iat") or 0.0) if isinstance(data, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _session_end(token: str) -> float:
    """When the session of a token the caller has VALIDATED ends (:func:`_session_deadline`), or
    0 when it cannot be read."""
    try:
        data = json.loads(_b64url_decode(token.split(".")[0]))
        return _session_deadline(data) if isinstance(data, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bearer_credential(request: Any) -> tuple[bool, str]:
    """``(presented, token)`` for the request's ``Authorization: Bearer`` header.

    Only the Bearer scheme is this module's. Another scheme — a reverse proxy's own ``Basic``
    login, riding the same header beside the session cookie — is someone else's credential:
    ``(False, "")``, ignored rather than refused, or every request through such a proxy would
    be. A Bearer with no token, or with more than one (``Bearer a b``, ``Bearer a,b``), is
    presented but unusable: ``(True, "")``.
    """
    raw = request.headers.get("Authorization")
    if not isinstance(raw, str):
        return False, ""
    scheme, _, credential = raw.strip().partition(" ")
    if scheme.lower() != "bearer":
        return False, ""
    credential = credential.strip()
    if not credential or any(ch.isspace() or ch == "," for ch in credential):
        return True, ""
    return True, credential


@dataclass(frozen=True)
class _Credentials:
    """The credential that authorizes a request — chosen ONCE, by one rule, for every path.

    ``token`` is the primary credential and never leaves this module; ``source`` says which
    carrier brought it (``query`` / ``header`` / ``cookie``); ``app`` is the app the request is
    scoped to, from the token's own claim or an app token layered over an owner session. A
    refusal carries either ``error_code`` (a stable wire code, for the Bearer's refusals) or
    ``reason`` (the prose reason the audit row records), and — for a browser carrier — the
    ``presented`` credential, so the refusal can tell the device why (:func:`refusal_notice`).
    That sentence is composed only when a refusal is actually answered: most calls here are
    the local-network bypass naming a session, where composing one per request would read the
    store and the config for nothing.
    """

    token: str = ""
    source: str = ""
    user_id: str = ""
    app: str = ""
    error_code: str = ""
    reason: str = ""
    presented: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.token) and not self.error_code and not self.reason

    @property
    def failure(self) -> str:
        """What the audit row says about a refusal: the stable code, else the reason."""
        return self.error_code or self.reason


def _select_request_credentials(request: Any, port: int) -> _Credentials:
    """The one answer to "which credential authorizes this request".

    The owner token has THREE carriers and one meaning. ``?token=`` is the browser entry link:
    the middleware binds it to the first address it sees and exchanges it for the HttpOnly
    ``pc_token_<port>`` cookie, which every later browser request carries. ``Authorization:
    Bearer`` is for everything that is not a browser — the CLI, a script, a native client —
    and is stateless: it sets no cookie and binds no address, and it keeps the token out of
    the URL, where it would land in shell history, the process list and every log that
    records a request line. The header accepts exactly the sessions the cookie accepts,
    judged by the same rules (signature, session lifetime, a live nonce), so it is a second
    carrier for the same credential, not a second kind of credential.

    Precedence, deliberately explicit:

    * ``?token=`` first — the browser exchange keeps its semantics. A DIFFERENT owner token
      in the header beside it is two sessions on one request, and there is no right one to
      pick: refused, ``auth_credential_conflict``.
    * then an owner token in the header (a valid session token with no ``app`` claim);
    * then the cookie.
    * A Bearer with none of those to stand on is refused with the one stable
      ``auth_bearer_invalid`` — an app token included, because in the header an app token
      only NARROWS an owner session and never stands in for one. The Bearer's refusal stays
      that one code on purpose, with no signed-out sentence: it says nothing about which
      failure it was. The browser carriers (the link and the cookie) get the sentence.

    Layered app identity (the untrusted-app sandbox, P1) then applies unchanged: an app's SDK
    sends the owner cookie PLUS its own app-scoped token — in the Bearer header (fetch) or as
    ``?app_token=`` (the ``/api/ws`` handshake, which cannot set headers) — and the token's
    ``app`` claim is adopted only when it validates for the SAME user, so it can only narrow
    the request. One that does not validate is not adopted, exactly as before this rule
    existed (a DISCOVERY recorded with the change: that fall-through keeps the owner's reach).
    """
    query_token = request.query.get("token") or ""
    cookie_token = request.cookies.get(f"pc_token_{port}", "")
    presented, bearer = _bearer_credential(request)
    # Judged once, against the SESSION lifetime: a header is a session carrier like the cookie
    # (``exp`` is only the entry link's click window, which is the query token's).
    checked = validate_token_with_app(bearer, use_session_exp=True) if bearer else None
    owner_bearer = checked is not None and checked[0] and not checked[3]

    if query_token:
        # Bytes, not str: `compare_digest` raises on a non-ASCII str, and the query is anyone's.
        if owner_bearer and not hmac.compare_digest(query_token.encode(), bearer.encode()):
            return _Credentials(error_code=ERR_CREDENTIAL_CONFLICT)
        token, source = query_token, "query"
    elif owner_bearer:
        token, source = bearer, "header"
    elif cookie_token:
        token, source = cookie_token, "cookie"
    elif presented:
        return _Credentials(error_code=ERR_BEARER_INVALID)
    else:
        return _Credentials(reason="Token required", source="none")

    if source == "header" and checked is not None:
        valid, user_id, reason, app = checked
    else:
        valid, user_id, reason, app = validate_token_with_app(
            token, use_session_exp=source != "query"
        )
    if not valid:
        return _Credentials(reason=reason, source=source, presented=token)

    if not app:
        layered = (bearer if source != "header" else "") or request.query.get("app_token", "")
        if layered and layered != token:
            a_valid, a_user, _reason, a_app = validate_token_with_app(layered)
            if a_valid and a_app and a_user == user_id:
                app = a_app
    return _Credentials(token=token, source=source, user_id=user_id, app=app)


def presented_session_nonce(request: Any, port: int) -> str:
    """The nonce of the VALID session token *request* presents, or ``""``.

    For the auth paths that GRANT WITHOUT CONSULTING the session token — ``AuthMode.NONE``'s
    dev middleware and the IP-gated local-network bypass. Both hand the request straight to
    the handler, so neither reaches the line in :func:`token_auth_middleware` that records
    ``session_nonce``. The request is authorized, and yet the session behind it is anonymous.

    That silently disables **every** paired-device distinction in those modes, which is the
    same shape of hole the ``app`` claim had before none-mode learned to adopt it: pairing
    succeeds, ``GET /api/devices`` lists the device, and then ``POST /api/browse/connector``
    answers ``browse_connector_unpaired`` with that device's own cookie, because
    ``_paired_device`` has no nonce to look up. ``/api/ws``'s origin-less upgrade (CA-7) reads
    the same key and fails closed for the same reason.

    Naming the session cannot WIDEN either path — a caller these modes admit already holds
    unrestricted owner reach — it only lets a handler tell *which kind of client* is calling.
    And it names only a session the token proves: the credential is chosen and validated by
    the SAME :func:`_select_request_credentials` the strict path uses — so a session carried in
    the ``Authorization`` header is named exactly like one carried in the cookie — and an
    absent, forged, expired or conflicting credential yields ``""``, so every consumer keeps
    its stricter branch. Validation also stamps the device's ``last_seen``, and the client is
    noted (:func:`note_session_client`), exactly as on the authenticated path.
    """
    credentials = _select_request_credentials(request, port)
    if not credentials.valid:
        return ""
    nonce = token_nonce(credentials.token)
    note_session_client(nonce, request, credentials.source)
    return nonce


def note_session_client(
    nonce: str, request: Any, source: str, *, ip: str = "", issued_at: float = 0.0
) -> None:
    """Record where the client of an AUTHORIZED request was seen from, and what it is.

    What Settings → Devices shows as "where" and as the device's kind and name, and what
    moves a link from the token limit to the browser limit once a browser opens it. The
    common request — the same client again — is a dict lookup; the store is read only when
    something about the client changed. *ip* is the address the middleware resolved (a
    trusted proxy's ``X-Real-IP``); without one, the connection's. *issued_at* is the token's
    signed ``iat``, which a row from before sign-in times were recorded adopts (so its listed
    end is 90 days from it). Never raises and never affects the verdict.
    """
    if not nonce:
        return
    try:
        ip = ip or str(getattr(request, "remote", "") or "")
        headers = getattr(request, "headers", None) or {}
        user_agent = str(headers.get("User-Agent") or "")[:256]
        client = (ip, user_agent, source in ("query", "cookie"))
        if _state.client_already_noted(nonce, client):
            return
        from personalclaw.dashboard.session_store import note_client

        note = note_client(
            nonce,
            ip=ip,
            user_agent=user_agent,
            browser_carrier=client[2],
            issued_at=issued_at,
        )
        if note.evicted:
            _signed_out(note.evicted, END_LIMIT, actor="system")
    except Exception:  # noqa: BLE001 — a note must never deny an authorized request
        logger.debug("could not note the client of an authorized session", exc_info=True)


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


def _audit_session(
    operation: str,
    *,
    caller: str,
    session_id: str,
    issuer: str,
    kind: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """One SEL row for a session's start or end, in the one shape every credential shares
    (``auth.signins``). Never the nonce or the token — the public handle is what names it, the
    same one Settings → Devices shows. Never raises."""
    from personalclaw.auth import signins

    signins.record(
        operation,
        caller=caller,
        session_id=session_id,
        issuer=issuer,
        kind=kind,
        source="token_auth",
        extra=extra,
    )


def _signed_out(ended: Any, reason: str, *, actor: str) -> int:
    """Finish ending *ended* (``(nonce, record)`` pairs the store already ended): drop each
    from memory, record each in the SEL — except app tokens, which are not sign-ins — and end
    the pushes each one registered, so a device signed out is not woken again."""
    count = 0
    ended_rows: set[str] = set()
    for nonce, record in ended:
        _state.revoke_nonce(nonce)
        count += 1
        ended_rows.add(record.device.id)
        if record.issuer == ISSUER_APP:
            continue
        _audit_session(
            "session_signed_out",
            caller=actor,
            session_id=record.device.id,
            issuer=record.issuer,
            kind=record.device.kind,
            extra={"reason": reason},
        )
    try:
        from personalclaw import push

        push.revoke_for_sessions(ended_rows, reason=reason)
    except Exception:  # noqa: BLE001 — the sign-out stands; say its pushes could not be ended
        logger.warning("could not end the pushes of the signed-out devices", exc_info=True)
    return count


def _keep_the_browser_s_pushes(previous_nonce: str, nonce: str) -> None:
    """A browser that swaps its sign-in for a newer link is the same browser: the pushes it
    turned on move to its new sign-in, before the old one is signed out and ends them."""
    try:
        from personalclaw import push
        from personalclaw.dashboard.session_store import load_session_records

        records = load_session_records()
        old, new = records.get(previous_nonce), records.get(nonce)
        if old is not None and new is not None:
            push.move_to_session(old.device.id, new.device.id, until=new.expiry)
    except Exception:  # noqa: BLE001 — the browser turns push on again; say why it has to
        logger.warning("could not move the browser's pushes to its new sign-in", exc_info=True)


def sign_out(nonces: list[str], reason: str, *, actor: str) -> int:
    """End each of *nonces*, remembering *reason* for the device that held it. Returns how
    many were live.

    THE one way a session ends early — a device signing itself out, the owner signing one out
    (or all the others) from Settings → Devices, ``personalclaw logout``, the per-kind limit,
    a browser that signed in again, and a startup link the next start replaced. Each is dropped
    from memory **and** from the durable store: memory alone lets the session return at the
    next restart, the store alone lets it keep working until then. And each is remembered with
    its reason, which is what lets the device that held it be told why on its next request
    instead of reading a bare 403.
    """
    if reason not in END_REASONS:
        raise ValueError(f"unknown end reason {reason!r}")
    from personalclaw.dashboard.session_store import end_sessions

    live = [n for n in nonces if n]
    try:
        ended = end_sessions(live, reason)
    except Exception:  # noqa: BLE001 — memory must still forget them; say that it could not
        logger.warning("could not end the sessions in the durable store", exc_info=True)
        for nonce in live:
            _state.revoke_nonce(nonce)
        return 0
    return _signed_out(ended, reason, actor=actor)


def retire_startup_links() -> int:
    """End every startup link no browser has opened. Returns how many. Called at each start.

    A start makes its own link (when it has a terminal to show it or a browser to open it in),
    so the link of the start before it no longer leads anywhere new. Left live, each start of a
    service that keeps being restarted added one more 30-day owner session that nobody held,
    each listed in Settings → Devices. A link a browser opened is that browser's sign-in, and
    stays: the browser that opens the next link swaps its sign-in for that one by itself
    (``END_REPLACED``).
    """
    from personalclaw.dashboard.session_store import unopened_links

    try:
        earlier = unopened_links(ISSUER_STARTUP)
    except Exception:  # noqa: BLE001 — a start must not fail on its predecessor's link
        logger.warning("could not read the earlier startup links", exc_info=True)
        return 0
    return sign_out(earlier, END_SUPERSEDED, actor="local-startup") if earlier else 0


def revoke_all_sessions() -> None:
    """Sign every session out, everywhere (``personalclaw logout``; also test isolation).

    **Ends them in the DURABLE store too, not just memory** (S1). This is the security half of
    persisting sessions: with only the in-memory clear, a revoked token would be rejected
    until the next restart and then accepted again, because `is_nonce_valid` would find its
    nonce still recorded on disk. "Revoke" that un-revokes itself on reboot is worse than no
    revoke at all — you would believe you had cut access off. Caught by
    `test_token_rejected_when_no_nonces_registered`, which is exactly the assertion that
    should notice.
    """
    try:
        from personalclaw.dashboard.session_store import load_session_records

        nonces = list(load_session_records())
    except Exception:  # noqa: BLE001
        logger.warning("could not read the durable session store during revoke", exc_info=True)
        nonces = []
    sign_out(nonces, END_SIGNED_OUT_EVERYWHERE, actor="system")
    _state.clear_all()
    with _APP_TOKENS_LOCK:
        _APP_TOKENS.clear()


def rotate_signing_key(*, actor: str) -> int:
    """Replace the key every session is signed with, signing every session out. Returns how many.

    The owner's answer to "the key, or a sign-in, may have been copied": ending sessions one by
    one leaves the key that could mint new ones, and this replaces it. Every browser, paired
    device and token stops working at once — the caller's own included — and each is told on its
    next request that the key was replaced, when, and how to sign back in (``END_KEY_REPLACED``).
    Integration tokens are separate credentials, and keep working.

    In order: the sessions are ended and the old key kept beside those endings, to recognise the
    tokens it signed (``session_store.retire_key``); then the new key is written — or, on an
    ephemeral key (tests, ``--test-mode``), a new one is drawn; then this process forgets
    everything it cached under the old one. Written to the SEL, per session and once for the key.
    """
    global _EPHEMERAL_SECRET
    from personalclaw.dashboard.session_store import retire_key, write_new_key

    ended = retire_key(_secret())
    if _EPHEMERAL_SECRET is not None:
        _EPHEMERAL_SECRET = os.urandom(32)
    else:
        write_new_key()
    reset_secret_cache()
    count = _signed_out(ended, END_KEY_REPLACED, actor=actor)
    _state.clear_all()
    with _APP_TOKENS_LOCK:
        _APP_TOKENS.clear()
    _sel_fn().log_api_access(
        caller=actor,
        operation="session_key_replaced",
        outcome="success",
        source="token_auth",
        resources=f"sessions_signed_out={count}",
    )
    return count


def ended_notice(nonce: str) -> SignedOutNotice | None:
    """What the device that held the session *nonce* is told about its ending, or *None* when no
    ending is remembered — the same sentence its next refused request carries."""
    return _ended_notice({"nonce": nonce}) if nonce else None


def secure_cookies() -> bool:
    """Whether session cookies should carry ``Secure`` (REMOTE-USER-AUTH T4.1).

    True only when the operator has declared an **https** public URL. Deliberately NOT the
    default: `Secure` makes a cookie undeliverable over plain http, which is how essentially
    every local install runs, so switching it on unconditionally would silently break login
    and page auth for everyone with nothing pointing at the cause.

    Any failure resolving this returns False — the value that keeps the box usable.
    """
    try:
        from personalclaw.dashboard.exposure import is_https

        return bool(is_https())
    except Exception:  # noqa: BLE001
        logger.debug("could not determine whether to set Secure on cookies", exc_info=True)
        return False


def revoke_token(token: str, *, actor: str = "owner") -> bool:
    """Sign out the ONE session *token* belongs to (logout). Returns whether it was live.

    Note this ends the SESSION, not just the presented string: any other copy of the same
    token dies with it, which is what a user pressing "sign out" means.
    """
    nonce = token_nonce(token)
    if not nonce:
        return False  # a malformed token has no session to revoke
    _state.revoke_nonce(nonce, token)
    return sign_out([nonce], END_SIGNED_OUT, actor=actor) > 0


def revoke_nonce(nonce: str) -> bool:
    """Drop ONE nonce from this process's live set only. Returns whether it was there.

    For the one caller that must retract a session the store never recorded (pairing, when
    the row could not be written). Every other end goes through :func:`sign_out`.
    """
    if not nonce:
        return False
    return _state.revoke_nonce(nonce)


def parse_duration(s: str) -> int | None:
    """``'<int>m'``, ``'<int>h'`` or ``'<int>d'`` in seconds, or *None* for anything else.

    The one lifetime grammar (``personalclaw.auth.lifetimes``), published to apps. It is NOT
    capped: a lifetime longer than :data:`MAX_SESSION_TTL_SECS` comes back as asked, for the
    caller to refuse — and :func:`generate_token` refuses it anyway — because a clamp here is how
    a request for a year used to be minted, silently, as something else.
    """
    return lifetime_seconds(s)


def parse_config_duration(s: str, *, default_secs: int) -> int:
    """Parse ``'<int>[mhd]'`` from CONFIG into seconds, falling back to *default_secs*.

    Deliberately a second function rather than a lenient `parse_duration`. That one serves
    `personalclaw token --ttl`, the token endpoint and apps, where a value it cannot read — or
    one over the limit — must be a hard error the user sees immediately. Here the input is a
    config file that may have been hand-edited, so the posture is the opposite: never let a
    typo brick the box. An unreadable value takes *default_secs*, and a value over the 90-day
    limit is applied AS the limit — the one place a longer lifetime is not refused, since
    nobody is there to be told; ``personalclaw doctor`` and the Doctor page say so instead
    (``personalclaw.auth.lifetimes.session_lifetime_report``).
    """
    if lifetime_seconds((s or "").strip()) is None:
        logger.warning("unparseable duration %r in config — using the default", s)
    return configured_lifetime(s, default_secs=default_secs)


#: The methods an internal route names. No call of the gateway's own is a HEAD or an OPTIONS.
_INTERNAL_ROUTE_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
#: A ``{name}`` or ``{name:regex}`` parameter of a route template, in the router's own syntax.
_ROUTE_PARAM = re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<regex>[^{}]+))?\}")


@dataclass(frozen=True)
class InternalRoute:
    """One operation the gateway's internal credential opens: a method on a route.

    Written ``"<METHOD> <template>"``, the template in the router's own syntax (``{id}`` is one
    path segment, ``{name:.+}`` is whatever the route itself allows there), and matched WHOLE. An
    entry opens exactly the operation it names: never a route under it, and never another method
    on the same route. Entries used to be path prefixes, so ``/api/triggers`` gave the credential
    every trigger route, creating, deleting and answering one included, to open the one ``/run``
    that is called with it.
    """

    method: str
    template: str
    pattern: re.Pattern[str]

    @classmethod
    def parse(cls, entry: str) -> "InternalRoute":
        """*entry* as a route. Raises ``ValueError`` on anything else, at startup."""
        method, _, template = entry.strip().partition(" ")
        template = template.strip()
        if method not in _INTERNAL_ROUTE_METHODS or not template.startswith("/"):
            raise ValueError(f"an internal route is written '<METHOD> /path', not {entry!r}")
        parts: list[str] = []
        at = 0
        for param in _ROUTE_PARAM.finditer(template):
            parts.append(re.escape(template[at : param.start()]))
            parts.append(f"(?:{param['regex']})" if param["regex"] else "[^{}/]+")
            at = param.end()
        parts.append(re.escape(template[at:]))
        return cls(method=method, template=template, pattern=re.compile("".join(parts)))

    def admits(self, method: str, path: str) -> bool:
        """Whether a *method* request for *path* is this operation.

        *path* is the string the router resolves (``request.rel_url.path_safe``: decoded, except
        that an encoded ``/`` stays encoded), so an entry admits exactly the requests its route
        serves. The decoded ``request.path`` is not that string: a job name holding a ``/``,
        sent percent-encoded, is one ``{id}`` segment to the router and two in ``request.path``.
        """
        return method == self.method and self.pattern.fullmatch(path) is not None


def token_auth_middleware(
    *,
    internal_routes: frozenset[str] = frozenset(),
    mixed_internal_routes: frozenset[str] = frozenset(),
    internal_secret: str = "",
    port: int = _DEFAULT_PORT,
    local_only: bool = True,
) -> Callable[..., Any]:
    """Factory returning aiohttp middleware for token-based dashboard auth.

    ALL requests require a valid token — loopback is not exempt, because
    local port forwarders (socat, ssh -R, custom scripts) make remote
    traffic appear as 127.0.0.1, which would otherwise bypass auth entirely.

    *internal_routes* are the operations (:class:`InternalRoute`) that
    PersonalClaw's own processes (an agent's tools, a scheduled script, the
    CLI) call with the gateway's internal credential: loopback AND a matching
    ``X-Internal-Secret`` header, read from ``<home>/.local_secret``.
    Non-loopback access to these is always denied.

    *mixed_internal_routes* are operations called by BOTH those processes
    (loopback + secret) AND the browser (cookie auth).  On non-loopback
    they perform explicit cookie validation (deny-by-default) instead
    of hard-denying, so DCV/SSH-forwarded browsers polling these routes
    (e.g. ``/api/spawn`` every 5s) don't trigger false session-expired
    banners.  Use this for any internal route that the browser calls too.

    A request that presents the internal credential on any other operation is
    refused as one (``internal_route_refused``). It is never judged as a
    browser that has not signed in: that answer is a sign-in sentence, which
    no process can act on, and which names the wrong cause.
    """
    strict_routes = tuple(InternalRoute.parse(entry) for entry in sorted(internal_routes))
    mixed_routes = tuple(InternalRoute.parse(entry) for entry in sorted(mixed_internal_routes))

    def _resolved_client_ip(request: web.Request) -> str:
        """Return the browser's IP, preferring a forwarded header from a TRUSTED peer.

        nginx (or any reverse proxy in the same compose network) sees the gateway's container
        IP as the TCP remote, not the actual client, so a forwarded header is the only way to
        recover the real one — and the real one is what IP binding binds to.

        **REMOTE-USER-AUTH T4.1 tightens who may set it.** Once the operator declares this
        instance internet-exposed (`dashboard.public_url`), only a peer listed in
        `dashboard.trusted_proxies` is believed. Before that change the rule was the shape of
        the TCP remote — "starts with 10./172.1x/192.168." — which is the classic mistake: on
        an exposed box every container neighbour, LAN device and SSRF-able local service sits
        on a private address, so any of them could set `X-Real-IP` and move a bound session to
        an address of their choosing.

        **Not exposed ⇒ behavior is unchanged**, deliberately. Home/compose installs depend on
        the private-subnet heuristic today, and silently breaking their nginx would be a
        regression paid by everyone to harden the few. Exposure is the operator's own
        statement, and it is what switches the strict rule on.
        """
        raw = request.remote or "unknown"
        forwarded = request.headers.get("X-Real-IP", "").strip()
        if not forwarded:
            return raw
        try:
            from personalclaw.dashboard.exposure import is_exposed, is_trusted_proxy

            if is_exposed():
                # Strict: an explicitly trusted peer, or the header is ignored entirely.
                if is_trusted_proxy(raw):
                    return forwarded
                logger.debug("ignoring X-Real-IP from untrusted peer on an exposed instance")
                return raw
        except Exception:  # noqa: BLE001 — never let this decision break a request
            logger.debug("exposure check failed; using the legacy proxy heuristic", exc_info=True)
        is_proxy = raw.startswith(
            ("127.", "10.", "172.1", "172.2", "172.3", "192.168.", "::1", "fc", "fd")
        )
        return forwarded if is_proxy else raw

    def _adopt(request: web.Request, credentials: _Credentials) -> None:
        """Expose the verified identity to handlers — never the credential itself.

        🔴 ``app`` is recorded on EVERY path, the internal routes included: the app permission
        middleware and every handler-level app check (``can_use_mcp_tool`` on
        ``/api/tools/invoke``) key on ``request["app"]``. The internal routes used to validate an
        app token like any other and drop the claim, so ``/api/tools/invoke?token=<an app's
        token>`` from loopback reached the handler as the OWNER.

        ``session_nonce`` says WHICH session authorized the request, so a handler can ask what
        kind of client is on the other end without re-deriving it from the raw credential. Only
        the nonce travels — it is the registry handle, and the token stays in this middleware.
        Consumed by the ``/api/ws`` origin check (CA-7): a paired device session is the one
        thing that can vouch for an origin-less upgrade.

        The client is noted here too (:func:`note_session_client`) — where it was seen from and
        what it is — because this is the one place that has both an authorized session and
        the request it came on.

        ``session_expires_at`` is when that session ends — its signed ``session_exp``, capped
        the way validation caps it (:func:`_session_deadline`) — which is what
        ``/api/auth-status`` tells the browser as its sign-in's remaining minutes.
        """
        request["user"] = credentials.user_id
        request["app"] = credentials.app
        request["session_nonce"] = token_nonce(credentials.token)
        request["session_expires_at"] = _session_end(credentials.token)
        note_session_client(
            request["session_nonce"],
            request,
            credentials.source,
            ip=_resolved_client_ip(request),
            issued_at=_issued_at(credentials.token),
        )

    def _refuse(request: web.Request, credentials: _Credentials, fallback: str) -> web.Response:
        """A Bearer's refusal is its stable wire code; every other refusal keeps its wording.

        JSON on every path, pages included: a header credential is sent by a client that
        reads JSON, never by a browser navigating to a page, so the paste-token gate would be
        an answer to a question nobody asked. The body never echoes the credential. A browser
        carrier whose session is known to have ended gets its signed-out sentence instead.
        """
        from personalclaw.http_errors import json_error

        headers = {"X-Auth-Required": "true"}
        if credentials.error_code == ERR_CREDENTIAL_CONFLICT:
            return json_error(ERR_CREDENTIAL_CONFLICT, status=403, headers=headers)
        if credentials.error_code:
            return json_error(ERR_BEARER_INVALID, status=403, headers=headers)
        return _deny(
            request,
            fallback,
            notice=refusal_notice(credentials.presented, source=credentials.source),
        )

    def _retire_the_sign_in_it_replaces(request: web.Request, token: str, user_id: str) -> None:
        """A browser opening a new link moves to it: end the sign-in its cookie held before.

        The gateway opens a fresh startup link in the default browser at every start, and the
        browser swaps its cookie for it. The session it swapped out stayed live for its whole
        lifetime with no browser holding it — a credential nobody would ever sign out, and one
        more identical "Chrome on Mac" row in Settings → Devices for every restart. Only an
        owner session in THIS browser's cookie is retired; a token held anywhere else is not.
        """
        previous = request.cookies.get(f"pc_token_{port}", "")
        if not previous or hmac.compare_digest(previous.encode(), token.encode()):
            return
        valid, _user, _reason, app = validate_token_with_app(previous, use_session_exp=True)
        previous_nonce = token_nonce(previous)
        if valid and not app and previous_nonce and previous_nonce != token_nonce(token):
            _keep_the_browser_s_pushes(previous_nonce, token_nonce(token))
            sign_out([previous_nonce], END_REPLACED, actor=user_id or "owner")

    @web.middleware
    async def middleware(request: web.Request, handler: object) -> web.StreamResponse:
        if os.environ.get("PERSONALCLAW_DEV_NO_AUTH") == "1":
            request["user"] = request.get("user") or "dev-local"
            return await handler(request)  # type: ignore[operator]

        path = request.path
        routed = request.rel_url.path_safe
        _matches_strict = any(route.admits(request.method, routed) for route in strict_routes)
        _matches_mixed = any(route.admits(request.method, routed) for route in mixed_routes)

        # The internal credential opens the listed operations and no other. Presented anywhere
        # else, it is refused as what it is, whichever credential rides beside it: it is never
        # passed on to the browser's session check, which reads a request carrying no session as
        # a device that has not signed in and answers with a sentence no process can act on.
        # Ahead of the local-network bypass on purpose: the development server runs with it, so a
        # call that bypass admitted was never seen failing, and every install refused it.
        if "X-Internal-Secret" in request.headers and not (_matches_strict or _matches_mixed):
            reason = f"{request.method} {path} does not take the internal secret"
            _sel_fn().log_api_access(
                caller=request.remote or "",
                operation="internal_auth",
                outcome="denied",
                source="token_auth",
                resources=path,
                error=reason,
            )
            _log_auth(request, "internal", "denied", reason)
            from personalclaw.http_errors import json_error

            return json_error("internal_route_refused", status=403)

        # Local-network bypass — opt-in, IP-gated.
        # When PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1, requests from loopback,
        # RFC1918, link-local, or ULA addresses skip token validation.
        # Off by default; intended for trusted home/dev LANs.
        if os.environ.get("PERSONALCLAW_BYPASS_LOCAL_NETWORKS") == "1":
            client_ip = _resolved_client_ip(request)
            if is_private_network(client_ip):
                request["user"] = request.get("user") or f"local-net:{client_ip}"
                # The bypass grants on the IP, so it never reaches the session_nonce line
                # below — which left a paired device on a bypassed LAN indistinguishable from
                # the owner's browser. Name the session when (and only when) the request
                # presents one the token proves. See presented_session_nonce.
                if not request.get("session_nonce"):
                    request["session_nonce"] = presented_session_nonce(request, port)
                _log_auth(
                    request,
                    request["user"],
                    "ok",
                    "local-network bypass",
                    identity=str(request["session_nonce"] or ""),
                )
                return await handler(request)  # type: ignore[operator]

        # Internal routes: loopback + secret grants immediate access.
        # If the secret is missing (browser request), fall through to
        # normal cookie auth so dashboard pages can call these routes.
        # local_only=False: treat ALL internal routes as mixed (the user has
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
                    _sel.log_api_access(
                        caller=request.remote or "",
                        operation="internal_auth",
                        outcome="denied",
                        source="token_auth",
                        resources=path,
                        error="no internal secret configured",
                    )
                    _log_auth(request, "internal", "denied", "no internal secret configured")
                    return _deny(request, "Forbidden")
                if hmac.compare_digest(internal_secret, _provided_secret):
                    # ONE row family per grant (it was two rows per request), tallied like
                    # every success: an MCP subprocess polling a route is one actor.
                    _log_auth(
                        request, "internal", "granted", "internal secret", operation="internal_auth"
                    )
                    return await handler(request)  # type: ignore[operator]
                # Wrong secret → deny (don't fall through)
                _sel = _sel_fn()
                _sel.log_api_access(
                    caller=request.remote or "",
                    operation="internal_auth",
                    outcome="denied",
                    source="token_auth",
                    resources=path,
                    error="wrong secret",
                )
                _log_auth(request, "internal", "denied", "wrong secret")
                return _internal_secret_invalid()
            # No secret header (a browser, the CLI) → verify session auth inline to satisfy
            # deny-by-default: positively confirm auth at the decision point rather than
            # deferring to downstream. The same selection the strict path below makes.
            credentials = _select_request_credentials(request, port)
            if not credentials.valid:
                _sel = _sel_fn()
                _sel.log_api_access(
                    caller=request.remote or "",
                    operation="internal_auth",
                    outcome="denied",
                    source="token_auth",
                    resources=path,
                    error=f"session auth failed: {credentials.failure}",
                )
                _log_auth(
                    request, "internal", "denied", f"session auth failed: {credentials.failure}"
                )
                return _refuse(request, credentials, "Forbidden")
            _adopt(request, credentials)
            _log_auth(
                request,
                credentials.user_id or "internal",
                "granted",
                f"{credentials.source} auth (no secret header)",
                operation="internal_auth",
            )
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
                    if not internal_secret or not hmac.compare_digest(
                        internal_secret, request.headers["X-Internal-Secret"]
                    ):
                        _sel = _sel_fn()
                        _sel.log_api_access(
                            caller=request.remote or "",
                            operation="internal_auth",
                            outcome="denied",
                            source="token_auth",
                            resources=path,
                            error="wrong secret (non-loopback mixed)",
                        )
                        _log_auth(
                            request, "internal", "denied", "wrong secret (non-loopback mixed)"
                        )
                        return _internal_secret_invalid()
                credentials = _select_request_credentials(request, port)
                if not credentials.valid:
                    _sel = _sel_fn()
                    _sel.log_api_access(
                        caller=request.remote or "",
                        operation="internal_auth",
                        outcome="denied",
                        source="token_auth",
                        resources=path,
                        error=f"mixed non-loopback session auth failed: {credentials.failure}",
                    )
                    _log_auth(
                        request,
                        "internal",
                        "denied",
                        f"mixed non-loopback session auth failed: {credentials.failure}",
                    )
                    return _refuse(request, credentials, "Forbidden")
                _adopt(request, credentials)
                _log_auth(
                    request,
                    credentials.user_id or "internal",
                    "granted",
                    f"mixed non-loopback {credentials.source} auth",
                    operation="internal_auth",
                )
                return await handler(request)  # type: ignore[operator]
            else:
                # INVARIANT: non-loopback access to strict internal routes is
                # ALWAYS denied.  Do NOT remove this branch — without it,
                # non-loopback requests would silently fall through to
                # normal cookie auth, defeating the machine-to-machine
                # isolation that the internal-secret design provides.
                _sel = _sel_fn()
                _sel.log_api_access(
                    caller=request.remote or "",
                    operation="internal_auth",
                    outcome="denied",
                    source="token_auth",
                    resources=path,
                    error="non-loopback source",
                )
                _log_auth(request, "internal", "denied", "non-loopback source")
                return _deny(request, "Forbidden")

        # Bypass static assets
        if any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await handler(request)  # type: ignore[operator]
        if path in _BYPASS_EXACT or any(t.fullmatch(path) for t in _BYPASS_TEMPLATES):
            return await handler(request)  # type: ignore[operator]
        credentials = _select_request_credentials(request, port)
        if not credentials.valid:
            _log_auth(request, "", "denied", credentials.failure)
            return _refuse(request, credentials, credentials.reason)
        token = credentials.token
        user_id = credentials.user_id
        # The browser exchange — address binding and the cookie — belongs to the entry link
        # alone. The cookie is already the session, and the header is a stateless carrier.
        from_query = credentials.source == "query"

        # Prefer X-Real-IP set by a trusted reverse proxy (nginx) over the
        # TCP remote address. See _resolved_client_ip for the trust rules.
        client_ip = _resolved_client_ip(request)

        # IP binding only applies on the initial query-param token exchange.
        # Cookie- and header-carried requests skip IP checks — the credential itself is the
        # proof, and IP validation behind a proxy is unreliable.
        if from_query and not check_token_ip(token, client_ip):
            _log_auth(request, user_id, "denied", "IP mismatch")
            return _deny(request, "IP mismatch", notice=link_used_notice())

        # Extract session_exp for cookie and IP binding on first query-param use
        session_exp = 0.0
        if from_query:
            try:
                payload_bytes = _b64url_decode(token.split(".")[0])
                session_exp = _session_deadline(json.loads(payload_bytes))
            except Exception:
                pass
            bind_token_ip(token, client_ip, session_exp)
            _retire_the_sign_in_it_replaces(request, token, user_id)

        # Expose authenticated identity to handlers (deny-by-default)
        _adopt(request, credentials)

        # Proceed to handler
        resp = await handler(request)  # type: ignore[operator]

        # Set cookie after handler (needs response object)
        if from_query:
            cookie_name = f"pc_token_{port}"
            remaining = int(session_exp - time.time()) if session_exp else MAX_SESSION_TTL_SECS
            resp.set_cookie(
                cookie_name,
                token,
                httponly=True,
                samesite="Lax",
                path="/",
                # The session's own end, plus the grace that lets its end be explained
                # (SIGNED_OUT_NOTICE_GRACE_SECS). The gateway refuses it at `session_exp`.
                max_age=min(max(0, remaining), MAX_SESSION_TTL_SECS) + SIGNED_OUT_NOTICE_GRACE_SECS,
                secure=secure_cookies(),
            )
            # Clear the non-port-specific cookie so only pc_token_{port} is used.
            resp.set_cookie("pc_token", "", max_age=0, path="/")

        _log_auth(request, user_id, "ok", "", identity=request["session_nonce"])
        return resp  # type: ignore[return-value]

    middleware._is_token_auth = True  # type: ignore[attr-defined]  # sentinel for server.py security gate  # noqa: E501
    return middleware


def auth_middleware(
    auth_cfg: Any,  # personalclaw.auth.modes.AuthConfig — typed as Any to avoid circular import
    *,
    internal_routes: frozenset[str] = frozenset(),
    mixed_internal_routes: frozenset[str] = frozenset(),
    internal_secret: str = "",
    port: int = _DEFAULT_PORT,
    local_only: bool = True,
) -> Callable[..., Any]:
    """Factory returning aiohttp middleware dispatched by ``auth_cfg.mode``.

    * ``NONE``        — passthrough (loopback invariant enforced at bind time).
    * ``LOCAL_TOKEN`` — delegates to :func:`token_auth_middleware`.

    The returned middleware carries the ``_is_token_auth`` sentinel the ``server.py``
    security invariant check reads: True for ``LOCAL_TOKEN``, False for ``NONE`` (where
    auth is intentionally absent). A mode that is neither cannot be served at all: it
    raises here, at startup, rather than answering every request with a refusal.
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
            internal_routes=internal_routes,
            mixed_internal_routes=mixed_internal_routes,
            internal_secret=internal_secret,
            port=port,
            local_only=local_only,
        )

    raise ValueError(f"auth_middleware: unknown AuthMode {mode!r}")


def _login_offered() -> bool:
    """Whether a password login page should be offered instead of the paste-token gate.

    Requires BOTH `auth.login_enabled` and an actually-configured credential. The second
    condition is what keeps a misconfiguration from becoming a lockout: enabling login and
    then losing the credential file would otherwise redirect every page to a form nobody can
    pass, with the paste-token gate — the escape hatch — no longer reachable. Any error here
    falls back to the existing gate, because that is the behavior that always works.
    """
    try:
        from personalclaw.auth.credentials import has_credentials
        from personalclaw.config.loader import AppConfig

        if not bool(AppConfig.load().auth.login_enabled):
            return False
        return bool(has_credentials())
    except Exception:  # noqa: BLE001
        logger.debug("could not determine whether login is offered", exc_info=True)
        return False


def notice_html(message: str) -> str:
    """*message* as HTML: escaped, with its `backticked` commands shown as code."""
    import html as _html

    parts = _html.escape(message).split("`")
    return "".join(f"<code>{part}</code>" if i % 2 else part for i, part in enumerate(parts))


def _notice_response(notice: SignedOutNotice, headers: dict[str, str]) -> web.Response:
    """The registered envelope for *notice*: its code, its sentence, and ``detail`` = the reason
    and when. Three literal calls rather than ``json_error(notice.code, …)``: the registry rail
    can only see a literal code (tests/test_http_error_codes_append_only.py)."""
    from personalclaw.http_errors import json_error

    extra = {"detail": {"reason": notice.reason, "at": notice.at}}
    if notice.code == ERR_SESSION_EXPIRED:
        return json_error(
            "session_expired",
            message=notice.message,
            status=403,
            headers=headers,
            error_extra=extra,
        )
    if notice.code == ERR_SESSION_REQUIRED:
        return json_error(
            "session_required",
            message=notice.message,
            status=403,
            headers=headers,
            error_extra=extra,
        )
    return json_error(
        "session_signed_out",
        message=notice.message,
        status=403,
        headers=headers,
        error_extra=extra,
    )


def _deny(
    request: web.Request, reason: str, *, notice: SignedOutNotice | None = None
) -> web.Response:
    """The refusal a request without a usable session gets. Both forms carry ``X-Auth-Required``.

    With a *notice* — every browser carrier's refusal (:func:`refusal_notice`), and a link
    opened on a second device (:func:`link_used_notice`) — an API request gets the registered
    envelope (``session_signed_out`` / ``session_expired`` / ``session_required``) carrying the
    sentence and the reason, which the SPA and the desktop app show; a page gets the same
    sentence under the notice's heading on the paste-token gate. Without one — a strict internal
    route reached from off this computer, or a credential presented to a gateway that holds none —
    an API request keeps the bare ``{"error": reason}`` a script branches on, and a page is
    treated as a device that is not signed in. A wrong credential is
    :func:`_internal_secret_invalid`.
    """
    headers = {"X-Auth-Required": "true"}
    if request.path.startswith("/api/"):
        if notice is not None:
            return _notice_response(notice, headers)
        return web.json_response({"error": reason}, status=403, headers=headers)
    # When a password login is on offer, an expired or absent session
    # on a PAGE request lands on /login instead of the paste-token gate. Telling a remote user
    # to "run personalclaw token in your terminal" is useless advice when the whole reason
    # they are here is that they are not at the terminal.
    #
    # Deliberately narrow: only non-API GETs, and never /login itself (that would loop). The
    # `?token=` path never reaches here at all — a valid token is authorized upstream — so the
    # local flow is untouched, and the gate remains the fallback whenever login is not offered.
    if request.method == "GET" and request.path != "/login" and _login_offered():
        return web.Response(
            status=302,
            headers={**headers, "Location": "/login", "Cache-Control": "no-store"},
        )
    notice = notice or not_signed_in_notice()
    return web.Response(
        text=_403_HTML.format(
            heading=notice.heading,
            explanation=notice_html(notice.message),
            owner_token_script=owner_token_script(scrub=True),
        ),
        status=403,
        content_type="text/html",
        headers=headers,
    )


def _internal_secret_invalid() -> web.Response:
    """The refusal of an internal route presented a credential this gateway did not issue.

    It used to be the bare word "Forbidden", and a tool shows the refusal it gets as its result,
    so the agent and its owner read a word that named nothing. One of PersonalClaw's own processes
    that gets it read its credential in a home other than this gateway's: each re-reads it at every
    call. The sentence is fixed, like every auth refusal's, and says only what this gateway knows.
    """
    from personalclaw.http_errors import json_error

    return json_error("internal_secret_invalid", status=403)


#: Successful authentications of ONE identity fold into one SEL row per this many seconds.
_SUCCESS_WINDOW_SECS = 15 * 60
#: How often closed windows are swept into their summary rows (and forgotten).
_SUCCESS_SWEEP_SECS = 60.0
#: A summary names at most this many distinct paths; the count covers every request.
_SUCCESS_MAX_PATHS = 10


class _SuccessTally:
    """Successful authentications, recorded as one SEL row per identity per window.

    Audit logging for this surface — log every security event to the SEL, capturing the who,
    what and when of each TRANSACTION so actions trace to an actor and an incident can be
    reconstructed — is about security EVENTS. A cookie-authenticated request is the same
    session presenting the same credential again, not
    a new event — and a row for each one was 94% of the log: one idle Home tab made 428 requests
    in 3 minutes and the log grew ~5 MB an hour (measured, day 8).

    So a failure is written as it happens (`_log_auth`), and so is the FIRST success of an
    identity in a window — the who/when evidence is immediate. The rest of that window's
    successes are COUNTED, and when the window closes one summary row records how many there
    were and which paths they reached. Every success is accounted for; the log holds a row per
    session per quarter hour instead of one per poll. An identity is a session nonce when the
    request carries one (two sessions of one user stay two actors), else the caller.
    """

    def __init__(self, window_secs: float = _SUCCESS_WINDOW_SECS) -> None:
        self._window = window_secs
        self._open: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def record(self, *, caller: str, operation: str, reason: str, identity: str, path: str) -> bool:
        """Account for one success. True when THIS request is the one to write as a row."""
        key = (caller, operation, identity or reason)
        now = time.time()
        with self._lock:
            closed = self._take_closed(now)
            entry = self._open.get(key)
            first = entry is None or now - entry["since"] >= self._window
            if entry is not None and not first:
                entry["count"] += 1
                if len(entry["paths"]) < _SUCCESS_MAX_PATHS:
                    entry["paths"].add(path)
            else:
                if entry is not None:
                    closed.append((key, entry))
                self._open[key] = {"since": now, "count": 0, "paths": set(), "reason": reason}
        for closed_key, closed_entry in closed:
            self._summarize(closed_key, closed_entry, now)
        return first

    def flush(self) -> None:
        """Summarize every open window now (shutdown, and tests)."""
        with self._lock:
            pending = list(self._open.items())
            self._open.clear()
        now = time.time()
        for key, entry in pending:
            self._summarize(key, entry, now)

    def _take_closed(self, now: float) -> list[tuple[tuple[str, str, str], dict[str, Any]]]:
        """Pop the windows that have closed, at most once per sweep interval. Caller holds lock."""
        if now - self._last_sweep < _SUCCESS_SWEEP_SECS:
            return []
        self._last_sweep = now
        closed = [(k, e) for k, e in self._open.items() if now - e["since"] >= self._window]
        for key, _entry in closed:
            del self._open[key]
        return closed

    @staticmethod
    def _summarize(key: tuple[str, str, str], entry: dict[str, Any], now: float) -> None:
        count = int(entry["count"])
        if count <= 0:
            return  # the window's one success is already its own row
        caller, operation, _identity = key
        elapsed = max(0, int(now - entry["since"]))
        metadata: dict[str, Any] = {
            "summary": True,
            "requests": count,
            "window_secs": elapsed,
            "paths": sorted(entry["paths"]),
        }
        if entry["reason"]:
            metadata["reason"] = entry["reason"]
        try:
            # `ok` for every summary: it counts successes of whichever operation it names, and
            # the window's first row already carries that operation's own success word.
            _sel_fn().log_api_access(
                caller=caller,
                operation=operation,
                outcome="ok",
                source="token_auth",
                resources=f"{count} more successful request(s) in {elapsed}s",
                metadata=metadata,
            )
        except Exception:
            logger.warning("Failed to log an auth summary to the SEL", exc_info=True)


_SUCCESSES = _SuccessTally()


def flush_success_tally() -> None:
    """Write every open success window's summary now — the gateway calls this on shutdown."""
    _SUCCESSES.flush()


def _log_auth(
    request: web.Request,
    user_id: str,
    outcome: str,
    reason: str,
    *,
    operation: str = "dashboard.token_auth",
    identity: str = "",
) -> None:
    """Log an auth decision to the SEL — a failure as it happens, a success through the tally.

    ``reason`` is context for the decision, not necessarily a failure — see
    :func:`_sel_reason_kwargs`, which decides whether it lands in ``error`` or
    ``metadata`` based on ``outcome``. A success is written only when it is the first of its
    identity's window (:class:`_SuccessTally`); the others are counted into that window's summary.
    ``identity`` is the session nonce when the caller has one, so two sessions of one user are
    tallied — and so written — as the two actors they are.
    """
    caller = user_id or request.remote or "unknown"
    if outcome in AUDIT_OUTCOME_SUCCESS and not _SUCCESSES.record(
        caller=caller,
        operation=operation,
        reason=reason,
        identity=identity,
        path=request.path,
    ):
        return
    try:
        _sel_fn().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            resources=request.path,
            **_sel_reason_kwargs(outcome, reason),
        )
    except Exception:
        logger.warning("Failed to log auth event to SEL", exc_info=True)
