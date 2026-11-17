"""An automation client's session as the sign-in check holds it: its key, its claim, its end.

A scoped pairing (``device_scopes``) mints a session whose token carries a signed ``scope`` claim
and whose row keeps the same scope. ``token_auth`` validates it with what is here:

* **the key** (:func:`signature_kind`) — a scoped token is signed under a key derived from the
  session key (:data:`hmac_domains.SCOPE`), and the key is bound to the claim
  (:func:`claimed_scope`), so neither kind of payload can be re-signed as the other;
* **the claim and the row agree, both ways** (:func:`judged`), on every request;
* **the idle end** — an automation client nothing has used for ``device_scopes.IDLE_END_SECS``
  is signed out on its next request, and by :func:`end_idle_sessions` when nothing asks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any

from personalclaw.dashboard import device_scopes, hmac_domains

logger = logging.getLogger(__name__)

#: Which key signed a token (:func:`signature_kind`).
SIGNED_BASE = "base"
SIGNED_SCOPED = "scoped"

#: What the nonce check answers for a token whose scope claim and session disagree, and for an
#: automation client unused past its idle end (which the caller ends).
REASON_SCOPE_MISMATCH = device_scopes.ERR_SCOPE_MISMATCH
REASON_IDLE = "idle"

#: What an automation client signed out for going unused is told (``session_store.END_IDLE``).
IDLE_SENTENCE = (
    "This automation client was signed out after 14 days unused. Pair it again from "
    "Settings → Devices & Access → Devices."
)

#: What a device whose token's scope and session's scope disagreed is told
#: (``session_store.END_SCOPE_MISMATCH``).
MISMATCH_SENTENCE = (
    "This device's access didn't match what was granted, so it was signed out. Pair it again "
    "from Settings → Devices & Access → Devices."
)

#: Who ends a scoped session the sign-in check refused (idle, or a mismatch), as the SEL names it.
ACTOR = "gateway"


def ended(reason: str) -> tuple[str, str]:
    """``(END_* reason, sentence)`` for the session a nonce check refused with *reason* (idle, or a
    mismatch), which the caller ends before any route runs; ``("", "")`` for any other refusal."""
    from personalclaw.dashboard.session_store import END_IDLE, END_SCOPE_MISMATCH

    return {
        REASON_IDLE: (END_IDLE, IDLE_SENTENCE),
        REASON_SCOPE_MISMATCH: (END_SCOPE_MISMATCH, MISMATCH_SENTENCE),
    }.get(reason, ("", ""))


def ended_sentence(end_reason: str) -> str:
    """The sentence a session ended for *end_reason* is told, when it is one ended here."""
    by_end = dict(ended(reason) for reason in (REASON_IDLE, REASON_SCOPE_MISMATCH))
    return by_end.get(end_reason, "")


def _signature(key: bytes, payload: bytes) -> str:
    return (
        base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )


def sign_scoped(payload: bytes, secret: bytes) -> str:
    """*payload*'s signature under the scoped key derived from the session key *secret*."""
    return _signature(hmac.new(secret, hmac_domains.SCOPE, hashlib.sha256).digest(), payload)


def signature_kind(payload: bytes, sig: str, secret: bytes) -> str:
    """Which key made *sig* over *payload*: the session key *secret* (:data:`SIGNED_BASE`), the
    scoped key derived from it (:data:`SIGNED_SCOPED`), or neither (``""``). A non-ASCII *sig* is
    neither: ``compare_digest`` raises on one, and a forged credential must be a refusal."""
    if not sig.isascii():
        return ""
    if hmac.compare_digest(sig, _signature(secret, payload)):
        return SIGNED_BASE
    if hmac.compare_digest(sig, sign_scoped(payload, secret)):
        return SIGNED_SCOPED
    return ""


