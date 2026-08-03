"""Missed fires: review, don't lie, don't storm (AUTOMATION-SUBSTRATE §3.4 — S65).

Local-first means a closed lid stops the loop. So the honest question after a restart is not "what
should have run" but "what do I tell the user about what didn't". Three answers this module makes
different, because collapsing any two of them produces a specific bad outcome:

* **Enumerate, bounded.** A machine down for a week with a minutely trigger missed 10,080 slots.
  Enumerating them all is an unusable page and a slow boot; enumerating
  none is the system lying about
  what happened. So: the newest N per trigger become reviewable rows, and everything older collapses
  into ONE summary per trigger that still says how many.
* **Review, don't auto-run.** A missed fire is a decision the user should make — "run the 3am backup
  now, at 9am" is sometimes right and sometimes exactly wrong. Run-now records `ran_late`; dismiss
  records `skipped_missed`. Both are ledger rows, because §1.3 bans
  silent drops and a dismissed card
  that left no trace is a silent drop with a UI.
* **`catch_up` fires ONCE, staggered.** launchd's RunAtLoad semantics. The storm guard is the
  once-per-trigger cap plus the same deterministic per-id stagger the
  scheduler already uses — without
  both, a laptop opening after a weekend runs every automation it owns simultaneously.

`next_fire_at` rolls forward as part of enumeration, so re-opening the
page does not re-enumerate the
same misses. Pure functions over records; the boot sequence that calls them is the service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.triggers.models import Outcome
from personalclaw.triggers.scheduling import (
    BOOT_STAGGER_BASE_SECS,
    BOOT_STAGGER_WINDOW_SECS,
    jitter_offset,
)

#: Total missed slots enumerated across ALL triggers at one boot. A hard
#: ceiling on the work a restart
#: does before the gateway is usable: past this the summary rows carry the count and nobody waits.
ENUMERATION_CAP = 480

#: Reviewable rows kept per trigger. Twenty is what a person will
#: actually scan; past that the value is
#: in the aggregate, which is what the summary row carries.
REVIEW_ROWS_PER_TRIGGER = 20

#: Ledger origin for an automatic catch-up fire. A DISTINCT origin, not a normal fire: "why did this
#: run at 09:02 when it is scheduled for 03:00" is answerable only if the
#: row says it was a catch-up.
CATCHUP_ORIGIN = "catchup"


@dataclass
class MissedSlot:
    """One fire that should have happened while the gateway was down."""

    trigger_id: str
    scheduled_for: float

    def to_dict(self) -> dict[str, Any]:
        return {"trigger_id": self.trigger_id, "scheduled_for": self.scheduled_for}


@dataclass
class MissedSummary:
    """The collapse of everything older than the review window, for ONE trigger.

    Carries `count` and the oldest/newest bounds rather than the slots themselves. The number is the
    information — "47 missed since Friday" is actionable, and 47 rows of it are not.
    """

    trigger_id: str
    count: int
    oldest: float
    newest: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "count": self.count,
            "oldest": self.oldest,
            "newest": self.newest,
        }


@dataclass
class MissedReview:
    """What one boot produced: reviewable rows, per-trigger summaries, and honesty about truncation.

    `truncated` is separate from the summaries because they answer
    different questions. A summary says
    "this trigger missed more than we listed"; `truncated` says "the
    ENUMERATION itself stopped early,
    so even the counts are a floor". Reporting a floor as a total is the "don't lie" half of §3.4.
    """

    rows: list[MissedSlot] = field(default_factory=list)
    summaries: list[MissedSummary] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "summaries": [s.to_dict() for s in self.summaries],
            "truncated": self.truncated,
        }


def enumerate_missed(
    *,
    trigger_id: str,
    last_fire_at: float,
    interval_secs: float,
    now: float,
    budget: int = ENUMERATION_CAP,
    review_rows: int = REVIEW_ROWS_PER_TRIGGER,
) -> tuple[list[MissedSlot], MissedSummary | None, int]:
    """Missed slots for one trigger. Returns `(review_rows, summary_or_None, budget_spent)`.

    Walks the grid from the last fire. Two guards that matter:

    * The budget bounds the ROWS BUILT, never the count. A minutely
    trigger down for a week has 10,080
      missed slots; counting them is one division, allocating them is
      what makes boot slow. Budgeting
      the count instead starves every other trigger — measured, and the
      comment in the body says how.
    * The NEWEST slots become the review rows. Older ones are the ones a person is least likely to
      want to run now — a 3am backup missed six days ago is history, last night's is a decision.

    A trigger with no interval (a one-shot, an event trigger) has no grid
    to walk and returns nothing:
    "missed" is only meaningful for a recurrence.
    """
    if interval_secs <= 0 or last_fire_at <= 0 or now <= last_fire_at:
        return [], None, 0

    # Count first, build second — and the BUDGET BOUNDS THE ROWS, not the count.
    #
    # Measured (S65): budgeting the count starved every other trigger. Thirty triggers each down a
    # week, shared budget 480: the alphabetically-first minutely trigger spent all 480 counting its
    # own 10,080 missed slots, and the other twenty-nine got NO review card — the page showed one
    # trigger and silently omitted the rest. Counting is arithmetic and costs nothing; allocating
    # `MissedSlot` objects is what makes boot slow. So the count is
    # always exact (which is what makes
    # the summary honest) and the budget is spent on rows.
    elapsed = now - last_fire_at
    total = int(elapsed // interval_secs)
    if total <= 0:
        return [], None, 0

    shown = min(total, max(0, review_rows), max(0, budget))
    rows = [
        MissedSlot(trigger_id=trigger_id, scheduled_for=last_fire_at + (total - i) * interval_secs)
        for i in range(shown, 0, -1)
    ]
    older = total - shown
    summary = (
        MissedSummary(
            trigger_id=trigger_id,
            count=older,
            oldest=last_fire_at + interval_secs,
            newest=last_fire_at + (total - shown) * interval_secs,
        )
        if older > 0
        else None
    )
    return rows, summary, shown


def review_at_boot(
    triggers: list[dict[str, Any]],
    *,
    now: float,
    budget: int = ENUMERATION_CAP,
) -> MissedReview:
    """Enumerate misses across every trigger, under ONE shared budget.

    Shared rather than per-trigger, because the thing being protected is boot time and that is a
    property of the whole pass. A per-trigger cap with fifty triggers is fifty times the ceiling.

    The budget buys ROWS, so a trigger with thousands of missed slots
    takes its ~20 and leaves the rest
    of the budget for everyone else — its remaining slots still show up,
    in its summary. Budgeting the
    COUNT instead let one noisy trigger consume the whole pass (measured:
    1 of 30 triggers got a card).

    Triggers are walked in id order so a boot is reproducible: an
    unstable order would give different
    triggers the remaining budget on different restarts, and "why did my backup get a review card
    yesterday but not today" is unanswerable.
    """
    review = MissedReview()
    remaining = max(0, budget)
    for entry in sorted(triggers, key=lambda t: str(t.get("id", ""))):
        if remaining <= 0:
            review.truncated = True
            break
        rows, summary, spent = enumerate_missed(
            trigger_id=str(entry.get("id", "") or ""),
            last_fire_at=float(entry.get("last_fire_at", 0.0) or 0.0),
            interval_secs=float(entry.get("interval_secs", 0.0) or 0.0),
            now=now,
            budget=remaining,
        )
        review.rows.extend(rows)
        if summary is not None:
            review.summaries.append(summary)
        remaining -= spent
    return review


def resolve_missed(action: str) -> tuple[str, str]:
    """What a user's decision on a review card records. Returns `(outcome, reason)`.

    Both branches write a ledger row. A dismissed card that left no trace
    would be a silent drop with a
    UI on it — §1.3's rule is not about the mechanism, it is about whether the history is honest.
    """
    if action == "run_now":
        return (
            Outcome.RAN_LATE.value,
            "ran from a missed-fire review card, after its scheduled slot",
        )
    if action == "dismiss":
        return Outcome.SKIPPED_MISSED.value, "the user dismissed the missed-fire card"
    return (
        Outcome.REFUSED.value,
        f"unknown review action {action!r}; expected run_now or dismiss",
    )


def catch_up_plan(triggers: list[dict[str, Any]], *, now: float) -> list[tuple[str, float, str]]:
    """Which triggers get an automatic catch-up fire, when, and why not for the rest.

    Returns `(trigger_id, fire_at, reason)` for every candidate —
    including the refused ones, because a
    trigger with `catch_up: true` that did NOT fire needs an explanation as much as one that did.

    Three guards, and each has a distinct failure:

    * **Opt-in.** Only `catch_up: true`. RunAtLoad semantics are a deliberate choice, not a default.
    * **Once per trigger.** At most one catch-up regardless of how many
    slots were missed — a weekend
      down would otherwise fire Monday's automation forty times.
    * **Staggered.** The same deterministic per-id offset the scheduler
    uses, so a laptop opening after
      a weekend does not run every automation it owns in the same second.
    """
    plan: list[tuple[str, float, str]] = []
    for entry in sorted(triggers, key=lambda t: str(t.get("id", ""))):
        tid = str(entry.get("id", "") or "")
        if not entry.get("catch_up"):
            plan.append((tid, 0.0, "catch_up is off: a missed slot is reviewed, not re-run"))
            continue
        if not entry.get("missed_last_slot"):
            plan.append((tid, 0.0, "nothing was missed"))
            continue
        if not entry.get("fires_automatically", True):
            plan.append((tid, 0.0, "disabled or paused: a catch-up must not restart it"))
            continue
        fire_at = now + BOOT_STAGGER_BASE_SECS + jitter_offset(tid, BOOT_STAGGER_WINDOW_SECS)
        plan.append((tid, fire_at, CATCHUP_ORIGIN))
    return plan


def within_rate_window(
    *, fires_in_window: int, max_per_hour: int, manual: bool = False
) -> tuple[bool, str]:
    """Whether another fire is allowed by the sliding hourly window.

    The backstop under catch-up: even a correctly staggered,
    once-per-trigger catch-up must not push a
    trigger past the cap its author set.

    A MANUAL fire bypasses it. That asymmetry is deliberate and stated in
    the plan: the cap exists to
    stop the machine from running away on its own, and a person clicking
    Run is not the machine running
    away. Floors that protect a remote service
    (`max_requests_per_sec`-class) are NOT bypassed — those
    protect someone else.
    """
    if manual:
        return True, "manual fires bypass the hourly cap"
    if max_per_hour <= 0:
        return True, "no hourly cap configured"
    if fires_in_window >= max_per_hour:
        return False, f"{fires_in_window} fires in the last hour reaches the cap of {max_per_hour}"
    return True, ""


def roll_forward(*, next_fire_at: float, interval_secs: float, now: float) -> float:
    """Where `next_fire_at` lands after enumeration, so a re-open does not re-enumerate.

    Rolls to the first grid slot strictly after `now`, preserving phase — the same rule as the
    scheduler's grid anchoring, for the same reason: rolling to `now + interval` would re-phase a
    schedule that was correct before the machine went to sleep.
    """
    if interval_secs <= 0:
        return next_fire_at
    if next_fire_at <= 0:
        return 0.0
    if next_fire_at > now:
        return next_fire_at
    missed = int((now - next_fire_at) // interval_secs) + 1
    return next_fire_at + missed * interval_secs
