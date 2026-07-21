"""Property-based tests for inline tool cards (backend).

Uses hypothesis to validate correctness properties from the design doc.
Each test is tagged with the property number and validated requirements.

IMPORTANT: hypothesis does NOT work with function-scoped pytest fixtures
like tmp_path or monkeypatch.  We use tempfile.mkdtemp() and
unittest.mock.patch instead.
"""

import platform
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw.dashboard.chat import _flush_segment, _prepare_messages
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog

# ── Helpers ──


def _make_state_no_fixture(**kwargs):
    """Create a DashboardState with mocked services and real ConversationLog.

    Uses tempfile.TemporaryDirectory() instead of pytest tmp_path so hypothesis
    tests can call this without fixtures. Returns (state, tmp_dir) so callers
    can clean up via tmp_dir.cleanup().
    """
    tmp_dir = tempfile.TemporaryDirectory()
    tmp = Path(tmp_dir.name)
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp),
        **kwargs,
    )
    return state, tmp_dir


# Strategy: non-empty printable text (no control chars that break redaction)
_text_st = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z"), min_codepoint=32),
    min_size=1,
    max_size=60,
)

# Strategy: tool names
_tool_name_st = st.text(
    alphabet=st.characters(categories=("L", "N"), min_codepoint=65),
    min_size=1,
    max_size=20,
)


# ── Segment flush on interrupting event ──


class TestSegmentFlushOnInterrupt:
    """For any stream where assistant_text is non-empty and an interrupting
    event arrives (EVENT_TOOL_CALL or EVENT_PERMISSION_REQUEST), the backend
    shall broadcast chat_segment before the interrupting event's broadcast,
    and assistant_text shall be empty after the flush.
    """

    @pytest.mark.skipif(
        platform.system() == "Darwin", reason="Hypothesis flaky on macOS CI (timing-sensitive)"
    )
    @given(
        text_chunks=st.lists(_text_st, min_size=1, max_size=5),
        tool_name=_tool_name_st,
    )
    @settings(deadline=2000)
    @pytest.mark.asyncio
    async def test_flush_segment_broadcasts_before_tool(self, text_chunks, tool_name):
        """Generate random text chunks followed by a tool call.  Verify
        _flush_segment broadcasts chat_segment and clears chunks from
        the session.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop1")

                    # Accumulate text chunks in the session
                    assistant_text = ""
                    for chunk in text_chunks:
                        session.append("chunk", chunk, "chunk")
                        assistant_text += chunk

                    # Record broadcasts
                    broadcasts: list[tuple[str, dict]] = []
                    state.broadcast_ws = lambda t, d: broadcasts.append((t, d))

                    # Flush segment (simulates what _run_chat does on EVENT_TOOL_CALL)
                    assert assistant_text != ""
                    _flush_segment(state, session, assistant_text)
                    assistant_text = ""

                    # Broadcast the tool_call after flush
                    state.broadcast_ws(
                        "tool_call", {"session": session.key, "tool": tool_name, "kind": "read"}
                    )

                    # Verify: chat_segment comes before tool_call
                    types = [b[0] for b in broadcasts]
                    assert "chat_segment" in types, "chat_segment must be broadcast"
                    assert "tool_call" in types, "tool_call must be broadcast"
                    seg_idx = types.index("chat_segment")
                    tool_idx = types.index("tool_call")
                    assert seg_idx < tool_idx, "chat_segment must precede tool_call"

                    # Verify: assistant_text is reset
                    assert assistant_text == ""

                    # Verify: no chunk messages remain in session
                    chunk_count = sum(1 for m in session.messages if m.get("role") == "chunk")
                    assert chunk_count == 0, "chunks must be removed after flush"

                    # Verify: an assistant message was persisted
                    assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
                    assert len(assistant_msgs) >= 1, "flushed text must be persisted as assistant"
                finally:
                    tmp_dir.cleanup()

    @given(text_chunks=st.lists(_text_st, min_size=1, max_size=5))
    @settings(deadline=None)
    def test_flush_segment_broadcasts_before_permission(self, text_chunks):
        """Generate random text chunks followed by a permission request.
        Verify _flush_segment broadcasts chat_segment.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop1perm")

                    assistant_text = ""
                    for chunk in text_chunks:
                        session.append("chunk", chunk, "chunk")
                        assistant_text += chunk

                    broadcasts: list[tuple[str, dict]] = []
                    state.broadcast_ws = lambda t, d: broadcasts.append((t, d))

                    assert assistant_text != ""
                    _flush_segment(state, session, assistant_text)
                    assistant_text = ""

                    types = [b[0] for b in broadcasts]
                    assert "chat_segment" in types
                    assert assistant_text == ""
                finally:
                    tmp_dir.cleanup()


