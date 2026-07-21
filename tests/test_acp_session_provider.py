"""Tests for AcpSessionProvider — the concurrent-path provider wrapping an AcpSession.
Driven against fakes (no real process); asserts it exposes the AgentProvider surface and
translates AcpEvents → neutral events via the shared adapter, identical to the client path."""

from __future__ import annotations

import pytest

from personalclaw.acp.types import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AcpEvent,
    AcpPromptStats,
)
from personalclaw.llm.acp_session_provider import AcpSessionProvider


class _FakeSession:
    def __init__(self, session_id="S1"):
        self.session_id = session_id
        self.last_prompt_stats = AcpPromptStats()
        self.last_prompt_stats.event_count = 3
        self.last_prompt_stats.tool_calls = [("read", "Read"), ("bash", "Bash")]
        self._active = False
        self.approved: list = []
        self.rejected: list = []
        self.cancelled = False

    async def stream_events(self, message):
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="hello")
        yield AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    async def stream_command(self, command):
        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text="cmd-out")
        yield AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    async def approve_tool(self, rid, option_id=None):
        self.approved.append(rid)

    async def reject_tool(self, rid):
        self.rejected.append(rid)

    def has_active_turn(self):
        return self._active

    async def cancel(self):
        self.cancelled = True

    async def wait_turn_done(self, timeout):
        return "cancelled"

    def context_usage_pct(self):
        return 42.0


class _FakeConn:
    def __init__(self, alive=True):
        self._alive = alive
        self._transport = type("T", (), {"pid": 4242})()
        self.agent_capabilities = {"loadSession": True, "promptCapabilities": {"image": True}}
        self.closed_sessions: list = []

    def is_process_alive(self):
        return self._alive

    async def close_session(self, sid):
        self.closed_sessions.append(sid)


def _mk():
    conn = _FakeConn()
    sess = _FakeSession()
    p = AcpSessionProvider(conn, sess, runtime_id="acp:demo-cli", model="opus", agent_name="Helper")
    return p, conn, sess


@pytest.mark.asyncio
async def test_stream_translates_events_and_stamps_telemetry():
    p, _conn, _sess = _mk()
    events = [e async for e in p.stream("hi")]
    assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == "hello"
    # telemetry stamped on the terminal event from the session's stats
    assert events[-1].event_count == 3
    assert events[-1].tool_call_count == 2


@pytest.mark.asyncio
async def test_stream_command_translates():
    p, _conn, _sess = _mk()
    events = [e async for e in p.stream_command("/usage")]
    assert events[0].text == "cmd-out"
    assert events[-1].kind == EVENT_COMPLETE


def test_identity_surface():
    p, _conn, sess = _mk()
    assert p.provider_id == "acp:demo-cli"
    assert p.session_id == "S1"
    assert p.agent_model == "opus"
    assert p.agent_name == "Helper"
    assert p.pid == 4242
    # declared_capabilities surfaces the connection's handshake caps by name
    assert "loadSession" in p.declared_capabilities
    assert "promptCapabilities" in p.declared_capabilities


@pytest.mark.asyncio
async def test_permissions_delegate_to_session():
    p, _conn, sess = _mk()
    await p.approve_tool(7)
    await p.reject_tool(9)
    assert sess.approved == [7] and sess.rejected == [9]


def test_context_usage_and_liveness():
    p, conn, _sess = _mk()
    assert p.context_usage_pct() == 42.0
    assert p.is_alive() is True
    conn._alive = False
    assert p.is_alive() is False


@pytest.mark.asyncio
async def test_shutdown_closes_only_this_session():
    p, conn, sess = _mk()
    await p.shutdown()
    assert conn.closed_sessions == ["S1"]  # closes THIS session, not the shared connection


@pytest.mark.asyncio
async def test_cancel_no_active_turn():
    p, _conn, sess = _mk()
    sess._active = False
    assert await p.cancel() == "no_turn"


