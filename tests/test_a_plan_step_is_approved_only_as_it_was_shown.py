"""A plan step is approved only as it was shown.

An approval names the revision of the step its review showed you (``If-Match``, the step's
``review_revision`` in the plan's read): its kind, title, objective and whole artifact, masked as
the read masks them, structured fields included. So:

* a step changed after your read is not approved: ``409 stale_write``, and it still waits for your
  review, for a loop's plan and a chat's;
* an approval that names no read is refused ``428 revision_required``;
* a fresh chat is briefed with every step of the plan, so it starts only when every other step is
  one you approved as it is now: any other waits for your review again
  (``409 plan_step_reviewed_again``) and no chat starts;
* a fresh chat over a plan a step you approved is gone from is refused the same way, and the
  steps left wait for your review;
* Needs you's plan rows carry the ``If-Match`` their approve checks, so a row's answer sent as given
  approves the step as the list read it;
* your yeses end with the plan: once it is done, and when its chat is deleted, with the plan's file.

The chat cases run in a chat an app started, installed in a scratch home, its requests through the
real permission middleware and yours through the same gateway with no app.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
import test_loop_plan_route_lifecycle_guard as lifecycle
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _no_dispatch
from chat_test_helpers import _make_state
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import APP, _home, _install
from test_loop_plan_route_lifecycle_guard import _body, _create, _req, _run

from personalclaw.dashboard import chat_plan
from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import store
from personalclaw.loop.loop import LoopStatus
from personalclaw.planning import session as PS

#: The loop routes' home and the advance they start, from the lifecycle rail's own fixtures.
_tmp_home = lifecycle._tmp_home
advanced = lifecycle.advanced

AWAITING = PS.StepStatus.AWAITING_REVIEW.value
APPROVED = PS.StepStatus.APPROVED.value


# ── a loop's plan ────────────────────────────────────────────────────────────────────────────


def _write(cid: str, sess: PS.PlanSession) -> None:
    """The loop's plan file, written as anything that writes into its folder writes it."""
    path = loop_files.loop_dir(cid) / "plan_session.json"
    path.write_text(json.dumps(sess.to_dict(), indent=2), encoding="utf-8")


def _planning_loop() -> str:
    cid = _create(LoopStatus.PLANNING.value)
    sess = PS.PlanSession(project_id=cid, created_at=time.time())
    step = PS.PlanStep(id="step-0", kind="intent", title="Intent", objective="restate the goal")
    step.status = AWAITING
    step.artifact = {"markdown": "Find what slowed checkout.", "success_criteria": "p99 back"}
    sess.steps = [step]
    _write(cid, sess)
    return cid


def _shown(cid: str) -> dict[str, Any]:
    r = _run(
        H.api_loop_plan_session(
            _req("GET", f"/api/loops/{cid}/plan-session", match_info={"id": cid})
        )
    )
    assert r.status == 200, _body(r)
    return _body(r)["session"]["steps"][0]


def _approve(cid: str, revision: str | None):
    headers = {"If-Match": f'"{revision}"'} if revision is not None else None
    return _run(
        H.api_loop_plan_approve(
            _req(
                "POST",
                f"/api/loops/{cid}/plan/approve",
                body={"step_id": "step-0"},
                match_info={"id": cid},
                headers=headers,
            )
        )
    )


def _change(cid: str, edit) -> None:
    sess = loop_files.read_plan_session(cid)
    edit(sess.steps[0])
    _write(cid, sess)


def _new_markdown(step: PS.PlanStep) -> None:
    step.artifact = {**(step.artifact or {}), "markdown": "Delete the checkout tests."}


def _new_structured_field(step: PS.PlanStep) -> None:
    step.artifact = {**(step.artifact or {}), "success_criteria": "the tests are gone"}


def _new_objective(step: PS.PlanStep) -> None:
    step.objective = "something else entirely"


