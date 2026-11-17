"""What an automation client's session may reach: the scope vocabulary, and its one refusal.

An automation client (a script, a CI job, another agent's runner) is paired like a phone, from a
code the owner makes in Settings → Devices, but the code names a *scope*: the parts of the
dashboard its session may use. The scope is one fact kept in two places, the signed ``scope``
claim on the token and the ``scope`` field of the session's row, and every request checks the
two agree (``token_auth``). The fixed vocabulary is :data:`SCOPES`; a session holds one or more.

Anything a scoped session may not do is answered with :func:`refused`, one body that names the
scopes it holds and never the route it asked for.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from aiohttp import web

# The vocabulary is core (`auth.automation_scopes`), so the pairing-code store can check a code's
# scope; it's named here too, where everything else about a scoped session lives.
from personalclaw.auth.automation_scopes import SCOPES, normalize_scope  # noqa: F401
from personalclaw.dashboard.device_scope_routes import (  # noqa: F401 — the table's names, here too
    DEVICE_SCOPE_ROUTES,
    FAMILIES,
    OUTSIDE,
    PUBLIC,
    SHELL,
    TARGET_LOOP,
    Row,
)

logger = logging.getLogger(__name__)

#: The scopes a pairing code may be made for in this release. The others answer or cause work,
#: which needs the client's own principal in the approval and loop doors; until those exist a
#: code naming one is refused, so no session holds a scope nothing enforces.
GRANTABLE: tuple[str, ...] = ("status", "loops")

#: The token claim, and the session row field, that carry the scope.
SCOPE_CLAIM = "scope"

#: The device kind of a scoped pairing. Set by the gateway alone, from the code's scope: a
#: client that declares it is ignored (``session_store.sanitize_device_kind``).
AUTOMATION_KIND = "automation"

#: An automation client unused this long is signed out (``session_store.END_IDLE``).
IDLE_END_SECS = 14 * 86400

#: A scoped pairing's lifetime, in days: chosen at pair/start, 30 unless the owner says.
DEFAULT_LIFETIME_DAYS = 30
MIN_LIFETIME_DAYS = 1
MAX_LIFETIME_DAYS = 90

#: Where the redeem page sends an automation client that has just paired.
LANDING = "/#/loops"

#: The wire code of the uniform refusal, and of a token whose scope its row doesn't hold.
ERR_SCOPE_REFUSED = "device_scope_refused"
ERR_SCOPE_MISMATCH = "device_scope_mismatch"


def scope_words(scope: Iterable[str]) -> str:
    """*scope* as the refusal names it: ``loops, status``."""
    return ", ".join(scope)


def refusal_message(scope: Iterable[str]) -> str:
    """The sentence of the uniform refusal: the scopes the session holds, never the route."""
    return f"This automation client can use only what its scope allows: {scope_words(scope)}."


def refused(scope: Iterable[str]) -> Any:
    """The uniform 403 for a scoped session: the same body whatever it asked for."""
    from personalclaw.http_errors import json_error

    return json_error(ERR_SCOPE_REFUSED, message=refusal_message(scope), status=403)


# ── The gate ────────────────────────────────────────────────────────────


def route_scope(method: str, route: str) -> Row:
    """The row for *method* on the canonical *route*, HEAD read as GET; :data:`OUTSIDE` for a
    route with no row, which is refused like one the table holds as outside."""
    verb = (method or "").upper()
    return DEVICE_SCOPE_ROUTES.get(("GET" if verb == "HEAD" else verb, route), OUTSIDE)


def in_a_family(route: str) -> bool:
    """Whether *route* sits under a family a scope touches, as a string prefix."""
    return route.startswith(FAMILIES)


def unclassified(served: Iterable[tuple[str, str]]) -> set[tuple[str, str]]:
    """The routes of *served* under a family that have no row: the census's finding."""
    return {(m, r) for m, r in served if in_a_family(r) and (m, r) not in DEVICE_SCOPE_ROUTES}


