"""Every request a rendered page makes is held to the egress guard, at the address it checked.

With the optional ``js-render`` extra, a page can be rendered in a headless browser before it is
read: by the agent's fetch with rendering, a knowledge source allowed the render tier, and a web
watch's headless tier. The render asked the guard about the one URL it was given, and then the
browser loaded the page and ran it with nothing between it and the network: its subresources,
frames, script requests and redirects went to any address the browser chose, and the browser did
its own lookups, so a name the guard had checked could be dialled somewhere else.

Now every request of the render is routed: each one is asked of the guard under the run's policy
and refused and audited (``door="web.render"``) when it is denied, and an allowed one is fetched by
the egress layer, which dials the address the guard checked and follows a redirect itself, asking
the guard of each hop: Chromium sends the next hop of a redirect a route answers on its own
network, past the route (measured in real Chromium). What routing cannot carry is refused: a
scheme but http and https, a service worker, a socket or a worker the page would start. And the
browser's own network, for anything routing misses, is a proxy nothing listens on.

Driven through ``render_url`` with Playwright replaced by a stand-in browser that requests what a
page would (the page, a script on a private address, a frame on loopback, a link-local metadata
address, a file), the real egress guard over a test resolver, and the egress layer's fetch replaced
by one that records what it was asked and answers without a network.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from personalclaw.net.client import FetchResponse
from personalclaw.web import render

pytestmark = pytest.mark.security

PAGE = "http://news.example/story"
#: What a page would request while it loads: allowed, then each kind the guard refuses.
SUBREQUESTS = [
    ("http://news.example/app.js", True),
    ("http://intranet.example/admin", False),  # a private address
    ("http://127.0.0.1:8080/", False),  # this machine
    ("http://169.254.169.254/latest/meta-data/", False),  # a cloud host's metadata
    ("file:///etc/hosts", False),  # not http or https
]
ADDRESSES = {"news.example": ["93.184.216.34"], "intranet.example": ["10.0.0.5"]}


def _resolver(host: str) -> list[str]:
    return ADDRESSES.get(host, [])


class _Request:
    def __init__(self, url: str, *, page: bool = False) -> None:
        self.url = url
        self.method = "GET"
        self.headers = {"accept": "*/*"}
        self.post_data_buffer = None
        self.resource_type = "document" if page else "script"
        self._page = page
        # The page's own navigation is the main frame's, which has no parent.
        self.frame = types.SimpleNamespace(parent_frame=None if page else object())

    def is_navigation_request(self) -> bool:
        return self._page


class _Route:
    def __init__(self, request: _Request) -> None:
        self.request = request
        self.outcome: tuple[str, Any] | None = None

    async def abort(self, error_code: str = "failed") -> None:
        self.outcome = ("abort", error_code)

    async def fulfill(self, **kw: Any) -> None:
        self.outcome = ("fulfill", kw)


class _Browser:
    """A headless browser that requests what a page would, through whatever routes it was given.
    Without a route, it would reach the network itself: that is what it records."""

    def __init__(self, seen: dict[str, Any], urls: list[str]) -> None:
        self.seen = seen
        self.urls = urls

    async def new_context(self, **kw: Any) -> "_Context":
        self.seen["context"] = kw
        return _Context(self)

    async def new_page(self) -> "_Page":  # the unrouted page a render without a context opens
        return _Page(_Context(self))

    async def close(self) -> None:
        pass


class _Context:
    def __init__(self, browser: _Browser) -> None:
        self.browser = browser
        self.routes: list[Any] = []

    async def add_init_script(self, script: str = "", **_kw: Any) -> None:
        self.browser.seen.setdefault("init", []).append(script)

    async def route(self, pattern: str, handler: Any) -> None:
        self.routes.append((pattern, handler))

    async def new_page(self) -> "_Page":
        return _Page(self)

    async def close(self) -> None:
        pass


class _Page:
    def __init__(self, context: _Context) -> None:
        self.context = context
        self.html = ""

    async def goto(self, url: str, **_kw: Any) -> Any:
        seen = self.context.browser.seen
        seen.setdefault("navigated", []).append(url)
        status = 0
        for target in [url, *self.context.browser.urls]:
            if not self.context.routes:
                seen.setdefault("unrouted", []).append(target)
                continue
            route = _Route(_Request(target, page=target == url))
            await self.context.routes[0][1](route, route.request)
            seen.setdefault("routed", {})[target] = route.outcome
            kind, kw = route.outcome or ("", {})
            if target == url and kind == "abort":
                raise RuntimeError(f"net::ERR_ABORTED at {url}")  # as Playwright's goto does
            if target == url and kind == "fulfill":
                status = kw.get("status", 0)
                self.html = (kw.get("body") or b"").decode()
        if seen.get("reaches_the_network"):
            seen["reached"] = await _reach_the_proxy(seen["launch"]["proxy"]["server"])
        return types.SimpleNamespace(status=status)

    async def content(self) -> str:
        return self.html


async def _reach_the_proxy(server: str) -> bytes:
    """What a request the browser sent past its routes, to its own network, would get there: the
    proxy's answer, ``b""`` when it closes the connection having said nothing."""
    import asyncio

    host, _, port = server.removeprefix("http://").partition(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(b"GET http://elsewhere.example/past-the-route HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    answer = await reader.read(100)
    writer.close()
    return answer


def _playwright(seen: dict[str, Any], urls: list[str]) -> types.ModuleType:
    class _Chromium:
        async def launch(self, **kw: Any) -> _Browser:
            seen["launch"] = kw
            return _Browser(seen, urls)

    class _Session:
        chromium = _Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: _Session()  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def browser(monkeypatch) -> dict[str, Any]:
    """The stand-in browser, and what the egress layer's fetch was asked."""
    seen: dict[str, Any] = {"fetched": []}
    monkeypatch.setattr(render, "is_available", lambda: True)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(
        sys.modules, "playwright.async_api", _playwright(seen, [u for u, _ in SUBREQUESTS])
    )

    async def fetch(door: str, url: str, **kw: Any) -> FetchResponse:
        seen["fetched"].append((url, {**kw, "door": door}))
        return FetchResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
            body=b"<html>story</html>" if url == PAGE else b"/* ok */",
        )

    monkeypatch.setattr(render, "fetch_as", fetch, raising=False)
    return seen


def _denials() -> list[str]:
    from personalclaw.sel import sel

    return [
        e.get("resources", "")
        for e in sel().recent(200)
        if e.get("operation") == "egress_fetch"
        and e.get("caller_identity", "").startswith("web.render")
        and e.get("outcome") == "denied"
    ]


@pytest.fixture(autouse=True)
def _sel_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_every_request_of_the_render_is_routed_through_the_guard(browser) -> None:
    result = await render.render_url(PAGE, resolver=_resolver)

    assert "unrouted" not in browser, f"the browser reached these itself: {browser.get('unrouted')}"
    routed = browser["routed"]
    for url, allowed in [(PAGE, True), *SUBREQUESTS]:
        kind = (routed[url] or ("",))[0]
        assert kind == ("fulfill" if allowed else "abort"), (url, routed[url])
    assert result.ok and result.html == "<html>story</html>"


@pytest.mark.asyncio
async def test_a_refused_request_reaches_nothing_and_is_audited(browser) -> None:
    await render.render_url(PAGE, resolver=_resolver)

    fetched = [u for u, _kw in browser["fetched"]]
    assert fetched == [PAGE, "http://news.example/app.js"], fetched
    denied = _denials()
    for url, allowed in SUBREQUESTS:
        if not allowed and url.startswith("http"):
            assert any(url[:40] in row for row in denied), (url, denied)


@pytest.mark.asyncio
async def test_an_allowed_request_is_dialled_at_the_checked_address(browser) -> None:
    """The egress layer's fetch is what reaches the network, under the run's policy and the same
    resolver, so it dials what the guard checked. It follows a redirect itself, asking the guard
    of each hop, and never hands one back: the browser would send the next hop past the route."""
    await render.render_url(PAGE, resolver=_resolver)

    url, kw = browser["fetched"][0]
    assert url == PAGE
    assert kw.get("follow_redirects", True) is True, "a redirect is followed by the egress layer"
    assert kw.get("resolver") is _resolver
    assert kw.get("policy") is not None
    assert kw.get("door") == "web.render", "its audit rows name the render"
    fulfilled = browser["routed"][PAGE][1]
    assert "content-encoding" not in {k.lower() for k in fulfilled.get("headers", {})}, fulfilled


@pytest.mark.asyncio
async def test_the_rendered_page_names_personalclaw(browser) -> None:
    """The browser's requests reach the guard with its own User-Agent, which the guard forwards:
    the context names PersonalClaw, as the plain fetch does, not HeadlessChrome."""
    from personalclaw.net.client import USER_AGENT

    await render.render_url(PAGE, resolver=_resolver)

    assert browser["context"].get("user_agent") == USER_AGENT


@pytest.mark.asyncio
async def test_what_routing_cannot_carry_is_refused(browser) -> None:
    await render.render_url(PAGE, resolver=_resolver)

    assert browser["context"].get("service_workers") == "block"
    script = "\n".join(browser.get("init", []))
    for name in ("WebSocket", "WebTransport", "RTCPeerConnection", "Worker", "SharedWorker"):
        assert name in script, name
    args = " ".join(browser["launch"].get("args", []))
    proxy = browser["launch"].get("proxy") or {}
    assert proxy.get("server", "").startswith("http://127.0.0.1:"), "the browser's own network"
    assert "<-loopback>" in proxy.get("bypass", ""), "loopback is held to that proxy too"
    assert "disable_non_proxied_udp" in args


@pytest.mark.asyncio
async def test_a_page_whose_address_redirects_is_opened_where_it_moved(
    browser, monkeypatch
) -> None:
    """Followed by the egress layer, a redirect of the page's own address would answer the
    browser at the address it asked for, and its relative links and ``location`` would read that.
    So the route ends that navigation, and the page goes to the final address, routed again."""
    moved = "http://news.example/b/"
    fetched: list[str] = []

    async def fetch(door: str, url: str, **kw: Any) -> FetchResponse:
        fetched.append(url)
        final = moved if url == PAGE else url  # the egress layer followed PAGE's redirect
        body = b"<html>moved story</html>" if final == moved else b"/* ok */"
        return FetchResponse(
            url=final, status=200, headers={"Content-Type": "text/html"}, body=body
        )

    monkeypatch.setattr(render, "fetch_as", fetch)

    result = await render.render_url(PAGE, resolver=_resolver)

    assert result.ok and result.url == moved and result.html == "<html>moved story</html>", result
    assert browser["navigated"] == [PAGE, moved]
    assert browser["routed"][PAGE] == ("abort", "aborted"), "the first navigation is ended"
    assert fetched[:2] == [PAGE, moved], "the final address is routed and fetched again"


@pytest.mark.asyncio
async def test_the_browsers_own_network_is_a_listener_of_the_renders_own(browser) -> None:
    """The proxy is a listener the render holds for as long as it runs, so no other program can
    take its port: a request that reaches it, past the routes, is refused and audited."""
    import asyncio

    browser["reaches_the_network"] = True

    result = await render.render_url(PAGE, resolver=_resolver)

    assert result.ok, result
    assert browser["reached"] == b"", "a connection reaching it is closed unanswered"
    rows = [r for r in _denials() if "past-the-route" in r]
    assert rows and rows[0].startswith("GET http://elsewhere.example/past-the-route"), rows
    port = int(browser["launch"]["proxy"]["server"].rpartition(":")[2])
    with pytest.raises(OSError):  # and it is closed when the render ends
        _r, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