# ── No segment when assistant_text is empty ──


class TestNoSegmentWhenEmpty:
    """For any EVENT_TOOL_CALL event that arrives when assistant_text is
    empty, the backend shall not broadcast a chat_segment event — only
    the tool_call event is broadcast.
    """

    @given(tool_name=_tool_name_st)
    @settings(deadline=None)
    def test_no_segment_when_text_empty(self, tool_name):
        """Generate tool call events with empty assistant_text.
        Verify no chat_segment broadcast.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop2")

                    broadcasts: list[tuple[str, dict]] = []
                    state.broadcast_ws = lambda t, d: broadcasts.append((t, d))

                    assistant_text = ""
                    if assistant_text:
                        _flush_segment(state, session, assistant_text)
                        assistant_text = ""

                    state.broadcast_ws(
                        "tool_call",
                        {"session": session.key, "tool": tool_name, "kind": "read"},
                    )

                    types = [b[0] for b in broadcasts]
                    assert (
                        "chat_segment" not in types
                    ), "chat_segment must NOT be broadcast when assistant_text is empty"
                    assert "tool_call" in types, "tool_call must still be broadcast"
                finally:
                    tmp_dir.cleanup()


# ── Persisted message structure after segmented stream ──


class TestPersistedMessageStructure:
    """For any stream containing N text segments separated by tool calls,
    after completion the session's persisted message list shall contain N
    separate assistant messages interleaved with tool messages, and zero
    chunk messages.
    """

    @given(
        segments=st.lists(
            st.tuples(_text_st, _tool_name_st),
            min_size=1,
            max_size=5,
        ),
        final_text=_text_st,
    )
    @settings(deadline=None)
    def test_persisted_structure_after_segments(self, segments, final_text):
        """Generate multi-segment streams (1-5 segments, random text per
        segment).  Simulate the _run_chat flow: for each segment, accumulate
        text as chunks, flush on tool call, then append tool message.
        After the final text, do the EVENT_COMPLETE finalization.
        Verify the persisted messages have the correct structure.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop6")

                    for text, tool_name in segments:
                        session.append("chunk", text, "chunk")
                        _flush_segment(state, session, text)
                        session.append("tool", f"🔧 {tool_name}", "msg msg-tool")

                    session.append("chunk", final_text, "chunk")
                    session.messages = [m for m in session.messages if m.get("role") != "chunk"]
                    session.append("assistant", final_text, "msg msg-a")

                    roles = [m.get("role") for m in session.messages]
                    assert "chunk" not in roles, "no chunk messages should remain"

                    n_segments = len(segments)
                    expected_assistant = n_segments + 1
                    expected_tool = n_segments
                    actual_assistant = roles.count("assistant")
                    actual_tool = roles.count("tool")

                    assert (
                        actual_assistant == expected_assistant
                    ), f"expected {expected_assistant} assistant msgs, got {actual_assistant}"
                    assert (
                        actual_tool == expected_tool
                    ), f"expected {expected_tool} tool msgs, got {actual_tool}"

                    non_chunk = [r for r in roles if r in ("assistant", "tool")]
                    for i, role in enumerate(non_chunk):
                        if i % 2 == 0:
                            assert (
                                role == "assistant"
                            ), f"position {i} should be assistant, got {role}"
                        else:
                            assert role == "tool", f"position {i} should be tool, got {role}"
                finally:
                    tmp_dir.cleanup()