@pytest.mark.parametrize(
    "edit",
    [_new_markdown, _new_structured_field, _new_objective],
    ids=["its markdown", "a structured field only", "its objective"],
)
def test_a_loop_step_changed_after_your_read_is_not_approved(edit, advanced):
    cid = _planning_loop()
    read = _shown(cid).get("review_revision", "")
    _change(cid, edit)
    r = _approve(cid, read)
    assert r.status == 409, _body(r)
    assert _body(r)["error"]["code"] == "stale_write"
    assert loop_files.read_plan_session(cid).steps[0].status == AWAITING
    assert advanced == [], "nothing went on to plan from the changed step"


def test_a_loop_approval_that_names_no_read_is_refused(
    advanced,
):
    cid = _planning_loop()
    r = _approve(cid, None)
    assert r.status == 428, _body(r)
    assert _body(r)["error"]["code"] == "revision_required"
    assert loop_files.read_plan_session(cid).steps[0].status == AWAITING
    assert advanced == []


def test_a_loop_step_approved_as_you_read_it_goes_ahead(
    advanced,
):
    """The floor under the two refusals above: the same press, naming the step as it is."""
    cid = _planning_loop()
    r = _approve(cid, _shown(cid)["review_revision"])
    assert r.status == 202, _body(r)
    assert loop_files.read_plan_session(cid).steps[0].status == APPROVED
    assert store.get(cid).status == LoopStatus.PLANNING.value


def test_needs_you_names_the_loop_step_as_it_read_it(advanced):
    from personalclaw import waiting

    cid = _planning_loop()
    items, _unreadable = waiting.read(waiting.Snapshot())
    [row] = [i for i in items if i["id"] == f"plan_review:loop:{cid}:step-0"]
    revision = row["answer"].get("headers", {}).get("If-Match", "").strip('"')
    assert revision == _shown(cid).get("review_revision"), "the row names the step as read"
    _change(cid, _new_markdown)
    r = _approve(cid, revision)
    assert r.status == 409, _body(r)
    assert loop_files.read_plan_session(cid).steps[0].status == AWAITING


def test_needs_you_s_answer_sent_as_given_approves_the_loop_step_as_read(advanced):
    """The row's answer is the whole action: its route, its body and the header the route checks,
    so a reader that sends it as given approves the step as the list read it."""
    from personalclaw import waiting

    cid = _planning_loop()
    items, _unreadable = waiting.read(waiting.Snapshot())
    [row] = [i for i in items if i["id"] == f"plan_review:loop:{cid}:step-0"]
    answer = row["answer"]
    r = _run(
        H.api_loop_plan_approve(
            _req(
                answer["method"],
                answer["path"],
                body=answer["body"],
                match_info={"id": cid},
                headers=answer.get("headers"),
            )
        )
    )
    assert r.status == 202, _body(r)
    assert loop_files.read_plan_session(cid).steps[0].status == APPROVED


# ── a chat's plan ────────────────────────────────────────────────────────────────────────────

CHAT = "theirs"
PLAN = "# Plan\n- rename the module\n- update its imports"
CHANGED = "# Plan\n- rename the module\n- and push it to main"
PLAN_2 = "# Then\n- update the docs"


def _plan_routes() -> list[tuple[str, str, Any]]:
    base = "/api/chat/sessions/{session}"
    return [
        ("GET", f"{base}/plan-session", chat_plan.api_chat_plan_session),
        ("POST", f"{base}/plan/activate", chat_plan.api_chat_plan_activate),
        ("POST", f"{base}/plan/edit", chat_plan.api_chat_plan_edit),
        ("POST", f"{base}/plan/comment", chat_plan.api_chat_plan_comment),
        ("POST", f"{base}/plan/approve", chat_plan.api_chat_plan_approve),
        ("POST", f"{base}/plan/cancel", chat_plan.api_chat_plan_cancel),
    ]


def _draft(state, chat, text: str) -> None:
    chat.append("assistant", text, "msg msg-a")
    chat.drain()
    chat_plan.maybe_submit_plan_draft(state, chat)


