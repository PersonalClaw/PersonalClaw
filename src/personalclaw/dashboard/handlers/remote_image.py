"""``POST /api/images/remote``: an image a shown text names on another site, fetched on a press.

The dashboard's page loads no image from another host (``server.dashboard_csp``'s ``img-src``). An
image in text the dashboard did not write is drawn as a placeholder naming its host
(``web/src/ui/RemoteImage.tsx``), and its Load button posts the address here. The gateway fetches
it under the egress guard's ``REMOTE_IMAGE`` profile (``net/policy.py``): public hosts only, the
address pinned, every redirect checked again and the owner's Denied hosts applied. The request is
audited under this route's door, with every refusal. Only a raster image comes back, told by its
first bytes, under the profile's size cap and one deadline over the whole load, and it is kept by
no cache. The fetch runs on a few workers of its own, so slow sites hold up no other work of the
gateway's. A load still waiting for one of them at its deadline asked its site nothing, and is told
to press Load again.

POST only, so nothing a page draws can make the request. The owner's own sign-in only: an app's
token is refused. The origin check and the sign-in's other checks are the gateway's, as for every
owner POST.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.memory_writes import ScopeCarryingExecutor
from personalclaw.net import client as net_client
from personalclaw.net.guard import EGRESS_SETTINGS, PLACES
from personalclaw.net.policy import REMOTE_IMAGE, EgressPolicy, remote_image_egress_policy
from personalclaw.request_validation import json_object_body

logger = logging.getLogger(__name__)

#: The route, and the door its requests are audited under (``net.client.audit``).
ROUTE = "/api/images/remote"
DOOR = "dashboard.remote_image"

#: The longest address taken. A page may name a longer one (a signed link, say); it is refused in
#: words the placeholder shows.
MAX_URL_CHARS = 4096

#: One deadline over the whole load, the guard's lookups and every redirect included: the
#: profile's own timeout holds each hop alone.
DEADLINE_S = REMOTE_IMAGE.timeout_s

#: How many loads run at once. More wait their turn, on these workers alone: the gateway's shared
#: pool is never held by a slow site.
WORKERS = 4
_pool: ScopeCarryingExecutor | None = None


def _workers() -> ScopeCarryingExecutor:
    """The loads' own workers, made at the first load, in the process that runs it."""
    global _pool
    if _pool is None:
        _pool = ScopeCarryingExecutor(max_workers=WORKERS, thread_name_prefix="remote-image")
    return _pool


#: Each raster image type the route answers with, by the bytes its file begins with.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: What the gateway asks the far site for: the types above, and nothing else.
_ACCEPT = "image/avif,image/webp,image/png,image/jpeg,image/gif"


