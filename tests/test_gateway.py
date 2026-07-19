"""Tests for personalclaw.gateway (GatewayOrchestrator) coverage."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.config.loader import AppConfig
from personalclaw.gateway import (
    _CRON_MSG_LIMIT,
    _EPOCH_RE,
    _EPOCH_WINDOW_SECS,
    _FAILURE_REMINDER_SECS,
    _MAX_INJECT_ATTEMPTS,
    _SUCCESS_REMINDER_SECS,
    _VOLATILE_RE,
    GatewayOrchestrator,
    _result_hash,
)


def _make_orchestrator(
    *,
    slack_enabled: bool = False,
    owner_id: str = "U_OWNER",
    no_dashboard: bool = False,
    no_crons: bool = False,
    no_open: bool = False,
) -> GatewayOrchestrator:
    """Build a GatewayOrchestrator with mocked credentials."""
    cfg = AppConfig()
    creds: dict[str, str] = {}
    if slack_enabled:
        creds = {
            "SLACK_APP_TOKEN": "xapp-test",
            "SLACK_BOT_TOKEN": "xoxb-test",
            "PERSONALCLAW_OWNER_ID": owner_id,
        }
    else:
        if owner_id:
            creds["PERSONALCLAW_OWNER_ID"] = owner_id
    with patch.object(cfg, "load_credentials", return_value=creds):
        orch = GatewayOrchestrator(
            cfg,
            no_dashboard=no_dashboard,
            no_crons=no_crons,
            no_open=no_open,
        )
    return orch


# ─── Helper utilities ────────────────────────────────────────────────────


def _mock_sessions():
    """Return a mock SessionManager with common methods."""
    s = MagicMock()
    s.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    s.release = MagicMock()
    s.reset = AsyncMock()
    s.cancel_current = AsyncMock()
    s.get_channel = MagicMock(return_value=None)
    s.get_thread = MagicMock(return_value=None)
    s.set_thread = AsyncMock()
    s.set_channel = AsyncMock()
    s.start_pool = AsyncMock()
    s.close_all = AsyncMock()
    s.recycle_background = AsyncMock()
    return s


def _mock_dashboard_state():
    """Return a mock DashboardState."""
    ds = MagicMock()
    ds._sessions = {}
    ds._yolo = False
    # The gateway reads YOLO through is_yolo_active() (TTL-aware), not the raw
    # field — mirror the field so per-test `ds._yolo = …` flows through.
    ds.is_yolo_active.side_effect = lambda: ds._yolo
    ds.notify = MagicMock()
    ds.push_sessions_update = MagicMock()
    ds.push_refresh = MagicMock()
    ds.broadcast_ws = MagicMock()
    ds.broadcast_ws_subagent_subscribers = MagicMock()
    ds.request_approval = AsyncMock(return_value=True)
    ds.resolve_approval = MagicMock()
    ds.resolve_session = MagicMock(return_value=None)
    ds.get_session = MagicMock(return_value=None)
    ds.get_or_create_session = MagicMock()
    ds.close_all_ws = AsyncMock()
    ds.file_indexes = MagicMock()
    ds.file_indexes.stop_all = MagicMock()
    ds._background_tasks = set()
    ds.clear_update_progress = MagicMock()
    ds.push_update_progress = MagicMock()
    return ds


def _mock_channel_delivery(channel="D_U1"):
    """Mock ChannelDelivery (the outbound handle the gateway delivers through).

    Slack rendering moved to the app's SlackDelivery; core delivers via these
    high-level methods (open_dm/deliver_text/deliver_cron_result/deliver_notification/
    deliver_subagent_reply)."""
    d = MagicMock()
    d.open_dm = AsyncMock(return_value=channel)
    d.deliver_text = AsyncMock(return_value="1.0")
    d.deliver_cron_result = AsyncMock(return_value="1.0")
    d.deliver_notification = AsyncMock(return_value="1.0")
    d.deliver_subagent_reply = AsyncMock()
    d.deliver_chat_mirror = AsyncMock()
    d.request_approval = AsyncMock(return_value=True)
    return d


# ═══════════════════════════════════════════════════════════════════════════
# Tests: __init__ and constructor
# ═══════════════════════════════════════════════════════════════════════════


class TestGatewayOrchestratorInit:
    """Constructor and attribute initialization."""

    def test_default_flags(self):
        orch = _make_orchestrator()
        assert orch._no_dashboard is False
        assert orch._no_crons is False
        assert orch._no_open is False

    def test_custom_flags(self):
        orch = _make_orchestrator(no_dashboard=True, no_crons=True, no_open=True)
        assert orch._no_dashboard is True
        assert orch._no_crons is True
        assert orch._no_open is True

    def test_slack_disabled_without_tokens(self):
        # Slack client/state now live in the slack-channel app's SlackRuntime; the
        # orchestrator no longer owns a `.slack` attribute. Core only tracks the
        # token-derived enabled flag.
        orch = _make_orchestrator(slack_enabled=False)
        assert orch._slack_enabled is False

    def test_slack_enabled_with_tokens(self):
        orch = _make_orchestrator(slack_enabled=True)
        assert orch._slack_enabled is True

    def test_owner_id_stored(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U123")
        assert orch._owner_id == "U123"
        assert orch.owner_id == "U123"

    def test_services_initially_none(self):
        orch = _make_orchestrator()
        assert orch.sessions is None
        assert orch.cron_svc is None
        assert orch.heartbeat_svc is None
        assert orch.subagent_mgr is None
        assert orch.dashboard_state is None
        assert orch._channel_delivery is None


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _result_hash utility
# ═══════════════════════════════════════════════════════════════════════════


class TestResultHash:
    """Dedup hash strips volatile data."""

    def test_stable_text_produces_consistent_hash(self):
        assert _result_hash("hello world") == _result_hash("hello world")

    def test_different_text_different_hash(self):
        assert _result_hash("foo") != _result_hash("bar")

    def test_strips_iso_timestamps(self):
        a = _result_hash("deployed at 2026-01-15T10:30:00Z successfully")
        b = _result_hash("deployed at 2026-05-20T22:00:00+05:00 successfully")
        assert a == b

    def test_strips_uuids(self):
        a = _result_hash("id=550e8400-e29b-41d4-a716-446655440000 done")
        b = _result_hash("id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 done")
        assert a == b

    def test_strips_epoch_within_window(self):
        now_epoch = str(int(time.time()))
        a = _result_hash(f"ts={now_epoch} ok")
        b = _result_hash("ts= ok")
        assert a == b

    def test_preserves_epoch_outside_window(self):
        old_epoch = str(int(time.time()) - _EPOCH_WINDOW_SECS - 1000)
        a = _result_hash(f"ts={old_epoch} ok")
        b = _result_hash("ts= ok")
        assert a != b

    def test_hash_length_is_16(self):
        assert len(_result_hash("anything")) == 16

    def test_millis_epoch_stripped(self):
        now_ms = str(int(time.time() * 1000))
        a = _result_hash(f"ts={now_ms} ok")
        b = _result_hash("ts= ok")
        assert a == b


# open_dm retry logic moved to the slack-channel app's SlackDelivery.open_dm
# (see apps/slack-channel/tests/test_delivery.py) — core no longer owns it.


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_services
# ═══════════════════════════════════════════════════════════════════════════


class TestInitServices:
    """Service initialization cluster."""

    def test_init_services_creates_all(self):
        orch = _make_orchestrator(slack_enabled=True)
        with patch("personalclaw.gateway.MemoryStore") as mock_mem:
            mock_mem_inst = MagicMock()
            mock_mem_inst.init = MagicMock()
            mock_mem_inst.rebuild_index = MagicMock(return_value=5)
            mock_mem.return_value = mock_mem_inst
            with patch("personalclaw.vector_memory.VectorMemoryStore") as mock_vm:
                mock_vm_inst = MagicMock()
                mock_vm_inst.init = MagicMock()
                mock_vm.return_value = mock_vm_inst
                with patch("personalclaw.gateway.SkillsLoader"):
                    with patch("personalclaw.gateway.HookManager"):
                        with patch("personalclaw.gateway.LessonStore"):
                            with patch("personalclaw.gateway.ContextBuilder"):
                                with patch("personalclaw.gateway.ConversationLog") as mock_cl:
                                    mock_cl_inst = MagicMock()
                                    mock_cl_inst.init = MagicMock()
                                    mock_cl.return_value = mock_cl_inst
                                    with patch("personalclaw.gateway.SessionManager"):
                                        with patch("personalclaw.gateway.HistoryConsolidator"):
                                            with patch("personalclaw.gateway.ChannelHistory"):
                                                with patch("personalclaw.agent.rebuild_agent_config", return_value=Path("/tmp/a")):
                                                    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="personalclaw 1.30.0")):
                                                        orch._init_services()

        assert orch.sessions is not None
        assert orch.ctx_builder is not None
        assert orch.conv_log is not None
        assert orch.consolidator is not None
        assert orch.channel_history is not None

    def test_init_services_dashboard_only_mode(self):
        orch = _make_orchestrator(slack_enabled=False)
        with patch("personalclaw.gateway.MemoryStore") as mock_mem:
            mock_mem_inst = MagicMock()
            mock_mem_inst.init = MagicMock()
            mock_mem_inst.rebuild_index = MagicMock(return_value=0)
            mock_mem.return_value = mock_mem_inst
            with patch("personalclaw.vector_memory.VectorMemoryStore") as mock_vm:
                mock_vm_inst = MagicMock()
                mock_vm_inst.init = MagicMock()
                mock_vm.return_value = mock_vm_inst
                with patch("personalclaw.gateway.SkillsLoader"):
                    with patch("personalclaw.gateway.HookManager"):
                        with patch("personalclaw.gateway.LessonStore"):
                            with patch("personalclaw.gateway.ContextBuilder"):
                                with patch("personalclaw.gateway.ConversationLog") as mock_cl:
                                    mock_cl_inst = MagicMock()
                                    mock_cl_inst.init = MagicMock()
                                    mock_cl.return_value = mock_cl_inst
                                    with patch("personalclaw.gateway.SessionManager"):
                                        with patch("personalclaw.gateway.HistoryConsolidator"):
                                            with patch("personalclaw.gateway.ChannelHistory"):
                                                with patch("personalclaw.agent.rebuild_agent_config", return_value=Path("/tmp/a")):
                                                    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="personalclaw 1.30.0")):
                                                        orch._init_services()

        assert orch._channel_delivery is None
        assert orch.sessions is not None


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _interactive_approval
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractiveApproval:
    """Tool approval callback logic."""

    @pytest.mark.asyncio
    async def test_auto_approve_when_no_ui(self):
        """No slack, no dashboard → auto-approve."""
        orch = _make_orchestrator(slack_enabled=False)
        orch.dashboard_state = None
        callback = orch._interactive_approval("cron")
        event = MagicMock()
        event.request_id = "req-1"
        event.title = "bash: ls"
        event.tool_input = ""
        event.tool_purpose = ""
        result = await callback(event, "")
        assert result is True

    @pytest.mark.asyncio
    async def test_yolo_mode_approves(self):
        """YOLO mode → auto-approve."""
        orch = _make_orchestrator(slack_enabled=False)
        orch.dashboard_state = _mock_dashboard_state()
        orch.dashboard_state._yolo = True
        callback = orch._interactive_approval("subagent")
        event = MagicMock()
        event.request_id = "req-2"
        event.title = "dangerous command"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")
        assert result is True

    @pytest.mark.asyncio
    async def test_slack_yolo_mode_approves(self):
        """Slack YOLO mode → auto-approve."""
        orch = _make_orchestrator(slack_enabled=False)
        orch.dashboard_state = None
        callback = orch._interactive_approval("cron")
        event = MagicMock()
        event.request_id = "req-3"
        event.title = "cmd"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=True):
            result = await callback(event, "")
        assert result is True

    @pytest.mark.asyncio
    async def test_dashboard_only_approval(self):
        """Dashboard approval without Slack."""
        orch = _make_orchestrator(slack_enabled=False)
        ds = _mock_dashboard_state()
        ds.request_approval = AsyncMock(return_value=False)
        ds._yolo = False
        orch.dashboard_state = ds
        callback = orch._interactive_approval("heartbeat")
        event = MagicMock()
        event.request_id = "req-4"
        event.title = "rm -rf /"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")
        assert result is False
        ds.request_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scoped_trust_auto_approves(self):
        """Session with _trust=True → auto-approve."""
        orch = _make_orchestrator(slack_enabled=False)
        ds = _mock_dashboard_state()
        ds._yolo = False
        session = MagicMock()
        session._trust = True
        session.running = True
        ds._sessions = {"my-session": session}
        orch.dashboard_state = ds
        resolver = MagicMock(return_value="my-session")
        callback = orch._interactive_approval("subagent", session_resolver=resolver)
        event = MagicMock()
        event.request_id = "req-5"
        event.title = "safe cmd"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.sel.sel") as mock_sel:
                mock_sel.return_value.log_api_access = MagicMock()
                result = await callback(event, "")
        assert result is True

    @pytest.mark.asyncio
    async def test_all_sessions_trusted_approves(self):
        """All sessions trusted, no resolver → auto-approve."""
        orch = _make_orchestrator(slack_enabled=False)
        ds = _mock_dashboard_state()
        ds._yolo = False
        session1 = MagicMock()
        session1._trust = True
        session1.running = False
        ds._sessions = {"s1": session1}
        orch.dashboard_state = ds
        callback = orch._interactive_approval("cron")
        event = MagicMock()
        event.request_id = "req-6"
        event.title = "cmd"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.sel.sel") as mock_sel:
                mock_sel.return_value.log_api_access = MagicMock()
                result = await callback(event, "")
        assert result is True

    @pytest.mark.asyncio
    async def test_auto_approve_sources_config(self):
        """Source in auto_approve_sources config → auto-approve."""
        cfg = AppConfig()
        cfg.hooks = {"auto_approve_sources": ["cron"]}
        with patch.object(cfg, "load_credentials", return_value={}):
            orch = GatewayOrchestrator(cfg)
        orch.dashboard_state = _mock_dashboard_state()
        orch.dashboard_state._yolo = False
        orch.dashboard_state._sessions = {}
        callback = orch._interactive_approval("cron")
        event = MagicMock()
        event.request_id = "req-7"
        event.title = "auto"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _deliver_result
# ═══════════════════════════════════════════════════════════════════════════


class TestDeliverResult:
    """Result routing to various surfaces."""

    @pytest.mark.asyncio
    async def test_silent_logs_only(self):
        orch = _make_orchestrator()
        orch._channel_delivery = _mock_channel_delivery()
        orch.dashboard_state = _mock_dashboard_state()
        await orch._deliver_result("Title", "summary", "result", "silent")
        orch.dashboard_state.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_dashboard_new_session(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.append = MagicMock()
        ds.get_or_create_session = MagicMock(return_value=session)
        orch.dashboard_state = ds
        await orch._deliver_result("Title", "task", "result", "dashboard")
        session.append.assert_called_once()
        ds.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_dashboard_specific_session(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.append = MagicMock()
        session.key = "my-session"
        ds.resolve_session = MagicMock(return_value=session)
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("Title", "task", "result", "dashboard:my-session")
        session.append.assert_called_once()

    @pytest.mark.asyncio
    async def test_dashboard_session_not_found(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        ds.resolve_session = MagicMock(return_value=None)
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("Title", "task", "result", "dashboard:gone")
        ds.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_dm_delivery(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        orch.dashboard_state = None
        await orch._deliver_result("Title", "task", "result", "channel")
        orch._channel_delivery.open_dm.assert_awaited()
        orch._channel_delivery.deliver_notification.assert_awaited()

    @pytest.mark.asyncio
    async def test_channel_thread_delivery(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        orch.dashboard_state = _mock_dashboard_state()
        await orch._deliver_result("Title", "task", "result", "channel:C123:1234.5678")
        orch._channel_delivery.deliver_notification.assert_awaited_once()
        # thread_ts is threaded through as the 4th positional arg
        call_args = orch._channel_delivery.deliver_notification.call_args
        assert call_args[0][0] == "C123"
        assert call_args[0][3] == "1234.5678"

    @pytest.mark.asyncio
    async def test_default_deliver_channel_and_dashboard(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds
        await orch._deliver_result("Title", "task", "result", "")
        orch._channel_delivery.deliver_notification.assert_awaited()
        ds.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_prompt_dashboard_session(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.key = "s1"
        session.enqueue_or_run_prompt = MagicMock(return_value=True)
        session.queue_depth = 0
        ds.resolve_session = MagicMock(return_value=session)
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("Title", "task", "result", "prompt:dashboard:s1")
        session.enqueue_or_run_prompt.assert_called_once()

    @pytest.mark.asyncio
    async def test_prompt_dashboard_session_not_found(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        ds.resolve_session = MagicMock(return_value=None)
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("Title", "task", "result", "prompt:dashboard:gone")
        ds.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_prompt_dashboard_queued(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.key = "s1"
        session.enqueue_or_run_prompt = MagicMock(return_value=False)
        session.queue_depth = 2
        ds.resolve_session = MagicMock(return_value=session)
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("Title", "task", "result", "prompt:dashboard:s1")
        ds.notify.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _shutdown
# ═══════════════════════════════════════════════════════════════════════════


class TestShutdown:
    """Graceful shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_with_no_services(self):
        orch = _make_orchestrator()
        await orch._shutdown()  # should not raise

    @pytest.mark.asyncio
    async def test_shutdown_stops_cron(self):
        orch = _make_orchestrator()
        orch.cron_svc = MagicMock()
        orch.cron_svc.stop = AsyncMock()
        orch.heartbeat_svc = MagicMock()
        orch.heartbeat_svc.stop = MagicMock()
        orch.inbox_svc = None
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.cancel_all = AsyncMock()
        orch.sessions = _mock_sessions()
        orch.dashboard_state = _mock_dashboard_state()
        orch._dashboard_runner = MagicMock()
        orch._dashboard_runner.cleanup = AsyncMock()
        await orch._shutdown()
        orch.cron_svc.stop.assert_awaited_once()
        orch.heartbeat_svc.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_handler_tasks(self):
        orch = _make_orchestrator()
        task = asyncio.create_task(asyncio.sleep(100))
        orch._handler_tasks.add(task)
        orch.cron_svc = None
        orch.heartbeat_svc = None
        orch.inbox_svc = None
        orch.subagent_mgr = None
        orch.sessions = None
        orch.dashboard_state = None
        orch._dashboard_runner = None
        await orch._shutdown()
        assert task.cancelled()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _check_for_updates
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckForUpdates:
    """Update check logic."""

    @pytest.mark.asyncio
    async def test_no_update_available(self):
        orch = _make_orchestrator()
        orch.dashboard_state = _mock_dashboard_state()
        with patch(
            "personalclaw.dashboard.handlers._do_update_check", new_callable=AsyncMock
        ):
            with patch(
                "personalclaw.dashboard.handlers._update_info", {"available": False}
            ):
                await orch._check_for_updates()

    @pytest.mark.asyncio
    async def test_update_available_no_auto(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds
        orch._auto_apply_update = AsyncMock()
        import personalclaw.dashboard.handlers as _h
        orig = _h._update_info.copy()
        # Create a config with auto_update=False
        fake_cfg = MagicMock()
        fake_cfg.auto_update = False
        try:
            _h._update_info.update({"available": True, "version": "9.9.9"})
            with patch.object(_h, "_do_update_check", new_callable=AsyncMock):
                with patch("personalclaw.config.AppConfig.load", return_value=fake_cfg):
                    await orch._check_for_updates()
        finally:
            _h._update_info.clear()
            _h._update_info.update(orig)
        orch._auto_apply_update.assert_not_awaited()
        ds.push_refresh.assert_called_with("update_available")

    @pytest.mark.asyncio
    async def test_update_check_exception_handled(self):
        orch = _make_orchestrator()
        with patch(
            "personalclaw.dashboard.handlers._do_update_check",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network"),
        ):
            await orch._check_for_updates()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _auto_apply_update
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoApplyUpdate:
    """Auto-update logic."""

    @pytest.mark.asyncio
    async def test_no_project_dir_returns_early(self):
        orch = _make_orchestrator()
        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": ""}, clear=False):
            await orch._auto_apply_update()  # should not raise

    @pytest.mark.asyncio
    async def test_non_main_branch_skips(self):
        orch = _make_orchestrator()
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"feat/test\n", b""))
        proc.returncode = 0
        with patch.dict(
            "os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}, clear=False
        ):
            with patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=proc,
            ) as mock_exec:
                await orch._auto_apply_update()
        # Branch gate: exactly one subprocess (branch detection) — no fetch.
        assert mock_exec.await_count == 1

    @pytest.mark.asyncio
    async def test_detached_head_coerced_to_main(self):
        """Detached HEAD (e.g. checked out at a release tag) is coerced to
        'main' and proceeds past the branch gate to the fetch step."""
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds

        call_count = [0]
        fetch_args: list[tuple] = []

        async def _fake_exec(*args, **kwargs):
            call_count[0] += 1
            proc = AsyncMock()
            if call_count[0] == 1:
                # branch detection → detached HEAD
                proc.communicate = AsyncMock(return_value=(b"HEAD\n", b""))
                proc.returncode = 0
            else:
                # fetch (fail it to stop the pipeline right after the gate)
                fetch_args.append(args)
                proc.communicate = AsyncMock(return_value=(b"", b"err"))
                proc.returncode = 1
            proc.wait = AsyncMock(return_value=proc.returncode)
            return proc

        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}):
            with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
                await orch._auto_apply_update()
        # Gate passed: fetch ran, targeting origin main.
        assert fetch_args and fetch_args[0][:4] == ("git", "fetch", "origin", "main")


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _check_missing_deps
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckMissingDeps:
    """Dep repair on gateway startup."""

    def test_check_missing_deps_no_missing(self):
        orch = _make_orchestrator()
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            orch._check_missing_deps()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_cron
# ═══════════════════════════════════════════════════════════════════════════