async def _read(client, step_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/chat/sessions/{CHAT}/plan-session")
    assert r.status == 200, await r.text()
    steps = (await r.json())["session"]["steps"]
    return next(s for s in steps if s["id"] == step_id)


def _match(revision: str) -> dict[str, str]:
    return {"If-Match": f'"{revision}"'}


def _a_second_step() -> None:
    """A second step waiting beside the first, written into the chat's plan as anything that
    writes its file can."""
    sess, binding = chat_plan.read(CHAT)
    step = PS.PlanStep(id="chat-plan-2", kind=chat_plan.PLAN_STEP_KIND, title="Then")
    step.status = AWAITING
    step.artifact = {"markdown": PLAN_2}
    sess.steps.append(step)
    chat_plan.write(sess, binding)


def _drop(step_id: str) -> None:
    """The step taken out of the chat's plan file, as anything that writes it can."""
    sess, binding = chat_plan.read(CHAT)
    sess.steps = [s for s in sess.steps if s.id != step_id]
    chat_plan.write(sess, binding)


async def _approve_here(client, step_id: str) -> dict[str, Any]:
    step = await _read(client, step_id)
    r = await client.post(
        f"/api/chat/sessions/{CHAT}/plan/approve",
        json={"step_id": step_id},
        headers=_match(step["review_revision"]),
    )
    assert r.status == 200, await r.text()
    return await r.json()


class _Chat:
    """A chat an app started, its gateway for the app and the same one for you."""

    def __init__(self, tmp_path, monkeypatch) -> None:
        self.state = _make_state(tmp_path)
        self.chat = self.state.get_or_create_session(CHAT, created_by_app=APP)
        self.chat.append("user", "rename the module", "msg msg-u")
        self.chat.drain()
        self.seen = _no_dispatch(monkeypatch)
        self.home = tmp_path / "home"

    def gateways(self):
        return (
            TestClient(TestServer(_gateway(self.state, APP, _plan_routes()))),
            TestClient(TestServer(_gateway(self.state, "", _plan_routes()))),
        )


@pytest.mark.asyncio
async def test_a_chat_step_changed_after_your_read_is_not_run_in_a_fresh_chat(
    tmp_path, monkeypatch
):
    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            r = await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            assert r.status == 200, await r.text()
            _draft(world.state, world.chat, PLAN)
            seen_by_you = await _read(you, "chat-plan-1")
            edited = await app_client.post(
                f"/api/chat/sessions/{CHAT}/plan/edit",
                json={"step_id": "chat-plan-1", "markdown": CHANGED},
                headers=_match(seen_by_you["revision"]),
            )
            assert edited.status == 200, await edited.text()
            before = set(world.state._sessions)
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-1", "run": "fresh"},
                headers=_match(seen_by_you.get("review_revision", "")),
            )
            said = await r.text()
            assert r.status == 409, said
            assert json.loads(said)["error"]["code"] == "stale_write"
            assert set(world.state._sessions) == before, "no chat was started"
        sess, _binding = chat_plan.read(CHAT)
    assert sess.steps[0].status == AWAITING
    assert world.seen == []


