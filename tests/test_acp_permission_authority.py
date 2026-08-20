"""AAP-5 §2.2 — the HOST is the ACP permission authority.

Every test here drives a real call site, not a predicate: the mode clamp is asserted
on :class:`AcpClient` (the chokepoint every mode path crosses), and the gate
behaviours are asserted by running ``run_chat`` over a synthetic ACP event stream —
the same harness ``test_dashboard_approval.py`` uses. Each restriction gets its
inverse floor so it reads as a *requirement* rather than an always-refuse.
"""

import asyncio
from dataclasses import fields, replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.permission_authority import (
    AUTO_APPROVE_MODES,
    HOST_AUTHORITY_MODE,
    NOT_GATEABLE,
    NotGateable,
    ProviderCoverage,
    ResidualState,
    command_probe,
    coverage_for,
    normalize_provider,
    not_gateable_entry,
    sanitize_mode,
)
from personalclaw.acp.translate import build_permission_event
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)

# ── mode clamp: the module contract ──────────────────────────────────────────


class TestSanitizeMode:
    def test_empty_asserts_the_restrictive_mode(self):
        """ "Whatever the CLI defaults to" IS the hole — the host names the mode."""
        d = sanitize_mode("")
        assert d.mode == HOST_AUTHORITY_MODE
        assert not d.downgraded

    @pytest.mark.parametrize("mode", sorted(AUTO_APPROVE_MODES))
    def test_every_declared_auto_approve_mode_is_clamped(self, mode):
        d = sanitize_mode(mode)
        assert d.mode == HOST_AUTHORITY_MODE
        assert d.downgraded
        assert mode in d.reason

    @pytest.mark.parametrize(
        "spelling", ["acceptEdits", "accept-edits", "ACCEPT_EDITS", "bypassPermissions", "yolo"]
    )
    def test_spelling_variants_do_not_slip_through(self, spelling):
        assert sanitize_mode(spelling).mode == HOST_AUTHORITY_MODE

    def test_plan_and_default_pass_through_verbatim(self):
        """Plan is behavioral, not an approval bypass — it must survive the clamp."""
        assert sanitize_mode("plan").mode == "plan"
        assert sanitize_mode("plan").downgraded is False
        assert sanitize_mode("default").mode == "default"

    def test_unknown_mode_is_clamped_not_assumed_safe(self):
        d = sanitize_mode("someAdapterModeWeHaveNeverSeen")
        assert d.mode == HOST_AUTHORITY_MODE
        assert d.downgraded

    def test_unattended_is_the_only_declared_escape(self):
        """§2.3 owns the unattended path; it must be an EXPLICIT opt-in, not a default."""
        assert sanitize_mode("bypassPermissions", unattended=True).mode == "bypassPermissions"
        assert sanitize_mode("bypassPermissions").mode == HOST_AUTHORITY_MODE


class TestCommandProbe:
    def test_probes_the_real_command_behind_an_unknown_title(self):
        assert command_probe("unknown", "git push --force") == "Running: git push --force"

    def test_no_probe_when_the_title_already_carries_the_command(self):
        assert command_probe("Running: ls -la", "ls -la") == ""

    def test_no_probe_without_a_command(self):
        assert command_probe("fs_write", "") == ""


def _claims_empty_residual(cov: ProviderCoverage) -> bool:
    """Does this coverage's prose assert a zero-length residual set?"""
    return "empty" in cov.measurement.lower()


