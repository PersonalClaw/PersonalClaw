"""AAP-6 §2.3 — unattended threading (gap 3) + the runtime-agnostic loop breaker (gap 5).

Two mechanisms, one tension. AAP-5 made the host the permission authority, so
``bypassPermissions`` is clamped to ``default`` at every door. §2.3 needs the exact
opposite for a genuinely unattended run — and only for one. So every widening test
here is paired with the INTERACTIVE floor that must stay clamped: that pairing is the
point, because the way this atom could quietly undo AAP-5 is to widen both.

The breaker half is driven the same way AAP-5's gate tests are: ``_run_chat`` over a
synthetic ACP event stream, not a predicate call. Phase 1's `G6` measured six
consecutive ACP tool failures producing no warn, no block and no circuit trip, so a
unit call on the counter would have passed *before* this change too and proved
nothing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.client import AcpClient
from personalclaw.acp.permission_authority import HOST_AUTHORITY_MODE
from personalclaw.acp.translate import extract_tool_update_events
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.dashboard.chat_runner import _run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.guardrails.loop_breaker import (
    BLOCK_THRESHOLD,
    CIRCUIT_THRESHOLD,
    WARN_THRESHOLD,
)
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.acp_session_provider import AcpSessionProvider
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)

# ── door 1: AcpClient — the chokepoint every mode path crosses ────────────────


class TestAcpClientModeDoor:
    """The widening and its floor, on the same door, in the same class."""

    def test_interactive_session_still_clamps_bypass(self):
        """THE regression that matters: AAP-5's clamp must survive AAP-6.

        An ordinary interactive session passes no ``unattended``, so the default has
        to be the safe one. If this ever goes green-to-red, the unattended plumbing
        has been wired unconditionally and every chat session just became its own
        permission authority.
        """
        c = AcpClient(mode="bypassPermissions", command=["true"])
        assert c._mode == HOST_AUTHORITY_MODE
        assert c._unattended is False

    def test_interactive_clamps_every_auto_approve_spelling(self):
        for spelling in ("acceptEdits", "accept-edits", "dontAsk", "yolo", "bypassPermissions"):
            assert AcpClient(mode=spelling, command=["true"])._mode == HOST_AUTHORITY_MODE

    def test_unattended_session_keeps_bypass(self):
        """§2.3's explicit path — and the reason gap 3 was a gap."""
        c = AcpClient(mode="bypassPermissions", command=["true"], unattended=True)
        assert c._mode == "bypassPermissions"
        assert c._unattended is True

    def test_unattended_does_not_widen_an_unknown_mode(self):
        """Unattended is not a blanket pass: an unrecognized mode is still clamped.

        Only modes we have positively classified as auto-approve are honoured. A
        never-seen adapter mode could mean anything, so the unattended path inherits
        sanitize_mode's fail-closed default rather than becoming an escape hatch.
        """
        c = AcpClient(mode="someModeNobodyHasSeen", command=["true"], unattended=True)
        assert c._mode == HOST_AUTHORITY_MODE

    def test_plan_mode_unaffected_either_way(self):
        assert AcpClient(mode="plan", command=["true"])._mode == "plan"
        assert AcpClient(mode="plan", command=["true"], unattended=True)._mode == "plan"


# ── door 2: the POOLED path (a warmed connection, specialized on claim) ──────


class _FakeConn:
    def __init__(self):
        self._dialect = MagicMock()
        self._dialect.set_mode_request = MagicMock(side_effect=lambda **kw: kw)
        self.sent: list = []

    def is_process_alive(self):
        return True


class _FakeSession:
    session_id = "S1"


class TestPooledModeDoor:
    """A warmed pool connection is ATTENDED by default, so an unattended claim must
    declare itself. Both directions asserted, because the ordering (set_unattended
    before set_mode) is load-bearing and silent when wrong."""

    def _mk(self, unattended=False):
        conn = _FakeConn()
        p = AcpSessionProvider(
            conn, _FakeSession(), runtime_id="acp:demo-cli", unattended=unattended
        )
        p._send_dialect_request = AsyncMock()
        return p, conn

    @pytest.mark.asyncio
    async def test_pooled_interactive_still_clamps(self):
        p, conn = self._mk(unattended=False)
        with patch("personalclaw.sel.sel", MagicMock()):
            await p.set_mode("bypassPermissions")
        assert conn._dialect.set_mode_request.call_args.kwargs["mode"] == HOST_AUTHORITY_MODE

    @pytest.mark.asyncio
    async def test_pooled_unattended_forwards_bypass(self):
        p, conn = self._mk(unattended=True)
        await p.set_mode("bypassPermissions")
        assert conn._dialect.set_mode_request.call_args.kwargs["mode"] == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_set_unattended_before_set_mode_is_what_lets_it_through(self):
        """The claim path calls set_unattended() then set_mode(). Prove the sequence
        matters: the same set_mode call clamps or forwards purely on that flag."""
        p, conn = self._mk(unattended=False)
        p.set_unattended(True)
        await p.set_mode("bypassPermissions")
        assert conn._dialect.set_mode_request.call_args.kwargs["mode"] == "bypassPermissions"


