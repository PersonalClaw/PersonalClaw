"""An approval nobody answered is not a Deny — in the chat's steps and the audit log either.

The chat's "Worked through N steps" summary names each step by its transcript row. When an
approval's window closed with nobody there, the runner wrote that row as ``bash (rejected)`` and
audited the call as ``rejected``: it treated the timeout as a refusal. #3698's Inbox note says what
happened ("Denied, no answer: bash"), and the approval card says "not run — no answer in time", so
the same call read three ways, and the steps line and the audit log's Denied filter both claimed a
decision nobody made. The audit families already leave ``expired`` out of Denied for exactly this
reason (``sel.AUDIT_OUTCOME_FAMILIES``).

The runner refuses the rest of a refused batch without asking again. After an expiry those calls
were written ``(rejected)`` too, so they now say the same thing the expired call does.

Driven through the real chat runner, with a window short enough to close.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import _bash_request, _finish, _start, _turn, _until

from personalclaw.sel import SecurityEventLog


@pytest.fixture
def audit(monkeypatch) -> list:
    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


def _steps(session) -> list[str]:
    """The transcript rows the steps summary names, in order."""
    return [m["content"] for m in session.messages if m.get("role") == "tool"]


def _recorded(session) -> list[str]:
    """How each permission row says it ended — what a reload's card renders."""
    return [
        json.loads(m["cls"]).get("resolved", "")
        for m in session.messages
        if m.get("role") == "permission"
    ]


def _invocations(audit: list) -> list[tuple[str, str, str]]:
    return [
        (e.operation, e.outcome, (e.metadata or {}).get("reason", ""))
        for e in audit
        if e.event_type == "tool_invocation"
    ]


@pytest.mark.asyncio
async def test_the_steps_say_an_expired_call_was_denied_with_no_answer(
    world, audit, monkeypatch  # noqa: F811 - the imported fixture, by name
):
    monkeypatch.setattr(world.state, "approval_window_secs", lambda: 0.05)
    task = await _start(world, _turn(_bash_request()))
    await _until(lambda: not world.state._pending_approvals, "the approval never expired")
    await asyncio.wait_for(task, timeout=5)

    assert _steps(world.session) == ["bash (denied, no answer)"]
    assert _recorded(world.session) == ["expired"]
    assert _invocations(audit) == [("bash", "expired", "interactive")], _invocations(audit)


@pytest.mark.asyncio
async def test_a_call_refused_after_it_in_the_same_batch_says_the_same(
    world, audit, monkeypatch  # noqa: F811
):
    """The rest of the batch is refused without asking — because nobody answered, not because
    anybody said no."""
    monkeypatch.setattr(world.state, "approval_window_secs", lambda: 0.05)
    task = await _start(
        world,
        _turn(
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _bash_request("req-2", "find /tmp/scratch -delete"),
        ),
    )
    await asyncio.wait_for(task, timeout=5)

    assert _steps(world.session) == ["bash (denied, no answer)", "bash (denied, no answer)"]
    assert _recorded(world.session) == ["expired", "expired"]
    assert _invocations(audit) == [
        ("bash", "expired", "interactive"),
        ("bash", "expired", "batch_rejection"),
    ], _invocations(audit)
    world.client.reject_tool.assert_any_call("req-2")


@pytest.mark.asyncio
async def test_a_person_s_deny_still_reads_rejected(world, audit):  # noqa: F811
    """The control: an answer IS a decision, and its rows say so."""
    task = await _start(
        world,
        _turn(
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _bash_request("req-2", "find /tmp/scratch -delete"),
        ),
    )
    try:
        world.state.decide_session_approval(world.session, "req-1", "rejected")
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    assert _steps(world.session) == ["bash (rejected)", "bash (rejected)"]
    assert _recorded(world.session) == ["rejected", "rejected"]
    assert [o for _, o, _ in _invocations(audit)] == ["rejected", "rejected"]