class TestNotGateableRegistry:
    def test_every_provider_is_enumerated_with_its_measurement(self):
        """A missing entry must never be readable as "gated" — SC #3's honesty clause."""
        assert set(NOT_GATEABLE) == {"kiro-cli", "claude-code", "codex"}
        for cov in NOT_GATEABLE.values():
            assert cov.measurement, f"{cov.provider} has no measurement provenance"

    def test_kiro_todo_list_is_the_declared_hole(self):
        entry = not_gateable_entry("acp:kiro-cli", "Creating task list: fix the thing")
        assert entry is not None
        assert entry.tool == "todo_list"
        assert "G27" in entry.observation
        assert not_gateable_entry("kiro-cli", "Completing #2") is not None

    def test_kiro_self_approved_reads_are_declared_too(self):
        """AAP-5's own live re-drive widened the residue: the write raised a card,
        the read in the SAME turn did not."""
        entry = not_gateable_entry("kiro-cli", "Reading todo_probe.txt:1-10")
        assert entry is not None and entry.tool == "fs_read"

    def test_a_gateable_kiro_tool_is_not_excused(self):
        """Vacuity floor for the registry: it must NOT match everything. The write and
        the shell command DID raise cards live, so they must not be excused here."""
        assert not_gateable_entry("kiro-cli", "Creating todo_probe.txt") is None
        assert not_gateable_entry("kiro-cli", "Running: rm -rf /tmp/x") is None

    def test_gated_universally_is_false_wherever_a_residual_exists(self):
        """The claim runtime disproved. claude-code and codex each declared
        "residual set measured EMPTY" (so ``gated_universally`` was True) while SEL
        was persisting plain ``ungated`` rows for both — and a plain ``ungated`` row
        is proof ``not_gateable_entry`` returned None for that title."""
        for provider in ("claude-code", "codex", "kiro-cli"):
            cov = coverage_for(provider)
            assert cov is not None, provider
            assert cov.entries, f"{provider} declares no measured residual"
            assert not cov.gated_universally, f"{provider} still claims full coverage"

    def test_an_empty_residual_is_still_expressible(self):
        """Category floor for the test above: ``gated_universally`` is a real
        statement, not a constant False. A provider that genuinely gates everything
        still says so — the registry lost none of its expressive range."""
        empty = ProviderCoverage(provider="hypothetical", measurement="measured, none")
        assert empty.gated_universally
        assert empty.unaccepted_residual == ()

    def test_every_measured_residual_carries_its_proving_observation(self):
        for cov in NOT_GATEABLE.values():
            for entry in cov.entries:
                assert entry.reason.strip(), f"{cov.provider}/{entry.tool} has no reason"
                assert entry.observation.strip(), f"{cov.provider}/{entry.tool} has no proof"

    def test_no_provider_claims_an_empty_residual_while_declaring_one(self):
        """The original defect in its general form: measurement prose asserting an
        empty residual next to entries that contradict it."""
        for cov in NOT_GATEABLE.values():
            assert not (cov.entries and _claims_empty_residual(cov)), cov.measurement

    def test_the_empty_claim_scanner_can_actually_fire(self):
        """Vacuity floor for the scanner above — the exact shape it must reject."""
        liar = ProviderCoverage(
            provider="liar",
            measurement="AAP-1 sweep — residual set measured EMPTY",
            entries=(NotGateable(tool="Terminal", reason="r", observation="o"),),
        )
        assert liar.entries and _claims_empty_residual(liar)

    def test_a_declared_residual_is_not_an_excused_one(self):
        """The third state. claude-code's measured holes are written down — so they
        are not "a new incident" — yet unaccepted, so nothing may go quiet."""
        entry = not_gateable_entry("acp:claude-code-acp", "Terminal")
        assert entry is not None and entry.tool == "Terminal"
        assert entry.state is ResidualState.UNACCEPTED
        assert entry.accepted is False  # the derived shorthand agrees
        cov = coverage_for("claude-code")
        assert cov is not None and cov.unaccepted_residual == cov.entries

    def test_an_accepted_residual_is_the_inverse_floor(self):
        """…and ``accepted`` is not uniformly False: kiro's two holes ARE blessed,
        which is what lets the host label them instead of shouting."""
        entry = not_gateable_entry("kiro-cli", "Creating task list: fix the thing")
        assert entry is not None and entry.state is ResidualState.ACCEPTED
        cov = coverage_for("kiro-cli")
        assert cov is not None and cov.unaccepted_residual == ()

    def test_a_new_entry_is_loud_until_someone_blesses_it(self):
        """Fail-loud by construction: acceptance is opt-in, never inherited."""
        fresh = NotGateable(tool="x", reason="r", observation="o")
        assert fresh.state is ResidualState.UNACCEPTED and fresh.accepted is False

    def test_the_state_is_a_rendered_field_not_a_derived_property(self):
        """§2.7's parity doc renders the registry by reflecting ``dataclasses.fields``
        and skipping collection-shaped fields, unwrapping ``Enum`` via ``.value``. A
        state expressed as a ``@property`` — like ``gated_universally``, which is
        deliberately absent below — never reaches that operator-facing surface, so
        this rail pins where the third state LIVES, not merely what it says."""
        names = {f.name for f in fields(NotGateable)}
        assert "state" in names
        assert "gated_universally" not in {f.name for f in fields(ProviderCoverage)}  # floor
        for cov in NOT_GATEABLE.values():
            for entry in cov.entries:
                assert isinstance(entry.state, ResidualState), (cov.provider, entry.tool)
                # Prose, not a bare flag: the doc prints this verbatim.
                assert " " in entry.state.value

    def test_provider_key_normalization_covers_all_three_spellings(self):
        assert normalize_provider("acp:claude-code-acp") == "claude-code"
        assert normalize_provider("codex-acp") == "codex"
        assert normalize_provider("KIRO") == "kiro-cli"


