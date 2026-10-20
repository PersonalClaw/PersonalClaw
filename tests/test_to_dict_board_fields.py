"""Tests for to_dict() Board fields: options, last_activity_ts, and the message count."""

import asyncio

from personalclaw.dashboard.chat_utils import _prepare_messages
from personalclaw.dashboard.state import _ChatSession


def _session(*messages: dict) -> _ChatSession:
    s = _ChatSession("test-session")
    for m in messages:
        s.messages.append(m)
    return s


def test_options_from_assistant():
    s = _session({"role": "assistant", "content": "Pick one.\n[OPTIONS: A | B | C]", "ts": "t1"})
    d = s.to_dict()
    assert d["has_options"] is True
    assert d["options"] == ["A", "B", "C"]
    assert "[OPTIONS:" not in d["prompt_preview"]


def test_no_options_when_user_last():
    s = _session(
        {"role": "assistant", "content": "Here you go.\n[OPTIONS: X | Y]", "ts": "t1"},
        {"role": "user", "content": "X", "ts": "t2"},
    )
    d = s.to_dict()
    assert d["has_options"] is False
    assert d["options"] == []


def test_last_activity_ts_from_tool_call():
    s = _session(
        {"role": "assistant", "content": "Let me check.", "ts": "t1"},
        {"role": "tool_call", "content": "grep ...", "ts": "t2"},
        {"role": "tool_result", "content": "found", "ts": "t3"},
        {"role": "assistant", "content": "Here's what I found.", "ts": "t4"},
    )
    d = s.to_dict()
    assert d["last_activity_ts"] == "t4"


def test_prompt_preview_truncation():
    long_text = "x" * 300 + "\n[OPTIONS: A | B]"
    s = _session({"role": "assistant", "content": long_text, "ts": "t1"})
    d = s.to_dict()
    assert len(d["prompt_preview"]) == 241  # 240 + "…"
    assert d["prompt_preview"].endswith("…")


def _turn(s: _ChatSession, question: str, answer: str) -> None:
    """One real turn through the session API the runner drives."""
    s.append("user", question)
    for i in range(0, len(answer), 2):
        s.stream_chunk(answer[i : i + 2])
    s.finish_stream(answer)
    s.signal_done()


def test_message_count_ignores_the_end_of_turn_signal():
    # #2862: a completed 2-turn chat is 4 messages, not 6. The end-of-turn marker goes to
    # live readers only (`signal_done`), never into the transcript the list counts.
    s = _ChatSession("test-session")
    _turn(s, "hi", "hello")
    _turn(s, "again", "sure")
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user", "assistant"]
    assert s.to_dict()["messages"] == 4


def test_message_count_single_turn():
    s = _ChatSession("test-session")
    _turn(s, "hi", "hello")
    assert s.to_dict()["messages"] == 2


def test_message_count_agrees_with_detail_view():
    # The list count and the detail endpoint must never diverge on the same session.
    # Mid-stream (running), however many chunks the answer arrived in, both count it once.
    s = _ChatSession("test-session")
    _turn(s, "hi", "hello")
    s.append("user", "more")
    for chunk in ("su", "re", " thing"):
        s.stream_chunk(chunk)
    loop = asyncio.new_event_loop()
    s.task = loop.create_future()  # running: the open answer is served as a streaming msg
    d = s.to_dict()
    assert d["messages"] == len(_prepare_messages(s.messages, s.running)) == 4
    loop.close()
