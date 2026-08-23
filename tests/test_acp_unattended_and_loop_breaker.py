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
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
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
    params_key,
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
        events = extract_tool_update_events(self._msg("failed"), {}, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results and results[0].tool_meta.get("ok") is False

    def test_completed_leaves_meta_empty(self):
        """Absent on success — matching the native tool_meta contract, so no existing
        reader changes behaviour on a passing call."""
        events = extract_tool_update_events(self._msg("completed"), {}, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results and "ok" not in results[0].tool_meta


# ── the same bit, DERIVED so it does not depend on the runtime's word (`G151`) ──


def _terminal_frame(update: dict) -> JsonRpcMessage:
    return JsonRpcMessage(
        method="session/update",
        params={"update": {"sessionUpdate": "tool_call_update", "toolCallId": "t1", **update}},
    )


def _ok_bit(update: dict):
    """The ``ok`` value the host ends up with for a terminal frame — via the real
    decoder, not the helper, so these are CALL-SITE assertions."""
    events = extract_tool_update_events(_terminal_frame(update), {}, {})
    results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
    assert results, f"no tool result decoded from {update!r}"
    return results[0].tool_meta.get("ok", "ABSENT")


# Byte-copies of the frames the two CLIs actually sent for the SAME command,
# `bash -c 'echo boom >&2; exit 3'`, captured live on 2026-08-23. The whole point of
# the atom is that these two disagree, so paraphrasing them would test the paraphrase.
KIRO_FAILED_FRAME = {
    "kind": "execute",
    "status": "completed",
    "title": "Running: bash -c 'echo boom >&2; exit 3'",
    "rawInput": {
        "__tool_use_purpose": "Run the requested command for the first time.",
        "command": "bash -c 'echo boom >&2; exit 3'",
    },
    "rawOutput": {
        "items": [{"Json": {"exit_status": "exit status: 3", "stdout": "", "stderr": "boom\n"}}]
    },
}
KIRO_PASSED_FRAME = {
    "kind": "execute",
    "status": "completed",
    "title": "Running: bash -c 'echo hello; exit 0'",
    "rawInput": {"command": "bash -c 'echo hello; exit 0'"},
    "rawOutput": {
        "items": [{"Json": {"exit_status": "exit status: 0", "stdout": "hello\n", "stderr": ""}}]
    },
}
CODEX_FAILED_FRAME = {
    "status": "failed",
    "rawOutput": {"formatted_output": "boom\n", "exit_code": 3},
    "_meta": {"terminal_exit": {"exit_code": 3, "signal": None, "terminal_id": "t1"}},
}


class TestFailureBitIsRuntimeAgnostic:
    """`G151`. Reading the bit off ``status`` alone made it a per-CLI lottery: kiro
    calls a non-zero-exit command a ``completed`` tool call, so every kiro failure was
    signed ``success`` and the entire warn/block/circuit path was inert on it while
    passing on codex. The host derives the bit now.

    The vacuity floor is the pair of NEGATIVE cases below: a rule that answered
    "failed" to everything would satisfy every positive case here, and it is exactly
    the failure mode that matters — a breaker that aborts a healthy turn is worse than
    one that misses a failure.
    """

    def test_kiro_completed_but_nonzero_exit_is_a_failure(self):
        """The measured defect, at the decoder. Was ``ABSENT`` before this change."""
        assert _ok_bit(KIRO_FAILED_FRAME) is False

    def test_codex_declared_failure_is_unchanged(self):
        """A runtime that signs its own failures is still decided by its own word."""
        assert _ok_bit(CODEX_FAILED_FRAME) is False

    def test_kiro_zero_exit_is_still_a_success(self):
        """Vacuity floor 1 — the SAME shape, the same keys, exit 0."""
        assert _ok_bit(KIRO_PASSED_FRAME) == "ABSENT"

    def test_the_command_text_is_never_what_decides(self):
        """Vacuity floor 2, and the reason the scan is key-based and skips ``rawInput``.
        kiro's own input for the failing call is ``{"command": "... exit 3"}``. A prose
        scan — or a scan of the whole frame — would sign EVERY invocation of that
        command failed no matter how it exited, which is the same class of bug as
        reading the status field: a bit that is not about this call's outcome."""
        frame = {
            "status": "completed",
            "rawInput": {"command": "bash -c 'echo boom >&2; exit 3'"},
            "rawOutput": {"items": [{"Json": {"exit_status": "exit status: 0", "stdout": ""}}]},
        }
        assert _ok_bit(frame) == "ABSENT"

    def test_output_prose_mentioning_failure_is_not_a_failure(self):
        """Vacuity floor 3. ``grep -c error`` and a test runner printing "1 failed" are
        SUCCESSFUL tool calls."""
        frame = {
            "status": "completed",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "3 failed, exit 1, ERROR"}}
            ],
        }
        assert _ok_bit(frame) == "ABSENT"

    def test_mcp_error_flag_is_a_failure(self):
        """A tool the CLI serves over MCP declares failure with ``isError``, not an
        exit status — the shape an app-provided tool reaches an ACP session in."""
        frame = {
            "status": "completed",
            "content": [
                {"type": "content", "isError": True, "content": {"type": "text", "text": "no"}}
            ],
        }
        assert _ok_bit(frame) is False

    def test_a_frame_declaring_nothing_stays_a_success(self):
        """Vacuity floor 4: no exit status anywhere → absent, not failed. Most tool
        results (a file read, a search) carry no exit status at all."""
        assert _ok_bit({"status": "completed", "rawOutput": {"content": "hello"}}) == "ABSENT"

    def test_a_true_flag_is_not_read_as_exit_one(self):
        """``True == 1`` in Python, so a boolean under an exit-status key would read as
        "exited 1" if the type were not checked first."""
        assert _ok_bit({"status": "completed", "rawOutput": {"exit_code": True}}) == "ABSENT"

    def test_deeply_buried_status_is_bounded_not_infinite(self):
        """kiro buries the status two levels down, so the walk must descend — but it is
        depth-bounded, so one pathological payload cannot become a traversal."""
        deep: dict = {"exit_code": 3}
        for _ in range(20):
            deep = {"nested": deep}
        assert _ok_bit({"status": "completed", "rawOutput": deep}) == "ABSENT"
        shallow: dict = {"a": {"b": {"exit_code": 3}}}
        assert _ok_bit({"status": "completed", "rawOutput": shallow}) is False


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


