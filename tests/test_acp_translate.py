"""Direct tests for the pure ACP↔neutral decoders in acp/translate.py.

These functions (text/tool/permission/command-result/JSONL decoding) are the single
translation surface shared by the N=1 AcpClient wrapper and the concurrent AcpSession.
Pre-P9#7 they were exercised only THROUGH the client's inline loop; this file tests them
directly so the coverage survives the client's slimming to a thin wrapper.
"""

from __future__ import annotations

import json

from personalclaw.acp import translate
from personalclaw.acp.types import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    JsonRpcMessage,
)


def _update(update: dict) -> JsonRpcMessage:
    return JsonRpcMessage(method="session/update", params={"update": update})


# ── extract_text_chunk ─────────────────────────────────────────────────────────
class TestExtractTextChunk:
    def test_plain_text(self):
        msg = _update(
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}
        )
        text, is_thinking = translate.extract_text_chunk(msg)
        assert text == "hi" and is_thinking is False

    def test_thinking_flagged(self):
        msg = _update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "thinking", "text": "pondering"},
            }
        )
        text, is_thinking = translate.extract_text_chunk(msg)
        assert text == "pondering" and is_thinking is True

    def test_non_text_returns_none(self):
        msg = _update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "x"})
        text, _ = translate.extract_text_chunk(msg)
        assert text is None


# ── extract_tool_event ─────────────────────────────────────────────────────────
class TestExtractToolEvent:
    def test_tool_call_event(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "title": "Read file",
                "kind": "read",
                "rawInput": {"path": "/x"},
            }
        )
        ev = translate.extract_tool_event(msg, {}, [])
        assert ev is not None and ev.kind == EVENT_TOOL_CALL
        assert ev.tool_call_id == "t1" and ev.title == "Read file"

    def test_non_tool_returns_none(self):
        msg = _update(
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}
        )
        assert translate.extract_tool_event(msg, {}, []) is None


# ── extract_tool_update_events (completed + failed both surface results) ─────────
class TestExtractToolUpdateEvents:
    def test_completed_yields_result(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "t1",
                "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": "done"}}],
            }
        )
        events = translate.extract_tool_update_events(msg, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert len(results) == 1 and "done" in results[0].tool_output

    def test_failed_still_surfaces_result(self):
        msg = _update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "t1",
                "status": "failed",
                "content": [
                    {
                        "type": "content",
                        "content": {"type": "text", "text": "ls: /nope: No such file"},
                    }
                ],
            }
        )
        events = translate.extract_tool_update_events(msg, {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert len(results) == 1 and "No such file" in results[0].tool_output


# ── build_permission_event ──────────────────────────────────────────────────────
class TestBuildPermissionEvent:
    def test_permission_event_fields(self):
        from personalclaw.acp.dialect import DefaultDialect

        msg = JsonRpcMessage(
            id=55,
            method="session/request_permission",
            params={"toolCall": {"title": "Write", "toolCallId": "t9"}, "options": []},
        )
        offered: dict = {}
        ev = translate.build_permission_event(msg, DefaultDialect(), {}, offered)
        assert ev.kind == EVENT_PERMISSION_REQUEST
        assert ev.title == "Write" and ev.request_id == 55


# ── format_command_result ────────────────────────────────────────────────────────
class TestFormatCommandResult:
    def test_message_and_data(self):
        out = translate.format_command_result({"message": "usage report", "data": {"tokens": 42}})
        assert "usage report" in out and '"tokens": 42' in out


# ── is_tool_interrupted_marker ────────────────────────────────────────────────────
class TestInterruptedMarker:
    def test_exact_marker_matches(self):
        assert translate.is_tool_interrupted_marker(translate.TOOL_INTERRUPTED_MARKER) is True

    def test_prose_quoting_marker_does_not_match(self):
        assert (
            translate.is_tool_interrupted_marker("I saw: " + translate.TOOL_INTERRUPTED_MARKER)
            is False
        )


# ── read_new_tool_results (per-session JSONL tail) ───────────────────────────────
class TestReadNewToolResults:
    def test_reads_tool_results_and_advances_pos(self, tmp_path):
        jsonl = tmp_path / "sess.jsonl"
        jsonl.write_text(
            json.dumps(
                {
                    "kind": "ToolResults",
                    "data": {
                        "content": [
                            {
                                "kind": "toolResult",
                                "data": {
                                    "toolUseId": "j1",
                                    "content": [{"kind": "text", "data": "jsonl output"}],
                                },
                            }
                        ]
                    },
                }
            )
            + "\n"
        )
        events, pos = translate.read_new_tool_results(jsonl, 0)
        assert len(events) == 1 and events[0].tool_call_id == "j1"
        assert "jsonl output" in events[0].tool_output
        assert pos > 0
        # A second read from the advanced position yields nothing new.
        events2, pos2 = translate.read_new_tool_results(jsonl, pos)
        assert events2 == [] and pos2 == pos

    def test_missing_file_is_noop(self, tmp_path):
        events, pos = translate.read_new_tool_results(tmp_path / "nope.jsonl", 0)
        assert events == [] and pos == 0


# ── coerce_tool_content ───────────────────────────────────────────────────────────
class TestCoerceToolContent:
    def test_flattens_text_blocks(self):
        out = translate.coerce_tool_content(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        assert "a" in out and "b" in out

    def test_plain_string_passthrough(self):
        assert translate.coerce_tool_content("plain") == "plain"


# ── encode_prompt_content ─────────────────────────────────────────────────────────
class TestEncodePromptContent:
    def test_wraps_message_as_content_blocks(self):
        blocks = translate.encode_prompt_content("hello")
        assert isinstance(blocks, list) and blocks
        # Each block is a content dict with a text field somewhere.
        assert any("hello" in json.dumps(b) for b in blocks)
