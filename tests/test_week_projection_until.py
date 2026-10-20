"""The week projection honors an exact `until` bound (issue 608).

The week grid draws 7 LOCAL calendar days — 167h or 169h across a DST transition —
while `days=7` arithmetic is fixed wall-clock fields. These rails pin the seam that
lets the two agree: `project_occurrences(until=...)` bounds by the caller's window,
and `/api/triggers/week?until=` threads it through with fail-closed validation.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as trigger_handlers
from personalclaw.triggers.calendar import project_occurrences


def _project(start: datetime, **kw):
    rows, _cut = project_occurrences(
        trigger_id="schedule:t1",
        trigger_name="t1",
        interval_secs=3600.0,
        first_fire_at=start.timestamp(),
        start=start,
        **kw,
    )
    return rows


def _week_request(query: str):
    app = web.Application()
    app["state"] = object()
    return make_mocked_request("GET", f"/api/triggers/week?{query}", app=app)


def _body(response) -> dict:
    return json.loads(response.body.decode())


@contextmanager
def _process_timezone(name: str):
    """Temporarily pin naive ``datetime.timestamp()`` to a deterministic gateway zone."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def test_until_is_the_bound_when_given():
    start = datetime(2026, 8, 3)
    until = start + timedelta(hours=5)
    rows = _project(start, days=7, until=until)
    assert len(rows) == 5  # hourly fires in [start, until), not a week's worth
    assert all(start.timestamp() <= r.at < until.timestamp() for r in rows)


def test_days_stays_the_fallback_without_until():
    start = datetime(2026, 8, 3)
    rows = _project(start, days=1)
    assert len(rows) == 24


def test_a_shorter_until_beats_a_wider_days():
    """The parameter exists to NAME the window, so it must win over the approximation."""
    start = datetime(2026, 8, 3)
    narrow = _project(start, days=7, until=start + timedelta(hours=2))
    assert len(narrow) == 2


def test_endpoint_validates_until_fail_closed():
    """Malformed / inverted / over-cap `until` values are 400s, not silent fallbacks."""
    import inspect

    from personalclaw.dashboard.handlers import triggers as mod

    src = inspect.getsource(mod.api_triggers_week)
    assert 'request.query.get("until")' in src
    assert "until must be an ISO date" in src
    assert "until must be after start and within 31 days" in src
    # And the real bound is echoed back, so the client can verify the agreed window.
    assert "(until or (start + timedelta(days=days))).isoformat()" in src


def test_browser_week_is_not_cut_in_the_gateway_process_zone(monkeypatch):
    """A late-Sunday fire stays inside the browser's week when the gateway runs in UTC."""
    from personalclaw import schedule

    browser_zone = ZoneInfo("America/Los_Angeles")
    monkeypatch.setattr(
        schedule,
        "get_local_tz",
        lambda: ("America/Los_Angeles", browser_zone),
    )
    monkeypatch.setattr(trigger_handlers, "_week_triggers", lambda _state: [object()])

    fire_at = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc).timestamp()
    seen: dict[str, datetime] = {}

    def project_one(_trigger, *, start, days, until):
        seen["start"] = start
        seen["until"] = until
        return project_occurrences(
            trigger_id="schedule:late-sunday",
            trigger_name="late Sunday",
            interval_secs=40 * 86400,
            first_fire_at=fire_at,
            start=start,
            days=days,
            until=until,
            tz_name="America/Los_Angeles",
        )

    monkeypatch.setattr(trigger_handlers, "_project_one", project_one)
    request = _week_request("start=2026-09-21T00:00:00&days=7&until=2026-09-28T00:00:00")

    with _process_timezone("UTC"):
        response = asyncio.run(trigger_handlers.api_triggers_week(request))

    assert response.status == 200
    assert len(_body(response)["occurrences"]) == 1
    assert seen["start"].tzinfo is timezone.utc
    assert seen["until"].tzinfo is timezone.utc


def test_mixed_awareness_inverted_bounds_return_400_after_normalization(monkeypatch):
    """Equal instants with mixed awareness are a 400, never a comparison TypeError."""
    from personalclaw import schedule

    server_zone = ZoneInfo("America/Los_Angeles")
    monkeypatch.setattr(
        schedule,
        "get_local_tz",
        lambda: ("America/Los_Angeles", server_zone),
    )
    monkeypatch.setattr(trigger_handlers, "_week_triggers", lambda _state: [])

    response = asyncio.run(
        trigger_handlers.api_triggers_week(
            _week_request("start=2026-09-21T00:00:00-07:00" "&days=7&until=2026-09-21T00:00:00")
        )
    )

    assert response.status == 400
    assert _body(response) == {"error": "until must be after start and within 31 days"}