def _decoded_result(frame: dict, call_id: str) -> LLMEvent:
    """A tool RESULT event produced by the real ACP pipeline from a real CLI frame.

    ``extract_tool_update_events`` then ``acp_event_to_agent_event`` — the two hops the
    live stream takes. Built this way on purpose: ``_fail_cycle`` below hands
    ``chat_runner`` a hand-written ``tool_meta={"ok": False}``, which tests the counter
    but would keep passing if no runtime on earth ever produced that bit. `G6` shipped
    exactly that way. These events carry only what the CLI actually said.
    """
    msg = JsonRpcMessage(
        method="session/update",
        params={"update": {"sessionUpdate": "tool_call_update", "toolCallId": call_id, **frame}},
    )
    results = [e for e in extract_tool_update_events(msg, {}, {}) if e.kind == EVENT_TOOL_RESULT]
    assert results, f"the decoder produced no tool result for {frame!r}"
    return acp_event_to_agent_event(results[0])


def _real_frame_cycle(n, frame):
    """n identical tool calls whose RESULTS are decoded from a real CLI frame."""
    out: list = []
    for i in range(n):
        out.append(
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"t{i}",
                title="bash",
                tool_input='{"command": "bash -c \'echo boom >&2; exit 3\'"}',
            )
        )
        out.append(_decoded_result(frame, f"t{i}"))
    out.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    return out


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


# ── the CALL SITE: a real CLI's frames, through the real decoder, into the breaker ──


class TestBreakerFiresOnRealRuntimeFrames:
    """The rail the earlier work did not have, and the reason it shipped inert.

    Every test above hands ``chat_runner`` a hand-written ``tool_meta={"ok": False}``.
    That proves the counter counts; it cannot notice that no runtime on earth produced
    the bit. These drive the SAME breaker from the literal frames the two CLIs sent for
    the same failing command, decoded by the real translate + adapter hops — so the
    chain the live system uses is what is under test, not a re-statement of its output.
    """

    @pytest.mark.asyncio
    async def test_kiro_completed_nonzero_exit_reaches_the_warn_rung(self, tmp_path):
        """`G151`, end to end. Before the derivation this stream produced NOTHING: ten
        such calls were measured live against kiro with no warn, no block, no trip."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _real_frame_cycle(WARN_THRESHOLD, KIRO_FAILED_FRAME))
        session = _session()
        await _drive(state, session)
        assert any("this is failure #" in t for t in _texts(session)), _texts(session)

    @pytest.mark.asyncio
    async def test_kiro_frames_reach_the_block_rung(self, tmp_path):
        state, client = _make_state(tmp_path)
        _set_stream(client, _real_frame_cycle(BLOCK_THRESHOLD, KIRO_FAILED_FRAME))
        session = _session()
        await _drive(state, session)
        assert any("was blocked" in t for t in _texts(session)), _texts(session)

    @pytest.mark.asyncio
    async def test_kiro_frames_trip_the_circuit_and_abort_the_turn(self, tmp_path):
        """done_when clause 2 on the runtime that could not reach it before."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _real_frame_cycle(CIRCUIT_THRESHOLD + 2, KIRO_FAILED_FRAME))
        session = _session()
        await _drive(state, session)
        assert any("Run aborted by the loop breaker" in t for t in _texts(session))
        client.cancel_session.assert_awaited()

    @pytest.mark.asyncio
    async def test_codex_frames_still_reach_the_warn_rung(self, tmp_path):
        """The runtime that already worked keeps working — the derivation only ever
        ADDS a failure the CLI declined to name."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _real_frame_cycle(WARN_THRESHOLD, CODEX_FAILED_FRAME))
        session = _session()
        await _drive(state, session)
        assert any("this is failure #" in t for t in _texts(session)), _texts(session)

    @pytest.mark.asyncio
    async def test_kiro_passing_frames_produce_no_breaker_text(self, tmp_path):
        """The vacuity floor for this whole class. Same runtime, same tool, same frame
        SHAPE, exit 0 — thirty of them. If the derivation ever widens into "kiro means
        failure", this is what goes red instead of a user's healthy turn being aborted
        in production."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _real_frame_cycle(CIRCUIT_THRESHOLD, KIRO_PASSED_FRAME))
        session = _session()
        await _drive(state, session)
        texts = _texts(session)
        assert not any("this is failure #" in t for t in texts), texts
        assert not any("Run aborted by the loop breaker" in t for t in texts), texts


