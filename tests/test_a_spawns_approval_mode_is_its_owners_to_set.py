"""A subagent started over ``POST /api/spawn`` asks as its owner's own settings say.

The route took ``approval_mode`` from the request body and handed it to the spawn, and ``"auto"``
let the subagent approve its own tool calls. Every agent's tool reaches the route with the
gateway's internal credential, so any caller could start a subagent that asked nobody, whatever
the owner had set. What lets a subagent approve its own calls is consent given for that run (a
workflow step's saved posture, a trigger's step, an app's scheduled job), passed in-process; a
request names none, and one that tries is refused, saying why, with nothing started.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_api_server import _make_api_app, _make_state


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.spawn.return_value = MagicMock(id="sa-1", done=False, error="")
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "interactive", "AUTO"])
async def test_a_request_that_names_an_approval_mode_is_refused(tmp_path, mode):
    manager = _manager()
    state = _make_state(tmp_path, subagents=manager)
    async with TestClient(TestServer(_make_api_app(state))) as client:
        resp = await client.post(
            "/api/spawn", json={"task": "tidy the notes", "approval_mode": mode}
        )

        assert resp.status == 400, await resp.text()
        error = (await resp.json())["error"]
    assert error["code"] == "approval_mode_not_accepted", error
    assert "your own approval settings" in error["message"], error
    manager.spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"approval_mode": ""}])
async def test_a_spawn_runs_under_its_owners_settings(tmp_path, body):
    manager = _manager()
    state = _make_state(tmp_path, subagents=manager)
    async with TestClient(TestServer(_make_api_app(state))) as client:
        resp = await client.post("/api/spawn", json={"task": "tidy the notes", **body})

        assert resp.status == 200, await resp.text()
    (call,) = manager.spawn.call_args_list
    assert not call.kwargs.get("approval_mode"), call.kwargs
