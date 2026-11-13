"""A loop's plan step changed after you approved it waits for your review again before the plan
is built.

A step's ``approved`` status in the loop's plan records that a yes was given. Each yes you give
through the approve route is also recorded in an owner book (``grants/plan_steps.json``,
`planning.approval_seals`), sealed to the step as its review showed it. Before the walkthrough
builds the loop's spec, every approved step is checked against its seal: one changed since, or one
with no yes of yours, goes back to review, the walkthrough waits on it, and no spec is built from
it. A step you approved that is no longer in the plan puts the whole plan back to you.

A change to how a read masks a step changes what its review showed, so that step goes back to
review too. That is meant, and pinned here so it stays that way.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import test_loop_plan_route_lifecycle_guard as lifecycle
from test_loop_plan_route_lifecycle_guard import _body, _create, _req, _run

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import store
from personalclaw.loop.loop import LoopStatus
from personalclaw.planning import session as PS

pytestmark = pytest.mark.security

#: The loop routes' home and the advance they start, from the lifecycle rail's own fixtures.
_tmp_home = lifecycle._tmp_home
advanced = lifecycle.advanced

AWAITING = PS.StepStatus.AWAITING_REVIEW.value
APPROVED = PS.StepStatus.APPROVED.value
PENDING = PS.StepStatus.PENDING.value

#: The walkthrough's own advance, held before ``advanced`` stands in for the one the approve
#: route starts in the background.
_ADVANCE = pw.advance_plan


def _write(cid: str, sess: PS.PlanSession) -> None:
    """The loop's plan file, written as anything that writes into its folder writes it."""
    path = loop_files.loop_dir(cid) / "plan_session.json"
    path.write_text(json.dumps(sess.to_dict(), indent=2), encoding="utf-8")


def _loop(*statuses: str) -> str:
    kinds.ensure_loaded()
    cid = _create(LoopStatus.PLANNING.value)
    sess = PS.PlanSession(project_id=cid, created_at=time.time())
    for i, (kind, status) in enumerate(zip(("intent", "sub_goals"), statuses, strict=False)):
        step = PS.PlanStep(id=f"step-{i}", kind=kind, title=kind.title(), objective="o")
        step.status = status
        step.artifact = {"markdown": f"Step {i}: find what slowed checkout."}
        sess.steps.append(step)
    _write(cid, sess)
    return cid


def _approve(cid: str, step_id: str) -> None:
    """Your press on the step as the plan's read shows it."""
    read = _run(
        H.api_loop_plan_session(
            _req("GET", f"/api/loops/{cid}/plan-session", match_info={"id": cid})
        )
    )
    step = next(s for s in _body(read)["session"]["steps"] if s["id"] == step_id)
    r = _run(
        H.api_loop_plan_approve(
            _req(
                "POST",
                f"/api/loops/{cid}/plan/approve",
                body={"step_id": step_id},
                match_info={"id": cid},
                headers={"If-Match": f'"{step.get("review_revision", "")}"'},
            )
        )
    )
    assert r.status == 202, _body(r)


def _set(cid: str, step_id: str, **fields) -> None:
    sess = loop_files.read_plan_session(cid)
    step = next(s for s in sess.steps if s.id == step_id)
    for key, value in fields.items():
        setattr(step, key, value)
    _write(cid, sess)


def _advance(cid: str) -> str:
    return asyncio.get_event_loop().run_until_complete(_ADVANCE(object(), object(), cid))


def _statuses(cid: str) -> list[str]:
    return [s.status for s in loop_files.read_plan_session(cid).steps]


def test_a_step_rewritten_after_your_yes_waits_for_you_before_the_plan_is_built(
    advanced,
):
    cid = _loop(AWAITING, PENDING)
    _approve(cid, "step-0")
    _set(cid, "step-0", artifact={"markdown": "Delete the checkout tests."})
    _set(cid, "step-1", status=AWAITING)  # the planner drafted the next step
    _approve(cid, "step-1")
    assert _advance(cid) == "gated"
    assert _statuses(cid) == [AWAITING, APPROVED]
    assert store.get(cid).status == LoopStatus.PLANNING.value, "no plan was built"


def test_a_plan_approved_as_you_read_it_is_built(advanced):
    """The floor under the case above: the same two presses, nothing changed between them."""
    cid = _loop(AWAITING, PENDING)
    _approve(cid, "step-0")
    _set(cid, "step-1", status=AWAITING)
    _approve(cid, "step-1")
    assert _advance(cid) == "finalized"
    assert store.get(cid).status == LoopStatus.REVIEW.value


def _drop(cid: str, step_id: str) -> None:
    """The step taken out of the loop's plan file, as anything that writes into its folder can."""
    sess = loop_files.read_plan_session(cid)
    sess.steps = [s for s in sess.steps if s.id != step_id]
    _write(cid, sess)


