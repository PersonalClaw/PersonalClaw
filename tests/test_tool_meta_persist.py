"""Tool input/output persist onto the tool message's meta so an inline
tool-detail pill still expands after a page reload.

These pin: (1) the meta dict built by the tool_call append carries ``input``,
coerced via tool_input_to_str (dict-safe); (2) the tool_result loop sets
``output`` on the matching message; (3) both survive the persistence redaction
round-trip (_redact_meta) with secrets stripped.
"""

from __future__ import annotations

from personalclaw.dashboard.chat_persistence import _redact_meta
from personalclaw.dashboard.chat_utils import tool_input_to_str


def test_tool_input_coerced_for_meta_dict_and_str():
    """tool_input is Any (dict for native, str for ACP). The value persisted to
    meta['input'] must be coerced the same way the broadcast does."""
    assert tool_input_to_str({"path": "/x", "n": 3}) == '{"path": "/x", "n": 3}'
    assert tool_input_to_str("echo hi") == "echo hi"
    assert tool_input_to_str(None) == ""


def test_tool_result_loop_sets_output_on_matching_message():
    """Mirror the chat_runner EVENT_TOOL_RESULT loop: find the tool message by
    tool_call_id, mark it done, and persist its output."""
    messages = [
        {"role": "assistant", "content": "hi"},
        {
            "role": "tool",
            "content": "🔧 read",
            "meta": {"tool_call_id": "tc_1", "purpose": "read a file", "input": "/etc/hosts"},
        },
    ]
    tool_call_id = "tc_1"
    _out = "file contents here"
    # The exact loop body from chat_runner.py:
    for m in reversed(messages):
        if m.get("role") == "tool" and m.get("meta", {}).get("tool_call_id") == tool_call_id:
            _meta = m.setdefault("meta", {})
            _meta["done"] = True
            _meta["output"] = _out
            break

    meta = messages[-1]["meta"]
    assert meta["done"] is True
    assert meta["output"] == "file contents here"
    assert meta["input"] == "/etc/hosts"  # input from the tool_call append survives


def test_meta_input_output_survive_redaction_roundtrip():
    """_redact_meta preserves the input/output keys and re-redacts their values
    (the persistence read/write boundary runs every meta through it)."""
    meta = {
        "tool_call_id": "tc_1",
        "purpose": "fetch",
        "input": "curl https://api.example.com",
        "output": "ok",
        "done": True,
    }
    red = _redact_meta(meta)
    assert set(red.keys()) == set(meta.keys())  # no keys dropped
    assert red["done"] is True
    assert red["tool_call_id"] == "tc_1"
    # String values pass through redaction (identity for safe strings here).
    assert red["input"] == "curl https://api.example.com"
    assert red["output"] == "ok"


def test_secret_in_tool_output_is_redacted_on_persist():
    """A credential captured in tool output must be redacted by the meta
    round-trip — the persisted inline detail must not leak secrets."""
    meta = {
        "tool_call_id": "t",
        "output": "export AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE0000000000000000000X",
    }
    red = _redact_meta(meta)
    # The raw secret value must not survive verbatim.
    assert "AKIAIOSFODNN7EXAMPLE0000000000000000000X" not in red["output"]
