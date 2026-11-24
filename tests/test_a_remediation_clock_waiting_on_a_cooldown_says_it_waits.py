"""A self-remediation clock that waits on a fix's cooldown says it waits, not that all is well.

A pass whose every planned job is in its cooldown runs nothing and looks again when the soonest is
due (`test_self_remediation_waits_for_a_fix_in_its_cooldown`). 🔴 Before, while it waited, the
clock wrote ``health_state: healthy``, so its schedule read "now: every 60m", the cadence of a home
with nothing to repair, while the score sat under its target and a fix was only waiting for its
cooldown. Now its state is ``waiting``, and its schedule says it waits for a fix in its cooldown.
A home with nothing left to repair still reads healthy, and one that is repairing, degraded.

Driven through the real engine and the real provider re-arming the real trigger store, in a home of
the test's own, and read back through the schedule's own sentence (`schedule_view`).
"""

from __future__ import annotations

from test_self_remediation_waits_for_a_fix_in_its_cooldown import (  # noqa: F401 - fixtures
    MIN,
    _a_deficit_fixed_by,
    _fire,
    _only_these_jobs,
    home,
    store,
)

from personalclaw.triggers.schedule_view import describe_cadence

WAITS = "now: waiting for a fix in its cooldown"


def test_a_clock_that_waits_on_a_cooldown_says_it_waits(store, monkeypatch):  # noqa: F811
    """🔴 Before: ``healthy``, and "now: every 60m"."""
    deficits = [_a_deficit_fixed_by("orphan_locks", cooldown_hours=0.25, ran_ago=1 * MIN)]
    trigger, wait, _said = _fire(store, monkeypatch, deficits)
    assert trigger.spec["health_state"] == "waiting"
    said = describe_cadence(trigger)
    assert WAITS in said, said
    assert "now: every" not in said, said
    # The wait itself is unchanged: it looks again when the fix is due.
    assert 13 * MIN < wait <= 14 * MIN + 5, wait


def test_a_clock_with_nothing_to_repair_still_reads_healthy(store, monkeypatch):  # noqa: F811
    """The control: no deficit, no wait, the long cadence."""
    trigger, _wait, _said = _fire(store, monkeypatch, [])
    assert trigger.spec["health_state"] == "healthy"
    assert "now: every 60m" in describe_cadence(trigger)


def test_a_clock_that_is_repairing_still_reads_degraded(store, monkeypatch):  # noqa: F811
    deficits = [
        _a_deficit_fixed_by("orphan_locks", cooldown_hours=0.0, ran_ago=0),
        _a_deficit_fixed_by("fts_rebuild", cooldown_hours=6.0, ran_ago=60 * MIN),
    ]
    trigger, _wait, _said = _fire(store, monkeypatch, deficits)
    assert trigger.spec["health_state"] == "degraded"
    assert "now: every 5m" in describe_cadence(trigger)
