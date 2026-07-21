"""approval_mode / silent fields + list serialization on the unified Trigger facade."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_trigger_create, api_triggers
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action


def _action(task="m", approval_mode=""):
    config = {"task_template": task}
    if approval_mode:
        config["approval_mode"] = approval_mode
    return {"provider": "invoke-agent", "config": config}


class TestScheduleTriggerApprovalMode:
    def _make_request(self, body: dict) -> MagicMock:
        mock_state = MagicMock()
        mock_state.crons.add_job.return_value = ScheduleJob(
            id="abc",
            name="t",
            action=make_agent_action(message="m"),
            schedule=ScheduleDefinition(kind="every", every_secs=300),
        )
        mock_state.crons.is_running.return_value = False
        mock_state.crons.running_since.return_value = None
        mock_state._sessions = {}
        request = MagicMock()
        request.app = {"state": mock_state}
        request.get = lambda *a, **k: "dashboard"
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_valid_approval_mode_auto(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "action": _action(approval_mode="auto"),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200
        # add_job receives the action; the approval_mode is folded into it.
        call = request.app["state"].crons.add_job.call_args
        assert call.kwargs["action"]["config"]["approval_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_invalid_approval_mode_rejected(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "action": _action(approval_mode="evil"),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 400
        request.app["state"].crons.add_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_silent_flag_set(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "silent": True,
                "action": _action(),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200
        assert request.app["state"].crons.add_job.return_value.silent is True

    @pytest.mark.asyncio
    async def test_no_approval_mode_accepted(self):
        request = self._make_request(
            {"trigger_type": "schedule", "name": "t", "every": 300, "action": _action()}
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200


class TestTriggerListFields:
    @pytest.mark.asyncio
    async def test_schedule_trigger_serialization_includes_action_and_fields(self):
        mock_job = MagicMock()
        mock_job.id = "j1"
        mock_job.name = "test"
        mock_job.message = "msg"
        mock_job.enabled = True
        mock_job.last_status = "ok"
        mock_job.agent_id = ""
        mock_job.channel = "C123"
        mock_job.approval_mode = "auto"
        mock_job.silent = True
        mock_job.strict_schedule = False
        mock_job.schedule = ScheduleDefinition(kind="every", every_secs=300)
        mock_job.last_run_ts = None
        mock_job.last_result = None
        mock_job.last_error = None
        mock_job.created_ts = None
        mock_job.model = ""
        mock_job.timezone = ""
        mock_job.skip_dates = []
        mock_job.script = ""
        mock_job.command = ""
        mock_job.action = make_agent_action(message="msg", approval_mode="auto")

        mock_state = MagicMock()
        mock_state.crons.list_jobs.return_value = [mock_job]
        mock_state.crons.is_running.return_value = False
        mock_state.crons.running_since.return_value = None
        mock_state._sessions = {}

        request = MagicMock()
        request.app = {"state": mock_state}
        request.query = {"type": "schedule"}

        resp = await api_triggers(request)
        data = json.loads(resp.body)
        t = data["triggers"][0]
        assert t["kind"] == "schedule"
        assert t["id"] == "schedule:j1"
        assert t["approval_mode"] == "auto"
        assert t["silent"] is True
        assert t["channel"] == "C123"
        # The action derives from the invoke-agent exec mode when unset.
        assert t["action"]["provider"] == "invoke-agent"
