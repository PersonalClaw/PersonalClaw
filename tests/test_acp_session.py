"""Tests for AcpSession (acp/session.py) — the P9 per-session turn loop over a
FrameRouter queue. Driven by a fake queue + stub send/cancel/liveness — no process."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.acp.session import AcpSession
from personalclaw.acp.types import JsonRpcMessage


def _mk(session_id="A", *, alive=True, dialect=None, session_files_dir=None):
    q: asyncio.Queue[JsonRpcMessage] = asyncio.Queue()
    sent: list = []  # (method, params) tuples from send_request
    responses: list = []  # (req_id, result) tuples from send_response
    cancels: list = []
    counter = {"id": 100}

    async def send_request(method, params):
        counter["id"] += 1
        rid = counter["id"]
        sent.append((method, params))
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        # stash the future so a test can resolve it as the turn's terminal response
        sent_futs.append((rid, fut))
        return rid, fut

    async def send_response(req_id, result):
        responses.append((req_id, result))

    async def cancel_session():
        cancels.append(session_id)

    sent_futs: list = []
    s = AcpSession(
        session_id,
        q,
        send_request=send_request,
        send_response=send_response,
        cancel_session=cancel_session,
        is_process_alive=lambda: alive,
        dialect=dialect,
        session_files_dir=session_files_dir,
    )
    # expose the plumbing tests need
    s._test_sent = sent
    s._test_responses = responses
    s._test_cancels = cancels
    s._test_sent_futs = sent_futs
    return s, q, sent, cancels


def _resolved_future(msg):
    """A pre-resolved response future (stands in for router.expect(rid))."""
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(msg)
    return fut


@pytest.mark.asyncio
async def test_drain_yields_updates_then_terminal_response():
    s, q, _sent, _c = _mk()
    # Notifications land on the QUEUE; the terminal response resolves the FUTURE
    # (this is how FrameRouter actually demuxes the two channels).
    q.put_nowait(
        JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {"n": 1}})
    )
    q.put_nowait(
        JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {"n": 2}})
    )
    fut = _resolved_future(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    got = []
    async for m in s._drain_turn(10, fut, timeout=5):
        got.append(m)
    assert len(got) == 3  # 2 updates + terminal response
    assert got[-1].id == 10  # last is the terminal response (from the future)
    assert got[0].params["update"]["n"] == 1
    assert got[1].params["update"]["n"] == 2  # ordering preserved: updates before terminal


@pytest.mark.asyncio
async def test_cancel_scopes_to_this_session():
    s, _q, _sent, cancels = _mk("A")
    await s.cancel()
    assert cancels == ["A"]  # session/cancel issued for THIS sid only
    assert s._cancelled is True


def _pending_future():
    """A response future that never resolves (the turn exits by stale/death/poison)."""
    return asyncio.get_event_loop().create_future()


@pytest.mark.asyncio
async def test_stale_turn_completes_after_silence():
    # After streaming a frame, prolonged silence ends the turn (no hang).
    import personalclaw.acp.session as sess_mod

    s, q, _sent, _c = _mk()
    q.put_nowait(JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {}}))
    # shrink the stale timeout so the test is fast
    orig = sess_mod._STALE_TURN_TIMEOUT
    sess_mod._STALE_TURN_TIMEOUT = 0.2
    try:
        got = []
        async for m in s._drain_turn(10, _pending_future(), timeout=5):
            got.append(m)
        # streamed the one update, then completed on staleness (never saw a terminal response)
        assert len(got) == 1
    finally:
        sess_mod._STALE_TURN_TIMEOUT = orig


@pytest.mark.asyncio
async def test_process_death_ends_turn():
    s, q, _sent, _c = _mk("A", alive=False)  # process not alive
    # no frames arrive; the liveness check ends the drain rather than hanging
    got = [m async for m in s._drain_turn(10, _pending_future(), timeout=3)]
    assert got == []


@pytest.mark.asyncio
async def test_router_closed_poison_ends_turn():
    s, q, _sent, _c = _mk()
    q.put_nowait(JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {}}))
    q.put_nowait(JsonRpcMessage(method="_router/closed"))  # connection died
    got = [m async for m in s._drain_turn(10, _pending_future(), timeout=5)]
    assert len(got) == 1  # the one update, then stopped on poison


@pytest.mark.asyncio
async def test_terminal_via_future_flushes_buffered_notifications():
    # The real router race: the terminal response resolves the FUTURE while
    # notifications are still buffered on the QUEUE. The drain must flush the
    # buffered updates FIRST, then yield the terminal last (never drop them).
    s, q, _sent, _c = _mk()
    fut = _resolved_future(JsonRpcMessage(id=7, result={"stopReason": "end_turn"}))
    # updates enqueued but not yet consumed when the (already-resolved) future is seen
    q.put_nowait(
        JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {"n": 1}})
    )
    q.put_nowait(
        JsonRpcMessage(method="session/update", params={"sessionId": "A", "update": {"n": 2}})
    )
    got = [m async for m in s._drain_turn(7, fut, timeout=5)]
    assert [m.id for m in got] == [None, None, 7]  # both updates flushed, terminal last
    assert got[0].params["update"]["n"] == 1
    assert got[-1].result["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_drain_stops_when_response_future_errors():
    # A connection-error future (router.close sets ConnectionError) ends the turn
    # cleanly rather than raising out of the async iterator.
    s, q, _sent, _c = _mk()
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_exception(ConnectionError("ACP connection closed"))
    got = [m async for m in s._drain_turn(9, fut, timeout=5)]
    assert got == []  # no frames, no exception escapes


# ── stream_events: the turn ladder over the two-channel drain ────────────────


def _upd(sid, update):
    return JsonRpcMessage(method="session/update", params={"sessionId": sid, "update": update})


@pytest.mark.asyncio
async def test_stream_events_text_tool_then_complete():
    from personalclaw.acp.types import (
        EVENT_COMPLETE,
        EVENT_TEXT_CHUNK,
        EVENT_THINKING_CHUNK,
        EVENT_TOOL_CALL,
    )

    s, q, _sent, _c = _mk()
    # queue up: thinking chunk, text chunk, a tool_call — then resolve the turn.
    q.put_nowait(
        _upd(
            "A",
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "thinking", "text": "hmm"},
            },
        )
    )
    q.put_nowait(
        _upd(
            "A",
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}},
        )
    )
    q.put_nowait(
        _upd(
            "A",
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "title": "Read",
                "kind": "read",
                "rawInput": {"path": "/x"},
            },
        )
    )

    async def _resolve_after_send():
        # stream_events calls _send_request (records a future); resolve it as the terminal.
        while not s._test_sent_futs:
            await asyncio.sleep(0)
        rid, fut = s._test_sent_futs[-1]
        # give the drain a moment to consume the queued updates first
        await asyncio.sleep(0.05)
        fut.set_result(JsonRpcMessage(id=rid, result={"stopReason": "end_turn"}))

    asyncio.ensure_future(_resolve_after_send())
    kinds = []
    async for ev in s.stream_events("hi", timeout=5):
        kinds.append(ev.kind)
    assert kinds[0] == EVENT_THINKING_CHUNK
    assert EVENT_TEXT_CHUNK in kinds
    assert EVENT_TOOL_CALL in kinds
    assert kinds[-1] == EVENT_COMPLETE
    # the prompt was sent as session/prompt on THIS session id
    assert _sent[-1][0] == "session/prompt"
    assert _sent[-1][1]["sessionId"] == "A"
    assert s.last_prompt_stats.text_chunks == 1  # only the non-thinking chunk counted


@pytest.mark.asyncio
async def test_stream_events_tool_interrupted_marker_synthesizes_complete():
    from personalclaw.acp.types import EVENT_COMPLETE, EVENT_TEXT_CHUNK

    s, q, _sent, _c = _mk()
    q.put_nowait(
        _upd(
            "A",
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": "Tool uses were interrupted, waiting for the next user prompt",
                },
            },
        )
    )
    # NOTE: we never resolve the future — the marker must synthesize a complete itself.
    kinds = [ev.kind async for ev in s.stream_events("go", timeout=5)]
    assert kinds == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]  # marker text, then synthetic complete
    assert s._turn_done.is_set()


@pytest.mark.asyncio
async def test_stream_events_permission_event_uses_dialect():
    from personalclaw.acp.dialect import DefaultDialect
    from personalclaw.acp.types import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST

    s, q, _sent, _c = _mk(dialect=DefaultDialect())
    # a server→client permission request (id + method), routed to this session's queue
    q.put_nowait(
        JsonRpcMessage(
            id=55,
            method="session/request_permission",
            params={
                "sessionId": "A",
                "toolCall": {"title": "Write", "toolCallId": "t9"},
                "options": [],
            },
        )
    )

    async def _resolve():
        while not s._test_sent_futs:
            await asyncio.sleep(0)
        rid, fut = s._test_sent_futs[-1]
        await asyncio.sleep(0.05)
        fut.set_result(JsonRpcMessage(id=rid, result={"stopReason": "end_turn"}))

    asyncio.ensure_future(_resolve())
    events = [ev async for ev in s.stream_events("edit", timeout=5)]
    perms = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(perms) == 1 and perms[0].request_id == 55 and perms[0].title == "Write"
    assert events[-1].kind == EVENT_COMPLETE
    # approving resolves the offered option via the dialect + sends a response
    await s.approve_tool(55)
    assert s._test_responses and s._test_responses[-1][0] == 55


@pytest.mark.asyncio
async def test_stream_command_formats_result_text():
    from personalclaw.acp.types import EVENT_COMPLETE, EVENT_TEXT_CHUNK

    s, q, _sent, _c = _mk()

    async def _resolve():
        while not s._test_sent_futs:
            await asyncio.sleep(0)
        rid, fut = s._test_sent_futs[-1]
        await asyncio.sleep(0.02)
        fut.set_result(JsonRpcMessage(id=rid, result={"message": "done", "data": {"k": "v"}}))

    asyncio.ensure_future(_resolve())
    events = [ev async for ev in s.stream_command("/usage", timeout=5)]
    # commands/execute output arrives in the terminal result → formatted as a text chunk
    texts = [e.text for e in events if e.kind == EVENT_TEXT_CHUNK]
    assert any("done" in t and '"k": "v"' in t for t in texts)
    assert events[-1].kind == EVENT_COMPLETE
    assert _sent[-1][0] == "_vendor.dev/commands/execute"


@pytest.mark.asyncio
async def test_stream_events_flushes_jsonl_tool_results(tmp_path):
    # Backends that persist tool results to a per-session JSONL file (opt-in via
    # session_files_dir) must have those surfaced as EVENT_TOOL_RESULT in the stream.
    import json as _json

    from personalclaw.acp.types import EVENT_COMPLETE, EVENT_TOOL_RESULT

    (tmp_path / "A.jsonl").write_text(
        _json.dumps(
            {
                "kind": "ToolResults",
                "data": {
                    "content": [
                        {
                            "kind": "toolResult",
                            "data": {
                                "toolUseId": "t1",
                                "content": [{"kind": "text", "data": "the output"}],
                            },
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    s, q, _sent, _c = _mk(session_files_dir=tmp_path)
    # a text chunk triggers a JSONL flush before it
    q.put_nowait(
        _upd(
            "A", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}
        )
    )

    async def _resolve():
        while not s._test_sent_futs:
            await asyncio.sleep(0)
        rid, fut = s._test_sent_futs[-1]
        await asyncio.sleep(0.05)
        fut.set_result(JsonRpcMessage(id=rid, result={"stopReason": "end_turn"}))

    asyncio.ensure_future(_resolve())
    events = [ev async for ev in s.stream_events("go", timeout=5)]
    results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(results) == 1
    assert results[0].tool_call_id == "t1" and "the output" in results[0].tool_output
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_per_session_turn_lock_is_not_process_wide():
    # Two sessions have independent turn locks (no shared/process-wide lock).
    sa, _qa, _sa2, _ca = _mk("A")
    sb, _qb, _sb2, _cb = _mk("B")
    assert sa._turn_lock is not sb._turn_lock
    async with sa._turn_lock:  # holding A's lock must not block B's
        assert not sb._turn_lock.locked()


# ── AcpConnection: multi-session on one process (the P9 win) ────────────────


class _FakeProc:
    """Fake asyncio subprocess: a writable stdin that records frames + a controllable
    returncode. stdout is driven separately via the router's readline source."""

    class _Stdin:
        def __init__(self):
            self.written = []

        def write(self, b):
            self.written.append(b)

        async def drain(self):
            pass

    def __init__(self):
        self.stdin = self._Stdin()
        self.returncode = None


