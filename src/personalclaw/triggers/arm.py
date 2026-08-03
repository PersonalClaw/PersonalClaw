"""Arming the clock: a trigger spec → its next fire (§3.1 — S96).

**🔴 THE BLOCKER THIS EXISTS TO CLEAR, measured before writing.** A migrated cron lands in
`triggers.json` with **`next_fire_at` empty**, and `service.due_ids` only surfaces triggers that
HAVE a `next_fire_at`. So:

    store.migrate_from_crons()   # lossless: true, enabled: true
    SVC.due_ids(...)             # []
    SVC.boot(store, ...)         # {'rearmed': [], 'total': 1}
    # next_fire_at after boot:   ('j-cron', '(none)')  → due_ids STILL []

A migrated cron is **permanently inert** in the new engine: it reports migrated-and-enabled and
never fires. That is the real reason the clock cutover could not proceed — not the double-fire risk
the queue described, which is downstream of a tick that can actually arm a clock.

`scheduling.py` had `recompute_from_completion` (interval only) and `boot_recovery` (needs an
EXISTING `next_fire_at`); `service.next_after_completion` returns 0.0 for every non-interval kind by
design ("the recurrence engine's job"). Nothing computed a FIRST fire from a spec. This module is
that missing primitive, for all four `CLOCK_KINDS`.

**Semantics are inherited, not invented.** `schedule.compute_next_run_ts` is the shipped, live
computation for the same question, and its two subtle rules are preserved verbatim here:

* **A cron expression is evaluated in the trigger's OWN timezone**, by building the croniter base as
  `datetime.fromtimestamp(now, tz=<trigger tz>)` — croniter interprets the expression in the base's
  tz and `get_next(float)` returns a UTC epoch. Evaluating in UTC instead would silently shift every
  tz-bearing job by the offset, which on a DST boundary is a moving target.
* **A past one-shot `at` returns 0.0 (never fires again)** rather than "now": re-arming an elapsed
  one-shot converts a missed appointment into an immediate surprise fire.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _trigger_tz(trigger: Any) -> Any:
    """The trigger's timezone, or UTC. Mirrors `schedule._job_tz`'s fail-safe direction.

    An unknown zone falls back to UTC rather than raising: a typo'd tz must not make a trigger
    unarmable, and UTC is the one zone that always exists.
    """
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    name = str(spec.get("timezone") or "").strip()
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - an invalid zone is a config typo, not a crash
        logger.debug("trigger %s has unknown timezone %r; using UTC", trigger.id, name)
        return timezone.utc


def next_fire(trigger: Any, *, now: float = 0.0, last_fire: float = 0.0) -> float:
    """The next fire for a clock trigger as a UTC epoch, or 0.0 when it will never fire again.

    0.0 means "not armable", and callers must treat it as "leave `next_fire_at` alone / do not
    schedule" — never as "fire now". The distinction matters for an elapsed one-shot: arming it to
    `now` would fire a missed appointment immediately.

    `last_fire` anchors an interval so a recompute does not re-phase the schedule; it defaults to
    the trigger's `created_at` grid, matching `next_after_completion`'s §3.1 anchoring rule.
    """
    now = now or time.time()
    if getattr(trigger, "kind", "") != "clock":
        # Only clock triggers have a time-driven next fire. An event/file/webhook trigger fires on
        # its source, and inventing a schedule for one would make it poll.
        return 0.0
    if not getattr(trigger, "enabled", False):
        return 0.0

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = str(spec.get("kind") or "").strip().lower()

    try:
        if kind == "cron":
            expr = str(spec.get("expr") or "").strip()
            if not expr:
                return 0.0
            from croniter import croniter  # type: ignore[import-untyped]

            if not croniter.is_valid(expr):
                # A broken expression must not arm: firing on a guessed cadence is worse than not
                # firing, and the row is already visible as broken in the store + doctor.
                logger.debug("trigger %s has an invalid cron expr %r", trigger.id, expr)
                return 0.0
            base = datetime.fromtimestamp(now, tz=_trigger_tz(trigger))
            return float(croniter(expr, base).get_next(float))

        if kind in ("interval", "sequence"):
            secs = _positive(spec.get("interval_secs"))
            if secs <= 0:
                return 0.0
            anchor = last_fire or _created_at(trigger) or now
            # Advance on the anchor's grid so a long-running job does not re-phase its own
            # schedule, and skip any slots already elapsed rather than firing a backlog.
            elapsed = max(0.0, now - anchor)
            slots = int(elapsed // secs) + 1
            return anchor + slots * secs

        if kind == "at":
            at = _positive(spec.get("at"))
            # A PAST one-shot never fires again — 0.0, not `now`. Re-arming an elapsed appointment
            # to the present turns a missed fire into an immediate surprise.
            return at if at > now else 0.0
    except Exception:  # noqa: BLE001 - an unarmable trigger must not break the boot sweep
        logger.warning(
            "could not compute next fire for %s", getattr(trigger, "id", "?"), exc_info=True
        )
        return 0.0

    return 0.0


def _positive(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out > 0 else 0.0


def _created_at(trigger: Any) -> float:
    """The trigger's creation epoch for interval anchoring, or 0.0."""
    raw = (
        trigger.spec.get("created_at") if isinstance(getattr(trigger, "spec", None), dict) else None
    )
    return _positive(raw)


def arm(trigger: Any, *, now: float = 0.0, last_fire: float = 0.0) -> str:
    """`next_fire` as the ISO string the store persists, or "" when not armable.

    Returns the empty string rather than a sentinel date so an unarmable trigger keeps the same
    "no next fire" representation the store already uses — one spelling for one meaning.
    """
    from personalclaw.triggers.service import to_iso

    when = next_fire(trigger, now=now, last_fire=last_fire)
    return to_iso(when) if when > 0 else ""


def needs_arming(trigger: Any) -> bool:
    """Whether this trigger is a clock trigger with no next fire recorded.

    This is the predicate the boot sweep filters on: exactly the population that migrated in
    enabled-but-inert. A trigger that already carries a `next_fire_at` is left alone, because
    re-arming a live schedule mid-flight is how a fire gets skipped or doubled.
    """
    return (
        getattr(trigger, "kind", "") == "clock"
        and bool(getattr(trigger, "enabled", False))
        and not str(getattr(trigger, "next_fire_at", "") or "").strip()
    )
