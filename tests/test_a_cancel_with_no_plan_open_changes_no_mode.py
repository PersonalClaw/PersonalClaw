"""Cancel plan mode with no plan open leaves the chat's mode as it is (#5792).

Cancel shows while a plan drafts, and the task-mode pill can leave plan mode under it. The pill
drops the plan, but a panel drawn before that, in this tab or another, still offers Cancel, and
Cancel restored the mode the plan was opened from, or Agent when none was recorded. So a chat the
pill had just set to Ask, read-only, went to Agent on that click. With no plan open, Cancel now
changes nothing and answers with the chat's mode.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app, _seed
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_plan


@pytest.mark.parametrize("pill", ["ask", "agent"])
@pytest.mark.asyncio
async def test_a_cancel_after_the_pill_left_plan_mode_keeps_the_pills_mode(tmp_path, pill):
    state = _make_state(tmp_path)
    chat = _seed(state)
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        await client.post("/api/chat/task-mode", json={"mode": pill, "session": "c1"})
        assert chat_plan.read("c1") == (None, {})
        r = await client.post("/api/chat/sessions/c1/plan/cancel")
        body = await r.json()
    assert (r.status, body["task_mode"], chat._task_mode) == (200, pill, pill)


@pytest.mark.asyncio
async def test_a_cancel_with_a_plan_open_still_restores_the_earlier_mode(tmp_path):
    state = _make_state(tmp_path)
    chat = _seed(state)
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/task-mode", json={"mode": "ask", "session": "c1"})
        await client.post("/api/chat/sessions/c1/plan/activate")
        assert chat._task_mode == "plan"
        r = await client.post("/api/chat/sessions/c1/plan/cancel")
        body = await r.json()
    assert (r.status, body["task_mode"], chat._task_mode) == (200, "ask", "ask")
    assert chat_plan.read("c1") == (None, {})
