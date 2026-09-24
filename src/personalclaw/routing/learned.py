"""Learned ordering — the ``learned`` mode's scoring stage (MODEL-ROUTING-TELEMETRY §4.2, MRT-5).

``policy.MODES`` has carried ``"learned"`` since MRT-4, but with no scoring stage behind it the
mode folded onto the heuristic floor (``policy.py`` docstring, and the comment at its ordering
site). This module is that stage: given the fold ``stats.py`` maintains, it reorders the candidate
refs for one ``(use_case, query_class)`` request.

Four semantics, each deliberate:

**A confidence floor, and what "no opinion" means.** A ref with fewer than ``min_samples``
recorded calls (config default 5) has no learned opinion, so it must be neither promoted NOR
demoted on that noise. It therefore keeps its incoming SLOT: the stage permutes only the refs that
do have an opinion, among the positions those refs already occupied, and every sub-threshold ref
stays exactly where the previous lever (the bound order, or the heuristic that ran before this
stage) put it. Demoting it instead would be a demotion on absent evidence, and promoting it would
let a single lucky call outrank a long measured record — the config prose for ``min_samples`` says
it plainly: below the floor "the simple local-first rule stands". With fewer than two opinions
there is nothing to compare, so the input is returned untouched.

**Hysteresis is a band, not a tie-break.** Two refs whose scores differ by less than
``hysteresis`` are *near-equal*: that difference is noise and must not reorder them, which is
exactly the flip-flopping the knob exists to prevent. So refs are grouped into bands, and inside a
band the incoming order stands — score differences are ignored there — with cost as the only thing
allowed to move a member. A band is anchored at its BEST score, so its width is exactly
``hysteresis``; grouping by "within hysteresis of the previous member" instead would chain
(0.90 → 0.86 → 0.82 …) and let a cheap, meaningfully worse ref overtake a better one, which is the
knob backwards — quality traded for pennies on every call.

**The cloud margin is asymmetric on purpose.** Local-first is the posture, so a cloud ref must
EARN its place: its score carries a ``cloud_quality_margin`` penalty, and a merely equal cloud ref
therefore loses to local. The penalty is applied before banding, so a cloud ref that beats local by
only a hair more than the margin lands in the same band as it and still does not jump — deliberately
conservative in the one direction of error that costs the user money and privacy.

**Degradation is a clause.** An empty, missing, partial or corrupt fold yields no opinions and the
refs come back unchanged, so the caller falls through to the heuristic — which is the permanent
below-confidence-floor floor, not a failure mode. Bad values are handled by the scoring path
itself; the ``except`` at the end is a fail-safe that logs when it fires, because a stage that
leans on someone else's ``except Exception`` is a stage whose failures are invisible.

The function is pure: no file, config, network or clock access — everything arrives as an argument,
including the cost lookup (injected, so a missing price cannot break a routing decision and the
rate table is not a dependency of an ordering decision).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from personalclaw.routing import stats as _stats

logger = logging.getLogger(__name__)

#: Logged only when the fail-safe (rather than the scoring path) handled a bad fold. Tests assert
#: on its absence: graceful degradation must not route through the panic catch.
_FAILSAFE_MSG = "learned_order failed — keeping the incoming order"


def _num(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` when it is not a number at all.

    ``bool`` is rejected: ``True`` is not a sample count. This is where a corrupt fold entry (a
    string where a number belongs) becomes "no opinion" instead of an exception.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _rows_for(stats: Any, use_case: str, query_class: str) -> dict[str, Any]:
    """The ``{ref: row}`` leaf of the fold for one bucket, or ``{}`` for anything unexpected.

    The fold's shape, as ``stats.fold_record`` writes it:
    ``stats["use_cases"][use_case][query_class][ref] -> row``.
    """
    if not isinstance(stats, dict):
        return {}
    buckets = stats.get("use_cases")
    if not isinstance(buckets, dict):
        return {}
    by_class = buckets.get(use_case)
    if not isinstance(by_class, dict):
        return {}
    rows = by_class.get(query_class)
    return rows if isinstance(rows, dict) else {}


def _opinion(row: Any, *, min_samples: int) -> float | None:
    """This ref's learned score, or ``None`` when the fold has no opinion about it.

    Scoring is delegated to :func:`stats._score` — the one place the 0.60/0.40 weighting and its
    no-feedback renormalization live. Recomputing from the row's components rather than reading the
    stored ``score`` field means a weight change takes effect without a refold, and there is never
    a second answer to disagree with the first.
    """
    if not isinstance(row, dict):
        return None
    samples = _num(row.get("n"))
    if samples is None or samples < min_samples:
        return None
    success_rate = _num(row.get("success_rate"))
    feedback = _num(row.get("feedback", 0.0))
    feedback_n = _num(row.get("feedback_n", 0))
    if success_rate is None or feedback is None or feedback_n is None:
        return None
    return _stats._score(success_rate, feedback, int(feedback_n))


def _bands(by_score: list[int], scores: dict[int, float], width: float) -> list[list[int]]:
    """Group score-descending positions into near-equal bands anchored at each band's BEST score.

    Anchoring (rather than chaining off the previous member) bounds a band's width at exactly
    ``width``, so cost can never move a ref past one that is meaningfully better.
    """
    bands: list[list[int]] = []
    for index in by_score:
        if bands and scores[bands[-1][0]] - scores[index] <= width:
            bands[-1].append(index)
        else:
            bands.append([index])
    return bands


def _cost_order(
    band: list[int], refs: list[str], cost_of: Callable[[str], float] | None
) -> list[int]:
    """Order one band: the incoming order, with cost the only thing allowed to move a member.

    Score differences inside a band are noise and are ignored here — that is what makes hysteresis
    a band rather than a tie-break. A ref whose price is unknown (no table entry, or a lookup that
    raises) keeps its slot, so a missing price degrades to "no cost opinion" for that ref alone.
    """
    incoming = sorted(band)
    if cost_of is None or len(incoming) < 2:
        return incoming
    priced: list[tuple[int, float]] = []
    for slot, index in enumerate(incoming):
        try:
            cost = _num(cost_of(refs[index]))
        except Exception:  # noqa: BLE001 — a missing price must never break a routing decision
            cost = None
        if cost is not None:
            priced.append((slot, cost))
    if len(priced) < 2:
        return incoming
    cheapest = [slot for slot, _ in sorted(priced, key=lambda pair: (pair[1], pair[0]))]
    ordered = list(incoming)
    for target, source in zip([slot for slot, _ in priced], cheapest):
        ordered[target] = incoming[source]
    return ordered


def learned_order(
    refs: list[str],
    *,
    use_case: str,
    query_class: str,
    stats: dict,
    hysteresis: float,
    cloud_quality_margin: float,
    local_keys: set[str],
    cost_of: Callable[[str], float] | None = None,
    min_samples: int = 5,
) -> list[str]:
    """Refs reordered by learned score. Returns them UNCHANGED when the fold cannot decide.

    ``stats`` is a loaded ``routing_stats.json`` fold; ``local_keys`` are normalized local-provider
    keys as ``policy._local_provider_keys()`` produces them; ``cost_of`` maps a ref to dollars for
    the within-band comparison and may be omitted (then cost never reorders anything).

    The result is always a permutation of ``refs`` — never a different set, never a different
    length — and the same inputs always produce the same output.
    """
    ordered = list(refs)
    if len(ordered) < 2:
        return ordered
    try:
        rows = _rows_for(stats, use_case, query_class)
        if not rows:
            return ordered
        # Lazy import: ``policy`` imports this module, and re-deriving local-vs-cloud here would
        # mint a second classifier that could disagree with the one the rest of routing uses.
        from personalclaw.routing.policy import is_local_ref

        margin = max(0.0, float(cloud_quality_margin))
        width = max(0.0, float(hysteresis))
        keys = set(local_keys)

        scores: dict[int, float] = {}
        for index, ref in enumerate(ordered):
            opinion = _opinion(rows.get(ref), min_samples=min_samples)
            if opinion is None:
                continue  # no opinion — this slot is frozen (see the module docstring)
            if not is_local_ref(ref, local_keys=keys):
                opinion -= margin  # asymmetric: cloud must beat local BY the margin
            scores[index] = opinion
        if len(scores) < 2:
            return ordered  # fewer than two opinions — nothing to compare

        by_score = sorted(scores, key=lambda index: (-scores[index], index))
        ranked: list[int] = []
        for band in _bands(by_score, scores, width):
            ranked.extend(_cost_order(band, ordered, cost_of))

        result = list(ordered)
        for slot, index in zip(sorted(scores), ranked):
            result[slot] = ordered[index]
        if sorted(result) != sorted(ordered):
            return list(refs)  # never return a different set (mirrors ``policy._stable_by``)
        return result
    except Exception:  # noqa: BLE001 — a routing decision must never fail a resolution
        logger.debug(_FAILSAFE_MSG, exc_info=True)
        return list(refs)
