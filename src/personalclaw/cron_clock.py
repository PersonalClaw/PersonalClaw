"""A cron expression's fires, stepped on the wall clock of the zone it is read in.

**Why this module exists.** ``croniter`` steps an AWARE start by keeping the UTC offset it started
in. From a September start in America/Toronto (EDT, -04:00) it finds 1 January 15:10 and stamps it
with September's offset: 15:10 EDT, which is 16:10 EST, so ``10 15 1 1 *`` armed to 21:10Z where
15:10 in Toronto that day is 20:10Z, and the Triggers page said "At 4:10 PM EST". Any fire across a
change of the clocks landed an hour late (or early, across the spring change), and croniter only
corrects for it with ``pytz`` zones, which PersonalClaw does not use.

So an expression is stepped on NAIVE wall time — what the clock on the wall reads in the zone — and
each wall time it names is then placed in the zone (:func:`_placed`):

* a wall time the zone shows once is that instant;
* one it shows twice, as the clocks go back, fires once, at its first instant: a job at 01:30 runs
  at 01:30 before the change and not again at 01:30 after it;
* one it never shows, as the clocks go forward, fires the moment they jump past it: a job at 02:30
  on the night 02:00 becomes 03:00 runs at 03:00, rather than not at all.

This is the one place a cron expression is stepped: a ``croniter(...)`` built anywhere else in
``src/personalclaw`` fails `tests/test_cron_is_stepped_on_the_wall_clock.py`. Validating or
matching one (``croniter.is_valid``, ``croniter.match``) needs no zone and stays where it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Literal

from croniter import CroniterBadDateError, croniter  # type: ignore[import-untyped]

#: How many wall times one step may pass over before it gives up. Only a wall time already behind
#: ``now`` is passed over — the first instant of a repeated one, seen from its second — so an
#: every-minute expression started in a repeated hour passes over at most that hour's minutes (two
#: hours' worth in a zone whose clocks move two). Exhausting it reads as "no next fire".
_MAX_STEPS = 256


@dataclass(frozen=True)
class Placement:
    """One fire of an expression: the instant ``at`` (a UTC epoch), the naive wall time ``wall`` the
    expression named, and how the zone shows that wall time (``placed``):

    * ``exact``: once, so the fire is at that instant;
    * ``jumped``: never, as the clocks go forward, so the fire is at the jump;
    * ``repeated``: twice, as the clocks go back, so the fire is at its first instant.
    """

    at: float
    wall: datetime
    placed: Literal["exact", "jumped", "repeated"]


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
#: The most days each month has, February's leap day included.
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
#: What :func:`never_runs` says of an expression the stepper finds no fire for and cannot name.
NO_DATE = "No date on the calendar matches it."


def never_runs(expr: str) -> str:
    """Why the valid expression *expr* matches no time on any calendar, in a plain sentence, or ''
    when it runs. ``0 0 30 2 *`` is valid and never comes round: February has no 30th.

    A day of the month that none of its months has is said by name. Any other expression the
    stepper finds no fire for (``croniter``'s bad-date refusal) is said in general. An expression
    ``croniter`` refuses outright is not this function's: :func:`croniter.is_valid` says that.
    """
    named = _impossible_days(expr)
    if named:
        return named
    try:
        croniter(expr, datetime(2000, 1, 1)).get_next(datetime)
    except CroniterBadDateError:
        # A weekday beside an impossible day (`0 0 30 2 1`) is refused too: name the day.
        return _impossible_days(expr, any_weekday=True) or NO_DATE
    except (ValueError, KeyError):
        return ""
    return ""


def _impossible_days(expr: str, *, any_weekday: bool = False) -> str:
    """'February has no 30th.' for a day of the month (field 3) that none of the months (field 4)
    has, when both are plain numbers or lists of them; '' otherwise. A restricted weekday (field
    5) can make the two days an either-or in cron, so it is named only with *any_weekday*, once
    the stepper has refused the expression."""
    fields = expr.split()
    if len(fields) != 5 or (fields[4] not in ("*", "?") and not any_weekday):
        return ""
    days, months = _numbers(fields[2], 1, 31), _numbers(fields[3], 1, 12)
    if days is None:
        return ""
    months = months if months is not None else list(range(1, 13))
    if not days or not months or any(min(days) <= _MONTH_DAYS[m - 1] for m in months):
        return ""
    day = min(days)
    names = [_MONTHS[m - 1] for m in sorted(set(months))]
    said = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"{said} {'has' if len(names) == 1 else 'have'} no {day}{_ordinal(day)}."


def _numbers(field: str, low: int, high: int) -> list[int] | None:
    """The values of a cron field made of plain numbers and ranges (``30``, ``29,30``, ``29-31``),
    or None for any other spelling (``*``, steps, names)."""
    out: list[int] = []
    for part in field.split(","):
        first, sep, last = part.partition("-")
        if not first.isdigit() or (sep and not last.isdigit()):
            return None
        a, b = int(first), int(last) if sep else int(first)
        if not low <= a <= b <= high:
            return None
        out.extend(range(a, b + 1))
    return out


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def upcoming(expr: str, now: float, tz: tzinfo, count: int) -> list[Placement]:
    """The next ``count`` fires of ``expr`` after the instant ``now``, read in ``tz``, in order.

    Each fire is the first one after the one before it, exactly as :func:`next_fire` finds it, so
    a preview of the fires and the clock that arms them cannot disagree. Wall times that land on one
    instant (two that the clocks jump past, and the time they jump to) are one fire. Fewer than
    ``count`` when a step passes :data:`_MAX_STEPS` wall times without one. Raises what ``croniter``
    raises for an expression it refuses: callers validate first.
    """
    steps = croniter(expr, _wall(now, tz))
    out: list[Placement] = []
    after = now
    while len(out) < count:
        for _ in range(_MAX_STEPS):
            wall = steps.get_next(datetime)
            at = _placed(wall, tz)
            if at > after:
                out.append(Placement(at=at, wall=wall, placed=_how_shown(wall, at, tz)))
                after = at
                break
        else:
            break
    return out


def next_fire(expr: str, now: float, tz: tzinfo) -> float:
    """The first fire of ``expr`` after the instant ``now`` (a UTC epoch), read in ``tz``.

    0.0 when none is found within :data:`_MAX_STEPS`. Raises what ``croniter`` raises for an
    expression it refuses, as it did before: callers validate first.
    """
    first = upcoming(expr, now, tz, 1)
    return first[0].at if first else 0.0


def previous_fire(expr: str, now: float, tz: tzinfo) -> float:
    """The last fire of ``expr`` at or before the instant ``now`` (a UTC epoch), read in ``tz``.

    The same placement :func:`next_fire` uses, so the boundary a sweep counts back to is a fire the
    schedule really made. 0.0 when none is found within :data:`_MAX_STEPS`.
    """
    steps = croniter(expr, _wall(now, tz))
    for _ in range(_MAX_STEPS):
        at = _placed(steps.get_prev(datetime), tz)
        if at <= now:
            return at
    return 0.0


def _how_shown(wall: datetime, at: float, tz: tzinfo) -> Literal["exact", "jumped", "repeated"]:
    """How the zone shows the wall time ``wall``, which :func:`_placed` put at ``at``."""
    if _wall(at, tz) != wall:
        return "jumped"
    second = wall.replace(tzinfo=tz, fold=1).timestamp()
    return "repeated" if second != at and _wall(second, tz) == wall else "exact"


def _wall(now: float, tz: tzinfo) -> datetime:
    """What the clock on the wall in ``tz`` reads at ``now``, as a naive datetime."""
    return datetime.fromtimestamp(now, tz).replace(tzinfo=None)


def _placed(wall: datetime, tz: tzinfo) -> float:
    """The instant the naive wall time ``wall`` fires at in ``tz`` (see the module docstring).

    ``fold=0`` is a repeated wall time's first instant, which is the one it fires at. A wall time
    the zone never shows reads back as a different one; it fires at the jump, the first instant
    whose offset is the one after the change, found between the two readings of ``wall`` (its
    ``fold=1`` reading, before the jump, and its ``fold=0`` one, after it).
    """
    first = wall.replace(tzinfo=tz)
    at = first.timestamp()
    if _wall(at, tz) == wall:
        return at
    after_change = wall.replace(tzinfo=tz, fold=1)
    offset_after = after_change.utcoffset()
    low, high = int(after_change.timestamp()), int(at)
    while high - low > 1:
        mid = (low + high) // 2
        if datetime.fromtimestamp(mid, tz).utcoffset() == offset_after:
            high = mid
        else:
            low = mid
    return float(high)