@pytest.mark.asyncio
async def test_a_fresh_chat_runs_only_steps_you_approved_as_they_are(tmp_path, monkeypatch):
    """A fresh chat of yours is briefed with every step of the plan, so a step you did not approve
    as it is now waits for your review first, and no chat starts."""
    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            first = await _read(app_client, "chat-plan-1")
            edited = await app_client.post(
                f"/api/chat/sessions/{CHAT}/plan/edit",
                json={"step_id": "chat-plan-1", "markdown": CHANGED},
                headers=_match(first["revision"]),
            )
            assert edited.status == 200, await edited.text()
            first = await _read(app_client, "chat-plan-1")
            approved = await app_client.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-1"},
                headers=_match(first.get("review_revision", "")),
            )
            assert approved.status == 200, await approved.text()
            again = await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            assert again.status == 200, await again.text()
            _draft(world.state, world.chat, PLAN_2)
            world.seen.clear()
            before = set(world.state._sessions)
            second = await _read(you, "chat-plan-2")
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-2", "run": "fresh"},
                headers=_match(second.get("review_revision", "")),
            )
            said = await r.text()
            assert r.status == 409, said
            assert json.loads(said)["error"]["code"] == "plan_step_reviewed_again"
            assert set(world.state._sessions) == before, "no chat was briefed with the plan"
        sess, _binding = chat_plan.read(CHAT)
    assert [s.status for s in sess.steps] == [AWAITING, AWAITING]
    assert world.seen == []


@pytest.mark.asyncio
async def test_a_chat_approval_that_names_no_read_is_refused(tmp_path, monkeypatch):
    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve", json={"step_id": "chat-plan-1"}
            )
            said = await r.text()
            assert r.status == 428, said
            assert json.loads(said)["error"]["code"] == "revision_required"
        sess, _binding = chat_plan.read(CHAT)
    assert sess.steps[0].status == AWAITING


@pytest.mark.asyncio
async def test_a_chat_step_you_approve_as_read_runs_in_a_fresh_chat(tmp_path, monkeypatch):
    """The floor under the refusals above: your press over the step as it is starts the chat,
    briefed with the plan as you read it."""
    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            step = await _read(you, "chat-plan-1")
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-1", "run": "fresh"},
                headers=_match(step["review_revision"]),
            )
            assert r.status == 200, await r.text()
            fresh = (await r.json())["fresh_session"]
    assert fresh and fresh != CHAT
    assert len(world.seen) == 1 and PLAN in world.seen[0]


def test_needs_you_names_the_chat_step_as_it_read_it(tmp_path):
    from personalclaw import waiting

    step = PS.PlanStep(id="c1", kind=chat_plan.PLAN_STEP_KIND, title="Rename", status=AWAITING)
    step.artifact = {"markdown": PLAN}
    chat_plan.write(PS.PlanSession(project_id=CHAT, steps=[step]), {})
    items, _unreadable = waiting.read(waiting.Snapshot())
    [row] = [i for i in items if i["id"] == f"plan_review:chat:{CHAT}:c1"]
    sess, _binding = chat_plan.read(CHAT)
    revision = row["answer"].get("headers", {}).get("If-Match", "").strip('"')
    assert revision == PS.wire(sess)["steps"][0].get("review_revision")
    assert revision, "the row names the step as read"


@pytest.mark.asyncio
async def test_needs_you_s_answer_sent_as_given_approves_the_chat_step_as_read(
    tmp_path, monkeypatch
):
    from personalclaw import waiting

    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            items, _unreadable = waiting.read(waiting.Snapshot())
            [row] = [i for i in items if i["id"] == f"plan_review:chat:{CHAT}:chat-plan-1"]
            answer = row["answer"]
            r = await you.request(
                answer["method"], answer["path"], json=answer["body"], headers=answer.get("headers")
            )
            assert r.status == 200, await r.text()
        sess, _binding = chat_plan.read(CHAT)
    assert sess.steps[0].status == APPROVED