# ── mode clamp: the AcpClient call site ──────────────────────────────────────


class TestAcpClientClampsAtTheCallSite:
    def _client(self, mode):
        from personalclaw.acp.client import AcpClient

        with patch("personalclaw.sel.sel", MagicMock()):
            return AcpClient(work_dir="/tmp", command=["true"], mode=mode)

    def test_constructor_refuses_an_auto_approve_mode(self):
        assert self._client("bypassPermissions")._mode == HOST_AUTHORITY_MODE

    def test_constructor_forwards_the_restrictive_mode_when_none_asked(self):
        assert self._client(None)._mode == HOST_AUTHORITY_MODE

    def test_constructor_keeps_plan(self):
        assert self._client("plan")._mode == "plan"

    @pytest.mark.asyncio
    async def test_live_set_mode_refuses_an_auto_approve_mode(self):
        """The post-start switch is the second door — it must clamp too."""
        client = self._client("default")
        client._session_id = "sess-1"
        sent: list = []
        client._send_dialect_request = AsyncMock(side_effect=lambda req: sent.append(req))
        with patch("personalclaw.sel.sel", MagicMock()):
            await client.set_mode("acceptEdits")
        assert client._mode == HOST_AUTHORITY_MODE
        assert sent == [], "an auto-approve mode must not be forwarded to the adapter"

    @pytest.mark.asyncio
    async def test_live_set_mode_still_switches_to_plan(self):
        """Inverse floor: the clamp is not an always-refuse."""
        client = self._client("default")
        client._session_id = "sess-1"
        client._send_dialect_request = AsyncMock()
        await client.set_mode("plan")
        assert client._mode == "plan"
        client._send_dialect_request.assert_awaited_once()


