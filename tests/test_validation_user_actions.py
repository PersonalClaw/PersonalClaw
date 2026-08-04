"""Simulate real user actions through MCP tool paths.

Exercises the exact call patterns the ACP agent sends when the LLM invokes MCP
tools, plus the dashboard API patterns. Each test represents a real user action
that input validation must accept.
"""

from unittest.mock import patch

# ── MCP Core: simulate ACP agent calling tools via JSON-RPC ──


class TestMcpCoreUserActions:
    """Simulate the exact JSON-RPC calls ACP agent sends to personalclaw-core."""

    def _simulate_tool_call(self, tool_name: str, arguments: dict) -> str:
        """Simulate what ACP agent does: JSON-RPC tools/call → the personalclaw-core
        MCP server's aggregating dispatch (routes each tool to its category module)."""
        from personalclaw.mcp_core import _aggregated_call_tool

        return _aggregated_call_tool(tool_name, arguments)

    # -- subagent_run: user says "search docs for X in parallel" --

    def test_spawn_fire_and_forget(self):
        with patch("personalclaw.mcp_subagents._post") as mock_post:
            mock_post.return_value = {"id": "abc12345"}
            result = self._simulate_tool_call(
                "subagent_run",
                {"task": "search the codebase for uses of SessionManager"},
            )
        assert "abc12345" in result
        assert "Spawned" in result

    def test_spawn_batch_tasks(self):
        with patch("personalclaw.mcp_subagents._post") as mock_post:
            mock_post.side_effect = [{"id": "a1"}, {"id": "b2"}]
            result = self._simulate_tool_call(
                "subagent_run",
                {"tasks": ["search for SessionManager", "count test files"]},
            )
        assert "2 subagent" in result
        assert "a1" in result
        assert "b2" in result

    def test_spawn_default_returns_immediately(self):
        """subagent_run always returns immediately — fire-and-forget."""
        with patch("personalclaw.mcp_subagents._post") as mock_post:
            mock_post.return_value = {"id": "ghi789"}
            result = self._simulate_tool_call("subagent_run", {"task": "quick check"})
        assert "Spawned" in result
        assert "completion event" in result.lower()

    # -- memory_remember: user says "remember to always use dark mode" --

    def test_learn_preference(self):
        with patch("personalclaw.mcp_memory._post") as mock_post:
            mock_post.return_value = {"status": "ok"}
            result = self._simulate_tool_call(
                "memory_remember",
                {
                    "rule": "Always use dark mode for code examples",
                    "category": "preference",
                },
            )
        assert "Saved lesson" in result
        mock_post.assert_called_once_with(
            "/api/lessons",
            {
                "rule": "Always use dark mode for code examples",
                "category": "preference",
                "scope": "global",
            },
        )

    def test_learn_with_negative(self):
        with patch("personalclaw.mcp_memory._post") as mock_post:
            mock_post.return_value = {"status": "ok"}
            result = self._simulate_tool_call(
                "memory_remember",
                {
                    "rule": "Use pytest for testing",
                    "category": "tool",
                    "negative": "Do not use unittest directly",
                },
            )
        assert "Saved lesson" in result

    def test_learn_category_defaults_to_knowledge(self):
        """LLM might omit category — should default to 'knowledge'."""
        with patch("personalclaw.mcp_memory._post") as mock_post:
            mock_post.return_value = {"status": "ok"}
            result = self._simulate_tool_call(
                "memory_remember",
                {
                    "rule": "The project uses Python 3.10",
                },
            )
        assert "Saved lesson" in result
        call_body = mock_post.call_args[0][1]
        assert call_body["category"] == "knowledge"

    # -- memory_list: user says "what have I taught you?" --

    def test_learn_list(self):
        with patch("personalclaw.mcp_memory._get") as mock_get:
            mock_get.return_value = {
                "lessons": [
                    {"rule": "use dark mode", "category": "preference"},
                    {"rule": "prefer pytest", "category": "tool"},
                ]
            }
            result = self._simulate_tool_call("memory_list", {})
        assert "dark mode" in result
        assert "pytest" in result

    def test_learn_list_empty(self):
        with patch("personalclaw.mcp_memory._get") as mock_get:
            mock_get.return_value = {"lessons": []}
            result = self._simulate_tool_call("memory_list", {})
        assert "No lessons" in result

    # -- memory_forget: user says "forget the dark mode rule" --

    def test_learn_remove(self):
        with patch("personalclaw.mcp_memory._delete") as mock_del:
            mock_del.return_value = {"removed": 1}
            result = self._simulate_tool_call(
                "memory_forget",
                {
                    "query": "dark mode",
                },
            )
        assert "Removed" in result

    # -- subagent_list: user says "what's running in the background?" --

    def test_spawn_list_empty(self):
        with patch("personalclaw.mcp_subagents._get") as mock_get:
            mock_get.return_value = {"agents": []}
            result = self._simulate_tool_call("subagent_list", {})
        assert "No subagents" in result

    # -- subagent_status: user says "get the full output from that subagent" --

    def test_spawn_status_returns_full_result(self):
        with patch("personalclaw.mcp_subagents._get") as mock_get:
            mock_get.return_value = {"result": "A" * 5000}
            result = self._simulate_tool_call("subagent_status", {"agent_id": "abc123"})
        assert len(result) == 5000
        mock_get.assert_called_with("/api/spawn/abc123")

    def test_spawn_status_not_found(self):
        with patch("personalclaw.mcp_subagents._get") as mock_get:
            mock_get.return_value = {"error": "not found"}
            result = self._simulate_tool_call("subagent_status", {"agent_id": "bad"})
        assert "Error" in result

    def test_spawn_status_missing_id(self):
        result = self._simulate_tool_call("subagent_status", {})
        assert "required" in result.lower()

    def test_spawn_status_non_string_id(self):
        result = self._simulate_tool_call("subagent_status", {"agent_id": 123})
        assert "Error" in result

    def test_spawn_status_rejects_non_alnum_id(self):
        result = self._simulate_tool_call("subagent_status", {"agent_id": "../../etc"})
        assert "invalid" in result.lower()

    def test_spawn_status_redacts_credentials(self):
        with patch("personalclaw.mcp_subagents._get") as mock_get:
            mock_get.return_value = {"result": "Found key AKIAIOSFODNN7EXAMPLE in output"}
            result = self._simulate_tool_call("subagent_status", {"agent_id": "abc123"})
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED" in result

    # -- unknown tool: should return clean error --

    def test_unknown_tool(self):
        result = self._simulate_tool_call("nonexistent_tool", {"x": 1})
        assert "Unknown tool" in result


