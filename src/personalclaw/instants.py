"""Timestamps the gateway stores and serves: instants, always written with their offset.

A stored time leaves this process to be read somewhere else: a browser in another timezone, a
phone abroad, an export, another tool. A bare date-time such as ``2026-10-01T01:17:09`` names no
instant, so whoever reads it supplies their own zone. Written by ``datetime.now()`` on a gateway
in Toronto and read by a browser in Los Angeles, a folder due again in five minutes read
"next in 3h", and its "polled just now" stayed pinned for three hours.

**The convention.** Every timestamp written for storage or for a response is UTC with its
offset, ``2026-10-01T05:17:09.123456+00:00``: what ``datetime.now(timezone.utc).isoformat()``
produces, and what most of the codebase already wrote. :func:`utc_now_iso` and
:func:`utc_iso` are that convention, so a writer cannot drop the zone by forgetting an argument.

**Stamps written before it.** The zone-less ones were written by ``datetime.now()``, this
machine's local wall clock, and :func:`as_instant` reads them as exactly that. It is the one
reading of a zone-less stamp: the store's backfill (:func:`backfill_zone_less`) and the
transcript reader both use it, so a legacy value and a new one name the same instant.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: A date-time with a clock part: the only shape that can be zone-less. A date alone
#: (``2026-10-26``) is a calendar day rather than an instant, and is left as it is.
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def utc_now_iso() -> str:
    """Now, as the instant every stored or served timestamp is written in."""
    return datetime.now(timezone.utc).isoformat()


def utc_iso(epoch: float) -> str:
    """Seconds since the epoch, as the instant every stored or served timestamp is written in."""
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()


def as_instant(stamp: Any) -> Any:
    """*stamp* with its offset, in UTC, when it is a zone-less date-time; otherwise unchanged.

    A zone-less date-time is read as this machine's local time, because that is the clock that
    wrote it (``datetime.now().isoformat()``). A stamp that already carries an offset keeps its
    exact text, so a value used to find a record (a message's ``ts``) still finds it. A date
    alone, an empty value, a number or anything that does not parse is returned as it was: this
    reads a record, it does not guess at one.
    """
    if not isinstance(stamp, str) or not _DATE_TIME.match(stamp):
        return stamp
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if parsed.tzinfo is not None:
        return stamp
    return parsed.astimezone(timezone.utc).isoformat()


def as_utc(stamp: Any) -> Any:
    """*stamp* as UTC text, for comparing it as TEXT with stored instants (``updated_at > ?``).

    Stored instants all read ``…+00:00``, so text order is time order only between stamps in
    that form: a zone-less date-time is read as this machine's local time (:func:`as_instant`)
    and one with another offset is moved to UTC. A date alone, or anything that is not a
    date-time, is returned as it was.
    """
    text = as_instant(stamp)
    if not isinstance(text, str) or not _DATE_TIME.match(text):
        return text
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def local_day(stamp: Any) -> str:
    """The calendar day (``YYYY-MM-DD``) *stamp* falls on in this machine's zone, or ``""``.

    For a rule that is about a DAY, such as a journal entry being editable on the day it was
    written. Slicing the first ten characters off an instant in UTC names the UTC day, which is
    tomorrow for an entry written in the evening anywhere west of Greenwich.
    """
    text = as_instant(stamp)
    if not isinstance(text, str) or not text:
        return ""
    try:
        return datetime.fromisoformat(text).astimezone().date().isoformat()
    except ValueError:
        return ""


def _zone_less_rows(db: Any, table: str, column: str) -> list[tuple[int, str]]:
    """The rows of ``table`` whose ``column`` holds a date-time with no offset.

    The GLOBs only narrow the scan to candidates, so a store that is already converted costs
    one indexed-free pass and no Python work per row; :func:`as_instant` makes the decision.
    """
    rows = db.execute(
        f'SELECT rowid, "{column}" FROM "{table}" '  # noqa: S608 - names come from sqlite_master
        f'WHERE "{column}" GLOB ? AND NOT ("{column}" GLOB ? OR "{column}" GLOB ?)',
        (
            "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][T ][0-9][0-9]:[0-9][0-9]*",
            "*[Zz]",
            "*[+-][0-9][0-9]:[0-9][0-9]",
        ),
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def backfill_zone_less(db: Any) -> int:
    """Rewrite every zone-less ``*_at`` value in this SQLite database as its UTC instant.

    Idempotent by content: a converted value carries an offset and is never selected again, so
    this runs on every open with no schema version to gate it, and costs a scan once the store
    is converted. Every ``*_at`` column is an instant by this codebase's naming; a column that
    holds a date alone is left untouched by :func:`as_instant`. One transaction: a store is
    either converted or as it was. Returns how many values changed.
    """
    # A virtual table's columns are not stored in it, and a WITHOUT ROWID table has no rowid to
    # address a row by; neither kind holds a stamp here.
    tables = [
        str(r[0])
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND sql NOT LIKE '%VIRTUAL%' AND sql NOT LIKE '%WITHOUT ROWID%'"
        ).fetchall()
    ]
    updates: list[tuple[str, str, str, int]] = []
    for table in tables:
        columns = [str(r[1]) for r in db.execute(f'PRAGMA table_info("{table}")').fetchall()]
        for column in columns:
            if not column.endswith("_at"):
                continue
            for rowid, value in _zone_less_rows(db, table, column):
                converted = as_instant(value)
                if converted != value:
                    updates.append((table, column, converted, rowid))
    if not updates:
        return 0
    db.execute("BEGIN")
    try:
        for table, column, converted, rowid in updates:
            db.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',  # noqa: S608
                (converted, rowid),
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return len(updates)