def route_admits(method: str, route: str, scope: Iterable[str]) -> bool:
    """Whether the row of *method* on *route* is one a session holding *scope* may reach, before
    its target rule."""
    row = route_scope(method, route)
    return row is not OUTSIDE and (row.scope in (SHELL, PUBLIC) or row.scope in set(scope))


def is_shell(request: Any) -> bool:
    """Whether *request* is for the shell every scoped session has: the page and the data-less
    build output a browser loads by itself (``sign_in_pair.pair_exempt``), less an installed
    app's page files, which say which apps are installed; and the ``/pair`` page."""
    from personalclaw.dashboard.sign_in_pair import an_apps_page_file, pair_exempt

    if request.method in ("GET", "HEAD") and request.path == "/pair":
        return True
    return pair_exempt(request) and not an_apps_page_file(request.path)


def scope_of(request: Any) -> tuple[str, ...]:
    """The scope of the automation client *request* comes from, or ``()``.

    What the sign-in check recorded (``request["device_scope"]``). Where nothing checked the
    request's session, with authentication off or behind the local-network bypass (F10), the
    scope a token it presents claims, which this gateway signed, whatever that session's state:
    a scoped client is never served as the owner by presenting its own cookie.
    """
    scope = tuple(request.get("device_scope") or ())
    if scope or not _unchecked(request):
        return scope
    from personalclaw.dashboard.scoped_sessions import presented_scope

    return presented_scope(request)


def _unchecked(request: Any) -> bool:
    from personalclaw.dashboard.origin import auth_is_off, local_network_bypass_enabled

    cfg = request.app.get("auth_cfg")
    return local_network_bypass_enabled() or (cfg is not None and auth_is_off(cfg))


def _template(request: web.Request) -> str:
    """The matched route's canonical template, or ``""`` for a request no route matched (a 404, a
    405), which is refused like any other outside its scope."""
    match_info = request.match_info
    if getattr(match_info, "http_exception", None) is not None:
        return ""
    resource = match_info.route.resource
    return resource.canonical if resource is not None else ""


async def _body(request: web.Request) -> Any:
    """The JSON object body, for the consent rule, read by the one reader every handler uses
    (``json_object_body``); ``None`` when there is none or it isn't an object. The handler refuses
    a body that isn't, through the same reader, so it carries no consent the handler would take.
    aiohttp keeps the bytes, so the handler reads the same body after."""
    from personalclaw.request_validation import RequestValidationError, json_object_body

    if not request.body_exists:
        return None
    try:
        return await json_object_body(request)
    except RequestValidationError:
        return None


async def _target_holds(row: Row, request: web.Request) -> bool:
    if row.target != TARGET_LOOP:
        return True
    from personalclaw.loop import store

    loop_id = str(request.match_info.get("id") or "")
    try:
        return bool(loop_id) and await asyncio.to_thread(store.get, loop_id) is not None
    except Exception:  # noqa: BLE001 — a store that can't say holds nothing: fail closed
        logger.warning("could not read the loop store for a scoped request", exc_info=True)
        return False


@web.middleware
async def device_scope_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """The gate a scoped session's every request meets, after token auth and the app gate.

    A request with no scope passes untouched. A scoped one reaches its route only when its row
    admits the scope, its target rule holds, and it gives no consent the row doesn't admit (F3).
    Anything else, an unmatched request included, gets :func:`refused`, before the route runs.
    """
    scope = scope_of(request)
    if not scope or is_shell(request):
        return await handler(request)
    from personalclaw.safety_flags import names_consent

    route = _template(request)
    row = route_scope(request.method, route) if route else OUTSIDE
    admitted = row is not OUTSIDE and (row.scope in (SHELL, PUBLIC) or row.scope in scope)
    if admitted and not row.consent and names_consent(await _body(request), request.query):
        admitted = False
    if admitted and not await _target_holds(row, request):
        admitted = False
    if not admitted:
        _note_refusal(request, route)
        return refused(scope)
    return await handler(request)


