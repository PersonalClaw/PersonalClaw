"""Waiting on you: one list of everything the owner must answer, from every source (#3888).

Each source is raised here by its own producer, on a real ``DashboardState``, and read back
through ``GET /api/waiting`` behind the real permission middleware:

* each source in #3888's table appears once, with its brief, the work it belongs to and the route
  its own source answers it through: an approval (a background call, a helper's start and a tool
  server's question among them), an agent's question, a loop's question, merge and conflict, a
  workflow run's gate, a trigger's parked action, a browser grant, a plan review (a loop's step,
  a plan ready to launch, a chat's step) and a bridge confirmation;
* every item carries its owner action, work, consequence, risk, age and whether it is current,
  and one past answering is not listed;
* the list orders by consequence, then age;
* an item answered through its own route leaves it;
* an app reads the list only when its manifest declares ``/api/waiting``, reads all of it without
  what answers an item, and an agent reads its own chat's items;
* nothing can be answered through an item that its own route refuses: an app token and an agent
  credential are refused on every item's answer route;
* a source that cannot be read is said, never shown as nothing waiting;
* the list says it moved in one ``waiting_changed`` frame per window, carrying nothing: each kind
  rings once as it is asked and once as it leaves, a burst rings once, a change off the event
  loop is handed to it, a run's status rings and its steps do not, and headless nothing rings.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install
from test_running_now import _held
from test_work_phase_classifier import CHAT, _World

from personalclaw import waiting

APP = "probe-waiting"
UNDECLARED_APP = "probe-silent"

KINDS = {
    "approval",
    "question",
    "loop_question",
    "gate",
    "park",
    "browse_grant",
    "plan_review",
    "bridge_confirmation",
}


def _routes() -> list[tuple[str, str, Any]]:
    from personalclaw.dashboard.handlers.waiting import api_waiting

    return [("GET", "/api/waiting", api_waiting)]


async def _read(state: Any, caller: str = "", **headers: str) -> tuple[int, Any]:
    async with TestClient(TestServer(_gateway(state, caller, _routes()))) as http:
        resp = await http.get("/api/waiting", headers=headers)
        body = await resp.json() if resp.content_type == "application/json" else await resp.text()
        return resp.status, body


async def _until(predicate, what: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never raised: {what}")


# ── one of each ──────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _everything(tmp_path):
    """One of each kind of thing waiting on you, each raised by its own producer."""
    from personalclaw.browse import grant
    from personalclaw.dashboard import chat_plan
    from personalclaw.inbound import bridge
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus
    from personalclaw.planning.session import PlanSession, PlanStep, StepStatus
    from personalclaw.triggers import parks
    from personalclaw.workflows import human_input
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    w = _World(tmp_path, _held(asyncio.Event()))
    state = w.state
    asks: list[asyncio.Task] = []

    # 1. approvals: a background call, a helper's start in the chat, and a tool server's question.
    asks.append(
        asyncio.create_task(
            state.request_approval(
                "bg-1",
                "cron",
                "shell",
                tool_input={"command": "rm -rf build"},
                risk_level="destructive",
            )
        )
    )
    asks.append(
        asyncio.create_task(
            state.request_approval(
                "spawn:researcher",
                "subagent",
                "spawn_agent",
                tool_input="Look up the release notes",
                session=f"dashboard:{CHAT}",
            )
        )
    )
    asks.append(
        asyncio.create_task(
            state.request_approval(
                "mcp-elicit-0123456789abcdef",
                "mcp:files",
                "mcp_elicitation:files",
                tool_purpose="Which folder should I index?",
            )
        )
    )
    # 2. an agent's question in the chat.
    asks.append(
        asyncio.create_task(
            state.owner_questions.ask(
                f"dashboard:{CHAT}",
                [
                    {
                        "question": "Which file should I change?",
                        "header": "File",
                        "multiSelect": False,
                        "options": [{"label": "a.py", "description": ""}],
                    }
                ],
                asked_by="agent in “Plan the garden”",
            )
        )
    )
    # 6. a browser grant.
    asks.append(
        asyncio.create_task(grant.request_grant(task="Read the docs", scope=("docs.example",)))
    )
    await _until(lambda: len(state._pending_approvals) == 3, "the three approvals")
    await _until(lambda: state.owner_questions.pending(), "the question")
    await _until(lambda: grant.pending_grants(), "the browser grant")

    # 3. a loop's question, and a Code loop's merge and conflict.
    asking = loops.create(Loop(id="", name="Sort the inbox", kind="goal", task="sort"))
    loops.update_status(asking.id, LoopStatus.RUNNING)
    loop_files.write_question(asking.id, "May I archive last year's mail?", why="It is 4 GB")
    loops.update_status(asking.id, LoopStatus.NEEDS_INPUT)
    merging = loops.create(Loop(id="", name="Ship the fix", kind="goal", task="fix"))
    loops.update_status(merging.id, LoopStatus.RUNNING)
    loop_files.write_question(
        merging.id, "Merge two tasks into main?", merge={"into": "main", "tasks": []}
    )
    loops.update_status(merging.id, LoopStatus.NEEDS_INPUT)
    conflicting = loops.create(Loop(id="", name="Rename the module", kind="goal", task="rename"))
    loops.update_status(conflicting.id, LoopStatus.RUNNING)
    loop_files.write_question(
        conflicting.id,
        "Rename the module conflicts with main.",
        asked_by=loop_files.SCHEDULER_QUESTION,
        conflict={"task_id": "t1", "title": "Rename the module", "into": "main", "files": ["a.py"]},
    )
    loops.update_status(conflicting.id, LoopStatus.NEEDS_INPUT)

    # 4. a workflow run waiting at its gate.
    run = runs.create(WorkflowRun(id="", workflow_name="release", status=RunStatus.NEEDS_INPUT))
    human_input.create_continuation(
        run.id,
        node_id="approve",
        instance_path="approve",
        epoch=0,
        ask={"kind": "approval", "prompt": "Ship v1.2?"},
        confirmation_id="conf-1",
    )

    # 5. a trigger's parked action.
    parks._write(
        parks.TriggerPark(
            token="tok-park",
            trigger_id="schedule:clock:nightly",
            card={"blocker": "Sign in to the bank's site"},
            question="Sign in to the bank's site",
            created_at=time.time(),
        )
    )

    # 7. plan reviews: a loop's step, a plan ready to launch, and a chat's step.
    planning = loops.create(
        Loop(id="", name="Plan the move", kind="goal", task="plan", status="planning")
    )
    loop_files.write_plan_session(
        PlanSession(
            project_id=planning.id,
            steps=[
                PlanStep(
                    id="s1",
                    kind="design",
                    title="Choose the movers",
                    status=StepStatus.AWAITING_REVIEW.value,
                )
            ],
        )
    )
    ready = loops.create(
        Loop(id="", name="Tidy the repo", kind="goal", task="tidy", status="review")
    )
    chat_plan.write(
        PlanSession(
            project_id=CHAT,
            steps=[
                PlanStep(
                    id="c1",
                    kind=chat_plan.PLAN_STEP_KIND,
                    title="Rename the module",
                    status=StepStatus.AWAITING_REVIEW.value,
                )
            ],
        ),
        {},
    )

    # 8. a bridge confirmation.
    token = bridge._mint_confirmation(
        SimpleNamespace(name="restart_gateway"), {"why": "update"}, asked_by="bridge:c1"
    )

    ids = {
        "run": run.id,
        "asking": asking.id,
        "merging": merging.id,
        "conflicting": conflicting.id,
        "planning": planning.id,
        "ready": ready.id,
        "bridge_token": token,
    }
    try:
        yield w, ids
    finally:
        for t in asks:
            t.cancel()
        await asyncio.gather(*asks, return_exceptions=True)
        bridge._pending.clear()


def _by_id(body: dict) -> dict[str, dict]:
    return {i["id"]: i for i in body["waiting"]}


@pytest.mark.asyncio
async def test_every_source_appears_once_with_its_brief_and_answer_route(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        status, body = await _read(w.state)
    assert status == 200, body
    assert body["unreadable"] == []
    items = _by_id(body)
    assert len(items) == len(body["waiting"]), "an item is listed twice"
    assert {i["kind"] for i in items.values()} == KINDS

    bg = items["approval:bg-1"]
    assert bg["brief"]["tool"] == "shell" and "rm -rf build" in bg["brief"]["tool_input"]
    assert bg["consequence"] == "destructive" and bg["risk"] == "destructive"
    assert bg["answer"]["path"] == "/api/approvals/bg-1/{answer}"
    assert bg["answer"]["takes"] == ["approve", "reject"]

    helper = items["approval:spawn:researcher"]
    assert helper["work"] == "subagent:researcher" and helper["session"] == CHAT
    assert helper["answer"]["path"] == "/api/approvals/spawn%3Aresearcher/{answer}"

    mcp = items["approval:mcp-elicit-0123456789abcdef"]
    assert mcp["brief"]["tool_purpose"] == "Which folder should I index?"
    assert mcp["session"] == "" and mcp["work"] == ""

    [question] = [i for i in items.values() if i["kind"] == "question"]
    assert question["session"] == CHAT and question["work"] == f"chat:{CHAT}"
    assert question["brief"]["questions"][0]["question"] == "Which file should I change?"
    assert question["answer"]["path"].startswith(f"/api/chat/sessions/{CHAT}/questions/")
    assert question["route"] == f"#/chat/{CHAT}"

    asking = items[f"loop_question:{ids['asking']}"]
    assert asking["brief"]["question"] == "May I archive last year's mail?"
    assert asking["brief"]["why"] == "It is 4 GB" and asking["consequence"] == "asks"
    assert asking["answer"]["path"] == f"/api/loops/{ids['asking']}/nudge"
    merge = items[f"loop_question:{ids['merging']}"]
    assert merge["consequence"] == "changes"
    assert merge["answer"]["path"] == f"/api/loops/{ids['merging']}/merge"
    conflict = items[f"loop_question:{ids['conflicting']}"]
    assert conflict["consequence"] == "changes" and conflict["brief"]["conflict"]["files"] == [
        "a.py"
    ]
    assert conflict["answer"]["path"] == f"/api/loops/{ids['conflicting']}/conflict"
    assert conflict["answer"]["takes"] == ["redo", "drop"]

    gate = items[f"gate:{ids['run']}:conf-1:0"]
    assert (
        gate["work"] == f"workflow:{ids['run']}" and gate["brief"]["ask"]["prompt"] == "Ship v1.2?"
    )
    assert gate["answer"]["path"] == f"/api/workflows/runs/{ids['run']}/confirm"
    assert gate["answer"]["body"]["resume_token"], "you are handed the gate's token"
    assert gate["route"] == f"#/workflows/runs/{ids['run']}"

    park = items["park:schedule:clock:nightly"]
    assert park["work"] == "trigger:schedule:clock:nightly" and park["consequence"] == "changes"
    assert park["brief"]["card"] == {"blocker": "Sign in to the bank's site"}
    assert park["answer"]["path"] == "/api/triggers/schedule%3Aclock%3Anightly/answer"
    assert park["answer"]["body"] == {"resume_token": "tok-park"}

    [browse] = [i for i in items.values() if i["kind"] == "browse_grant"]
    assert browse["brief"]["task"] == "Read the docs"
    assert browse["answer"]["path"].startswith("/api/browse/grants/")

    step = items[f"plan_review:loop:{ids['planning']}:s1"]
    assert step["brief"]["step"]["title"] == "Choose the movers"
    assert step["answer"]["path"] == f"/api/loops/{ids['planning']}/plan/approve"
    assert step["answer"]["body"] == {"step_id": "s1"}
    launch = items[f"plan_review:loop:{ids['ready']}"]
    assert launch["answer"] == {
        "method": "PATCH",
        "path": f"/api/loops/{ids['ready']}",
        "takes": ["start"],
        "body": {"action": "start"},
    }
    chat_step = items[f"plan_review:chat:{CHAT}:c1"]
    assert chat_step["session"] == CHAT
    assert chat_step["answer"]["path"] == f"/api/chat/sessions/{CHAT}/plan/approve"

    [confirm] = [i for i in items.values() if i["kind"] == "bridge_confirmation"]
    assert "restart_gateway" in confirm["brief"]["body"]
    assert confirm["brief"]["asked_by"] == "bridge:c1"
    assert ids["bridge_token"] not in confirm["id"], "a confirmation's token is no id"
    assert confirm["answer"]["path"].endswith(ids["bridge_token"])


@pytest.mark.asyncio
async def test_every_item_says_its_action_work_consequence_risk_age_and_whether_current(tmp_path):
    async with _everything(tmp_path) as (w, _ids):
        _status, body = await _read(w.state)
    for item in body["waiting"]:
        assert set(item) == set(waiting.ITEM_FIELDS), item["id"]
        assert item["consequence"] in waiting.CONSEQUENCES, item["id"]
        assert item["current"] is True, item["id"]
        assert set(item["answer"]) == {"method", "path", "takes", "body"}, item["id"]
        if item["asked_at"] is not None:
            assert isinstance(item["age_secs"], int) and item["age_secs"] >= 0, item["id"]


# ── its order, and what leaves it ────────────────────────────────────────────────────────────


def _entry(approval_id: str, ts: float, risk: str = "") -> dict[str, Any]:
    return {"id": approval_id, "session": "", "tool": "shell", "risk": risk, "ts": ts}


def test_the_list_orders_by_consequence_then_age():
    now = time.time()
    snap = waiting.Snapshot(
        approvals=[
            _entry("newer", now - 10),
            _entry("deletes", now - 5, "destructive"),
            _entry("older", now - 300),
        ],
        questions=[{"id": "q1", "session": CHAT, "ts": now - 900, "questions": []}],
        window=3600.0,
    )
    with patch("personalclaw.loop.store.list_all", return_value=[]):
        items, _ = waiting.read(snap)
    assert [i["id"] for i in items if i["kind"] in ("approval", "question")] == [
        "approval:deletes",
        "approval:older",
        "approval:newer",
        "question:q1",
    ], "destructive first, then what changes something, oldest first, then the asks"


def test_an_item_past_answering_is_not_listed():
    now = time.time()
    snap = waiting.Snapshot(
        approvals=[_entry("late", now - 120), _entry("in-time", now - 30)], window=60.0
    )
    with patch("personalclaw.loop.store.list_all", return_value=[]):
        items, _ = waiting.read(snap)
    assert [i["id"] for i in items if i["kind"] == "approval"] == ["approval:in-time"]
    assert items[0]["expires_at"] is not None


@pytest.mark.asyncio
async def test_an_item_answered_through_its_own_route_leaves(tmp_path):
    from personalclaw.dashboard.handlers import sessions

    async with _everything(tmp_path) as (w, _ids):
        answered = [("POST", "/api/approvals/{id}/{action}", sessions.api_approval_resolve)]
        async with TestClient(TestServer(_gateway(w.state, "", answered))) as http:
            resp = await http.post("/api/approvals/bg-1/reject")
            assert resp.status == 200, await resp.text()
        _status, body = await _read(w.state)
    assert "approval:bg-1" not in _by_id(body)


@pytest.mark.asyncio
async def test_a_source_that_cannot_be_read_is_said(tmp_path):
    async with _everything(tmp_path) as (w, _ids):
        with patch("personalclaw.triggers.parks.parks_dir", side_effect=OSError("disk gone")):
            _status, body = await _read(w.state)
    assert body["unreadable"] == ["park"]
    assert not any(i["kind"] == "park" for i in body["waiting"])
    assert any(i["kind"] == "approval" for i in body["waiting"]), "the rest is still read"


# ── who reads what ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_app_that_declares_the_route_reads_it_all_without_what_answers_an_item(
    tmp_path,
):
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/waiting"]})
        async with _everything(tmp_path) as (w, ids):
            _status, mine = await _read(w.state)
            status, theirs = await _read(w.state, APP)
    assert status == 200, theirs
    assert set(_by_id(theirs)) == set(_by_id(mine)), "an app reads the whole list, as the relay"
    for item in theirs["waiting"]:
        assert item["answer"]["path"] == "" and item["answer"]["body"] == {}, item["id"]
        assert item["answer"]["takes"] == _by_id(mine)[item["id"]]["answer"]["takes"]
        assert ids["bridge_token"] not in str(item), item["id"]


@pytest.mark.asyncio
async def test_an_app_that_does_not_declare_the_route_is_refused(tmp_path):
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", UNDECLARED_APP, {"api": ["/api/chat"]})
        w = _World(tmp_path, _held(asyncio.Event()))
        status, body = await _read(w.state, UNDECLARED_APP)
    assert status == 403, body
    assert "api path not in declared permissions" in str(body)


@pytest.mark.asyncio
async def test_an_agent_reads_only_its_own_chats_items(tmp_path):
    async with _everything(tmp_path) as (w, _ids):
        status, body = await _read(
            w.state,
            "",
            **{"X-Internal-Secret": "the gateway's", "X-Session-Key": f"dashboard:{CHAT}"},
        )
    assert status == 200, body
    assert {i["session"] for i in body["waiting"]} == {CHAT}
    assert {i["kind"] for i in body["waiting"]} == {"approval", "question", "plan_review"}
    assert all(i["answer"]["path"] == "" for i in body["waiting"]), "an agent answers nothing"


# ── the list adds no authority ───────────────────────────────────────────────────────────────


def _answer_routes() -> list[tuple[str, str, Any]]:
    """Every route an item names, with its real handler: what an item's answer is sent to."""
    from personalclaw.dashboard import chat_plan
    from personalclaw.dashboard.chat_questions import api_chat_question_answer
    from personalclaw.dashboard.handlers import (
        browse_mirror,
        external_access,
        loop_routes,
        sessions,
        trigger_runs,
    )
    from personalclaw.workflows import handlers as runs

    return [
        ("POST", "/api/approvals/{id}/{action}", sessions.api_approval_resolve),
        (
            "POST",
            "/api/chat/sessions/{session}/questions/{question}/answer",
            api_chat_question_answer,
        ),
        ("POST", "/api/loops/{id}/nudge", loop_routes.api_loop_nudge),
        ("POST", "/api/loops/{id}/merge", loop_routes.api_loop_merge),
        ("POST", "/api/loops/{id}/conflict", loop_routes.api_loop_conflict),
        ("POST", "/api/loops/{id}/plan/approve", loop_routes.api_loop_plan_approve),
        ("PATCH", "/api/loops/{id}", loop_routes.api_loop_action),
        ("POST", "/api/workflows/runs/{run_id}/confirm", runs.api_run_confirm),
        ("POST", "/api/workflows/runs/{run_id}/resume", runs.api_run_resume),
        ("POST", "/api/triggers/{id}/answer", trigger_runs.api_trigger_answer),
        (
            "POST",
            "/api/browse/grants/{request_id}/{action}",
            browse_mirror.api_browse_grant_resolve,
        ),
        ("POST", "/api/chat/sessions/{session}/plan/approve", chat_plan.api_chat_plan_approve),
        (
            "POST",
            "/api/external-access/bridge/confirmations/{id}",
            external_access.api_bridge_confirmation,
        ),
    ]


