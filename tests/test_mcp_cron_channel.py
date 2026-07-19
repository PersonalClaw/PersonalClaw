"""Tests for the mcp_schedule channel parameter."""

from pathlib import Path
from unittest.mock import patch

from personalclaw.mcp_schedule import _call_tool


class TestScheduleAddChannel:
    def test_add_with_channel(self, tmp_path: Path) -> None:
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
                {"name": "ops", "message": "check", "every": 300, "channel": "C0AP77JJSN6"},
            )
            mock_svc.add_job.assert_called_once()
            call_kwargs = mock_svc.add_job.call_args
            assert (
                call_kwargs.kwargs.get("channel") == "C0AP77JJSN6"
                or call_kwargs[1].get("channel") == "C0AP77JJSN6"
            )
            assert "abc" in result

    def test_add_without_channel(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
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
                    "agent_id": "",
                },
            )()
            mock_svc.add_job.return_value = mock_job
            result = _call_tool("schedule_add", {"name": "ops", "message": "check", "every": 300})
            call_kwargs = mock_svc.add_job.call_args
            assert (
                call_kwargs.kwargs.get("channel") is None or call_kwargs[1].get("channel") is None
            )
            assert "def" in result
