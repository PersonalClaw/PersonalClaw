"""An explicit one-time ``when`` is read to its instant with no model, in the owner's zone.

"remind me at 5 pm", "in 20 minutes", "tomorrow at 9am", "2026-10-01 14:00" name one instant, and
reading them needs a clock and a zone, not a model. Only a phrase that leaves the time to judgement
("tomorrow morning", "later today", "this weekend") goes to the model, and a repeating one
("every weekday at 9") is never read as one time.

Every case here is read at the same instant — Sunday 27 September 2026, 14:03 in Los Angeles — so
each expectation is a wall-clock time a person can check.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from personalclaw.triggers import when as W

ZONE = "America/Los_Angeles"
LA = ZoneInfo(ZONE)
NOW = datetime(2026, 9, 27, 14, 3, tzinfo=LA)


def _at(*parts: int, tz: ZoneInfo = LA) -> datetime:
    return datetime(*parts, tzinfo=tz)


def _read(text: str) -> datetime | None:
    found = W.read_one_time(text, now=NOW.timestamp(), zone=ZONE)
    return None if found is None else datetime.fromtimestamp(found.at, LA)


@pytest.mark.parametrize(
    "text,expected",
    [
        # a clock time today, or the next one
        ("at 5pm", _at(2026, 9, 27, 17, 0)),
        ("5 pm", _at(2026, 9, 27, 17, 0)),
        ("at 5:30 PM", _at(2026, 9, 27, 17, 30)),
        ("17:00", _at(2026, 9, 27, 17, 0)),
        ("at 17:45", _at(2026, 9, 27, 17, 45)),
        ("at 9", _at(2026, 9, 27, 21, 0)),  # the next 9 o'clock: tonight's
        ("at 09:00", _at(2026, 9, 28, 9, 0)),  # zero-padded: a 24-hour time
        ("at 10 in the morning", _at(2026, 9, 28, 10, 0)),
        ("at 10 in the evening", _at(2026, 9, 27, 22, 0)),
        ("at noon", _at(2026, 9, 28, 12, 0)),  # today's has passed
        ("at midnight", _at(2026, 9, 28, 0, 0)),
        ("at 12am", _at(2026, 9, 28, 0, 0)),
        ("at 12pm", _at(2026, 9, 28, 12, 0)),
        ("today at 6pm", _at(2026, 9, 27, 18, 0)),
        ("tonight at 8", _at(2026, 9, 27, 20, 0)),
        ("at 8 tonight", _at(2026, 9, 27, 20, 0)),
        ("this evening at 7", _at(2026, 9, 27, 19, 0)),
        # another day
        ("tomorrow at 9am", _at(2026, 9, 28, 9, 0)),
        ("tomorrow at 9", _at(2026, 9, 28, 9, 0)),
        ("tomorrow at 3", _at(2026, 9, 28, 15, 0)),  # a day named: 1-6 is the afternoon
        ("noon tomorrow", _at(2026, 9, 28, 12, 0)),
        ("9am tomorrow", _at(2026, 9, 28, 9, 0)),
        ("tomorrow morning at 7:30", _at(2026, 9, 28, 7, 30)),
        ("tomorrow evening at 7", _at(2026, 9, 28, 19, 0)),
        ("on monday at 9am", _at(2026, 9, 28, 9, 0)),
        ("monday 9am", _at(2026, 9, 28, 9, 0)),
        ("next friday at 5pm", _at(2026, 10, 2, 17, 0)),
        ("sunday at 5pm", _at(2026, 9, 27, 17, 0)),  # today is Sunday, and 5pm is ahead
        ("sunday at 1pm", _at(2026, 10, 4, 13, 0)),  # today's has passed
        # a date
        ("2026-10-01 14:00", _at(2026, 10, 1, 14, 0)),
        ("2026-10-01T14:00", _at(2026, 10, 1, 14, 0)),
        ("2026-10-01 at 2pm", _at(2026, 10, 1, 14, 0)),
        ("on 2026-10-01 at 2pm", _at(2026, 10, 1, 14, 0)),
        ("oct 1 at 2pm", _at(2026, 10, 1, 14, 0)),
        ("October 1st at 2pm", _at(2026, 10, 1, 14, 0)),
        ("1 october 2026 14:00", _at(2026, 10, 1, 14, 0)),
        ("sep 1 at 9am", _at(2027, 9, 1, 9, 0)),  # this year's has passed
        # a delay
        ("in 20 minutes", _at(2026, 9, 27, 14, 23)),
        ("in 20 min", _at(2026, 9, 27, 14, 23)),
        ("in 20m", _at(2026, 9, 27, 14, 23)),
        ("in 1 hour", _at(2026, 9, 27, 15, 3)),
        ("in an hour", _at(2026, 9, 27, 15, 3)),
        ("in a minute", _at(2026, 9, 27, 14, 4)),
        ("in 1h30m", _at(2026, 9, 27, 15, 33)),
        ("in 1 hour and 30 minutes", _at(2026, 9, 27, 15, 33)),
        ("in an hour and a half", _at(2026, 9, 27, 15, 33)),
        ("in half an hour", _at(2026, 9, 27, 14, 33)),
        ("in 1.5 hours", _at(2026, 9, 27, 15, 33)),
        ("in 90 seconds", _at(2026, 9, 27, 14, 4, 30)),
        ("in 3 days", _at(2026, 9, 30, 14, 3)),
        ("in 2 weeks", _at(2026, 10, 11, 14, 3)),
        ("20 minutes from now", _at(2026, 9, 27, 14, 23)),
        ("remind me in 5 minutes", _at(2026, 9, 27, 14, 8)),
    ],
)
def test_an_explicit_time_is_read_to_its_instant(text, expected):
    assert _read(text) == expected, text


@pytest.mark.parametrize(
    "text,expected",
    [
        # 17:00 UTC today was 10:00 here, so it is tomorrow's
        ("at 5pm UTC", _at(2026, 9, 28, 17, 0, tz=ZoneInfo("UTC"))),
        ("at 9am Europe/London", _at(2026, 9, 28, 9, 0, tz=ZoneInfo("Europe/London"))),
        ("2026-10-01T14:00:00Z", _at(2026, 10, 1, 14, 0, tz=ZoneInfo("UTC"))),
        ("2026-10-01T14:00-04:00", _at(2026, 10, 1, 18, 0, tz=ZoneInfo("UTC"))),
    ],
)
def test_a_time_that_names_its_zone_is_read_in_that_zone(text, expected):
    found = W.read_one_time(text, now=NOW.timestamp(), zone=ZONE)
    assert found is not None, text
    assert found.at == expected.timestamp(), (text, datetime.fromtimestamp(found.at, LA))


def test_the_zone_is_the_owners_not_the_machines():
    """The same words are a different instant in another zone: the reader never uses the
    machine's own clock zone when it is handed one."""
    here = W.read_one_time("at 5pm", now=NOW.timestamp(), zone=ZONE)
    kolkata = W.read_one_time("at 5pm", now=NOW.timestamp(), zone="Asia/Kolkata")
    assert here is not None and kolkata is not None
    assert datetime.fromtimestamp(kolkata.at, ZoneInfo("Asia/Kolkata")).hour == 17
    assert here.at != kolkata.at
    assert here.zone == ZONE and kolkata.zone == "Asia/Kolkata"


