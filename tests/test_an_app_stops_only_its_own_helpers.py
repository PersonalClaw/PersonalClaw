"""An app stops and clears only the helpers its own work started.

An app whose manifest declares ``/api/spawn`` may stop a helper (``DELETE /api/spawn/{id}``),
clear the finished ones (``DELETE /api/spawn``) and stop a whole fan-out
(``POST /api/spawn/cancel-fanout``). None of the three asked whose helper it was, so the app
stopped or cleared any chat's: your own chats', a Temporary or Incognito chat's, a run's. Its stop
was recorded as yours (``SubagentInfo.stopped_by_you``), so the helper's report started no turn,
and a 404 against a 200 told it which helper ids exist.

Now the three keep to the reach reading a helper keeps (``subagent_reach``): an app acts on its own
work's helpers, another chat's answers as an id that never existed does and is left as it was, and
an app's stop is the app's, named in how the helper ended. You stop and clear every one, as before.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.subagent import SubagentInfo

APP = "garden-notes"
CHAT = "dashboard:chat-1-1700000001"
YOURS_RUNNING, YOURS_DONE, YOURS_SECOND = "a1000000", "a2000000", "a3000000"
APPS_RUNNING, APPS_DONE, APPS_STEP = "e1000000", "e2000000", "e3000000"
NEVER = "0badc0de"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """The gateway's helper table: your chat's two waiting helpers and a finished one, and the
    app's own waiting, finished and run helpers."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.subagent import SubagentManager

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "subagents"
    )
    manager = SubagentManager(sessions=MagicMock(), ctx_builder=None)
    app_key = f"app:{APP}"
    for agent_id, parent, done, run in (
        (YOURS_RUNNING, CHAT, False, ""),
        (YOURS_SECOND, CHAT, False, ""),
        (YOURS_DONE, CHAT, True, ""),
        (APPS_RUNNING, app_key, False, ""),
        (APPS_DONE, app_key, True, ""),
        (APPS_STEP, app_key, False, "workflow:app-run-1"),
    ):
        manager._agents[agent_id] = SubagentInfo(
            id=agent_id,
            task=f"task {agent_id}",
            parent_session_key=parent,
            parent_run=run,
            done=done,
            queued=not done,
            result="Done." if done else "",
            app=APP if parent == app_key else "",
        )
    dashboard = DashboardState(sessions=MagicMock(count=0), start_time=0.0, subagents=manager)
    dashboard.push_sessions_update = MagicMock()
    dashboard.get_or_create_session(CHAT.split(":", 1)[1], memory_mode="persistent")
    return dashboard


def _request(state, method: str, path: str, *, by: str, agent_id: str = "", body=None):
    served = web.Application()
    served["state"] = state
    request = make_mocked_request(
        method, path, match_info={"agent_id": agent_id} if agent_id else {}, app=served
    )
    if by == "app":
        request["app"] = APP
    else:
        request["user"] = "owner"
    if body is not None:

        async def _json():
            return body

        request.json = _json  # type: ignore[method-assign]
    return request


async def _stop(state, agent_id: str, *, by: str) -> web.Response:
    from personalclaw.dashboard.handlers.messaging import api_spawn_delete

    path = f"/api/spawn/{agent_id}"
    return await api_spawn_delete(_request(state, "DELETE", path, by=by, agent_id=agent_id))


@pytest.mark.asyncio
async def test_an_apps_stop_of_your_helper_answers_as_an_id_that_never_existed(state):
    """🔴 Red before: it answered 200 and stopped your chat's helper."""
    refused = await _stop(state, YOURS_RUNNING, by="app")
    missing = await _stop(state, NEVER, by="app")
    assert refused.status == missing.status == 404
    assert refused.body == missing.body
    info = state.subagents.get(YOURS_RUNNING)
    assert not info.done and not info.cancelled


@pytest.mark.asyncio
async def test_an_apps_refused_stop_is_written_down(state, monkeypatch):
    """🔴 Red before: nothing said an app reached for another chat's helper."""
    from personalclaw.dashboard.handlers import messaging

    sel = MagicMock()
    monkeypatch.setattr(messaging, "_sel", lambda: sel)
    await _stop(state, YOURS_RUNNING, by="app")
    [call] = sel.log_api_access.call_args_list
    assert call.kwargs["operation"] == "spawn.stop" and call.kwargs["outcome"] == "denied"
    assert call.kwargs["resources"] == f"subagent:{YOURS_RUNNING}"


