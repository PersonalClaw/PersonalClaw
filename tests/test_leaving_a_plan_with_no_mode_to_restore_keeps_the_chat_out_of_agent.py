"""Leaving plan mode goes back to the mode the plan recorded, and never to Agent when it recorded
none it can go back to.

Activation records the chat's mode before plan mode (``resume_task_mode``), and Cancel and
Approve restore it. A binding that lost it, or one an older build wrote with a mode this build
does not know, fell back to Agent, where every tool runs, whatever the chat was in before. It now
falls back to Ask, where only read-only tools run. Each case drives a real runtime past the gate,
so a fix that only changed the reported mode fails it.
"""

from __future__ import annotations

import pytest
import test_chat_plan_mode
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app, _seed, _shown
from chat_test_helpers import _make_state
from test_native_runtime import _drain, _Tool

from personalclaw.dashboard import chat_plan

pytestmark = pytest.mark.security

#: What a binding can hold where the mode to restore should be: nothing, plan mode itself (leaving
#: plan mode for plan mode leaves nothing), and a mode no build of this one knows.
_NO_MODE_TO_RESTORE = {
    "missing": None,
    "plan": "plan",
    "unknown": "full-auto",
}


async def _leave(client, state, chat, how: str) -> dict:
    """Draft the plan, then leave plan mode by *how*: Cancel, or Approve of its one step."""
    chat.append("assistant", "the plan", "msg msg-a")
    chat.drain()
    chat_plan.maybe_submit_plan_draft(state, chat)
    if how == "cancel":
        r = await client.post("/api/chat/sessions/c1/plan/cancel")
    else:
        r = await client.post(
            "/api/chat/sessions/c1/plan/approve",
            json={"step_id": "chat-plan-1"},
            headers=_shown("c1", "chat-plan-1"),
        )
    assert r.status == 200, await r.text()
    return await r.json()


def _record(recorded: str | None) -> None:
    """Rewrite the binding activation wrote so it holds *recorded* as the mode to restore."""
    sess, binding = chat_plan.read("c1")
    assert sess is not None and binding.get("resume_task_mode") == "agent", "premise"
    if recorded is None:
        del binding["resume_task_mode"]
    else:
        binding["resume_task_mode"] = recorded
    chat_plan.write(sess, binding)


@pytest.mark.parametrize("how", ["cancel", "approve"])
@pytest.mark.parametrize("recorded", list(_NO_MODE_TO_RESTORE))
@pytest.mark.asyncio
async def test_a_plan_with_no_mode_to_restore_leaves_the_chat_in_ask(tmp_path, how, recorded):
    """🔴 Red before: the chat left plan mode in Agent, and the write ran."""
    state = _make_state(tmp_path)
    chat = _seed(state)
    tool = _Tool(name="write_file", requires_approval=False)
    rt = test_chat_plan_mode.TestTheGateNotThePrompt._runtime_bound_to(state, tool)
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        _record(_NO_MODE_TO_RESTORE[recorded])
        body = await _leave(client, state, chat, how)
    assert body["task_mode"] == "ask"
    assert chat._task_mode == "ask"
    await rt.start()
    await _drain(rt, "write the file")
    assert tool.invoked == [], "a write ran in the mode the chat left plan mode for"


@pytest.mark.parametrize("how", ["cancel", "approve"])
@pytest.mark.asyncio
async def test_a_recorded_mode_is_the_one_restored(tmp_path, how):
    """The control: a mode the plan did record is restored as it is, so a fix that always leaves
    plan mode in Ask fails here."""
    state = _make_state(tmp_path)
    chat = _seed(state)
    async with TestClient(TestServer(_app(state))) as client:
        await client.post("/api/chat/sessions/c1/plan/activate")
        _record("build")
        body = await _leave(client, state, chat, how)
    assert body["task_mode"] == "build"
    assert chat._task_mode == "build"


@pytest.mark.asyncio
async def test_a_parked_run_approval_resumes_with_no_mode_to_restore_runs_read_only(
    tmp_path, monkeypatch
):
    """Approve resumes a run that plan mode parked. When the plan recorded no mode to go back to,
    the resumed run starts in Ask, and its write is refused at the gate."""
    state = _make_state(tmp_path)
    chat = _seed(state)
    tool = _Tool(name="write_file", requires_approval=False)
    rt = test_chat_plan_mode.TestTheGateNotThePrompt._runtime_bound_to(state, tool)
    resumed_in: list[str] = []

    async def _run_chat(_state, session, _msg, **_kw):
        resumed_in.append(session._task_mode)

    monkeypatch.setattr("personalclaw.dashboard.chat_runner.run_chat", _run_chat)
    task = test_chat_plan_mode.TestMidTurnParkAndResume._running(state, chat)
    try:
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate", json={"stop": True})
            _record(None)
            body = await _leave(client, state, chat, "approve")
    finally:
        task.cancel()
    assert body["resumed"] is True and body["task_mode"] == "ask"
    assert resumed_in == ["ask"], "the parked run did not resume in Ask"
    await rt.start()
    await _drain(rt, "write the file")
    assert tool.invoked == [], "the resumed run's write ran"
