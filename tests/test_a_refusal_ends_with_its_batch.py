"""A refusal reaches the calls asked for with it, and the next step's call asks again.

The approval card promises "Nothing is remembered. The next tool call asks again." One Deny used to
refuse every later tool call of the turn without a card: the runner held the refusal until the turn
ended, so a call the model made two steps later, after it had read the refusal, was refused in the
person's name without her ever seeing it. The same held for an approval nobody answered in time: the
turn's later calls were written up as "no answer in time" without having been asked at all.

A refusal still covers the requests the agent had already sent when it was decided: they wait in the
stream right behind it. Anything else the agent reports first (the refused call's result, a card, a
word of text) means it has the answer, and the next call it makes asks again. A stopped turn is the
exception, because nothing more of it should ask.

Driven through the real chat runner.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from test_dashboard_approval import _set_stream
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import _bash_request, _finish, _until

from personalclaw.approval_answer import YOU
from personalclaw.dashboard.chat import run_chat
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.sel import SecurityEventLog


@pytest.fixture
def audit(monkeypatch) -> list:
    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


def _card(request_id: str, command: str) -> LLMEvent:
    """The call's card, which the native runtime reports just before it asks about the call."""
    return LLMEvent(
        kind=EVENT_TOOL_CALL,
        title="bash",
        tool_kind="execute",
        tool_call_id=f"tc-{request_id}",
        tool_input=json.dumps({"command": command}),
    )


def _refused_result(request_id: str) -> LLMEvent:
    """What the runtime reports for a call it was told not to run."""
    return LLMEvent(
        kind=EVENT_TOOL_RESULT,
        title="bash",
        tool_call_id=f"tc-{request_id}",
        tool_output="Error: bash was not run: the user declined this tool call.",
        tool_meta={"ok": False},
    )


def _asked(world) -> list[str]:  # noqa: F811 - the imported fixture, by name
    """The calls a card was raised for, in order."""
    return [d["request_id"] for kind, d in world.frames if kind == "approval"]


def _decisions(audit: list) -> list[tuple[str, str]]:
    """How each asked or refused call was audited: its outcome, and whether it was asked."""
    return [
        (e.outcome, (e.metadata or {}).get("reason", ""))
        for e in audit
        if e.event_type == "tool_invocation"
        and (e.metadata or {}).get("reason") in ("interactive", "batch_rejection")
    ]


async def _run(world, events) -> asyncio.Task:  # noqa: F811
    _set_stream(world.client, events)
    return asyncio.create_task(run_chat(world.state, world.session, "clean up the scratch dir"))


async def _asks(world, request_id: str) -> None:  # noqa: F811
    await _until(
        lambda: request_id in world.session._approval_futures,
        f"{request_id} never asked: it was refused without a card",
    )


@pytest.mark.asyncio
async def test_the_next_step_s_call_asks_again_after_a_deny(world, audit):  # noqa: F811
    """The native shape: the refused call's result, then the model's next step makes a call."""
    task = await _run(
        world,
        [
            _card("req-1", "rm -rf /tmp/scratch"),
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _refused_result("req-1"),
            _card("req-2", "ls /tmp/scratch"),
            _bash_request("req-2", "ls /tmp/scratch"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="I left the scratch dir as it is."),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    try:
        await _asks(world, "req-1")
        world.state.decide_session_approval(world.session, "req-1", "rejected", by=YOU)
        await _asks(world, "req-2")
        world.state.decide_session_approval(world.session, "req-2", "rejected", by=YOU)
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    assert _asked(world) == ["req-1", "req-2"]
    assert _decisions(audit) == [("rejected", "interactive"), ("rejected", "interactive")]
    world.client.reject_tool.assert_any_call("req-2")


@pytest.mark.asyncio
async def test_a_deny_refuses_the_requests_waiting_behind_it_and_nothing_after(
    world, audit  # noqa: F811
):
    """Two requests sent together are one decision; the call after the agent heard it asks."""
    task = await _run(
        world,
        [
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _bash_request("req-2", "find /tmp/scratch -delete"),
            _refused_result("req-1"),
            _refused_result("req-2"),
            _bash_request("req-3", "ls /tmp/scratch"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Nothing was removed."),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    try:
        await _asks(world, "req-1")
        world.state.decide_session_approval(world.session, "req-1", "rejected", by=YOU)
        await _asks(world, "req-3")
        world.state.decide_session_approval(world.session, "req-3", "rejected", by=YOU)
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    assert _asked(world) == ["req-1", "req-3"]
    assert _decisions(audit) == [
        ("rejected", "interactive"),
        ("rejected", "batch_rejection"),
        ("rejected", "interactive"),
    ]
    for rid in ("req-1", "req-2", "req-3"):
        world.client.reject_tool.assert_any_call(rid)


@pytest.mark.asyncio
async def test_the_next_step_s_call_asks_again_after_an_approval_expires(
    world, audit, monkeypatch  # noqa: F811
):
    """An unanswered approval says "no answer" only for calls that were asked and waited."""
    monkeypatch.setattr(world.state, "approval_window_secs", lambda: 0.05)
    task = await _run(
        world,
        [
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _refused_result("req-1"),
            _bash_request("req-2", "ls /tmp/scratch"),
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Nobody answered, so nothing ran."),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    try:
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    assert _asked(world) == ["req-1", "req-2"]
    assert _decisions(audit) == [("expired", "interactive"), ("expired", "interactive")]


@pytest.mark.asyncio
async def test_a_stopped_turn_asks_nothing_more(world, audit):  # noqa: F811
    """A stop ends every approval of the turn, and the calls after it are not asked either."""
    task = await _run(
        world,
        [
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            _refused_result("req-1"),
            _bash_request("req-2", "ls /tmp/scratch"),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="cancelled"),
        ],
    )
    try:
        await _asks(world, "req-1")
        assert world.state.cancel_turn_approvals(world.session.key) == 1
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    assert _asked(world) == ["req-1"]
    assert _decisions(audit) == [("cancelled", "interactive"), ("cancelled", "batch_rejection")]
    world.client.reject_tool.assert_any_call("req-2")