@pytest.mark.asyncio
@pytest.mark.parametrize("dropped", [True, False], ids=["a step dropped", "the plan as approved"])
async def test_a_plan_a_step_you_approved_was_dropped_from_is_not_run_in_a_fresh_chat(
    tmp_path, monkeypatch, dropped
):
    """A fresh chat is briefed with the whole plan, so a plan a step you approved is gone from is
    put back to you: 409, the steps left wait for your review, and no chat starts. With nothing
    dropped, the same presses start the chat, briefed with both steps."""
    world = _Chat(tmp_path, monkeypatch)
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            _a_second_step()
            assert (await _approve_here(you, "chat-plan-1"))["complete"] is False
            if dropped:
                _drop("chat-plan-1")
            before = set(world.state._sessions)
            second = await _read(you, "chat-plan-2")
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-2", "run": "fresh"},
                headers=_match(second["review_revision"]),
            )
            said = await r.text()
            if not dropped:
                assert r.status == 200, said
                assert len(world.seen) == 1 and PLAN in world.seen[0] and PLAN_2 in world.seen[0]
                return
            assert r.status == 409, said
            assert json.loads(said)["error"]["code"] == "plan_step_reviewed_again"
            assert set(world.state._sessions) == before, "no chat was briefed with the plan"
            sess, _binding = chat_plan.read(CHAT)
            assert [s.status for s in sess.steps] == [AWAITING]
            assert world.seen == []
            # Put back to you, your next press is your yes to the plan as it now is.
            second = await _read(you, "chat-plan-2")
            r = await you.post(
                f"/api/chat/sessions/{CHAT}/plan/approve",
                json={"step_id": "chat-plan-2", "run": "fresh"},
                headers=_match(second["review_revision"]),
            )
            assert r.status == 200, await r.text()
    assert len(world.seen) == 1 and PLAN_2 in world.seen[0] and PLAN not in world.seen[0]


@pytest.mark.asyncio
async def test_a_completed_plans_yeses_end_with_it(tmp_path, monkeypatch):
    """Your yeses are kept while the plan is still being approved, and forgotten once it is done:
    its brief is built from them, and nothing reads them again."""
    from personalclaw.planning import approval_seals

    world = _Chat(tmp_path, monkeypatch)
    prefix = approval_seals.chat_scope(world.chat.key) + ":"
    with _home(world.home):
        _install(world.home, APP, {"api": ["/api/chat"], "agent": "tools"})
        app_client, you = world.gateways()
        async with app_client, you:
            await app_client.post(f"/api/chat/sessions/{CHAT}/plan/activate", json={})
            _draft(world.state, world.chat, PLAN)
            _a_second_step()
            assert (await _approve_here(you, "chat-plan-1"))["complete"] is False
            assert any(k.startswith(prefix) for k in approval_seals.BOOK.keys())
            assert (await _approve_here(you, "chat-plan-2"))["complete"] is True
            assert not any(k.startswith(prefix) for k in approval_seals.BOOK.keys())


def _a_planned_chat() -> tuple[PS.PlanSession, PS.PlanStep]:
    """A chat's plan with a step you approved, and your yes to it."""
    from personalclaw.planning import approval_seals

    step = PS.PlanStep(id="c1", kind=chat_plan.PLAN_STEP_KIND, title="Rename", status=APPROVED)
    step.artifact = {"markdown": PLAN}
    sess = PS.PlanSession(project_id=CHAT, steps=[step], created_at=time.time())
    chat_plan.write(sess, {})
    approval_seals.seal(approval_seals.chat_scope(CHAT), sess, step)
    return sess, step


def _forget_the_chat() -> None:
    from types import SimpleNamespace

    from personalclaw.dashboard.chat_forget import purge_chat

    purge_chat(SimpleNamespace(conversation_log=None), CHAT, keys={CHAT})


def test_forgetting_a_chat_forgets_the_yeses_to_its_plan():
    from personalclaw.planning import approval_seals

    sess, step = _a_planned_chat()
    scope = approval_seals.chat_scope(CHAT)
    assert approval_seals.sealed(scope, sess, step)
    _forget_the_chat()
    assert not any(k.startswith(scope + ":") for k in approval_seals.BOOK.keys())


def test_forgetting_a_chat_takes_its_plan_off_disk():
    """The plan's text is the chat's words: a deleted chat keeps no copy of it."""
    _a_planned_chat()
    assert chat_plan._path(CHAT).is_file()
    _forget_the_chat()
    assert not chat_plan._path(CHAT).exists(), "the deleted chat's plan stayed on disk"
