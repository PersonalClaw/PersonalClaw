"""An MCP call is sent at most once, never after its caller stopped waiting, and an answer lost
after it was sent says the call may have gone through.

Before this change the connection's worker sent every call put on its queue, and looked at the
caller only once the server had answered. A call that waited behind a slow one past its deadline
was answered "timed out", and then sent anyway when the slow one finished: the agent was told
nothing happened, the server did the work, and the agent's retry did it twice. A call that was
already out when its deadline passed, or whose connection ended before the server answered, was
answered as a failure, though the server may have done it. And a stdio server that ended stayed
ended: every later call was answered with an empty failure, and none started it again.

Every server here is a real MCP server, the SDK's own ``FastMCP``, over stdio, Streamable HTTP and
SSE. It writes down each call it receives, the moment it receives it, and its slow tool answers
only once the test lets it.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import port_guard
import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw import mcp_client
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.mcp_client import McpClientRegistry

NAME = "recorder"

#: The deadline a call waits for its answer in these tests, in place of the two minutes an agent's
#: call gets. Long enough for an ordinary call to a local server on a busy machine.
_DEADLINE = 2.0

#: The deadline for a call that should simply be answered.
_ROOMY = 60.0

# The server writes down each call it receives, before it does anything else. `slow_save` then
# answers only once the release file exists; `refuse` answers with the tool's own error.
_RECORDING_SERVER = textwrap.dedent("""
    import json, os, sys

    import anyio
    from mcp.server.fastmcp import FastMCP

    TRANSPORT, RECORD, RELEASE, PORT_FILE, PID_FILE = sys.argv[1:6]
    mcp = FastMCP("recorder")
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


    def received(tool, note):
        with open(RECORD, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": tool, "note": note}) + "\\n")


    @mcp.tool(description="Save a note; answers once it is let.")
    async def slow_save(note: str) -> str:
        received("slow_save", note)
        while not os.path.exists(RELEASE):
            await anyio.sleep(0.02)
        return f"saved {note}"


    @mcp.tool(description="Save a note.")
    def save(note: str) -> str:
        received("save", note)
        return f"saved {note}"


    @mcp.tool(description="Refuse to save a note.")
    def refuse(note: str) -> str:
        received("refuse", note)
        raise ValueError(f"the notebook is read-only, so {note} was not saved")


    if TRANSPORT == "stdio":
        mcp.run()
    else:
        import socket

        import uvicorn

        app = mcp.streamable_http_app() if TRANSPORT == "http" else mcp.sse_app()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        with open(PORT_FILE + ".tmp", "w", encoding="utf-8") as f:
            f.write(str(sock.getsockname()[1]))
        os.replace(PORT_FILE + ".tmp", PORT_FILE)
        uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on")).run(sockets=[sock])
    """)


@dataclass
class Recorder:
    transport: str
    spec: dict[str, Any]
    record: Path
    release_file: Path
    pid_file: Path

    def received(self) -> list[tuple[str, str]]:
        """Every call the server received, in order, as ``(tool, note)``."""
        if not self.record.exists():
            return []
        rows = [json.loads(line) for line in self.record.read_text("utf-8").splitlines()]
        return [(row["tool"], row["note"]) for row in rows]

    async def until_received(self, count: int) -> None:
        deadline = time.monotonic() + 30
        while len(self.received()) < count:
            assert time.monotonic() < deadline, f"the server received only {self.received()}"
            await asyncio.sleep(0.02)

    def release(self) -> None:
        self.release_file.write_text("go", encoding="utf-8")

    def end(self) -> None:
        """End the server's process at once, as a crash would."""
        os.kill(int(self.pid_file.read_text("utf-8")), signal.SIGKILL)


def _recorder(tmp_path: Path, transport: str, *, pooled: bool = False):
    script = tmp_path / "recording_server.py"
    script.write_text(_RECORDING_SERVER, encoding="utf-8")
    record = tmp_path / f"received.{transport}.jsonl"
    release = tmp_path / f"release.{transport}"
    port_file = tmp_path / f"port.{transport}"
    pid_file = tmp_path / f"pid.{transport}"
    argv = [str(script), transport, str(record), str(release), str(port_file), str(pid_file)]
    pooling = {"poolable": True} if pooled else {}
    if transport == "stdio":
        yield Recorder(
            transport,
            {"command": sys.executable, "args": argv, **pooling},
            record,
            release,
            pid_file,
        )
        return
    stderr = tmp_path / f"server.{transport}.stderr"
    with stderr.open("wb") as err:
        proc = subprocess.Popen([sys.executable, *argv], stdout=subprocess.DEVNULL, stderr=err)
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the fake server did not start: {stderr.read_text()!r}")
            time.sleep(0.05)
        port = int(port_file.read_text("utf-8").strip())
        port_guard.GUARD.own(port)  # the server this test started chose it
        path = "/mcp" if transport == "http" else "/sse"
        spec = {"type": transport, "url": f"http://127.0.0.1:{port}{path}", **pooling}
        yield Recorder(transport, spec, record, release, pid_file)
    finally:
        release.write_text("go", encoding="utf-8")  # nothing is left waiting on the test
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


@pytest.fixture(params=["stdio", "http", "sse"])
def recorder(request, tmp_path):
    yield from _recorder(tmp_path, request.param)


@pytest.fixture(params=["stdio", "sse"])
def reporting_recorder(request, tmp_path):
    """A server over a transport whose session hears its connection end."""
    yield from _recorder(tmp_path, request.param)


@pytest.fixture
def stdio_recorder(tmp_path):
    yield from _recorder(tmp_path, "stdio")


@pytest.fixture
def http_recorder(tmp_path):
    yield from _recorder(tmp_path, "http")


@pytest.fixture
def pooled_recorder(tmp_path):
    yield from _recorder(tmp_path, "stdio", pooled=True)


def _registry(recorder: Recorder) -> McpClientRegistry:
    reg = McpClientRegistry()
    reg.load_from_specs({NAME: recorder.spec})
    return reg


# ── a call whose caller stopped waiting before it was sent ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_call_queued_past_its_deadline_is_dropped_unsent(recorder, monkeypatch):
    """A slow call is out; a second call waits behind it past its deadline. The second is never
    sent, and says so; the first was sent, and says it may have gone through. A call after them
    is answered as it always was."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _DEADLINE)
    reg = _registry(recorder)
    conn = reg.get(NAME)
    assert conn is not None
    try:
        assert {"slow_save", "save"} <= {tool.name for tool in await conn.list_tools()}
        first = asyncio.create_task(conn.call_tool("slow_save", {"note": "first"}))
        await recorder.until_received(1)  # the first call is out, and the server is on it
        second = await conn.call_tool("save", {"note": "second"})
        first_answer = await first
        recorder.release()
        monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _ROOMY)
        # Sent behind the second, so by its answer the second has left the queue.
        third = await conn.call_tool("save", {"note": "third"})
    finally:
        await reg.shutdown_all()

    assert recorder.received() == [("slow_save", "first"), ("save", "third")]
    ok, said = second
    assert ok is False and said.startswith("MCP tool 'save' was not sent: "), said
    assert "Nothing reached the server" in said, said
    ok, said = first_answer
    assert ok is True, said
    assert said.startswith("MCP tool 'slow_save' may have gone through: "), said
    assert "failed" not in said and "timed out" not in said, said
    assert third == (True, "saved third")


@pytest.mark.asyncio
async def test_a_pooled_server_never_sends_another_chats_call_after_it_left(
    pooled_recorder, monkeypatch
):
    """One connection serves every chat for a server that shares it. A call from a second chat
    waiting behind the first chat's slow call is dropped unsent at its deadline, as its own
    chat's would be."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _DEADLINE)
    reg = _registry(pooled_recorder)
    mine, theirs = reg.get(NAME, "chat-a"), reg.get(NAME, "chat-b")
    assert mine is not None and mine is theirs, "a shared server serves both chats on one line"
    try:
        first = asyncio.create_task(mine.call_tool("slow_save", {"note": "from chat a"}))
        await pooled_recorder.until_received(1)
        second = await theirs.call_tool("save", {"note": "from chat b"})
        await first
        pooled_recorder.release()
        monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _ROOMY)
        third = await theirs.call_tool("save", {"note": "chat b again"})
    finally:
        await reg.shutdown_all()

    assert pooled_recorder.received() == [("slow_save", "from chat a"), ("save", "chat b again")]
    assert second[0] is False and "was not sent" in second[1], second
    assert third == (True, "saved chat b again")