@pytest.mark.asyncio
async def test_an_app_stops_its_own_helper_as_its_own_stop(state):
    """🔴 Red before: its stop was recorded as yours, so the helper's report started no turn."""
    stopped = await _stop(state, APPS_RUNNING, by="app")
    assert stopped.status == 200 and json.loads(stopped.body)["cancelled"] is True
    info = state.subagents.get(APPS_RUNNING)
    assert info.cancelled and not info.stopped_by_you
    assert APP in info.error


@pytest.mark.asyncio
async def test_an_apps_clear_clears_only_its_own_finished_helpers(state):
    """🔴 Red before: it cleared every chat's."""
    from personalclaw.dashboard.handlers.messaging import api_spawn_clear

    cleared = await api_spawn_clear(_request(state, "DELETE", "/api/spawn", by="app"))
    assert json.loads(cleared.body) == {"ok": True, "cleared": 1}
    assert state.subagents.get(APPS_DONE) is None
    assert state.subagents.get(YOURS_DONE) is not None


async def _stop_fanout(state, body: dict, *, by: str) -> web.Response:
    from personalclaw.dashboard.handlers.messaging import api_spawn_cancel_fanout

    path = "/api/spawn/cancel-fanout"
    return await api_spawn_cancel_fanout(_request(state, "POST", path, by=by, body=body))


@pytest.mark.asyncio
async def test_an_apps_stop_of_your_chats_fanout_stops_nothing(state):
    """🔴 Red before: it stopped every helper of your chat and marked the fan-out stopped."""
    refused = await _stop_fanout(state, {"parent_session": CHAT}, by="app")
    unknown = await _stop_fanout(state, {"parent_session": "dashboard:no-such-chat"}, by="app")
    assert json.loads(refused.body) == json.loads(unknown.body) == {"ok": True, "cancelled": 0}
    for agent_id in (YOURS_RUNNING, YOURS_SECOND):
        assert not state.subagents.get(agent_id).cancelled
    assert CHAT not in state.subagents._fanout_stops


@pytest.mark.asyncio
async def test_an_app_stops_its_own_runs_fanout_as_its_own_stop(state):
    """🔴 Red before: its stop was recorded as yours."""
    stopped = await _stop_fanout(state, {"parent_run": "workflow:app-run-1"}, by="app")
    assert json.loads(stopped.body) == {"ok": True, "cancelled": 1}
    info = state.subagents.get(APPS_STEP)
    assert info.cancelled and not info.stopped_by_you and APP in info.error
    assert not state.subagents.get(APPS_RUNNING).cancelled


@pytest.mark.asyncio
async def test_a_fanout_holding_your_helper_too_stops_nothing_for_an_app(state):
    """🔴 Red before: every helper of the fan-out was stopped, yours among them."""
    yours = state.subagents.get(YOURS_RUNNING)
    yours.parent_run = "workflow:app-run-1"
    stopped = await _stop_fanout(state, {"parent_run": "workflow:app-run-1"}, by="app")
    assert json.loads(stopped.body) == {"ok": True, "cancelled": 0}
    assert not yours.cancelled and not state.subagents.get(APPS_STEP).cancelled


@pytest.mark.asyncio
async def test_you_stop_and_clear_every_helper_as_before(state):
    """The positive control: your signed-in session reaches every chat's helpers, and your stop is
    your own."""
    from personalclaw.dashboard.handlers.messaging import api_spawn_clear

    stopped = await _stop(state, APPS_RUNNING, by="owner")
    assert stopped.status == 200 and state.subagents.get(APPS_RUNNING).stopped_by_you
    fanout = await _stop_fanout(state, {"parent_session": CHAT}, by="owner")
    assert json.loads(fanout.body) == {"ok": True, "cancelled": 2}
    assert all(state.subagents.get(i).stopped_by_you for i in (YOURS_RUNNING, YOURS_SECOND))
    cleared = await api_spawn_clear(_request(state, "DELETE", "/api/spawn", by="owner"))
    assert json.loads(cleared.body)["cleared"] == 5
    assert (await _stop(state, NEVER, by="owner")).status == 404
