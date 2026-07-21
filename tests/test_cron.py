"""Tests for the cron service."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from personalclaw.schedule import (
    _TIMER_POLL_SECS,
    ScheduleDefinition,
    ScheduleJob,
    ScheduleService,
    _job_tz,
    compute_next_run_ts,
    cron_expr_matches,
    make_agent_action,
    validate_cron_expr,
)


class TestCronExprMatching:
    def test_every_minute(self) -> None:
        dt = datetime(2026, 2, 15, 9, 30, tzinfo=timezone.utc)
        assert cron_expr_matches("* * * * *", dt)

    def test_specific_minute_hour(self) -> None:
        dt = datetime(2026, 2, 15, 9, 30, tzinfo=timezone.utc)
        assert cron_expr_matches("30 9 * * *", dt)
        assert not cron_expr_matches("0 9 * * *", dt)

    def test_step(self) -> None:
        dt = datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc)
        assert cron_expr_matches("*/5 * * * *", dt)
        dt2 = datetime(2026, 2, 15, 9, 3, tzinfo=timezone.utc)
        assert not cron_expr_matches("*/5 * * * *", dt2)

    def test_range(self) -> None:
        # 2026-02-16 is Monday, 2026-02-15 is Sunday
        dt_mon = datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc)  # Monday
        assert cron_expr_matches("0 9 * * 1-5", dt_mon)  # cron: 1=Mon..5=Fri
        dt_sun = datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc)  # Sunday
        assert not cron_expr_matches("0 9 * * 1-5", dt_sun)

    def test_named_days(self) -> None:
        dt_mon = datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc)  # Monday
        assert cron_expr_matches("0 9 * * MON-FRI", dt_mon)

    def test_comma_list(self) -> None:
        dt = datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc)
        assert cron_expr_matches("0 9,10,11 * * *", dt)
        assert not cron_expr_matches("0 10,11 * * *", dt)

    def test_invalid_expr(self) -> None:
        dt = datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc)
        assert not cron_expr_matches("bad", dt)


class TestValidateCronExpr:
    def test_valid(self) -> None:
        assert validate_cron_expr("0 9 * * *")
        assert validate_cron_expr("*/5 * * * MON-FRI")
        assert validate_cron_expr("0 9 1,15 * *")

    def test_invalid(self) -> None:
        assert not validate_cron_expr("bad")
        assert not validate_cron_expr("* * *")
        assert not validate_cron_expr("")


class TestCronService:
    def test_add_job_every(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="test", action=make_agent_action(message="hello"), every_secs=300)
        assert job.id
        assert job.name == "test"
        assert job.schedule.kind == "every"
        assert job.schedule.every_secs == 300

    def test_add_job_at(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(
            name="once", action=make_agent_action(message="do it"), at_ts=9999999999.0
        )
        assert job.schedule.kind == "at"
        assert job.schedule.at_ts == 9999999999.0

    def test_add_job_cron_expr(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(
            name="daily", action=make_agent_action(message="briefing"), cron_expr="0 9 * * *"
        )
        assert job.schedule.kind == "cron"
        assert job.schedule.cron_expr == "0 9 * * *"

    def test_add_job_invalid_cron_expr(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        with pytest.raises(ValueError, match="Invalid cron"):
            svc.add_job(name="bad", action=make_agent_action(message="nope"), cron_expr="invalid")

    def test_add_job_no_schedule_raises(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        with pytest.raises(ValueError, match="Must provide"):
            svc.add_job(name="bad", action=make_agent_action(message="nope"))

    def test_min_interval(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="fast", action=make_agent_action(message="go"), every_secs=5)
        assert job.schedule.every_secs == 60

    def test_remove_job(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="rm", action=make_agent_action(message="bye"), every_secs=300)
        assert svc.remove_job(job.id)
        assert not svc.remove_job("nonexistent")

    def test_list_jobs(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        svc.add_job(name="a", action=make_agent_action(message="1"), every_secs=300)
        svc.add_job(name="b", action=make_agent_action(message="2"), every_secs=600)
        assert len(svc.list_jobs()) == 2

    def test_persistence(self, tmp_path: Path) -> None:
        svc1 = ScheduleService(base_dir=tmp_path)
        svc1._load()
        svc1.add_job(name="persist", action=make_agent_action(message="test"), every_secs=300)

        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        assert len(svc2.list_jobs()) == 1
        assert svc2.list_jobs()[0].name == "persist"

    def test_persistence_cron_expr(self, tmp_path: Path) -> None:
        svc1 = ScheduleService(base_dir=tmp_path)
        svc1._load()
        svc1.add_job(
            name="daily", action=make_agent_action(message="hi"), cron_expr="0 9 * * MON-FRI"
        )

        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        job = svc2.list_jobs()[0]
        assert job.schedule.kind == "cron"
        assert job.schedule.cron_expr == "0 9 * * MON-FRI"

    def test_status(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        svc.add_job(name="s", action=make_agent_action(message="m"), every_secs=300)
        status = svc.status()
        assert status["jobs"] == 1
        assert status["enabled"] == 1

    def test_load_corrupted(self, tmp_path: Path) -> None:
        (tmp_path / "crons.json").write_text("not json")
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        assert svc.list_jobs() == []

    def test_add_job_default_not_silent(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="t", action=make_agent_action(message="m"), every_secs=300)
        assert job.silent is False

    def test_silent_field_persists(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="t", action=make_agent_action(message="m"), every_secs=300)
        job.silent = True
        svc._save()

        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        assert svc2.list_jobs()[0].silent is True

    def test_silent_field_default_false(self) -> None:
        job = ScheduleJob(id="x", name="x", action=make_agent_action(message="x"))
        assert job.silent is False

    def test_add_job_with_channel(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(
            name="ops",
            action=make_agent_action(message="check"),
            every_secs=300,
            channel="C0AP77JJSN6",
        )
        assert job.channel == "C0AP77JJSN6"

    def test_add_job_channel_persists(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(
            name="ops",
            action=make_agent_action(message="check"),
            every_secs=300,
            channel="C0AP77JJSN6",
        )
        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        loaded = [j for j in svc2.list_jobs() if j.id == job.id][0]
        assert loaded.channel == "C0AP77JJSN6"

    def test_add_job_channel_default_none(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="ops", action=make_agent_action(message="check"), every_secs=300)
        assert job.channel is None

    def test_approval_mode_default(self, tmp_path: Path) -> None:
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="test", action=make_agent_action(message="hello"), every_secs=300)
        assert job.approval_mode == ""

    def test_approval_mode_persists(self, tmp_path: Path) -> None:
        svc1 = ScheduleService(base_dir=tmp_path)
        svc1._load()
        svc1.add_job(
            name="auto-job",
            action=make_agent_action(message="go", approval_mode="auto"),
            every_secs=300,
        )
        svc1._save()

        svc2 = ScheduleService(base_dir=tmp_path)
        svc2._load()
        loaded = svc2.list_jobs()[0]
        assert loaded.approval_mode == "auto"

    def test_approval_mode_missing_in_json(self, tmp_path: Path) -> None:
        """Old crons.json without approval_mode should default to empty string."""
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
        assert svc.list_jobs()[0].approval_mode == ""


class TestTimerRestoreOnLoad:
    """Verify that _load() restores timers for active jobs when running."""

    def _write_jobs(self, tmp_path: Path, jobs: list[dict]) -> None:
        (tmp_path / "crons.json").write_text(json.dumps({"version": 1, "jobs": jobs}))

    def _make_job(self, *, enabled: bool = True, job_id: str = "abc123") -> dict:
        return {
            "id": job_id,
            "name": "test",
            "message": "hello",
            "schedule": {"kind": "every", "every_secs": 300},
            "enabled": enabled,
            "created_ts": time.time(),
        }

    def test_load_active_jobs_arms_timer(self, tmp_path: Path) -> None:
        """Active jobs loaded from disk must trigger _arm_timer."""
        self._write_jobs(tmp_path, [self._make_job()])
        svc = ScheduleService(base_dir=tmp_path)
        svc._running = True
        with patch.object(svc, "_arm_timer") as mock_arm:
            svc._load()
            mock_arm.assert_called_once()

    def test_load_paused_jobs_no_timer(self, tmp_path: Path) -> None:
        """Paused (disabled) jobs must NOT trigger _arm_timer."""
        self._write_jobs(tmp_path, [self._make_job(enabled=False)])
        svc = ScheduleService(base_dir=tmp_path)
        svc._running = True
        with patch.object(svc, "_arm_timer") as mock_arm:
            svc._load()
            mock_arm.assert_not_called()

    def test_load_not_running_no_timer(self, tmp_path: Path) -> None:
        """Jobs loaded before start() must NOT trigger _arm_timer."""
        self._write_jobs(tmp_path, [self._make_job()])
        svc = ScheduleService(base_dir=tmp_path)
        with patch.object(svc, "_arm_timer") as mock_arm:
            svc._load()
            mock_arm.assert_not_called()

    def test_load_logs_restored_count(self, tmp_path: Path, caplog) -> None:
        """Log message must include the count of restored timers."""
        self._write_jobs(
            tmp_path,
            [self._make_job(job_id="a"), self._make_job(job_id="b")],
        )
        svc = ScheduleService(base_dir=tmp_path)
        svc._running = True
        with patch.object(svc, "_arm_timer"):
            with caplog.at_level(logging.INFO, logger="personalclaw.schedule"):
                svc._load()
        assert "Restored 2 cron timer(s) from disk" in caplog.text


class TestEffectiveDelay:
    """Tests for _effective_delay — the capped timer delay used by _arm_timer."""

    def test_far_future_at_job_capped_at_poll_interval(self, tmp_path: Path) -> None:
        """A one-shot job far in the future must not sleep beyond _TIMER_POLL_SECS."""
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        svc.add_job(name="future", action=make_agent_action(message="later"), at_ts=9999999999.0)

        assert svc._effective_delay() == _TIMER_POLL_SECS

    def test_imminent_job_not_capped(self, tmp_path: Path) -> None:
        """A job due very soon should return its actual short delay, not the poll interval."""
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        svc.add_job(name="soon", action=make_agent_action(message="now"), at_ts=time.time() + 2)

        delay = svc._effective_delay()

        assert delay < _TIMER_POLL_SECS

    def test_no_jobs_defaults_to_poll_interval(self, tmp_path: Path) -> None:
        """With no jobs, _effective_delay returns the poll interval."""
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()

        assert svc._effective_delay() == _TIMER_POLL_SECS

    def test_disabled_jobs_default_to_poll_interval(self, tmp_path: Path) -> None:
        """Disabled jobs should not influence the delay — falls back to poll interval."""
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(name="off", action=make_agent_action(message="skip"), at_ts=9999999999.0)
        job.enabled = False

        assert svc._effective_delay() == _TIMER_POLL_SECS


class TestFormatSchedule:
    @pytest.fixture(autouse=False)
    def _utc_tz(self):
        """Pin TZ=UTC for tests that compare dates across today/future."""
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        yield
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    def test_cron_expr_human_readable(self, monkeypatch) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        s = ScheduleDefinition(kind="cron", cron_expr="0 22 * * 1-5")
        result = format_schedule(s, tz_name="")
        assert "Monday through Friday" in result
        assert "10:00 PM" in result

    def test_cron_expr_with_timezone(self) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        s = ScheduleDefinition(kind="cron", cron_expr="0 22 * * 1-5")
        result = format_schedule(s, tz_name="America/Los_Angeles")
        # Expression is evaluated in job timezone (LA), so 22:00 = 10 PM local
        assert "10:00 PM" in result
        assert "PDT" in result or "PST" in result
        assert "Monday through Friday" in result

    def test_cron_expr_single_day(self, monkeypatch) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        s = ScheduleDefinition(kind="cron", cron_expr="0 21 * * 5")
        result = format_schedule(s, tz_name="")
        assert "Friday" in result

    def test_single_digit_hour_with_timezone(self) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        # 03:00 in LA timezone = 3 AM local, no date boundary issue
        s = ScheduleDefinition(kind="cron", cron_expr="0 3 * * *")
        result = format_schedule(s, tz_name="America/Los_Angeles")
        assert "PDT" in result or "PST" in result
        assert "3:00 AM" in result

    def test_every_secs(self) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        s = ScheduleDefinition(kind="every", every_secs=300)
        assert format_schedule(s) == "every 300s"

    def test_every_hours(self) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        s = ScheduleDefinition(kind="every", every_secs=7200)
        assert format_schedule(s) == "every 2h"

    def test_at_timestamp_today(self, monkeypatch, _utc_tz) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        # Mock "now" to 2026-04-10, job at 3PM same day
        fake_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        # Mock only covers now() and fromtimestamp() — extend if format_schedule evolves.
        monkeypatch.setattr(
            "personalclaw.schedule.datetime",
            type(
                "D",
                (datetime,),
                {
                    "now": classmethod(lambda cls, tz=None: fake_now),
                    "fromtimestamp": staticmethod(
                        lambda ts, tz=None: datetime.fromtimestamp(ts, tz)
                    ),
                },
            ),
        )
        job_ts = datetime(2026, 4, 10, 15, 0, tzinfo=timezone.utc).timestamp()
        result = format_schedule(ScheduleDefinition(kind="at", at_ts=job_ts))
        assert result.startswith("at ")
        assert "," not in result  # no date for today

    def test_at_timestamp_future_date(self, monkeypatch, _utc_tz) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        # Mock "now" to 2026-04-10, job on Apr 17
        fake_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        # Mock only covers now() and fromtimestamp() — extend if format_schedule evolves.
        monkeypatch.setattr(
            "personalclaw.schedule.datetime",
            type(
                "D",
                (datetime,),
                {
                    "now": classmethod(lambda cls, tz=None: fake_now),
                    "fromtimestamp": staticmethod(
                        lambda ts, tz=None: datetime.fromtimestamp(ts, tz)
                    ),
                },
            ),
        )
        job_ts = datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc).timestamp()
        result = format_schedule(ScheduleDefinition(kind="at", at_ts=job_ts))
        assert "Apr 17" in result

    def test_unknown_kind(self) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        s = ScheduleDefinition(kind="unknown")
        assert format_schedule(s) == "unknown"

    def test_every_5_minutes(self, monkeypatch) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        s = ScheduleDefinition(kind="cron", cron_expr="*/5 * * * *")
        result = format_schedule(s, tz_name="")
        assert "5 minutes" in result

    def test_invalid_timezone_falls_back(self, monkeypatch) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        s = ScheduleDefinition(kind="cron", cron_expr="0 22 * * 1-5")
        result = format_schedule(s, tz_name="Invalid/Timezone")
        # Should still return a description, just without tz conversion
        assert "Monday through Friday" in result

    def test_config_timezone_fallback(self, monkeypatch) -> None:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": "America/New_York"})()),
        )
        s = ScheduleDefinition(kind="cron", cron_expr="0 22 * * 1-5")
        result = format_schedule(s)
        # Expression is evaluated in job timezone (ET fallback), so 22:00 = 10 PM local
        assert "10:00 PM" in result
        assert "EDT" in result or "EST" in result
        assert "Monday through Friday" in result


class TestComputeNextRunTs:
    """Tests for compute_next_run_ts helper."""

    def test_every_schedule(self) -> None:
        now = 5000.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="every", every_secs=300),
            created_ts=1000.0,
            last_run_ts=4800.0,
        )
        result = compute_next_run_ts(job, now=now)
        assert result == 5100.0

    def test_every_schedule_no_last_run(self) -> None:
        now = 5000.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="every", every_secs=60),
            created_ts=4970.0,
        )
        result = compute_next_run_ts(job, now=now)
        assert result == 5030.0

    def test_every_schedule_overdue_returns_now(self) -> None:
        now = 5000.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="every", every_secs=60),
            created_ts=1000.0,
            last_run_ts=1000.0,
        )
        result = compute_next_run_ts(job, now=now)
        assert result == now

    def test_at_schedule_future(self) -> None:
        now = 5000.0
        future_ts = 8600.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="at", at_ts=future_ts),
        )
        assert compute_next_run_ts(job, now=now) == future_ts

    def test_at_schedule_past_returns_none(self) -> None:
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="at", at_ts=1000.0),
        )
        assert compute_next_run_ts(job, now=5000.0) is None

    def test_cron_schedule(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        now = 1745000000.0  # 2025-04-18T18:13:20Z
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 12 * * *"),
        )
        result = compute_next_run_ts(job, now=now)
        # next "0 12 * * *" after 2025-04-18T18:13:20Z → 2025-04-19T12:00:00Z
        expected = datetime(2025, 4, 19, 12, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_disabled_job_returns_none(self) -> None:
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="every", every_secs=300),
            enabled=False,
        )
        assert compute_next_run_ts(job, now=5000.0) is None

    def test_invalid_cron_expr_returns_none(self) -> None:
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="invalid"),
        )
        assert compute_next_run_ts(job, now=5000.0) is None

    def test_every_schedule_no_last_run_uses_created_ts_zero(self) -> None:
        """When last_run_ts is None and created_ts is 0.0 (default), uses 0.0 as base."""
        now = 5000.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="every", every_secs=300),
            created_ts=0.0,
            last_run_ts=None,
        )
        # 0.0 + 300 = 300.0, which is < now, so returns now
        assert compute_next_run_ts(job, now=now) == now

    def test_at_schedule_exact_now_returns_none(self) -> None:
        """at_ts exactly equal to now is treated as expired."""
        now = 5000.0
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="at", at_ts=now),
        )
        assert compute_next_run_ts(job, now=now) is None


class TestTimezoneScheduling:
    """Tests for timezone-aware cron scheduling."""

    def test_job_tz_returns_zoneinfo(self) -> None:
        job = ScheduleJob(
            id="j1", name="t", action=make_agent_action(message="m"), timezone="America/Toronto"
        )
        tz = _job_tz(job)
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "America/Toronto"

    def test_job_tz_empty_returns_utc(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        job = ScheduleJob(id="j1", name="t", action=make_agent_action(message="m"), timezone="")
        assert _job_tz(job) == ZoneInfo("UTC")

    def test_job_tz_invalid_falls_back_to_utc(self) -> None:
        job = ScheduleJob(
            id="j1", name="t", action=make_agent_action(message="m"), timezone="Fake/Zone"
        )
        assert _job_tz(job) == ZoneInfo("UTC")

    def test_compute_next_run_ts_with_timezone(self) -> None:
        """Job at 1pm Toronto should compute next fire at 17:00 UTC (EDT = UTC-4)."""
        # 2025-04-18T12:00:00 UTC = 2025-04-18T08:00:00 EDT
        # Next "0 13 * * *" in Toronto = 2025-04-18T13:00:00 EDT = 17:00:00 UTC
        now = datetime(2025, 4, 18, 12, 0, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
            timezone="America/Toronto",
        )
        result = compute_next_run_ts(job, now=now)
        expected = datetime(2025, 4, 18, 17, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_compute_next_run_ts_no_timezone_stays_utc(self, monkeypatch) -> None:
        """Backward compat: no timezone means cron_expr evaluated as UTC."""
        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        now = datetime(2025, 4, 18, 12, 0, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
        )
        result = compute_next_run_ts(job, now=now)
        expected = datetime(2025, 4, 18, 13, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_is_due_respects_timezone(self) -> None:
        """Job at 1pm Toronto should be due at 17:00 UTC, not 13:00 UTC."""
        # 17:00 UTC = 13:00 EDT → should match "0 13 * * *" in Toronto
        now_due = datetime(2025, 4, 18, 17, 0, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
            timezone="America/Toronto",
        )
        assert ScheduleService._is_due(job, now_due) is True

    def test_is_due_not_due_at_utc_time(self) -> None:
        """Job at 1pm Toronto should NOT be due at 13:00 UTC (= 9am EDT)."""
        now_not_due = datetime(2025, 4, 18, 13, 0, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
            timezone="America/Toronto",
        )
        assert ScheduleService._is_due(job, now_not_due) is False

    def test_is_due_no_timezone_fires_at_utc(self, monkeypatch) -> None:
        """Backward compat: no timezone fires at UTC time."""
        monkeypatch.setattr(
            "personalclaw.schedule.AppConfig.load",
            staticmethod(lambda: type("C", (), {"timezone": ""})()),
        )
        now = datetime(2025, 4, 18, 13, 0, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
        )
        assert ScheduleService._is_due(job, now) is True

    def test_is_due_dedup_uses_utc_minute(self) -> None:
        """Same UTC minute should be deduped regardless of timezone."""
        now = datetime(2025, 4, 18, 17, 0, 30, tzinfo=timezone.utc).timestamp()
        last = datetime(2025, 4, 18, 17, 0, 5, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 13 * * *"),
            timezone="America/Toronto",
            last_run_ts=last,
        )
        # Same UTC minute (both timestamps in 17:00 UTC), should be deduped
        assert ScheduleService._is_due(job, now) is False

    def test_is_due_spring_forward_skipped_hour(self) -> None:
        """During spring forward, croniter.match still fires for the skipped hour."""
        # 2025-03-09: Toronto clocks jump 2:00 AM EST -> 3:00 AM EDT at 07:00 UTC.
        # 07:30 UTC = 3:30 AM EDT (hour=3, minute=30). Despite the cron expr
        # targeting hour=2, croniter.match() returns True — this is a known
        # croniter behavior where DST-gap hours are still matched. The job
        # fires once at the shifted UTC time rather than being skipped.
        now = datetime(2025, 3, 9, 7, 30, tzinfo=timezone.utc).timestamp()
        job = ScheduleJob(
            id="j1",
            name="test",
            action=make_agent_action(message="msg"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="30 2 * * *"),
            timezone="America/Toronto",
        )
        assert ScheduleService._is_due(job, now) is True
