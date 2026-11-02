"""JS-render fetch path — render a client-side page in a headless browser.

Some pages return an empty shell over HTTP and build their content with JavaScript;
the plain ``net.fetch`` path sees only the shell. ``render_url`` drives a headless
Chromium (Playwright) to execute the page's JS and returns the *rendered* HTML for the
shared extractor.

A headless browser does its own DNS and connections, so left alone it would bypass
``net.fetch``'s IP-pinning SSRF guard for everything the page loads. To keep the egress
invariant ("nothing reaches an attacker-influenced private host"), the browser makes no
request of its own: every request the render makes (the page, its subresources and frames,
its script requests) is routed back here, asked of the SAME egress guard
(``net.guard.evaluate``) under the run's policy, refused and audited (``door="web.render"``)
when denied, and fetched through ``net.fetch`` when allowed, which dials the address the guard
checked and follows a redirect itself, asking the guard of each hop: Chromium sends the next hop
of a redirect a route answers on its own network, past the route. A page whose own address
redirects is then opened at the address it moved to (the route ends that first navigation, and
the page goes to the final address, routed and checked in turn), so its relative links and its
scripts' ``location`` read the address it is at. A frame's own redirect is followed the same way
but the frame is not moved: it stays at the address it asked for, its relative links resolving
against that. What routing cannot carry is
refused: a scheme but http and https (the guard's own rule), a service worker (blocked on the
context), and a socket or a worker the page would start (``WebSocket``, ``WebTransport``,
WebRTC, ``Worker``, ``SharedWorker``, taken away before the page runs: the page's own scripts
are where that removal reaches). The browser's own network, for anything routing misses, is a
listener this render holds on 127.0.0.1 for as long as it runs, with loopback held to it too: it
refuses every connection that reaches it and audits it as a ``web.render`` refusal, and holding
the port means no other program can take it.

Playwright is an OPTIONAL dependency (``pip install personalclaw[js-render]`` +
``playwright install chromium``). When absent, ``render_url`` returns a clear
unavailable result so ``web_fetch`` degrades to the plain HTTP fetch rather than failing.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from personalclaw.net.client import EgressBlocked, audit, fetch_as
from personalclaw.net.guard import evaluate
from personalclaw.net.policy import STRICT, EgressPolicy

logger = logging.getLogger(__name__)

# Default time budget for a render (navigation + network-idle settle), ms.
_DEFAULT_NAV_TIMEOUT_MS = 20000

#: The door a render's egress rows name (``net.client.audit``).
_DOOR = "web.render"

#: The browser's own network, for anything request routing does not carry: a proxy that is a
#: listener of the render's own (:func:`_dead_end`), loopback held to it too (Chromium bypasses a
#: proxy for loopback unless told not to), and WebRTC kept off UDP that does not go through it.
_LOOPBACK_TOO = "<-loopback>"
_NO_NETWORK_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",)

#: What a refusal at the render's own listener says: something the page did reached for the
#: network without a route.
_PAST_THE_ROUTE = "the render's browser reached for the network itself, past the route"

#: Run before any of the page's own scripts: the sockets a page can open, which request routing
#: does not see, are taken away.
_NO_SOCKETS = """(() => {
  const refused = function () {
    throw new DOMException("this page cannot open a connection here", "SecurityError");
  };
  for (const name of ["WebSocket", "WebTransport", "RTCPeerConnection",
                      "webkitRTCPeerConnection", "RTCDataChannel", "Worker", "SharedWorker"]) {
    try { Object.defineProperty(window, name, { value: refused, configurable: false }); }
    catch (e) {}
  }
})();"""

#: Request headers the egress layer sets itself, and response headers that describe the bytes
#: as sent (the body arrives decoded, its length the decoded one).
_REQUEST_DROPPED = frozenset({"host", "connection", "content-length", "accept-encoding"})
_RESPONSE_DROPPED = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)


def is_available() -> bool:
    """Whether the Playwright dependency is importable (the browser binary is a
    separate `playwright install` step, surfaced as a runtime error if missing)."""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


@dataclass
class RenderResult:
    """Outcome of a JS render. ``ok=False`` with ``unavailable=True`` means Playwright
    isn't installed (caller should fall back to plain fetch); other failures carry an
    error + recovery hints."""

    ok: bool
    url: str = ""
    html: str = ""
    status: int = 0
    unavailable: bool = False
    error: str = ""
    recovery_hints: list[str] | None = None
    risk_level: str = "safe"


async def render_url(
    url: str,
    *,
    policy: EgressPolicy = STRICT,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    resolver=None,
) -> RenderResult:
    """Render ``url`` in headless Chromium and return its post-JS HTML.

    Egress is pre-validated through the same guard the HTTP path uses, so a private/
    loopback/IMDS target is denied before the browser launches. ``resolver`` is
    injectable for testing the guard without real DNS.
    """
    if not is_available():
        return RenderResult(
            ok=False,
            url=url,
            unavailable=True,
            error="JS rendering is not available (Playwright is not installed).",
            recovery_hints=[
                "Install it: pip install 'personalclaw[js-render]' && playwright install chromium.",
                "Without it, web_fetch uses the plain HTTP fetch (no JS execution).",
            ],
        )

    # Egress pre-flight — a headless browser bypasses net.fetch's IP pinning, so the
    # guard MUST run here before any navigation. Denied → never reaches the browser, and the
    # refusal is in the security log as a refused fetch's is.
    guard_kw = {"resolver": resolver} if resolver is not None else {}
    decision = evaluate(url, policy, **guard_kw)
    if not decision.allow:
        audit(url, policy, outcome="denied", reason=decision.reason, door="web.render")
        return RenderResult(
            ok=False,
            url=url,
            error=decision.reason,
            recovery_hints=list(decision.recovery_hints),
            risk_level=decision.risk_level,
        )

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - guarded by is_available()
        return RenderResult(
            ok=False, url=url, unavailable=True, error=f"Playwright import failed: {exc}"
        )

    #: The address the page's own navigation moved to, when it redirected (:func:`_route`).
    moved: list[str] = []

    async def guarded(route: Any, request: Any) -> None:
        await _route(route, request, policy=policy, resolver=resolver, moved=moved)

    at, dead_end = url, None
    try:
        dead_end = await _dead_end(policy)
        port = dead_end.sockets[0].getsockname()[1]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                proxy={"server": f"http://127.0.0.1:{port}", "bypass": _LOOPBACK_TOO},
                args=list(_NO_NETWORK_ARGS),
            )
            try:
                context = await browser.new_context(service_workers="block")
                await context.add_init_script(script=_NO_SOCKETS)
                await context.route("**/*", guarded)
                page = await context.new_page()
                resp = None
                for _hop in range(policy.max_redirects + 1):
                    moved.clear()
                    try:
                        resp = await page.goto(at, wait_until="networkidle", timeout=timeout_ms)
                    except Exception:
                        if not moved:
                            raise
                    if not moved:
                        break
                    at = moved[0]  # the page goes to where its address moved, routed again
                else:
                    raise RuntimeError(f"its address redirected over {policy.max_redirects} times")
                html = await page.content()
                status = resp.status if resp else 0
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning("render_url failed for %s: %s", url, exc, exc_info=True)
        return RenderResult(
            ok=False,
            url=url,
            error=f"render failed: {exc}",
            recovery_hints=[
                "The page may be slow or block automation; retry, or use web_fetch without render."
            ],
        )
    finally:
        if dead_end is not None:
            dead_end.close()
            await dead_end.wait_closed()

    return RenderResult(ok=True, url=at, html=html, status=status)


async def _dead_end(policy: EgressPolicy) -> Any:
    """The browser's own network for one render: a listener on 127.0.0.1 this render holds, so no
    other program can, that refuses every connection reaching it and audits it as a
    ``web.render`` refusal naming its request line, cut short. The render closes it as it ends."""

    async def refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=1)
        except Exception:  # noqa: BLE001 - a connection that says nothing is refused all the same
            line = b""
        said = line.decode("latin-1", "replace").strip()[:120] or "(a connection with no request)"
        audit(said, policy, outcome="denied", reason=_PAST_THE_ROUTE, door=_DOOR)
        writer.close()

    return await asyncio.start_server(refuse, host="127.0.0.1", port=0)


def _is_the_page(request: Any) -> bool:
    """Whether *request* is the page's own navigation (the main frame's), not a frame's or a
    subresource's."""
    try:
        return bool(request.is_navigation_request()) and request.frame.parent_frame is None
    except Exception:  # noqa: BLE001 - a request that cannot say is not the page's
        return False


