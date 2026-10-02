"""A queued turn that runs past its time limit ends saying so, and its clock stops while it asks.

A message sent while another turn runs is queued, and its turn — like a retry's, or a message the
linked channel sent meanwhile — is bounded so a wedged one cannot hold the chat. Measured: when
that bound ran out, the chat said "The reply stopped before it finished. Send your message again
to retry." — the notice for a reply a fault cut short — and the turn ended as a stop nobody
pressed. It now ends in an error that says it ran past its limit, once. And the bound is on the
turn's own work: while one of its calls waits on her answer, the clock stops.

Driven through the real chat runner and its queue, with a scripted runtime.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from test_dashboard_approval import _context_builder, _make_session, _make_state

from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK

PAST_LIMIT = (
    "This turn ran past its 1-second limit and was stopped. Send your message again to retry."
)


async def _answers_at_once():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The first answer.")
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _never_finishes():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Reading the whole history first")
    await asyncio.sleep(60)
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _asks_then_answers():
    yield LLMEvent(kind=EVENT_PERMISSION_REQUEST, title="Run command", request_id="req-9")
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Ran it: the tests pass.")
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _chat(tmp_path, second_turn):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    turns = [_answers_at_once, second_turn]
    client.stream = MagicMock(side_effect=lambda *a, **k: turns.pop(0)())
    client.context_usage_pct = MagicMock(return_value=None)
    session = _make_session("chat-7-queue")
    session._titled = True  # no title call: the runtime plays these two turns only
    state._sessions[session.key] = session
    return state, session


def _frames(state, kind):
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args and c.args[0] == kind]


async def _both_turns_end(state, session):
    """The chat is done once the queued turn ends: a turn that hands over to the queue sends no
    ``chat_done`` of its own, so the one frame is the queued turn's."""
    for _ in range(1500):
        if _frames(state, "chat_done") and session.task is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the queued turn never ended: {session.messages}")


@pytest.mark.asyncio
async def test_a_queued_turn_past_its_limit_ends_in_the_error_that_says_so(tmp_path):
    """Red before the fix: the queued turn ended "stopped", on the cut-short notice."""
    state, session = _chat(tmp_path, _never_finishes)
    with (
        patch("personalclaw.dashboard.chat_runner.CHAT_TURN_TIMEOUT", 1.0),
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
    ):
        assert session.enqueue_or_run_prompt("what failed overnight?", run_chat, state)
        assert not session.enqueue_or_run_prompt("and what changed since?", run_chat, state)
        await _both_turns_end(state, session)

    errors = [m["content"] for m in session.messages if m.get("role") == "error"]
    assert errors == [PAST_LIMIT]
    assert TURN_CUT_SHORT_NOTICE not in errors
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["error"]
    # What it had written before the limit stays, ahead of the notice.
    rows = [m["role"] for m in session.messages]
    assert rows.index("error") > max(i for i, r in enumerate(rows) if r == "assistant")


@pytest.mark.asyncio
async def test_a_queued_turn_waiting_on_her_answer_is_not_on_the_clock(tmp_path):
    """Its call waits on her answer for longer than the limit; the turn still finishes."""
    state, session = _chat(tmp_path, _asks_then_answers)

    async def answer_late():
        for _ in range(500):
            fut = session._approval_futures.get("req-9")
            if fut is not None:
                await asyncio.sleep(2.5)  # past the limit
                fut.set_result("approved")
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the queued turn never asked")

    with (
        patch("personalclaw.dashboard.chat_runner.CHAT_TURN_TIMEOUT", 1.0),
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
    ):
        assert session.enqueue_or_run_prompt("what failed overnight?", run_chat, state)
        assert not session.enqueue_or_run_prompt("run the tests", run_chat, state)
        answering = asyncio.create_task(answer_late())
        await _both_turns_end(state, session)
        await answering

    assert [m["content"] for m in session.messages if m.get("role") == "error"] == []
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["complete"]
    assert any(
        "the tests pass" in m["content"] for m in session.messages if m["role"] == "assistant"
    )
