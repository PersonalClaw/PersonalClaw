"""Unit tests for chat_utils.py — redaction, model normalization, queue ops."""

import json

import pytest

from personalclaw.dashboard.chat_utils import (
    _extract_bash_command,
    _history_key_for,
    _normalize_model,
    _prepare_messages,
    _redact_deep,
    _redact_for_display,
    _remove_queued_by_id,
    _validate_tool_name,
    is_deprecated_model,
    resolve_history_key,
    tool_input_to_str,
)


class TestToolInputToStr:
    """tool_input is Any (ACP→str, native loop→dict); display code slices it."""

    def test_str_passthrough(self):
        assert tool_input_to_str('{"a": 1}') == '{"a": 1}'

    def test_none_is_empty(self):
        assert tool_input_to_str(None) == ""

    def test_dict_is_json(self):
        assert tool_input_to_str({"x": "y"}) == '{"x": "y"}'

    def test_list_is_json(self):
        assert tool_input_to_str(["a", "b"]) == '["a", "b"]'

    def test_other_is_str(self):
        assert tool_input_to_str(42) == "42"

    def test_result_is_sliceable(self):
        # The bug: dict[:4000] → KeyError(slice). Coerced output must slice.
        for v in (None, "hi", {"k": "v"}, ["a"], 7):
            assert isinstance(tool_input_to_str(v)[:4000], str)

    def test_unserializable_dict_falls_back_to_str(self):
        class _NoJSON:
            pass

        out = tool_input_to_str({"obj": _NoJSON()})
        assert isinstance(out, str) and out  # default=str makes it serialize


class TestRedactDeep:
    def test_string(self):
        # AKIAIOSFODNN7EXAMPLE is a well-known test AWS key
        result = _redact_deep("key AKIAIOSFODNN7EXAMPLE here")
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_dict(self):
        result = _redact_deep({"a": "AKIAIOSFODNN7EXAMPLE"})
        assert "AKIAIOSFODNN7EXAMPLE" not in result["a"]

    def test_list(self):
        result = _redact_deep(["AKIAIOSFODNN7EXAMPLE"])
        assert "AKIAIOSFODNN7EXAMPLE" not in result[0]

    def test_nested(self):
        result = _redact_deep({"a": [{"b": "AKIAIOSFODNN7EXAMPLE"}]})
        assert "AKIAIOSFODNN7EXAMPLE" not in result["a"][0]["b"]

    def test_non_string_passthrough(self):
        assert _redact_deep(42) == 42
        assert _redact_deep(None) is None


class TestExtractBashCommand:
    def test_json_input(self):
        assert _extract_bash_command('{"command": "ls -la"}') == "ls -la"

    def test_raw_input(self):
        assert _extract_bash_command("ls -la") == "ls -la"

    def test_empty_json(self):
        assert _extract_bash_command("{}") == ""

    def test_invalid_json(self):
        assert _extract_bash_command("not json {") == "not json {"

    def test_native_dict_bash_command(self):
        # Native loop passes the parsed dict, not a JSON string.
        assert _extract_bash_command({"command": "ls -la"}) == "ls -la"

    def test_native_dict_non_bash_returns_empty_string(self):
        """Regression: a native write_file tool's args dict (no `command` key)
        must coerce to "" — returning the dict made downstream regex guards
        (is_read_only_bash) raise "expected string or bytes-like object, got
        'dict'" on every native tool that hits the approval path."""
        from personalclaw.dashboard.state import is_read_only_bash

        cmd = _extract_bash_command({"path": "poem.txt", "content": "roses"})
        assert cmd == ""
        # The downstream guard must not raise on the coerced value.
        assert is_read_only_bash(cmd) is False

    def test_non_string_non_dict_returns_empty(self):
        assert _extract_bash_command(None) == ""
        assert _extract_bash_command(42) == ""
        # A dict whose `command` is itself non-string coerces to "".
        assert _extract_bash_command({"command": {"nested": 1}}) == ""


