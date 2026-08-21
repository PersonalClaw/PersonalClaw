"""Device pairing + the Devices registry (COMPANION-APPS C2 / T1.1).

Four routes, one credential type:

* ``POST /api/devices/pair/start``     — owner-authenticated; mints a code + QR payload
* ``POST /api/devices/pair/complete``  — auth-EXEMPT; the device redeems the code for a session
* ``GET  /api/devices``                — owner-authenticated; the registry
* ``POST /api/devices/{id}/revoke``    — owner-authenticated; locks one device out

**There is no device token.** A paired device gets an ordinary session cookie from the same
:func:`generate_token` the owner's browser uses; pairing only writes provenance
(``issuer="pair"`` plus a ``device`` block) onto the session row it just minted. That is the
load-bearing decision: a second credential type would need its own expiry, its own revocation
list, its own middleware branch and its own bugs, and the first time the two disagreed the
device would be authenticated by one and unknown to the other. The registry is therefore a
VIEW over `sessions.json`, which is why "revoke" and "the session is gone" cannot drift apart
— they are the same write.

``pair/complete`` is exempt from token auth for exactly the reason ``/api/auth/login`` and
``/api/auth/enroll/complete`` are: the caller has no session yet, and gating the route behind
the session it exists to mint would be circular. It carries the same compensating guards —
origin check, per-IP lockout, single-use hashed code with a short TTL — and it shares the
lockout counters and the cookie writer with the login path rather than reimplementing them,
because two cookie writers means two security postures for one session model.

Every route emits a SEL event, INCLUDING the denials. A rejected pairing attempt is the event
an owner most wants to see afterwards; a rejection that leaves no trace is indistinguishable
from an attempt that never happened.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from aiohttp import web

from personalclaw.dashboard.handlers.page_shell import page_document
from personalclaw.dashboard.origin import check_origin
from personalclaw.dashboard.session_store import (
    DeviceInfo,
    attach_device,
    device_sessions,
    forget_session,
    nonces_for_device,
    sanitize_device_kind,
    sanitize_device_name,
)
from personalclaw.dashboard.token_auth import (
    DEFAULT_BROWSER_SESSION_TTL_SECS,
    generate_token,
    parse_config_duration,
    revoke_nonce,
)
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

# Verbatim from the plan's C2 "Error codes (Tier-S)" row. The asymmetry (`_code_invalid` but
# `_expired`) is the contract's, not a slip here: CA-2 maps these exact strings to copy, so
# guessing a tidier pair would mean the frontend showing a raw code to the user.
ERR_CODE_INVALID = "device_pair_code_invalid"
ERR_CODE_EXPIRED = "device_pair_expired"
ERR_ORIGIN = "device_pair_origin_rejected"
ERR_LOCKED_OUT = "device_pair_locked_out"
ERR_UNKNOWN_DEVICE = "device_unknown"

#: Coarse User-Agent → device kind. Only consulted when the client did not say, and the result
#: still goes through `sanitize_device_kind`, so a match here cannot widen the vocabulary.
_UA_KINDS: tuple[tuple[str, str], ...] = (
    ("iphone", "mobile"),
    ("ipad", "mobile"),
    ("android", "mobile"),
    ("mobile", "mobile"),
    ("electron", "desktop"),
    ("curl", "cli"),
    ("python-requests", "cli"),
    ("macintosh", "browser"),
    ("windows", "browser"),
    ("linux", "browser"),
)

#: The user id a paired device authenticates as. Distinct from `enrolled-device` so the auth
#: log says which door was used.
PAIRED_DEVICE_USER = "paired-device"


def _sel() -> Any:
    from personalclaw.sel import sel

    return sel()


def _audit(
    operation: str,
    outcome: str,
    *,
    caller: str = "owner",
    error: str = "",
    resources: str = "",
) -> None:
    """One SEL event per route outcome. Never raises: an audit failure must not eat the reply.

    Never carries a code, a nonce, or a token — a pairing code in the security log is a
    pairing code in every log shipper downstream of it.
    """
    try:
        _sel().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            source="devices",
            error=error,
            resources=resources,
        )
    except Exception:  # noqa: BLE001
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is an empty body, not a 500
        return {}
    return body if isinstance(body, dict) else {}


def _pair_base_url(request: web.Request) -> str:
    """The origin a device should be pointed at.

    Prefers the configured public URL (a tunnelled instance must not hand out a LAN address),
    then falls back to the Host the OWNER's own authenticated request arrived on — which on a
    LAN install is exactly the address a phone needs. `Host` is caller-controlled in general,
    but this caller is the authenticated owner reading their own dashboard, so echoing it back
    tells them nothing they did not type; it is still bounded and screened for control
    characters so it cannot smuggle anything into a rendered QR payload.
    """
    try:
        from personalclaw.dashboard.exposure import public_url

        configured = public_url()
        if configured:
            return configured.rstrip("/")
    except Exception:  # noqa: BLE001
        logger.debug("could not resolve the configured public URL", exc_info=True)

    host = str(request.host or "")[:255]
    if not host or any(ch.isspace() or not ch.isprintable() for ch in host):
        return ""
    return f"{request.scheme}://{host}"


# ── pair/start ──────────────────────────────────────────────────────────


async def api_devices_pair_start(request: web.Request) -> web.Response:
    """POST /api/devices/pair/start — mint a single-use pairing code + QR payload.

    Behind the normal middleware, so this is the "I am already in on my laptop and want my
    phone in too" path. The code is returned ONCE; nothing can read it back, because the store
    holds only its hash.
    """
    if not check_origin(request):
        _audit("device_pair_started", "denied", error="origin rejected")
        return json_error(ERR_ORIGIN, status=403)

    from personalclaw.auth import pairing

    body = await _body(request)
    label = sanitize_device_name(body.get("label", ""))

    code, expires_at = pairing.issue_code(label=label)
    formatted = pairing.format_code(code)
    base = _pair_base_url(request)
    # The QR payload: a single URL a phone camera can act on. The code rides in it because a
    # two-step "scan this, then type that" flow is the one people abandon. Resolved HERE and
    # never composed in the browser (C2 (a)) — the dashboard may be open on loopback while the
    # scanning phone needs the LAN address, and a browser-composed URL hands it `127.0.0.1`.
    pairing_url = f"{base}/pair?code={formatted}" if base else ""

    _audit("device_pair_started", "ok", resources=f"label={label}" if label else "")
    return web.json_response(
        {
            "code": formatted,
            "pairing_url": pairing_url,
            "expires_at": expires_at,
            "expires_in": pairing.PAIR_CODE_TTL_SECS,
        }
    )


def _derive_device_name(request: web.Request) -> str:
    """A label for a device that did not send one (C2 (b)).

    Coarse on purpose. A parsed browser-version string would be precise and wrong within a
    month; "iPhone" is what the owner would have typed anyway, and they can rename it.
    """
    ua = str(request.headers.get("User-Agent") or "").lower()
    for token, label in (
        ("iphone", "iPhone"),
        ("ipad", "iPad"),
        ("android", "Android device"),
        ("macintosh", "Mac"),
        ("windows", "Windows PC"),
        ("linux", "Linux device"),
    ):
        if token in ua:
            return label
    return "Paired device"


def _derive_device_kind(request: web.Request) -> str:
    """A kind for a device that did not declare one. Still clamped by the caller."""
    ua = str(request.headers.get("User-Agent") or "").lower()
    for token, kind in _UA_KINDS:
        if token in ua:
            return kind
    return "unknown"


# ── pair/complete ───────────────────────────────────────────────────────


async def api_devices_pair_complete(request: web.Request) -> web.Response:
    """POST /api/devices/pair/complete — redeem a code for a durable device session.

    Auth-exempt by necessity (see the module docstring), so it wears login's guards: origin
    check, per-IP lockout, and a single-use hashed code.
    """
    from personalclaw.dashboard.handlers.auth import (
        _auth_cfg,
        _clear_failures,
        _client_ip,
        _lockout_remaining,
        _record_failure,
        _set_session_cookie,
    )

    ip = _client_ip(request)
    cfg = _auth_cfg()

    if not check_origin(request):
        _audit("device_paired", "denied", caller=ip, error="origin rejected")
        return json_error(ERR_ORIGIN, status=403)

    remaining = _lockout_remaining(ip, cfg)
    if remaining:
        _audit("device_paired", "denied", caller=ip, error=f"locked out retry_after={remaining}s")
        return json_error(ERR_LOCKED_OUT, status=429, headers={"Retry-After": str(remaining)})

    from personalclaw.auth import pairing

    body = await _body(request)
    code = str(body.get("code") or "")
    # `device_name` is OPTIONAL (C2 (b)): omitted, the gateway derives one, so a client with
    # nothing but a code can still pair.
    name = sanitize_device_name(body.get("device_name", ""))
    kind = sanitize_device_kind(body.get("kind", ""))

    outcome = pairing.redeem_code(code)
    if not outcome.ok:
        # A wrong code and an expired one both count toward the lockout: the difference is
        # what the user is told, not how much grinding they are allowed.
        _record_failure(ip)
        _audit("device_paired", "denied", caller=ip, error=f"code {outcome.result}")
        err = ERR_CODE_EXPIRED if outcome.result == pairing.RESULT_EXPIRED else ERR_CODE_INVALID
        return json_error(err, status=401)

    # A device session at the same TTL as a browser login rather than the 1-year cap: a phone
    # in a drawer should not hold a live session for a year.
    ttl = parse_config_duration(cfg.session_ttl, default_secs=DEFAULT_BROWSER_SESSION_TTL_SECS)
    token = generate_token(PAIRED_DEVICE_USER, ttl_seconds=ttl)
    nonce = _nonce_of(token)

    device = DeviceInfo(
        id=secrets.token_hex(8),
        # The device's own name wins, then the owner's label from `pair/start`, then a derived
        # one. None of the three is trusted text — all went through `sanitize_device_name`.
        name=name or sanitize_device_name(outcome.label) or _derive_device_name(request),
        # An explicit kind wins; otherwise derive, then clamp again so the derivation cannot
        # widen the vocabulary either.
        kind=kind if kind != "unknown" else sanitize_device_kind(_derive_device_kind(request)),
        minted_at=time.time(),
    )
    if not nonce or not attach_device(nonce, device):
        # The session exists but is not attributable, so it cannot be listed or revoked. Refuse
        # and retract it rather than leave an unrevocable session behind: an un-listed device
        # session is the exact failure the registry exists to prevent.
        if nonce:
            revoke_nonce(nonce)
            forget_session(nonce)
        _audit("device_paired", "denied", caller=ip, error="could not persist the device session")
        return json_error(ERR_CODE_INVALID, status=503)

    _clear_failures(ip)
    _audit(
        "device_paired",
        "granted",
        caller=ip,
        resources=f"device={device.id} kind={device.kind}",
    )

    resp = web.json_response(
        {
            "ok": True,
            "device_id": device.id,
            "name": device.name,
            "kind": device.kind,
            "expires_in": ttl,
        }
    )
    _set_session_cookie(request, resp, token, ttl)
    return resp


def _nonce_of(token: str) -> str:
    """The nonce inside a freshly minted token, or "" when it cannot be read.

    Pairing needs the session's identity to annotate its row, and the token payload is where
    that identity already lives — deriving it here keeps `generate_token` device-unaware.
    """
    from personalclaw.dashboard.token_auth import _b64url_decode

    try:
        payload = _b64url_decode(token.split(".")[0])
        return str(json.loads(payload).get("nonce") or "")
    except Exception:  # noqa: BLE001
        logger.warning("could not read the nonce out of a freshly minted token", exc_info=True)
        return ""


# ── /pair — the joining device's redeem screen ──────────────────────────


async def pair_page(request: web.Request) -> web.Response:
    """GET /pair — the screen the URL from ``pair/start`` actually points at.

    Served as a standalone document rather than an SPA route, exactly like ``/login`` and for the
    identical reason: the browser opening it has **no session**, so every authenticated bundle
    fetch the SPA makes on boot would 403 before a single field rendered. The `pairing_url` the
    panel hands out was a dead end in both states without this — 403 without a token, and with
    one the SPA's hash router simply landed on the dashboard.

    **Why exempting this path from token auth opens nothing.** The document is a CONSTANT: the
    code is never interpolated into it server-side (the script reads `?code=` out of
    `location.search` in the browser), so there is no injection surface and no secret on the
    page. Every grant still happens at ``/api/devices/pair/complete``, which keeps its own
    guards — origin check, per-IP lockout, single-use hashed short-TTL code. What the exemption
    buys is that the door is reachable at all.

    A browser that ALREADY holds a valid session is redirected home instead of being offered the
    form. That is not tidiness: redeeming a code here overwrites this browser's session cookie,
    so the owner's own laptop would silently become a "device" row while its previous session
    row stayed behind unreachable — and with ``MAX_CONCURRENT_NONCES`` at 5 a self-pair also
    spends an eviction slot for nothing.
    """
    from personalclaw.dashboard.handlers.auth import has_valid_session

    if has_valid_session(request, int(request.app.get("port") or 0)):
        raise web.HTTPFound("/")
    return web.Response(
        text=_PAIR_HTML,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


# The card body. Shares `/login`'s shell (handlers/page_shell.py) so the two doors into the same
# gateway are one visual language rather than two hand-written copies of the tokens.
_PAIR_BODY = """\
<h1>Pair this device</h1>
<p>This will sign this device in to the PersonalClaw dashboard on your home network.</p>
<form id='f' autocomplete='off'>
<input id='c' name='code' type='text' placeholder='XXXX-XXXX' autocomplete='one-time-code'
inputmode='latin' autocapitalize='characters' spellcheck='false' aria-label='Pairing code'>
<input id='n' name='device_name' type='text' placeholder="This device's name (optional)"
autocomplete='off' spellcheck='false' aria-label="This device's name">
<button id='b' type='submit'>Pair this device</button>
</form>
<div class='err' id='e' role='alert' aria-live='polite'></div>
<div class='hint'>The code is on the device you started from, under
<code>Settings &rarr; Devices</code>. It expires five minutes after it is created.</div>"""

# Every error code `pair/complete` can return, mapped to something a person can act on. An
# unmapped code would render as a raw identifier, so the fallback is a sentence too.
_PAIR_SCRIPT = """\
var MESSAGES = {
  device_pair_code_invalid: 'That code is not valid, or has already been used.',
  device_pair_expired: 'That code has expired. Start pairing again on the other device.',
  device_pair_locked_out: 'Too many attempts. Wait a moment and try again.',
  device_pair_origin_rejected:
    'This gateway will not accept pairing from this address. On the host, set the dashboard '
    + 'URL to the address you are using here, then try again.'
};
var params = new URLSearchParams(window.location.search);
var prefilled = (params.get('code') || '').toUpperCase();
if (prefilled) { document.getElementById('c').value = prefilled; }
document.getElementById(prefilled ? 'n' : 'c').focus();
document.getElementById('f').addEventListener('submit', function (ev) {
  ev.preventDefault();
  var btn = document.getElementById('b'), err = document.getElementById('e');
  btn.disabled = true; err.textContent = '';
  fetch('/api/devices/pair/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({
      code: document.getElementById('c').value,
      device_name: document.getElementById('n').value
    })
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      return { ok: r.ok, data: d };
    });
  }).then(function (res) {
    if (res.ok) { window.location.href = '/'; return; }
    var code = (res.data && res.data.error && res.data.error.code)
      || 'device_pair_code_invalid';
    err.textContent = MESSAGES[code] || 'Pairing failed. Ask for a new code and try again.';
    btn.disabled = false;
  }).catch(function () {
    err.textContent = 'Could not reach the gateway.';
    btn.disabled = false;
  });
});
"""

_PAIR_HTML = page_document(
    title="Pair this device — PersonalClaw", body=_PAIR_BODY, script=_PAIR_SCRIPT
)


# ── the registry ────────────────────────────────────────────────────────


async def api_devices_list(request: web.Request) -> web.Response:
    """GET /api/devices — every paired device with a live session.

    Derived from `sessions.json`, so a device disappears from this list the moment its session
    is revoked or expires. Never returns a nonce: the registry is something the owner reads out
    loud, and the nonce is the credential.
    """
    rows: list[dict[str, Any]] = []
    for record in device_sessions().values():
        device = record.device
        if device is None:  # pragma: no cover — `device_sessions` filters these out
            continue
        rows.append(
            {
                "id": device.id,
                "name": device.name,
                "kind": device.kind,
                "minted_at": device.minted_at,
                # 0.0 means "never made an authorized request" and the panel renders it as
                # "never". It is deliberately not coalesced to `minted_at` — see `DeviceInfo`.
                "last_seen": device.last_seen,
                "issuer": record.issuer,
                "expires_at": record.expiry,
            }
        )
    rows.sort(key=lambda r: float(r["minted_at"] or 0.0), reverse=True)
    _audit("devices_listed", "ok", resources=f"devices={len(rows)}")
    return web.json_response({"devices": rows})


async def api_devices_revoke(request: web.Request) -> web.Response:
    """POST /api/devices/{id}/revoke — lock one device out.

    Drops the in-memory nonce AND the durable row, in that order. Both halves are load-bearing
    and for different clocks: without the in-memory drop the device keeps working until this
    process restarts, and without the durable drop it comes back TO LIFE at the next restart.
    A revoke that un-revokes on reboot is worse than no revoke, because the owner was told it
    worked.
    """
    if not check_origin(request):
        _audit("device_revoked", "denied", error="origin rejected")
        return json_error(ERR_ORIGIN, status=403)

    device_id = request.match_info.get("id", "")
    nonces = nonces_for_device(device_id)
    if not nonces:
        _audit("device_revoked", "denied", error="unknown device", resources=f"device={device_id}")
        return json_error(ERR_UNKNOWN_DEVICE, status=404)

    for nonce in nonces:
        revoke_nonce(nonce)
        forget_session(nonce)

    _audit("device_revoked", "ok", resources=f"device={device_id} sessions={len(nonces)}")
    return web.json_response({"ok": True, "revoked": len(nonces)})


def register_device_routes(app: web.Application) -> None:
    """Wire C2's four API routes plus the redeem PAGE the pairing URL points at."""
    app.router.add_post("/api/devices/pair/start", api_devices_pair_start)
    app.router.add_post("/api/devices/pair/complete", api_devices_pair_complete)
    app.router.add_get("/api/devices", api_devices_list)
    app.router.add_post("/api/devices/{id}/revoke", api_devices_revoke)
    # Registered here rather than beside the other pages in server.py: it is the entry point of
    # `pair/start`'s URL, and splitting the two across files is how the URL came to point at a
    # route that did not exist.
    app.router.add_get("/pair", pair_page)