class TestPooledSessionProviderClampsToo:
    """The pooled/concurrent path builds the dialect request ITSELF rather than going
    through AcpClient — fixing only the wrapper would leave this raw child open."""

    def _provider(self):
        from personalclaw.llm.acp_session_provider import AcpSessionProvider

        provider = AcpSessionProvider.__new__(AcpSessionProvider)
        provider._conn = MagicMock()
        provider._session = MagicMock(session_id="sess-1")
        # __init__ is bypassed here, so state it explicitly: these are ATTENDED
        # sessions. AAP-6 added the unattended axis that set_mode reads, and the
        # clamp asserted below is precisely what an attended session must keep.
        provider._unattended = False
        provider._conn._dialect.set_mode_request.side_effect = lambda **kw: kw
        provider._send_dialect_request = AsyncMock()
        return provider

    @pytest.mark.asyncio
    async def test_pooled_set_mode_refuses_an_auto_approve_mode(self):
        provider = self._provider()
        with patch("personalclaw.sel.sel", MagicMock()):
            await provider.set_mode("bypassPermissions")
        forwarded = provider._conn._dialect.set_mode_request.call_args.kwargs["mode"]
        assert forwarded == HOST_AUTHORITY_MODE

    @pytest.mark.asyncio
    async def test_pooled_set_mode_still_forwards_plan(self):
        """Inverse floor."""
        provider = self._provider()
        await provider.set_mode("plan")
        assert provider._conn._dialect.set_mode_request.call_args.kwargs["mode"] == "plan"
        provider._send_dialect_request.assert_awaited_once()


class TestPermissionFrameCarriesKind:
    def test_declared_kind_survives_into_the_event(self):
        """G18's half that §2.2 needs: the residue check must be able to NAME the tool."""
        msg = JsonRpcMessage(
            id=7,
            method="session/request_permission",
            params={
                "sessionId": "s",
                "toolCall": {"toolCallId": "tc-1", "kind": "edit"},
                "options": [{"optionId": "allow", "name": "Allow"}],
            },
        )
        dialect = MagicMock()
        dialect.parse_permission_options.return_value = [{"id": "allow", "label": "Allow"}]
        event = build_permission_event(msg, dialect, {}, {}, {})
        assert event.tool_kind == "edit"
        assert event.title == "unknown"  # the adapter sent none — unchanged


# ── the gate call sites: drive run_chat ─────────────────────────────────────


async def _async_iter(items):
    for item in items:
        yield item


def _make_state(tmp_path, context_builder=None):
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
    state.context_builder = context_builder
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


def _context_builder(on_tool_call=None):
    cb = MagicMock()
    if on_tool_call is None:
        cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    else:
        cb.hooks.on_tool_call.side_effect = on_tool_call
    cb.build_message.return_value = ("hello", None)
    return cb


def _session(key="chat-1-aap5", *, task_mode="agent", trust=False):
    s = _ChatSession(key)
    s._trust = trust
    s._task_mode = task_mode
    return s


def _set_stream(client, events):
    client.stream = MagicMock(side_effect=lambda *a, **kw: _async_iter(events))


def _tool_texts(session):
    return [m["content"] for m in session.messages if m.get("role") in ("tool", "permission")]


async def _drive(state, session, *, answer=None, sel_mock=None):
    """Run one turn; optionally answer the interactive approval card.

    ``sel_mock`` lets a caller inspect the audit rows the turn persisted — the SEL
    half of "loud" lives there, not in the transcript.
    """
    if answer is not None:

        async def _answer():
            for _ in range(60):
                fut = session._approval_futures.get("req-1")
                if fut and not fut.done():
                    fut.set_result(answer)
                    return
                await asyncio.sleep(0.01)

        asyncio.get_event_loop().create_task(_answer())
    with patch("personalclaw.dashboard.chat_runner.sel", sel_mock or MagicMock()):
        await run_chat(state, session, "hello")


def _ungated_audit_rows(sel_mock):
    """The ``log_tool_invocation`` kwargs for rows the host was never asked about."""
    return [
        c.kwargs
        for c in sel_mock.return_value.log_tool_invocation.call_args_list
        if str(c.kwargs.get("outcome", "")).startswith("ungated")
    ]


