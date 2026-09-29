"""A schedule read in a named time zone keeps its wall-clock time across a change of the clocks.

Before, in America/Toronto on 29 September (EDT): a trigger with cron
``10 15 1 1 *`` armed to ``2027-01-01T21:10Z`` and the Triggers page said "At 4:10 PM EST … Jan 1,
04:10 p.m.". 15:10 in Toronto on 1 January is 20:10Z, 3:10 PM EST. croniter kept the offset of the
moment it stepped from (EDT) for a fire after the clocks go back, so every fire across a change
landed an hour off — late after the autumn change, early after the spring one — and every surface
that stepped a cron said so.

Every case here is a real zone across a real change, from a fixed clock.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from personalclaw import schedule as schedule_mod
from personalclaw.triggers import arm
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import describe_cadence

TORONTO = ZoneInfo("America/Toronto")
LONDON = ZoneInfo("Europe/London")
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")  # its clocks move by thirty minutes

#: 29 September 2026, 06:26 in Toronto (EDT): the moment the schedule is read.
SEPT_29 = datetime(2026, 9, 29, 6, 26, tzinfo=TORONTO).timestamp()


def next_fire(expr: str, now: float, tz: ZoneInfo) -> float:
    from personalclaw.cron_clock import next_fire as step

    return step(expr, now, tz)


def previous_fire(expr: str, now: float, tz: ZoneInfo) -> float:
    from personalclaw.cron_clock import previous_fire as step

    return step(expr, now, tz)


def _z(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


@pytest.mark.parametrize(
    ("expr", "tz", "now", "fires"),
    [
        # Toronto, from summer time to a fire after the clocks go back (1 November 2026).
        ("10 15 1 1 *", TORONTO, SEPT_29, "2027-01-01T20:10Z"),
        ("0 9 1 11 *", TORONTO, SEPT_29, "2026-11-01T14:00Z"),
        # Toronto, from winter time to a fire after they go forward (14 March 2027).
        (
            "0 9 15 3 *",
            TORONTO,
            datetime(2027, 2, 1, tzinfo=TORONTO).timestamp(),
            "2027-03-15T13:00Z",
        ),
        # London, from BST to GMT (25 October 2026).
        (
            "0 8 1 11 *",
            LONDON,
            datetime(2026, 10, 1, tzinfo=LONDON).timestamp(),
            "2026-11-01T08:00Z",
        ),
    ],
)
def test_a_fire_after_the_clocks_change_keeps_its_wall_time(expr, tz, now, fires):
    """🔴 Before: 2027-01-01T21:10Z and 2026-11-01T15:00Z (an hour late), 2027-03-15T12:00Z (an
    hour early), 2026-11-01T09:00Z (an hour late)."""
    assert _z(next_fire(expr, now, tz)) == fires


def test_a_time_the_clocks_show_twice_fires_once():
    """01:30 on 1 November 2026 happens twice in Toronto; a daily 01:30 job runs at the first."""
    before = datetime(2026, 11, 1, 0, 50, tzinfo=TORONTO).timestamp()
    first = next_fire("30 1 * * *", before, TORONTO)
    assert _z(first) == "2026-11-01T05:30Z"  # 01:30 EDT
    assert _z(next_fire("30 1 * * *", first + 1, TORONTO)) == "2026-11-02T06:30Z"
    # Seen from inside the repeated hour (01:10 EST), 01:30 has already run: not again.
    inside = datetime(2026, 11, 1, 1, 10, tzinfo=TORONTO, fold=1).timestamp()
    assert _z(next_fire("30 1 * * *", inside, TORONTO)) == "2026-11-02T06:30Z"


@pytest.mark.parametrize(
    ("expr", "tz", "now", "fires"),
    [
        # 02:30 never shows in Toronto on 14 March 2027: it runs at 03:00, when the clocks jump.
        (
            "30 2 * * *",
            TORONTO,
            datetime(2027, 3, 14, 1, 50, tzinfo=TORONTO).timestamp(),
            "2027-03-14T07:00Z",
        ),
        # Lord Howe Island goes from 02:00 to 02:30 on 4 October 2026.
        (
            "15 2 4 10 *",
            LORD_HOWE,
            datetime(2026, 9, 30, tzinfo=LORD_HOWE).timestamp(),
            "2026-10-03T15:30Z",
        ),
    ],
)
def test_a_time_the_clocks_skip_fires_as_they_jump(expr, tz, now, fires):
    """🔴 Before: 2027-03-14T07:30Z and 2026-10-03T15:45Z, as long after the jump as the time was
    after its start."""
    assert _z(next_fire(expr, now, tz)) == fires


def test_a_repeating_schedule_resumes_after_the_jump():
    start = datetime(2027, 3, 14, 1, 50, tzinfo=TORONTO).timestamp()
    jump = next_fire("*/15 * * * *", start, TORONTO)
    assert _z(jump) == "2027-03-14T07:00Z"  # 03:00 EDT: the skipped 02:xx slots run once
    assert _z(next_fire("*/15 * * * *", jump + 1, TORONTO)) == "2027-03-14T07:15Z"


def test_the_last_fire_is_counted_on_the_same_clock():
    """A scheduled report's dueness counts back to its last boundary with the same placement.

    🔴 Before: 2026-10-01T12:00Z for the first, an hour before the report's 09:00 EDT."""
    nov_2 = datetime(2026, 11, 2, tzinfo=TORONTO).timestamp()
    assert _z(previous_fire("0 9 1 10 *", nov_2, TORONTO)) == "2026-10-01T13:00Z"
    after_jump = datetime(2027, 3, 14, 3, 10, tzinfo=TORONTO).timestamp()
    assert _z(previous_fire("30 2 * * *", after_jump, TORONTO)) == "2027-03-14T07:00Z"