# ── the bridge kwarg: unattended must STOP being popped for ACP ──────────────


class TestBridgeThreadsUnattendedToAcp:
    """Plan §"Bridge kwargs": ``unattended`` stops being popped for ACP. Asserted on
    the acp_agent factory — the only consumer on that branch — because that is where
    a pop would show up as a missing kwarg."""

    def _entry(self):
        from personalclaw.llm.registry import ProviderEntry

        return ProviderEntry(
            name="acp:demo-cli",
            type="acp_agent",
            model="",
            options={"command": ["true"]},
        )

    def test_factory_honours_unattended_true(self):
        from personalclaw.llm.acp_agent import _factory

        p = _factory(entry=self._entry(), acp_mode="bypassPermissions", unattended=True)
        assert p._unattended is True
        assert p._client._mode == "bypassPermissions"

    def test_factory_without_the_kwarg_stays_clamped(self):
        """The pre-AAP-6 behaviour, kept as the floor: no flag → AAP-5's clamp."""
        from personalclaw.llm.acp_agent import _factory

        p = _factory(entry=self._entry(), acp_mode="bypassPermissions")
        assert p._unattended is False
        assert p._client._mode == HOST_AUTHORITY_MODE

    def test_bridge_injects_for_acp_and_not_for_native(self):
        """The bridge pops ``unattended`` (the model-axis resolvers must never see it)
        and re-injects it ONLY on the ACP branch."""
        import personalclaw.providers.provider_bridge as pb

        seen: dict = {}

        def _fake_registry_resolve(use_case, **kwargs):
            seen.update(kwargs)
            return MagicMock()

        with patch.object(pb, "_resolve_from_config_registry", _fake_registry_resolve):
            pb.resolve_provider_for_use_case(
                "chat",
                session_key="cron:x",
                agent="a",
                model_override="Prov/m",
                provider_kind="acp:demo-cli",
                unattended=True,
            )
        assert seen.get("unattended") is True

        seen.clear()
        with patch.object(pb, "_resolve_from_config_registry", _fake_registry_resolve):
            with patch.object(pb, "_build_native_runtime", lambda **kw: seen.update(kw)):
                pb.resolve_provider_for_use_case(
                    "chat",
                    session_key="chat-1",
                    agent="a",
                    provider_kind="native",
                    unattended=True,
                )
        # The native branch takes it as an EXPLICIT argument, never via **kwargs — so
        # it is present as a real parameter and absent from the forwarded kwargs.
        assert seen.get("unattended") is True


# ── translate: the failure bit the breaker needs (was dropped entirely) ──────