@pytest.mark.parametrize(
    "text",
    [
        "tomorrow",
        "tomorrow morning",
        "later today",
        "tonight",
        "in a bit",
        "in a few minutes",
        "end of day",
        "this weekend",
        "next week",
        "at 5pm PT",  # an abbreviation names no zone for certain
        "at 25:00",
        "at 5:75",
        "in 0 minutes",
        "banana",
        "",
    ],
)
def test_a_phrase_that_leaves_the_time_to_judgement_is_not_read(text):
    """Left to the model, which is handed the clock and the zone. A guess here would be a
    reminder at a time nobody said."""
    assert W.read_one_time(text, now=NOW.timestamp(), zone=ZONE) is None


def test_an_explicit_time_that_has_passed_is_read_as_passed():
    """A named date in the past is read as it was said, so the caller can say it has passed
    instead of scheduling something else."""
    found = W.read_one_time("2026-09-01 09:00", now=NOW.timestamp(), zone=ZONE)
    assert found is not None and found.at < NOW.timestamp()


@pytest.mark.parametrize(
    "text",
    [
        "every day at 9",
        "daily at 7am",
        "every weekday at 9",
        "weekdays at 9am",
        "on mondays at 9",
        "hourly",
        "every 20 minutes",
        "twice a day",
        "each morning",
        "nightly",
        "0 9 * * 1-5",
    ],
)
def test_a_repeating_phrase_is_recurring(text):
    assert W.is_recurring(text), text
    assert W.read_one_time(text, now=NOW.timestamp(), zone=ZONE) is None, text


@pytest.mark.parametrize(
    "text", ["at 5pm", "tomorrow at 9", "this weekend", "next monday", "in 20 minutes", "tonight"]
)
def test_a_one_time_phrase_is_not_recurring(text):
    assert not W.is_recurring(text), text


def test_the_reading_is_described_as_a_person_says_it():
    """What the chat and the Triggers page echo back, so a misread is caught while it is fresh."""
    found = W.read_one_time("tomorrow at 9am", now=NOW.timestamp(), zone=ZONE)
    assert found is not None
    assert found.describe(now=NOW.timestamp()) == "Mon Sep 28, 9:00 AM PDT"
    today = W.read_one_time("at 5pm", now=NOW.timestamp(), zone=ZONE)
    assert today is not None
    assert today.describe(now=NOW.timestamp()) == "today, 5:00 PM PDT"
