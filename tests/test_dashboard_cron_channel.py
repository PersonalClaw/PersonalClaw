"""Channel validation on schedule-trigger create (the unified /api/triggers facade)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_trigger_create
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action


def _real_job(**over):
    """A real ScheduleJob so the handler's full serialization doesn't choke on a mock."""
    base = dict(
        id="abc",
        name="t",
        action=make_agent_action(message="m"),
        schedule=ScheduleDefinition(kind="every", every_secs=300),
    )
    base.update(over)
    return ScheduleJob(**base)


def _schedule_body(**extra):
    body = {
        "trigger_type": "schedule",
        "name": "test",
        "every": 300,
        "action": {"provider": "invoke-agent", "config": {"task_template": "msg"}},
    }
    body.update(extra)
    return body


class TestScheduleTriggerChannel:
    @pytest.mark.asyncio
    async def test_valid_channel_accepted(self):
        mock_state = MagicMock()
        mock_state.crons.add_job.return_value = _real_job()
        mock_state.crons.is_running.return_value = False
        mock_state.crons.running_since.return_value = None
        mock_state._sessions = {}
        request = MagicMock()
        request.app = {"state": mock_state}
        request.get = lambda *a, **k: "dashboard"
        request.json = AsyncMock(return_value=_schedule_body(channel="C0AP77JJSN6"))
        resp = await api_trigger_create(request)
        assert resp.status == 200
        mock_state.crons.add_job.assert_called_once()
        call = mock_state.crons.add_job.call_args
        assert call.kwargs.get("channel") == "C0AP77JJSN6"

    @pytest.mark.asyncio
    async def test_invalid_channel_rejected(self):
        mock_state = MagicMock()
        request = MagicMock()
        request.app = {"state": mock_state}
        request.get = lambda *a, **k: "dashboard"
        request.json = AsyncMock(return_value=_schedule_body(channel="not-valid"))
        resp = await api_trigger_create(request)
        assert resp.status == 400
        mock_state.crons.add_job.assert_not_called()
