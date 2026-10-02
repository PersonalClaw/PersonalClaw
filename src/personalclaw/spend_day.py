"""The calendar day spend is counted in: the day in her timezone.

One rule for every surface that says "today", "this week" or "this month" about spend: the Usage
page's totals, tables, per-day fold and chart, the Settings home's tile, the daily caps
(``guardrails.budgets``, which start afresh at her midnight), the monthly usage recap, and the day
a model's price was set. Her timezone is the one her schedules run in, from the one place a zone is
resolved (:func:`personalclaw.timezones.resolve_zone`): ``config.timezone`` when it is set
(``personalclaw setup``), else this machine's zone, else UTC.

Spend counted the gateway process's own clock while her schedules ran in her timezone, so a gateway
whose clock was in another zone (a container on UTC, a machine set to another city) put a turn at
20:30 in Toronto, 00:30 UTC, on the next day's Usage and cap, and reset her caps at 20:00.

The usage ledger stamps each row in UTC (``usage_ledger.TurnUsage.ts``); a row's day is the date
that instant falls on in her timezone, and a window of days starts at her midnight of its first
day, written as the ledger writes its stamps so the two compare as text.

The zone is resolved each time a day is asked for and never cached, as ``timezones`` resolves it,
so a corrected ``config.timezone`` counts from the next read. A caller that dates many rows
resolves it once (:func:`zone`) and passes it to each call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from personalclaw.timezones import resolve_zone

#: How a day is spelled everywhere spend is keyed by one.
DAY_FORMAT = "%Y-%m-%d"


def zone() -> tzinfo:
    """Her timezone: the zone her spend days start and end in."""
    return resolve_zone()


def _zone(tz: tzinfo | None) -> tzinfo:
    return zone() if tz is None else tz


def today(tz: tzinfo | None = None) -> str:
    """Today in her timezone (``YYYY-MM-DD``): the day the daily caps are counting."""
    return datetime.now(_zone(tz)).strftime(DAY_FORMAT)


def next_day_starts(tz: tzinfo | None = None) -> float:
    """When the day the daily caps count ends, as epoch seconds: her next midnight, the moment the
    caps start afresh."""
    tz = _zone(tz)
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz).timestamp()


def day_of(ts: Any, tz: tzinfo | None = None) -> str:
    """Her day an ISO timestamp falls on, or ``""`` when it names no instant.

    A stamp with no offset is read as UTC, the zone the ledger writes in.
    """
    try:
        at = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return ""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(_zone(tz)).strftime(DAY_FORMAT)


def day_of_epoch(ts: Any, tz: tzinfo | None = None) -> str:
    """Her day a POSIX timestamp falls on, or ``""`` when it is not one."""
    try:
        return datetime.fromtimestamp(float(ts), _zone(tz)).strftime(DAY_FORMAT)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def days_ending(day: str, count: int) -> list[str]:
    """The *count* days that end with *day*, oldest first."""
    end = datetime.strptime(day, DAY_FORMAT)
    return [(end - timedelta(days=n)).strftime(DAY_FORMAT) for n in range(count - 1, -1, -1)]


def start_of(day: str, tz: tzinfo | None = None) -> str:
    """The instant of her midnight that starts *day*, spelled as the ledger spells its stamps."""
    date = datetime.strptime(day, DAY_FORMAT).date()
    midnight = datetime.combine(date, datetime.min.time(), tzinfo=_zone(tz))
    return midnight.astimezone(timezone.utc).isoformat()
