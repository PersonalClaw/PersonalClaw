"""Opening a sign-in link (``?token=``): where it signs a browser in, and what it does elsewhere.

A link signs a browser in at exactly two places (:func:`exchange_point`):

* **a shell navigation** (``/?token=…``, or any page the shell answers): the shell is served
  ``no-store`` with the new sign-in's pair on the first-in-``<head>`` scrub script
  (``owner_token_url.inject_scrub``), which keeps it in the page's storage before any other script
  runs;
* **the dashboard's own fetch** of ``/api/auth/session?token=…`` (``freshSignIn.withLink``).

Both answers carry the pair in their ``X-PersonalClaw-Pair`` header too, which is what a fetch
reads, and what the web build's development server reads off the shell exchange it makes for its
own origin (``web/vite.config.ts``).

The exchange mints a NEW, bound session for the browser (:mod:`sign_in_pair`); it never hands the
link itself to the browser as its cookie. Its lifetime is what's left of the link's, so a channel's
one-hour link gives a one-hour sign-in. In order:

1. every refusal: an app's link (it only narrows), a link with nothing left of its life, and a
   startup link another browser already opened (``try_consume``);
2. the mint, which doesn't count the sign-in it replaces against the browser limit;
3. the answer;
4. only once the answer stands: the replaced sign-in's pushes move to the minted one, the replaced
   sign-in ends, and a startup link's own session ends (``END_LINK_OPENED``), so one exchange
   leaves one live session.

A failed answer delivered nothing, so it ends only the minted sign-in: the browser keeps the
sign-in and the pushes it had, and a startup link it spent can be opened again.

Anywhere else, and in the desktop app's window, whose own sign-in a link never replaces, a link
authorizes that one request, sets no cookie and gives its row no browser kind. A ``personalclaw
token`` or ready-line link stays a token either way, so it still works as a Bearer and a browser
counts once.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from aiohttp import web

from personalclaw.dashboard import token_auth as ta
from personalclaw.dashboard.pair_refusals import link_opened_notice
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, bound, serves_the_shell

#: Where the dashboard's own fetch exchanges a link (``web/src/lib/freshSignIn.ts``).
FETCH_EXCHANGE_PATH = "/api/auth/session"

Adopt = Callable[[web.Request, Any], None]
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def exchange_point(request: web.Request) -> str:
    """``"shell"`` or ``"fetch"`` where *request* exchanges its link for a browser's sign-in, or
    ``""`` where the link only authorizes this one request. Only a GET exchanges: a HEAD, which
    the router answers like a GET, spends nothing and sets no cookie."""
    if request.method == "GET" and serves_the_shell(request):
        return "shell"
    if request.method == "GET" and request.path == FETCH_EXCHANGE_PATH:
        return "fetch"
    return ""


def _live_bound_cookie(request: web.Request, port: int) -> Any:
    """The credentials of a live bound sign-in in *request*'s cookie, or ``None``."""
    cookie = request.cookies.get(ta.session_cookie_name(request, port), "")
    if not cookie or not bound(cookie):
        return None
    valid, user, _reason, app = ta.validate_token_with_app(cookie, use_session_exp=True)
    if not valid or app:
        return None
    return ta._Credentials(token=cookie, source="cookie", user_id=user)


def keeps_its_cookie(request: web.Request, port: int, credentials: Any) -> Any:
    """A refused link on a shell navigation, beside a live bound cookie, answers as if the query
    weren't there: the shell, its URL scrubbed. The same browser reopening the printed link isn't
    signed out by it, and the shell holds no data, so this opens nothing."""
    if credentials.source != "query" or credentials.valid or not serves_the_shell(request):
        return credentials
    return _live_bound_cookie(request, port) or credentials


def _in_a_window(request: web.Request, port: int) -> bool:
    """Whether *request*'s cookie holds the desktop window's own sign-in, which a link opened in
    the window never replaces: there it only authorizes the one request."""
    from personalclaw.dashboard.session_store import ISSUER_WINDOW, load_session_records

    cookie = _live_bound_cookie(request, port)
    if cookie is None:
        return False
    record = load_session_records().get(ta.token_nonce(cookie.token))
    return record is not None and record.issuer == ISSUER_WINDOW


def _previous_sign_in(request: web.Request, port: int, link_nonce: str) -> str:
    """The nonce of the owner sign-in this browser's cookie held before, or ``""``."""
    previous = request.cookies.get(ta.session_cookie_name(request, port), "")
    if not previous:
        return ""
    valid, _user, _reason, app = ta.validate_token_with_app(previous, use_session_exp=True)
    nonce = ta.token_nonce(previous) if valid and not app else ""
    return nonce if nonce and nonce != link_nonce else ""