@pytest.mark.asyncio
async def test_a_queued_call_whose_turn_stopped_is_never_sent(stdio_recorder, monkeypatch):
    """The caller's wait can end with its turn stopping, not only at the deadline: the call it
    left in the queue is not sent either."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _ROOMY)
    reg = _registry(stdio_recorder)
    conn = reg.get(NAME)
    assert conn is not None
    try:
        first = asyncio.create_task(conn.call_tool("slow_save", {"note": "first"}))
        await stdio_recorder.until_received(1)
        second = asyncio.create_task(conn.call_tool("save", {"note": "stopped"}))
        await asyncio.sleep(0.2)  # queued behind the first
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        stdio_recorder.release()
        assert await first == (True, "saved first")
        third = await conn.call_tool("save", {"note": "third"})
    finally:
        await reg.shutdown_all()

    assert stdio_recorder.received() == [("slow_save", "first"), ("save", "third")]
    assert third == (True, "saved third")


# ── a call that was out when its connection ended ─────────────────────────────────────────────


async def _end_with_a_call_out(recorder: Recorder, conn: Any) -> tuple[Any, Any]:
    """End the server while one call is out and another waits behind it; their answers."""
    first = asyncio.create_task(conn.call_tool("slow_save", {"note": "first"}))
    await recorder.until_received(1)
    second = asyncio.create_task(conn.call_tool("save", {"note": "second"}))
    await asyncio.sleep(0.2)  # queued behind the first
    recorder.end()
    return await asyncio.wait_for(asyncio.gather(first, second), timeout=30)


def _assert_out_and_waiting(first: Any, second: Any) -> None:
    ok, said = first
    assert ok is True and said.startswith("MCP tool 'slow_save' may have gone through: "), said
    ok, said = second
    assert ok is False and said.startswith("MCP tool 'save' was not sent: "), said


@pytest.mark.asyncio
async def test_a_connection_that_ends_with_a_call_out_says_it_may_have_gone_through(
    reporting_recorder, monkeypatch
):
    """The server ends while a call is out and another waits behind it. Both are answered then,
    not at their deadline: the one out may have gone through, the one waiting was not sent. A
    stdio server is started again by the next call."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _ROOMY)
    reg = _registry(reporting_recorder)
    conn = reg.get(NAME)
    assert conn is not None
    stdio = reporting_recorder.transport == "stdio"
    try:
        started = time.monotonic()
        first, second = await _end_with_a_call_out(reporting_recorder, conn)
        answered_in = time.monotonic() - started
        third = await conn.call_tool("save", {"note": "third"}) if stdio else None
    finally:
        await reg.shutdown_all()

    assert answered_in < _ROOMY / 2, "an answer waited for its deadline"
    _assert_out_and_waiting(first, second)
    if stdio:
        assert third == (True, "saved third"), "the server was not started again"
        assert reporting_recorder.received() == [("slow_save", "first"), ("save", "third")]
    else:
        assert reporting_recorder.received() == [("slow_save", "first")]