async def _route(
    route: Any,
    request: Any,
    *,
    policy: EgressPolicy,
    resolver: Any,
    moved: list[str] | None = None,
) -> None:
    """One request of the render: refused and audited when the guard denies it, else fetched by
    the egress layer at the address the guard checked, each redirect followed there and its hop
    asked of the guard, and answered to the browser as it came. The page's own navigation whose
    address redirected is ended instead, its final address put in *moved*, so the page goes there
    (:func:`render_url`) and reads the address it is at."""
    url = str(request.url)
    # The guard refuses every scheme but http and https, so only those reach the fetch.
    guard_kw = {"resolver": resolver} if resolver is not None else {}
    decision = evaluate(url, policy, **guard_kw)
    if not decision.allow:
        audit(url, policy, outcome="denied", reason=decision.reason, door=_DOOR)
        await route.abort("blockedbyclient")
        return
    headers = {
        k: v for k, v in dict(request.headers or {}).items() if k.lower() not in _REQUEST_DROPPED
    }
    try:
        resp = await fetch_as(
            _DOOR,
            url,
            policy=policy,
            method=str(request.method or "GET"),
            headers=headers,
            data=request.post_data_buffer,
            **guard_kw,
        )
    except EgressBlocked:
        await route.abort("blockedbyclient")  # the egress layer audited it
        return
    except Exception as exc:  # noqa: BLE001 — a request that could not be carried is refused
        logger.info("render: %s was not fetched: %s", url, exc)
        await route.abort("failed")
        return
    if moved is not None and resp.url != url and _is_the_page(request):
        moved.append(resp.url)
        await route.abort("aborted")
        return
    await route.fulfill(
        status=resp.status,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in _RESPONSE_DROPPED},
        body=resp.body,
    )
