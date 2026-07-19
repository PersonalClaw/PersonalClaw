"""Tests for cron job channel-delivery error handling.

Verifies that channel delivery failures do not mark the job as failed,
that dashboard notifications are redacted, and that failure-path channel
errors are also guarded.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.schedule import ScheduleJob, ScheduleDefinition, make_agent_action


def _make_gateway():
    """Build a minimal GatewayOrchestrator with mocked dependencies."""
    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.sessions = MagicMock()
    gw.sessions.get_pid = MagicMock(return_value=None)
    gw.ctx_builder = MagicMock()
    gw._channel_delivery = MagicMock()
    gw._channel_delivery.open_dm = AsyncMock(return_value="D_U1")
    gw._channel_delivery.deliver_text = AsyncMock(return_value="1.0")
    gw._channel_delivery.deliver_cron_result = AsyncMock(return_value="1.0")
    gw._channel_delivery.deliver_notification = AsyncMock(return_value="1.0")
    gw._channel_delivery.deliver_subagent_reply = AsyncMock()
    gw._channel_delivery.request_approval = AsyncMock(return_value=True)
    gw.conv_log = None
    gw.dashboard_state = MagicMock()
    gw._owner_id = "U000"
    gw.subagent_mgr = None
    gw._cron_injecting = {}
    gw._no_crons = False
    gw.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    gw.sessions.release = MagicMock()
    gw.sessions.reset = AsyncMock()
    gw.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    gw.ctx_builder.hooks = MagicMock()
    gw._interactive_approval = MagicMock(return_value="cb")
    return gw


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


def _run_callback(gw, job, stream_result="done", stream_side_effect=None):
    """Init cron on the gateway, capture the callback, and invoke it."""
    captured_cb = None

    async def fake_stream(client, msg, **kwargs):
        if stream_side_effect:
            raise stream_side_effect
        return stream_result

    with patch("personalclaw.gateway.stream_and_collect", fake_stream), patch(
        "personalclaw.gateway.ScheduleService"
    ) as mock_cron_cls:

        def capture_cron(on_job=None, **kw):
            nonlocal captured_cb
            captured_cb = on_job
            svc = MagicMock()
            svc.start = AsyncMock()
            return svc

        mock_cron_cls.side_effect = capture_cron

        async def _init_and_run():
            await gw._init_cron()
            assert captured_cb is not None
            return await captured_cb(job)

        return asyncio.run(_init_and_run())


class TestChannelDeliveryFailureDoesNotFailJob:
    """Channel delivery throwing should not mark the cron job as failed."""

    def test_job_returns_result_when_delivery_throws(self) -> None:
        gw = _make_gateway()
        gw._channel_delivery.deliver_cron_result = AsyncMock(side_effect=Exception("not_in_channel"))
        job = _make_job()
        result = _run_callback(gw, job)
        assert result == "done"

    def test_dashboard_gets_delivery_failure_notification(self) -> None:
        gw = _make_gateway()
        gw._channel_delivery.deliver_cron_result = AsyncMock(side_effect=Exception("channel_not_found"))
        job = _make_job()
        _run_callback(gw, job)
        calls = gw.dashboard_state.notify.call_args_list
        # First call: success notification, second: delivery failure warning.
        # The dashboard notification body is a PClaw-UI surface — emoji-free
        # (see the no-emoji status-sentinel removal); the warning is plain text.
        assert len(calls) == 2
        assert "channel delivery failed" in calls[1].args[2]
        assert "channel_not_found" in calls[1].args[2]

    def test_job_succeeds_when_delivery_is_none(self) -> None:
        gw = _make_gateway()
        gw._channel_delivery = None
        job = _make_job()
        result = _run_callback(gw, job)
        assert result == "done"


class TestDashboardNotificationRedaction:
    """Dashboard notify must redact result_text."""

    def test_dashboard_notify_calls_redaction(self) -> None:
        gw = _make_gateway()
        gw._channel_delivery = None  # skip channel-delivery path
        job = _make_job()

        with patch("personalclaw.gateway.redact_exfiltration_urls") as mock_url, patch(
            "personalclaw.gateway.redact_credentials"
        ) as mock_cred:
            mock_url.return_value = ("redacted_url", False)
            mock_cred.return_value = ("fully_redacted", False)
            _run_callback(gw, job, stream_result="secret http://evil.com data")

        mock_url.assert_called()
        mock_cred.assert_called()
        body = gw.dashboard_state.notify.call_args.args[2]
        assert body == "fully_redacted"


class TestFailurePathDeliveryGuarded:
    """The except-block channel notification should not raise if delivery throws."""

    def test_failure_path_delivery_error_does_not_propagate(self) -> None:
        gw = _make_gateway()
        gw._channel_delivery.deliver_text = AsyncMock(side_effect=Exception("channel_down"))
        job = _make_job()
        with pytest.raises(RuntimeError, match="job broke"):
            _run_callback(gw, job, stream_side_effect=RuntimeError("job broke"))
        gw._channel_delivery.deliver_text.assert_awaited_once()


class TestSilentJobFailurePath:
    """Silent jobs (all app-manifest crons) must not attempt channel delivery
    on the failure path — their created_by is an ``app:<name>`` pseudo-user
    that open_dm can never resolve."""

    def test_silent_job_failure_skips_channel_delivery(self) -> None:
        gw = _make_gateway()
        job = _make_job(channel=None, created_by="app:demo", silent=True)
        with pytest.raises(RuntimeError, match="job broke"):
            _run_callback(gw, job, stream_side_effect=RuntimeError("job broke"))
        gw._channel_delivery.open_dm.assert_not_awaited()
        gw._channel_delivery.deliver_text.assert_not_awaited()

    def test_loud_job_failure_still_delivers(self) -> None:
        gw = _make_gateway()
        job = _make_job(channel=None, created_by="U123", silent=False)
        with pytest.raises(RuntimeError, match="job broke"):
            _run_callback(gw, job, stream_side_effect=RuntimeError("job broke"))
        gw._channel_delivery.open_dm.assert_awaited_once_with("U123")
        gw._channel_delivery.deliver_text.assert_awaited_once()
