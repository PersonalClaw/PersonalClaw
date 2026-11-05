"""Quiet hours keep the configured timezone, the one every timed trigger runs in.

The quiet-hours window was read on the process's own clock (`datetime.now()`, and the hour of any
moment handed in as it was), while every timed trigger runs in the configured `timezone`
(`timezones.resolve_zone`). On a gateway whose process zone is not the owner's (a container on UTC
with the owner's zone set), a 22:00 to 07:00 window was held from 22:00 to 07:00 UTC: notices were
held through the owner's evening and interrupted again in the middle of the owner's night. The
window is now read at the moment's wall time in the configured zone, and the moments a surface
asks about are that zone's.

Honolulu is the configured zone here because it keeps one offset all year (UTC-10), so the
instants below are the same wall times on every date the suite runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personalclaw import timezones
from personalclaw.providers import entity_routes

_QUIET = {"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}
_HST = timezone(timedelta(hours=-10))


@pytest.fixture(autouse=True)
def honolulu(monkeypatch):
    monkeypatch.setattr(timezones, "_config_zone_name", lambda: "Pacific/Honolulu")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 4, hour, minute, tzinfo=timezone.utc)


def test_a_moment_late_in_the_owners_evening_is_quiet():
    # 09:00 UTC is 23:00 in Honolulu, inside its 22:00 to 07:00 window; on the process's clock it
    # read as 09:00, outside it, and the notice pinged at the owner's 23:00.
    assert entity_routes.quiet_hours_now(now=_utc(9), settings=_QUIET) is True


def test_a_moment_in_the_owners_afternoon_is_not_quiet():
    # 01:00 UTC is 15:00 in Honolulu; read as 01:00, it was held as if it were the dead of night.
    assert entity_routes.quiet_hours_now(now=_utc(1), settings=_QUIET) is False


def test_the_window_ends_at_its_own_end_in_the_configured_zone():
    """The window is [start, end): 06:59 is the last quiet minute and 07:00 the first loud one."""
    assert entity_routes.quiet_hours_now(now=_utc(16, 59), settings=_QUIET) is True
    assert entity_routes.quiet_hours_now(now=_utc(17, 0), settings=_QUIET) is False


def test_the_moments_a_surface_asks_about_are_the_configured_zones():
    moments = entity_routes.quiet_window_moments(_QUIET)

    assert moments is not None
    inside, outside = moments
    for moment, hour in ((inside, 22), (outside, 7)):
        assert moment.tzinfo is not None, "a moment on the process's own clock"
        assert moment.utcoffset() == _HST.utcoffset(None)
        assert (moment.hour, moment.minute) == (hour, 0)
    assert entity_routes.quiet_hours_now(now=inside, settings=_QUIET) is True
    assert entity_routes.quiet_hours_now(now=outside, settings=_QUIET) is False


def test_with_no_timezone_configured_the_machines_clock_still_decides(monkeypatch):
    """The control: no configured zone falls back as every fire path does, to the machine's."""
    monkeypatch.setattr(timezones, "_config_zone_name", lambda: "")
    local = datetime.now().astimezone()
    wanted = entity_routes._in_quiet_window("22:00", "07:00", local.hour * 60 + local.minute)

    assert entity_routes.quiet_hours_now(now=local, settings=_QUIET) is wanted


def test_settings_names_the_zone_the_window_is_read_in():
    """Settings named the window "server time", which it no longer is: the read names the zone."""
    import asyncio
    import json

    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request("GET", "/api/notifications/settings")
    body = json.loads(asyncio.run(entity_routes.handle_notifications_settings_get(req)).body)

    assert body["quiet_hours_zone"] == "Pacific/Honolulu"
    assert "quiet_hours_zone" not in body["settings"], "a read-only fact, never a saved setting"


def test_a_zone_with_daylight_saving_reads_the_window_at_each_dates_offset(monkeypatch):
    """New York is UTC-4 in July and UTC-5 in January: 22:30 there is quiet on both dates."""
    monkeypatch.setattr(timezones, "_config_zone_name", lambda: "America/New_York")
    summer = datetime(2026, 7, 15, 2, 30, tzinfo=timezone.utc)  # 22:30 EDT
    winter = datetime(2026, 1, 15, 3, 30, tzinfo=timezone.utc)  # 22:30 EST
    for moment in (summer, winter):
        assert entity_routes.quiet_hours_now(now=moment, settings=_QUIET) is True
    # And an hour before each, 21:30, is not.
    for moment in (summer - timedelta(hours=1), winter - timedelta(hours=1)):
        assert entity_routes.quiet_hours_now(now=moment, settings=_QUIET) is False


def test_the_buddy_says_the_window_changes_at_the_configured_zones_edges(monkeypatch):
    """The buddy's `changes_at` read the edges on the process's clock, so it said quiet hours
    ended at 07:00 UTC while `quiet_now` read them in the configured zone."""
    import types

    from personalclaw import buddy_policy
    from personalclaw.config import loader

    monkeypatch.setattr(entity_routes, "load_notifications_settings", lambda: dict(_QUIET))
    buddy = types.SimpleNamespace(
        show=True, window=True, tray=True, dock=True, notifications=True, away_after_secs=300
    )
    monkeypatch.setattr(
        loader.AppConfig, "load", classmethod(lambda cls: types.SimpleNamespace(buddy=buddy))
    )

    said = buddy_policy.policy(now=_utc(9))["notifications"]  # 23:00 in Honolulu: quiet

    assert said["quiet_now"] is True
    changes = datetime.fromisoformat(said["changes_at"])
    assert changes.astimezone(timezone.utc) == _utc(17), "07:00 in Honolulu, when the window ends"
