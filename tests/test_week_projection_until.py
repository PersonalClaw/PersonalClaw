"""The week projection honors an exact `until` bound (issue 608).

The week grid draws 7 LOCAL calendar days — 167h or 169h across a DST transition —
while `days=7` arithmetic is fixed wall-clock fields. These rails pin the seam that
lets the two agree: `project_occurrences(until=...)` bounds by the caller's window,
and `/api/triggers/week?until=` threads it through with fail-closed validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
