"""A helper that was stopped says so, where its chat shows it (#3820).

A chat shows each helper a turn started under the call that started it, and a running one can be
stopped from there. A stop ends the helper with its reason as its error ("Cancelled by user"), so
the row read a stop as a failure. The ending says it was stopped: the ``subagent_done`` frame and
the status route (``GET /api/spawn/{id}``) carry ``stopped``, and a helper that failed on its own
does not.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.subagent_endings import done_event

STOPPED = "aaaa1111"
FAILED = "bbbb2222"


def _helper(agent_id: str, *, stopped: bool) -> SubagentInfo:
    return SubagentInfo(
        id=agent_id,
        task="Read up on LSM trees",
        parent_session_key="dashboard:chat-a",
        done=True,
        error="Cancelled by user" if stopped else "the provider timed out",
        cancelled=stopped,
    )


def test_the_done_frame_says_a_stopped_helper_was_stopped():
    assert done_event(_helper(STOPPED, stopped=True))["stopped"] is True
    assert done_event(_helper(FAILED, stopped=False))["stopped"] is False


@pytest.mark.asyncio
async def test_the_status_route_says_it_too(monkeypatch):
    from personalclaw.dashboard.handlers import messaging

    manager = SubagentManager(sessions=MagicMock(), ctx_builder=None)
    for agent_id, stopped in ((STOPPED, True), (FAILED, False)):
        manager._agents[agent_id] = _helper(agent_id, stopped=stopped)
    app = web.Application()
    app["state"] = SimpleNamespace(subagents=manager)
    # The chat reads its own helpers: who may read which is held by its own tests.
    monkeypatch.setattr(
        messaging, "reader_of", lambda _r, _s: SimpleNamespace(reads=lambda _k: True)
    )

    async def status(agent_id: str) -> dict:
        request = make_mocked_request(
            "GET", f"/api/spawn/{agent_id}", match_info={"agent_id": agent_id}, app=app
        )
        response = await messaging.api_spawn_status(request)
        assert response.status == 200
        return json.loads(response.body)

    assert (await status(STOPPED))["stopped"] is True
    assert (await status(FAILED))["stopped"] is False