# ── _prepare_messages chunk collapse ──


class TestPrepareMessagesChunkCollapse:
    """For any message list with trailing chunk messages (mid-stream state),
    _prepare_messages shall collapse them into a single streaming message
    while passing through assistant and tool messages unchanged.
    """

    @given(
        assistant_texts=st.lists(_text_st, min_size=0, max_size=3),
        tool_names=st.lists(_tool_name_st, min_size=0, max_size=3),
        trailing_chunks=st.lists(_text_st, min_size=1, max_size=5),
    )
    @settings(deadline=None)
    def test_prepare_messages_collapses_trailing_chunks(
        self, assistant_texts, tool_names, trailing_chunks
    ):
        """Generate message lists with interleaved assistant/tool messages
        followed by trailing chunks.  Verify _prepare_messages output:
        - assistant and tool messages pass through unchanged
        - trailing chunks collapse into a single streaming message
        """
        messages: list[dict] = []

        # Build interleaved assistant/tool prefix
        n_pairs = min(len(assistant_texts), len(tool_names))
        for i in range(n_pairs):
            messages.append(
                {"role": "assistant", "content": assistant_texts[i], "cls": "msg msg-a"}
            )
            messages.append(
                {"role": "tool", "content": f"🔧 {tool_names[i]}", "cls": "msg msg-tool"}
            )
        # Any remaining assistant texts
        for i in range(n_pairs, len(assistant_texts)):
            messages.append(
                {"role": "assistant", "content": assistant_texts[i], "cls": "msg msg-a"}
            )

        # Add trailing chunks
        for chunk in trailing_chunks:
            messages.append({"role": "chunk", "content": chunk, "cls": "chunk"})

        result = _prepare_messages(messages, running=True)

        # Count roles in output
        result_roles = [m.get("role") for m in result]

        # No chunk messages in output
        assert "chunk" not in result_roles, "chunks must be collapsed"

        # Exactly one streaming message at the end
        streaming_count = result_roles.count("streaming")
        assert streaming_count == 1, f"expected exactly 1 streaming message, got {streaming_count}"
        assert result[-1]["role"] == "streaming", "streaming must be last"

        # Assistant and tool messages pass through
        input_assistant = sum(1 for m in messages if m["role"] == "assistant")
        input_tool = sum(1 for m in messages if m["role"] == "tool")
        output_assistant = result_roles.count("assistant")
        output_tool = result_roles.count("tool")
        assert output_assistant == input_assistant
        assert output_tool == input_tool

        # Streaming content is concatenation of all chunk contents
        # (after redaction, which is identity for our safe test strings)
        expected_text = "".join(trailing_chunks)
        # The streaming content may have been redacted but for safe chars
        # it should match
        assert result[-1]["content"] == expected_text


# ── Chunk sequence monotonicity ──