def _send(item: dict[str, Any], owner_body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """The request answering *item* as its card would send it, with the token you hold."""
    answer = item["answer"]
    path = answer["path"].replace("{answer}", "approve")
    body = dict(owner_body)
    takes = answer["takes"]
    if item["kind"] == "question":
        body = {"answers": [{"selected": [0], "other": ""}]}
    elif item["kind"] in ("park", "bridge_confirmation"):
        body.update({"answer": True, "confirm": True})
    elif item["kind"] == "gate":
        body.update({"verb": "approve"})
    elif item["kind"] == "loop_question" and takes == ["text"]:
        body = {"text": "Yes, archive it."}
    elif item["kind"] == "loop_question":
        body = {"tips": {}, "confirm": True}
    return answer["method"], path, body


@pytest.mark.asyncio
async def test_nothing_is_answered_through_an_item_that_its_own_route_refuses(tmp_path):
    """An app whose manifest declares the list and every answer route, and an agent's credential,
    send each item's own answer, with the token you hold: each is refused by that route as before,
    and the item is still waiting."""
    declared = {
        "api": [
            "/api/waiting",
            "/api/approvals",
            "/api/chat",
            "/api/loops",
            "/api/workflows",
            "/api/triggers",
            "/api/browse",
            "/api/external-access",
        ]
    }
    agent = {"X-Internal-Secret": "the gateway's", "X-Session-Key": f"dashboard:{CHAT}"}
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, declared)
        async with _everything(tmp_path) as (w, _ids):
            _status, mine = await _read(w.state)
            for caller, headers in ((APP, {}), ("", agent)):
                async with TestClient(
                    TestServer(_gateway(w.state, caller, _answer_routes()))
                ) as http:
                    for item in mine["waiting"]:
                        method, path, body = _send(item, item["answer"]["body"])
                        resp = await http.request(method, path, json=body, headers=headers)
                        said = await resp.text()
                        if not caller and item["kind"] == "gate" and resp.status == 409:
                            # A gate holds an agent at its live controller (`controller.resume`,
                            # `test_only_the_owner_answers_an_approval`); with none running here,
                            # the door says so first and applies nothing.
                            assert "run_not_live" in said, said
                            continue
                        assert resp.status == 403, (
                            caller or "agent",
                            item["id"],
                            resp.status,
                            said,
                        )
            _status, after = await _read(w.state)
    assert set(_by_id(after)) == set(_by_id(mine)), "something was answered"


