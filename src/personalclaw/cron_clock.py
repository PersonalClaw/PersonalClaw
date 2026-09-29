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

from datetime import datetime, tzinfo

from croniter import croniter  # type: ignore[import-untyped]

#: How many wall times one step may pass over before it gives up. Only a wall time already behind
#: ``now`` is passed over — the first instant of a repeated one, seen from its second — so an
#: every-minute expression started in a repeated hour passes over at most that hour's minutes (two
#: hours' worth in a zone whose clocks move two). Exhausting it reads as "no next fire".
_MAX_STEPS = 256


def next_fire(expr: str, now: float, tz: tzinfo) -> float:
    """The first fire of ``expr`` after the instant ``now`` (a UTC epoch), read in ``tz``.

    0.0 when none is found within :data:`_MAX_STEPS`. Raises what ``croniter`` raises for an
    expression it refuses, as it did before: callers validate first.
    """
    steps = croniter(expr, _wall(now, tz))
    for _ in range(_MAX_STEPS):
        at = _placed(steps.get_next(datetime), tz)
        if at > now:
            return at
    return 0.0


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
