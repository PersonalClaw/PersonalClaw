"""GET /api/logs — the SSE stream says hello before it has anything to say.

On a quiet logger with an empty ring buffer the stream used to emit ZERO bytes
until the 30-second keepalive: a client (or proxy) could not tell "connected
and idle" from "hung", so a healthy quiet log stream looked broken. The stream
must put a comment frame on the wire immediately after the response prepares —
invisible to EventSource consumers, decisive for anything watching bytes.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.dashboard.handlers.updates as updates


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/logs", updates.api_logs)
    return app


async def _read_head(resp, n: int, timeout: float = 3.0) -> bytes:
    return await asyncio.wait_for(resp.content.read(n), timeout=timeout)


@pytest.mark.asyncio
async def test_connected_frame_arrives_immediately_on_an_empty_ring(monkeypatch):
    monkeypatch.setattr(updates, "_log_ring", type(updates._log_ring)(maxlen=10))
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/logs")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        head = await _read_head(resp, len(b": connected\n\n"))
        assert head == b": connected\n\n"
        resp.close()


@pytest.mark.asyncio
async def test_connected_frame_precedes_the_history_replay(monkeypatch):
    ring = type(updates._log_ring)(maxlen=10)
    ring.append("2026-09-04 INFO personalclaw: hello-from-the-ring")
    monkeypatch.setattr(updates, "_log_ring", ring)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/logs")
        expected = b": connected\n\ndata: 2026-09-04 INFO personalclaw: hello-from-the-ring\n\n"
        head = await _read_head(resp, len(expected))
        assert head == expected
        resp.close()