# ── the list says it moved ───────────────────────────────────────────────────────────────────

#: A window short enough for a test, the same rule as the gateway's.
WINDOW = 0.05


def _rings(w: Any) -> list[Any]:
    return [d for k, d in w.frames if k == waiting.CHANGED]


async def _a_window() -> None:
    await asyncio.sleep(WINDOW * 3)


async def _ask_approval(w, ids):
    ids["task"] = asyncio.create_task(w.state.request_approval("bg-9", "cron", "shell"))
    await _until(lambda: "bg-9" in w.state._pending_approvals, "the approval")
    return "approval:bg-9"


async def _answer_approval(w, ids):
    from personalclaw.dashboard.handlers import sessions

    answered = [("POST", "/api/approvals/{id}/{action}", sessions.api_approval_resolve)]
    async with TestClient(TestServer(_gateway(w.state, "", answered))) as http:
        resp = await http.post("/api/approvals/bg-9/approve")
        assert resp.status == 200, await resp.text()
    await ids["task"]


async def _ask_question(w, ids):
    ids["task"] = asyncio.create_task(
        w.state.owner_questions.ask(
            f"dashboard:{CHAT}",
            [{"question": "Which?", "header": "Which", "multiSelect": False, "options": []}],
            asked_by="agent",
        )
    )
    await _until(lambda: w.state.owner_questions.pending(), "the question")
    ids["question"] = w.state.owner_questions.pending()[0]["id"]
    return f"question:{ids['question']}"


