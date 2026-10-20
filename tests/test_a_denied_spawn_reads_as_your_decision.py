"""Your Deny of a step's start is your decision, and the run says so — not that something broke.

Measured on a running gateway: she denied the approval a batch step's subagent asked for before it
started. The step was recorded as an internal failure — ``{"class": "internal", "cause_plain":
"spawn declined, so it never started", "remediation": "check the subagent's transcript for the
failing turn"}``, a transcript that never existed — and a "Workflow run failed" item came back to
her Inbox asking for her input on her own answer.

A person's Deny is a decline: the step is DECLINED "denied by you", with no failure class and no
remedy, the run ends ``declined`` naming the step, and no failure is raised. A start nobody approved
(no answer in time) is still a step that did not run, and its remedy says what to do about that
instead of pointing at a transcript.

Driven through a real ``RunController`` over a real ``SubagentManager`` whose spawn approval goes
through the real ``DashboardState.request_approval``, answered through the real decision path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    _until,
)

from personalclaw.approval_answer import YOU
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    FailureClass,
    InstanceState,
    RunStatus,
    WorkflowRun,
)


def _spec() -> dict[str, Any]:
    return {
        "name": "subagent-batch-1",
        "root": {
            "kind": "parallel",
            "id": "batch",
            "config": {"join": "quorum", "quorum": 1},
            "children": [
                {
                    "kind": "stage",
                    "id": "audit_the_notes_0",
                    "label": "Audit the notes",
                    "config": {"prompt": "audit the notes"},
                }
            ],
        },
    }


def _relaying_manager(state):
    """A real SubagentManager whose spawn gate asks the REAL dashboard state and relays how the
    approval ended, as the gateway does (`Gateway._asked_decision`): a Deny is yours, an approval
    nobody answered is nobody's."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.approval_grants import NOBODY
    from personalclaw.approval_grants import YOU as DECIDED_BY_YOU
    from personalclaw.approval_grants import ToolDecision
    from personalclaw.subagent import SubagentManager

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=RuntimeError("no provider in this test"))
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("audit the notes", None))
    ctx.hooks.auto_approve_subagent_spawn = False
    holder: dict[str, Any] = {}

    async def _spawn_approve(event: Any, parent_key: str = "") -> ToolDecision:
        request_id = str(event.request_id)
        info = holder["manager"].get(request_id.removeprefix("spawn:"))
        approved = await state.request_approval(
            request_id, "subagent", event.title, session=info.parent_session_key if info else ""
        )
        ended = state.ended_as(request_id)
        if ended in ("expired", "cancelled"):
            return ToolDecision(False, ended, NOBODY)
        return ToolDecision(approved, "approved" if approved else "rejected", DECIDED_BY_YOU)

    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, on_spawn_approval=_spawn_approve, is_yolo=lambda: False
    )
    holder["manager"] = manager
    state.subagents = manager
    return manager


@pytest.fixture
def batch(world, monkeypatch):  # noqa: F811 - the imported fixture
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    manager = _relaying_manager(world.state)
    run = store.create(WorkflowRun(id="", workflow_name="subagent-batch-1"))
    store.write_spec(run.id, _spec())
    world.run = run
    world.controller = RunController(
        run,
        _spec(),
        services=EngineServices(subagents=manager, cwd="", attention_state=world.state),
    )
    return world


async def _asked(w) -> str:
    await _until(
        lambda: any(a.startswith("spawn:") for a in w.state._pending_approvals),
        "the step's start never asked for approval",
    )
    (approval_id,) = [a for a in w.state._pending_approvals if a.startswith("spawn:")]
    return approval_id


def _failure_rows(w) -> list:
    return [
        i
        for i in w.inbox.items.values()
        if i.refs.get("workflow") == w.run.id and i.item_kind == "needs_input"
    ]


@pytest.mark.asyncio
async def test_your_deny_declines_the_step_and_raises_no_failure(batch):
    w = batch
    driving = asyncio.create_task(w.controller.run_to_completion(timeout=20.0))
    approval_id = await _asked(w)

    assert w.state.resolve_approval(approval_id, False, by=YOU) is True
    status = await asyncio.wait_for(driving, timeout=10)

    (inst,) = store.read_state(w.run.id).values()
    assert inst.state is InstanceState.DECLINED, inst.state
    assert inst.failure is None, "her Deny was recorded as a failure"
    assert inst.degraded_reason == "denied by you"
    assert status is RunStatus.DECLINED
    run = store.get(w.run.id)
    assert run.error_message == "“Audit the notes” was denied by you.", run.error_message
    assert not _failure_rows(w), "her own Deny came back to her Inbox as a failure"
    # Her answer is a decision: its own row reads handled.
    (row,) = [i for i in w.inbox.items.values() if i.refs.get("approval") == approval_id]
    assert row.status == "handled"


@pytest.mark.asyncio
async def test_a_start_nobody_approved_points_at_no_transcript(batch, monkeypatch):
    """Nobody answered in time: the step did not run, which is not her decision — it fails, and
    its remedy is to run it again and answer, since it never ran a turn to read."""
    w = batch
    monkeypatch.setattr(type(w.state), "approval_window_secs", lambda _self: 0.05)
    status = await asyncio.wait_for(w.controller.run_to_completion(timeout=20.0), timeout=10)

    (inst,) = store.read_state(w.run.id).values()
    assert inst.state is InstanceState.FAILED, inst.state
    assert status is RunStatus.FAILED
    assert inst.failure.failure_class is FailureClass.PERMISSION
    assert "never started" in inst.failure.cause_plain
    assert "transcript" not in inst.failure.remediation, inst.failure.remediation
    assert inst.failure.remediation == "run this step again, and answer its approval when it asks"
