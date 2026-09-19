"""Knowledge web_url connector routes fetch + detect_changes through the egress guard (N2).

Previously it fetched arbitrary user/agent-supplied bookmark URLs with raw httpx and NO
SSRF check — a bookmark of http://169.254.169.254/ or an internal host was fetched
unguarded. Now both go through net.fetch(policy=CONNECTOR).
"""

import asyncio
import socket

from personalclaw.knowledge.connectors.web_url import WebUrlConnector


def _run(coro):
    return asyncio.run(coro)


def _fake_dns(mapping):
    def _gai(host, *a, **k):
        ips = mapping.get(host)
        if ips is None:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, None, None, "", (ip, 0)) for ip in ips]

    return _gai


def test_web_url_fetch_blocks_private(monkeypatch):
    """A bookmark resolving to a private/LAN IP is blocked (returns error_kind=blocked)."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"intranet.local": ["10.0.0.5"]}))
    text, meta = _run(WebUrlConnector().fetch({"uri": "http://intranet.local/page"}))
    assert text == ""
    assert meta.get("error_kind") == "blocked"
    assert "security guard" in meta.get("error", "").lower()


def test_web_url_fetch_blocks_imds(monkeypatch):
    """A bookmark of the AWS IMDS address is blocked."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"metadata.internal": ["169.254.169.254"]})
    )
    text, meta = _run(
        WebUrlConnector().fetch({"uri": "http://metadata.internal/latest/meta-data/"})
    )
    assert text == ""
    assert meta.get("error_kind") == "blocked"


def test_web_url_detect_changes_blocks_private(monkeypatch):
    """detect_changes (the scheduled HEAD refresh) is guarded too — a private host
    returns False (no change / not probed), never an unguarded HEAD."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"nas.local": ["192.168.1.9"]}))
    changed = _run(WebUrlConnector().detect_changes({"uri": "http://nas.local/feed"}))
    assert changed is False


def test_web_url_fetch_public_attempts(monkeypatch):
    """A public host passes the guard and the fetch is attempted (stubbed transport)."""
    import personalclaw.net.client as client

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"example.com": ["93.184.216.34"]}))

    async def fake_fetch(url, **kw):
        return client.FetchResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html"},
            body=b"<html><head><title>Hi</title></head><body><p>Hello world content</p></body></html>",  # noqa: E501
        )

    import personalclaw.net as net

    monkeypatch.setattr(net, "fetch", fake_fetch)
    text, meta = _run(WebUrlConnector().fetch({"uri": "https://example.com/"}))
    assert meta.get("error") is None
    assert meta.get("url") == "https://example.com/"
    assert meta.get("content_hash")


# ── meta-refresh redirect stubs (#265) ──────────────────────────────────────────

_STUB_URL = "https://docs.example.test/basics/RAIDZ.html"
#: The exact stub shape measured in #265 — a RELATIVE target.
_STUB_BODY = (
    '<meta http-equiv="refresh" content="0; url=Pool Structure/RAIDZ.html"/>\n\n'
    "You should have been redirected."
)
_DEST_URL = "https://docs.example.test/basics/Pool Structure/RAIDZ.html"
_DEST_BODY = (
    "<html><head><title>RAIDZ vs draid</title></head><body><article>"
    + "<p>RAIDZ is a variant of RAID-5 that avoids the write hole, and draid trades "
    "flexibility for fast rebuilds using distributed spare capacity. </p>" * 6
    + "</article></body></html>"
)


def _serving(pages, calls):
    """A fake net.fetch serving `pages` (url -> (body, content_type)), recording calls."""
    import personalclaw.net.client as client

    async def fake_fetch(url, **kw):
        calls.append(url)
        body, ctype = pages.get(url, ("<html><body>not found</body></html>", "text/html"))
        status = 200 if url in pages else 404
        return client.FetchResponse(
            url=url, status=status, headers={"Content-Type": ctype}, body=body.encode()
        )

    return fake_fetch


def test_meta_refresh_stub_is_followed_to_the_real_article(monkeypatch):
    """🔴 THE BUG: the stub was stored as the item's content. Now the destination is."""
    import personalclaw.net as net

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    calls: list[str] = []
    monkeypatch.setattr(
        net,
        "fetch",
        _serving(
            {_STUB_URL: (_STUB_BODY, "text/html"), _DEST_URL: (_DEST_BODY, "text/html")}, calls
        ),
    )

    text, meta = _run(WebUrlConnector().fetch({"uri": _STUB_URL}))

    assert calls == [_STUB_URL, _DEST_URL], "the relative target must be resolved and fetched"
    assert "avoids the write hole" in text, "the destination article must be the stored content"
    assert "should have been redirected" not in text.lower(), "the stub must NOT be stored"
    # The item records where the content actually came from, and the hop is legible.
    assert meta["url"] == _DEST_URL
    assert meta["meta_refresh_chain"] == [_DEST_URL]
    assert meta["page_title"] == "RAIDZ vs draid"