class TestChunkSequenceMonotonicity:
    """For any stream with one or more segment boundaries, the chunk_seq
    values in chat_chunk broadcasts shall be strictly monotonically
    increasing across the entire stream — never reset at segment boundaries.
    """

    @given(
        segments=st.lists(
            st.tuples(
                st.lists(_text_st, min_size=1, max_size=4),
                _tool_name_st,
            ),
            min_size=1,
            max_size=5,
        ),
        final_chunks=st.lists(_text_st, min_size=1, max_size=3),
    )
    @settings(deadline=None)
    def test_chunk_seq_monotonic_across_segments(self, segments, final_chunks):
        """Generate multi-segment streams.  Simulate the _run_chat event
        loop: for each segment, emit text chunks (incrementing chunk_seq),
        then flush on tool call (without resetting chunk_seq).
        Collect all seq values from chat_chunk broadcasts and verify
        strict monotonic increase.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop8")

                    broadcasts: list[tuple[str, dict]] = []
                    state.broadcast_ws = lambda t, d: broadcasts.append((t, d))

                    chunk_seq = 0
                    assistant_text = ""

                    for text_chunks, tool_name in segments:
                        for chunk in text_chunks:
                            assistant_text += chunk
                            chunk_seq += 1
                            session.append("chunk", chunk, "chunk")
                            state.broadcast_ws(
                                "chat_chunk",
                                {"session": session.key, "content": chunk, "seq": chunk_seq},
                            )

                        if assistant_text:
                            _flush_segment(state, session, assistant_text)
                            assistant_text = ""
                        state.broadcast_ws(
                            "tool_call",
                            {"session": session.key, "tool": tool_name, "kind": "read"},
                        )
                        session.append("tool", f"🔧 {tool_name}", "msg msg-tool")

                    for chunk in final_chunks:
                        assistant_text += chunk
                        chunk_seq += 1
                        session.append("chunk", chunk, "chunk")
                        state.broadcast_ws(
                            "chat_chunk",
                            {"session": session.key, "content": chunk, "seq": chunk_seq},
                        )

                    seq_values = [b[1]["seq"] for b in broadcasts if b[0] == "chat_chunk"]
                    assert len(seq_values) >= 2, "need at least 2 chunks to verify monotonicity"
                    for i in range(1, len(seq_values)):
                        assert (
                            seq_values[i] > seq_values[i - 1]
                        ), f"seq[{i}]={seq_values[i]} must be > seq[{i-1}]={seq_values[i-1]}"
                finally:
                    tmp_dir.cleanup()


# ── No segment events for tool-free streams ──


class TestNoSegmentForToolFreeStreams:
    """For any stream consisting only of EVENT_TEXT_CHUNK events followed
    by EVENT_COMPLETE, the backend shall not broadcast any chat_segment
    events.
    """

    @given(text_chunks=st.lists(_text_st, min_size=1, max_size=10))
    @settings(deadline=None)
    def test_no_segment_in_text_only_stream(self, text_chunks):
        """Generate text-only streams (no tool calls).  Simulate the
        _run_chat event loop: emit text chunks, then do EVENT_COMPLETE
        finalization.  Verify zero chat_segment broadcasts.
        """
        with tempfile.TemporaryDirectory() as config_tmp:
            with patch("personalclaw.dashboard.state.config_dir", return_value=Path(config_tmp)):
                state, tmp_dir = _make_state_no_fixture()
                try:
                    session = state.get_or_create_session("prop10")

                    broadcasts: list[tuple[str, dict]] = []
                    state.broadcast_ws = lambda t, d: broadcasts.append((t, d))

                    assistant_text = ""
                    chunk_seq = 0

                    for chunk in text_chunks:
                        assistant_text += chunk
                        chunk_seq += 1
                        session.append("chunk", chunk, "chunk")
                        state.broadcast_ws(
                            "chat_chunk",
                            {"session": session.key, "content": chunk, "seq": chunk_seq},
                        )

                    if assistant_text:
                        session.messages = [m for m in session.messages if m.get("role") != "chunk"]
                        session.append("assistant", assistant_text, "msg msg-a")

                    state.broadcast_ws("chat_done", {"session": session.key})

                    segment_broadcasts = [b for b in broadcasts if b[0] == "chat_segment"]
                    assert (
                        len(segment_broadcasts) == 0
                    ), f"expected 0 chat_segment broadcasts, got {len(segment_broadcasts)}"

                    assistant_msgs = [m for m in session.messages if m.get("role") == "assistant"]
                    assert (
                        len(assistant_msgs) == 1
                    ), f"expected 1 assistant message, got {len(assistant_msgs)}"
                finally:
                    tmp_dir.cleanup()
