"""Approving a chat's plan to run in a fresh chat starts one whose brief is the plan alone (#3821).

The plan gate's Approve & run it has a menu (`web/src/ui/chat/ChatPlanGate.tsx`), and one of its
choices is "Approve, and run it in a fresh chat": the clean-context option for a long planning
conversation. It is the same answer through the same route, `POST
/api/chat/sessions/{s}/plan/approve`, with ``run: "fresh"``, so the guard on who may approve
(`approval_answer`) holds for it as for the plain approve.

What it does, held here:

  · a new chat starts in the same project, working folder, agent and model, and its first message
    is the approved plan (the plan's markdown), with nothing of the conversation that produced it;
  · that chat runs one turn on it; the planning chat runs none, and a run the plan parked there is
    not resumed;
  · the planning chat leaves the plan posture as an approval does, and says where the plan runs;
  · the plan cannot be approved again in the planning chat;
  · a choice that does not complete the plan (a step still waits after it) is refused, and
    approves nothing.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app, _no_dispatch, _seed
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_plan
from personalclaw.planning import session as PS

PLAN = "# Approved plan\n- rename the module\n- update its imports"
TALK = "a long planning conversation about the rename"


async def _drafted(client, state, chat) -> None:
    # A running turn is stopped to plan ("Stop and plan"), which parks it.
    await client.post("/api/chat/sessions/c1/plan/activate", json={"stop": True})
    chat.append("assistant", PLAN, "msg msg-a")
    chat.drain()
    chat_plan.maybe_submit_plan_draft(state, chat)


def _planning_chat(state):
    chat = _seed(state)
    chat.append("user", TALK, "msg msg-u")
    chat.append("assistant", "Here is how I would do it.", "msg msg-a")
    chat.drain()
    chat.project_id = "p-0a1b2c3d"
    chat.workspace_dir = "/tmp/the-repo"
    return chat


@pytest.mark.asyncio
async def test_the_fresh_chat_starts_with_the_plan_and_nothing_else(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    chat = _planning_chat(state)
    seen = _no_dispatch(monkeypatch)
    async with TestClient(TestServer(_app(state))) as client:
        await _drafted(client, state, chat)
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "fresh"},
        )
        assert r.status == 200, await r.text()
        body = await r.json()
        fresh = state._sessions[body["fresh_session"]]
        again = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "fresh"},
        )
        assert again.status == 409, "a plan run in a fresh chat is not approved again here"

    assert body["complete"] is True and body["resumed"] is False
    assert fresh.key != chat.key
    assert fresh.project_id == chat.project_id and fresh.workspace_dir == chat.workspace_dir
    users = [m["content"] for m in fresh.messages if m["role"] == "user"]
    assert len(users) == 1 and PLAN in users[0]
    assert TALK not in users[0] and "u1" not in users[0]
    # One turn, in the fresh chat, on the plan alone.
    assert len(seen) == 1 and PLAN in seen[0] and TALK not in seen[0]
    # The planning chat left the plan posture and says where the plan runs.
    assert chat._task_mode == "agent"
    assert any(m.get("meta", {}).get("plan_runs_in") == fresh.key for m in chat.messages)


@pytest.mark.asyncio
async def test_a_parked_run_is_not_resumed_in_the_planning_chat(tmp_path, monkeypatch):
    import asyncio

    state = _make_state(tmp_path)
    chat = _planning_chat(state)
    seen = _no_dispatch(monkeypatch)

    class _Stop:
        async def __call__(self, *a, **kw):
            return None

    state.sessions.stop_turn = _Stop()
    chat.task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
    try:
        async with TestClient(TestServer(_app(state))) as client:
            await _drafted(client, state, chat)
            assert chat_plan.read("c1")[1].get("parked") is True
            r = await client.post(
                "/api/chat/sessions/c1/plan/approve",
                json={"step_id": "chat-plan-1", "run": "fresh"},
            )
            body = await r.json()
    finally:
        chat.task.cancel()
    assert body["resumed"] is False
    assert len(seen) == 1, "only the fresh chat runs"
    assert body["fresh_session"] != chat.key
    assert "parked" not in chat_plan.read("c1")[1]


@pytest.mark.asyncio
async def test_a_fresh_chat_is_refused_while_a_step_still_waits_after_this_one(
    tmp_path, monkeypatch
):
    state = _make_state(tmp_path)
    chat = _planning_chat(state)
    seen = _no_dispatch(monkeypatch)
    async with TestClient(TestServer(_app(state))) as client:
        await _drafted(client, state, chat)
        sess, binding = chat_plan.read("c1")
        sess.steps.append(PS.PlanStep(id="chat-plan-2", kind="plan", title="Then the docs"))
        chat_plan.write(sess, binding)
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "fresh"},
        )
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "plan_not_complete"
    sess, _binding = chat_plan.read("c1")
    assert sess.steps[0].status == PS.StepStatus.AWAITING_REVIEW.value, "nothing was approved"
    assert seen == []


@pytest.mark.asyncio
async def test_an_unknown_way_to_run_is_refused(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    chat = _planning_chat(state)
    _no_dispatch(monkeypatch)
    async with TestClient(TestServer(_app(state))) as client:
        await _drafted(client, state, chat)
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "somewhere"},
        )
        assert r.status == 400


def _as_caller(state):
    """The plan routes, with the principal a test names in ``X-Test-App`` put on the request as
    the token middleware would put an app's."""
    from aiohttp import web

    @web.middleware
    async def app_token(request, handler):
        if request.headers.get("X-Test-App"):
            request["app"] = request.headers["X-Test-App"]
        return await handler(request)

    server = _app(state)
    server.middlewares.append(app_token)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller",
    [{"X-Internal-Secret": "the-gateway's"}, {"X-Test-App": "minutes"}],
    ids=["an agent", "an app"],
)
async def test_only_you_run_a_plan_in_a_fresh_chat(tmp_path, monkeypatch, caller):
    """Running the plan in a fresh chat starts a chat of yours with the plan as its brief: an
    agent's request is refused as any approval of its own plan is, and so is an app's, even in a
    chat it started, where its plain approve is allowed."""
    state = _make_state(tmp_path)
    chat = _planning_chat(state)
    seen = _no_dispatch(monkeypatch)
    before = set(state._sessions)
    async with TestClient(TestServer(_as_caller(state))) as client:
        await _drafted(client, state, chat)
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "fresh"},
            headers=caller,
        )
        assert r.status == 403
    assert set(state._sessions) == before, "no chat was started"
    sess, _binding = chat_plan.read("c1")
    assert sess.steps[0].status == PS.StepStatus.AWAITING_REVIEW.value, "nothing was approved"
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_mode", ["temporary", "incognito"])
async def test_a_fresh_chat_is_never_broader_than_the_chat_that_planned_it(
    tmp_path, monkeypatch, memory_mode
):
    """A plan made in a private chat runs in a private chat: the same memory mode (no lasting
    record, no memory written, and for a temporary chat none read), the same ephemeral posture and
    the same mode. Started with the defaults, it would be an ordinary, kept, memory-on chat, which
    is more than the owner chose for that work."""
    state = _make_state(tmp_path)
    state.get_or_create_session("c1", memory_mode=memory_mode, ephemeral=True, mode="plan-mode")
    chat = _planning_chat(state)
    _no_dispatch(monkeypatch)
    async with TestClient(TestServer(_app(state))) as client:
        await _drafted(client, state, chat)
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1", "run": "fresh"},
        )
        fresh = state._sessions[(await r.json())["fresh_session"]]
    assert fresh.memory_mode == memory_mode
    assert fresh.is_restricted, "it writes no memory"
    assert fresh.blocks_reads == (memory_mode == "temporary")
    assert f"dashboard:{fresh.key}" in state._ephemeral_keys, "it keeps no lasting record"
    assert fresh.mode == chat.mode
