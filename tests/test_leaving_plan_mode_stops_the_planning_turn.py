"""Leaving plan mode while the plan's own turn runs stops that turn first (#5792).

The gate that keeps a planning turn from changing anything is the task mode, read at each tool
call. Cancel plan mode, or the task-mode pill set to another mode, restores the chat's earlier
mode, so a planning turn still running took that mode's tools for its remaining calls, and under
Trust or YOLO a change it asked for ran with no plan approved. Both doors now stop that turn while
the chat is still in plan mode, then leave it. A turn plan mode is only waiting on (it runs in its
own mode), a plan with no turn running, and a mode change with no plan open are left as they were.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app, _seed
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_plan


class _StopSeesTheMode:
    """``SessionManager.stop_turn`` recording the chat's task mode at the moment it is asked."""

    def __init__(self, chat) -> None:
        self.chat = chat
        self.modes: list[str] = []

    async def __call__(self, key, force=False):
        self.modes.append(getattr(self.chat, "_task_mode", ""))
        return "stopped"


def _running(state, chat) -> tuple[asyncio.Task, _StopSeesTheMode]:
    stop = _StopSeesTheMode(chat)
    state.sessions.stop_turn = stop
    chat.task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
    return chat.task, stop


LEAVE = {
    "Cancel plan mode": ("post", "/api/chat/sessions/c1/plan/cancel", None),
    "the task-mode pill": ("post", "/api/chat/task-mode", {"mode": "agent", "session": "c1"}),
}


@pytest.mark.parametrize("door", list(LEAVE))
@pytest.mark.asyncio
async def test_the_planning_turn_is_stopped_while_the_chat_is_still_in_plan_mode(tmp_path, door):
    state = _make_state(tmp_path)
    chat = _seed(state)
    method, path, body = LEAVE[door]
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        assert chat._task_mode == "plan"
        task, stop = _running(state, chat)  # the planning turn drafts the plan
        try:
            r = await getattr(client, method)(path, json=body)
            assert r.status == 200
        finally:
            task.cancel()
    assert stop.modes == ["plan"], "the planning turn ran on after the mode left plan"
    assert chat._task_mode == "agent"
    assert chat_plan.read("c1") == (None, {})


@pytest.mark.parametrize("door", list(LEAVE))
@pytest.mark.asyncio
async def test_a_turn_plan_mode_only_waits_on_runs_on(tmp_path, door):
    state = _make_state(tmp_path)
    chat = _seed(state)
    method, path, body = LEAVE[door]
    async with TestClient(TestServer(_app(state))) as client:
        task, stop = _running(state, chat)  # a turn of the chat's own mode, already running
        try:
            r = await client.post("/api/chat/sessions/c1/plan/activate")
            assert (await r.json())["after_turn"] is True
            r = await getattr(client, method)(path, json=body)
            assert r.status == 200
        finally:
            task.cancel()
    assert stop.modes == []
    assert chat_plan.read("c1") == (None, {})


@pytest.mark.parametrize("door", list(LEAVE))
@pytest.mark.asyncio
async def test_a_plan_with_no_turn_running_has_nothing_to_stop(tmp_path, door):
    state = _make_state(tmp_path)
    chat = _seed(state)
    stop = _StopSeesTheMode(chat)
    state.sessions.stop_turn = stop
    method, path, body = LEAVE[door]
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        r = await getattr(client, method)(path, json=body)
        assert r.status == 200
    assert stop.modes == []
    assert chat._task_mode == "agent"


@pytest.mark.asyncio
async def test_a_mode_change_with_no_plan_open_stops_no_turn(tmp_path):
    state = _make_state(tmp_path)
    chat = _seed(state)
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/task-mode", json={"mode": "plan", "session": "c1"})
        task, stop = _running(state, chat)
        try:
            r = await client.post("/api/chat/task-mode", json={"mode": "agent", "session": "c1"})
            assert r.status == 200
        finally:
            task.cancel()
    assert stop.modes == []
    assert chat._task_mode == "agent"


@pytest.mark.parametrize("door", list(LEAVE))
@pytest.mark.asyncio
async def test_a_stopped_planning_turns_partial_reply_leaves_no_plan(tmp_path, door):
    """The stopped turn ends before its stop returns, as ``run_chat``'s end does, and hands its
    partial reply to the walkthrough as the step's draft. The plan is dropped before the stop, so
    that end finds none, and leaving plan mode leaves no plan awaiting review behind it."""
    state = _make_state(tmp_path)
    chat = _seed(state)
    method, path, body = LEAVE[door]

    async def stop_turn(key, force=False):
        chat.messages.append({"role": "assistant", "content": "1. Delete the old files"})
        chat_plan.maybe_submit_plan_draft(state, chat, ran_in="plan")
        return "stopped"

    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        state.sessions.stop_turn = stop_turn
        chat.task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
        try:
            r = await getattr(client, method)(path, json=body)
            status = r.status
        finally:
            chat.task.cancel()
    sess, _binding = chat_plan.read("c1")
    step = chat_plan.PS.current_step(sess) if sess else None
    assert (status, chat._task_mode, step.status if step else None) == (
        200,
        "agent",
        None,
    ), f"{door}: left plan mode with a plan step {step.status if step else None!r} open"