async def _answer_question(w, ids):
    w.state.owner_questions.answer(f"dashboard:{CHAT}", ids["question"], skip=True)
    await ids["task"]


async def _ask_loop(w, ids):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loops.create(Loop(id="", name="Sort the inbox", kind="goal", task="sort"))
    loops.update_status(loop.id, LoopStatus.RUNNING)
    await _a_window()
    w.frames.clear()
    loop_files.write_question(loop.id, "May I archive it?")
    loops.update_status(loop.id, LoopStatus.NEEDS_INPUT)
    ids["loop"] = loop.id
    return f"loop_question:{loop.id}"


async def _answer_loop(w, ids):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus

    loop_files.clear_question(ids["loop"])
    loops.update_status(ids["loop"], LoopStatus.RUNNING)


async def _ask_again_while_waiting(w, ids):
    """A loop already waiting on you asks a new question (a Code loop's scheduler asking a merge
    after its question): its status does not move, its question does."""
    from personalclaw.loop import files as loop_files

    key = await _ask_loop(w, ids)
    await _a_window()
    w.frames.clear()
    loop_files.write_question(ids["loop"], "Merge two tasks?", merge={"into": "main", "tasks": []})
    return key


async def _ask_gate(w, ids):
    from personalclaw.workflows import human_input
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = runs.create(WorkflowRun(id="", workflow_name="release", status=RunStatus.NEEDS_INPUT))
    cont = human_input.create_continuation(
        run.id, node_id="ask", instance_path="ask", epoch=0, ask={"kind": "input", "prompt": "?"}
    )
    ids["run"], ids["token"] = run.id, cont.token
    return f"gate:{run.id}:ask:0"