# ── the product surfaces that step a cron ────────────────────────────────────────────────────


def _yearly() -> Trigger:
    return Trigger(
        id="clock:new-year-plan",
        name="New year plan",
        kind="clock",
        enabled=True,
        spec={"kind": "cron", "expr": "10 15 1 1 *", "timezone": "America/Toronto"},
        workflow={
            "inline": {"provider": "send-message", "config": {"text_template": "Plan the year."}}
        },
    )


def test_the_trigger_arms_to_its_wall_time():
    """🔴 Before: 2027-01-01T21:10Z."""
    assert _z(arm.cadence_next_fire(_yearly(), now=SEPT_29)) == "2027-01-01T20:10Z"


def test_the_triggers_page_names_the_hour_it_runs(monkeypatch):
    """🔴 Before: "At 4:10 PM EST, on day 1 of the month, only in January"."""
    monkeypatch.setattr(schedule_mod, "time", SimpleNamespace(time=lambda: SEPT_29))
    assert describe_cadence(_yearly()).startswith("At 3:10 PM EST")


def test_a_skip_date_across_a_change_is_read_on_that_day():
    """🔴 Before: False — 23:30 on 1 November, stepped from the EDT midnight before it, read as
    00:30 on the 2nd, so a skip date on the 1st was reported as a day the schedule never fires."""
    assert arm._cron_fires_on_date("30 23 * * *", date(2026, 11, 1), "America/Toronto")


# ── one owner ────────────────────────────────────────────────────────────────────────────────

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _constructions(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "croniter"
    ]


def test_a_cron_is_stepped_in_one_place():
    """Stepping one anywhere else hands croniter an aware start again. `is_valid` and `match`
    (attribute calls) are not steps, and stay where they are."""
    elsewhere = {
        path.relative_to(SRC).as_posix(): lines
        for path in sorted(SRC.rglob("*.py"))
        if path.name != "cron_clock.py" and (lines := _constructions(path))
    }
    assert elsewhere == {}
    assert _constructions(SRC / "cron_clock.py"), "the scan found the one place it steps"
