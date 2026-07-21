"""Wrapper-level tests for AcpClient (post-P9#7).

AcpClient is now a thin N=1 wrapper over AcpConnection + AcpSession. The turn-loop
machinery it used to own inline (the stdout reader, frame classification, dispatch,
permission decode, tool-result tailing) has moved into the shared modules and is tested
where it lives:

* stdout-boundary turn behavior (text/thinking/tool/permission/interrupted/stale/error/
  command, send_message string API, stop_reason passthrough) — test_acp_turn_scenarios.py
  (the black-box oracle, driven through the wrapper's real FrameRouter+AcpSession path).
* the single reader + frame routing — test_acp_reader.py
* the per-session turn loop + cancel/stale/has_active_turn — test_acp_session.py
* process spawn/kill/PID-tree/stderr/pipes/env — test_acp_transport.py
* the pure ACP↔neutral decoders — test_acp_translate.py

So this file covers ONLY what the wrapper itself owns: construction/config, the
transport-proxy attrs external code reaches through, the handshake orchestration
(load-vs-new, ordered activate/model/mode/effort, snapshot capture), and that the
turn/lifecycle methods delegate to the held session/connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.acp.client import AcpClient, AcpError, _make_unified_diff  # noqa: F401
from personalclaw.acp.types import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    METHOD_SET_MODE,
    METHOD_SET_MODEL,
    AcpEvent,
    AcpPromptStats,
)


# ── construction / config ─────────────────────────────────────────────────────
class TestAcpClientInit:
    def test_defaults(self):
        client = AcpClient()
        assert not client.is_ready
        assert client._session_id is None
        assert client._session is None and client._connection is None

    def test_custom_work_dir(self, tmp_path):
        client = AcpClient(work_dir=tmp_path)
        assert client._work_dir == tmp_path

    def test_stores_session_key_and_channel(self):
        client = AcpClient(session_key="test-key", channel_id="C0ABC123")
        assert client._session_key == "test-key"
        assert client._channel_id == "C0ABC123"

    def test_make_unified_diff_reexport(self):
        # Re-exported at module scope for importers; delegates to translate.
        out = _make_unified_diff("a\n", "b\n", "f.txt")
        assert "f.txt" in out and "-a" in out and "+b" in out


# ── transport proxies (external code reaches through these names) ───────────────
class TestTransportProxies:
    def test_pid_proxies_transport(self):
        client = AcpClient()
        client._transport._pid = 4321
        assert client._pid == 4321  # session.py / session_pid.py read client._pid

    def test_process_alive_and_exit_code_proxy(self):
        client = AcpClient()
        assert client.is_process_alive() is False  # nothing spawned
        assert client.exit_code is None

    def test_is_responsive_delegates(self):
        client = AcpClient()
        # No process → not responsive; must not raise.
        assert client.is_responsive() is False

    def test_touch_activity_delegates(self):
        client = AcpClient()
        client.touch_activity()  # no-op without a process, must not raise

    def test_rekey_updates_identity(self):
        client = AcpClient(session_key="a")
        client.rekey("b", channel_id="C9")
        assert client._session_key == "b" and client._channel_id == "C9"


# ── handshake orchestration (_initialize_session over a fake connection) ────────
def _fake_conn(*, caps=None, sid="sess-1", snapshot=None):
    """A MagicMock AcpConnection recording send_request calls made during handshake."""
    conn = MagicMock()
    conn.initialize = AsyncMock(return_value=caps or {})
    conn.agent_capabilities = caps or {}
    sess = MagicMock()
    sess.session_id = sid
    sess.last_prompt_stats = AcpPromptStats()
    sess._last_stop_reason = ""
    conn.new_session = AsyncMock(return_value=sess)
    conn.load_session = AsyncMock(return_value=None)
    conn.last_session_new_snapshot = snapshot or {"sessionId": sid, "modes": {}}
    conn.send_request = AsyncMock(return_value=(1, MagicMock()))
    conn.drain_init_notifications = AsyncMock()
    conn.close = AsyncMock()
    return conn, sess


class TestInitializeSession:
    @pytest.mark.asyncio
    async def test_new_session_basic(self, tmp_path):
        client = AcpClient(work_dir=tmp_path, model="auto")
        conn, sess = _fake_conn()
        client._connection = conn
        await client._initialize_session()
        assert client._session is sess
        assert client._session_id == "sess-1"
        conn.new_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snapshot_retained(self, tmp_path):
        client = AcpClient(work_dir=tmp_path)
        conn, _ = _fake_conn(snapshot={"sessionId": "s", "models": [{"id": "m1"}]})
        client._connection = conn
        await client._initialize_session()
        assert client.session_snapshot.get("models") == [{"id": "m1"}]

    @pytest.mark.asyncio
    async def test_activate_agent_and_model_ordered(self, tmp_path):
        # set_model must be issued AFTER activate-agent; both via conn.send_request.
        client = AcpClient(work_dir=tmp_path, agent="ops", model="gpt-x")
        conn, _ = _fake_conn()
        client._connection = conn
        await client._initialize_session()
        methods = [c.args[0] for c in conn.send_request.call_args_list]
        assert METHOD_SET_MODE in methods and METHOD_SET_MODEL in methods
        assert methods.index(METHOD_SET_MODE) < methods.index(METHOD_SET_MODEL)

    @pytest.mark.asyncio
    async def test_load_session_resumes_when_available(self, tmp_path):
        # A resume id + loadSession capability + an existing session file → session/load
        # path; when it returns a session, we mark resumed and skip session/new.
        client = AcpClient(work_dir=tmp_path, session_files_dir=tmp_path)
        client.set_resume_session_id("old-sid")
        (tmp_path / "old-sid.json").write_text("{}")
        conn, _ = _fake_conn(caps={"loadSession": True})
        resumed_sess = MagicMock()
        resumed_sess.session_id = "old-sid"
        resumed_sess.last_prompt_stats = AcpPromptStats()
        resumed_sess._last_stop_reason = ""
        conn.load_session = AsyncMock(return_value=resumed_sess)
        client._connection = conn
        await client._initialize_session()
        assert client._resumed is True
        assert client._session_id == "old-sid"
        conn.new_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_new_when_load_returns_none(self, tmp_path):
        client = AcpClient(work_dir=tmp_path, session_files_dir=tmp_path)
        client.set_resume_session_id("old-sid")
        (tmp_path / "old-sid.json").write_text("{}")
        conn, sess = _fake_conn(caps={"loadSession": True})
        conn.load_session = AsyncMock(return_value=None)  # load didn't take
        client._connection = conn
        await client._initialize_session()
        assert client._resumed is False
        conn.new_session.assert_awaited_once()


# ── turn/lifecycle delegation to the held session ───────────────────────────────
def _client_with_session(events):
    """A client whose ensure_ready is stubbed and whose session yields *events*."""
    client = AcpClient()
    client.ensure_ready = AsyncMock()
    sess = MagicMock()
    sess.last_prompt_stats = AcpPromptStats(event_count=3, tool_calls=[("read", "x")])
    sess._last_stop_reason = "end_turn"

    async def _stream(msg, timeout=0.0):
        for e in events:
            yield e

    sess.stream_events = _stream
    sess.stream_command = _stream
    sess.approve_tool = AsyncMock()
    sess.reject_tool = AsyncMock()
    sess.cancel = AsyncMock()
    sess.wait_turn_done = AsyncMock(return_value="end_turn")
    sess.has_active_turn = MagicMock(return_value=True)
    client._session = sess
    client._session_id = "s"
    return client, sess


class TestTurnDelegation:
    @pytest.mark.asyncio
    async def test_stream_events_delegates_and_stamps_telemetry(self):
        client, sess = _client_with_session(
            [
                AcpEvent(kind=EVENT_TEXT_CHUNK, text="hi"),
                AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
        events = [e async for e in client.stream_events("go")]
        assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
        # telemetry stamped from the session's stats onto the terminal event
        assert events[-1].event_count == 3
        assert events[-1].tool_call_count == 1

    @pytest.mark.asyncio
    async def test_send_message_concatenates_text_excludes_thinking(self):
        from personalclaw.acp.types import EVENT_THINKING_CHUNK

        client, _ = _client_with_session(
            [
                AcpEvent(kind=EVENT_THINKING_CHUNK, text="hmm"),
                AcpEvent(kind=EVENT_TEXT_CHUNK, text="Hello, "),
                AcpEvent(kind=EVENT_TEXT_CHUNK, text="world!"),
                AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
        result = await client.send_message("hi")
        assert result == "Hello, world!"  # thinking excluded

    @pytest.mark.asyncio
    async def test_approve_reject_delegate(self):
        client, sess = _client_with_session([])
        await client.approve_tool("r1")
        sess.approve_tool.assert_awaited_once_with("r1", None)
        await client.reject_tool("r1")
        sess.reject_tool.assert_awaited_once_with("r1")

    @pytest.mark.asyncio
    async def test_cancel_and_wait_and_active_delegate(self):
        client, sess = _client_with_session([])
        await client.cancel_session()
        sess.cancel.assert_awaited_once()
        assert await client.wait_turn_done(1.0) == "end_turn"
        assert client.has_active_turn() is True

    @pytest.mark.asyncio
    async def test_approve_before_session_raises(self):
        client = AcpClient()  # no session
        with pytest.raises(AcpError):
            await client.approve_tool("r1")


# ── set_* live reconfig issues session-scoped dialect requests on the connection ─
class TestLiveReconfig:
    @pytest.mark.asyncio
    async def test_set_model_sends_and_records(self):
        client = AcpClient(model="auto")
        client._session_id = "s"
        conn = MagicMock()
        conn.send_request = AsyncMock(return_value=(1, MagicMock()))
        conn._dialect = client._dialect
        client._connection = conn
        await client.set_model("new-model")
        assert client._model == "new-model"
        assert any(c.args[0] == METHOD_SET_MODEL for c in conn.send_request.call_args_list)

    @pytest.mark.asyncio
    async def test_set_model_before_session_raises(self):
        client = AcpClient()
        with pytest.raises(AcpError):
            await client.set_model("m")