async def _answer_gate(w, ids):
    from personalclaw.workflows import human_input

    assert human_input.consume_continuation(ids["run"], ids["token"]) is not None


async def _ask_park(w, ids):
    from personalclaw.triggers import parks

    trigger = SimpleNamespace(id="schedule:clock:nightly", name="Nightly")
    card = json.dumps({"needs_input": {"blocker": "Sign in"}})
    result = SimpleNamespace(success=True, outcome="needs_input", stdout=card, stderr="")
    with patch.object(parks, "_raise_row"):  # its Inbox row is the Inbox's business
        park = parks.raise_park(trigger, result)
    assert park is not None
    ids["token"] = park.token
    return "park:schedule:clock:nightly"


async def _answer_park(w, ids):
    from personalclaw.triggers import parks

    assert parks.claim("schedule:clock:nightly", ids["token"]) is not None


async def _ask_grant(w, ids):
    from personalclaw.browse import grant

    ids["task"] = asyncio.create_task(grant.request_grant(task="Read", request_id="g9"))
    await _until(lambda: grant.pending_grants(), "the grant")
    return "browse_grant:g9"


async def _answer_grant(w, ids):
    from personalclaw.browse import grant

    assert grant.approve_grant("g9")
    await ids["task"]


async def _ask_loop_step(w, ids):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop
    from personalclaw.planning.session import PlanSession, PlanStep, StepStatus

    loop = loops.create(Loop(id="", name="Plan", kind="goal", task="plan", status="planning"))
    await _a_window()
    w.frames.clear()
    step = PlanStep(id="s1", kind="design", title="Pick", status=StepStatus.AWAITING_REVIEW.value)
    ids["loop"], ids["plan"] = loop.id, PlanSession(project_id=loop.id, steps=[step])
    loop_files.write_plan_session(ids["plan"])
    return f"plan_review:loop:{loop.id}:s1"


