"""WS7 — JS-render fetch path (web/render.py + web_fetch render=True).

Playwright is an optional dep (not installed in CI), so these tests exercise:
- graceful unavailability when Playwright is absent,
- the egress guard running BEFORE the browser navigates (the SSRF invariant),
- web_fetch(render=True) falling back to the HTTP path when unavailable,
- web_fetch(render=True) using the rendered HTML when render succeeds.

The browser itself is never launched — render_url is monkeypatched where a successful
render is needed; the guard is exercised with is_available forced True + a fake resolver.
"""

from __future__ import annotations

import socket

import pytest

from personalclaw.net.client import FetchResponse
from personalclaw.web import fetch as wf
from personalclaw.web import render as rd
from personalclaw.web.fetch import web_fetch
from personalclaw.web.render import RenderResult, render_url


def _resolver(mapping):
    def _r(host):
        if host not in mapping:
            raise socket.gaierror(host)
        return mapping[host]
    return _r




def _web_tool_provider_cls():
    """Load WebToolProvider from the web-tools APP (it moved out of core). Mirrors how
    the app loader imports an installed app's provider module."""
    import importlib.util
    import sys
    from pathlib import Path
    app_dir = Path(__file__).resolve().parents[2] / "apps" / "web-tools"
    if not app_dir.is_dir():  # standalone core clone — the web-tools app isn't present
        pytest.skip("web-tools app dir not present (standalone clone)")
    uniq = "_pclaw_app_web_tools__provider"
    if uniq in sys.modules:
        return sys.modules[uniq].WebToolProvider
    spec = importlib.util.spec_from_file_location(uniq, app_dir / "provider.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[uniq] = mod
    added = str(app_dir) not in sys.path
    if added:
        sys.path.insert(0, str(app_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(app_dir))
    return mod.WebToolProvider

@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(wf, "_seen_by_session", {})
    yield


# ── render_url ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_render_unavailable_without_playwright(monkeypatch):
    monkeypatch.setattr(rd, "is_available", lambda: False)
    out = await render_url("https://example.com")
    assert out.ok is False
    assert out.unavailable is True
    assert any("playwright" in h.lower() for h in (out.recovery_hints or []))


@pytest.mark.asyncio
async def test_render_egress_guard_blocks_before_browser(monkeypatch):
    # Playwright "available", but the URL resolves to a private IP → the egress guard
    # denies it BEFORE any browser launch (async_playwright must never be reached).
    monkeypatch.setattr(rd, "is_available", lambda: True)
    def _boom(*a, **k):
        raise AssertionError("browser must not launch for a guard-denied URL")
    # If the code tried to import/use playwright, this would surface — but the guard
    # returns first, so we just assert the deny outcome.
    out = await render_url("http://internal", resolver=_resolver({"internal": ["10.0.0.5"]}))
    assert out.ok is False
    assert out.unavailable is False
    assert "non-public" in out.error or "private" in out.error.lower()


@pytest.mark.asyncio
async def test_render_guard_allows_public_then_would_launch(monkeypatch):
    # Public URL passes the guard; with Playwright genuinely absent the import path
    # returns an unavailable result (proves the guard is not what stops it here).
    monkeypatch.setattr(rd, "is_available", lambda: True)  # claim available…
    # …but the real import inside will fail (not installed) → handled gracefully.
    out = await render_url("https://example.com", resolver=_resolver({"example.com": ["93.184.216.34"]}))
    assert out.ok is False  # import fails in this env
    assert out.unavailable is True or "import" in out.error.lower()


# ── web_fetch(render=True) ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_fetch_render_falls_back_to_http_when_unavailable(monkeypatch):
    # render requested but unavailable → falls through to the normal HTTP fetch.
    async def _fake_render(url, **kw):
        return RenderResult(ok=False, url=url, unavailable=True, error="no playwright")
    monkeypatch.setattr(rd, "render_url", _fake_render)
    async def _fake_net(url, **kw):
        return FetchResponse(url=url, status=200, headers={"Content-Type": "text/html"},
                             body=b"<html><body><p>http path body content here</p></body></html>")
    monkeypatch.setattr(wf, "net_fetch", _fake_net)
    out = await web_fetch("https://example.com/p", require_provenance=False, render=True)
    assert out.ok is True
    assert "http path body" in out.content


@pytest.mark.asyncio
async def test_web_fetch_render_uses_rendered_html(monkeypatch):
    # A successful render: web_fetch extracts from the rendered HTML, not an HTTP fetch.
    async def _fake_render(url, **kw):
        return RenderResult(ok=True, url=url, status=200,
                            html="<html><body><article><p>JS-rendered content that is long enough.</p></article></body></html>")
    monkeypatch.setattr(rd, "render_url", _fake_render)
    async def _net_boom(url, **kw):
        raise AssertionError("net_fetch must not be called when render succeeds")
    monkeypatch.setattr(wf, "net_fetch", _net_boom)
    out = await web_fetch("https://spa.example.com/p", require_provenance=False, render=True)
    assert out.ok is True
    assert "JS-rendered content" in out.content


@pytest.mark.asyncio
async def test_web_fetch_render_error_surfaces(monkeypatch):
    # A genuine render failure (not unavailability) surfaces, no silent HTTP fallback.
    async def _fake_render(url, **kw):
        return RenderResult(ok=False, url=url, unavailable=False, error="render failed: timeout",
                            recovery_hints=["retry"])
    monkeypatch.setattr(rd, "render_url", _fake_render)
    async def _net_boom(url, **kw):
        raise AssertionError("net_fetch must not be called when render hard-fails")
    monkeypatch.setattr(wf, "net_fetch", _net_boom)
    out = await web_fetch("https://x.com/p", require_provenance=False, render=True)
    assert out.ok is False
    assert "render failed" in out.error


@pytest.mark.asyncio
async def test_tool_exposes_render_param():
    WebToolProvider = _web_tool_provider_cls()
    tools = {t.name: t for t in await WebToolProvider().list_tools()}
    props = tools["web_fetch"].parameters["properties"]
    assert "render" in props