class TestNormalizeModel:
    def test_deprecated_mapped(self):
        assert _normalize_model("claude-opus-4.6-1m") == "claude-opus-4.6"
        assert _normalize_model("claude-sonnet-4.6-1m") == "claude-sonnet-4.6"

    def test_non_deprecated_passthrough(self):
        assert _normalize_model("claude-sonnet-4.6") == "claude-sonnet-4.6"
        assert _normalize_model("custom-model") == "custom-model"

    def test_is_deprecated(self):
        assert is_deprecated_model("claude-opus-4.6-1m") is True
        assert is_deprecated_model("claude-sonnet-4.6") is False


class TestValidateToolName:
    def test_valid_name(self):
        assert _validate_tool_name("execute_bash") == "execute_bash"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_tool_name("")

    def test_execute_kind_skips_length(self):
        long_name = "x" * 500
        result = _validate_tool_name(long_name, tool_kind="execute")
        assert len(result) == 500


class TestHistoryKeyFor:
    def test_raw_key(self):
        assert _history_key_for("chat-1-123") == "dashboard:chat-1-123"

    def test_already_prefixed(self):
        assert _history_key_for("dashboard:chat-1-123") == "dashboard:chat-1-123"

    def test_filesystem_roundtrip(self):
        assert _history_key_for("dashboard_chat-1-123") == "dashboard:chat-1-123"

    def test_stacked_prefixes(self):
        assert _history_key_for("dashboard_dashboard_chat-1") == "dashboard:chat-1"

    def test_bare_id_namespaced_generically(self):
        # _history_key_for is for DASHBOARD ids only — it namespaces any bare id.
        # Channel-thread resolution is provider-agnostic + runtime (resolve_history_key).
        assert _history_key_for("1783737058.246229") == "dashboard:1783737058.246229"


class TestResolveHistoryKey:
    """Provider-agnostic: core trusts the PERSISTED key, not a key shape. A channel
    thread (any provider) persists under its own bare key; a dashboard session under
    dashboard:. resolve_history_key asks the log which one has metadata — no Slack (or
    any provider) specific pattern in core."""

    class _FakeLog:
        def __init__(self, keys):
            self._keys = set(keys)

        def get_metadata(self, key):
            return {"created_at": "x"} if key in self._keys else {}

    def test_channel_thread_key_resolves_bare(self):
        # Persisted under the bare provider key (e.g. a Slack/Discord/… thread).
        log = self._FakeLog({"1783737058.246229"})
        assert resolve_history_key(log, "1783737058.246229") == "1783737058.246229"

    def test_dashboard_session_resolves_namespaced(self):
        log = self._FakeLog({"dashboard:chat-1-x"})
        assert resolve_history_key(log, "chat-1-x") == "dashboard:chat-1-x"

    def test_missing_returns_none(self):
        log = self._FakeLog(set())
        assert resolve_history_key(log, "nope") is None

    def test_none_log_returns_none(self):
        assert resolve_history_key(None, "x") is None


class TestRemoveQueuedById:
    def test_removes_matching(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "queued", "cls": json.dumps({"queue_id": "q1"})},
        ]
        assert _remove_queued_by_id(msgs, "q1") is True
        assert len(msgs) == 1

    def test_no_match(self):
        msgs = [{"role": "queued", "cls": json.dumps({"queue_id": "q1"})}]
        assert _remove_queued_by_id(msgs, "q2") is False
        assert len(msgs) == 1

    def test_non_queued_skipped(self):
        msgs = [{"role": "user", "cls": json.dumps({"queue_id": "q1"})}]
        assert _remove_queued_by_id(msgs, "q1") is False


class TestPrepareMessages:
    def test_strips_done(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "done", "content": ""}]
        result = _prepare_messages(msgs, running=False)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_collapses_chunks(self):
        msgs = [
            {"role": "chunk", "content": "hel"},
            {"role": "chunk", "content": "lo"},
            {"role": "user", "content": "next"},
        ]
        result = _prepare_messages(msgs, running=False)
        assert result[0]["role"] == "streaming"
        assert "hel" in result[0]["content"]
        assert result[1]["role"] == "user"

    def test_trailing_chunks(self):
        msgs = [{"role": "chunk", "content": "partial"}]
        result = _prepare_messages(msgs, running=True)
        assert len(result) == 1
        assert result[0]["role"] == "streaming"


class TestRedactForDisplay:
    def test_redacts_credentials(self):
        result = _redact_for_display("key AKIAIOSFODNN7EXAMPLE here")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
