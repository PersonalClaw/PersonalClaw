"""Tests for the mcp_schedule thread_ts parameter."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.mcp_schedule import _call_tool
from personalclaw.schedule import ScheduleDefinition
from personalclaw.validation import (
    MCP_SCHEDULE_SCHEMAS,
    SCHEDULE_ADD_SCHEMA,
    ValidationError,
    validate_tool_args,
)


class TestScheduleAddThreadTs:
    def test_add_with_thread_ts(self, tmp_path: Path) -> None:
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_job = type(
                "Job",
                (),
                {
                    "id": "abc",
                    "name": "test",
                    "schedule": type(
                        "S",
                        (),
                        {"kind": "every", "every_secs": 300, "cron_expr": None, "at_ts": None},
                    )(),
                },
            )()
            mock_svc.add_job.return_value = mock_job
            result = _call_tool(
                "schedule_add",
                {
                    "name": "ops",
                    "message": "check",
                    "every": 300,
                    "channel": "C0AP77JJSN6",
                    "thread_ts": "1776298241.408339",
                },
            )
            call_kwargs = mock_svc.add_job.call_args
            assert (
                call_kwargs.kwargs.get("thread_ts") == "1776298241.408339"
                or call_kwargs[1].get("thread_ts") == "1776298241.408339"
            )
            assert "abc" in result

    def test_add_without_thread_ts(self, tmp_path: Path) -> None:
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_job = type(
                "Job",
                (),
                {
                    "id": "def",
                    "name": "test",
                    "schedule": type(
                        "S",
                        (),
                        {"kind": "every", "every_secs": 300, "cron_expr": None, "at_ts": None},
                    )(),
                },
            )()
            mock_svc.add_job.return_value = mock_job
            result = _call_tool(
                "schedule_add",
                {"name": "ops", "message": "check", "every": 300},
            )
            call_kwargs = mock_svc.add_job.call_args
            assert (
                call_kwargs.kwargs.get("thread_ts") is None
                or call_kwargs[1].get("thread_ts") is None
            )
            assert "def" in result


class TestScheduleUpdateThreadTs:
    def test_update_sets_thread_ts(self, tmp_path: Path) -> None:
        with patch("personalclaw.mcp_schedule.ScheduleService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            fake_job = MagicMock()
            fake_job.id = "abc"
            fake_job.name = "test-job"
            fake_job.schedule = ScheduleDefinition(kind="every", every_secs=300)
            mock_svc.update_job.return_value = fake_job
            result = _call_tool(
                "schedule_update",
                {"job_id": "abc", "thread_ts": "1776298241.408339"},
            )
            call_kwargs = mock_svc.update_job.call_args
            assert (
                call_kwargs.kwargs.get("thread_ts") == "1776298241.408339"
                or call_kwargs[1].get("thread_ts") == "1776298241.408339"
            )
            assert "Updated" in result


class TestThreadTsValidation:
    """Schema rejects invalid thread_ts formats."""

    def test_valid_thread_ts_accepted(self) -> None:
        args = {"name": "j", "message": "go", "every": 300, "thread_ts": "1776298241.408339"}
        result = validate_tool_args(args, SCHEDULE_ADD_SCHEMA)
        assert result["thread_ts"] == "1776298241.408339"

    def test_invalid_thread_ts_rejected(self) -> None:
        args = {"name": "j", "message": "go", "every": 300, "thread_ts": "not-a-timestamp"}
        with pytest.raises(ValidationError, match="thread_ts"):
            validate_tool_args(args, SCHEDULE_ADD_SCHEMA)

    def test_empty_thread_ts_passes(self) -> None:
        args = {"name": "j", "message": "go", "every": 300}
        result = validate_tool_args(args, SCHEDULE_ADD_SCHEMA)
        assert "thread_ts" not in result

    def test_schedule_update_thread_ts_validated(self) -> None:
        schema = MCP_SCHEDULE_SCHEMAS["schedule_update"]
        args = {"job_id": "abc123", "thread_ts": "bad"}
        with pytest.raises(ValidationError, match="thread_ts"):
            validate_tool_args(args, schema)

    def test_schedule_update_valid_thread_ts(self) -> None:
        schema = MCP_SCHEDULE_SCHEMAS["schedule_update"]
        args = {"job_id": "abc123", "thread_ts": "1776298241.408339"}
        result = validate_tool_args(args, schema)
        assert result["thread_ts"] == "1776298241.408339"
