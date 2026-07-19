"""Tests for mcp_schedule channel auto-capture."""

import uuid

from personalclaw.mcp_schedule import _call_tool_inner


class TestScheduleAddChannelCapture:
    def test_schedule_add_captures_channel_from_env(self, monkeypatch, tmp_path):
        """PERSONALCLAW_CHANNEL_ID env var is used as job channel."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.setenv("PERSONALCLAW_CHANNEL_ID", "C0ABC123")

        job_name = f"test-job-{uuid.uuid4().hex[:8]}"
        result = _call_tool_inner(
            "schedule_add",
            {"name": job_name, "message": "hello", "every": 120},
        )
        assert "Added job" in result

        from personalclaw.schedule import ScheduleService

        svc = ScheduleService(base_dir=tmp_path)
        jobs = svc.list_jobs()
        matching = [j for j in jobs if j.name == job_name]
        assert len(matching) == 1
        assert matching[0].channel == "C0ABC123"

    def test_schedule_add_no_env_channel_is_none(self, monkeypatch, tmp_path):
        """Without PERSONALCLAW_CHANNEL_ID, job channel is None (DM fallback)."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)

        job_name = f"test-no-channel-{uuid.uuid4().hex[:8]}"
        _call_tool_inner(
            "schedule_add",
            {"name": job_name, "message": "hello", "every": 120},
        )

        from personalclaw.schedule import ScheduleService

        svc = ScheduleService(base_dir=tmp_path)
        jobs = svc.list_jobs()
        matching = [j for j in jobs if j.name == job_name]
        assert len(matching) == 1
        assert matching[0].channel is None

    def test_cron_respects_personalclaw_home(self, monkeypatch, tmp_path):
        """ScheduleService uses PERSONALCLAW_HOME when set, not the default ~/.personalclaw."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)

        job_name = f"test-home-{uuid.uuid4().hex[:8]}"
        result = _call_tool_inner(
            "schedule_add",
            {"name": job_name, "message": "hello", "every": 120},
        )
        assert "Added job" in result

        # Job should be in tmp_path, not ~/.personalclaw
        crons_file = tmp_path / "crons.json"
        assert crons_file.exists(), "crons.json not written to PERSONALCLAW_HOME directory"

        from personalclaw.schedule import ScheduleService

        svc = ScheduleService(base_dir=tmp_path)
        jobs = svc.list_jobs()
        assert any(j.name == job_name for j in jobs)


class TestScheduleAddZeroToken:
    def test_schedule_add_command_creates_command_mode_job(self, monkeypatch, tmp_path):
        """schedule_add with command= creates a zero-token job (exec_mode == 'command')."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)

        job_name = f"cmd-{uuid.uuid4().hex[:8]}"
        result = _call_tool_inner(
            "schedule_add",
            {"name": job_name, "message": "ignored", "every": 120, "command": "echo hi"},
        )
        assert "Added job" in result

        from personalclaw.schedule import ScheduleService

        svc = ScheduleService(base_dir=tmp_path)
        job = next(j for j in svc.list_jobs() if j.name == job_name)
        assert job.exec_mode == "command"
        assert job.command == "echo hi"

    def test_schedule_add_rejects_both_script_and_command(self, monkeypatch, tmp_path):
        """script and command are mutually exclusive (ValueError surfaced as 'Error: ...')."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        result = _call_tool_inner(
            "schedule_add",
            {
                "name": f"both-{uuid.uuid4().hex[:8]}",
                "message": "m",
                "every": 120,
                "command": "echo x",
                "script": "crons/a.py:run",
            },
        )
        assert result.startswith("Error:")