# ── the breaker's IDENTITY: a per-call narration must not mint a new bucket ──


#: kiro's real ``rawInput`` for the four byte-identical failing calls it ran live on
#: 2026-08-23 — same command, a different narration every time.
KIRO_PER_CALL_INPUTS = [
    json.dumps(
        {
            "__tool_use_purpose": f"Run the requested command for the {nth} time.",
            "command": "bash -c 'echo boom >&2; exit 3'",
        },
        indent=2,
    )
    for nth in ("first", "second", "third", "fourth", "fifth", "sixth")
]


def _kiro_narrated_cycle(n):
    """n identical failing calls carrying kiro's per-call ``__tool_use_purpose``."""
    out: list = []
    for i in range(n):
        out.append(
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"t{i}",
                title="bash",
                tool_input=KIRO_PER_CALL_INPUTS[i % len(KIRO_PER_CALL_INPUTS)],
            )
        )
        out.append(_decoded_result(KIRO_FAILED_FRAME, f"t{i}"))
    out.append(LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    return out


class TestBreakerIdentityIgnoresAdapterNarration:
    """`G152`, the half that survived the failure-bit fix. Four identical failing calls
    each arrived correctly signed ``ok: False`` and the breaker STILL said nothing,
    because ``params_key`` bucketed on kiro's per-call ``__tool_use_purpose`` and gave
    each call its own streak of one."""

    def test_identical_calls_with_different_narration_are_one_bucket(self):
        keys = {params_key("bash", raw) for raw in KIRO_PER_CALL_INPUTS}
        assert len(keys) == 1, keys

    def test_different_commands_are_still_different_buckets(self):
        """Vacuity floor: the normalization merges narration, never arguments. If it
        ever merged on tool name alone, every bash call in a run would share one streak
        and an ordinary session would start tripping the breaker."""
        a = json.dumps({"__tool_use_purpose": "same", "command": "ls"})
        b = json.dumps({"__tool_use_purpose": "same", "command": "rm -rf /"})
        assert params_key("bash", a) != params_key("bash", b)

    def test_native_dict_args_are_untouched(self):
        """The native runtime passes a dict of real arguments and shares this function,
        so the ACP fix must be a no-op there."""
        assert params_key("write", {"path": "a.txt"}) != params_key("write", {"path": "b.txt"})
        assert params_key("write", {"path": "a.txt"}) == params_key("write", {"path": "a.txt"})

    def test_an_all_metadata_input_keeps_its_original_identity(self):
        """Stripping everything would collapse unrelated calls into one bucket, so an
        input made only of adapter metadata is left alone."""
        a = json.dumps({"__tool_use_purpose": "one"})
        b = json.dumps({"__tool_use_purpose": "two"})
        assert params_key("bash", a) != params_key("bash", b)

    def test_a_non_json_input_string_is_left_alone(self):
        assert params_key("bash", "not json at all") == params_key("bash", "not json at all")
        assert params_key("bash", "a") != params_key("bash", "b")

    @pytest.mark.asyncio
    async def test_narrated_kiro_calls_reach_the_warn_rung(self, tmp_path):
        """The CALL SITE for both halves at once: real frames for the failure bit, real
        per-call inputs for the identity. This is the stream measured live."""
        state, client = _make_state(tmp_path)
        _set_stream(client, _kiro_narrated_cycle(WARN_THRESHOLD))
        session = _session()
        await _drive(state, session)
        assert any("this is failure #" in t for t in _texts(session)), _texts(session)

    @pytest.mark.asyncio
    async def test_narrated_kiro_calls_reach_the_block_rung(self, tmp_path):
        state, client = _make_state(tmp_path)
        _set_stream(client, _kiro_narrated_cycle(BLOCK_THRESHOLD))
        session = _session()
        await _drive(state, session)
        assert any("was blocked" in t for t in _texts(session)), _texts(session)
