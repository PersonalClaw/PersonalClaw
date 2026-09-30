"""A turn sent again is the same message: her words stay hers, and its context goes with it.

When a turn comes back with no reply, or its runtime is lost, the gateway sends it again. It used to
queue the text the MODEL had been sent, with the context blocks already in front of it, and the
queue treated that like a new message: a second user row in the transcript and a second bubble,
which began "The user opened this chat to investigate the following entity …" with the entity's
fenced snapshot, before her own words. Measured on an "Investigate in chat" conversation.

A retry now re-sends her message as the same turn: no new row, no new bubble, never merged with a
message queued behind it, the same checkpoint turn, and the context it was given the first time,
given again.

Driven through the real chat runner; only the runtimes are fakes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from test_chat_turn_outcome import _client, _frames, _session, _state, _streaming, _turn

from personalclaw.acp.errors import AcpProcessDied
from personalclaw.dashboard.chat_utils import _dequeue_next_message
from personalclaw.dashboard.state import _ChatSession
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

HER_WORDS = "Draft a reply to this email with my final abstract, 120 words at most."
SNAPSHOT = "Subject: Your talk is accepted\n\nPlease send your abstract and a photo by Friday."
REPLY = "Here is a draft that includes your abstract."


def _investigate(session: _ChatSession) -> None:
    """What `POST /api/investigate` stages, and the row her own message is when she sends it."""
    session._investigate_ctx = {
        "kind": "inbox_item",
        "title": "Inbox: Talks team",
        "back_link": "#/inbox",
        "snapshot": SNAPSHOT,
    }
    session.append("user", HER_WORDS, "msg msg-u")


def _blank_then_reply():
    """A runtime whose first answer is empty and whose second is the reply."""
    calls = {"n": 0}

    async def stream():
        calls["n"] += 1
        if calls["n"] > 1:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=REPLY)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    return stream


async def _reply_stream():
    yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=REPLY)
    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _user_rows(session: _ChatSession) -> list[str]:
    return [m["content"] for m in session.messages if m.get("role") == "user"]


def _sent(state) -> list[str]:
    """The message each attempt handed the context builder: what the model is given."""
    return [c.args[0] for c in state.context_builder.build_message.call_args_list]


def _assert_the_same_message_went_again(state, session) -> None:
    assert _user_rows(session) == [HER_WORDS], session.messages
    assert _frames(state, "chat_user_message") == []
    first, again = _sent(state)
    for sent in (first, again):
        assert "<untrusted_content source=investigate:inbox_item>" in sent
        assert sent.count(SNAPSHOT.splitlines()[0]) == 1
        assert sent.count(HER_WORDS) == 1
    assert [m["content"] for m in session.messages if m.get("role") == "assistant"] == [REPLY]


@pytest.mark.asyncio
async def test_a_turn_sent_again_after_an_empty_answer_is_her_message_once(tmp_path):
    state = _streaming(tmp_path, _blank_then_reply())
    session = _session(state)
    _investigate(session)

    await _turn(state, session, HER_WORDS)

    _assert_the_same_message_went_again(state, session)


@pytest.mark.asyncio
async def test_a_turn_sent_again_after_its_runtime_was_lost_is_her_message_once(tmp_path):
    acquire = AsyncMock(
        side_effect=[AcpProcessDied("agent exited"), (_client(_reply_stream), True, False)]
    )
    state = _state(tmp_path, acquire)
    session = _session(state)
    _investigate(session)
    # The runtime is lost before the context builder runs, so the first attempt hands it nothing.
    state.context_builder.build_message.side_effect = lambda message, *a, **kw: (message, None)

    await _turn(state, session, HER_WORDS)

    assert _user_rows(session) == [HER_WORDS], session.messages
    assert _frames(state, "chat_user_message") == []
    (again,) = _sent(state)
    assert "<untrusted_content source=investigate:inbox_item>" in again
    assert again.count(SNAPSHOT.splitlines()[0]) == 1
    assert again.count(HER_WORDS) == 1
    assert [m["content"] for m in session.messages if m.get("role") == "assistant"] == [REPLY]


@pytest.mark.asyncio
async def test_a_turn_sent_again_is_the_same_checkpoint_turn(tmp_path, monkeypatch):
    from personalclaw import turn_checkpoints

    opened: list[str] = []
    monkeypatch.setattr(
        turn_checkpoints, "begin_turn", lambda key, **kw: opened.append(key) or len(opened)
    )
    state = _streaming(tmp_path, _blank_then_reply())
    session = _session(state)
    _investigate(session)

    await _turn(state, session, HER_WORDS)

    assert len(opened) == 1, "the retry opened a second checkpoint turn for one message"


def test_a_retry_is_never_merged_with_a_message_queued_behind_it():
    session = _ChatSession("s1")
    session.queue_retry(HER_WORDS)
    session.queue_append("And sign it from both of us.")

    sent, consumed = _dequeue_next_message(session, merge_enabled=True)

    assert (sent, [item.get("retry") for item in consumed]) == (HER_WORDS, ["here"])
    assert [q["content"] for q in session._queue] == ["And sign it from both of us."]
