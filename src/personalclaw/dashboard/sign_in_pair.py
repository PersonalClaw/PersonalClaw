"""A browser's sign-in, and the pair that only its own page holds.

A session a browser signs in with is *bound*: its token carries the signed claim
:data:`BOUND_CLAIM`, and every request on it presents the session's **pair** beside the cookie. The
pair is derived from the session, handed to the page as it signs in, and kept in the page's own
origin storage.

* :func:`pair_for` derives it. Nothing is stored: it's an HMAC of the session's nonce under the key
  every sign-in is signed with, so replacing that key, which ends every session, changes it too.
* :func:`presented_pair` reads it off a request: the :data:`PAIR_HEADER` header, and, only when
  that is absent, on a WebSocket upgrade, the ``pc-pair.<pair>`` subprotocol entry the dashboard
  offers beside ``personalclaw`` (:mod:`socket_protocols`). A page's own socket can't set a
  header, so it offers the subprotocol; the desktop app's main process adds the header to its
  windows' upgrades and leaves the page's offer in place, so the header decides.
* :func:`pair_exempt` is the short list a browser fetches by itself, with no script of the page to
  add a header: the shell document and the data-less build files around it. A valid cookie still
  has to come with them. Only the pair is waived.
* :func:`ends_its_own_sign_in`: a sign-in's own Bearer can end that sign-in. It only ends, and
  grants nothing.

The pair never goes in a cookie, a URL, a log line or the security event log, and no answer
carries it except the sign-ins that hand it to the page.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any

from personalclaw.dashboard.socket_protocols import PAIR_PROTOCOL_PREFIX

#: The header every request on a bound session carries the pair in.
PAIR_HEADER = "X-PersonalClaw-Pair"
#: The signed claim that marks a session as bound to its pair.
BOUND_CLAIM = "pb"

#: A pair's only shape: 32 bytes of HMAC, base64url without padding.
_PAIR_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")

#: The ``detail.reason`` of a refusal on a live bound session, by what was missing or wrong.
#: A missing pair, an unpaired navigation and a refused signature are no ending: their answers
#: carry no ``X-Auth-Required``, so a page keeps the pair it holds and asks again.
REFUSED_PAIR_MISSING = "pair_missing"
REFUSED_NAVIGATION_UNPAIRED = "navigation_unpaired"
REFUSED_SIGNATURE = "signature_refused"
#: A wrong pair and a cookie holding a session that isn't bound end the page's sign-in: their
#: answers carry ``X-Auth-Required``, which every client reads as signed out.
REFUSED_PAIR_WRONG = "pair_wrong"
REFUSED_UNBOUND_COOKIE = "unbound_cookie"
#: The build capability the ready line and a proven healthz answer carry, whatever the auth mode:
#: this gateway has the desktop window's own door (``GET /api/token/local?for=window``). A desktop
#: app asks that door only of a gateway that says so (``desktop/homeGateway.js`` reads the same
#: name, ``tests/test_the_window_door_flag_parity.py``).
WINDOW_DOOR = "window_door"
SIGNS_THE_PAGE_OUT = frozenset({REFUSED_PAIR_WRONG, REFUSED_UNBOUND_COOKIE})

#: Build output a browser fetches by itself, at the origin's root: the worker, the manifest and
#: the licence notices. With :data:`_EXEMPT_PREFIXES`, the shell (:func:`pair_exempt`) and the
#: always-open static prefixes ``token_auth`` bypasses, it's every file a browser loads with no
#: script of the page asking.
_EXEMPT_FILES = frozenset(
    {
        "/sw.js",
        "/manifest.webmanifest",
        "/THIRD_PARTY_NOTICES.txt",
        "/THIRD_PARTY_NOTICES_NPM.txt",
    }
)
_EXEMPT_PREFIXES = ("/icons/",)
#: An installed app's static page files (``handlers/apps.api_app_ui_asset``). Its backend, under
#: ``/apps/{name}/api/``, isn't on the list.
_APP_UI = re.compile(r"/apps/[^/]+/ui/.*")

#: The prefixes a family of routes owns, where no path is ever the shell, matched or not: the API
#: (the sockets under it), an app's backend, the static build files, and the surfaces that sign
#: their own callers in. An unmatched path under one of them is refused like any other request
#: there, never answered with the shell on the cookie alone.
_ROUTE_FAMILIES = re.compile(
    r"/(?:api|ws|assets|icons|sprites|vendor|fonts|v1|a2a|capture|mcp|artifacts/serve)(?:/|$)"
    r"|/apps/[^/]+/api(?:/|$)"
)


def is_pair(value: object) -> bool:
    """Whether *value* has a pair's shape. Every reader checks this before it compares."""
    return isinstance(value, str) and _PAIR_SHAPE.fullmatch(value) is not None


