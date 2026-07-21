"""Tests for cron → Slack thread routing.

Verifies that _cron_callback stores thread_ts after posting, and that
_subagent_done routes cron-spawned subagent results via session injection.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action
from personalclaw.subagent import SubagentInfo

# ── Helpers (same pattern as test_cron_slack_delivery.py) ──


def _make_gateway():
    from personalclaw.gateway import GatewayOrchestrator

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.sessions = MagicMock()
    gateway.sessions.get_pid = MagicMock(return_value=None)
    gateway.ctx_builder = MagicMock()
    gateway._channel_delivery = MagicMock()
    gateway._channel_delivery.open_dm = AsyncMock(return_value="D_U1")
    gateway._channel_delivery.deliver_text = AsyncMock(return_value="1.0")
    gateway._channel_delivery.deliver_cron_result = AsyncMock(return_value="1.0")
    gateway._channel_delivery.deliver_notification = AsyncMock(return_value="1.0")
    gateway._channel_delivery.deliver_subagent_reply = AsyncMock()
    gateway._channel_delivery.request_approval = AsyncMock(return_value=True)
    gateway.conv_log = None
    gateway.dashboard_state = MagicMock()
    gateway._owner_id = "U000"
    gateway._cron_injecting = {}
    gateway._no_crons = False
    gateway.subagent_mgr = MagicMock()
    gateway.subagent_mgr.running = []
    gateway.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    gateway.sessions.release = MagicMock()
    gateway.sessions.reset = AsyncMock()
    gateway.sessions.cancel_current = AsyncMock()
    gateway.sessions.set_thread = AsyncMock()
    gateway.sessions.set_channel = AsyncMock()
    gateway.sessions.get_thread = MagicMock(return_value=None)
    gateway.sessions.get_channel = MagicMock(return_value=None)
    gateway.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    gateway.ctx_builder.hooks = MagicMock()
    gateway._interactive_approval = MagicMock(return_value="cb")
    gateway._cfg = MagicMock()
    gateway._cfg.agent.max_subagents = 4
    return gateway


def _make_job(**overrides):
    defaults = dict(
        id="j1",
        name="test-job",
        action=make_agent_action(message="go", approval_mode="auto"),
        schedule=ScheduleDefinition(kind="every", every_secs=300),
        channel="C123",
    )
    defaults.update(overrides)
    return ScheduleJob(**defaults)


def _run_callback(gateway, job, stream_result="done"):
    captured_callback = None

    async def fake_stream(client, msg, **kwargs):
        return stream_result

    with (
        patch("personalclaw.gateway.stream_and_collect", fake_stream),
        patch("personalclaw.gateway.ScheduleService") as mock_cron_cls,
    ):

        def capture_cron(on_job=None, **kw):
            nonlocal captured_callback
            captured_callback = on_job
            service = MagicMock()
            service.start = AsyncMock()
            return service

        mock_cron_cls.side_effect = capture_cron

        async def _init_and_run():
            await gateway._init_cron()
            assert captured_callback is not None
            return await captured_callback(job)

        return asyncio.run(_init_and_run())


def _capture_subagent_done(gateway):
    """Init subagents on the gateway and return the captured _subagent_done."""
    captured_done = None

    with patch("personalclaw.gateway.SubagentManager") as mock_mgr_cls:

        def capture_manager(**kw):
            nonlocal captured_done
            captured_done = kw["on_done"]
            mgr = MagicMock()
            mgr.running = []
            gateway.subagent_mgr = mgr
            return mgr

        mock_mgr_cls.side_effect = capture_manager
        gateway._init_subagents()

    assert captured_done is not None
    return captured_done


# ── Tests: _cron_callback stores thread_ts ──


class TestCronCallbackStoresThread:
    """_cron_callback must store thread_ts and channel after posting to Slack."""

    def test_stores_thread_ts_after_post(self) -> None:
        gateway = _make_gateway()
        gateway._channel_delivery.deliver_cron_result = AsyncMock(return_value="1711957800.001234")
        _run_callback(gateway, _make_job())
        gateway.sessions.set_thread.assert_awaited_once_with("cron:j1", "1711957800.001234")

    def test_stores_channel_after_post(self) -> None:
        gateway = _make_gateway()
        gateway._channel_delivery.deliver_cron_result = AsyncMock(return_value="1711957800.001234")
        _run_callback(gateway, _make_job(channel="C999"))
        gateway.sessions.set_channel.assert_awaited_once_with("cron:j1", "C999")

    def test_skips_storage_when_post_returns_none(self) -> None:
        gateway = _make_gateway()
        gateway._channel_delivery.deliver_cron_result = AsyncMock(return_value=None)
        _run_callback(gateway, _make_job())
        gateway.sessions.set_thread.assert_not_awaited()


# ── Tests: _subagent_done injects cron results via session ──


class TestSubagentDoneCronRouting:
    """_subagent_done must inject cron subagent results into the cron session."""

    @pytest.mark.asyncio
    async def test_injects_into_cron_session(self) -> None:
        gateway = _make_gateway()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(
            id="sub1",
            task="analyse data",
            parent_session_key="cron:j1",
        )
        info.result = "analysis complete"
        info.done = True
        with (
            patch("personalclaw.gateway.redact_exfiltration_urls", return_value=("", False)),
            patch("personalclaw.gateway.redact_credentials", return_value=("", False)),
            patch(
                "personalclaw.gateway.stream_and_collect",
                new_callable=AsyncMock,
                return_value="injected",
            ),
        ):
            await subagent_done(info)

        gateway.sessions.get_or_create.assert_awaited()
        gateway.sessions.release.assert_called_once_with("cron:j1")

    @pytest.mark.asyncio
    async def test_resets_session_when_last_subagent(self) -> None:
        gateway = _make_gateway()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(
            id="sub2",
            task="check status",
            parent_session_key="cron:j1",
        )
        info.result = "all good"
        info.done = True
        with (
            patch("personalclaw.gateway.redact_exfiltration_urls", return_value=("", False)),
            patch("personalclaw.gateway.redact_credentials", return_value=("", False)),
            patch(
                "personalclaw.gateway.stream_and_collect",
                new_callable=AsyncMock,
                return_value="done",
            ),
        ):
            await subagent_done(info)

        gateway.sessions.reset.assert_awaited_once_with("cron:j1")

    @pytest.mark.asyncio
    async def test_injection_failure_does_not_propagate(self) -> None:
        gateway = _make_gateway()
        gateway.sessions.get_or_create = AsyncMock(side_effect=Exception("session_down"))
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(
            id="sub3",
            task="risky task",
            parent_session_key="cron:j1",
        )
        info.result = "done"
        info.done = True
        with (
            patch("personalclaw.gateway.redact_exfiltration_urls", return_value=("", False)),
            patch("personalclaw.gateway.redact_credentials", return_value=("", False)),
        ):
            # Should not raise
            await subagent_done(info)
        gateway.dashboard_state.notify.assert_called()

    @pytest.mark.asyncio
    async def test_retries_on_acp_error(self) -> None:
        """stream_and_collect retries on AcpError then succeeds."""
        from personalclaw.acp.client import AcpError

        gateway = _make_gateway()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="sub4", task="retry task", parent_session_key="cron:j1")
        info.result = "ok"
        info.done = True

        call_count = 0

        async def flaky_stream(client, msg, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AcpError("transient 500")
            return "recovered"

        with (
            patch("personalclaw.gateway.redact_exfiltration_urls", return_value=("", False)),
            patch("personalclaw.gateway.redact_credentials", return_value=("", False)),
            patch("personalclaw.gateway.stream_and_collect", side_effect=flaky_stream),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            await subagent_done(info)

        assert call_count == 2
        mock_sleep.assert_awaited_once_with(1)  # 2 ** 0
        gateway.sessions.cancel_current.assert_any_await("cron:j1")


# ── Tests: cancel_current called before release in _subagent_done ──


class TestSubagentDoneCancelsBeforeRelease:
    """_subagent_done must call cancel_current before release to prevent
    'Prompt already in progress' when wait_for cancels mid-stream."""

    def _patches(self):
        return (
            patch("personalclaw.gateway.redact_exfiltration_urls", return_value=("", False)),
            patch("personalclaw.gateway.redact_credentials", return_value=("", False)),
        )

    @pytest.mark.asyncio
    async def test_cron_injection_cancels_before_release(self) -> None:
        gateway = _make_gateway()
        call_order: list[str] = []
        gateway.sessions.cancel_current = AsyncMock(
            side_effect=lambda k: call_order.append("cancel")
        )
        gateway.sessions.release = MagicMock(side_effect=lambda k: call_order.append("release"))
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="s1", task="work", parent_session_key="cron:j1")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with (
            patch(
                "personalclaw.gateway.stream_and_collect", new_callable=AsyncMock, return_value="ok"
            ),
            p1,
            p2,
        ):
            await subagent_done(info)
        assert call_order == ["cancel", "release"]

    @pytest.mark.asyncio
    async def test_slack_injection_cancels_before_release(self) -> None:
        gateway = _make_gateway()
        gateway._channel_delivery.open_dm = AsyncMock(return_value="D123")
        gateway._channel_delivery.deliver_subagent_reply = AsyncMock()
        call_order: list[str] = []
        gateway.sessions.cancel_current = AsyncMock(
            side_effect=lambda k: call_order.append("cancel")
        )
        gateway.sessions.release = MagicMock(side_effect=lambda k: call_order.append("release"))
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="s3", task="work", parent_session_key="slack:U000")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with (
            patch(
                "personalclaw.gateway.stream_and_collect", new_callable=AsyncMock, return_value="ok"
            ),
            p1,
            p2,
        ):
            await subagent_done(info)
        assert call_order == ["cancel", "release"]

    @pytest.mark.asyncio
    async def test_cancel_failure_does_not_prevent_release(self) -> None:
        """If cancel_current raises, release must still be called."""
        gateway = _make_gateway()
        gateway.sessions.cancel_current = AsyncMock(side_effect=Exception("cancel failed"))
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="s4", task="work", parent_session_key="cron:j1")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with (
            patch(
                "personalclaw.gateway.stream_and_collect", new_callable=AsyncMock, return_value="ok"
            ),
            p1,
            p2,
        ):
            await subagent_done(info)
        gateway.sessions.release.assert_called_once_with("cron:j1")


# ── Tests: dashboard/channel injection silently loses results on AcpError ──


class TestDashboardInjectionRoutesRunChat:
    """Dashboard subagent injection routes through _run_chat for full streaming."""

    def _patches(self):
        return (
            patch(
                "personalclaw.gateway.redact_exfiltration_urls", side_effect=lambda s: (s, False)
            ),
            patch("personalclaw.gateway.redact_credentials", side_effect=lambda s: (s, False)),
        )

    @pytest.mark.asyncio
    async def test_routes_through_run_chat_when_idle(self) -> None:
        """Idle session triggers _run_chat with the announce message."""
        gateway = _make_gateway()
        session = MagicMock()
        session.running = False
        session.key = "chat-1-123"
        session.task = None
        gateway.dashboard_state.get_session = MagicMock(return_value=session)
        gateway.dashboard_state._background_tasks = set()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="ar1", task="review code", parent_session_key="dashboard:chat-1-123")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with (
            patch("personalclaw.gateway._run_chat", new_callable=AsyncMock),
            p1,
            p2,
        ):
            await subagent_done(info)

        assert session.task is not None, "session.task must be set for running indicator"

    @pytest.mark.asyncio
    async def test_notification_when_slot_gone(self) -> None:
        """Missing session falls through to notification-only path."""
        gateway = _make_gateway()
        gateway.dashboard_state.get_session = MagicMock(return_value=None)
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="ar3", task="review code", parent_session_key="dashboard:chat-1-789")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with p1, p2:
            await subagent_done(info)

        gateway.dashboard_state.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_busy_slot_awaits_then_injects(self) -> None:
        """Busy session is awaited, then _run_chat starts after it finishes."""
        from unittest.mock import PropertyMock

        gateway = _make_gateway()
        session = MagicMock()
        _done_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        _done_future.set_result(None)
        # First access: True (enters busy branch), second access: False (re-check passes)
        type(session).running = PropertyMock(side_effect=[True, False])
        session.task = _done_future
        session.key = "chat-1-busy"
        gateway.dashboard_state.get_session = MagicMock(return_value=session)
        gateway.dashboard_state._background_tasks = set()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="busy1", task="work", parent_session_key="dashboard:chat-1-busy")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        _mock_run_chat = AsyncMock(return_value=None)
        with (
            patch("personalclaw.gateway._run_chat", _mock_run_chat),
            p1,
            p2,
        ):
            await subagent_done(info)

        # _run_chat should have been triggered
        assert _mock_run_chat.called, "_run_chat must be called after busy session becomes idle"
        assert (
            session.task is not _done_future
        ), "session.task must be reassigned to the new _run_chat task"

    @pytest.mark.asyncio
    async def test_busy_slot_timeout_queues_result(self) -> None:
        """If busy session doesn't finish within timeout, result is queued."""
        gateway = _make_gateway()
        session = MagicMock()
        session.running = True  # stays busy even after timeout
        # Task that never completes
        _stuck = asyncio.get_running_loop().create_future()
        session.task = _stuck
        session.key = "chat-1-stuck"
        gateway.dashboard_state.get_session = MagicMock(return_value=session)
        gateway.dashboard_state._background_tasks = set()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="stuck1", task="work", parent_session_key="dashboard:chat-1-stuck")
        info.result = "done"
        info.done = True
        p1, p2 = self._patches()
        with (
            patch("personalclaw.gateway.INJECTION_TIMEOUT", 0.01),
            p1,
            p2,
        ):
            await subagent_done(info)

        session.queue_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_callback_notifies_with_redacted_reason(self) -> None:
        """_on_inject_done calls notify_injection_failed with redacted reason."""
        gateway = _make_gateway()
        session = MagicMock()
        session.running = False
        session.key = "chat-1-err"
        session.task = None
        gateway.dashboard_state.get_session = MagicMock(return_value=session)
        gateway.dashboard_state._background_tasks = set()
        subagent_done = _capture_subagent_done(gateway)
        info = SubagentInfo(id="err1", task="work", parent_session_key="dashboard:chat-1-err")
        info.result = "done"
        info.done = True

        # Make _run_chat raise an error
        _mock_run_chat = AsyncMock(side_effect=RuntimeError("provider crashed"))
        p1, p2 = self._patches()
        with (
            patch("personalclaw.gateway._run_chat", _mock_run_chat),
            p1,
            p2,
        ):
            await subagent_done(info)
            # Let the task's done callbacks fire
            await asyncio.sleep(0.1)

        gateway.subagent_mgr.notify_injection_failed.assert_called_once()
        call_kwargs = gateway.subagent_mgr.notify_injection_failed.call_args
        assert "provider crashed" in str(call_kwargs)