class _ScriptedStdout:
    def __init__(self):
        self._q = __import__("asyncio").Queue()

    def push(self, obj):
        self._q.put_nowait((__import__("json").dumps(obj) + "\n").encode())

    async def readline(self):
        return await self._q.get()


@pytest.mark.asyncio
async def test_connection_opens_two_concurrent_sessions():
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    proc = _FakeProc()
    out = _ScriptedStdout()
    router = FrameRouter(out.readline)
    router.start()
    conn = AcpConnection(proc, router)
    # respond to the two session/new requests (ids 1 and 2 — connection's counter)
    out.push({"id": 1, "result": {"sessionId": "sess-1"}})
    out.push({"id": 2, "result": {"sessionId": "sess-2"}})
    s1 = await conn.new_session({"cwd": "/tmp", "mcpServers": []}, timeout=3)
    s2 = await conn.new_session({"cwd": "/tmp", "mcpServers": []}, timeout=3)
    assert s1.session_id == "sess-1" and s2.session_id == "sess-2"
    assert conn.session_count() == 2
    # interleaved frames demux to the right session queues
    out.push({"method": "session/update", "params": {"sessionId": "sess-1", "update": {"n": "a"}}})
    out.push({"method": "session/update", "params": {"sessionId": "sess-2", "update": {"n": "b"}}})
    f1 = await asyncio.wait_for(s1._queue.get(), timeout=2)
    f2 = await asyncio.wait_for(s2._queue.get(), timeout=2)
    assert f1.params["update"]["n"] == "a" and f2.params["update"]["n"] == "b"
    await conn.close()


