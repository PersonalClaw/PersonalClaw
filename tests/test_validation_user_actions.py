"""Simulate real user actions through MCP tool paths.

Exercises the exact call patterns the ACP agent sends when the LLM invokes MCP
tools, plus the dashboard API patterns. Each test represents a real user action
that input validation must accept.
"""

from unittest.mock import MagicMock, patch

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


class TestMcpScheduleUserActions:
    """Simulate the exact JSON-RPC calls ACP agent sends to personalclaw-schedule."""

    def _simulate_tool_call(self, tool_name: str, arguments: dict) -> str:
        from personalclaw.mcp_schedule import _call_tool

        return _call_tool(tool_name, arguments)

    # -- schedule_add: user says "check my pipeline every 5 minutes" --

    def test_add_every_interval(self, tmp_path):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "abc123"
            job.name = "pipeline check"
            job.schedule = MagicMock()
            job.schedule.kind = "every"
            job.schedule.every_secs = 300
            job.schedule.cron_expr = None
            job.schedule.at_ts = None
            job.agent_id = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "pipeline check",
                    "message": "check the status of my deployment pipeline",
                    "every": 300,
                },
            )
        assert "abc123" in result
        assert "pipeline check" in result

    # -- schedule_add with cron expression: "weekdays at 9am" --

    def test_add_cron_expression(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "def456"
            job.name = "standup"
            job.schedule = MagicMock()
            job.schedule.kind = "cron"
            job.schedule.cron_expr = "0 9 * * 1-5"
            job.schedule.every_secs = None
            job.schedule.at_ts = None
            job.agent_id = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "standup",
                    "message": "summarize yesterday's work",
                    "cron_expr": "0 9 * * 1-5",
                },
            )
        assert "def456" in result

    # -- schedule_add with agent: "use a named code agent for this job" --

    def test_add_with_agent(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "ghi789"
            job.name = "code-agent check"
            job.schedule = MagicMock()
            job.schedule.kind = "every"
            job.schedule.every_secs = 600
            job.schedule.cron_expr = None
            job.schedule.at_ts = None
            job.agent_id = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "code-agent check",
                    "message": "check the build pipeline",
                    "every": 600,
                    "agent": "my-code-agent",
                },
            )
        assert "ghi789" in result
        # Verify the agent was carried into the invoke-agent action passed to add_job
        from personalclaw.schedule import make_agent_action

        assert svc.add_job.call_args.kwargs["action"] == make_agent_action(
            message="check the build pipeline", agent="my-code-agent"
        )

    # -- schedule_add with approval_mode: "auto-approve tools for this cron" --

    def test_add_with_approval_mode_auto(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "appr001"
            job.name = "auto review"
            job.schedule = MagicMock()
            job.schedule.kind = "cron"
            job.schedule.cron_expr = "0 16 * * 1-5"
            job.schedule.every_secs = None
            job.schedule.at_ts = None
            job.agent_id = ""
            job.approval_mode = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "auto review",
                    "message": "review CRs",
                    "cron_expr": "0 16 * * 1-5",
                    "agent": "gaia-cr-review",
                    "approval_mode": "auto",
                },
            )
        assert "appr001" in result
        from personalclaw.schedule import make_agent_action

        assert svc.add_job.call_args.kwargs["action"] == make_agent_action(
            message="review CRs", agent="gaia-cr-review", approval_mode="auto"
        )

    def test_add_with_approval_mode_empty(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "appr002"
            job.name = "default approval"
            job.schedule = MagicMock()
            job.schedule.kind = "every"
            job.schedule.every_secs = 300
            job.schedule.cron_expr = None
            job.schedule.at_ts = None
            job.agent_id = ""
            job.approval_mode = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "default approval",
                    "message": "check stuff",
                    "every": 300,
                },
            )
        assert "appr002" in result
        # approval_mode should remain empty (not set)
        assert job.approval_mode == ""

    # -- schedule_add without agent (most common): should work fine --

    def test_add_without_agent(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "noagent1"
            job.name = "basic"
            job.schedule = MagicMock()
            job.schedule.kind = "every"
            job.schedule.every_secs = 120
            job.schedule.cron_expr = None
            job.schedule.at_ts = None
            job.agent_id = ""
            svc.add_job.return_value = job
            result = self._simulate_tool_call(
                "schedule_add",
                {
                    "name": "basic",
                    "message": "hello",
                    "every": 120,
                },
            )
        assert "noagent1" in result
        # agent_id should NOT have been set
        assert job.agent_id == ""

    # -- schedule_list: user says "what cron jobs do I have?" --

    def test_list_jobs(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "list1"
            job.name = "my job"
            job.message = "do stuff"
            job.enabled = True
            job.schedule = MagicMock()
            job.schedule.kind = "every"
            job.schedule.every_secs = 300
            job.schedule.cron_expr = None
            job.schedule.at_ts = None
            svc.list_jobs.return_value = [job]
            result = self._simulate_tool_call("schedule_list", {})
        assert "my job" in result
        assert "list1" in result

    def test_list_empty(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            svc.list_jobs.return_value = []
            result = self._simulate_tool_call("schedule_list", {})
        assert "No cron jobs" in result

    # -- schedule_remove/pause/resume --

    def test_remove_job(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            svc.remove_job.return_value = True
            result = self._simulate_tool_call("schedule_remove", {"job_id": "abc12345"})
        assert "Removed" in result

    def test_pause_job(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            svc.enable_job.return_value = True
            result = self._simulate_tool_call("schedule_pause", {"job_id": "abc12345"})
        assert "Paused" in result

    def test_resume_job(self):
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc:
            svc = mock_svc.return_value
            svc.enable_job.return_value = True
            result = self._simulate_tool_call("schedule_resume", {"job_id": "abc12345"})
        assert "Resumed" in result

    # -- schedule_remove_all --

    def test_remove_all(self):
        with (
            patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc,
            patch.dict("os.environ", {"PERSONALCLAW_CLI": "1"}, clear=False) as env,
        ):
            env.pop("PERSONALCLAW_SESSION_KEY", None)
            svc = mock_svc.return_value
            job = MagicMock()
            job.id = "x"
            job.session_key = ""
            svc.list_jobs.return_value = [job]
            svc.remove_job.return_value = True
            result = self._simulate_tool_call("schedule_remove_all", {})
        assert "Removed 1" in result


# ── JSON-RPC Envelope: simulate ACP agent protocol ──


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

    def _cron_call(self, name: str, args: dict) -> str:
        from personalclaw.mcp_schedule import _call_tool

        return _call_tool(name, args)

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
        assert "Error" in result
        assert "must be one of" in result

    def test_cron_interval_too_small(self):
        result = self._cron_call(
            "schedule_add",
            {
                "name": "spam",
                "message": "flood",
                "every": 5,  # below 60s minimum
            },
        )
        assert "Error" in result
        assert ">= 60" in result

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