class TestNoSilentWriteUnderAsk:
    """done_when clause 1: a file write under task-mode=Ask yields a card or a block."""

    @pytest.mark.asyncio
    async def test_ask_mode_blocks_the_write_at_the_prompt(self, tmp_path):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="ask", trust=True)  # trust ON: the gate still wins
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input='{"path": "/tmp/aap5.txt", "content": "x"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.reject_tool.assert_awaited_once_with("req-1")
        client.approve_tool.assert_not_awaited()
        assert any("Ask mode" in t for t in _tool_texts(session)), _tool_texts(session)

    @pytest.mark.asyncio
    async def test_agent_mode_lets_the_same_write_through(self, tmp_path):
        """Inverse floor: the block is a task-mode requirement, not a dead end."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input='{"path": "/tmp/aap5.txt", "content": "x"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.approve_tool.assert_awaited_once()
        client.reject_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_untrusted_write_raises_an_interactive_card(self, tmp_path):
        """The other half of "card OR block": no trust → the user is asked."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="agent", trust=False)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input='{"path": "/tmp/aap5.txt", "content": "x"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session, answer="denied")
        assert any(m.get("role") == "permission" for m in session.messages)
        client.reject_tool.assert_awaited_once()


class TestDenyListAtThePrompt:
    """done_when clause 2: the deny-list rejects a denied command at the prompt."""

    @staticmethod
    def _deny_git_push(name):
        from personalclaw.security import is_denied

        reason = is_denied(name)
        return ToolHookResult.deny(reason) if reason else ToolHookResult.allow()

    @pytest.mark.asyncio
    async def test_denied_command_hidden_behind_an_unknown_title_is_rejected(self, tmp_path):
        """The title is "unknown" (G18) — the deny-list must see the real command."""
        state, client = _make_state(tmp_path, context_builder=_context_builder(self._deny_git_push))
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="unknown",
                    tool_kind="execute",
                    request_id="req-1",
                    tool_input='{"command": "git push --force origin main"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.reject_tool.assert_awaited_once_with("req-1")
        client.approve_tool.assert_not_awaited()
        assert any("Blocked by security policy" in t for t in _tool_texts(session)), _tool_texts(
            session
        )

    @pytest.mark.asyncio
    async def test_an_allowed_command_behind_the_same_unknown_title_proceeds(self, tmp_path):
        """Inverse floor: the probe denies by PATTERN, it does not deny everything."""
        state, client = _make_state(tmp_path, context_builder=_context_builder(self._deny_git_push))
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="unknown",
                    tool_kind="execute",
                    request_id="req-1",
                    tool_input='{"command": "git status"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.approve_tool.assert_awaited_once()
        client.reject_tool.assert_not_awaited()


class TestBlockingPreToolUseOnTheAcpPath:
    """done_when clause 2b: blocking PreToolUse fires PRE-execution on every
    permission-surfaced ACP tool. The clamp is what makes "every" mean something —
    a self-approving CLI never reaches this branch at all."""

    def _state(self, tmp_path, hook_output):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        state._hook_store.fire_for_ids = AsyncMock(return_value=hook_output)
        return state, client

    @pytest.mark.asyncio
    async def test_blocked_hook_rejects_before_execution(self, tmp_path):
        blocking_hook = MagicMock(
            exit_code=2, stdout="", stderr="no writes today", hook_name="deny-writes"
        )
        state, client = self._state(tmp_path, [blocking_hook])
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input='{"path": "/tmp/a"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.reject_tool.assert_awaited_once_with("req-1")
        client.approve_tool.assert_not_awaited()
        assert any("hook blocked" in t for t in _tool_texts(session)), _tool_texts(session)

    @pytest.mark.asyncio
    async def test_passing_hook_lets_the_tool_run(self, tmp_path):
        """Inverse floor: the hook is consulted, not an unconditional block."""
        state, client = self._state(tmp_path, [])
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input='{"path": "/tmp/a"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.approve_tool.assert_awaited_once()
        client.reject_tool.assert_not_awaited()