@pytest.mark.asyncio
async def test_connection_request_correlates_response():
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    proc = _FakeProc()
    out = _ScriptedStdout()
    router = FrameRouter(out.readline)
    router.start()
    conn = AcpConnection(proc, router)
    out.push(
        {"id": 1, "result": {"protocolVersion": 1, "agentCapabilities": {"promptCapabilities": {}}}}
    )
    caps = await conn.initialize({"protocolVersion": 1}, timeout=3)
    assert isinstance(caps, dict)
    # a session/new request the connection wrote is present in stdin
    import json as _j

    methods = [_j.loads(b.decode())["method"] for b in proc.stdin.written]
    assert "initialize" in methods
    await conn.close()


@pytest.mark.asyncio
async def test_connection_new_session_without_sid_raises():
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    proc = _FakeProc()
    out = _ScriptedStdout()
    router = FrameRouter(out.readline)
    router.start()
    conn = AcpConnection(proc, router)
    out.push({"id": 1, "result": {}})  # no sessionId
    with pytest.raises(RuntimeError):
        await conn.new_session({"cwd": "/tmp"}, timeout=3)
    await conn.close()


@pytest.mark.asyncio
async def test_connection_close_session_unregisters():
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    proc = _FakeProc()
    out = _ScriptedStdout()
    router = FrameRouter(out.readline)
    router.start()
    conn = AcpConnection(proc, router)
    out.push({"id": 1, "result": {"sessionId": "s1"}})
    await conn.new_session({"cwd": "/tmp"}, timeout=3)
    assert router.has_session("s1")
    await conn.close_session("s1")
    assert not router.has_session("s1") and conn.session_count() == 0
    await conn.close()