class TestInitCron:
    """Cron service initialization and callback."""

    @pytest.mark.asyncio
    async def test_init_cron_no_crons_flag(self):
        orch = _make_orchestrator(no_crons=True)
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()
        assert orch.cron_svc is not None
        mock_cs_inst.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_init_cron_starts_when_enabled(self):
        orch = _make_orchestrator(no_crons=False)
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()
        mock_cs_inst.start.assert_awaited_once()
        mock_cs_inst.start_reaper.assert_called_once()

    @pytest.mark.asyncio
    async def test_cron_callback_single_agent(self):
        """Cron callback runs single-agent path."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("full msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        # Extract the callback
        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "j1"
        job.name = "test-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="cron result",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:j1", "run task")):
                result = await callback(job)

        assert result == "cron result"
        assert job.last_result == "cron result"

    @pytest.mark.asyncio
    async def test_cron_callback_dedup_suppresses(self):
        """Duplicate result suppresses Slack delivery."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "j2"
        job.name = "dedup-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = _result_hash("stable output")
        job.consecutive_dupes = 1
        job.last_posted_at = time.time()
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="stable output",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:j2", "run")):
                with patch("personalclaw.sel.sel") as mock_sel:
                    mock_sel.return_value.log_tool_invocation = MagicMock()
                    result = await callback(job)

        assert result == "stable output"
        assert job.consecutive_dupes == 2
        # Cron result delivery should NOT have been called (suppressed)
        orch._channel_delivery.deliver_cron_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cron_callback_silent_suppresses(self):
        """Silent job suppresses delivery."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = None
        orch._channel_delivery = None

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "j3"
        job.name = "silent-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = ""
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = True
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="silent result",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:j3", "run")):
                with patch("personalclaw.sel.sel") as mock_sel:
                    mock_sel.return_value.log_tool_invocation = MagicMock()
                    result = await callback(job)

        assert result == "silent result"


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_subagents
# ═══════════════════════════════════════════════════════════════════════════


class TestInitSubagents:
    """Subagent manager initialization."""

    @pytest.mark.asyncio
    async def test_init_subagents_creates_manager(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.dashboard_state = _mock_dashboard_state()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst._max_concurrent = 10
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        assert orch.subagent_mgr is not None

    @pytest.mark.asyncio
    async def test_init_subagents_respects_max_concurrent(self):
        orch = _make_orchestrator()
        orch._cfg.agent.max_subagents = 5
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.dashboard_state = None
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst._max_concurrent = 5
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        assert orch.subagent_mgr._max_concurrent == 5


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_heartbeat
# ═══════════════════════════════════════════════════════════════════════════


class TestInitHeartbeat:
    """Heartbeat service initialization."""

    @pytest.mark.asyncio
    async def test_init_heartbeat_creates_service(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.consolidator = MagicMock()
        orch.dashboard_state = _mock_dashboard_state()
        with patch("personalclaw.gateway.HeartbeatService") as mock_hs:
            mock_hs_inst = MagicMock()
            mock_hs_inst.start = AsyncMock()
            mock_hs.return_value = mock_hs_inst
            await orch._init_heartbeat()
        assert orch.heartbeat_svc is not None
        mock_hs_inst.start.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _notif_meta
# ═══════════════════════════════════════════════════════════════════════════


class TestNotifMeta:
    """Notification metadata builder (channel deep links come from the
    ChannelDelivery seam's build_thread_link — core never builds vendor URLs)."""

    @staticmethod
    def _orch(with_delivery: bool = True):
        orch = _make_orchestrator()
        if with_delivery:
            delivery = _mock_channel_delivery()
            delivery.build_thread_link = MagicMock(
                side_effect=lambda chan, ts: f"https://chat.example/{chan}/{ts}"
            )
            orch._channel_delivery = delivery
        else:
            orch._channel_delivery = None
        return orch

    def test_none_for_empty_key(self):
        orch = self._orch()
        assert orch._notif_meta("") is None
        assert orch._notif_meta(None) is None

    def test_dashboard_session(self):
        result = self._orch()._notif_meta("dashboard:my-session")
        assert result == {"session": "my-session"}

    def test_channel_link_via_seam(self):
        orch = self._orch()
        result = orch._notif_meta("C123:1234.567890")
        assert result == {"channel_link": "https://chat.example/C123/1234.567890"}
        orch._channel_delivery.build_thread_link.assert_called_once_with(
            "C123", "1234.567890"
        )

    def test_channel_key_without_delivery_returns_none(self):
        assert self._orch(with_delivery=False)._notif_meta("C123:1.2") is None

    def test_cron_key_returns_none(self):
        assert self._orch()._notif_meta("cron:j1") is None

    def test_subagent_key_returns_none(self):
        assert self._orch()._notif_meta("subagent:a1") is None

    def test_hook_key_returns_none(self):
        assert self._orch()._notif_meta("hook:h1") is None


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_dashboard and _init_mcp_discovery
# ═══════════════════════════════════════════════════════════════════════════


class TestInitDashboard:
    """Dashboard initialization."""

    @pytest.mark.asyncio
    async def test_init_dashboard_creates_state(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.cron_svc = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.ctx_builder = MagicMock()
        orch.conv_log = MagicMock()
        orch.consolidator = MagicMock()
        orch._channel_delivery = None
        ds = _mock_dashboard_state()
        runner = MagicMock()
        with patch(
            "personalclaw.gateway.start_dashboard",
            new_callable=AsyncMock,
            return_value=(runner, ds),
        ):
            await orch._init_dashboard()
        assert orch.dashboard_state is ds
        assert orch._dashboard_runner is runner

    def test_init_mcp_discovery_logs(self):
        orch = _make_orchestrator()
        with patch("personalclaw.mcp_discovery.list_servers", return_value=[]):
            orch._init_mcp_discovery()  # should not raise

    def test_init_mcp_discovery_handles_error(self):
        orch = _make_orchestrator()
        with patch(
            "personalclaw.mcp_discovery.list_servers", side_effect=RuntimeError("fail")
        ):
            orch._init_mcp_discovery()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Volatile regex patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestVolatilePatterns:
    """Regex constants used in dedup."""

    def test_volatile_re_matches_iso_timestamp(self):
        assert _VOLATILE_RE.search("2026-05-14T10:30:00Z")

    def test_volatile_re_matches_uuid(self):
        assert _VOLATILE_RE.search("550e8400-e29b-41d4-a716-446655440000")

    def test_epoch_re_matches_10_digit(self):
        assert _EPOCH_RE.search("1715700000")

    def test_epoch_re_matches_13_digit(self):
        assert _EPOCH_RE.search("1715700000000")

    def test_constants_values(self):
        assert _MAX_INJECT_ATTEMPTS == 2
        assert _CRON_MSG_LIMIT == 3000
        assert _SUCCESS_REMINDER_SECS == 86400
        assert _FAILURE_REMINDER_SECS == 3600
        assert _EPOCH_WINDOW_SECS == 300


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Cron failure paths
# ═══════════════════════════════════════════════════════════════════════════


class TestCronFailurePaths:
    """Cron callback error handling."""

    @pytest.mark.asyncio
    async def test_cron_callback_failure_alerts(self):
        """First failure sends alert."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jfail"
        job.name = "fail-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0
        job._acp_retried = False

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jfail", "run")):
                with patch("personalclaw.sel.sel") as mock_sel:
                    mock_sel.return_value.log_tool_invocation = MagicMock()
                    with pytest.raises(RuntimeError, match="boom"):
                        await callback(job)

        orch._channel_delivery.deliver_text.assert_awaited()
        assert job.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_cron_callback_failure_dedup_suppresses(self):
        """Duplicate failure within window suppresses Slack."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jfail2"
        job.name = "fail-dedup"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        # Pre-set failure hash to match what will be generated
        job.last_failure_hash = _result_hash("RuntimeError: boom")
        job.last_failure_at = time.time()
        job.consecutive_failures = 1
        job._acp_retried = False

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jfail2", "run")):
                with patch("personalclaw.sel.sel") as mock_sel:
                    mock_sel.return_value.log_tool_invocation = MagicMock()
                    with pytest.raises(RuntimeError, match="boom"):
                        await callback(job)

        # Delivery should NOT be called (suppressed)
        orch._channel_delivery.deliver_text.assert_not_awaited()
        assert job.consecutive_failures == 2

    @pytest.mark.asyncio
    async def test_cron_multi_agent_sequence(self):
        """Multi-agent sequence runs agents sequentially."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = None
        orch._channel_delivery = None

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jmulti"
        job.name = "multi-agent"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = ["agent-a", "agent-b"]
        job.agent_id = None
        job.channel = ""
        job.created_by = ""
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="agent result",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jmulti", "run")):
                result = await callback(job)

        assert result == "agent result"
        assert job.last_result == "agent result"
        # get_or_create called twice (once per agent)
        assert orch.sessions.get_or_create.await_count == 2


# ═══════════════════════════════════════════════════════════════════════════
# Tests: run_gateway entry point
# ═══════════════════════════════════════════════════════════════════════════


class TestRunGateway:
    """Top-level run_gateway function."""

    @pytest.mark.asyncio
    async def test_run_gateway_creates_orchestrator(self):
        from personalclaw.gateway import run_gateway

        cfg = AppConfig()
        with patch.object(cfg, "load_credentials", return_value={}):
            with patch.object(
                GatewayOrchestrator, "run", new_callable=AsyncMock
            ) as mock_run:
                await run_gateway(cfg, no_dashboard=True, no_crons=True)
        mock_run.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_autonudge
# ═══════════════════════════════════════════════════════════════════════════


class TestInitAutonudge:
    """AutoNudge service initialization."""

    @pytest.mark.asyncio
    async def test_disabled_when_feature_flag_off(self):
        orch = _make_orchestrator()
        with patch("personalclaw.gateway.autonudge_enabled", return_value=False):
            await orch._init_autonudge()
        assert not hasattr(orch, "autonudge_svc") or orch.autonudge_svc is None  # noqa: E501

    @pytest.mark.asyncio
    async def test_enabled_creates_service(self):
        orch = _make_orchestrator()
        orch.dashboard_state = _mock_dashboard_state()
        with patch("personalclaw.gateway.autonudge_enabled", return_value=True):
            with patch("personalclaw.gateway.AutoNudgeService") as mock_ans:
                mock_inst = MagicMock()
                mock_inst.start = AsyncMock()
                mock_inst.subscribe = MagicMock()
                mock_ans.return_value = mock_inst
                await orch._init_autonudge()
        assert orch.autonudge_svc is not None
        mock_inst.start.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_inbox
# ═══════════════════════════════════════════════════════════════════════════


class TestInitInbox:
    """Inbox service initialization."""

    @pytest.mark.asyncio
    async def test_disabled_in_config_builds_service_without_provider(self):
        """Inbox is now Slack-INDEPENDENT: draft/classify/digest run over stored
        items through the bound chat model, so the service is ALWAYS constructed.
        ``inbox.enabled=False`` only means no message-source (poll) provider is
        attached — not that the service is absent. (Old behavior: inbox_svc is None
        when disabled — removed; _init_inbox reads self._cfg.inbox, not AppConfig.load.)"""
        orch = _make_orchestrator()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.sessions = _mock_sessions()
        orch.dashboard_state = None
        orch._cfg.inbox.enabled = False
        await orch._init_inbox()
        assert orch.inbox_svc is not None
        assert orch.inbox_svc._provider is None  # disabled → no poll source


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _auto_apply_update git path
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoApplyUpdateGitPath:
    """Git-based auto-update (non-platform)."""

    @pytest.mark.asyncio
    async def test_fetch_fails_returns_early(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds

        # branch detection succeeds, fetch fails
        call_count = [0]

        async def _fake_exec(*args, **kwargs):
            call_count[0] += 1
            proc = AsyncMock()
            if call_count[0] == 1:
                # branch detection
                proc.communicate = AsyncMock(return_value=(b"main\n", b""))
                proc.returncode = 0
            else:
                # fetch fails
                proc.communicate = AsyncMock(return_value=(b"", b"error"))
                proc.returncode = 1
            proc.wait = AsyncMock(return_value=proc.returncode)
            return proc

        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}):
            with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
                await orch._auto_apply_update()
        ds.clear_update_progress.assert_called()

    @pytest.mark.asyncio
    async def test_no_diff_returns_early(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds

        call_count = [0]

        async def _fake_exec(*args, **kwargs):
            call_count[0] += 1
            proc = AsyncMock()
            if call_count[0] == 1:
                # branch detection
                proc.communicate = AsyncMock(return_value=(b"main\n", b""))
                proc.returncode = 0
            elif call_count[0] == 2:
                # fetch succeeds
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                # diff --quiet returns 0 (no diff)
                proc.returncode = 0
            proc.wait = AsyncMock(return_value=proc.returncode)
            return proc

        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}):
            with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
                await orch._auto_apply_update()
        ds.clear_update_progress.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: run method (partial — covers init sequence)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunMethod:
    """Gateway run method."""

    @pytest.mark.asyncio
    async def test_run_raises_on_shutdown(self):
        """run() exits when shutdown_event is set."""
        import personalclaw

        orch = _make_orchestrator()

        # Mock all init methods
        orch._init_services = MagicMock()
        orch._init_cron = AsyncMock()
        orch._init_heartbeat = AsyncMock()
        orch._init_inbox = AsyncMock()
        orch._init_mcp_discovery = MagicMock()
        orch._init_subagents = MagicMock()
        orch._init_dashboard = AsyncMock()
        orch._init_autonudge = AsyncMock()
        orch._check_for_updates = AsyncMock()
        orch._shutdown = AsyncMock()

        # Set shutdown immediately
        personalclaw.shutdown_event.set()
        try:
            with patch("personalclaw.session.cleanup_orphaned_sessions"):
                with patch("personalclaw.dashboard.handlers._bg_mcp_probe", new_callable=AsyncMock):
                    with patch("os._exit"):
                        with patch("resource.getrlimit", return_value=(256, 10240)):
                            with patch("resource.setrlimit"):
                                await orch.run()
        finally:
            personalclaw.shutdown_event.clear()

        orch._init_services.assert_called_once()
        orch._init_cron.assert_awaited_once()
        orch._shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_no_dashboard_uses_api_server(self):
        """--no-dashboard uses _init_api_server."""
        import personalclaw

        orch = _make_orchestrator(no_dashboard=True)

        orch._init_services = MagicMock()
        orch._init_cron = AsyncMock()
        orch._init_heartbeat = AsyncMock()
        orch._init_inbox = AsyncMock()
        orch._init_mcp_discovery = MagicMock()
        orch._init_subagents = MagicMock()
        orch._init_dashboard = AsyncMock()
        orch._init_api_server = AsyncMock()
        orch._init_autonudge = AsyncMock()
        orch._check_for_updates = AsyncMock()
        orch._shutdown = AsyncMock()

        personalclaw.shutdown_event.set()
        try:
            with patch("personalclaw.session.cleanup_orphaned_sessions"):
                with patch("personalclaw.dashboard.handlers._bg_mcp_probe", new_callable=AsyncMock):
                    with patch("os._exit"):
                        with patch("resource.getrlimit", return_value=(256, 10240)):
                            with patch("resource.setrlimit"):
                                await orch.run()
        finally:
            personalclaw.shutdown_event.clear()

        orch._init_dashboard.assert_not_awaited()
        orch._init_api_server.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_api_server
# ═══════════════════════════════════════════════════════════════════════════


class TestInitApiServer:
    """API-only server initialization."""

    @pytest.mark.asyncio
    async def test_init_api_server(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.cron_svc = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch._channel_delivery = None
        ds = _mock_dashboard_state()
        runner = MagicMock()
        with patch(
            "personalclaw.dashboard.start_api_server",
            new_callable=AsyncMock,
            return_value=(runner, ds),
        ):
            await orch._init_api_server()
        assert orch.dashboard_state is ds


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Cron success reminder after 24h
# ═══════════════════════════════════════════════════════════════════════════


class TestCronSuccessReminder:
    """Cron dedup reminder after 24h."""

    @pytest.mark.asyncio
    async def test_success_reminder_after_24h(self):
        """After 24h of same result, re-posts with warning."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "j_remind"
        job.name = "reminder-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = _result_hash("same output")
        job.consecutive_dupes = 5
        # Posted more than 24h ago
        job.last_posted_at = time.time() - _SUCCESS_REMINDER_SECS - 100
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="same output",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:j_remind", "run")):
                result = await callback(job)

        # Should have posted (reminder path)
        orch._channel_delivery.deliver_cron_result.assert_awaited()
        assert "same result" in result


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _subagent_done callback (via _init_subagents)
# ═══════════════════════════════════════════════════════════════════════════


class TestSubagentDone:
    """Subagent completion routing."""

    def _setup_orch_with_subagent_mgr(self):
        """Create orchestrator with subagent manager initialized."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.dashboard_state = _mock_dashboard_state()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst.running = []
                mock_sm_inst.running_agents_for = MagicMock(return_value=[])
                mock_sm_inst.get = MagicMock(return_value=None)
                mock_sm_inst.notify_injection_failed = MagicMock()
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        return orch, mock_sm

    @pytest.mark.asyncio
    async def test_dashboard_session_idle_triggers_run_chat(self):
        """Subagent done → dashboard session idle → _run_chat."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        # Get the on_done callback
        on_done = mock_sm.call_args[1]["on_done"]

        session = MagicMock()
        session.running = False
        session.task = None
        session.key = "test-session"
        session.mode = ""
        session._recovery_chat_triggered = False
        session._pending_subagent_failures = []
        orch.dashboard_state.get_session = MagicMock(return_value=session)

        info = MagicMock()
        info.id = "agent-1"
        info.parent_session_key = "dashboard:test-session"
        info.error = None
        info.result = "done!"
        info.result_path = ""
        info.task = "do something"
        info.agent = "coder"
        info.silent = False
        info.elapsed = 5.0
        info.started = 0.0

        with patch("personalclaw.dashboard.chat_runner._run_chat", new_callable=AsyncMock, return_value=None):
            await on_done(info)

        orch.dashboard_state.notify.assert_called()
        orch.dashboard_state.push_sessions_update.assert_called()

    @pytest.mark.asyncio
    async def test_dashboard_session_busy_queues(self):
        """Subagent done → dashboard session busy → queues message."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]

        session = MagicMock()
        session.running = True
        # Create a task that completes but session stays running
        never_done = asyncio.get_event_loop().create_future()
        never_done.set_result(None)
        session.task = asyncio.ensure_future(asyncio.sleep(0))
        await session.task  # let it complete
        # But session.running stays True (simulating another claim)
        session.running = True
        session.key = "busy-session"
        session.mode = ""
        session._recovery_chat_triggered = False
        session._pending_subagent_failures = []
        session.queue_append = MagicMock()
        orch.dashboard_state.get_session = MagicMock(return_value=session)

        info = MagicMock()
        info.id = "agent-2"
        info.parent_session_key = "dashboard:busy-session"
        info.error = None
        info.result = "queued result"
        info.result_path = ""
        info.task = "task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        await on_done(info)
        session.queue_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_cron_parent_injects_result(self):
        """Subagent done → cron parent → injects into session."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]
        orch.subagent_mgr.running = []

        info = MagicMock()
        info.id = "agent-3"
        info.parent_session_key = "cron:job1"
        info.error = None
        info.result = "cron agent result"
        info.result_path = ""
        info.task = "cron task"
        info.agent = ""
        info.silent = False
        info.elapsed = 2.0
        info.started = 0.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="llm response",
        ):
            await on_done(info)

        orch.dashboard_state.notify.assert_called()

    @pytest.mark.asyncio
    async def test_notification_only_for_unknown_parent(self):
        """Subagent done → unknown parent → notification only."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]

        info = MagicMock()
        info.id = "agent-4"
        info.parent_session_key = "subagent:parent"
        info.error = "something failed"
        info.result = None
        info.result_path = ""
        info.task = "failed task"
        info.agent = ""
        info.silent = False
        info.elapsed = 0.5
        info.started = 0.0

        await on_done(info)
        orch.dashboard_state.notify.assert_called()

    @pytest.mark.asyncio
    async def test_silent_subagent_no_notification(self):
        """Silent subagent → no dashboard notification."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]

        info = MagicMock()
        info.id = "agent-5"
        info.parent_session_key = "subagent:x"
        info.error = None
        info.result = "silent"
        info.result_path = ""
        info.task = "quiet task"
        info.agent = ""
        info.silent = True
        info.elapsed = 1.0
        info.started = 0.0

        await on_done(info)
        orch.dashboard_state.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_slack_parent_injects(self):
        """Subagent done → Slack parent → injects into session."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]
        orch._channel_delivery = _mock_channel_delivery()

        info = MagicMock()
        info.id = "agent-6"
        info.parent_session_key = "C123:1234.567890"
        info.error = None
        info.result = "slack result"
        info.result_path = ""
        info.task = "slack task"
        info.agent = ""
        info.silent = False
        info.elapsed = 3.0
        info.started = time.monotonic() - 3.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="synthesized response",
        ):
            await on_done(info)

        orch.dashboard_state.notify.assert_called()

    @pytest.mark.asyncio
    async def test_dashboard_session_gone_notification_only(self):
        """Subagent done → dashboard session gone → notification only."""
        orch, mock_sm = self._setup_orch_with_subagent_mgr()
        on_done = mock_sm.call_args[1]["on_done"]
        orch.dashboard_state.get_session = MagicMock(return_value=None)

        info = MagicMock()
        info.id = "agent-7"
        info.parent_session_key = "dashboard:gone-session"
        info.error = None
        info.result = "orphan result"
        info.result_path = ""
        info.task = "orphan task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        await on_done(info)
        orch.dashboard_state.notify.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _interactive_approval Slack path
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractiveApprovalSlack:
    """Channel-based approval (approve/reject via the delivery seam).

    The Slack block-building + pending-approval registry tests
    (``_build_approval_blocks``/``_pending_approvals``/``update_message``)
    moved to the slack-channel app's test_delivery — core only sees the
    high-level ``ChannelDelivery.request_approval`` outcome now.
    """

    @pytest.mark.asyncio
    async def test_channel_approval_approved(self):
        """Channel request_approval returns True → approved."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        orch._channel_delivery.request_approval = AsyncMock(return_value=True)
        orch.sessions = _mock_sessions()
        orch.sessions.get_channel = MagicMock(return_value=None)
        orch.sessions.get_thread = MagicMock(return_value=None)
        ds = _mock_dashboard_state()
        ds._yolo = False
        ds._sessions = {}
        orch.dashboard_state = ds

        callback = orch._interactive_approval("subagent")
        event = MagicMock()
        event.request_id = "req-slack-1"
        event.title = "bash: echo hello"
        event.tool_input = ""
        event.tool_purpose = ""

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")

        assert result is True
        orch._channel_delivery.request_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_approval_rejects(self):
        """Channel request_approval returns False → rejected."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        orch._channel_delivery.request_approval = AsyncMock(return_value=False)
        orch.sessions = _mock_sessions()
        orch.sessions.get_channel = MagicMock(return_value=None)
        orch.sessions.get_thread = MagicMock(return_value=None)
        ds = _mock_dashboard_state()
        ds._yolo = False
        ds._sessions = {}
        orch.dashboard_state = ds

        callback = orch._interactive_approval("cron")
        event = MagicMock()
        event.request_id = "req-slack-2"
        event.title = "dangerous"
        event.tool_input = ""
        event.tool_purpose = ""

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")

        assert result is False
        orch._channel_delivery.request_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_approval_exception_falls_to_dashboard(self):
        """Channel approval raises → falls back to dashboard approval."""
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch._channel_delivery = _mock_channel_delivery()
        orch._channel_delivery.request_approval = AsyncMock(
            side_effect=RuntimeError("slack down")
        )
        orch.sessions = _mock_sessions()
        ds = _mock_dashboard_state()
        ds._yolo = False
        ds._sessions = {}
        ds.request_approval = AsyncMock(return_value=True)
        orch.dashboard_state = ds

        callback = orch._interactive_approval("heartbeat")
        event = MagicMock()
        event.request_id = "req-slack-3"
        event.title = "cmd"
        event.tool_input = ""
        event.tool_purpose = ""

        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            result = await callback(event, "")

        assert result is True
        ds.request_approval.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Heartbeat callback
# ═══════════════════════════════════════════════════════════════════════════


class TestHeartbeatCallback:
    """Heartbeat task execution callback."""

    @pytest.mark.asyncio
    async def test_heartbeat_task_success(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.consolidator = MagicMock()
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()
        orch._deliver_result = AsyncMock()

        with patch("personalclaw.gateway.HeartbeatService") as mock_hs:
            mock_hs_inst = MagicMock()
            mock_hs_inst.start = AsyncMock()
            mock_hs.return_value = mock_hs_inst
            await orch._init_heartbeat()

        # Get the on_task callback
        callback = mock_hs.call_args[1]["on_task"]

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="heartbeat done",
        ):
            result = await callback("check status", "")

        assert result == "heartbeat done"
        orch._deliver_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_heartbeat_task_keep_response(self):
        """HEARTBEAT_KEEP response suppresses delivery."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.consolidator = MagicMock()
        orch.dashboard_state = None
        orch._deliver_result = AsyncMock()

        with patch("personalclaw.gateway.HeartbeatService") as mock_hs:
            mock_hs_inst = MagicMock()
            mock_hs_inst.start = AsyncMock()
            mock_hs.return_value = mock_hs_inst
            await orch._init_heartbeat()

        callback = mock_hs.call_args[1]["on_task"]

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="still checking HEARTBEAT_KEEP",
        ):
            result = await callback("poll endpoint", "dashboard:s1")

        assert "HEARTBEAT_KEEP" in result
        orch._deliver_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_heartbeat_task_failure(self):
        """Heartbeat task exception propagates."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.consolidator = MagicMock()
        orch.dashboard_state = None

        with patch("personalclaw.gateway.HeartbeatService") as mock_hs:
            mock_hs_inst = MagicMock()
            mock_hs_inst.start = AsyncMock()
            mock_hs.return_value = mock_hs_inst
            await orch._init_heartbeat()

        callback = mock_hs.call_args[1]["on_task"]

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=RuntimeError("llm error"),
        ):
            with pytest.raises(RuntimeError, match="llm error"):
                await callback("broken task", "")


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _auto_apply_update venv path
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoApplyUpdateVenvPath:
    """Venv-based auto-update (pip install -e .)."""

    @staticmethod
    def _fake_exec_factory(call_count, *, pip_rc: int = 0):
        """Subprocess fake for the auto-apply pipeline call sequence:
        1 branch detection → main, 2 fetch, 3 diff --quiet (rc=1: has
        changes), 4 status --porcelain (clean), 5 reset --hard,
        6 pip install -e . (``pip_rc``)."""

        async def _fake_exec(*args, **kwargs):
            call_count[0] += 1
            proc = AsyncMock()
            proc.kill = MagicMock()
            if call_count[0] == 1:
                proc.communicate = AsyncMock(return_value=(b"main\n", b""))
                proc.returncode = 0
            elif call_count[0] == 2:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count[0] == 3:
                proc.returncode = 1  # diff --quiet → has changes
            elif call_count[0] == 4:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            elif call_count[0] == 5:
                proc.returncode = 0  # git reset --hard
            elif call_count[0] == 6:
                # pip install -e .
                proc.communicate = AsyncMock(return_value=(b"", b"boom" if pip_rc else b""))
                proc.returncode = pip_rc
            else:
                proc.returncode = 0
            proc.wait = AsyncMock(return_value=proc.returncode)
            return proc

        return _fake_exec

    @pytest.mark.asyncio
    async def test_venv_update_full_path_reaches_restart(self):
        """Full venv update: fetch, diff, reset, pip install, frontend build,
        then the graceful re-exec is REACHED (guards the old dead tail whose
        swallowed importlib.reload NameError meant os.execv never ran and
        every auto-update kept the old code running)."""
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds
        orch.sessions = _mock_sessions()

        call_count = [0]
        reexec = AsyncMock()

        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}):
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=self._fake_exec_factory(call_count),
            ):
                with patch("personalclaw.gateway.build_frontend_async", new_callable=AsyncMock):
                    with patch(
                        "personalclaw.dashboard.handlers.updates._graceful_reexec", reexec
                    ):
                        # execv must NOT be the restart path when a dashboard
                        # state exists — if it is reached the test fails loudly.
                        with patch("os.execv", side_effect=AssertionError("direct execv")):
                            await orch._auto_apply_update()

        ds.push_update_progress.assert_any_call("pulling", "Fetching latest changes…")
        ds.push_update_progress.assert_any_call("installing", "Installing package…")
        ds.push_update_progress.assert_any_call("building", "Building frontend…")
        ds.push_update_progress.assert_any_call("restarting", "Restarting server…")
        reexec.assert_awaited_once_with(ds)

    @pytest.mark.asyncio
    async def test_pip_failure_aborts_before_restart(self):
        """pip install failure must NOT restart into a broken env — the
        pipeline pushes an error step and keeps the current image running."""
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds
        orch.sessions = _mock_sessions()

        call_count = [0]
        reexec = AsyncMock()

        with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/tmp/proj"}):
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=self._fake_exec_factory(call_count, pip_rc=1),
            ):
                with patch(
                    "personalclaw.gateway.build_frontend_async", new_callable=AsyncMock
                ) as fe_build:
                    with patch(
                        "personalclaw.dashboard.handlers.updates._graceful_reexec", reexec
                    ):
                        with patch("os.execv", side_effect=AssertionError("direct execv")):
                            await orch._auto_apply_update()

        ds.push_update_progress.assert_any_call("error", "pip install failed")
        fe_build.assert_not_awaited()
        reexec.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Subagent channel injection timeout
# ═══════════════════════════════════════════════════════════════════════════


class TestSubagentSlackInjection:
    """Subagent injection into Slack sessions."""

    def _setup(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst.running = []
                mock_sm_inst.running_agents_for = MagicMock(return_value=[])
                mock_sm_inst.get = MagicMock(return_value=None)
                mock_sm_inst.notify_injection_failed = MagicMock()
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        return orch, mock_sm

    @pytest.mark.asyncio
    async def test_slack_injection_timeout_retries(self):
        """Channel injection timeout → retries then fails."""
        orch, mock_sm = self._setup()
        on_done = mock_sm.call_args[1]["on_done"]

        info = MagicMock()
        info.id = "agent-timeout"
        info.parent_session_key = "C123:ts.123"
        info.error = None
        info.result = "result"
        info.result_path = ""
        info.task = "task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ):
            await on_done(info)

        # Should have notified injection failed
        orch.subagent_mgr.notify_injection_failed.assert_called()

    @pytest.mark.asyncio
    async def test_cron_injection_timeout(self):
        """Cron injection timeout → notifies failure."""
        orch, mock_sm = self._setup()
        on_done = mock_sm.call_args[1]["on_done"]
        orch.subagent_mgr.running = []

        info = MagicMock()
        info.id = "agent-cron-timeout"
        info.parent_session_key = "cron:job1"
        info.error = None
        info.result = "result"
        info.result_path = ""
        info.task = "cron task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ):
            await on_done(info)

        orch.subagent_mgr.notify_injection_failed.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _deliver_result truncation
# ═══════════════════════════════════════════════════════════════════════════


class TestDeliverResultTruncation:
    """Prompt truncation for large results."""

    @pytest.mark.asyncio
    async def test_prompt_truncates_large_result(self):
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.key = "s1"
        session.enqueue_or_run_prompt = MagicMock(return_value=True)
        session.queue_depth = 0
        ds.resolve_session = MagicMock(return_value=session)
        orch.dashboard_state = ds
        # Create a result larger than MAX_PROMPT_BYTES
        large_result = "x" * 200000
        with patch("personalclaw.gateway.sel") as mock_sel:
            mock_sel.return_value.log_api_access = MagicMock()
            await orch._deliver_result("T", "s", large_result, "prompt:dashboard:s1")
        session.enqueue_or_run_prompt.assert_called_once()
        # Verify the prompt was truncated
        call_args = session.enqueue_or_run_prompt.call_args[0]
        assert len(call_args[0].encode("utf-8")) <= 131072 + 100  # MAX_PROMPT_BYTES + overhead


# ═══════════════════════════════════════════════════════════════════════════
# Tests: embedding wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestEmbeddingWiring:
    """Embeddings resolve from the Settings > Models active binding."""

    @pytest.mark.asyncio
    async def test_embed_fn_from_active_binding(self):
        orch = _make_orchestrator()
        orch.vector_memory = MagicMock()
        orch.vector_memory.embed_fn = None
        with patch(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            return_value=lambda x: [0.0],
        ):
            from personalclaw.embedding_providers.registry import get_active_embed_fn
            fn = get_active_embed_fn()
            if fn:
                orch.vector_memory.embed_fn = fn
        assert orch.vector_memory.embed_fn is not None

    @pytest.mark.asyncio
    async def test_no_binding_leaves_embeddings_off(self):
        orch = _make_orchestrator()
        orch.vector_memory = MagicMock()
        orch.vector_memory.embed_fn = None
        with patch(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            return_value=None,
        ):
            from personalclaw.embedding_providers.registry import get_active_embed_fn
            fn = get_active_embed_fn()
            if fn:
                orch.vector_memory.embed_fn = fn
        assert orch.vector_memory.embed_fn is None


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# Tests: _interactive_approval with thread context
# ═══════════════════════════════════════════════════════════════════════════


class TestApprovalThreadContext:
    """Approval with parent thread context.

    (The Slack channel-vs-DM routing / block-building for a threaded approval
    prompt moved to the slack-channel app's test_delivery — that's now internal
    to ChannelDelivery.request_approval. Core only tests the trust fall-through
    below.)
    """

    @pytest.mark.asyncio
    async def test_scoped_trust_not_trusted(self):
        """Session exists but not trusted → falls through to interactive."""
        orch = _make_orchestrator(slack_enabled=False)
        ds = _mock_dashboard_state()
        ds._yolo = False
        session = MagicMock()
        session._trust = False
        session.running = False
        ds._sessions = {"my-session": session}
        ds.request_approval = AsyncMock(return_value=False)
        orch.dashboard_state = ds
        resolver = MagicMock(return_value="my-session")
        callback = orch._interactive_approval("subagent", session_resolver=resolver)
        event = MagicMock()
        event.request_id = "req-notrust"
        event.title = "cmd"
        event.tool_input = ""
        event.tool_purpose = ""
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.sel.sel") as mock_sel:
                mock_sel.return_value.log_api_access = MagicMock()
                result = await callback(event, "")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Cron ACP retry path
# ═══════════════════════════════════════════════════════════════════════════


class TestCronAcpRetry:
    """Cron ACP process death retry."""

    @pytest.mark.asyncio
    async def test_acp_retry_on_process_death(self):
        """ACP error with 'not running' triggers retry."""
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = None

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jacp"
        job.name = "acp-retry"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = ""
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0
        job._acp_retried = False

        from personalclaw.acp.client import AcpError

        call_count = [0]

        async def _fake_stream(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise AcpError("process not running")
            return "retry success"

        with patch("personalclaw.gateway.stream_and_collect", side_effect=_fake_stream):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jacp", "run")):
                result = await callback(job)

        assert result == "retry success"


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Subagent _inject_with_retry paths
# ═══════════════════════════════════════════════════════════════════════════


class TestInjectWithRetry:
    """_inject_with_retry error handling."""

    def _setup(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.dashboard_state = _mock_dashboard_state()
        orch._channel_delivery = _mock_channel_delivery()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst.running = []
                mock_sm_inst.running_agents_for = MagicMock(return_value=[])
                mock_sm_inst.get = MagicMock(return_value=None)
                mock_sm_inst.notify_injection_failed = MagicMock()
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        return orch, mock_sm

    @pytest.mark.asyncio
    async def test_acp_process_died_during_injection(self):
        """AcpProcessDied during injection → resets session."""
        from personalclaw.acp.client import AcpProcessDied

        orch, mock_sm = self._setup()
        on_done = mock_sm.call_args[1]["on_done"]

        info = MagicMock()
        info.id = "agent-died"
        info.parent_session_key = "C123:ts.1"
        info.error = None
        info.result = "result"
        info.result_path = ""
        info.task = "task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=AcpProcessDied("dead"),
        ):
            await on_done(info)

        orch.subagent_mgr.notify_injection_failed.assert_called()

    @pytest.mark.asyncio
    async def test_prompt_busy_exhausted(self):
        """PromptBusyExhaustedError → resets session."""
        from personalclaw.llm_helpers import PromptBusyExhaustedError

        orch, mock_sm = self._setup()
        on_done = mock_sm.call_args[1]["on_done"]

        info = MagicMock()
        info.id = "agent-busy"
        info.parent_session_key = "C123:ts.2"
        info.error = None
        info.result = "result"
        info.result_path = ""
        info.task = "task"
        info.agent = ""
        info.silent = False
        info.elapsed = 1.0
        info.started = 0.0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            side_effect=PromptBusyExhaustedError("exhausted"),
        ):
            await on_done(info)

        orch.subagent_mgr.notify_injection_failed.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_autonudge _fire callback
# ═══════════════════════════════════════════════════════════════════════════


class TestAutonudgeFire:
    """AutoNudge fire callback."""

    @pytest.mark.asyncio
    async def test_fire_no_dashboard(self):
        """Fire with no dashboard → returns False."""
        orch = _make_orchestrator()
        orch.dashboard_state = None
        with patch("personalclaw.gateway.autonudge_enabled", return_value=True):
            with patch("personalclaw.gateway.AutoNudgeService") as mock_ans:
                mock_inst = MagicMock()
                mock_inst.start = AsyncMock()
                mock_inst.subscribe = MagicMock()
                mock_ans.return_value = mock_inst
                await orch._init_autonudge()

        # Get the on_fire callback
        on_fire = mock_ans.call_args[1]["on_fire"]
        loop = MagicMock()
        loop.id = "loop1"
        loop.session_name = "s1"
        loop.message = "nudge"
        loop.stop_sentinel_path = None
        loop.cycle_count = 0
        result = await on_fire(loop)
        assert result is False

    @pytest.mark.asyncio
    async def test_fire_session_missing(self):
        """Fire with missing session → removes loop."""
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        ds._sessions = {}
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.autonudge_enabled", return_value=True):
            with patch("personalclaw.gateway.AutoNudgeService") as mock_ans:
                mock_inst = MagicMock()
                mock_inst.start = AsyncMock()
                mock_inst.subscribe = MagicMock()
                mock_inst.remove = AsyncMock()
                mock_ans.return_value = mock_inst
                await orch._init_autonudge()

        on_fire = mock_ans.call_args[1]["on_fire"]
        loop = MagicMock()
        loop.id = "loop2"
        loop.session_name = "gone"
        loop.message = "nudge"
        loop.stop_sentinel_path = None
        loop.cycle_count = 0
        result = await on_fire(loop)
        assert result is False

    @pytest.mark.asyncio
    async def test_fire_session_running_skips(self):
        """Fire with running session → returns False (skip)."""
        orch = _make_orchestrator()
        ds = _mock_dashboard_state()
        session = MagicMock()
        session.running = True
        session.key = "busy"
        ds._sessions = {"busy": session}
        orch.dashboard_state = ds
        with patch("personalclaw.gateway.autonudge_enabled", return_value=True):
            with patch("personalclaw.gateway.AutoNudgeService") as mock_ans:
                mock_inst = MagicMock()
                mock_inst.start = AsyncMock()
                mock_inst.subscribe = MagicMock()
                mock_ans.return_value = mock_inst
                await orch._init_autonudge()

        on_fire = mock_ans.call_args[1]["on_fire"]
        loop = MagicMock()
        loop.id = "loop3"
        loop.session_name = "busy"
        loop.message = "nudge"
        loop.stop_sentinel_path = None
        loop.cycle_count = 0
        result = await on_fire(loop)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_inbox enabled path
# ═══════════════════════════════════════════════════════════════════════════


class TestInitInboxEnabled:
    """Inbox service when enabled in config."""

    @pytest.mark.asyncio
    async def test_enabled_without_slack_uses_filesystem_source(self):
        """Inbox enabled but no Slack client → the service is still built and binds
        the FILESYSTEM message-source provider (Slack-independent by design). Old
        behavior — 'no-op without a Slack client, inbox_svc stays None' — was removed
        when inbox triage moved off the Slack dependency."""
        orch = _make_orchestrator()  # no slack
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.memory = MagicMock()
        orch.sessions = _mock_sessions()
        orch.dashboard_state = None
        orch._cfg.inbox.enabled = True
        await orch._init_inbox()
        assert orch.inbox_svc is not None
        # Enabled + no Slack → the default provider resolves to the filesystem source
        # (get_default_provider("filesystem")). If that provider isn't installed the
        # binding is None but the service still exists — either way it's not Slack.
        prov = orch.inbox_svc._provider
        assert prov is None or getattr(prov, "source_name", "") != "slack"


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _init_dashboard wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestInitDashboardWiring:
    """Dashboard wiring with slack and no_crons."""

    @pytest.mark.asyncio
    async def test_dashboard_wires_slack_client(self):
        # The gateway no longer wires a live Slack client into the dashboard —
        # the slack-channel app's transport sets dashboard_state.slack_client at
        # start_inbound. Core no longer passes any channel client to start_dashboard; the transport registers channel_delivery at start_inbound.
        orch = _make_orchestrator(slack_enabled=True)
        orch.sessions = _mock_sessions()
        orch.cron_svc = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.ctx_builder = MagicMock()
        orch.conv_log = MagicMock()
        orch.consolidator = MagicMock()
        ds = _mock_dashboard_state()
        runner = MagicMock()
        with patch(
            "personalclaw.gateway.start_dashboard",
            new_callable=AsyncMock,
            return_value=(runner, ds),
        ) as mock_start:
            await orch._init_dashboard()
        # Clean-break contract: start_dashboard has NO channel-client kwarg at
        # all — outbound goes through the registered ChannelDelivery.
        assert "slack_client" not in mock_start.call_args.kwargs
        assert ds.no_crons is False

    @pytest.mark.asyncio
    async def test_dashboard_no_crons_flag(self):
        orch = _make_orchestrator(no_crons=True)
        orch.sessions = _mock_sessions()
        orch.cron_svc = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.ctx_builder = MagicMock()
        orch.conv_log = MagicMock()
        orch.consolidator = MagicMock()
        orch._channel_delivery = None
        ds = _mock_dashboard_state()
        runner = MagicMock()
        with patch(
            "personalclaw.gateway.start_dashboard",
            new_callable=AsyncMock,
            return_value=(runner, ds),
        ):
            await orch._init_dashboard()
        assert ds.no_crons is True


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Cron with acked_items
# ═══════════════════════════════════════════════════════════════════════════


class TestCronAckedItems:
    """Cron callback with acked_items."""

    @pytest.mark.asyncio
    async def test_acked_items_appended_to_message(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        orch.dashboard_state = None
        orch._channel_delivery = None

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jack"
        job.name = "acked-job"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = ""
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = ["item1", "item2"]
        job.silent = True
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="acked result",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jack", "run")):
                with patch("personalclaw.sel.sel") as mock_sel:
                    mock_sel.return_value.log_tool_invocation = MagicMock()
                    result = await callback(job)

        assert result == "acked result"
        # Verify acked_items were passed to build_message
        call_args = orch.ctx_builder.build_message.call_args[0][0]
        assert "item1" in call_args
        assert "item2" in call_args


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _retrigger_recovery
# ═══════════════════════════════════════════════════════════════════════════


class TestRetriggerRecovery:
    """Recovery retrigger for queued subagent failures."""

    def _setup(self):
        orch = _make_orchestrator()
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.hooks = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.dashboard_state = _mock_dashboard_state()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            with patch("personalclaw.gateway.SubagentManager") as mock_sm:
                mock_sm_inst = MagicMock()
                mock_sm_inst.start_reaper = MagicMock()
                mock_sm_inst.running = []
                mock_sm_inst.running_agents_for = MagicMock(return_value=[])
                mock_sm_inst.get = MagicMock(return_value=None)
                mock_sm_inst.notify_injection_failed = MagicMock()
                mock_sm.return_value = mock_sm_inst
                orch._init_subagents()
        return orch, mock_sm

    @pytest.mark.asyncio
    async def test_subagent_event_injection_failed(self):
        """Subagent injection_failed event updates session."""
        orch, mock_sm = self._setup()
        on_event = mock_sm.call_args[1]["on_event"]

        session = MagicMock()
        session.append = MagicMock()
        session._pending_subagent_failures = []
        orch.dashboard_state.get_session = MagicMock(return_value=session)

        info = MagicMock()
        info.id = "agent-fail"
        info.parent_session_key = "dashboard:session1"
        info.task = "failed task"

        await on_event(
            "subagent_injection_failed",
            info,
            {"error": "timed out", "failure_msg": "Agent failed"},
        )

        session.append.assert_called_once()
        assert len(session._pending_subagent_failures) == 1
        orch.dashboard_state.push_sessions_update.assert_called()

    @pytest.mark.asyncio
    async def test_subagent_event_chunk(self):
        """Subagent chunk event broadcasts to subscribers."""
        orch, mock_sm = self._setup()
        on_event = mock_sm.call_args[1]["on_event"]

        info = MagicMock()
        info.id = "agent-chunk"
        info.parent_session_key = "dashboard:session1"

        await on_event("subagent_chunk", info, {"text": "partial"})
        orch.dashboard_state.broadcast_ws_subagent_subscribers.assert_called()

    @pytest.mark.asyncio
    async def test_subagent_event_status(self):
        """Generic subagent status event broadcasts to all."""
        orch, mock_sm = self._setup()
        on_event = mock_sm.call_args[1]["on_event"]

        info = MagicMock()
        info.id = "agent-status"
        info.parent_session_key = "dashboard:session1"

        await on_event("subagent_started", info, {})
        orch.dashboard_state.broadcast_ws.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: run() signal handling and bg session
# ═══════════════════════════════════════════════════════════════════════════


class TestRunSignalAndBgSession:
    """Run method signal handling and background session."""

    @pytest.mark.asyncio
    async def test_run_wires_active_embedding(self):
        """run() wires the embedding fn from the Settings > Models binding."""
        # no_dashboard=True so the bg-session task short-circuits the dashboard
        # branch (otherwise it races on _local_only/_dashboard_port set by the
        # mocked _init_dashboard).
        orch = _make_orchestrator(no_dashboard=True)

        orch._init_services = MagicMock()
        orch.vector_memory = MagicMock()
        orch._init_cron = AsyncMock()
        orch._init_heartbeat = AsyncMock()
        orch._init_inbox = AsyncMock()
        orch._init_mcp_discovery = MagicMock()
        orch._init_subagents = MagicMock()
        orch._init_dashboard = AsyncMock()
        orch._init_autonudge = AsyncMock()
        orch._init_api_server = AsyncMock()
        orch._check_for_updates = AsyncMock()
        orch._shutdown = AsyncMock()

        # Use a fresh asyncio.Event bound to this test's loop. The shared
        # module-level shutdown_event can be polluted by prior tests in full-file runs.
        fresh_event = asyncio.Event()
        fresh_event.set()
        with patch(
            "personalclaw.embedding_providers.registry.get_active_embed_fn",
            return_value=lambda x: [0.0],
        ) as mock_embed:
            with patch("personalclaw.shutdown_event", fresh_event):
                with patch("personalclaw.gateway.shutdown_event", fresh_event):
                    with patch("personalclaw.session.cleanup_orphaned_sessions"):
                        with patch("personalclaw.dashboard.handlers._bg_mcp_probe", new_callable=AsyncMock):
                            with patch("os._exit"):
                                with patch("resource.getrlimit", return_value=(256, 10240)):
                                    with patch("resource.setrlimit"):
                                        await orch.run()

        mock_embed.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Tests: _check_missing_deps pip install path
# ═══════════════════════════════════════════════════════════════════════════


class TestBgSessionDashboardBranch:
    """run() -> _start_bg_session dashboard URL printing path."""

    @pytest.mark.asyncio
    async def test_bg_session_prints_dashboard_url(self):
        """_start_bg_session prints dashboard URLs when not _no_dashboard."""
        orch = _make_orchestrator(no_dashboard=False, no_open=True)

        orch._init_services = MagicMock()
        orch.vector_memory = MagicMock()
        orch._init_cron = AsyncMock()
        orch._init_heartbeat = AsyncMock()
        orch._init_inbox = AsyncMock()
        orch._init_mcp_discovery = MagicMock()
        orch._init_subagents = MagicMock()
        orch._init_autonudge = AsyncMock()
        orch._check_for_updates = AsyncMock()
        orch._shutdown = AsyncMock()

        # Real-ish sessions stub so _start_bg_session passes the assert
        orch.sessions = MagicMock()
        orch.sessions.start_pool = AsyncMock()

        # Stub _init_dashboard to set the attributes _start_bg_session reads
        async def _init_dash():
            orch._local_only = True
            orch._configured_host = None
            orch._dashboard_port = 6779
        orch._init_dashboard = _init_dash

        fresh_event = asyncio.Event()
        fresh_event.set()
        with patch("personalclaw.embedding_providers.registry.get_active_embed_fn", return_value=None):
          with patch("personalclaw.shutdown_event", fresh_event):
            with patch("personalclaw.gateway.shutdown_event", fresh_event):
                with patch("personalclaw.gateway.resolve_dashboard_host",
                           return_value="127.0.0.1"):
                    with patch("personalclaw.gateway.build_dashboard_url",
                               return_value="http://127.0.0.1:6779/?t=tok"):
                        with patch("personalclaw.gateway.format_dashboard_urls",
                                   return_value=["url-line-1", "url-line-2"]):
                            with patch("personalclaw.session.cleanup_orphaned_sessions"):
                                with patch("personalclaw.dashboard.handlers._bg_mcp_probe", new_callable=AsyncMock):
                                    with patch("os._exit"):
                                        with patch("resource.getrlimit", return_value=(256, 10240)):
                                            with patch("resource.setrlimit"):
                                                await orch.run()
                                                # Let bg_session task drain
                                                await asyncio.sleep(0)
                                                await asyncio.sleep(0)

        orch.sessions.start_pool.assert_awaited_once_with(blocking=False)


class TestCheckMissingDepsPip:
    """Dep repair via pip install."""

    def test_pip_install_on_missing_dep(self):
        orch = _make_orchestrator()
        with patch("importlib.util.find_spec", return_value=None):
            with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/proj"}):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    orch._check_missing_deps()
                mock_run.assert_called_once()

    def test_pip_install_failure(self):
        orch = _make_orchestrator()
        with patch("importlib.util.find_spec", return_value=None):
            with patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": "/proj"}):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=1, stderr=b"error"
                    )
                    orch._check_missing_deps()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# Tests: Cron with Slack delivery failure
# ═══════════════════════════════════════════════════════════════════════════


class TestCronSlackDeliveryFailure:
    """Cron Slack delivery exception handling."""

    @pytest.mark.asyncio
    async def test_slack_delivery_exception_notifies_dashboard(self):
        orch = _make_orchestrator(slack_enabled=True, owner_id="U1")
        orch.sessions = _mock_sessions()
        orch.ctx_builder = MagicMock()
        orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
        orch.ctx_builder.hooks = MagicMock()
        orch.subagent_mgr = MagicMock()
        orch.subagent_mgr.running = []
        ds = _mock_dashboard_state()
        orch.dashboard_state = ds
        orch._channel_delivery = _mock_channel_delivery()
        orch._channel_delivery.deliver_cron_result = AsyncMock(
            side_effect=RuntimeError("channel error")
        )

        with patch("personalclaw.gateway.ScheduleService") as mock_cs:
            mock_cs_inst = MagicMock()
            mock_cs_inst.start = AsyncMock()
            mock_cs_inst.start_reaper = MagicMock()
            mock_cs_inst.register_active_session_key = MagicMock()
            mock_cs_inst.clear_active_session_key = MagicMock()
            mock_cs.return_value = mock_cs_inst
            await orch._init_cron()

        callback = mock_cs.call_args[1]["on_job"]

        job = MagicMock()
        job.id = "jslack"
        job.name = "slack-fail"
        job.persistent_session = True
        job.command = ""
        job.script = ""
        job.provider = "invoke-agent"
        job.agent_sequence = []
        job.agent_id = None
        job.channel = ""
        job.created_by = "U1"
        job.approval_mode = "auto"
        job.env = None
        job.acked_items = []
        job.silent = False
        job.thread_ts = None
        job.last_posted_hash = ""
        job.consecutive_dupes = 0
        job.last_posted_at = 0.0
        job.last_failure_hash = ""
        job.last_failure_at = 0.0
        job.consecutive_failures = 0

        with patch(
            "personalclaw.gateway.stream_and_collect",
            new_callable=AsyncMock,
            return_value="result",
        ):
            with patch("personalclaw.gateway.build_schedule_session_context", return_value=("cron:jslack", "run")):
                result = await callback(job)

        assert result == "result"
        # Dashboard should have been notified about the Slack failure
        assert ds.notify.call_count >= 2  # once for result, once for slack failure


class TestShutdownReapsAppBackends:
    """_shutdown() must reap app-backend subprocesses, and do it EARLY — before
    the ACP/session teardown that can hang for many seconds on a wedged delegate
    CLI. Otherwise an operator SIGKILLing the gateway during that window orphans
    the backends (the leak this guards against)."""

    @pytest.mark.asyncio
    async def test_shutdown_calls_stop_all_before_session_teardown(self):
        orch = _make_orchestrator(owner_id="U1")
        orch.dashboard_state = None  # skip the history-save branch
        orch._handler_tasks = []
        orch.loop_watchdog = None
        orch.cron_svc = None
        orch.heartbeat_svc = None
        orch._dashboard_runner = None
        orch._socket_client = None

        order: list[str] = []

        sup = MagicMock()
        sup.stop_all = MagicMock(side_effect=lambda: order.append("stop_all"))

        sessions = MagicMock()
        async def _close_all():
            order.append("sessions.close_all")
        sessions.close_all = _close_all
        orch.sessions = sessions
        orch.subagent_mgr = None

        with patch(
            "personalclaw.apps.backend_runtime.get_backend_supervisor",
            return_value=sup,
        ):
            await orch._shutdown()

        # stop_all ran, and ran BEFORE the (potentially slow) session teardown.
        assert "stop_all" in order, "app backends were never reaped on shutdown"
        assert order.index("stop_all") < order.index("sessions.close_all"), (
            "app-backend reap must precede ACP/session teardown so it isn't "
            "blocked behind a wedged delegate CLI"
        )

    @pytest.mark.asyncio
    async def test_shutdown_survives_stop_all_raising(self):
        """A supervisor failure must not abort the rest of shutdown."""
        orch = _make_orchestrator(owner_id="U1")
        orch.dashboard_state = None
        orch._handler_tasks = []
        orch.loop_watchdog = None
        orch.cron_svc = None
        orch.heartbeat_svc = None
        orch._dashboard_runner = None
        orch._socket_client = None
        orch.sessions = None
        orch.subagent_mgr = None

        sup = MagicMock()
        sup.stop_all = MagicMock(side_effect=RuntimeError("boom"))
        with patch(
            "personalclaw.apps.backend_runtime.get_backend_supervisor",
            return_value=sup,
        ):
            # Must not raise despite stop_all blowing up.
            await orch._shutdown()
        sup.stop_all.assert_called_once()