# ── What the security log says (F16a) ───────────────────────────────────

#: Per session: when its current minute began, how many requests it had refused, and which route
#: templates. One row a minute a session: the first refusal writes it, and the next minute's first
#: writes the count of the one before.
_REFUSALS: dict[str, tuple[float, int, set[str]]] = {}
_REFUSALS_LOCK = threading.Lock()
_REFUSALS_TRACKED = 64


def _note_refusal(request: web.Request, route: str) -> None:
    """Count a refused request against its session, and write the minute's row. Never the raw path
    or the body: the route template (``unmatched`` for a 404 or a 405) and the count."""
    nonce = str(request.get("session_nonce") or "")
    template = f"{request.method} {route}" if route else f"{request.method} unmatched"
    now = time.time()
    with _REFUSALS_LOCK:
        started, count, routes = _REFUSALS.get(nonce, (0.0, 0, set()))
        fresh = now - started >= 60.0
        if fresh:
            previous = (count, sorted(routes))
            started, count, routes = now, 0, set()
        _REFUSALS[nonce] = (started, count + 1, routes | {template})
        if len(_REFUSALS) > _REFUSALS_TRACKED:
            _REFUSALS.pop(next(iter(_REFUSALS)), None)
    if fresh:
        _write_refusal_row(request, template, previous)


def _write_refusal_row(
    request: web.Request, template: str, previous: tuple[int, list[str]]
) -> None:
    try:
        from personalclaw.dashboard.session_store import load_session_records
        from personalclaw.sel import sel

        record = load_session_records().get(str(request.get("session_nonce") or ""))
        handle = record.device.id if record is not None else ""
        before = f" previous_minute={previous[0]} ({', '.join(previous[1])})" if previous[0] else ""
        sel().log_api_access(
            caller=f"device:{handle}" if handle else "device",
            operation=ERR_SCOPE_REFUSED,
            outcome="denied",
            source="device_scopes",
            resources=f"{template}{before}",
            error="outside the session's scope",
        )
    except Exception:  # noqa: BLE001 — the refusal stands; say the row could not be written
        logger.warning("could not write the scope-refusal row to the SEL", exc_info=True)


# ── The socket (F11) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrameRow:
    """What a scoped socket may hear of one frame type: the scope that admits it, and whether a
    frame of it is heard whatever it is about (``subject_free``), or only about a loops-table row.
    """

    scope: str
    subject_free: bool = False


#: Every frame type a scoped socket hears; any other is unheard by construction. A loop's work
#: phase reaches a ``loops`` socket only when its work is a loops-table row's (``loop:<id>``);
#: the build the gateway serves is the shell's.
FRAME_SCOPES: dict[str, FrameRow] = {
    "work_phase": FrameRow("loops"),
    "spa_build": FrameRow(SHELL, subject_free=True),
}

#: The frames of a family a scope touches that no scope hears in this release (the census holds
#: each such frame to a verdict, here or in :data:`FRAME_SCOPES`).
FRAMES_UNHEARD: frozenset[str] = frozenset({"approval", "approval_resolved"})


def socket_hears(scope: Iterable[str], event_type: str, subject: Any) -> bool:
    """Whether a socket of a session holding *scope* may hear a frame of *event_type* about
    *subject* (``ws_state.frame_subject``)."""
    row = FRAME_SCOPES.get(event_type)
    if row is None or not (row.scope in (SHELL, PUBLIC) or row.scope in set(scope)):
        return False
    if row.subject_free:
        return True
    kind, key = subject if isinstance(subject, tuple) and len(subject) == 2 else ("", "")
    if kind != "work" or not isinstance(key, str) or not key.startswith("loop:"):
        return False
    from personalclaw.loop import store

    try:
        return store.get(key.removeprefix("loop:")) is not None
    except Exception:  # noqa: BLE001 — a store that can't say: the frame is unheard
        return False
