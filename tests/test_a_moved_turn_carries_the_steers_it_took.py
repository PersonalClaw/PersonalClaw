"""A turn moved to another runtime is asked again with the steers it had taken, after her message.

Measured before this was written: a steer a running turn takes becomes her row after the turn's
message ("Steered into the answer"). Moved to another agent before any answer, the turn asked her
message again on the new runtime as the same turn, and a fresh runtime is handed the conversation
before that message, so the steer, after it, was in nothing the new agent read: it answered her
message as if the steer was never sent.

Now the turn is sent again with each steer its attempt took, in order (``running_turn.say_moved``
hands them to the retry, ``_ChatSession.queue_retry`` keeps them, and the retried ``run_chat``
asks her message followed by them). Nothing is added to the chat: the rows are the same. A move
after the answer started is unchanged, and a turn moved with no steer is asked her message alone.
"""

from __future__ import annotations

import pytest
from chat_test_helpers import _make_state
from test_dashboard_approval import _complete_event, _context_builder
from test_dashboard_approval import _make_state as _approval_state
from test_dashboard_approval import _patch_stats, _set_stream

from personalclaw.dashboard import running_turn
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.chat_utils import _dequeue_next_message
from personalclaw.turn_source import STEERED

pytestmark = pytest.mark.asyncio

HER = "Find the parser tests that fail on main."
STEERS = ["Only look in the src folder.", "And skip the slow ones."]


def _turn_with_steers(state, steers=STEERS):
    chat = state.get_or_create_session("chat-1-1791067183")
    chat.append("user", HER, "msg msg-u", broadcast=False)
    for words in steers:
        chat.append("user", words, "msg msg-u", meta={STEERED: True}, broadcast=False)
    chat._rebinding = running_turn.Rebinding({"agent": "reviewer"})
    return chat


async def _moved(state, chat, *, answered: bool = False):
    sent: list[list[str]] = []
    turn_row = next(m for m in chat.messages if m.get("content") == HER)

    def attempt():
        at = chat.messages.index(turn_row)
        return turn_row, chat.messages[at + 1 :]

    rows_before = [dict(m) for m in chat.messages]
    moved = await running_turn.say_moved(
        state, chat, chat.key, answered, lambda steered=(): sent.append(list(steered)), attempt
    )
    return moved, sent, rows_before


async def test_a_moved_turn_is_sent_again_with_its_steers_in_order(tmp_path):
    """🔴 Red before: it was sent again with her message alone."""
    state = _make_state(tmp_path)
    chat = _turn_with_steers(state)

    moved, sent, rows_before = await _moved(state, chat)

    assert moved
    assert sent == [STEERS]
    rows_after = [m for m in chat.messages if m.get("role") != "notice"]
    assert rows_after == rows_before, "the move added or changed a row of hers"


async def test_a_moved_turn_with_no_steer_is_sent_again_with_her_message_alone(tmp_path):
    state = _make_state(tmp_path)
    chat = _turn_with_steers(state, steers=[])

    _, sent, _ = await _moved(state, chat)

    assert sent == [[]]


async def test_a_move_after_the_answer_started_sends_nothing_again(tmp_path):
    state = _make_state(tmp_path)
    chat = _turn_with_steers(state)

    _, sent, _ = await _moved(state, chat, answered=True)

    assert sent == []


async def test_the_retry_keeps_the_steers_and_is_asked_with_them_after_her_message(tmp_path):
    """🔴 Red before: the retry held her message alone, and was asked it alone."""
    cb = _context_builder()
    state, client = _approval_state(tmp_path, context_builder=cb)
    chat = _turn_with_steers(state)
    chat._rebinding = None
    _set_stream(client, [_complete_event()])

    chat.queue_retry(HER, steered=STEERS)
    message, consumed = _dequeue_next_message(chat, merge_enabled=True)
    assert consumed[0]["steered"] == STEERS
    with _patch_stats():
        await run_chat(state, chat, message, _retry=True, steered=consumed[0]["steered"])

    asked = cb.build_message.call_args.args[0]
    assert asked == "\n\n".join([HER, *STEERS])
