"""Tests for cron skip_dates and timezone feature."""

import time
from calendar import timegm
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from personalclaw.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    ScheduleService,
    make_agent_action,
)


class TestIsDueSkipDates:
    """Unit tests for _is_due() skip_dates logic."""

    def _make_cron_job(self, **kwargs) -> ScheduleJob:
        defaults = dict(
            id="test-123",
            name="test",
            action=make_agent_action(message="hello"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="* * * * *"),
            created_ts=time.time() - 3600,
        )
        defaults.update(kwargs)
        return ScheduleJob(**defaults)

    def test_no_skip_dates_fires_normally(self) -> None:
        job = self._make_cron_job()
        now = time.time()
        assert ScheduleService._is_due(job, now)

    def test_skip_today_blocks_firing(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        job = self._make_cron_job(skip_dates=[today], timezone="UTC")
        now = time.time()
        assert not ScheduleService._is_due(job, now)

    def test_skip_other_date_fires_normally(self) -> None:
        job = self._make_cron_job(skip_dates=["2099-12-25"], timezone="UTC")
        now = time.time()
        assert ScheduleService._is_due(job, now)

    def test_skip_dates_uses_job_timezone(self) -> None:
        """A date that is 'today' in one timezone but not another."""
        # Use a timezone far ahead of UTC so the local date may differ
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Pacific/Auckland")  # UTC+12/+13
        local_today = datetime.now(tz).strftime("%Y-%m-%d")

        job = self._make_cron_job(skip_dates=[local_today], timezone="Pacific/Auckland")
        now = time.time()
        # Should be skipped because we're checking in Auckland's timezone
        assert not ScheduleService._is_due(job, now)

    def test_skip_dates_falls_back_to_global_config_tz(self) -> None:
        """When job.timezone is empty, falls back to global config."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        job = self._make_cron_job(skip_dates=[today], timezone="")

        with patch("personalclaw.schedule.AppConfig.load") as mock_cfg:
            mock_cfg.return_value.timezone = "UTC"
            assert not ScheduleService._is_due(job, time.time())

    def test_skip_dates_empty_list_fires(self) -> None:
        job = self._make_cron_job(skip_dates=[])
        assert ScheduleService._is_due(job, time.time())

    def test_skip_dates_uses_now_parameter_not_wall_clock(self) -> None:
        """skip_dates check should use the now parameter, not datetime.now()."""
        # Synthetic now: 2026-04-06 12:00 UTC
        synthetic_now = timegm((2026, 4, 6, 12, 0, 0, 0, 0, 0))
        job = self._make_cron_job(
            skip_dates=["2026-04-06"],
            timezone="UTC",
            created_ts=synthetic_now - 3600,
        )
        assert not ScheduleService._is_due(job, synthetic_now)

    def test_last_run_ts_not_updated_on_skip(self, tmp_path: Path) -> None:
        """Skipped jobs should not update last_run_ts."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        svc = ScheduleService(base_dir=tmp_path)
        svc._load()
        job = svc.add_job(
            name="test", action=make_agent_action(message="hello"), cron_expr="* * * * *"
        )
        job.skip_dates = [today]
        job.timezone = "UTC"
        svc._save()

        original_ts = job.last_run_ts
        assert not ScheduleService._is_due(job, time.time())
        assert job.last_run_ts == original_ts


class TestIsDueSkipWithEverySchedule:
    """Skip dates apply to all schedule types including 'every'."""

    def test_every_schedule_skipped(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        job = ScheduleJob(
            id="test-every",
            name="test",
            action=make_agent_action(message="hello"),
            schedule=ScheduleDefinition(kind="every", every_secs=60),
            created_ts=time.time() - 120,  # due
            skip_dates=[today],
            timezone="UTC",
        )
        assert not ScheduleService._is_due(job, time.time())

    def test_invalid_timezone_falls_back_to_utc(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        job = ScheduleJob(
            id="test-badtz",
            name="test",
            action=make_agent_action(message="hello"),
            schedule=ScheduleDefinition(kind="cron", cron_expr="* * * * *"),
            created_ts=time.time() - 3600,
            skip_dates=[today],
            timezone="NotATimezone",
        )
        # Should not crash; falls back to UTC and still skips
        assert not ScheduleService._is_due(job, time.time())
