"""Tests for the CORE _interactive_approval contract.

Slack no longer lives in the core gateway. The channel (Slack, …) is a
pluggable ChannelDelivery handle (``gateway._channel_delivery``) with an
async ``request_approval(event, *, source, parent_session_key, sessions,
on_prompted)`` method. The Block-Kit message building, thread-ts resolution,
``_pending_approvals`` registry, and message-update all moved INTO the app's
SlackDelivery.request_approval — those are exercised by the app's own
test_delivery, not here.

What remains a CORE contract (and is tested below):
- ``_interactive_approval(source)`` returns a callback that delegates to
  ``_channel_delivery.request_approval`` and returns its bool result.
- When no channel is registered, it falls back to
  ``dashboard_state.request_approval``.
- The CLI ``--approval yolo|reads`` auto-approve short-circuit emits SEL audit.
- SubagentManager threads ``parent_session_key`` through its approval hooks.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.llm_helpers import LLMEvent


def _make_gateway():
    from personalclaw.gateway import GatewayOrchestrator

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.sessions = MagicMock()
    gateway.sessions.get_pid = MagicMock(return_value=None)
    # Channel delivery seam (replaces the old core `gateway.slack`).
    gateway._channel_delivery = MagicMock()
    gateway._channel_delivery.request_approval = AsyncMock(return_value=True)
    gateway.dashboard_state = MagicMock()
    # Gateway reads global YOLO via personalclaw.trust_mode (patched off per test).
    gateway.dashboard_state.is_yolo_active.return_value = False
    gateway.dashboard_state._sessions = {}
    gateway.dashboard_state.request_approval = AsyncMock(return_value=True)
    gateway.dashboard_state.resolve_approval = MagicMock()
    gateway._owner_id = "U000"
    gateway._cfg = MagicMock()
    gateway._cfg.hooks = MagicMock()
    gateway._cfg.hooks.get = MagicMock(return_value=[])
    gateway._cfg.agent.max_subagents = 4
    gateway.sessions.get_channel = MagicMock(return_value=None)
    gateway.sessions.get_thread = MagicMock(return_value=None)
    gateway._approval_mode = None
    return gateway


def _make_event(request_id: str = "req1", title: str = "shell: ls") -> LLMEvent:
    return LLMEvent(kind="permission_request", request_id=request_id, title=title)


# ── Tests: _interactive_approval delegates to the channel delivery seam ──


class TestChannelDelegation:
    """The core callback delegates approval to _channel_delivery.request_approval."""

    @pytest.mark.asyncio
    async def test_delegates_to_channel_and_returns_result(self) -> None:
        """_interactive_approval calls request_approval and returns its bool."""
        gateway = _make_gateway()
        gateway._channel_delivery.request_approval = AsyncMock(return_value=True)

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            approve_fn = gateway._interactive_approval("subagent")
            result = await approve_fn(_make_event(), "1775113012.860459")

        assert result is True
        gateway._channel_delivery.request_approval.assert_awaited_once()
        call = gateway._channel_delivery.request_approval.call_args
        # event is the first positional; source + routing context ride kwargs.
        assert call.args[0].request_id == "req1"
        assert call.kwargs["source"] == "subagent"
        assert call.kwargs["parent_session_key"] == "1775113012.860459"
        assert call.kwargs["sessions"] is gateway.sessions

    @pytest.mark.asyncio
    async def test_returns_false_when_channel_rejects(self) -> None:
        """A False decision from the channel propagates as False."""
        gateway = _make_gateway()
        gateway._channel_delivery.request_approval = AsyncMock(return_value=False)

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            approve_fn = gateway._interactive_approval("subagent")
            result = await approve_fn(_make_event(), "cron:j1")

        assert result is False

    @pytest.mark.asyncio
    async def test_falls_back_to_dashboard_when_no_channel(self) -> None:
        """With no channel registered, approval falls back to the dashboard."""
        gateway = _make_gateway()
        gateway._channel_delivery = None
        gateway.dashboard_state.request_approval = AsyncMock(return_value=True)

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            approve_fn = gateway._interactive_approval("subagent")
            result = await approve_fn(_make_event(), "")

        assert result is True
        gateway.dashboard_state.request_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_exception_falls_back_to_dashboard(self) -> None:
        """If the channel raises, core falls back to the dashboard prompt."""
        gateway = _make_gateway()
        gateway._channel_delivery.request_approval = AsyncMock(
            side_effect=RuntimeError("slack down")
        )
        gateway.dashboard_state.request_approval = AsyncMock(return_value=False)

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            approve_fn = gateway._interactive_approval("subagent")
            result = await approve_fn(_make_event(), "")

        assert result is False
        gateway.dashboard_state.request_approval.assert_awaited_once()


# ── Tests: subagent passes parent_session_key to approval ──


class TestSubagentPassesParentKey:
    """SubagentManager must pass parent_session_key to approval callbacks."""

    @pytest.mark.asyncio
    async def test_spawn_approval_receives_parent_session_key(self) -> None:
        """on_spawn_approval is called with parent_session_key."""
        from personalclaw.subagent import SubagentManager

        captured_args: list = []

        async def mock_spawn_approval(
            request_id: str, description: str, parent_session_key: str = ""
        ) -> bool:
            captured_args.append((request_id, description, parent_session_key))
            return False  # reject to avoid running

        sessions = MagicMock()
        sessions.get_pid = MagicMock(return_value=None)
        sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
        sessions.release = MagicMock()
        sessions.reset = AsyncMock()
        ctx_builder = MagicMock()
        ctx_builder.hooks = MagicMock()

        manager = SubagentManager(
            sessions=sessions,
            ctx_builder=ctx_builder,
            on_spawn_approval=mock_spawn_approval,
            is_yolo=lambda: False,
        )

        info = manager.spawn("check oncall", parent_session_key="1775113012.860459")
        assert info is not None

        # Await the spawned task directly (deterministic, no sleep)
        with contextlib.suppress(Exception):
            await manager._tasks[info.id]

        assert len(captured_args) == 1
        assert captured_args[0][2] == "1775113012.860459"

    @pytest.mark.asyncio
    async def test_tool_approval_receives_parent_session_key(self) -> None:
        """on_tool_approval is called with parent_session_key during tool requests."""
        from personalclaw.subagent import SubagentManager

        captured: list = []

        async def mock_tool_approval(event: LLMEvent, parent_session_key: str = "") -> bool:
            captured.append(parent_session_key)
            return False  # reject to stop the loop

        # Mock client whose stream yields one permission_request then ends
        mock_client = MagicMock()
        perm_event = LLMEvent(
            kind="permission_request", request_id="tool1", title="shell: ls"
        )

        async def _stream(_msg: str):
            yield perm_event

        mock_client.stream = _stream
        mock_client.reject_tool = AsyncMock()

        sessions = MagicMock()
        sessions.get_pid = MagicMock(return_value=None)
        sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        sessions.get_approval_policy = MagicMock(return_value=None)
        sessions.release = MagicMock()
        sessions.reset = AsyncMock()
        ctx_builder = MagicMock()
        ctx_builder.hooks = MagicMock()
        ctx_builder.build_message = MagicMock(return_value=("task", {}))

        manager = SubagentManager(
            sessions=sessions,
            ctx_builder=ctx_builder,
            on_tool_approval=mock_tool_approval,
            on_spawn_approval=AsyncMock(return_value=True),
            is_yolo=lambda: False,
        )

        info = manager.spawn("ls /tmp", parent_session_key="1775113012.860459")
        assert info is not None

        with contextlib.suppress(Exception):
            await manager._tasks[info.id]

        assert len(captured) == 1
        assert captured[0] == "1775113012.860459"


# ── Tests: --approval CLI mode emits SEL audit events ──


class TestApprovalModeSelAudit:
    """`--approval yolo` and `reads` auto-approvals must emit SEL events.

    This short-circuit lives in the core _interactive_approval, before any
    channel delegation, so it is a core contract.
    """

    @pytest.mark.asyncio
    async def test_yolo_emits_sel_audit_on_approve(self) -> None:
        """`--approval yolo` records the decision via sel().log_api_access."""
        gateway = _make_gateway()
        gateway._approval_mode = "yolo"

        mock_sel = MagicMock()
        mock_sel.log_api_access = MagicMock()
        with patch("personalclaw.gateway.sel", return_value=mock_sel), patch(
            "personalclaw.trust_mode.is_yolo_active", return_value=False
        ):
            approve_fn = gateway._interactive_approval("cron")
            result = await approve_fn(_make_event(title="shell: rm -rf /"), "")

        assert result is True
        mock_sel.log_api_access.assert_called_once()
        kwargs = mock_sel.log_api_access.call_args.kwargs
        assert kwargs["caller"] == "cli:approval=yolo"
        assert kwargs["operation"] == "cron.cli_approval_auto_approve"
        assert kwargs["outcome"] == "ok"
        # Title is redacted — destructive command body still passes through
        # (redaction targets credentials/URLs, not shell args), so the
        # operation context is preserved for triage.
        assert "rm" in kwargs["resources"]

    @pytest.mark.asyncio
    async def test_reads_emits_sel_audit_on_read_only_tool(self) -> None:
        """`--approval reads` records the decision when the tool is read-only."""
        gateway = _make_gateway()
        gateway._approval_mode = "reads"

        mock_sel = MagicMock()
        mock_sel.log_api_access = MagicMock()
        with patch("personalclaw.gateway.sel", return_value=mock_sel), patch(
            "personalclaw.trust_mode.is_yolo_active", return_value=False
        ):
            approve_fn = gateway._interactive_approval("subagent")
            result = await approve_fn(_make_event(title="read /tmp/foo.txt"), "")

        assert result is True
        mock_sel.log_api_access.assert_called_once()
        kwargs = mock_sel.log_api_access.call_args.kwargs
        assert kwargs["caller"] == "cli:approval=reads"
        assert kwargs["operation"] == "subagent.cli_approval_auto_approve"

    @pytest.mark.asyncio
    async def test_reads_no_sel_audit_on_write_tool_falls_through(self) -> None:
        """`--approval reads` with a write tool must NOT emit a yolo-style auto-approve event.

        The decision falls through to the standard interactive flow (channel
        delegation); SEL emission for the eventual decision happens elsewhere.
        """
        gateway = _make_gateway()
        gateway._approval_mode = "reads"

        mock_sel = MagicMock()
        mock_sel.log_api_access = MagicMock()
        with patch("personalclaw.gateway.sel", return_value=mock_sel), patch(
            "personalclaw.trust_mode.is_yolo_active", return_value=False
        ):
            approve_fn = gateway._interactive_approval("subagent")
            await approve_fn(_make_event(title="shell: rm -rf /"), "")

        # No `cli_approval_auto_approve` event — the write tool fell
        # through to the standard flow, not the auto-approve path.
        for call in mock_sel.log_api_access.call_args_list:
            assert "cli_approval_auto_approve" not in call.kwargs.get("operation", "")

    @pytest.mark.asyncio
    async def test_yolo_returns_true_even_if_sel_raises(self) -> None:
        """SEL failures must NOT block the approval — fail-open on logging."""
        gateway = _make_gateway()
        gateway._approval_mode = "yolo"

        broken_sel = MagicMock(side_effect=RuntimeError("sel unavailable"))
        with patch("personalclaw.gateway.sel", broken_sel), patch(
            "personalclaw.trust_mode.is_yolo_active", return_value=False
        ):
            approve_fn = gateway._interactive_approval("cron")
            result = await approve_fn(_make_event(), "")

        assert result is True  # approval still proceeds