class TestUngatedResidue:
    """done_when clause 3: the residual not-gateable set, made visible not silent."""

    @staticmethod
    def _tool_pair(tool_call_id, title, kind, tool_input=""):
        return [
            LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=tool_call_id,
                title=title,
                tool_kind=kind,
                tool_input=tool_input,
            ),
            LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id=tool_call_id, tool_output="ok"),
        ]

    @pytest.mark.asyncio
    async def test_declared_hole_is_surfaced_but_does_not_abort(self, tmp_path):
        """kiro's todo_list: written down, so it is labelled — not treated as new."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="ask")
        _set_stream(
            client,
            self._tool_pair("tc-1", "Creating task list: fix the thing", "other")
            + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        await _drive(state, session)
        texts = [
            c.args[1].get("text", "")
            for c in state.broadcast_ws.call_args_list
            if c.args and c.args[0] == "activity_event"
        ]
        assert any("Not gated by host" in t for t in texts), texts
        client.cancel_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_undeclared_ungated_mutation_under_ask_aborts_the_turn(self, tmp_path):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="ask")
        _set_stream(
            client,
            self._tool_pair("tc-9", "rm -rf /tmp/aap5", "execute", '{"command": "rm -rf /tmp/x"}')
            + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        await _drive(state, session)
        client.cancel_session.assert_awaited_once()
        assert any("ungated" in t for t in _tool_texts(session)), _tool_texts(session)
        assert any("turn stopped" in t for t in _tool_texts(session)), _tool_texts(session)

    @pytest.mark.asyncio
    async def test_a_gated_tool_is_never_reported_as_ungated(self, tmp_path):
        """Inverse floor: passing through the gate must clear the residue flag."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="tc-2",
                    title="fs_write",
                    tool_kind="edit",
                ),
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_call_id="tc-2",
                    tool_input='{"path": "/tmp/a"}',
                ),
                LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="tc-2", tool_output="ok"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.approve_tool.assert_awaited_once()
        client.cancel_session.assert_not_awaited()
        assert not any("ungated" in t for t in _tool_texts(session)), _tool_texts(session)

    @pytest.mark.asyncio
    async def test_native_runtime_tools_are_never_judged_ungated(self, tmp_path):
        """The native loop gates in-loop before approval; judging it would be a false
        positive on every YOLO turn."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        client.provider_id = "native"
        session = _session(task_mode="ask")
        _set_stream(
            client,
            self._tool_pair("tc-3", "rm -rf /tmp/aap5", "execute", '{"command": "rm -rf /x"}')
            + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        await _drive(state, session)
        client.cancel_session.assert_not_awaited()
        assert not any("ungated" in t for t in _tool_texts(session)), _tool_texts(session)

    @pytest.mark.asyncio
    async def test_vacuity_floor_gate_fires_in_the_same_turn_as_an_ungated_tool(self, tmp_path):
        """ "Gated" must not be able to pass by nothing being attempted: in ONE turn a
        gated write raises a card AND kiro's ungateable todo_list is surfaced."""
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(task_mode="agent", trust=True)
        _set_stream(
            client,
            self._tool_pair("tc-1", "Creating task list: plan it", "other")
            + [
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="tc-2",
                    title="fs_write",
                    tool_kind="edit",
                ),
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="fs_write",
                    tool_kind="edit",
                    request_id="req-1",
                    tool_call_id="tc-2",
                    tool_input='{"path": "/tmp/a"}',
                ),
                LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="tc-2", tool_output="ok"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        await _drive(state, session)
        client.approve_tool.assert_awaited_once_with("req-1")  # the gate DID fire
        texts = [
            c.args[1].get("text", "")
            for c in state.broadcast_ws.call_args_list
            if c.args and c.args[0] == "activity_event"
        ]
        assert any("Not gated by host" in t for t in texts), texts


# ── the third state: declared, unaccepted, and therefore still loud ──────────