class TestAcpFailureSignalReachesTheHost:
    """`G6`'s root cause: `completed` and `failed` produced an IDENTICAL event, so no
    consumer could see a failure. Without this the breaker cannot count."""

    def _msg(self, status):
        return JsonRpcMessage(
            method="session/update",
            params={
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "t1",
                    "status": status,
                    "content": [{"type": "content", "content": {"type": "text", "text": "boom"}}],
                }
            },
        )

    def test_failed_marks_ok_false(self):
        events = extract_tool_update_events(self._msg("failed"), {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results and results[0].tool_meta.get("ok") is False

    def test_completed_leaves_meta_empty(self):
        """Absent on success — matching the native tool_meta contract, so no existing
        reader changes behaviour on a passing call."""
        events = extract_tool_update_events(self._msg("completed"), {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results and "ok" not in results[0].tool_meta


# ── the _run_chat harness (same shape as AAP-5's) ────────────────────────────


async def _async_iter(items):
    for item in items:
        yield item


def _make_state(tmp_path):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "acp:kiro-cli"
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


def _session(key="chat-1-aap6", *, trust=False):
    s = _ChatSession(key)
    s._trust = trust
    s.acp_provider = "acp:kiro-cli"
    return s


def _set_stream(client, events):
    client.stream = MagicMock(side_effect=lambda *a, **kw: _async_iter(events))


def _texts(session):
    return [str(m.get("content", "")) for m in session.messages]


async def _drive(state, session):
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _run_chat(state, session, "hello")


def _fail_cycle(n):
    """n identical failing tool calls (same tool, same args) as an event stream."""
    out: list = []
    for i in range(n):
        out.append(
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"t{i}",
                title="bash",
                tool_input='{"command": "exit 1"}',
            )
        )
        out.append(
            LLMEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=f"t{i}",
                tool_output="exit status 1",
                tool_meta={"ok": False},
            )
        )
    out.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    return out


# ── gap 3: which sessions count as unattended, and what that changes ─────────


class TestUnattendedClassification:
    """How an unattended session is DISTINGUISHED from an interactive one: the
    canonical by-construction classifier (``is_unattended_session``) OR the loop
    manager's explicit ``session._unattended``. Never a heuristic on content."""

    @pytest.mark.asyncio
    async def test_interactive_session_is_attended_and_unmoded(self, tmp_path):
        """The floor. A chat session gets unattended=False and NO forwarded mode, so
        AAP-5's clamp is what the CLI sees."""
        state, client = _make_state(tmp_path)
        _set_stream(client, [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
        await _drive(state, _session("chat-1-aap6"))
        kw = state.sessions.get_or_create.call_args.kwargs
        assert kw["unattended"] is False
        assert kw["acp_mode"] is None

    @pytest.mark.asyncio
    async def test_cron_session_is_unattended_and_gets_bypass(self, tmp_path):
        """§2.3's "unify so cron/scheduled runs get it too, not just loops". Before
        this, only loop/manager.py set the mode, so a cron ACP turn ran in the
        prompting mode AND parked on a human who was asleep."""
        state, client = _make_state(tmp_path)
        _set_stream(client, [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
        await _drive(state, _session("cron:nightly"))
        kw = state.sessions.get_or_create.call_args.kwargs
        assert kw["unattended"] is True
        assert kw["acp_mode"] == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_loop_worker_session_still_works_the_old_way(self, tmp_path):
        """The loop manager's explicit flag keeps working — the new classifier is an
        addition, not a replacement."""
        state, client = _make_state(tmp_path)
        _set_stream(client, [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
        s = _session("some-worker-key")
        s._unattended = True
        await _drive(state, s)
        assert state.sessions.get_or_create.call_args.kwargs["unattended"] is True

    @pytest.mark.asyncio
    async def test_explicit_session_mode_is_not_overridden(self, tmp_path):
        """An explicitly-set session mode wins; the default only fills a blank."""
        state, client = _make_state(tmp_path)
        _set_stream(client, [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
        s = _session("cron:nightly")
        s.acp_mode = "plan"
        await _drive(state, s)
        assert state.sessions.get_or_create.call_args.kwargs["acp_mode"] == "plan"


class TestUnattendedFailFast:
    """The half that actually prevents the wedge — and the half kiro gets on its own,
    having no permission-mode axis at all."""

    @pytest.mark.asyncio
    async def test_unattended_permission_request_is_denied_immediately(self, tmp_path):
        """An interactive card waits up to two hours. On an unattended turn nobody
        will ever answer, so that is not a gate — it is a stall that ends in a
        rejection anyway. The timeout here IS the assertion: pre-fix this parks."""
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id="req-1",
                    title="Write file",
                    tool_input='{"path": "/tmp/x"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        session = _session("cron:nightly")
        await asyncio.wait_for(_drive(state, session), timeout=10)
        client.reject_tool.assert_awaited_with("req-1")
        assert any("auto-denied" in t for t in _texts(session))

    @pytest.mark.asyncio
    async def test_interactive_permission_request_still_parks_for_a_human(self, tmp_path):
        """The floor for the fail-fast: it must NOT leak into an attended session.
        Here the card is rendered and the turn waits until answered."""
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id="req-1",
                    title="Write file",
                    tool_input='{"path": "/tmp/x"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        session = _session("chat-1-aap6")

        async def _answer():
            for _ in range(400):
                fut = session._approval_futures.get("req-1")
                if fut and not fut.done():
                    fut.set_result("approved")
                    return True
                await asyncio.sleep(0.01)
            return False

        task = asyncio.get_event_loop().create_task(_answer())
        await asyncio.wait_for(_drive(state, session), timeout=15)
        assert await task, "the turn never rendered an approval card to answer"
        client.approve_tool.assert_awaited_with("req-1")
        assert not any("auto-denied" in t for t in _texts(session))


# ── gap 5: the breaker, driven over a real failing-tool ACP stream ───────────


class TestAcpLoopBreaker:
    """`G6` measured that six consecutive failures produced nothing. Each rung is
    driven through _run_chat, and the vacuity floor (a passing stream produces no
    breaker text at all) is asserted so a rail that matches everything can't read as
    a pass."""

    @pytest.mark.asyncio
    async def test_warn_rung_fires_at_the_threshold(self, tmp_path):
        state, client = _make_state(tmp_path)
        _set_stream(client, _fail_cycle(WARN_THRESHOLD))
        session = _session()
        await _drive(state, session)
        # Assert the FAILURE path's own wording, not the shared "change approach"
        # tail: the structural (no-progress) note ends the same way, so matching on
        # that alone would also pass if the failure signal never arrived and three
        # identical SUCCESSES tripped the structural detector instead.
        assert any("this is failure #" in t for t in _texts(session))

    @pytest.mark.asyncio
    async def test_block_rung_names_the_repeated_failure(self, tmp_path):
        state, client = _make_state(tmp_path)
        _set_stream(client, _fail_cycle(BLOCK_THRESHOLD))
        session = _session()
        await _drive(state, session)
        assert any("was blocked" in t for t in _texts(session))

    @pytest.mark.asyncio
    async def test_circuit_trips_and_aborts_the_turn(self, tmp_path):
        """done_when clause 2: a deliberately failing-tool ACP session trips the
        circuit and aborts the turn with the standard breaker message."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _fail_cycle(CIRCUIT_THRESHOLD + 2))
        session = _session()
        await _drive(state, session)
        texts = _texts(session)
        assert any("Run aborted by the loop breaker" in t for t in texts), texts[-3:]
        # The turn is aborted by cancelling the CLI's turn, not by abandoning the
        # stream — so every post-loop finalizer still runs.
        client.cancel_session.assert_awaited()

    @pytest.mark.asyncio
    async def test_circuit_announced_once_not_per_result(self, tmp_path):
        state, client = _make_state(tmp_path)
        _set_stream(client, _fail_cycle(CIRCUIT_THRESHOLD + 8))
        session = _session()
        await _drive(state, session)
        hits = [t for t in _texts(session) if "Run aborted by the loop breaker" in t]
        assert len(hits) == 1, hits

    @pytest.mark.asyncio
    async def test_passing_stream_produces_no_breaker_text(self, tmp_path):
        """Vacuity floor. Without this, a breaker that fired on everything would pass
        every test above."""
        state, client = _make_state(tmp_path)
        events: list = []
        for i in range(CIRCUIT_THRESHOLD + 2):
            events.append(
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"t{i}",
                    title="bash",
                    tool_input='{"command": "echo %d"}' % i,
                )
            )
            events.append(
                LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id=f"t{i}", tool_output=f"out-{i}")
            )
        events.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
        _set_stream(client, events)
        session = _session()
        await _drive(state, session)
        texts = " ".join(_texts(session))
        assert "was blocked" not in texts
        assert "Run aborted by the loop breaker" not in texts
        client.cancel_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_distinct_arguments_are_not_one_bucket(self, tmp_path):
        """params-awareness: a tool failing on DIFFERENT inputs is not the same loop,
        so the per-key BLOCK rung must not fire. Keyed off the streamed arguments,
        which for ACP arrive in the tool_call/tool_call_update frames."""
        state, client = _make_state(tmp_path)
        events: list = []
        for i in range(BLOCK_THRESHOLD + 2):
            events.append(
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"t{i}",
                    title="bash",
                    tool_input='{"command": "distinct-%d"}' % i,
                )
            )
            events.append(
                LLMEvent(
                    kind=EVENT_TOOL_RESULT,
                    tool_call_id=f"t{i}",
                    tool_output="nope",
                    tool_meta={"ok": False},
                )
            )
        events.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
        _set_stream(client, events)
        session = _session()
        await _drive(state, session)
        assert not any("was blocked" in t for t in _texts(session))

    @pytest.mark.asyncio
    async def test_a_success_clears_the_streak(self, tmp_path):
        state, client = _make_state(tmp_path)
        events: list = []
        for i in range(BLOCK_THRESHOLD + 4):
            events.append(
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"t{i}",
                    title="bash",
                    tool_input='{"command": "flip"}',
                )
            )
            _ok = (i % 2) == 1  # every other call succeeds → streak never reaches 5
            events.append(
                LLMEvent(
                    kind=EVENT_TOOL_RESULT,
                    tool_call_id=f"t{i}",
                    tool_output=f"r-{i}",
                    tool_meta={} if _ok else {"ok": False},
                )
            )
        events.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
        _set_stream(client, events)
        session = _session()
        await _drive(state, session)
        assert not any("was blocked" in t for t in _texts(session))
