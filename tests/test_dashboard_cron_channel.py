"""Channel validation on schedule-trigger create (the unified /api/triggers facade).

Since #3686 a schedule's ``channel`` names a chat channel set up here, or ``<channel>:<target>``
for a chat on it, and each channel checks its own ids; the old rule was one platform's id shape.
"""

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
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
        # A schedule that runs an agent needs the owner's yes to the grant (#3712), sent with the
        # create; the channel is what these tests are about.
        "confirm": True,
    }
    body.update(extra)
    return body


class _CodeChat(ChannelTransportProvider):
    """A chat channel whose channels are codes like ``C0AP77JJSN6``."""

    name = "codechat"
    display_name = "CodeChat"

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def validate_target(self, target: str) -> str:
        return "" if re.fullmatch(r"[CDGW][A-Z0-9]+", target or "") else "A CodeChat id is C0123."


@pytest.fixture(autouse=True)
def _codechat():
    channel_transports.register_transport(_CodeChat())
    yield
    channel_transports.unregister_transport("codechat")


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
        request.json = AsyncMock(return_value=_schedule_body(channel="codechat:C0AP77JJSN6"))
        resp = await api_trigger_create(request)
        assert resp.status == 200
        # 🔴 SUPERSEDED CONTRACT (S101 write re-point): the channel is `delivery` on the store row
        # (LEGACY_FIELD_MAP: `channel → delivery`), not an `add_job` kwarg.
        from personalclaw.dashboard.handlers.triggers import _trigger_store

        trigger = _trigger_store().get("clock:test").trigger
        assert trigger.delivery == "channel:codechat:C0AP77JJSN6"

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
