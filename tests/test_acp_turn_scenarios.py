"""Black-box turn scenarios — the stdout-boundary oracle for the P9 convergence (task #7).

The ~65 existing client turn tests mock ``_prompt_loop`` to yield ``(action, msg)`` tuples —
white-box tests of the machinery being RETIRED. This module instead scripts RAW JSON-RPC
frames onto a fake stdout and drives the PUBLIC ``stream_events`` API, asserting on the
yielded ``AcpEvent`` stream. That is architecture-agnostic: it exercises whatever reader
the client uses (today the inline ``_read_message``/``_prompt_loop``; after convergence the
FrameRouter+AcpSession path) with ZERO changes — so it is a faithful oracle to guard the
cutover. Each scenario models one real turn shape (text, thinking, tool call+result,
permission, interrupted, stale, error, command).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.acp.client import AcpClient
from personalclaw.acp.types import (
    EVENT_AGENT_SWITCHED,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    METHOD_AGENT_SWITCHED,
)


class _ScriptedStdout:
    """A fake asyncio StreamReader whose readline() returns pre-scripted JSON-RPC frames
    (one per line), then BLOCKS (like a real idle stdio pipe that stays open).

    Real backend stdout does NOT hit EOF the instant a turn's frames are written — the
    process stays alive for the next turn. Blocking after exhaustion models that: a turn
    completes on its terminal ``result`` frame (or the stale-silence timeout for the
    no-terminal scenarios), never on a synthetic EOF/close. This keeps the FrameRouter
    reader alive across the turn, exactly as in production."""

    def __init__(self, frames: list[dict]):
        self._lines = [(json.dumps(f) + "\n").encode() for f in frames]
        self._i = 0

    async def readline(self) -> bytes:
        if self._i >= len(self._lines):
            # Stream stays open briefly after the scripted frames (a real pipe doesn't EOF
            # the instant a turn's frames are written), THEN closes. The short idle lets a
            # turn complete on its terminal `result` frame before the close, while the
            # eventual EOF still drives the no-terminal scenarios (stale/interrupted) and
            # never hangs the suite.
            await asyncio.sleep(0.3)
            return b""  # EOF
        line = self._lines[self._i]
        self._i += 1
        return line


def _client_with_frames(frames: list[dict], *, req_id: int = 1, session_key: str = "s") -> AcpClient:
    """A ready AcpClient wired to a scripted stdout that emits *frames*, driven through
    the REAL turn path (FrameRouter → AcpSession) — the P9#7 wrapper architecture.

    We stub only ``ensure_ready`` (no real subprocess) and inject a live
    :class:`AcpConnection` whose FrameRouter reads the scripted stdout via a fake
    transport, plus one bound :class:`AcpSession` on the well-known ``req_id``. Because
    the turn's request id is deterministic (the fake connection's id counter starts at
    the scripted *req_id* minus one), the terminal frame's ``id`` matches. The scenario
    assertions below are unchanged — only this harness plumbing adapts to the wrapper."""
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    client = AcpClient(session_key=session_key)

    scripted = _ScriptedStdout(frames)

    class _FakeTransport:
        """Minimal transport: a readline() over the scripted frames + a no-op stdin."""
        def __init__(self):
            self._alive = True

        async def readline(self) -> bytes:
            return await scripted.readline()

        async def write(self, data: str) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

    transport = _FakeTransport()
    router = FrameRouter(transport.readline)
    conn = AcpConnection(None, router, dialect=client._dialect, transport=transport)
    # The turn's request id must equal the scripted terminal frame's id. The session
    # allocates its prompt id via conn.send_request → conn._req_id() (pre-increment),
    # so seed the counter so the FIRST allocated id is *req_id*.
    conn._next_id = req_id - 1

    # Bind the session (registers its router queue) BEFORE starting the reader, so no
    # scripted notification is read + routed before the queue exists (else it would be
    # dropped to the broadcast sink). Real spawns register per session/new too.
    session = conn._bind_session("sess-abc", session_files_dir=None)
    router.start()
    client._connection = conn
    client._session = session
    client._session_id = "sess-abc"
    client.ensure_ready = AsyncMock()  # connection + session already wired above
    return client


async def _collect(client: AcpClient, message: str = "go") -> list:
    return [ev async for ev in client.stream_events(message)]


# ── frame builders ───────────────────────────────────────────────────────────
# Every session-scoped notification carries ``sessionId`` (as real backends do) so the
# FrameRouter demuxes it to the session's queue. The bound session id is "sess-abc".
_SID = "sess-abc"


def _text(t: str, kind: str = "text") -> dict:
    return {"method": "session/update", "params": {"sessionId": _SID, "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": kind, "text": t}}}}


def _tool_call(tid: str, title: str, kind: str = "read", raw: dict | None = None) -> dict:
    return {"method": "session/update", "params": {"sessionId": _SID, "update": {
        "sessionUpdate": "tool_call", "toolCallId": tid, "title": title, "kind": kind,
        "rawInput": raw or {"path": "/x"}}}}


def _tool_update(tid: str, *, status: str = "completed", output: str = "done") -> dict:
    return {"method": "session/update", "params": {"sessionId": _SID, "update": {
        "sessionUpdate": "tool_call_update", "toolCallId": tid, "status": status,
        "content": [{"type": "content", "content": {"type": "text", "text": output}}]}}}


def _complete(req_id: int = 1, reason: str = "end_turn") -> dict:
    return {"id": req_id, "result": {"stopReason": reason}}


# ── scenarios ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scenario_plain_text_then_complete():
    c = _client_with_frames([_text("hello world"), _complete()])
    events = await _collect(c)
    assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == "hello world"
    assert events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_scenario_thinking_then_text():
    c = _client_with_frames([_text("pondering", "thinking"), _text("answer"), _complete()])
    events = await _collect(c)
    kinds = [e.kind for e in events]
    assert kinds == [EVENT_THINKING_CHUNK, EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    # only the non-thinking chunk counts toward text_chunks telemetry
    assert c.last_prompt_stats.text_chunks == 1


@pytest.mark.asyncio
async def test_scenario_tool_call_then_update_result():
    c = _client_with_frames([
        _tool_call("t1", "Read file"),
        _tool_update("t1", output="file contents"),
        _complete(),
    ])
    events = await _collect(c)
    kinds = [e.kind for e in events]
    assert EVENT_TOOL_CALL in kinds
    # a tool_call_update carrying terminal content yields a tool_result on the same card
    assert EVENT_TOOL_RESULT in kinds
    assert kinds[-1] == EVENT_COMPLETE
    tc = next(e for e in events if e.kind == EVENT_TOOL_CALL)
    assert tc.title == "Read file" and tc.tool_call_id == "t1"
    tr = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert tr.tool_call_id == "t1" and "file contents" in tr.tool_output


@pytest.mark.asyncio
async def test_scenario_permission_request():
    c = _client_with_frames([
        {"id": 55, "method": "session/request_permission",
         "params": {"sessionId": _SID, "toolCall": {"title": "Write", "toolCallId": "t9"}, "options": []}},
        _complete(),
    ])
    events = await _collect(c)
    perms = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(perms) == 1 and perms[0].title == "Write" and perms[0].request_id == 55
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_scenario_agent_switch():
    c = _client_with_frames([
        {"method": METHOD_AGENT_SWITCHED, "params": {"sessionId": _SID, "agentName": "planner"}},
        _text("switched"), _complete(),
    ])
    events = await _collect(c)
    sw = [e for e in events if e.kind == EVENT_AGENT_SWITCHED]
    assert sw and sw[0].text == "planner"


@pytest.mark.asyncio
async def test_scenario_tool_interrupted_marker_synthesizes_complete():
    # The security-filter marker → synthetic complete (no terminal frame scripted).
    c = _client_with_frames([_text("Tool uses were interrupted, waiting for the next user prompt")])
    c._emit_tool_interrupted_sel = MagicMock()  # skip SEL wiring, focus on the event contract
    events = await _collect(c)
    assert events[-1].kind == EVENT_COMPLETE   # synthesized despite no `result` frame
    assert any(e.kind == EVENT_TEXT_CHUNK for e in events)


@pytest.mark.asyncio
async def test_scenario_multi_text_chunks_stream_in_order():
    c = _client_with_frames([_text("one "), _text("two "), _text("three"), _complete()])
    events = await _collect(c)
    texts = [e.text for e in events if e.kind == EVENT_TEXT_CHUNK]
    assert texts == ["one ", "two ", "three"]
    assert c.last_prompt_stats.text_chunks == 3


@pytest.mark.asyncio
async def test_scenario_error_terminal_raises():
    # A terminal frame carrying `error` (not `result`) → the turn raises AcpError.
    from personalclaw.acp.errors import AcpError

    c = _client_with_frames([_text("partial"), {"id": 1, "error": {"code": -32000, "message": "boom"}}])
    with pytest.raises(AcpError):
        await _collect(c)


@pytest.mark.asyncio
async def test_scenario_command_result_formatted_as_text():
    # stream_command uses commands/execute → the terminal result's message+data is
    # formatted into a text chunk (extract_agent_from_result path).
    c = _client_with_frames([{"id": 1, "result": {"message": "usage report", "data": {"tokens": 42}}}])
    events = [ev async for ev in c.stream_command("/usage")]
    texts = [e.text for e in events if e.kind == EVENT_TEXT_CHUNK]
    assert any("usage report" in t and "\"tokens\": 42" in t for t in texts)
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_scenario_jsonl_tool_results_surface(tmp_path):
    # Backends that persist tool results to a per-session JSONL file (opt-in via
    # session_files_dir) must surface them as EVENT_TOOL_RESULT — the client-unique
    # path the session replicates; guard it here too.
    c = _client_with_frames([_text("running tool"), _complete()])
    # The per-session JSONL tail is owned by the AcpSession now (opt-in via its
    # session_files_dir); point it at the tmp file so the real reader surfaces results.
    c._session._session_files_dir = tmp_path
    (tmp_path / "sess-abc.jsonl").write_text(json.dumps({
        "kind": "ToolResults",
        "data": {"content": [{"kind": "toolResult", "data": {
            "toolUseId": "j1", "content": [{"kind": "text", "data": "jsonl output"}]}}]},
    }) + "\n")
    events = await _collect(c)
    results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(results) == 1 and results[0].tool_call_id == "j1"
    assert "jsonl output" in results[0].tool_output


# ── send_message (the →str public API) ─────────────────────────────────────────
# stream_events is only one of the two turn surfaces. `send_message` returns the
# whole turn as a STRING (a preserved public method the cutover reimplements by
# draining the same frame stream — task #7 step 3, retiring `_read_prompt_response`).
# The oracle must pin its contract at the stdout boundary too, or the cutover has no
# net under the string API.
@pytest.mark.asyncio
async def test_scenario_send_message_concatenates_text():
    c = _client_with_frames([_text("Hello, "), _text("world!"), _complete()])
    result = await c.send_message("hi")
    assert result == "Hello, world!"           # non-thinking chunks joined in order
    assert c.last_prompt_stats.text_chunks == 2
    assert c._last_stop_reason == "end_turn"   # terminal stopReason recorded for wait_turn_done


@pytest.mark.asyncio
async def test_scenario_send_message_excludes_thinking():
    # Thinking chunks stream to the UI but must NOT leak into the returned answer string.
    c = _client_with_frames([_text("deliberating", "thinking"), _text("final answer"), _complete()])
    result = await c.send_message("hi")
    assert result == "final answer"            # thinking excluded from the concatenation
    assert c.last_prompt_stats.text_chunks == 1


@pytest.mark.asyncio
async def test_scenario_send_message_error_frame_raises():
    from personalclaw.acp.errors import AcpError

    c = _client_with_frames([_text("partial"), {"id": 1, "error": {"code": -32000, "message": "kaboom"}}])
    with pytest.raises(AcpError):
        await c.send_message("hi")


# ── failed-tool result (V6 live facet, now pinned black-box) ───────────────────
@pytest.mark.asyncio
async def test_scenario_failed_tool_surfaces_result():
    # A tool_call_update with status="failed" carries the error text in the SAME
    # content shape as "completed" and MUST surface as EVENT_TOOL_RESULT (the user
    # needs to see the failure) — translate.extract_tool_update_events, the failed branch.
    c = _client_with_frames([
        _tool_call("t1", "Run ls"),
        _tool_update("t1", status="failed", output="ls: /nope: No such file or directory"),
        _complete(),
    ])
    events = await _collect(c)
    tr = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(tr) == 1 and tr[0].tool_call_id == "t1"
    assert "No such file or directory" in tr[0].tool_output   # failure text surfaced, not dropped
    assert events[-1].kind == EVENT_COMPLETE                   # turn still completes normally


# ── stale-synthetic complete (agent streamed text but never sent `result`) ─────
@pytest.mark.asyncio
async def test_scenario_stale_text_without_terminal_synthesizes_complete(monkeypatch):
    # The stdout closes (EOF) after text but with NO terminal `result` frame. Because
    # text was streamed (_stale_eligible), the turn is finalized with a synthetic
    # EVENT_COMPLETE(end_turn) rather than surfacing a timeout — distinct from the
    # security-marker synthesize (that path keys off the interrupted marker text).
    # Shrink the stale-silence window (real value 90s) so the oracle stays fast — the
    # CONTRACT under test is "synthesize on stale EOF", not the wall-clock duration.
    monkeypatch.setattr("personalclaw.acp.client._STALE_TURN_TIMEOUT", 0.1)
    c = _client_with_frames([_text("here is a partial reply")])
    events = await _collect(c)
    assert any(e.kind == EVENT_TEXT_CHUNK for e in events)
    assert events[-1].kind == EVENT_COMPLETE
    assert events[-1].stop_reason == "end_turn"


# ── stop_reason passthrough (not hardcoded to end_turn) ────────────────────────
@pytest.mark.asyncio
async def test_scenario_stop_reason_passthrough_non_default():
    # The terminal frame's stopReason is surfaced VERBATIM on EVENT_COMPLETE — guards
    # against a cutover that hardcodes end_turn instead of reading the frame.
    c = _client_with_frames([_text("cut short"), _complete(reason="max_tokens")])
    events = await _collect(c)
    assert events[-1].kind == EVENT_COMPLETE
    assert events[-1].stop_reason == "max_tokens"
    assert c._last_stop_reason == "max_tokens"
