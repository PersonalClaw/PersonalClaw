"""A queued message of the owner's runs until it is done; a turn nobody is watching is stopped
only once it goes quiet.

Measured: in an attended dashboard chat, the owner sent a follow-up while a long turn ran. It was
queued, and when its turn came it ran under the wall-clock limit meant for turns that run on their
own: ten minutes in, with the agent still working (it had just written a test file and started the
tests), the turn was stopped with "This turn ran past its 10-minute limit and was stopped. Send
your message again to retry." Turns typed into the same chat while it was idle ran 40 minutes and
more. A message the owner typed now runs as one typed into an idle chat does, queued or not.

The turns that stay bounded (a subagent's report, a loop's nudge, an automation's result, a message
from a linked channel) are bounded by silence: each event that shows the turn working starts the
clock again, and the clock stops while one of its calls runs or waits on the owner's answer.
Stopped, the turn says how far it got and that the chat is free, and does not ask the owner to
send anything again.

Driven through the real chat runner and its queue, with a scripted runtime and limits of a second.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from test_dashboard_approval import _context_builder, _make_session, _make_state

from personalclaw.dashboard import turn_deadline
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE
from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)
from personalclaw.llm.events import EVENT_TEXT_CHUNK

LIMIT = "personalclaw.dashboard.chat_runner.CHAT_TURN_IDLE_LIMIT"
REPORT = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1b2c3` completed ✅\nThe flaky test is fixed."
STOPPED_QUIET = (
    "This turn was stopped after 1 second with no progress, before its first step. What it did "
    "is above, and the chat is free for your next message."
)


async def _answers_at_once():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The first answer.")
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _keeps_working():
    """Streams for 2.5 s, well past the 1 s limit, never quiet for more than 0.25 s."""
    for n in range(10):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"step {n}. ")
        await asyncio.sleep(0.25)
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="All of it is done.")
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _goes_quiet():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Reading the report first")
    await asyncio.sleep(60)
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _one_long_call():
    """One call that runs 2.5 s, past the 1 s limit, with nothing streamed while it runs."""
    yield LLMEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id="tc-tests",
        title="Running: pytest -q",
        tool_kind="execute",
        tool_input="pytest -q",
    )
    await asyncio.sleep(2.5)
    yield LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="tc-tests", tool_output="12 passed")
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The tests pass.")
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


def _errors(session):
    return [m["content"] for m in session.messages if m.get("role") == "error"]


def _answered(session, words):
    return any(words in m["content"] for m in session.messages if m["role"] == "assistant")


async def _both_turns_end(state, session):
    """The chat is done once the queued turn ends: a turn that hands over to the queue sends no
    ``chat_done`` of its own, so the one frame is the queued turn's."""
    for _ in range(1500):
        if _frames(state, "chat_done") and session.task is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the queued turn never ended: {session.messages}")


async def _queue_behind_a_turn(state, session, queue):
    """Start a turn of the owner's, and queue the second message behind it with *queue*."""
    assert session.enqueue_or_run_prompt("what failed overnight?", run_chat, state)
    queue()
    await _both_turns_end(state, session)


# ── the owner's own queued message ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owners_queued_message_runs_past_the_limit_until_it_is_done(tmp_path):
    """Red before the fix: its turn was stopped one second in, at the wall-clock limit, though
    it never stopped working. Queued as the dashboard's send queues it."""
    state, session = _chat(tmp_path, _keeps_working)
    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _queue_behind_a_turn(
            state,
            session,
            lambda: session.queue_append("now fix it and run the tests", attended=True),
        )

    assert _errors(session) == []
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["complete"]
    assert _answered(session, "All of it is done.")


@pytest.mark.asyncio
async def test_the_owners_inbox_reply_queued_behind_a_turn_runs_until_it_is_done(tmp_path):
    """An Inbox reply the owner typed is attended too (``enqueue_or_run_prompt(attended=True)``)."""
    state, session = _chat(tmp_path, _keeps_working)
    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _queue_behind_a_turn(
            state,
            session,
            lambda: session.enqueue_or_run_prompt("yes, go", run_chat, state, attended=True),
        )

    assert _errors(session) == []
    assert _answered(session, "All of it is done.")


