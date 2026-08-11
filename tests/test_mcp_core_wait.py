"""Test subagent_run fire-and-forget functionality."""

from unittest.mock import patch

from personalclaw.mcp_subagents import _call_tool


def test_spawn_run_single_task():
    """Test subagent_run with single task returns immediately."""
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.return_value = {"id": "abc123"}

        result = _call_tool("subagent_run", {"task": "test task"})

        assert "abc123" in result
        assert "Spawned" in result
        assert "completion event" in result.lower()


def test_spawn_run_batch_tasks():
    """A contract-bearing batch compiles into ONE run rather than N loose spawns.

    This asserted three independent `/api/spawn` calls before the compile cutover. N
    fire-and-forget spawns have no run record, so they cannot render as one widget, survive a
    gateway restart, or be retried per branch — which is why the batch path now goes through
    `compile_batch`. The two POSTs are the compiled def and the run started against it.
    """
    declared = {
        "objective": "determine how the subsystem behaves",
        "output_format": "a markdown list of findings",
        "boundary": "do not modify any source file",
    }
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.side_effect = [{"ok": True}, {"ok": True, "run_id": "run-7"}]

        result = _call_tool(
            "subagent_run",
            {
                "tasks": [
                    {"task": "probe the first subsystem", **declared},
                    {"task": "probe the second subsystem", **declared},
                    {"task": "probe the third subsystem", **declared},
                ]
            },
        )

        assert "run-7" in result
        assert [c.args[0] for c in mock_post.call_args_list] == [
            "/api/workflows",
            "/api/workflows/runs",
        ]


def test_spawn_run_error():
    """Test subagent_run handles spawn API errors."""
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.return_value = {"error": "capacity reached"}

        result = _call_tool("subagent_run", {"task": "failing task"})

        assert "queued" in result or "Error" in result


def test_spawn_run_no_args():
    """Test subagent_run with no task or tasks returns error."""
    result = _call_tool("subagent_run", {})
    assert "Error" in result


def test_spawn_run_empty_tasks():
    """Test subagent_run with empty tasks array returns error."""
    result = _call_tool("subagent_run", {"tasks": []})
    assert "Error" in result


def test_spawn_run_passes_parent_session():
    """subagent_run resolves the parent session key and includes it in the spawn body."""
    with (
        patch("personalclaw.mcp_subagents._post") as mock_post,
        patch("personalclaw.mcp_subagents._resolve_session_key", return_value="dashboard:chat-1"),
    ):
        mock_post.return_value = {"id": "x1"}
        result = _call_tool("subagent_run", {"task": "test"})

        assert "Spawned" in result
        body = (
            mock_post.call_args.args[1]
            if len(mock_post.call_args.args) > 1
            else mock_post.call_args.kwargs.get("body", {})
        )
        assert body.get("parent_session") == "dashboard:chat-1"


def test_spawn_run_batch_partial_failure():
    """A failed persist REPORTS the error and never starts a run against a def that is not there.

    Pre-cutover this asserted a partial success ("stops on first spawn error"), which a batch
    compiled into one run cannot have: there is a single def to save, so the save either lands or
    the batch does not exist. Asserting the run POST was never made is the load-bearing half — a
    run row pointing at a missing def is a widget that survives as a broken row.
    """
    declared = {
        "objective": "determine how the subsystem behaves",
        "output_format": "a markdown list of findings",
        "boundary": "do not modify any source file",
    }
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.side_effect = [{"error": "capacity reached"}]

        result = _call_tool(
            "subagent_run",
            {
                "tasks": [
                    {"task": "probe the first subsystem", **declared},
                    {"task": "probe the second subsystem", **declared},
                ]
            },
        )

        assert "capacity reached" in result
        assert [c.args[0] for c in mock_post.call_args_list] == ["/api/workflows"]