def test_meta_refresh_target_is_re_judged_by_the_real_egress_guard(monkeypatch):
    """🔴 The target is parsed out of UNTRUSTED fetched content, so it is exactly the shape an
    SSRF attempt takes. The hop must re-enter net.fetch — here the REAL one, which refuses it.

    Only the first hop is stubbed; everything else is delegated to the genuine
    `net.client.fetch`, so this asserts against the actual guard rather than against a fake.
    """
    import personalclaw.net as net
    import personalclaw.net.client as client

    real_fetch = client.fetch
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    evil = "http://169.254.169.254/latest/meta-data/"
    calls: list[str] = []

    async def routing_fetch(url, **kw):
        calls.append(url)
        if url == _STUB_URL:
            return client.FetchResponse(
                url=url,
                status=200,
                headers={"Content-Type": "text/html"},
                body=f'<meta http-equiv="refresh" content="0; url={evil}">'.encode(),
            )
        return await real_fetch(url, **kw)  # the genuine guard judges the hop

    monkeypatch.setattr(net, "fetch", routing_fetch)

    text, meta = _run(WebUrlConnector().fetch({"uri": _STUB_URL}))

    assert evil in calls, "the hop must go through the egress chokepoint, not around it"
    # Refused by the guard, so nothing from the private target was stored...
    assert "meta-data" not in text
    assert meta.get("meta_refresh_chain") is None
    # ...and the bookmark the user legitimately saved is NOT failed: any page could otherwise
    # break a working bookmark just by advertising a private redirect target.
    assert meta.get("error") is None
    assert meta["url"] == _STUB_URL


def test_meta_refresh_loop_does_not_spin(monkeypatch):
    """A stub pointing at itself is a loop, not a redirect: one attempt, stub kept."""
    import personalclaw.net as net

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    calls: list[str] = []
    self_ref = '<meta http-equiv="refresh" content="0; url=RAIDZ.html">'
    monkeypatch.setattr(net, "fetch", _serving({_STUB_URL: (self_ref, "text/html")}, calls))

    _text, meta = _run(WebUrlConnector().fetch({"uri": _STUB_URL}))

    assert calls == [_STUB_URL], "a self-referential stub must not be re-fetched"
    assert meta.get("error") is None


def test_meta_refresh_chain_is_bounded(monkeypatch):
    """An unbounded chain is a page's choice to make us fetch forever."""
    import personalclaw.net as net
    from personalclaw.knowledge.connectors.web_url import _MAX_META_REFRESH_HOPS

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    # hop0 -> hop1 -> ... -> hop9, each a stub pointing at the next.
    pages = {
        f"https://docs.example.test/hop{i}.html": (
            f'<meta http-equiv="refresh" content="0; url=hop{i + 1}.html">',
            "text/html",
        )
        for i in range(10)
    }
    calls: list[str] = []
    monkeypatch.setattr(net, "fetch", _serving(pages, calls))

    _text, meta = _run(WebUrlConnector().fetch({"uri": "https://docs.example.test/hop0.html"}))

    # The original fetch plus at most _MAX_META_REFRESH_HOPS follows.
    assert len(calls) == 1 + _MAX_META_REFRESH_HOPS
    assert len(meta["meta_refresh_chain"]) == _MAX_META_REFRESH_HOPS


def test_meta_refresh_dead_target_keeps_the_last_good_body(monkeypatch):
    """A redirect target that 404s must not turn a working bookmark into a failed one."""
    import personalclaw.net as net

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    calls: list[str] = []
    monkeypatch.setattr(net, "fetch", _serving({_STUB_URL: (_STUB_BODY, "text/html")}, calls))

    _text, meta = _run(WebUrlConnector().fetch({"uri": _STUB_URL}))

    assert calls == [_STUB_URL, _DEST_URL]
    assert meta.get("error") is None, "the ingest must not fail because the TARGET is gone"
    assert meta["url"] == _STUB_URL
    assert meta.get("meta_refresh_chain") is None


def test_an_ordinary_page_is_not_re_fetched(monkeypatch):
    """No meta refresh ⇒ exactly one fetch. The follow must cost nothing on normal pages."""
    import personalclaw.net as net

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"docs.example.test": ["93.184.216.34"]}))
    calls: list[str] = []
    monkeypatch.setattr(net, "fetch", _serving({_STUB_URL: (_DEST_BODY, "text/html")}, calls))

    text, meta = _run(WebUrlConnector().fetch({"uri": _STUB_URL}))

    assert calls == [_STUB_URL]
    assert "meta_refresh_chain" not in meta
    assert "write hole" in text