@pytest.mark.asyncio
async def test_the_owners_queued_message_is_not_bounded_even_when_it_goes_quiet(tmp_path):
    """The owner's runs as a turn typed into an idle chat does: nothing here stops it, their Stop
    does."""
    state, session = _chat(tmp_path, _goes_quiet)
    with patch(LIMIT, 0.5), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        assert session.enqueue_or_run_prompt("what failed?", run_chat, state)
        session.queue_append("and why?", attended=True)
        for _ in range(300):
            if any("Reading the report" in (m.get("content") or "") for m in session.messages):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(1.5)  # three times the limit, in silence
        assert session.running and _errors(session) == []
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)


# ── a turn nobody is watching ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_queued_report_that_goes_quiet_is_stopped_and_says_the_chat_is_free(tmp_path):
    """Red before the fix: it ended telling the owner to retry, and said nothing of how far it
    got."""
    state, session = _chat(tmp_path, _goes_quiet)
    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _queue_behind_a_turn(state, session, lambda: session.queue_append(REPORT))

    assert _errors(session) == [STOPPED_QUIET]
    assert TURN_CUT_SHORT_NOTICE not in _errors(session)
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["error"]
    # What it had written before it went quiet stays, ahead of the notice.
    rows = [m["role"] for m in session.messages]
    assert rows.index("error") > max(i for i, r in enumerate(rows) if r == "assistant")


@pytest.mark.asyncio
async def test_a_queued_report_that_keeps_working_is_not_stopped(tmp_path):
    """Red before the fix: the bound was on how long it ran, not on how long it was quiet."""
    state, session = _chat(tmp_path, _keeps_working)
    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _queue_behind_a_turn(state, session, lambda: session.queue_append(REPORT))

    assert _errors(session) == []
    assert _answered(session, "All of it is done.")


@pytest.mark.asyncio
async def test_a_bounded_turn_is_not_stopped_while_one_long_call_runs(tmp_path):
    """Red before the fix: nothing streams while a long test run works, and the clock ran."""
    state, session = _chat(tmp_path, _one_long_call)
    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _queue_behind_a_turn(state, session, lambda: session.queue_append(REPORT))

    assert _errors(session) == []
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["complete"]
    assert _answered(session, "The tests pass.")


@pytest.mark.asyncio
async def test_a_bounded_turn_waiting_on_the_owners_answer_is_not_on_the_clock(tmp_path):
    """Its call waits on the owner's answer for longer than the limit; the turn still finishes."""
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

    with patch(LIMIT, 1.0), patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        answering = asyncio.create_task(answer_late())
        await _queue_behind_a_turn(state, session, lambda: session.queue_append(REPORT))
        await answering

    assert _errors(session) == []
    assert [d["outcome"] for d in _frames(state, "chat_done")] == ["complete"]
    assert _answered(session, "the tests pass")


@pytest.mark.asyncio
async def test_a_turn_with_anything_but_the_owners_in_it_is_bounded(tmp_path):
    """The owner's message merged with an automation's result is not all theirs, so its turn is
    bounded."""
    state, session = _chat(tmp_path, _goes_quiet)
    with (
        patch(LIMIT, 1.0),
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch(
            "personalclaw.dashboard.chat_runner._dequeue_next_message",
            lambda s, merge_enabled: ("both", [s.queue_pop(0), s.queue_pop(0)]),
        ),
    ):

        def queue():
            session.queue_append("and why?", attended=True)
            session.queue_append("nightly backup finished")

        await _queue_behind_a_turn(state, session, queue)

    assert _errors(session) == [STOPPED_QUIET]


# ── how far it got, and its retry ──────────────────────────────────────────────────────────


def test_the_notice_says_how_far_the_turn_got():
    from personalclaw.dashboard.turn_endings import past_limit_notice

    assert past_limit_notice(900.0, steps=12) == (
        "This turn was stopped after 15 minutes with no progress, 12 steps in. What it did is "
        "above, and the chat is free for your next message."
    )
    assert "1 minute with no progress, 1 step in." in past_limit_notice(60.0, steps=1)
    assert "Send" not in past_limit_notice(900.0, steps=0)


@pytest.mark.asyncio
async def test_a_retry_is_bounded_only_when_the_attempt_it_repeats_was():
    """A turn's retry asks whether this attempt was bounded (`turn_deadline.bounded`)."""
    seen: list[bool] = []

    async def _attempt():
        seen.append(turn_deadline.bounded())

    state = MagicMock()
    state.waiting_on_owner.return_value = False
    await turn_deadline.run_within(state, MagicMock(key="k"), _attempt(), 1.0)
    await _attempt()
    assert seen == [True, False]