class TestUnacceptedResidualStaysLoud:
    """§2.2's registry had two states and needed three. claude-code and codex both
    declared "residual set measured EMPTY" while SEL was persisting plain ``ungated``
    rows for them — the data was false, not the shape. Populating the registry the
    obvious way would have swapped one defect for a worse one, because a declared
    entry also quiets the transcript, downgrades the SEL outcome and suppresses the
    turn abort. These two tests are the same drive either side of the accepted bit.
    """

    _CMD = '{"command": "printf hi > /private/tmp/aap5-probe.txt"}'

    def _drive_claude_code_terminal(self, tmp_path):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        client.provider_id = "acp:claude-code"
        session = _session(task_mode="ask")
        _set_stream(
            client,
            TestUngatedResidue._tool_pair("tc-cc", "Terminal", "execute", self._CMD)
            + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        return state, client, session

    @staticmethod
    def _activity(state):
        return [
            c.args[1].get("text", "")
            for c in state.broadcast_ws.call_args_list
            if c.args and c.args[0] == "activity_event"
        ]

    @staticmethod
    def _tool_meta(session):
        return [m.get("meta", {}) for m in session.messages if m.get("role") == "tool"]

    @pytest.mark.asyncio
    async def test_a_declared_but_unaccepted_hole_keeps_every_signal(self, tmp_path):
        """claude-code's ungated ``Terminal`` IS in the registry — so it is not a NEW
        hole — and is unaccepted, so nothing goes quiet: the ``(ungated: …)``
        transcript line, the plain ``ungated`` audit row with its generic reason, the
        ``ungated_declared=False`` marker, and the abort of a destructive tool that
        ran under ask mode with no card."""
        declared = not_gateable_entry("claude-code", "Terminal")
        assert declared is not None  # precondition, not luck: the registry DOES hold it
        assert declared.state is ResidualState.UNACCEPTED
        state, client, session = self._drive_claude_code_terminal(tmp_path)
        sel_mock = MagicMock()
        await _drive(state, session, sel_mock=sel_mock)

        texts = self._activity(state)
        assert any("Ran without host approval" in t for t in texts), texts
        assert not any("Not gated by host" in t for t in texts), texts
        assert any("(ungated:" in t for t in _tool_texts(session)), _tool_texts(session)
        assert any("turn stopped" in t for t in _tool_texts(session)), _tool_texts(session)
        client.cancel_session.assert_awaited_once()
        assert any(m.get("ungated_declared") is False for m in self._tool_meta(session))
        rows = _ungated_audit_rows(sel_mock)
        assert [r["outcome"] for r in rows] == ["ungated"], rows
        assert rows[0]["metadata"]["reason"] == ("no session/request_permission for this tool_call")

    @pytest.mark.asyncio
    async def test_accepting_that_same_residual_is_what_quiets_it(self, tmp_path):
        """Vacuity floor for the rail above. Flip ONLY ``state`` on that same entry
        and every signal inverts — so the loudness is carried by the third state, not
        by something incidental to this drive. It is also the regression this whole
        change exists to prevent: this quiet shape is what a naive population of the
        registry would have produced for an out-of-workspace write that executed."""
        cov = NOT_GATEABLE["claude-code"]
        blessed = ProviderCoverage(
            provider=cov.provider,
            measurement=cov.measurement,
            entries=tuple(replace(e, state=ResidualState.ACCEPTED) for e in cov.entries),
        )
        state, client, session = self._drive_claude_code_terminal(tmp_path)
        sel_mock = MagicMock()
        with patch.dict(NOT_GATEABLE, {"claude-code": blessed}):
            await _drive(state, session, sel_mock=sel_mock)

        texts = self._activity(state)
        assert any("Not gated by host" in t for t in texts), texts
        assert not any("Ran without host approval" in t for t in texts), texts
        assert not any("(ungated:" in t for t in _tool_texts(session)), _tool_texts(session)
        client.cancel_session.assert_not_awaited()
        assert any(m.get("ungated_declared") is True for m in self._tool_meta(session))
        rows = _ungated_audit_rows(sel_mock)
        assert [r["outcome"] for r in rows] == ["ungated_declared"], rows
        assert "claude-code runs its shell tool" in rows[0]["metadata"]["reason"]
