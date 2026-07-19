"""Schedule-trigger PATCH forwards the canonical action to the update_job kwarg.

A schedule trigger's agent now rides the canonical ``action`` (invoke-agent's
``config.agent``), not a top-level ``agent`` key. ``_update_schedule`` forwards the
whole ``action`` dict to the schedule service's ``action`` kwarg so an agent change
persists. These tests lock that mapping.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_trigger_detail
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action


def _make_request(body: dict, raw_id: str = "abc123") -> MagicMock:
    mock_state = MagicMock()
    mock_state.crons.update_job.return_value = ScheduleJob(
        id=raw_id, name="t", action=make_agent_action(message="m"), schedule=ScheduleDefinition(kind="every", every_secs=300)
    )
    mock_state.crons.is_running.return_value = False
    mock_state.crons.running_since.return_value = None
    mock_state._sessions = {}

    request = MagicMock()
    request.app = {"state": mock_state}
    request.method = "PUT"
    request.match_info = {"id": f"schedule:{raw_id}"}
    request.json = AsyncMock(return_value=body)
    return request


def _agent_action(agent: str, task: str = "m", approval_mode: str = "") -> dict:
    config = {"task_template": task, "agent": agent}
    if approval_mode:
        config["approval_mode"] = approval_mode
    return {"action": {"provider": "invoke-agent", "config": config}}


class TestScheduleTriggerUpdateAgent:
    @pytest.mark.asyncio
    async def test_action_agent_forwarded_in_action_kwarg(self):
        request = _make_request(_agent_action("bxt-brain-leader"))
        resp = await api_trigger_detail(request)
        assert resp.status == 200
        update_job = request.app["state"].crons.update_job
        update_job.assert_called_once()
        _, kwargs = update_job.call_args
        assert kwargs["action"]["config"]["agent"] == "bxt-brain-leader"
        assert "agent_id" not in kwargs  # the canonical action carries the agent

    @pytest.mark.asyncio
    async def test_action_persisted_on_job(self):
        request = _make_request(_agent_action("worker"))
        resp = await api_trigger_detail(request)
        assert resp.status == 200
        # The canonical action is written back onto the job.
        assert request.app["state"].crons.update_job.return_value.action["provider"] == "invoke-agent"

    @pytest.mark.asyncio
    async def test_other_fields_patched_alongside_action(self):
        body = {"name": "renamed", "channel": "C0AP77JJSN6", "silent": True}
        body.update(_agent_action("bxt-brain-leader", approval_mode="auto"))
        request = _make_request(body)
        resp = await api_trigger_detail(request)
        assert resp.status == 200
        _, kwargs = request.app["state"].crons.update_job.call_args
        assert kwargs.get("name") == "renamed"
        assert kwargs["action"]["config"]["agent"] == "bxt-brain-leader"
        assert kwargs["action"]["config"]["approval_mode"] == "auto"
        assert kwargs.get("channel") == "C0AP77JJSN6"
        assert kwargs.get("silent") is True

    @pytest.mark.asyncio
    async def test_name_only_patch_does_not_touch_agent(self):
        request = _make_request({"name": "renamed"})
        resp = await api_trigger_detail(request)
        assert resp.status == 200
        _, kwargs = request.app["state"].crons.update_job.call_args
        assert "action" not in kwargs

    @pytest.mark.asyncio
    async def test_job_not_found_returns_404(self):
        request = _make_request(_agent_action("bxt-brain-leader"), raw_id="missing")
        request.app["state"].crons.update_job.return_value = None
        resp = await api_trigger_detail(request)
        assert resp.status == 404