async def _approve_loop_step(w, ids):
    from personalclaw.loop import files as loop_files
    from personalclaw.planning.session import StepStatus

    ids["plan"].steps[0].status = StepStatus.APPROVED.value
    loop_files.write_plan_session(ids["plan"])


async def _ask_launch(w, ids):
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import Loop, LoopStatus

    loop = loops.create(Loop(id="", name="Tidy", kind="goal", task="tidy", status="planning"))
    await _a_window()
    w.frames.clear()
    loops.update_status(loop.id, LoopStatus.REVIEW)
    ids["loop"] = loop.id
    return f"plan_review:loop:{loop.id}"


async def _launch(w, ids):
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus

    loops.update_status(ids["loop"], LoopStatus.RUNNING)


async def _ask_chat_step(w, ids):
    from personalclaw.dashboard import chat_plan
    from personalclaw.planning.session import PlanSession, PlanStep, StepStatus

    step = PlanStep(
        id="c1",
        kind=chat_plan.PLAN_STEP_KIND,
        title="Rename",
        status=StepStatus.AWAITING_REVIEW.value,
    )
    chat_plan.write(PlanSession(project_id=CHAT, steps=[step]), {})
    return f"plan_review:chat:{CHAT}:c1"


async def _end_chat_plan(w, ids):
    from personalclaw.dashboard import chat_plan

    chat_plan.clear(CHAT)