def claimed_scope(claims: dict[str, Any], signed: str) -> tuple[str, ...] | None:
    """The scope *claims* name (``()`` for none), or *None* when they can't be this gateway's.

    The key is bound to the claim: a payload carrying ``scope`` verifies under the scoped key
    only, and one without it under the session key only. A claim that isn't a sorted list from
    the vocabulary is refused too.
    """
    has_claim = device_scopes.SCOPE_CLAIM in claims
    if has_claim != (signed == SIGNED_SCOPED):
        return None
    if not has_claim:
        return ()
    raw = claims.get(device_scopes.SCOPE_CLAIM)
    try:
        scope = device_scopes.normalize_scope(raw)
    except ValueError:
        return None
    return scope if list(scope) == raw else None


def token_scope(token: str) -> tuple[str, ...]:
    """The ``scope`` claim of a token the caller has VALIDATED (like ``token_auth.token_nonce``),
    or ``()``: validation has already held the claim to its session's row, both ways."""
    try:
        encoded = token.split(".", 1)[0]
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except Exception:  # noqa: BLE001 — an unreadable payload names no scope
        return ()
    raw = data.get(device_scopes.SCOPE_CLAIM) if isinstance(data, dict) else None
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        return ()
    return tuple(raw)


def judged(claimed: tuple[str, ...], scope: tuple[str, ...], last_used: float, now: float) -> str:
    """Why a live session holding *scope*, last used at *last_used*, may not answer a token that
    claims *claimed*: ``""`` when it may. Both directions: a claim the session doesn't hold, and
    a scoped session presented without its claim."""
    if tuple(claimed) != tuple(scope):
        return REASON_SCOPE_MISMATCH
    if scope and last_used and last_used + device_scopes.IDLE_END_SECS <= now:
        return REASON_IDLE
    return ""


def end_idle_sessions(*, now: float | None = None) -> int:
    """Sign out every automation client nothing has used for ``device_scopes.IDLE_END_SECS``,
    each through ``token_auth.sign_out(..., END_IDLE)``. Returns how many ended.

    Validation ends an idle client on its next request; this ends the ones nothing asks for,
    when the gateway starts and when the owner lists the devices signed in.
    """
    from personalclaw.dashboard.session_store import END_IDLE, idle_sessions
    from personalclaw.dashboard.token_auth import sign_out

    try:
        idle = idle_sessions(now=now)
    except Exception:  # noqa: BLE001 — an unreadable store ends nothing; validation still does
        logger.warning("could not read the session store to end idle automation clients")
        return 0
    return sign_out(idle, END_IDLE, actor=ACTOR) if idle else 0


#: nonce → when its last scope-mismatch row was written: one a minute per session.
_MISMATCH_LOGGED: dict[str, float] = {}
_MISMATCH_LOCK = threading.Lock()
_MISMATCH_TRACKED = 64


def end_refused(nonce: str, reason: str) -> str:
    """End the session *nonce* names when validation refused it as idle or for a mismatch, and
    say why the request is refused: ``"signed out"`` for the idle end, the mismatch's reason for a
    mismatch, ``""`` for any other refusal (nothing is ended). Before any route runs, through the
    one way a session ends (``token_auth.sign_out``), so it is remembered and in the SEL; a
    mismatch fails closed."""
    end, _said = ended(reason)
    if not end:
        return ""
    from personalclaw.dashboard.session_store import END_IDLE
    from personalclaw.dashboard.token_auth import sign_out

    if reason == REASON_SCOPE_MISMATCH:
        audit_mismatch(nonce)
    sign_out([nonce], end, actor=ACTOR)
    return "signed out" if end == END_IDLE else reason


