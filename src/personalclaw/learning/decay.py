"""One decay kernel, three profiles — how learned things lose their grip.

Three decay implementations existed, each locally reasonable and mutually
incompatible: facet stability (`preference_facets.decay`, a clean half-life),
memory heat (`memory_record.heat`, `0.7·log1p(visits)/ln10 + 0.5·e^(−days/30)` — a
*combined* usage-and-recency score), and the curator's day-threshold ladder
(30d stale / 90d archived, no curve at all). Three answers to "is this still
relevant?" means three different answers for the same entity depending on which
code path asked.

This is the one kernel. Its form:

    strength = exp(−baseλ × entityMultiplier × activeDaysSinceUse)

**Importance is a second axis, not an exemption.** It modulates λ by
`(1 − importance·0.8)` rather than pinning strength to 1.0. An important thing
decays *slower*; it does not decay *never*. Exemption is what produces a library
full of things marked important once and never revisited.

**Pruning needs BOTH low strength and low importance.** Either alone is a false
positive: a critical runbook consulted twice a year is low-strength and must
survive, while a trivial note touched constantly is high-strength and worth
dropping when it stops being touched.

**Strength never enters surfacing rank.** It gates eviction and review only. This
is doctrine, not preference: if strength ranked results, a thing would surface
because it surfaced recently — a feedback loop that buries anything not already
popular, which is the opposite of what a personal system should do.

**The clock counts ACTIVE days.** Wall-clock decay punishes a single user for
taking a holiday: come back after three weeks and the whole library has gone
stale without a single decision being made about it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Base decay rate — the λ before any per-kind multiplier. Tuned so an entity with
#: multiplier 1.0 and no importance falls to ~0.5 strength after 30 active days,
#: matching the half-life the facet store already used (this kernel replaces it, so
#: the numbers have to line up or every existing facet shifts meaning on upgrade).
BASE_LAMBDA = math.log(2) / 30.0

#: Per-kind multipliers. Higher decays faster. The spread encodes what endures: a
#: strategy that worked is still worth knowing next year, while a specific failure
#: is usually about a version of the world that no longer exists.
KIND_MULTIPLIERS: dict[str, float] = {
    "strategy": 0.4,  # endures — how to approach a class of problem
    "preference": 0.5,  # the user's tastes change slowly
    "lesson": 0.7,
    "skill": 1.0,  # the reference point
    "template": 1.0,
    "procedural": 1.2,  # tool-level priors churn with tooling
    "failure": 2.0,  # goes stale fast — usually about a world that moved on
    "speculative": 3.0,  # a hedged claim decays fastest of all
}

#: How much importance can slow decay. At importance 1.0, λ is cut to 20% — five
#: times slower, still not zero.
IMPORTANCE_DAMPING = 0.8

#: Strength below this is a candidate for eviction (given low importance too).
PRUNE_STRENGTH = 0.15
#: Importance above this protects an entity from pruning regardless of strength.
PRUNE_IMPORTANCE = 0.5

#: A reinforcement within this window counts at HALF value. Ten retrievals in one
#: minute is one act of attention, and counting it ten times inflates heat enough
#: to distort every comparison against it.
REINFORCE_WINDOW_SECS = 3600.0
REINFORCE_DAMPED_WEIGHT = 0.5

#: Strength this low with stability this high is the interesting case: something the
#: system is confident about that nobody uses. That is a REVIEW decision, not a
#: silent archival — the confidence is evidence the user may want it back.
REVIEW_STRENGTH_MAX = 0.25
REVIEW_STABILITY_MIN = 0.6


@dataclass(frozen=True)
class DecayVerdict:
    """What the kernel concluded about one entity."""

    strength: float
    prune: bool
    review: bool
    reason: str

    def __bool__(self) -> bool:
        """Truthy when the entity is still healthy (neither pruned nor flagged)."""
        return not (self.prune or self.review)


def effective_lambda(kind: str, importance: float = 0.0) -> float:
    """The decay rate for one entity: base × kind multiplier × importance damping."""
    multiplier = KIND_MULTIPLIERS.get(kind, 1.0)
    damped = 1.0 - max(0.0, min(1.0, importance)) * IMPORTANCE_DAMPING
    return BASE_LAMBDA * multiplier * max(0.05, damped)


def strength(
    *,
    kind: str,
    active_days_since_use: float,
    importance: float = 0.0,
) -> float:
    """Current strength in (0, 1]. Pure — no clock, no store, no I/O.

    Takes ACTIVE days rather than a timestamp: the caller owns the calendar, which
    is what makes this testable without freezing time and what lets the active-days
    clock exist at all.
    """
    days = max(0.0, float(active_days_since_use))
    return math.exp(-effective_lambda(kind, importance) * days)


def reinforcement_weight(secs_since_last: float) -> float:
    """How much this reinforcement counts: 1.0, or 0.5 inside the damping window."""
    if secs_since_last < 0:
        return 1.0
    return REINFORCE_DAMPED_WEIGHT if secs_since_last < REINFORCE_WINDOW_SECS else 1.0


def evaluate(
    *,
    kind: str,
    active_days_since_use: float,
    importance: float = 0.0,
    stability: float = 0.0,
    pinned: bool = False,
    source_type: str = "agent",
    linked_neighbors: int = 0,
) -> DecayVerdict:
    """Decide an entity's fate. The single entry point for eviction and review.

    Four things can spare an entity, and each exists for a different reason:

    - **pinned** — an explicit user decision, which outranks any inference;
    - **user-authored** — the curator may age agent-created content only; deleting
      what the user wrote themselves is not curation, it is data loss;
    - **importance** — a second axis, not an exemption (see the module docstring);
    - **a strongly-linked neighbour** — evicting one end of a chain leaves the other
      pointing at nothing, which is worse than keeping a cold entry.
    """
    s = strength(kind=kind, active_days_since_use=active_days_since_use, importance=importance)

    if pinned:
        return DecayVerdict(s, False, False, "pinned")
    if source_type == "user":
        return DecayVerdict(s, False, False, "user_authored")

    if s <= REVIEW_STRENGTH_MAX and stability >= REVIEW_STABILITY_MIN:
        # Confident but unused: the confidence is itself evidence the user may want
        # this back, so it becomes a review proposal rather than a silent archival.
        return DecayVerdict(s, False, True, "decayed_but_stable")

    if s < PRUNE_STRENGTH and importance < PRUNE_IMPORTANCE:
        if linked_neighbors > 0:
            return DecayVerdict(s, False, False, "chain_spared")
        return DecayVerdict(s, True, False, "low_strength_low_importance")

    return DecayVerdict(s, False, False, "healthy")


# ── The active-days clock ──


def active_days_between(active_dates: list[str], since: str, until: str | None = None) -> float:
    """Count days the user was actually present between two timestamps.

    Vacation-proof by construction: a fortnight away contributes zero decay,
    because for a single-user system "time passed" and "the user moved on" are very
    different claims and only the second should age anything.
    """
    start = _parse(since)
    if start is None:
        return 0.0
    end = _parse(until) if until else datetime.now(timezone.utc)
    if end is None or end <= start:
        return 0.0
    start_day, end_day = start.date(), end.date()
    count = 0
    for raw in active_dates:
        day = _parse_date(raw)
        if day is not None and start_day < day <= end_day:
            count += 1
    return float(count)


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(raw: str):
    parsed = _parse(raw if "T" in str(raw) else f"{raw}T00:00:00+00:00")
    return parsed.date() if parsed else None
