"""A cron schedule's human label must name the zone it is stated in (#445).

`_humanize_cron` gated its whole timezone-aware branch on ``tz_name`` being TRUTHY. A trigger
with no explicit ``timezone`` — the default, and what every stock schedule carries — therefore
fell through to `cron_descriptor`'s raw output: a bare ``At 09:00 AM``, naming no zone and
converted to nothing.

Meanwhile the FIRE was armed through ``_job_tz`` -> ``resolve_zone("")``, which walks
explicit -> config -> machine -> UTC. So one row carried a label in cron text and a
``next_fire_at`` that is a real instant in the resolved zone, and the frontend localizes that
instant for the viewer. Measured on a ``server_tz: UTC`` host read from Pacific:

    list label   'At 09:00 AM'
    next fire    'Sep 19, 02:00 AM'

The same schedule, in one row, seven hours apart, with nothing on screen to explain it.

The fix drops the gate so the zone is always resolved — the same thing `format_schedule`'s
``at`` branch already did unconditionally, and the same one-owner direction #2520 established.
This rail pins the OBSERVABLE consequence (the label names a zone) rather than the branch
condition, so a future refactor is free to reach it differently.
"""

from __future__ import annotations

import re

import pytest

from personalclaw.schedule import ScheduleDefinition, format_schedule

# The zone token that must FOLLOW the meridiem: `At 9:00 AM PDT` / `... AM UTC` / `... AM GMT+5`.
#
# 🪤 Anchored after AM/PM deliberately. The first draft searched the whole label for
# `\b[A-Z]{2,5}\b` and passed on the UNFIXED string `At 09:00 AM` — because **"AM" is itself two
# uppercase letters**. The rail that existed to catch a missing zone was satisfied by the
# meridiem, so 11 of its 12 assertions were green against the very code they were written to
# reject. Requiring a token after the meridiem is what makes it measure the zone.
_ZONE_AFTER_MERIDIEM = re.compile(r"\b(?:AM|PM)\s+([A-Z]{2,5}|GMT[+-]\d+|[+-]\d{2}:?\d{2})\b")


def _cron(expr: str) -> ScheduleDefinition:
    return ScheduleDefinition(kind="cron", cron_expr=expr)


@pytest.mark.parametrize("expr", ["0 9 * * *", "0 8 * * *", "30 6 * * 1", "0 9 1 * *"])
def test_label_names_a_zone_with_no_explicit_timezone(expr: str) -> None:
    """The regression itself: no explicit zone must NOT mean no zone disclosed."""
    label = format_schedule(_cron(expr), tz_name="")

    assert label.startswith("At "), f"still the humanized cron prose, not an expr dump: {label!r}"
    assert _ZONE_AFTER_MERIDIEM.search(label), (
        f"the label must name the zone it is stated in, got {label!r} — this is the #445 "
        "contradiction: a bare time reads as the VIEWER's local while the fire is armed in the "
        "resolved server zone"
    )


@pytest.mark.parametrize(
    "tz_name,expected",
    # The zone token each declared zone must resolve to, matched EXACTLY against the captured
    # group rather than by substring: `"E" in label` (the first draft) is true of almost any
    # sentence, so it would have accepted a label that named the wrong zone entirely.
    [("UTC", {"UTC"}), ("America/New_York", {"EDT", "EST"}), ("Asia/Tokyo", {"JST"})],
)
def test_an_explicit_zone_is_still_honoured(tz_name: str, expected: set[str]) -> None:
    """The vacuity floor. "Always resolve" must not mean "ignore what the trigger declared"."""
    label = format_schedule(_cron("0 9 * * *"), tz_name=tz_name)
    m = _ZONE_AFTER_MERIDIEM.search(label)
    assert m, f"{tz_name} produced no zone token at all: {label!r}"
    assert m.group(1) in expected, f"{tz_name} must reach the label, got {label!r}"
    assert "9:00 AM" in label, f"and the hour is the cron's own, got {label!r}"


def test_an_unusable_zone_never_breaks_a_list_render() -> None:
    """`describe_cadence` wraps this, but the fallback belongs here too: a typo'd zone on one
    trigger must degrade to prose, not raise into a whole list's render."""
    label = format_schedule(_cron("0 9 * * *"), tz_name="Not/AZone")
    assert "9:00 AM" in label or "09:00 AM" in label, label


@pytest.mark.parametrize("expr", ["*/5 * * * *", "0 */2 * * *", "* * * * *"])
def test_non_clock_cadences_are_left_alone(expr: str) -> None:
    """Only a fixed minute+hour cron states a time of day. "Every 5 minutes" has no hour to
    localize, so it must not acquire a spurious zone suffix."""
    label = format_schedule(_cron(expr), tz_name="")
    assert label.startswith("Every"), label
    assert "At " not in label, label


def test_the_label_and_the_armed_fire_agree_on_the_hour() -> None:
    """The actual user-facing invariant, asserted end to end rather than by inspection: the hour
    the label prints is the hour the scheduler will fire in that same zone."""
    from datetime import datetime

    from personalclaw.schedule import ScheduleJob, compute_next_run_ts
    from personalclaw.timezones import resolve_zone

    job = ScheduleJob(id="x", name="x", schedule=_cron("0 9 * * *"), timezone="")
    ts = compute_next_run_ts(job)
    assert ts is not None

    label = format_schedule(_cron("0 9 * * *"), tz_name="")
    fired_hour = datetime.fromtimestamp(ts, resolve_zone("")).strftime("%-I")
    assert f"At {fired_hour}:00" in label, (
        f"label {label!r} must name the hour the fire is armed for ({fired_hour}:00) in the zone "
        "resolve_zone answers with — that agreement is what #445 lacked"
    )