def audit_mismatch(nonce: str) -> None:
    """One SEL row, a minute at most per session, for a token whose scope claim its session
    doesn't hold. Named by the session's public handle, never the nonce. Never raises."""
    now = time.time()
    with _MISMATCH_LOCK:
        if now - _MISMATCH_LOGGED.get(nonce, 0.0) < 60.0:
            return
        _MISMATCH_LOGGED[nonce] = now
        if len(_MISMATCH_LOGGED) > _MISMATCH_TRACKED:
            _MISMATCH_LOGGED.pop(next(iter(_MISMATCH_LOGGED)), None)
    try:
        from personalclaw.dashboard.session_store import load_session_records
        from personalclaw.sel import sel

        record = load_session_records().get(nonce)
        handle = record.device.id if record is not None else ""
        scope = device_scopes.scope_words(record.scope) if record is not None else ""
        sel().log_api_access(
            caller="token_auth",
            operation=device_scopes.ERR_SCOPE_MISMATCH,
            outcome="denied",
            source="token_auth",
            resources=f"session={handle} scope={scope}",
            error="the token's scope claim and its session's scope differ",
        )
    except Exception:  # noqa: BLE001 — the refusal stands; say the row could not be written
        logger.warning("could not write the scope-mismatch row to the SEL", exc_info=True)


def presented_scope(request: Any) -> tuple[str, ...]:
    """The scope a token *request* presents claims, when this gateway signed that token, whatever
    its session's state now; ``()`` for none.

    For the modes that admit a request without checking its session (``device_scopes.scope_of``):
    the cookie and the Bearer are read, and a claim counts only when its signature verifies
    (``token_auth._verified_claims``, which binds the claim to the scoped key)."""
    from personalclaw.dashboard import token_auth

    port = int(request.app.get("port") or 0) if hasattr(request, "app") else 0
    _presented, bearer = token_auth._bearer_credential(request)
    for token in (request.cookies.get(token_auth.session_cookie_name(request, port), ""), bearer):
        claims = token_auth._verified_claims(token) if token else None
        scope = tuple(claims.get(device_scopes.SCOPE_CLAIM) or ()) if claims else ()
        if scope:
            return scope
    return ()


# ── What a scoped session holds open (F9) ───────────────────────────────

#: nonce → the closers of what its session holds open: its sockets and its loops' streams.
_OPEN: dict[str, dict[int, Any]] = {}
_OPEN_LOCK = threading.Lock()


def hold_open(nonce: str, close: Any, *, ends: float = 0.0, loop: Any = None) -> Any:
    """Register *close* (a no-argument callable) to run when the session *nonce* names ends.
    Returns the release, which the holder calls when it closes by itself.

    With *ends* (the session's own end, in epoch seconds), the session's end closes it too, on
    *loop* (the running event loop unless one is given), unless it was released first. The
    release cancels that timer, so a holder that closed by itself leaves nothing waiting."""
    token = id(close)
    with _OPEN_LOCK:
        _OPEN.setdefault(nonce, {})[token] = close
    timer = None
    if ends:
        import asyncio

        timer = (loop or asyncio.get_running_loop()).call_later(
            max(0.0, ends - time.time()), _ran_out, nonce
        )

    def release() -> None:
        if timer is not None:
            timer.cancel()
        with _OPEN_LOCK:
            held = _OPEN.get(nonce)
            if held is not None:
                held.pop(token, None)
                if not held:
                    _OPEN.pop(nonce, None)

    return release


def _ran_out(nonce: str) -> None:
    """The session *nonce* reached its own end: close everything it holds open through
    :func:`close_for`, the path every sign-out closes through. The row's end is the store's to
    record (``END_EXPIRED`` is never passed in), so nothing else is written here."""
    close_for([nonce])


def close_for(nonces: Any) -> None:
    """Close everything the sessions *nonces* name hold open: they were signed out, ended idle or
    for a mismatch. Called by every ending (``token_auth._signed_out``). Never raises."""
    for nonce in nonces:
        with _OPEN_LOCK:
            closers = list((_OPEN.pop(nonce, None) or {}).values())
        for close in closers:
            try:
                close()
            except Exception:  # noqa: BLE001 — one closer that fails must not keep the others
                logger.warning("could not close what a signed-out session held open", exc_info=True)


def still_open(nonce: str, scope: tuple[str, ...]) -> None:
    """An open scoped socket counts as use: stamp its session as used now (R3-6)."""
    from personalclaw.dashboard import token_auth

    token_auth._state._admitted(nonce, tuple(scope), time.time())