@pytest.mark.asyncio
async def test_cancel_acked_fire_and_forget():
    p, _conn, sess = _mk()
    sess._active = True
    assert await p.cancel() == "acked"  # wait_ack_timeout=0 → optimistic acked
    assert sess.cancelled is True


# ── double-gate + opener ─────────────────────────────────────────────────────


def test_gate_off_when_flag_off_even_for_concurrent_dialect(monkeypatch):
    # the default dialect IS concurrent-capable, but with the runtime flag OFF the gate
    # is OFF (the one-session client path stays authoritative). Monkeypatch the flag off
    # so this doesn't depend on the machine's real config.json.
    import personalclaw.config as config_mod
    from personalclaw.llm.acp_session_provider import concurrent_sessions_enabled

    class _Cfg:
        agent = type("A", (), {"acp_concurrent_sessions": False})()

    monkeypatch.setattr(config_mod.AppConfig, "load", staticmethod(lambda: _Cfg()))
    assert concurrent_sessions_enabled("default") is False


def test_gate_off_for_non_concurrent_dialect(monkeypatch):
    import personalclaw.config as config_mod
    from personalclaw.llm.acp_session_provider import concurrent_sessions_enabled

    # claude-code/codex adapters aren't proven-concurrent → OFF even with the flag ON.
    class _Cfg:
        agent = type("A", (), {"acp_concurrent_sessions": True})()

    monkeypatch.setattr(config_mod.AppConfig, "load", staticmethod(lambda: _Cfg()))
    assert concurrent_sessions_enabled("claude-code") is False


def test_gate_on_only_when_both_true(monkeypatch):
    import personalclaw.config as config_mod
    from personalclaw.llm.acp_session_provider import concurrent_sessions_enabled

    class _Cfg:
        agent = type("A", (), {"acp_concurrent_sessions": True})()

    # The gate reads config via `from personalclaw.config import AppConfig` at call time,
    # so patch the class's load on the source module.
    monkeypatch.setattr(config_mod.AppConfig, "load", staticmethod(lambda: _Cfg()))
    assert concurrent_sessions_enabled("default") is True  # capable dialect + flag on
    assert concurrent_sessions_enabled("claude-code") is False  # flag on but dialect not capable


@pytest.mark.asyncio
async def test_set_model_is_session_scoped():
    # Live set_* must build a SESSION-SCOPED dialect request (carries this session's id)
    # and send it via the connection — so it affects only THIS co-tenant session.
    from personalclaw.acp.dialect import DefaultDialect

    sent: list = []

    class _Conn:
        _dialect = DefaultDialect()
        agent_capabilities = {}
        _transport = type("T", (), {"pid": 1})()

        def is_process_alive(self):
            return True

        async def send_request(self, method, params):
            sent.append((method, params))
            return 1, None

    sess = _FakeSession("SID9")
    p = AcpSessionProvider(_Conn(), sess, runtime_id="acp:demo-cli")
    await p.set_model("opus")
    assert sent and sent[-1][1]["sessionId"] == "SID9"
    assert sent[-1][1]["modelId"] == "opus"
    assert p.agent_model == "opus"


@pytest.mark.asyncio
async def test_open_session_provider_opens_and_wraps():
    opened: dict = {}

    class _Conn:
        agent_capabilities = {}
        _transport = type("T", (), {"pid": 1})()

        async def new_session(self, params, *, session_files_dir=None):
            opened["params"] = params
            opened["sfd"] = session_files_dir
            return _FakeSession("NEW")

        def is_process_alive(self):
            return True

    from personalclaw.llm.acp_session_provider import open_acp_session_provider

    p = await open_acp_session_provider(
        _Conn(), runtime_id="acp:demo-cli", cwd="/tmp/ws", model="opus", agent_name="Helper"
    )
    assert p.session_id == "NEW"
    assert opened["params"]["cwd"] == "/tmp/ws" and opened["params"]["mcpServers"] == []