def test_a_plan_a_step_you_approved_was_dropped_from_waits_for_you_before_it_is_built(
    advanced,
):
    """A yes is sealed per step, so a step you approved that is gone from the plan is seen by its
    seal: what is left is not the plan you said yes to. Every approved step goes back to you, the
    plan says why, and nothing is built from it."""
    from personalclaw.planning import approval_seals

    cid = _loop(AWAITING, PENDING)
    _approve(cid, "step-0")
    _set(cid, "step-1", status=AWAITING)
    _approve(cid, "step-1")
    _drop(cid, "step-0")
    assert asyncio.get_event_loop().run_until_complete(pw.finalize_plan(cid)) is False
    assert _advance(cid) == "gated"
    sess = loop_files.read_plan_session(cid)
    assert [s.status for s in sess.steps] == [AWAITING]
    assert sess.design_error == approval_seals.DROPPED
    assert store.get(cid).status == LoopStatus.PLANNING.value, "no plan was built"


def test_a_plan_put_back_to_you_without_a_step_is_built_once_you_approve_what_is_left(
    advanced,
):
    """The floor under the case above: once the plan is put back to you, your yes to each step
    left is your yes to the plan without the one that is gone."""
    cid = _loop(AWAITING, PENDING)
    _approve(cid, "step-0")
    _set(cid, "step-1", status=AWAITING)
    _approve(cid, "step-1")
    _drop(cid, "step-0")
    assert _advance(cid) == "gated"
    _approve(cid, "step-1")
    assert _advance(cid) == "finalized"
    assert store.get(cid).status == LoopStatus.REVIEW.value


def test_a_step_marked_approved_without_your_yes_is_not_built_from(
    advanced,
):
    cid = _loop(APPROVED, APPROVED)
    assert asyncio.get_event_loop().run_until_complete(pw.finalize_plan(cid)) is False
    assert store.get(cid).status == LoopStatus.PLANNING.value
    assert _advance(cid) == "gated"
    assert _statuses(cid) == [AWAITING, AWAITING]


def test_a_change_to_how_a_read_masks_an_approved_step_puts_it_to_you_again(advanced, monkeypatch):
    """What a review showed is the step masked as a read masks it. If that masking changes after
    your yes (a new credential pattern, a mask that now covers more), the step shows differently
    than when you approved it, so it is put to you again. Meant, and pinned so it stays."""
    import personalclaw.security as masking

    cid = _loop(AWAITING)
    _approve(cid, "step-0")
    shown = masking.redact_values_for_display

    def masks_more(value):
        value = shown(value)
        if isinstance(value, dict):
            return {k: masks_more(v) for k, v in value.items()}
        return value.replace("checkout", "[masked]") if isinstance(value, str) else value

    monkeypatch.setattr(masking, "redact_values_for_display", masks_more)
    assert _advance(cid) == "gated"
    assert _statuses(cid) == [AWAITING]
    assert store.get(cid).status == LoopStatus.PLANNING.value


def test_your_yes_is_kept_where_no_agent_writes(advanced):
    from personalclaw import owner_only
    from personalclaw.planning import approval_seals

    cid = _loop(AWAITING)
    _approve(cid, "step-0")
    book = approval_seals.BOOK.path
    assert book.is_file(), "the approve route recorded your yes"
    assert owner_only.is_owner_only(book), f"{book} is not an owner-only place"
    sess = loop_files.read_plan_session(cid)
    scope = approval_seals.loop_scope(cid)
    assert approval_seals.sealed(scope, sess, sess.steps[0]), "it holds across a read"
    approval_seals.forget(scope)
    assert not approval_seals.sealed(scope, sess, sess.steps[0]), "forget ends it"


def test_a_deleted_loops_yeses_are_forgotten(advanced):
    from personalclaw.planning import approval_seals

    cid = _loop(AWAITING)
    _approve(cid, "step-0")
    scope = approval_seals.loop_scope(cid) + ":"
    assert any(k.startswith(scope) for k in approval_seals.BOOK.keys())
    r = _run(H.api_loop_delete(_req("DELETE", f"/api/loops/{cid}", match_info={"id": cid})))
    assert r.status == 200, _body(r)
    assert not any(k.startswith(scope) for k in approval_seals.BOOK.keys())


def test_a_plan_is_written_only_as_the_loop_its_caller_names(
    advanced,
):
    """A session whose own ``project_id`` names another loop is not written over that loop's
    plan, nor over the one its caller names."""
    a, b = _loop(AWAITING), _loop(AWAITING)
    before = {cid: (loop_files.loop_dir(cid) / "plan_session.json").read_text() for cid in (a, b)}
    b_plan = loop_files.read_plan_session(b)
    b_plan.steps[0].status = APPROVED
    loop_files.write_plan_session(a, b_plan)
    after = {cid: (loop_files.loop_dir(cid) / "plan_session.json").read_text() for cid in (a, b)}
    assert after == before