# ── MCP Schedule: simulate ACP agent calling schedule tools ──


class TestJsonRpcProtocol:
    """Verify the JSON-RPC envelope handling matches ACP agent's expectations."""

    def test_initialize_handshake(self):
        """ACP agent sends initialize as the first message."""
        from personalclaw.validation import validate_jsonrpc_request

        method, rid, params = validate_jsonrpc_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "ACP agent", "version": "1.0.0"},
                },
            }
        )
        assert method == "initialize"
        assert rid == 1

    def test_tools_call(self):
        """ACP agent sends tools/call with name and arguments."""
        from personalclaw.validation import validate_jsonrpc_request

        method, rid, params = validate_jsonrpc_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "schedule_add",
                    "arguments": {"name": "test", "message": "hi", "every": 60},
                },
            }
        )
        assert method == "tools/call"

    def test_notification_no_id(self):
        """ACP agent sends notifications/initialized with no id."""
        from personalclaw.validation import validate_jsonrpc_request

        method, rid, params = validate_jsonrpc_request(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        assert method == "notifications/initialized"
        assert rid is None


# ── Validation: verify bad inputs are caught without affecting good ones ──


class TestBadInputsCaught:
    """Verify that malicious/malformed inputs are rejected cleanly."""

    def _core_call(self, name: str, args: dict) -> str:
        from personalclaw.mcp_core import _aggregated_call_tool

        return _aggregated_call_tool(name, args)

    def test_spawn_empty_task(self):
        result = self._core_call("subagent_run", {"task": ""})
        assert "Error" in result

    def test_spawn_task_with_hidden_unicode(self):
        """Zero-width chars should be stripped, not cause errors."""
        with patch("personalclaw.mcp_subagents._post") as mock_post:
            mock_post.return_value = {"id": "clean1"}
            result = self._core_call(
                "subagent_run",
                {"task": "search\u200b for\u200d files"},
            )
        assert "clean1" in result
        # Verify the API received cleaned text
        call_body = mock_post.call_args[0][1]
        assert "\u200b" not in call_body["task"]
        assert "\u200d" not in call_body["task"]

    def test_learn_invalid_category(self):
        result = self._core_call(
            "memory_remember",
            {
                "rule": "test",
                "category": "evil_category",
            },
        )
        # PLATFORM-LEGIBILITY §2: the MCP string boundary now surfaces the
        # WHAT/WHY/FIX envelope — the bad value is named and a FIX line points at
        # the allowed values (did-you-mean), instead of a bare "must be one of".
        assert "Error" in result
        assert "evil_category" in result
        assert "WHAT:" in result and "FIX:" in result

    def test_a_sub_floor_interval_is_flagged(self):
        """🔴 The floor moved, and S109 made it REAL for the first time.

        This asserted `schedule_add`'s schema `min_val=60`. Retiring that alias would have left the
        floor enforced by nothing: measured, `automation_create` with `interval_secs=5` persisted a
        5-second LLM poll with `ok: True` and zero issues, because `MIN_CLOCK_INTERVAL_SECS` was
        declared and read by no code at all.

        A WARNING rather than an error, because R1 makes the floor overridable — the trigger still
        runs, which is why the old `min_val=60` hard rejection was not the right shape to port.
        """
        from personalclaw.triggers.models import MIN_CLOCK_INTERVAL_SECS, validate_spec

        issues = validate_spec("clock", {"kind": "interval", "interval_secs": 5})
        flagged = [i for i in issues if i.path == "spec.interval_secs"]
        assert flagged, "a 5-second LLM poll must not pass silently"
        assert str(MIN_CLOCK_INTERVAL_SECS) in flagged[0].message
        assert flagged[0].severity != "error", "R1 makes the floor overridable"
        assert not [
            i
            for i in validate_spec(
                "clock", {"kind": "interval", "interval_secs": MIN_CLOCK_INTERVAL_SECS}
            )
            if i.path == "spec.interval_secs"
        ]

    def test_extra_fields_rejected(self):
        result = self._core_call(
            "subagent_run",
            {
                "task": "test",
                "injected_field": "malicious",
            },
        )
        assert "Error" in result
        assert "unknown field" in result

    def test_wrong_type_rejected(self):
        result = self._core_call(
            "subagent_run",
            {
                "task": 12345,  # should be string
            },
        )
        assert "Error" in result

    def test_oversized_response_truncated(self):
        """Responses > 100K are truncated at the MCP protocol layer."""
        large_text = "x" * 200_000
        from personalclaw.validation import build_tool_response

        response = build_tool_response(large_text)
        assert len(response["content"][0]["text"]) < 150_000
        assert "truncated" in response["content"][0]["text"]


# ── Dashboard API body validation helpers ──


class TestDashboardApiPatterns:
    """Simulate dashboard REST API input patterns."""

    def test_lesson_create_body(self):
        """POST /api/lessons body validation."""
        from personalclaw.validation import (
            ALLOWED_LESSON_CATEGORIES,
            validate_api_body,
            validate_string_field,
        )

        body = validate_api_body({"rule": "use dark mode", "category": "preference"})
        rule = validate_string_field(body, "rule", required=True, max_len=500)
        cat = validate_string_field(body, "category", allowed=ALLOWED_LESSON_CATEGORIES)
        assert rule == "use dark mode"
        assert cat == "preference"

    def test_cron_create_body(self):
        """POST /api/crons body validation."""
        from personalclaw.validation import validate_api_body, validate_string_field

        body = validate_api_body(
            {
                "name": "check pipeline",
                "message": "check deployment status",
                "every": 300,
            }
        )
        name = validate_string_field(body, "name", required=True, max_len=500)
        msg = validate_string_field(body, "message", required=True, max_len=5000)
        assert name == "check pipeline"
        assert msg == "check deployment status"

    def test_chat_message_body(self):
        """POST /api/chat body validation."""
        from personalclaw.validation import validate_api_body, validate_string_field

        body = validate_api_body({"message": "what's the status of my pipeline?"})
        msg = validate_string_field(body, "message", required=True, max_len=50_000)
        assert msg == "what's the status of my pipeline?"

    def test_skill_create_body(self):
        """POST /api/skills body validation."""
        from personalclaw.validation import validate_api_body, validate_string_field

        body = validate_api_body(
            {
                "name": "my-skill",
                "content": "---\nname: my-skill\n---\n# My Skill\nDo stuff.",
            }
        )
        name = validate_string_field(body, "name", required=True, max_len=100)
        content = validate_string_field(body, "content", required=True, max_len=50_000)
        assert name == "my-skill"
        assert "My Skill" in content
