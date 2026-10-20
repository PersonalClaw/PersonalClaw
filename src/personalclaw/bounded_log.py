"""Bounded logs keep their newest rows by each row's own time, in the order the rows happened.

A bounded log — a JSON-lines file, a list of records in a document, a table — lets its oldest rows
go once it is full. Which rows are the oldest is a question about TIME, and where a line sits in a
file answers it only while every line was written as it happened. Two writers add rows that did
not just happen: a merge restore brings an archive's in, and a sync brings another machine's, and
neither can put an older row where it belongs without rewriting the file. A trim that kept the
file's last lines then kept the archive's old rows and deleted the newest ones, and said nothing.

So every bounded log in core keeps ONE rule, and keeps it here:

* **A trim keeps the newest rows by their own time** (:func:`newest`, :func:`trim_jsonl`,
  :func:`prune_table`). It is right whatever order the rows are in, so no writer — a merge, a
  sync, a clock that stepped back — can make it delete newer rows to keep older ones.
* **A merge writes the log in time order** (:func:`merge_jsonl`). A reader that takes the end of
  the file for the newest rows — the security audit, a run history page, "the last verdict
  wins" — then reads them right.
* **A list of the log is newest first by the same time** (:func:`newest_first`), so a reader that
  shows the head of one, as the notification bell and the phone's Recent list do, shows what
  happened last whatever order the file holds.

A row's time is its *at*: the name of its field, or a function that reads it. The value is an
instant written with its offset, a zone-less date-time (read as this machine's local time, as
:func:`personalclaw.instants.as_instant` reads one), or a number — seconds since the epoch, or a
sequence number a single writer counts up, which orders its rows the same way. A row whose time
cannot be read sorts before every row whose time can: nothing says it is recent, so it is the
first a trim lets go. Rows at the same time keep the order they had.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar, Union

from personalclaw.atomic_write import atomic_write
from personalclaw.instants import as_instant

T = TypeVar("T")

#: A row's time: the name of its field, or a function that reads it off the row.
At = Union[str, Callable[[Any], Any]]

#: The time of a row whose time cannot be read: before every other.
UNKNOWN = -math.inf

#: How many times a merge reads a log again when the log changed while it was being merged.
_MERGE_ATTEMPTS = 20

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def instant(value: Any) -> float:
    """The moment *value* names, as a number to order by, or :data:`UNKNOWN`."""
    if isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else UNKNOWN
    if not isinstance(value, str) or not value.strip():
        return UNKNOWN
    stamp = as_instant(value.strip())
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return UNKNOWN


def time_of(row: Any, at: At) -> float:
    """*row*'s time (:func:`instant` of what *at* reads off it)."""
    if callable(at):
        try:
            return instant(at(row))
        except (AttributeError, KeyError, TypeError, ValueError):
            return UNKNOWN
    return instant(row.get(at)) if isinstance(row, Mapping) else UNKNOWN


def in_time_order(rows: Iterable[T], *, at: At) -> list[T]:
    """*rows*, oldest first by their own time; rows at the same time keep their order."""
    return sorted(rows, key=lambda row: time_of(row, at))


def newest_first(rows: Iterable[T], *, at: At) -> list[T]:
    """*rows*, newest first by their own time: :func:`in_time_order` turned around, so of rows at
    one time the one written last comes first, and a row whose time cannot be read comes last."""
    return in_time_order(rows, at=at)[::-1]


def newest(rows: Iterable[T], keep: int, *, at: At) -> list[T]:
    """The at most *keep* newest of *rows* by their own time, oldest first: what a trim keeps."""
    ordered = in_time_order(rows, at=at)
    return ordered[max(0, len(ordered) - max(0, keep)) :]


def trim_jsonl(path: Path, keep: int, *, at: At, past: int | None = None) -> bool:
    """Rewrite the JSON-lines log at *path* to its *keep* newest rows, in time order, once it
    holds more than *past* rows (twice *keep* unless said): letting it grow to twice its bound
    between trims keeps the common append one write. Returns whether it trimmed.

    Every line is judged by the row it holds, and kept byte for byte; a line that holds no row has
    no time, so it is the first to go. Raises ``OSError``, for a caller whose write is best-effort
    to report as it reports its own.
    """
    lines = _lines(path)
    if len(lines) <= (2 * keep if past is None else past):
        return False
    kept = newest(lines, keep, at=lambda line: _line_value(line, at))
    atomic_write(path, "".join(line + "\n" for line in kept))
    return True


class LogKeptChanging(OSError):
    """A merge read a log that changed every time before it could write it back."""


def merge_jsonl(src: Path, dst: Path, *, key: str, at: At) -> int:
    """Bring into the JSON-lines log at *dst* every row of *src* it does not hold, and write the
    two in time order. Returns how many rows came in; nothing is written when none did.

    A row is matched on its *key* (on its whole line when it has none), so a merge repeated
    brings nothing. A line of *src* that holds no row is not taken in; every line of *dst* stays,
    byte for byte. *dst* must exist: what a home does not hold at all is copied, not merged.

    The live home goes on writing its logs while a merge runs, so the log is read again just
    before it is replaced and the merge done again if it changed meanwhile: a line appended
    between the read and the write would otherwise be lost. Raises :class:`LogKeptChanging` when
    it never stopped changing, having written nothing.
    """
    incoming = [(line, row) for line in _lines(src) if (row := _row(line)) is not None]
    for _attempt in range(_MERGE_ATTEMPTS):
        before = signature(dst)
        held = _lines(dst)
        have = {_identity(line, _row(line), key) for line in held}
        came: list[str] = []
        for line, row in incoming:
            identity = _identity(line, row, key)
            if identity in have:
                continue
            have.add(identity)
            came.append(line)
        if not came:
            return 0
        merged = in_time_order(held + came, at=lambda line: _line_value(line, at))
        if signature(dst) != before:
            continue
        atomic_write(dst, "".join(line + "\n" for line in merged))
        return len(came)
    raise LogKeptChanging(f"{dst.name} kept changing while it was being merged; left as it was")


def prune_table(cur: Any, table: str, keep: int, *, at: str) -> int:
    """Delete all but the *keep* newest rows of *table* by its column *at* (rows at the same time
    by their rowid), through the database cursor *cur*. Returns how many went.

    The same rule as :func:`newest`, for a table: a merge that brings another database's rows in
    keeps their row ids, which say nothing about when."""
    for name in (table, at):
        if not _IDENTIFIER.match(name):
            raise ValueError(f"not a plain SQL identifier: {name!r}")
    cur.execute(
        f'DELETE FROM "{table}" WHERE rowid NOT IN '  # noqa: S608 — both names checked above
        f'(SELECT rowid FROM "{table}" ORDER BY "{at}" DESC, rowid DESC LIMIT ?)',
        (max(0, keep),),
    )
    return max(0, cur.rowcount)


def _lines(path: Path) -> list[str]:
    """The non-blank lines of *path*, without their line ends; none for a file that is not there."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]


def _row(line: str) -> dict | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _line_value(line: str, at: At) -> Any:
    """What *at* reads off the row *line* holds, for :func:`time_of` (via a callable *at*)."""
    row = _row(line)
    if row is None:
        return None
    if callable(at):
        return at(row)
    return row.get(at)


def _identity(line: str, row: dict | None, key: str) -> str:
    value = row.get(key) if row is not None else None
    return str(value) if value not in (None, "") else line.strip()


def signature(path: Path) -> tuple[int, int, int] | None:
    """What the file at *path* is now — which file, how long, last written when — or None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)
