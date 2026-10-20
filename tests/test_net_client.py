"""Egress enforcement plane (net/client.fetch) — pre-flight deny, the redirect-hop
re-evaluation contract, byte-cap, and the uniform EgressBlocked error.

The aiohttp request is mocked so no socket is opened; the focus is the guard wiring
(deny before connect, re-check each redirect Location) which is the security contract.
"""

from __future__ import annotations

import socket
import sys
import types

import pytest

from personalclaw.net.client import EgressBlocked, fetch
from personalclaw.net.policy import STRICT


def _resolver(mapping):
    def _r(host):
        if host not in mapping:
            raise socket.gaierror(host)
        return mapping[host]

    return _r


# ── Pre-flight deny: no connection is attempted for a blocked URL ──────────────


@pytest.mark.asyncio
async def test_fetch_denies_before_connecting(monkeypatch):
    # If the guard blocks, aiohttp must never be touched. Install a booby-trapped
    # aiohttp so any use raises — proving the deny is pre-connect.
    boom = types.ModuleType("aiohttp")

    def _explode(*a, **k):
        raise AssertionError("aiohttp must not be used for a denied fetch")

    boom.ClientSession = _explode  # type: ignore[attr-defined]
    boom.ClientTimeout = lambda **k: None  # type: ignore[attr-defined]
    boom.TCPConnector = _explode  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiohttp", boom)

    with pytest.raises(EgressBlocked) as ei:
        await fetch(
            "http://169.254.169.254/latest/meta-data",
            policy=STRICT,
            resolver=_resolver({"169.254.169.254": ["169.254.169.254"]}),
        )
    assert ei.value.recovery_hints  # carries recovery guidance


# ── Redirect-hop re-evaluation: a redirect to a private IP is blocked ──────────


class _FakeResp:
    def __init__(self, status, headers, body=b""):
        self.status = status
        self.headers = headers
        self._body = body
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def iter_chunked(self, n):
        yield self._body


class _FakeSession:
    """Returns queued responses in order, one per request() call."""

    queue: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def request(self, method, url, **kw):
        return type(self).queue.pop(0)


@pytest.fixture
def fake_aiohttp(monkeypatch):
    fake = types.ModuleType("aiohttp")
    fake.ClientSession = _FakeSession  # type: ignore[attr-defined]
    fake.ClientTimeout = lambda **k: None  # type: ignore[attr-defined]
    fake.TCPConnector = lambda **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiohttp", fake)
    _FakeSession.queue = []
    return _FakeSession


@pytest.mark.asyncio
async def test_redirect_to_private_ip_is_blocked(fake_aiohttp):
    # First hop (public) 302→ a private host; the redirect target must be re-checked
    # and blocked — the gap an allow_redirects=True client leaves open.
    fake_aiohttp.queue = [_FakeResp(302, {"Location": "http://internal/"})]
    with pytest.raises(EgressBlocked):
        await fetch(
            "https://public.com/start",
            policy=STRICT,
            resolver=_resolver({"public.com": ["8.8.8.8"], "internal": ["10.0.0.9"]}),
        )


@pytest.mark.asyncio
async def test_redirect_to_public_is_followed(fake_aiohttp):
    fake_aiohttp.queue = [
        _FakeResp(302, {"Location": "https://other.com/final"}),
        _FakeResp(200, {"Content-Type": "text/plain"}, body=b"hello"),
    ]
    resp = await fetch(
        "https://public.com/start",
        policy=STRICT,
        resolver=_resolver({"public.com": ["8.8.8.8"], "other.com": ["1.1.1.1"]}),
    )
    assert resp.status == 200
    assert resp.text == "hello"
    assert resp.url == "https://other.com/final"


@pytest.mark.asyncio
async def test_too_many_redirects_blocks(fake_aiohttp):
    pol = STRICT.with_overrides(max_redirects=1)
    fake_aiohttp.queue = [
        _FakeResp(302, {"Location": "https://a.com/2"}),
        _FakeResp(302, {"Location": "https://a.com/3"}),
    ]
    with pytest.raises(EgressBlocked) as ei:
        await fetch("https://a.com/1", policy=pol, resolver=_resolver({"a.com": ["8.8.8.8"]}))
    assert "redirect" in ei.value.decision.reason


@pytest.mark.asyncio
async def test_byte_cap_truncates(fake_aiohttp):
    pol = STRICT.with_overrides(max_bytes=4)
    fake_aiohttp.queue = [_FakeResp(200, {"Content-Type": "text/plain"}, body=b"0123456789")]
    resp = await fetch(
        "https://big.com/file", policy=pol, resolver=_resolver({"big.com": ["8.8.8.8"]})
    )
    assert resp.truncated is True
    assert len(resp.body) == 4
