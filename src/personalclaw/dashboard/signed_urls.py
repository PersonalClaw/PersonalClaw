"""Signed resource URLs: what the browser loads by itself, on a browser's bound sign-in.

A page can't add a header to what the browser fetches for an ``<img>``, a ``<video>``, a download
or a new tab, so those requests carry the session cookie and no pair (:mod:`sign_in_pair`). On a
pairing gateway the page has each such URL signed first, here, and the signature stands in for the
pair beside the cookie of the same session.

* **The mint.** ``POST /api/auth/signed-urls`` with ``{"urls": [...]}`` answers each URL with
  ``pc_exp`` and ``pc_sig`` appended. It's honoured only on a request that carried its session's
  pair, and a URL it won't sign is answered in the same 200, beside the others.
* **The signature.** ``b64url(HMAC-SHA256(HMAC(key, "url\\0" + nonce), canonical))``. It's keyed by
  the session's nonce, never by its pair, so a signature is good for that session and nothing
  else. The canonical form is the method (HEAD as GET), the percent-encoded path, the query
  without ``pc_sig`` and ``pc_exp``, percent-encoded and sorted, then ``pc_exp``, joined by ``\\n``,
  which no encoded part can hold.
* **The expiry.** The session's end or two hours from now, whichever comes first. That outlasts a
  film's playback, and the page signs again at 80% of the way.
* **Where it's accepted** (:func:`verdict`): GET and HEAD, on :data:`SIGNABLE_ROUTES` only, beside a
  valid cookie of the same session. A signature for another session, path, method or query is
  refused, and so is any other ``pc_`` key.

The same shape as a deployed artifact's capability URL (``artifacts/deploy.SERVED_PATH``), with
the session as the capability's owner.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from typing import Any
from urllib.parse import quote

from aiohttp import web
from yarl import URL

#: The query keys a signed URL carries.
SIGNATURE_KEY = "pc_sig"
EXPIRY_KEY = "pc_exp"
#: The only keys the canonical form leaves out. Any other ``pc_`` key is refused.
SIGNED_KEYS = frozenset({SIGNATURE_KEY, EXPIRY_KEY})

#: The longest a signature lasts.
MAX_SIGNED_SECS = 2 * 3600
#: The most URLs one mint signs.
MAX_URLS = 64
#: The longest URL the mint reads.
MAX_URL_CHARS = 4096
#: The most digits a ``pc_exp`` may have: a second count far past any session's end.
MAX_EXPIRY_DIGITS = 12

#: Every route the browser loads by itself, from a page that can't add a header: four media routes
#: an element's ``src`` or ``data`` loads, and five a download or a new tab opens. A route outside
#: this tuple never accepts a signature. ``tests/test_signed_urls.py`` pins it.
SIGNABLE_ROUTES: tuple[str, ...] = (
    "/api/artifacts/{slug}/raw",
    "/api/file-raw",
    "/api/knowledge/items/{id}/file",
    "/api/knowledge/items/{id}/thumbnail",
    "/api/rooms/{room_id}/export",
    "/api/chat/sessions/{session}/export",
    "/api/inbox/{id}/attachments/{aid}",
    "/api/projects/{project_id}/export",
    "/api/dashboard/views/{view_id}/tiles/refresh",
)
#: One segment per parameter, the way the router matches it, so a deeper path or a sibling
#: doesn't inherit the route's place on the list.
_SIGNABLE = tuple(
    re.compile(re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(route))) for route in SIGNABLE_ROUTES
)


def signable(method: str, path: str) -> bool:
    """Whether a signature may stand in for the pair on *method* at the decoded *path*."""
    return method in ("GET", "HEAD") and any(route.fullmatch(path) for route in _SIGNABLE)


def _canonical(method: str, path: str, query: Any) -> str | None:
    """The signed form of a request, or ``None`` when its query holds a ``pc_`` key other than
    the two a signature adds. *path* is the URL's ``path_safe``: an encoded ``/`` or ``%`` stays
    encoded, so two paths the router tells apart never sign the same."""
    keys = list(query.keys())
    if any(key.startswith("pc_") and key not in SIGNED_KEYS for key in keys):
        return None
    pairs = sorted(
        (quote(key, safe=""), quote(value, safe=""))
        for key, value in query.items()
        if key not in SIGNED_KEYS
    )
    encoded = "&".join(f"{key}={value}" for key, value in pairs)
    return "\n".join(("GET" if method == "HEAD" else method, quote(path, safe="/"), encoded))


def _signature(secret: bytes, nonce: str, canonical: str, expiry: int) -> str:
    key = hmac.new(secret, b"url\0" + nonce.encode(), hashlib.sha256).digest()
    digest = hmac.new(key, f"{canonical}\n{expiry}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verdict(request: Any, nonce: str) -> bool | None:
    """Whether *request*'s signature admits it on the session *nonce* names: ``None`` when it
    carries none, so the pair is asked for as usual, else whether it holds.

    Called only beside a valid cookie of that session (``sign_in_pair.pair_refusal``).
    """
    from personalclaw.dashboard.token_auth import _secret

    query = request.rel_url.query
    if SIGNATURE_KEY not in query and EXPIRY_KEY not in query:
        return None
    if not signable(request.method, request.rel_url.path):
        return False
    signatures, expiries = query.getall(SIGNATURE_KEY, []), query.getall(EXPIRY_KEY, [])
    if len(signatures) != 1 or len(expiries) != 1 or not _an_expiry(expiries[0]):
        return False
    expiry = int(expiries[0])
    canonical = _canonical(request.method, request.rel_url.path_safe, query)
    if expiry < time.time() or canonical is None or not signatures[0].isascii():
        return False
    expected = _signature(_secret(), nonce, canonical, expiry)
    return hmac.compare_digest(expected.encode(), signatures[0].encode())


def _an_expiry(text: str) -> bool:
    """Whether *text* is a ``pc_exp`` the mint could have written: ASCII digits, and few of them."""
    return text.isascii() and text.isdigit() and len(text) <= MAX_EXPIRY_DIGITS


def sign(url: str, *, nonce: str, session_end: float, origin: str) -> dict[str, Any]:
    """*url* signed for the session *nonce* names, as the mint answers it: ``{url, signed, exp}``,
    or ``{url, error}`` when it isn't a URL this gateway signs. *origin* is the request's own,
    the one host an absolute URL may name."""
    refused = {"url": url, "error": "not_signable"}
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        return {"url": url if isinstance(url, str) else "", "error": "not_signable"}
    if url.startswith("//"):
        # Scheme-relative: another host's URL, whatever it names.
        return refused
    try:
        parsed = URL(url)
        if parsed.is_absolute():
            if str(parsed.origin()) != origin:
                return refused
            parsed = parsed.relative()
    except ValueError:
        return refused
    if parsed.fragment or not signable("GET", parsed.path):
        return refused
    canonical = _canonical("GET", parsed.path_safe, parsed.query)
    if canonical is None:
        return refused
    from personalclaw.dashboard.token_auth import _secret

    expiry = int(min(session_end, time.time() + MAX_SIGNED_SECS))
    signature = _signature(_secret(), nonce, canonical, expiry)
    signed = parsed.update_query({EXPIRY_KEY: str(expiry), SIGNATURE_KEY: signature})
    return {"url": url, "signed": str(signed), "exp": expiry}


async def api_signed_urls(request: web.Request) -> web.Response:
    """POST /api/auth/signed-urls — sign the page's resource URLs on its own sign-in.

    Body ``{"urls": [...]}``. Honoured only on a bound sign-in that carried its pair (a cookie with
    the pair, or a bound Bearer with it), which the token middleware has already checked: what
    reaches here without that is a script's unbound Bearer, or a gateway that signs nobody in, and
    neither has a cookie a signature could stand beside.
    """
    from personalclaw.dashboard.sign_in_pair import pair_for, pair_matches, presented_pair
    from personalclaw.dashboard.token_auth import _secret
    from personalclaw.http_errors import json_error
    from personalclaw.request_validation import json_object_body

    nonce = str(request.get("session_nonce") or "")
    _presented, pair = presented_pair(request)
    if not nonce or not pair_matches(pair_for(_secret(), nonce), pair):
        return json_error("signed_urls_need_a_page", status=403)
    body = await json_object_body(request)
    urls = body.get("urls") if isinstance(body, dict) else None
    if not isinstance(urls, list) or len(urls) > MAX_URLS:
        return json_error(
            "invalid_request",
            message=f"Send up to {MAX_URLS} URLs as a list in `urls`.",
            status=400,
        )
    session_end = float(request.get("session_expires_at") or 0.0) or time.time()
    origin = f"{request.scheme}://{request.host}"
    answers = [sign(url, nonce=nonce, session_end=session_end, origin=origin) for url in urls]
    return web.json_response({"urls": answers}, headers={"Cache-Control": "no-store"})