@pytest.mark.asyncio
async def test_over_streamable_http_a_server_that_ends_mid_call_is_answered_at_the_deadline(
    http_recorder, monkeypatch
):
    """The Streamable HTTP client does not tell its session when a response stream breaks, so a
    call out when its server ends is answered at its deadline, in the same words."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _DEADLINE)
    reg = _registry(http_recorder)
    conn = reg.get(NAME)
    assert conn is not None
    try:
        first, second = await _end_with_a_call_out(http_recorder, conn)
    finally:
        await reg.shutdown_all()

    _assert_out_and_waiting(first, second)
    assert http_recorder.received() == [("slow_save", "first")]


# ── what is unchanged ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_ordinary_call_and_a_refusal_are_answered_as_before(stdio_recorder, monkeypatch):
    """BASELINE (passes before and after): what the server answers is the answer, success or its
    own error; neither says anything about being sent."""
    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _ROOMY)
    reg = _registry(stdio_recorder)
    conn = reg.get(NAME)
    assert conn is not None
    try:
        saved = await conn.call_tool("save", {"note": "groceries"})
        refused = await conn.call_tool("refuse", {"note": "groceries"})
    finally:
        await reg.shutdown_all()

    assert saved == (True, "saved groceries")
    ok, said = refused
    assert ok is False and "the notebook is read-only, so groceries was not saved" in said, said
    assert "may have gone through" not in said and "was not sent" not in said, said
    assert stdio_recorder.received() == [("save", "groceries"), ("refuse", "groceries")]


# ── what the agent reads and what the chat keeps ──────────────────────────────────────────────

KEY = "chat-3-1790361100"


def _chat_state(tmp_path: Path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "native"
    client.context_usage_pct = MagicMock(return_value=None)
    del client.cancel_session
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("save my note", None)
    state.context_builder = builder
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


async def _replayed(events: list[AgentEvent]):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_the_agent_and_the_chat_both_say_it_may_have_gone_through(
    stdio_recorder, tmp_path, monkeypatch
):
    """The agent's tool result says the call may have gone through, never that it failed, and the
    chat keeps those words on the call: live, in the chat's record, and after it is read back."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession

    monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", _DEADLINE)
    reg = _registry(stdio_recorder)
    provider = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title=f"mcp/{NAME}/slow_save",
                    tool_input=json.dumps({"note": "first"}),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="checking"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[provider.McpToolProvider(lambda: reg)],
    )
    events: list[AgentEvent] = []
    try:
        await runtime.start()

        async def turn() -> None:
            async for event in runtime.stream("save my note"):
                events.append(event)
                if event.kind == EVENT_PERMISSION_REQUEST:
                    await runtime.approve_tool(event.request_id)

        await asyncio.wait_for(turn(), timeout=30)
    finally:
        stdio_recorder.release()
        await reg.shutdown_all()

    [result] = [event for event in events if event.kind == EVENT_TOOL_RESULT]
    told = str(result.tool_output)
    assert told.startswith("MCP tool 'slow_save' may have gone through: "), told
    assert (result.tool_meta or {}).get("ok") is not False, result.tool_meta
    assert stdio_recorder.received() == [("slow_save", "first")]

    # The chat, given what the runtime streamed.
    state, client = _chat_state(tmp_path)
    shown = [event for event in events if event.kind != EVENT_PERMISSION_REQUEST]
    client.stream = MagicMock(side_effect=lambda *a, **kw: _replayed(shown))
    session = _ChatSession(KEY)
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "save my note")

    frames = [
        call.args[1]
        for call in state.broadcast_ws.call_args_list
        if call.args and call.args[0] == "tool_result"
    ]
    [frame] = [f for f in frames if f.get("tool_call_id") == "c1"]
    assert "may have gone through" in frame["output"] and frame.get("ok") is not False, frame

    def card(rows: list[dict]) -> dict:
        [row] = [
            r
            for r in rows
            if r.get("role") == "tool" and (r.get("meta") or {}).get("tool_call_id") == "c1"
        ]
        return row["meta"]

    kept = card(session.messages)
    assert kept.get("done") is True and kept.get("ok") is not False, kept
    assert kept["output"].startswith("MCP tool 'slow_save' may have gone through: "), kept

    save_session_to_history(state, session, force=True, final=True)
    read_back = card(state.conversation_log.read_messages(f"dashboard:{KEY}"))
    assert read_back.get("ok") is not False, read_back
    assert read_back["output"] == kept["output"]