async def _ask_bridge(w, ids):
    from personalclaw.inbound import bridge

    ids["token"] = bridge._mint_confirmation(
        SimpleNamespace(name="restart_gateway"), {}, asked_by="bridge:c1"
    )
    return f"bridge_confirmation:{waiting._confirmation_ref(ids['token'])}"


async def _answer_bridge(w, ids):
    from personalclaw.inbound import bridge

    assert bridge.take_confirmation(ids["token"]) is not None


#: Each kind of thing waiting on you, asked by its own producer and ended its own way.
ASKED_AND_ENDED = {
    "approval": (_ask_approval, _answer_approval),
    "question": (_ask_question, _answer_question),
    "loop_question": (_ask_loop, _answer_loop),
    "loop_question asked again": (_ask_again_while_waiting, _answer_loop),
    "gate": (_ask_gate, _answer_gate),
    "park": (_ask_park, _answer_park),
    "browse_grant": (_ask_grant, _answer_grant),
    "plan_review of a loop's step": (_ask_loop_step, _approve_loop_step),
    "plan_review of a plan to launch": (_ask_launch, _launch),
    "plan_review of a chat's step": (_ask_chat_step, _end_chat_plan),
    "bridge_confirmation": (_ask_bridge, _answer_bridge),
}


@pytest.fixture
def _short_window(monkeypatch):
    monkeypatch.setattr(waiting, "WINDOW_SECS", WINDOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(ASKED_AND_ENDED))
