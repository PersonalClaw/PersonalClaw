"""Tests for the P9 FrameRouter (acp/reader.py) — the single-reader stdout demux.

Driven by a fake readline feeding scripted JSON-RPC frames; no real ACP process.
The load-bearing property is SESSION ISOLATION: frames for session A and B are
demuxed to separate queues and one session's fate doesn't stall the other."""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.acp.reader import FrameRouter
from personalclaw.acp.types import JsonRpcMessage


class _FakeStdout:
    """A scriptable async line source: feed() lines, then it blocks (like a live
    stream awaiting more) until close()."""

    def __init__(self):
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, obj):
        self._q.put_nowait((json.dumps(obj) + "\n").encode())

    def feed_raw(self, line: bytes):
        self._q.put_nowait(line)

    def eof(self):
        self._q.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._q.get()


def _f(**kw) -> dict:
    return kw


@pytest.mark.asyncio
async def test_response_resolves_pending_future():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    fut = r.expect(2)
    out.feed(_f(id=2, result={"sessionId": "s-abc"}))
    resp = await asyncio.wait_for(fut, timeout=2)
    assert resp.result["sessionId"] == "s-abc"
    await r.close()


@pytest.mark.asyncio
async def test_session_update_demuxed_to_its_queue():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    qa = r.register_session("A")
    qb = r.register_session("B")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {"x": 1}}))
    out.feed(_f(method="session/update", params={"sessionId": "B", "update": {"y": 2}}))
    a = await asyncio.wait_for(qa.get(), timeout=2)
    b = await asyncio.wait_for(qb.get(), timeout=2)
    assert a.params["sessionId"] == "A" and b.params["sessionId"] == "B"
    assert qa.empty() and qb.empty()  # no cross-contamination
    await r.close()


@pytest.mark.asyncio
async def test_session_isolation_interleaved():
    # The concurrency property: interleaved A/B frames land in the right queues in order.
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    qa = r.register_session("A")
    qb = r.register_session("B")
    for i in range(5):
        out.feed(_f(method="session/update", params={"sessionId": "A", "update": {"n": i}}))
        out.feed(_f(method="session/update", params={"sessionId": "B", "update": {"n": i}}))
    a_ns = [(await asyncio.wait_for(qa.get(), timeout=2)).params["update"]["n"] for _ in range(5)]
    b_ns = [(await asyncio.wait_for(qb.get(), timeout=2)).params["update"]["n"] for _ in range(5)]
    assert a_ns == [0, 1, 2, 3, 4] and b_ns == [0, 1, 2, 3, 4]
    await r.close()


@pytest.mark.asyncio
async def test_one_session_unregister_does_not_stall_other():
    # Cancel/drop session A mid-stream; B must keep receiving (isolation under teardown).
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    r.register_session("A")
    qb = r.register_session("B")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {}}))
    r.unregister_session("A")  # A is gone
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {}}))  # now routes to broadcast/drop
    out.feed(_f(method="session/update", params={"sessionId": "B", "update": {"ok": True}}))
    b = await asyncio.wait_for(qb.get(), timeout=2)
    assert b.params["update"]["ok"] is True
    await r.close()


@pytest.mark.asyncio
async def test_server_request_routed_to_session_then_handler():
    # A server→client REQUEST (id + method) with a known sessionId → its queue;
    # with an unknown session → the on_server_request handler.
    seen: list = []
    out = _FakeStdout()
    r = FrameRouter(out.readline, on_server_request=lambda m: seen.append(m))
    r.start()
    q = r.register_session("A")
    out.feed(_f(id=99, method="session/request_permission", params={"sessionId": "A"}))
    out.feed(_f(id=100, method="session/request_permission", params={"sessionId": "ZZZ"}))
    routed = await asyncio.wait_for(q.get(), timeout=2)
    assert routed.id == 99 and routed.method == "session/request_permission"
    await asyncio.sleep(0.05)
    assert len(seen) == 1 and seen[0].id == 100  # unknown session → handler
    await r.close()


@pytest.mark.asyncio
async def test_broadcast_for_idless_and_unknown_session():
    seen: list = []
    out = _FakeStdout()
    r = FrameRouter(out.readline, on_broadcast=lambda m: seen.append(m))
    r.start()
    out.feed(_f(method="session/update", params={"sessionId": "unknown"}))  # no such session
    out.feed(_f(method="_notify", params={}))  # id-less, no session
    await asyncio.sleep(0.05)
    assert len(seen) == 2
    await r.close()


@pytest.mark.asyncio
async def test_process_death_fails_pending_and_wakes_sessions():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    fut = r.expect(7)
    q = r.register_session("A")
    out.eof()  # EOF → connection closed
    with pytest.raises(Exception):
        await asyncio.wait_for(fut, timeout=2)
    # session consumer is woken with a poison frame (doesn't hang forever)
    poison = await asyncio.wait_for(q.get(), timeout=2)
    assert poison.method == "_router/closed"


@pytest.mark.asyncio
async def test_process_death_fans_out_to_ALL_concurrent_sessions():
    # The P9-specific failure mode (plan risk): process death must fail EVERY pending
    # request AND wake EVERY registered session queue — not just the first. A regression
    # that woke only one session would deadlock the co-tenants.
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    futs = [r.expect(i) for i in (10, 11, 12)]         # 3 in-flight requests
    queues = [r.register_session(sid) for sid in ("A", "B", "C")]  # 3 concurrent sessions
    out.eof()  # connection dies
    # every pending future fails (no request hangs)
    for f in futs:
        with pytest.raises(Exception):
            await asyncio.wait_for(f, timeout=2)
    # every session queue receives the poison frame (no co-tenant is left hanging)
    for q in queues:
        poison = await asyncio.wait_for(q.get(), timeout=2)
        assert poison.method == "_router/closed"


@pytest.mark.asyncio
async def test_non_json_line_skipped():
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    q = r.register_session("A")
    out.feed_raw(b"this is a stray log line, not json\n")
    out.feed(_f(method="session/update", params={"sessionId": "A", "update": {"ok": 1}}))
    got = await asyncio.wait_for(q.get(), timeout=2)  # router kept reading past the junk
    assert got.params["update"]["ok"] == 1
    await r.close()


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_not_reader():
    # A tiny queue that overflows must drop-oldest, never block the single reader.
    out = _FakeStdout()
    r = FrameRouter(out.readline)
    r.start()
    q = r.register_session("A")
    # shrink the queue for the test
    q._maxsize = 3  # type: ignore[attr-defined]
    for i in range(10):
        out.feed(_f(method="session/update", params={"sessionId": "A", "update": {"n": i}}))
    await asyncio.sleep(0.1)
    # queue holds at most ~3 and has the NEWEST frames (oldest dropped)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait().params["update"]["n"])
    assert drained and max(drained) == 9 and len(drained) <= 3
    await r.close()