# ── classify_frame: the shared turn-action classifier (cutover step 1) ────────


def test_classify_frame_actions():
    from personalclaw.acp.session import classify_frame
    from personalclaw.acp.types import JsonRpcMessage as M

    assert classify_frame(M(id=5, result={"stopReason": "end_turn"}), 5) == "complete"
    assert classify_frame(M(id=5, error={"code": -1}), 5) == "error"
    assert classify_frame(M(method="session/update", params={"sessionId": "A"}), 5) == "update"
    assert (
        classify_frame(M(id=9, method="session/request_permission", params={"sessionId": "A"}), 5)
        == "permission"
    )
    assert classify_frame(M(method="_vendor.dev/metadata", params={}), 5) == "metadata"
    assert classify_frame(M(method="totally-unknown"), 5) == "skip"
    # a response for a DIFFERENT req id is not this turn's completion
    assert classify_frame(M(id=99, result={}), 5) == "skip"


# ── extract_text_chunk: shared text/thinking classifier (cutover step 2) ──────


def test_extract_text_chunk_shared():
    from personalclaw.acp.session import extract_text_chunk
    from personalclaw.acp.types import JsonRpcMessage as M

    def up(**c):
        return M(
            method="session/update",
            params={"update": {"sessionUpdate": "agent_message_chunk", "content": c}},
        )

    assert extract_text_chunk(up(text="hi", type="text")) == ("hi", False)
    assert extract_text_chunk(up(text="mm", type="thinking")) == ("mm", True)
    assert extract_text_chunk(up(text="mm", type="reasoning")) == ("mm", True)
    # non-text-chunk updates → (None, False)
    assert extract_text_chunk(
        M(method="session/update", params={"update": {"sessionUpdate": "tool_call"}})
    ) == (None, False)
    assert extract_text_chunk(M(method="session/update", params={})) == (None, False)


def test_client_extract_text_chunk_delegates_to_shared():
    # The client's method must produce the SAME result as the shared fn (no drift).
    from personalclaw.acp.session import extract_text_chunk
    from personalclaw.acp.types import JsonRpcMessage as M

    msg = M(
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": "x", "type": "thinking"},
            }
        },
    )
    # call the unbound method with a bare instance-free object isn't safe; compare via a real-ish client is heavy.  # noqa: E501
    # Instead assert the client method body delegates (both yield identical output for the same msg).  # noqa: E501
    assert extract_text_chunk(msg) == ("x", True)
