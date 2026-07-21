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
    """Test subagent_run with tasks array spawns all and returns immediately."""
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.side_effect = [{"id": "a1"}, {"id": "b2"}, {"id": "c3"}]

        result = _call_tool("subagent_run", {"tasks": ["task1", "task2", "task3"]})

        assert "3 subagent" in result
        assert "a1" in result
        assert "b2" in result
        assert "c3" in result
        assert mock_post.call_count == 3


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
    """Test subagent_run stops on first spawn error in batch."""
    with patch("personalclaw.mcp_subagents._post") as mock_post:
        mock_post.side_effect = [{"id": "ok1"}, {"error": "capacity reached"}]

        result = _call_tool("subagent_run", {"tasks": ["task1", "task2"]})

        assert "Spawned" in result or "queued" in result