def raster_type(body: bytes) -> str:
    """The image type *body* is by its first bytes (PNG, JPEG, GIF, WebP or AVIF), or ``""``.

    What the far site's ``Content-Type`` says is not read: a page or an SVG that calls itself a
    PNG is still a page, and only a picture with no markup and no script comes back."""
    for signature, kind in _SIGNATURES:
        if body.startswith(signature):
            return kind
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if body[4:8] == b"ftyp" and body[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return ""


def _scheme_and_host(url: str) -> tuple[str, str]:
    """*url*'s scheme and host, or ``""`` for what can't be read: an address the guard refused as
    malformed may not parse a second time."""
    try:
        parts = urlparse(url)
        return parts.scheme.lower(), parts.hostname or ""
    except ValueError:
        return "", ""


def _refused(exc: net_client.EgressBlocked, asked: str) -> str:
    """Why the guard refused the image's address (*asked*, or a redirect from it), in a sentence
    for the owner. A private or loopback address is named as such, not as something to allow: an
    image from another site loads from a public address or not at all."""
    decision = exc.decision
    scheme, parsed_host = _scheme_and_host(decision.url)
    host = decision.host or parsed_host or "its site"
    category = decision.category or ""
    if not category and "redirect" in (decision.reason or ""):
        return (
            "The image was not loaded: its site redirected it more than "
            f"{REMOTE_IMAGE.max_redirects} times."
        )
    if category == "deny_list":
        return f"The image was not loaded: {host} is on Denied hosts in {EGRESS_SETTINGS}."
    if category == "unresolvable":
        return f"The image was not loaded: {host} could not be found."
    if category == "malformed":
        if decision.url and decision.url != asked:
            where = "a plain-http address"
            if scheme != "http":
                where = "an address PersonalClaw doesn't load"
            return f"The image was not loaded: its site sent it on to {where}."
        return "The image was not loaded: its address is not one PersonalClaw loads."
    place = PLACES.get(category, "an address that is not public")
    return (
        f"The image was not loaded: {host} is on {place}, and an image from another site loads "
        "only from a public address."
    )


def _fetch_on_a_thread(
    url: str,
    policy: EgressPolicy,
    deadline: float,
    began: threading.Event | None = None,
) -> net_client.FetchResponse:
    """The guarded fetch, on a loop of its own, given until *deadline* (``time.monotonic()``). The
    guard looks each host up as it goes, which blocks and can't be cut: a lookup still under way at
    the deadline runs to the resolver's own timeout, and the fetch stops at its next step. A load
    that waited its turn past the deadline starts nothing; *began* is set once one does start."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    if began is not None:
        began.set()

    async def bounded() -> net_client.FetchResponse:
        # The handler's own deadline: the loop's clock is time.monotonic().
        async with asyncio.timeout_at(deadline):
            return await net_client.fetch_as(DOOR, url, policy=policy, headers={"Accept": _ACCEPT})

    return asyncio.run(bounded())


async def api_remote_image(request: web.Request) -> web.StreamResponse:
    """The image at the ``https`` address the page sends as ``url``, or a refusal.

    The image comes back with its type, ``Cache-Control: no-store`` and
    ``X-Content-Type-Options: nosniff``."""
    if request.get("app"):
        return json_error("remote_image_owner_only", status=403)
    url = (await json_object_body(request, empty_ok=False)).get("url")
    url = url.strip() if isinstance(url, str) else ""
    if not url:
        return json_error(
            "invalid_request", message="Send the image's address as `url`.", status=400
        )
    if len(url) > MAX_URL_CHARS:
        return json_error(
            "invalid_request",
            message="The image was not loaded: its address is longer than PersonalClaw loads.",
            status=400,
        )
    policy = remote_image_egress_policy()
    deadline = time.monotonic() + DEADLINE_S
    began = threading.Event()
    loop = asyncio.get_running_loop()
    try:
        answer = await asyncio.wait_for(
            loop.run_in_executor(_workers(), _fetch_on_a_thread, url, policy, deadline, began),
            timeout=DEADLINE_S,
        )
    except net_client.EgressBlocked as exc:
        if exc.decision.category == "unresolvable":
            # A name that doesn't resolve now may later: a failure a press can clear.
            return json_error("remote_image_unreachable", message=_refused(exc, url), status=502)
        return json_error("remote_image_refused", message=_refused(exc, url), status=403)
    except TimeoutError:
        if not began.is_set():
            return json_error("remote_image_busy", status=503)
        return json_error(
            "remote_image_unreachable",
            message="The image's site took too long to send it, so nothing was shown.",
            status=502,
        )
    except (aiohttp.ClientError, ValueError) as exc:
        logger.debug("a remote image's site could not be reached: %s", type(exc).__name__)
        return json_error(
            "remote_image_unreachable",
            message="The image's site could not be reached, so nothing was shown.",
            status=502,
        )
    except Exception:
        logger.warning("loading a remote image failed", exc_info=True)
        return json_error("internal_error", status=500)
    if answer.status != 200:
        return json_error(
            "remote_image_unreachable",
            message=f"The image's site answered {answer.status}, not with the image.",
            status=502,
        )
    if answer.truncated:
        return json_error("remote_image_too_large", status=413)
    kind = raster_type(answer.body)
    if not kind:
        return json_error("remote_image_not_an_image", status=415)
    return web.Response(
        body=answer.body,
        content_type=kind,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def register_remote_image_routes(app: web.Application) -> None:
    """The one route, POST only: a GET of it is answered 405 by the router."""
    app.router.add_post("/api/images/remote", api_remote_image)