async def test_each_kind_rings_once_as_it_is_asked_and_once_as_it_leaves(
    kind, tmp_path, _short_window
):
    """Asked, the list rings once and the item is in it; answered or ended at its source, the list
    rings once more and the item is gone: one read after one frame shows it on every surface."""
    from personalclaw.inbound import bridge

    ask, end = ASKED_AND_ENDED[kind]
    w = _World(tmp_path, _held(asyncio.Event()))
    ids: dict[str, Any] = {}
    target = "personalclaw.inbox_providers.native_source.get_dashboard_state"
    try:
        with patch(target, return_value=w.state):
            item = await ask(w, ids)
            await _a_window()
            assert _rings(w) == [{}], f"{kind}: asking it rang {len(_rings(w))} times"
            _status, body = await _read(w.state)
            assert item in _by_id(body), f"{kind}: not listed after its ring"

            w.frames.clear()
            await end(w, ids)
            await _a_window()
            assert _rings(w) == [{}], f"{kind}: ending it rang {len(_rings(w))} times"
            _status, body = await _read(w.state)
            assert item not in _by_id(body), f"{kind}: still listed after its ring"
    finally:
        task = ids.get("task")
        if task is not None and not task.done():
            task.cancel()
        bridge._pending.clear()


@pytest.mark.asyncio
async def test_a_burst_of_ten_asks_rings_once_per_window(tmp_path, _short_window):
    w = _World(tmp_path, _held(asyncio.Event()))
    asks = [
        asyncio.create_task(w.state.request_approval(f"bg-{n}", "cron", "shell")) for n in range(10)
    ]
    try:
        await _until(lambda: len(w.state._pending_approvals) == 10, "the ten approvals")
        assert _rings(w) == [], "nothing rings before its window closes"
        await _a_window()
        assert _rings(w) == [{}], "ten asks, one frame, with nothing in it to render"

        for n in range(10):
            w.state.resolve_approval(f"bg-{n}", True, by=_owner())
        await _a_window()
        assert len(_rings(w)) == 2, "ten answers, one more frame"
    finally:
        for t in asks:
            t.cancel()
        await asyncio.gather(*asks, return_exceptions=True)


def _owner():
    from personalclaw import approval_answer

    return approval_answer.YOU


@pytest.mark.asyncio
async def test_a_change_off_the_event_loop_is_handed_to_it(tmp_path, _short_window):
    """A loop's files and a run's gate are written from worker threads: the ring is handed to the
    gateway's loop, and rings there once."""
    w = _World(tmp_path, _held(asyncio.Event()))
    w.state._ws_loop = asyncio.get_running_loop()
    await asyncio.gather(*(asyncio.to_thread(waiting.changed, w.state) for _ in range(5)))
    await _a_window()
    assert _rings(w) == [{}]


def test_a_ring_with_no_gateway_is_nothing():
    """Headless (a CLI command, a script), nothing listens: a change rings nothing and raises
    nothing, so no ask ever fails for its ring."""
    with patch("personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=None):
        waiting.changed_elsewhere()
    broken = SimpleNamespace(_ws_loop=None)
    waiting.changed(broken)  # no running loop here: nothing to ring on


@pytest.mark.asyncio
async def test_a_runs_status_rings_and_its_steps_do_not(tmp_path, _short_window):
    """A run's gates are listed while it needs input, so its status moving rings the list; what
    its steps do between does not."""
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    w = _World(tmp_path, _held(asyncio.Event()))
    publish = WorkflowWatchdog(state=w.state)._publisher("r1")
    publish("workflow_node_update", {"node_id": "a", "status": "running"})
    await _a_window()
    assert _rings(w) == [], "a step's progress is no change to what waits on you"
    publish("workflow_run_update", {"status": "needs_input"})
    await _a_window()
    assert _rings(w) == [{}]
