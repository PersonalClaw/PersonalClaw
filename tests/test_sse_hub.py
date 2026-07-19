"""Tests for the SSE substrate (personalclaw.dashboard.sse)."""

import asyncio
import json

import pytest

from personalclaw.dashboard.sse import Periodic, SseHub, SseRegistry


class TestSseHub:
    def test_subscribe_returns_distinct_queues(self) -> None:
        hub = SseHub()
        q1 = hub.subscribe()
        q2 = hub.subscribe()
        assert q1 is not q2
        assert hub.subscriber_count == 2

    def test_unsubscribe_is_idempotent(self) -> None:
        hub = SseHub()
        q = hub.subscribe()
        hub.unsubscribe(q)
        hub.unsubscribe(q)  # no raise
        assert hub.subscriber_count == 0

    def test_publish_fans_out_to_all(self) -> None:
        hub = SseHub()
        q1, q2 = hub.subscribe(), hub.subscribe()
        hub.publish("ping", {"n": 1})
        f1, f2 = q1.get_nowait(), q2.get_nowait()
        assert f1.name == "ping" and f2.name == "ping"
        assert json.loads(f1.data) == {"n": 1}

    def test_publish_string_payload_is_verbatim(self) -> None:
        """A str payload is written as-is (producer owns serialization)."""
        hub = SseHub()
        q = hub.subscribe()
        hub.publish("refresh", "crons,lessons")
        frame = q.get_nowait()
        assert frame.data == "crons,lessons"  # NOT JSON-quoted

    def test_publish_dict_payload_is_json(self) -> None:
        hub = SseHub()
        q = hub.subscribe()
        hub.publish("session_title", {"key": "chat-1", "title": "Hi"})
        frame = q.get_nowait()
        assert json.loads(frame.data) == {"key": "chat-1", "title": "Hi"}

    def test_publish_to_no_subscribers_is_noop(self) -> None:
        SseHub().publish("x", {"a": 1})  # no raise

    def test_full_queue_drops_without_raising(self) -> None:
        hub = SseHub()
        q = hub.subscribe()
        # Fill past maxsize; publish must not raise on a slow consumer.
        for i in range(200):
            hub.publish("e", {"i": i})
        assert q.full()


class TestSseRegistry:
    def test_hub_creates_and_reuses(self) -> None:
        reg = SseRegistry()
        h1 = reg.hub("loop:abc")
        h2 = reg.hub("loop:abc")
        assert h1 is h2

    def test_peek_does_not_create(self) -> None:
        reg = SseRegistry()
        assert reg.peek("loop:abc") is None
        reg.hub("loop:abc")
        assert reg.peek("loop:abc") is not None

    def test_publish_only_when_subscribers(self) -> None:
        reg = SseRegistry()
        # No hub yet → publish is a silent no-op (does not resurrect a hub).
        reg.publish("loop:abc", "new_finding", {"cycle": 1})
        assert reg.peek("loop:abc") is None
        # With a live subscriber, the event lands.
        q = reg.hub("loop:abc").subscribe()
        reg.publish("loop:abc", "new_finding", {"cycle": 2})
        frame = q.get_nowait()
        assert frame.name == "new_finding"
        assert json.loads(frame.data) == {"cycle": 2}

    def test_evict_if_empty(self) -> None:
        reg = SseRegistry()
        hub = reg.hub("loop:abc")
        q = hub.subscribe()
        reg._evict_if_empty("loop:abc", hub)  # still has subscriber → kept
        assert reg.peek("loop:abc") is hub
        hub.unsubscribe(q)
        reg._evict_if_empty("loop:abc", hub)  # now empty → evicted
        assert reg.peek("loop:abc") is None


class TestPeriodic:
    def test_frame_and_interval(self) -> None:
        p = Periodic(lambda: ("dashboard", {"ok": True}), 5)
        assert p.interval == 5
        name, data = p.frame()
        assert name == "dashboard" and data == {"ok": True}


@pytest.mark.asyncio
async def test_stream_response_delivers_then_closes(monkeypatch) -> None:
    """stream_response writes an on_connect frame + a published event, then
    stops cleanly when shutdown_event is set."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard import sse as sse_mod

    hub = SseHub()

    async def handler(request: web.Request) -> web.StreamResponse:
        return await sse_mod.stream_response(
            request, hub, on_connect=[("hello", {"v": 1})]
        )

    app = web.Application()
    app.router.add_get("/s", handler)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/s")
        # Read the on_connect frame.
        line = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), timeout=2)
        text = line.decode()
        assert "event: hello" in text
        assert 'data: {"v": 1}' in text
        # Publish a live event and read it.
        hub.publish("tick", "raw-string")
        line2 = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), timeout=2)
        text2 = line2.decode()
        assert "event: tick" in text2
        assert "data: raw-string" in text2
        # Signal shutdown so the handler loop exits without hanging the test.
        sse_mod.shutdown_event.set()
        try:
            hub.publish("bye", "x")  # wake the wait_for
            await asyncio.wait_for(resp.content.read(), timeout=2)
        except (asyncio.TimeoutError, Exception):
            pass
        finally:
            sse_mod.shutdown_event.clear()


@pytest.mark.asyncio
async def test_stream_response_close_after_connect_does_not_hang() -> None:
    """With close_after_connect, the snapshot is sent and the stream ends immediately
    (a terminal-state resource emits no further events) — and the hub it may have
    created is evicted from the registry."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard import sse as sse_mod
    from personalclaw.dashboard.sse import SseRegistry

    registry = SseRegistry()
    feed = "knowledge:ingest:done-item"

    async def handler(request: web.Request) -> web.StreamResponse:
        return await sse_mod.stream_response(
            request, registry.hub(feed), on_connect=[("status", {"processing_status": "done"})],
            registry_evict=(registry, feed), close_after_connect=True,
        )

    app = web.Application()
    app.router.add_get("/s", handler)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/s")
        # The whole body arrives and the stream closes promptly (no hang).
        body = await asyncio.wait_for(resp.content.read(), timeout=2)
        assert b"event: status" in body and b'"processing_status": "done"' in body
    # The transient hub was evicted (not left lingering).
    assert registry.peek(feed) is None
