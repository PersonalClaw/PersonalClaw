"""A plan step's review is yours: its comment and its edit, as its approval.

A loop's plan is approved only by you (`approval_answer`): an agent's tool and an app are refused.
Its comment (which sends the step back to be drafted again) and its edit (which replaces the
draft you approve) are refused to them the same way, and the step is left as it was. A chat's
plan is approved by you, or by an app in a chat it started; never by an agent's tool. Its comment
and edit follow the same rule: an agent's tool is refused, and an app reviews in its own chat.
"""

from __future__ import annotations

import json

import pytest
import test_loop_plan_route_lifecycle_guard as lifecycle
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _no_dispatch
from chat_test_helpers import _make_state
from test_a_plan_step_is_approved_only_as_it_was_shown import (
    CHAT,
    PLAN,
    _draft,
    _match,
    _plan_routes,
    _planning_loop,
    _read,
)
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import APP, _home, _install
from test_loop_plan_route_lifecycle_guard import _body, _req, _run

from personalclaw.dashboard import chat_plan
from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.planning import session as PS

#: The loop routes' home and the advance they start, from the lifecycle rail's own fixtures.
_tmp_home = lifecycle._tmp_home
advanced = lifecycle.advanced

AGENT = {"X-Internal-Secret": "the gateway's", "X-Session-Key": "dashboard:loop-plan-x"}


def _plan_file(cid: str) -> str:
    return (loop_files.loop_dir(cid) / "plan_session.json").read_text(encoding="utf-8")


def _loop_review(cid: str, route: str, caller: str):
    """A comment or an edit of the loop's step, from an agent's tool or an app."""
    step = loop_files.read_plan_session(cid).steps[0]
    handler, body = {
        "comment": (H.api_loop_plan_comment, {"step_id": "step-0", "text": "drop the tests"}),
        "edit": (H.api_loop_plan_edit, {"step_id": "step-0", "markdown": "Drop the tests."}),
    }[route]
    headers = {"If-Match": f'"{PS.step_revision(step)}"'}
    if caller == "agent":
        headers.update(AGENT)
    req = _req(
        "POST",
        f"/api/loops/{cid}/plan/{route}",
        body=body,
        match_info={"id": cid},
        headers=headers,
    )
    if caller == "app":
        req["app"] = APP
    return _run(handler(req))


@pytest.mark.parametrize("route", ["comment", "edit"])
@pytest.mark.parametrize("caller", ["agent", "app"])
def test_only_you_review_a_loops_plan_step(route, caller, advanced):
    cid = _planning_loop()
    before = _plan_file(cid)
    r = _loop_review(cid, route, caller)
    assert r.status == 403, _body(r)
    assert _body(r)["error"]["code"] == "approval_owner_only"
    assert _plan_file(cid) == before, "the step is as it was"
    assert advanced == [], "nothing was sent to the planner"


@pytest.mark.parametrize("route", ["comment", "edit"])
def test_you_review_a_loops_plan_step(route, advanced):
    """The floor under the refusals above: the same request, as yours."""
    cid = _planning_loop()
    step = loop_files.read_plan_session(cid).steps[0]
    handler, body, ok = {
        "comment": (H.api_loop_plan_comment, {"step_id": "step-0", "text": "add a rollback"}, 202),
        "edit": (H.api_loop_plan_edit, {"step_id": "step-0", "markdown": "Add a rollback."}, 200),
    }[route]
    r = _run(
        handler(
            _req(
                "POST",
                f"/api/loops/{cid}/plan/{route}",
                body=body,
                match_info={"id": cid},
                headers={"If-Match": f'"{PS.step_revision(step)}"'},
            )
        )
    )
    assert r.status == ok, _body(r)


async def _chat_review(client, route: str, step: dict, headers: dict | None = None):
    body = (
        {"step_id": step["id"], "text": "drop the tests"}
        if route == "comment"
        else {"step_id": step["id"], "markdown": "Drop the tests."}
    )
    return await client.post(
        f"/api/chat/sessions/{CHAT}/plan/{route}",
        json=body,
        headers={**_match(step["revision"]), **(headers or {})},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["comment", "edit"])
async def test_an_agents_tool_does_not_review_a_chats_plan_step(tmp_path, monkeypatch, route):
    state = _make_state(tmp_path)
    chat = state.get_or_create_session(CHAT)
    chat.append("user", "rename the module", "msg msg-u")
    chat.drain()
    seen = _no_dispatch(monkeypatch)
    async with TestClient(TestServer(_gateway(state, "", _plan_routes()))) as you:
        await you.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
        _draft(state, chat, PLAN)
        step = await _read(you, "chat-plan-1")
        r = await _chat_review(you, route, step, AGENT)
        said = await r.text()
        assert r.status == 403, said
        assert json.loads(said)["error"]["code"] == "approval_owner_only"
    sess, _binding = chat_plan.read(CHAT)
    assert sess.steps[0].status == PS.StepStatus.AWAITING_REVIEW.value
    assert PLAN in PS.step_markdown(sess.steps[0]), "the draft is as it was"
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["comment", "edit"])
async def test_an_app_reviews_a_plan_step_in_a_chat_it_started(tmp_path, monkeypatch, route):
    state = _make_state(tmp_path)
    chat = state.get_or_create_session(CHAT, created_by_app=APP)
    chat.append("user", "rename the module", "msg msg-u")
    chat.drain()
    _no_dispatch(monkeypatch)
    home = tmp_path / "home"
    with _home(home):
        _install(home, APP, {"api": ["/api/chat"], "agent": "tools"})
        async with TestClient(TestServer(_gateway(state, APP, _plan_routes()))) as app_client:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(state, chat, PLAN)
            step = await _read(app_client, "chat-plan-1")
            r = await _chat_review(app_client, route, step)
            assert r.status == 200, await r.text()
