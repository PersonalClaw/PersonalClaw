"""Tests for session-scoped cron_remove_all."""

import uuid

import pytest

from personalclaw.mcp_schedule import _call_tool_inner
from personalclaw.schedule import ScheduleService, make_agent_action


def _unique_name() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _isolate_cron_store(monkeypatch, tmp_path):
    """Route all ScheduleService() instances to tmp_path for test isolation."""
    monkeypatch.setattr("personalclaw.schedule._DEFAULT_DIR", tmp_path)
    monkeypatch.setattr("personalclaw.mcp_schedule.config_dir", lambda: tmp_path)


class TestCronAddSessionKey:
    def test_captures_session_key_from_env(self, monkeypatch):
        """cron_add tags job with PERSONALCLAW_SESSION_KEY."""
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-abc")
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        name = _unique_name()
        result = _call_tool_inner("schedule_add", {"name": name, "message": "hi", "every": 120})
        assert "Added job" in result

        svc = ScheduleService()
        jobs = [j for j in svc.list_jobs() if j.name == name]
        assert jobs[0].session_key == "sess-abc"

    def test_no_session_key_leaves_empty(self, monkeypatch):
        """Without PERSONALCLAW_SESSION_KEY, session_key is empty."""
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        monkeypatch.setattr("personalclaw.mcp_schedule._resolve_session_key", lambda: "")
        name = _unique_name()
        _call_tool_inner("schedule_add", {"name": name, "message": "hi", "every": 120})

        svc = ScheduleService()
        jobs = [j for j in svc.list_jobs() if j.name == name]
        assert jobs[0].session_key == ""


class TestCronRemoveAllScoped:
    def test_removes_only_own_session_jobs(self, monkeypatch):
        """With session key, cron_remove_all only removes jobs from that session."""
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        monkeypatch.delenv("PERSONALCLAW_CLI", raising=False)
        n1, n2 = _unique_name(), _unique_name()

        # Create job as session A
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-A")
        _call_tool_inner("schedule_add", {"name": n1, "message": "a", "every": 120})

        # Create job as session B
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-B")
        _call_tool_inner("schedule_add", {"name": n2, "message": "b", "every": 120})

        # Remove all as session A — should only remove n1
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-A")
        result = _call_tool_inner("schedule_remove_all", {})
        assert "Removed 1 job(s)" in result

        svc = ScheduleService()
        remaining = [j for j in svc.list_jobs() if j.name in (n1, n2)]
        assert len(remaining) == 1
        assert remaining[0].name == n2

    def test_cli_removes_all(self, monkeypatch):
        """With PERSONALCLAW_CLI=1, cron_remove_all removes everything."""
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        n1, n2 = _unique_name(), _unique_name()

        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-X")
        _call_tool_inner("schedule_add", {"name": n1, "message": "a", "every": 120})

        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-Y")
        _call_tool_inner("schedule_add", {"name": n2, "message": "b", "every": 120})

        # CLI admin removes everything
        monkeypatch.setenv("PERSONALCLAW_CLI", "1")
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
        result = _call_tool_inner("schedule_remove_all", {})
        assert "Removed 2 job(s)" in result

    def test_no_session_key_no_cli_returns_error(self, monkeypatch):
        """Without session key or CLI flag, returns error."""
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
        monkeypatch.delenv("PERSONALCLAW_CLI", raising=False)
        name = _unique_name()

        # Create a job via CLI so it exists
        monkeypatch.setenv("PERSONALCLAW_CLI", "1")
        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-owner")
        monkeypatch.setattr("personalclaw.mcp_schedule._resolve_session_key", lambda: "sess-owner")
        _call_tool_inner("schedule_add", {"name": name, "message": "a", "every": 120})

        # Try to remove without session key or CLI
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
        monkeypatch.delenv("PERSONALCLAW_CLI", raising=False)
        monkeypatch.setattr("personalclaw.mcp_schedule._resolve_session_key", lambda: "")
        result = _call_tool_inner("schedule_remove_all", {})
        assert "Error: no session key set" in result

    def test_no_matching_jobs_returns_message(self, monkeypatch):
        """Session with no owned jobs gets appropriate message."""
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
        monkeypatch.delenv("PERSONALCLAW_CLI", raising=False)
        name = _unique_name()

        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-owner")
        _call_tool_inner("schedule_add", {"name": name, "message": "a", "every": 120})

        monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "sess-other")
        result = _call_tool_inner("schedule_remove_all", {})
        assert "No cron jobs owned by this session" in result


class TestSessionKeyPersistence:
    def test_session_key_survives_reload(self, tmp_path):
        """session_key field persists through save/load cycle."""
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="persist", action=make_agent_action(message="test"), every_secs=300)
        job.session_key = "sess-persist"
        svc._save()

        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        assert svc2.list_jobs()[0].session_key == "sess-persist"

    def test_missing_session_key_defaults_empty(self, tmp_path):
        """Old crons.json without session_key defaults to empty string."""
        import json

        data = {
            "version": 2,
            "jobs": [
                {
                    "id": "abc123",
                    "name": "legacy",
                    "message": "hi",
                    "schedule": {"kind": "every", "every_secs": 300},
                }
            ],
        }
        (tmp_path / "crons.json").write_text(json.dumps(data))
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        assert svc.list_jobs()[0].session_key == ""
