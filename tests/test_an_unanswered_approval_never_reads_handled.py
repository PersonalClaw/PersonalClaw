"""An approval that ends with nobody answering it expires, saying why — it never reads "Handled".

Measured on a running gateway: at a gateway shutdown, two approvals a batch run was waiting on
were closed in the Inbox as "handled", with no decision anyone had made. "Handled" is the Inbox's
word for a request its owner dealt with, and a row a restart closed carried it, with a check mark.

The approval's answer is awaited in memory, so none survives a restart, and the ruling is the one
that stays true everywhere: an approval nobody answered EXPIRES, and its row says why — the gateway
stopped or restarted, nobody answered in time, or the work that asked was stopped. Only an answer
makes a row handled.
"""

from __future__ import annotations

import asyncio

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (  # the shared approval harness
    _resolved,
    _until,
)

from personalclaw import restart_request, shutdown_event
from personalclaw.approval_answer import YOU
from personalclaw.inbox import ItemKind, emit_attention_item


def _row(w, approval_id: str):
    (row,) = [i for i in w.inbox.items.values() if i.refs.get("approval") == approval_id]
    return row


async def _waiting(w, approval_id: str) -> asyncio.Task:
    task = asyncio.create_task(
        w.state.request_approval(approval_id, "subagent", "subagent_run(audit)", session="")
    )
    await _until(lambda: approval_id in w.state._pending_approvals, "never listed")
    return task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stopping", "verb"),
    [(restart_request.SHUTTING_DOWN, "stopped"), (restart_request.RESTARTING, "restarted")],
)
async def test_an_ask_the_gateway_stopping_ended_expires(
    world, monkeypatch, stopping, verb  # noqa: F811
):
    task = await _waiting(world, "spawn:aa0001")
    monkeypatch.setattr(shutdown_event, "is_set", lambda: True)
    monkeypatch.setattr(restart_request, "stopping_for", lambda: stopping)

    task.cancel()  # what a stopping gateway does to every waiter
    with pytest.raises(asyncio.CancelledError):
        await task

    row = _row(world, "spawn:aa0001")
    assert row.status == "expired", row.status
    assert row.refs["ended"] == f"the gateway {verb} before anyone answered"
    (frame,) = _resolved(world, "spawn:aa0001")
    assert frame["outcome"] == "cancelled" and frame["ended"] == row.refs["ended"], frame


def test_a_row_left_open_by_a_previous_process_expires_at_boot(world):  # noqa: F811
    emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: subagent_run",
        body="A step of a workflow run is waiting for your decision on subagent_run.",
        refs={"approval": "spawn:bb0002"},
        dedup_key="approval:spawn:bb0002",
    )

    assert world.state.close_orphaned_approval_rows() == 1

    row = _row(world, "spawn:bb0002")
    assert row.status == "expired", row.status
    assert row.refs["ended"] == "the gateway restarted before anyone answered"


@pytest.mark.asyncio
async def test_an_ask_nobody_answered_in_time_expires(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(world.state), "approval_window_secs", lambda _self: 0.05)
    assert await world.state.request_approval("spawn:cc0003", "subagent", "subagent_run") is False

    row = _row(world, "spawn:cc0003")
    assert row.status == "expired", row.status
    assert row.refs["ended"] == "nobody answered within 0 seconds"


@pytest.mark.asyncio
async def test_an_answer_is_what_makes_a_row_handled(world):  # noqa: F811
    """The positive control: a decision still reads as one."""
    task = await _waiting(world, "spawn:dd0004")
    assert world.state.resolve_approval("spawn:dd0004", False, by=YOU) is True
    assert await asyncio.wait_for(task, timeout=2) is False

    row = _row(world, "spawn:dd0004")
    assert row.status == "handled"
    assert "ended" not in row.refs