async def _as_the_cookie(
    request: web.Request, handler: Handler, port: int, adopt: Adopt, refusal: web.Response
) -> web.StreamResponse:
    """A spent link on a shell navigation, beside a live bound cookie, is the cookie's request;
    anywhere else, *refusal*."""
    cookie = _live_bound_cookie(request, port) if serves_the_shell(request) else None
    if cookie is None:
        return refusal
    adopt(request, cookie)
    return await handler(request)


async def open_link(
    request: web.Request,
    handler: Handler,
    credentials: Any,
    *,
    port: int,
    client_ip: str,
    adopt: Adopt,
) -> web.StreamResponse:
    """Answer *request*, whose valid credential is the link *credentials* holds."""
    from personalclaw.dashboard.session_store import ISSUER_UNKNOWN, load_session_records

    token, user_id = credentials.token, credentials.user_id
    if not ta.check_token_ip(token, client_ip):
        ta._log_auth(request, user_id, "denied", "IP mismatch")
        refused = ta._deny(request, "IP mismatch", notice=ta.link_used_notice())
        return await _as_the_cookie(request, handler, port, adopt, refused)
    session_end = ta._session_end(token)
    ta.bind_token_ip(token, client_ip, session_end)
    point = "" if _in_a_window(request, port) else exchange_point(request)
    if not point:
        adopt(request, replace(credentials, source="link"))
        resp = await handler(request)
        ta._log_auth(request, user_id, "ok", "", identity=request["session_nonce"])
        return resp

    link_nonce = ta.token_nonce(token)
    record = load_session_records().get(link_nonce)
    issuer = record.issuer if record is not None else ISSUER_UNKNOWN
    left = session_end - time.time()
    remaining = max(1, int(left))
    if credentials.app or left <= 0:
        ta._log_auth(request, user_id, "denied", "a link that signs no browser in")
        return ta._deny(request, "link refused", notice=ta.not_signed_in_notice())
    if issuer == ta.ISSUER_STARTUP and not ta.try_consume(token, session_end):
        ta._log_auth(request, user_id, "denied", "startup link already opened")
        refused = ta._deny(request, "link opened", notice=link_opened_notice())
        return await _as_the_cookie(request, handler, port, adopt, refused)

    # The link was seen here, as a link: its row keeps its kind, and the browser gets a row of its
    # own, the minted one.
    ta.note_session_client(link_nonce, request, "link", ip=client_ip)
    previous = _previous_sign_in(request, port, link_nonce)
    device = ta.client_of(request)
    # The presence clock runs from the link's own sign-in, never from the exchange.
    device.minted_at = record.device.minted_at if record is not None else 0.0
    minted = ta.mint_session(
        user_id, remaining, issuer=issuer, device=device, bound=True, spare=previous
    )
    adopt(request, replace(credentials, token=minted.token, source="cookie"))
    request["sign_in_pair"] = minted.pair
    startup = issuer == ta.ISSUER_STARTUP
    try:
        resp = await handler(request)
    except BaseException:
        _take_back(minted, token if startup else "")
        raise
    if resp.status >= 400:
        _take_back(minted, token if startup else "")
        return resp
    if previous:
        ta._keep_the_browser_s_pushes(previous, minted.nonce)
        ta.sign_out([previous], ta.END_REPLACED, actor=user_id or "owner")
    if startup:
        ta.sign_out([link_nonce], ta.END_LINK_OPENED, actor=user_id or "owner")
    _deliver(request, resp, minted, port=port, remaining=remaining)
    ta._log_auth(request, user_id, "ok", "", identity=request["session_nonce"])
    return resp


def _take_back(minted: Any, startup_link: str) -> None:
    """The answer failed after the mint, so no browser received the minted sign-in: it ends, and
    a startup link it spent (*startup_link*) can be opened again, released first so that it never
    reads as spent with no sign-in to show for it. The mint changed nothing else, except at the cap
    of browser sign-ins (``session_store.POOL_CAPS``), where it ended the least recently used one,
    which stays ended."""
    if startup_link:
        ta.release_consumed(startup_link)
    ta.sign_out([minted.nonce], ta.END_SIGNED_OUT, actor="system")


def _deliver(
    request: web.Request,
    resp: web.StreamResponse,
    minted: Any,
    *,
    port: int,
    remaining: int,
) -> None:
    """Set the minted sign-in's cookie on *resp*, and hand its pair to the page: in the answer's
    header, and on the shell's scrub script (already in the document, from
    ``request["sign_in_pair"]``). Neither answer may be kept by a cache."""
    resp.set_cookie(
        ta.session_cookie_name(request, port),
        minted.token,
        httponly=True,
        samesite="Lax",
        path="/",
        # The session's own end, plus the grace that lets its end be explained.
        max_age=min(remaining, ta.MAX_SESSION_TTL_SECS) + ta.SIGNED_OUT_NOTICE_GRACE_SECS,
        secure=ta.secure_cookies(),
    )
    # Clear the non-port-specific cookie so only the port's own is used.
    resp.set_cookie("pc_token", "", max_age=0, path="/")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers[PAIR_HEADER] = minted.pair