def pair_for(secret: bytes, nonce: str) -> str:
    """The pair of the session *nonce* names, under the signing key *secret*."""
    digest = hmac.new(secret, b"pair\0" + nonce.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def presented_pair(request: Any) -> tuple[bool, str]:
    """``(presented, pair)`` for *request*: whether it presents a pair at all, and the pair when
    it has a pair's shape. A malformed one is presented but unusable: ``(True, "")``.

    The header first, whenever it is there, on an upgrade too: the desktop app adds it to its
    windows' upgrades beside the page's offered subprotocol, and a wrong header is never rescued by
    a right subprotocol. Without a header, the first ``pc-pair.*`` entry of the offered
    subprotocols, which is how a page's own socket presents it.
    """
    headers = getattr(request, "headers", None) or {}
    header = headers.get(PAIR_HEADER)
    if header is not None:
        value = str(header).strip()
        return True, value if is_pair(value) else ""
    getall = getattr(headers, "getall", None)
    offered = getall("Sec-WebSocket-Protocol", []) if callable(getall) else []
    for entry in offered:
        for name in (part.strip() for part in str(entry).split(",")):
            if name.startswith(PAIR_PROTOCOL_PREFIX):
                value = name[len(PAIR_PROTOCOL_PREFIX) :]
                return True, value if is_pair(value) else ""
    return False, ""


def started_by_digest(local_secret: str) -> str:
    """What a desktop window's sign-in records of the gateway start that minted it: a digest of
    that start's local secret, never the secret (``session_store.SessionRecord.started_by``)."""
    return hashlib.sha256(b"window\0" + local_secret.encode()).hexdigest()


def pair_matches(expected: str, presented: str) -> bool:
    """Whether *presented* is the pair *expected*, in constant time."""
    return bool(presented) and hmac.compare_digest(expected.encode(), presented.encode())


def is_navigation(request: Any) -> bool:
    """Whether *request* is the browser loading a page into a window, a tab or a frame."""
    headers = getattr(request, "headers", None) or {}
    mode = str(headers.get("Sec-Fetch-Mode") or "").lower()
    dest = str(headers.get("Sec-Fetch-Dest") or "").lower()
    return mode == "navigate" or dest in ("document", "iframe", "frame")


def serves_the_shell(request: Any) -> bool:
    """Whether the router answers *request* with the dashboard's shell document: ``/``, or a page
    path no route matches, outside every prefix a family of routes owns (:data:`_ROUTE_FAMILIES`),
    which ``fallbacks.spa_fallback`` answers with the shell for the web app's own router. The shell
    is the same build output for every one of them: no data, no cookie, nothing of this sign-in."""
    if request.method not in ("GET", "HEAD"):
        return False
    path = request.path
    if path == "/":
        return True
    if _ROUTE_FAMILIES.match(path):
        return False
    match_info = getattr(request, "match_info", None)
    return getattr(match_info, "http_exception", None) is not None


def bound(token: str) -> bool:
    """Whether *token* claims to be a browser's bound sign-in. Read without checking the
    signature, so it may only ever REFUSE: a forged claim is refused like any forgery."""
    encoded = (token or "").split(".", 1)[0]
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (binascii.Error, TypeError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get(BOUND_CLAIM))


def pair_refusal(request: Any, token: str, source: str) -> str:
    """Why the valid session *token*, on *source*, can't authorize *request* as it is, or ``""``.

    The cookie carries only a browser's bound sign-in, and a bound sign-in, on the cookie or as a
    Bearer, needs its pair. An unbound Bearer (the CLI, a script) and a link are what they always
    were. Checked in the order the refusals are told: a cookie that isn't bound, then a missing
    pair, then a wrong one. The cookie alone is enough for the files a browser loads by itself
    (:func:`pair_exempt`), and for a resource URL the page had signed (``signed_urls.verdict``). A
    bound sign-in's Bearer alone is enough for its own sign-out (:func:`ends_its_own_sign_in`).
    """
    from personalclaw.dashboard.token_auth import _secret, token_nonce

    if source == "query":
        return ""
    if not bound(token):
        return REFUSED_UNBOUND_COOKIE if source == "cookie" else ""
    if source == "cookie" and pair_exempt(request):
        return ""
    if source == "header" and ends_its_own_sign_in(request):
        return ""
    presented, pair = presented_pair(request)
    if pair and pair_matches(pair_for(_secret(), token_nonce(token)), pair):
        return ""
    if presented:
        return REFUSED_PAIR_WRONG
    if source == "cookie":
        from personalclaw.dashboard.signed_urls import verdict

        signed = verdict(request, token_nonce(token))
        if signed:
            request["admitted_by_signature"] = True
            return ""
        if signed is not None:
            return REFUSED_SIGNATURE
        if is_navigation(request):
            return REFUSED_NAVIGATION_UNPAIRED
    return REFUSED_PAIR_MISSING


#: The route that ends the session that authorized it, and nothing else (``handlers/auth``).
SIGN_OUT_PATH = "/api/auth/logout"


def ends_its_own_sign_in(request: Any) -> bool:
    """Whether *request* is a sign-out, which ends only the session that authorized it: on its own
    Bearer that needs no pair, since it grants nothing, and the desktop app may hold a window's
    sign-in whose pair it no longer has. A sign-out the cookie carries keeps the pair rule."""
    return (
        getattr(request, "method", "") == "POST" and getattr(request, "path", "") == SIGN_OUT_PATH
    )


def pair_exempt(request: Any) -> bool:
    """Whether *request* is one a browser makes by itself, where a valid cookie is enough.

    GET and HEAD only, and only for data-less build output: the shell (:func:`serves_the_shell`),
    the files in :data:`_EXEMPT_FILES`, the icons, and an installed app's static page files.
    """
    if request.method not in ("GET", "HEAD"):
        return False
    path = request.path
    return (
        path in _EXEMPT_FILES
        or path.startswith(_EXEMPT_PREFIXES)
        or _APP_UI.fullmatch(path) is not None
        or serves_the_shell(request)
    )
