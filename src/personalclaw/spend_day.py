"""The calendar day spend is counted in: this machine's local day.

The daily cap resets at this machine's local midnight (``guardrails.budgets``), so every surface
that says "today" about spend counts that same day: the Usage page's totals, its per-day fold and
chart, and the Settings home's tile. The page counted the UTC day while the cap beside it counted
the local one, so for the evening hours west of Greenwich one page said a turn cost money today and
that nothing had been spent today.

The usage ledger stamps each row in UTC (``usage_ledger.TurnUsage.ts``); a row's day is the local
date that instant falls on, and a window of days starts at the local midnight of its first day,
written as the ledger writes its stamps so the two compare as text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

#: How a day is spelled everywhere spend is keyed by one.
DAY_FORMAT = "%Y-%m-%d"


def today() -> str:
    """Today on this machine's clock (``YYYY-MM-DD``): the day the daily cap is counting."""
    return datetime.now().strftime(DAY_FORMAT)


def day_of(ts: Any) -> str:
    """The local day an ISO timestamp falls on, or ``""`` when it names no instant.

    A stamp with no offset is read as UTC, the zone the ledger writes in.
    """
    try:
        at = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return ""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone().strftime(DAY_FORMAT)


def day_of_epoch(ts: Any) -> str:
    """The local day a POSIX timestamp falls on, or ``""`` when it is not one."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime(DAY_FORMAT)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def days_ending(day: str, count: int) -> list[str]:
    """The *count* days that end with *day*, oldest first."""
    end = datetime.strptime(day, DAY_FORMAT)
    return [(end - timedelta(days=n)).strftime(DAY_FORMAT) for n in range(count - 1, -1, -1)]


def start_of(day: str) -> str:
    """The instant *day*'s local midnight falls on, spelled as the ledger spells its stamps."""
    midnight = datetime.strptime(day, DAY_FORMAT).astimezone()
    return midnight.astimezone(timezone.utc).isoformat()
