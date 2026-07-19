"""The granularity dial — how far an open-ended loop chases diminishing returns.

The dial maps to a marginal-value threshold ``T`` and a patience window ``N``
(§5.2). The supervisor (deterministic) reads the judge's marginal-value trail:
when the last ``N`` cycles all scored below ``T``, returns are exhausted and the
loop completes. ``forever`` disables value-based self-stop entirely (§5.5).

| Dial       | T (min marginal) | N (window) | Meaning                                |
|------------|------------------|------------|----------------------------------------|
| quick      | 3.0              | 1          | Stop as soon as a cycle stops adding.  |
| balanced   | 2.0              | 2          | Stop when gains shrink for a couple.   |
| exhaustive | 1.0              | 3          | Keep going until truly dry.            |
| forever    | — (disabled)     | —          | Never self-stop on value.              |
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev

# Minimum trail length before the calibrated band trusts its own variance estimate.
# Below this, σ from a handful of samples is noise, so the caller falls back to the
# fixed dial threshold (``returns_exhausted``). Four is the smallest trail that gives
# a meaningful population stdev without over-fitting to two or three cycles.
_MIN_REPS = 4


@dataclass(frozen=True)
class DialSetting:
    threshold: float  # minimum marginal-value score a cycle must clear
    window: int       # consecutive sub-threshold cycles before returns-exhausted


_DIAL: dict[str, DialSetting | None] = {
    "quick": DialSetting(threshold=3.0, window=1),
    "balanced": DialSetting(threshold=2.0, window=2),
    "exhaustive": DialSetting(threshold=1.0, window=3),
    "forever": None,  # value-based self-stop disabled
}


def dial_for(granularity: str) -> DialSetting | None:
    """The threshold+window for a granularity, or None when self-stop is disabled."""
    return _DIAL.get(granularity, _DIAL["balanced"])


def returns_exhausted(marginal_scores: list[float], granularity: str) -> bool:
    """True iff the last ``N`` marginal scores are all below the dial's threshold.

    ``forever`` never exhausts. A trail shorter than the window never exhausts
    (not enough evidence yet).
    """
    setting = dial_for(granularity)
    if setting is None:
        return False
    if len(marginal_scores) < setting.window:
        return False
    recent = marginal_scores[-setting.window:]
    return all(s < setting.threshold for s in recent)


def calibrated_band(scores: list[float], floor: float) -> float:
    """The noise floor of a marginal-value trail: ``max(2σ, floor)`` (P4 "prove-the-instrument").

    A gain smaller than twice the trail's own variance is indistinguishable from noise. This
    is a **diagnostic** of how jittery the signal is (surfaced on the verdict as ``band_used``
    and available for progress-vs-noise reasoning); the exhaustion decision itself uses the
    patience-window widening in :func:`returns_exhausted_calibrated`, not a raised per-cycle
    bar (raising the bar would make low cycles *more* below it — the wrong direction for
    detecting the ABSENCE of progress). With a flat trail (σ≈0) the floor is returned unchanged.
    """
    if len(scores) < 2:
        return floor
    return max(2.0 * pstdev(scores), floor)


def _noise_patience(scores: list[float], setting: DialSetting) -> int:
    """Extra sub-threshold cycles required before exhaustion, sized by trail noise.
    0 when the signal is calm; grows as population-σ rises relative to the dial threshold.
    This is what makes a noisy loop *more patient* (harder to stop on a variance dip)."""
    if len(scores) < 2 or setting.threshold <= 0:
        return 0
    return int(pstdev(scores) / setting.threshold)


def returns_exhausted_calibrated(marginal_scores: list[float], granularity: str) -> bool:
    """Variance-aware returns-exhaustion (P4). Same absolute bar as :func:`returns_exhausted`
    (the recent window must all be below the dial threshold — bounded, dial meaning intact),
    but a NOISY signal must show a **longer run** of sub-threshold cycles before the loop
    trusts that returns are exhausted. A couple of low cycles amid a jittery trail is likely a
    variance dip, not genuine exhaustion, so the patience window widens with the trail's σ.

    Falls back to the plain fixed-threshold :func:`returns_exhausted` until the trail reaches
    ``_MIN_REPS`` (σ from <4 samples is unreliable). ``forever`` never exhausts.
    """
    setting = dial_for(granularity)
    if setting is None:
        return False
    if len(marginal_scores) < _MIN_REPS:
        return returns_exhausted(marginal_scores, granularity)
    eff_window = setting.window + _noise_patience(marginal_scores, setting)
    if len(marginal_scores) < eff_window:
        return False  # not enough consecutive low evidence yet under this noise level
    recent = marginal_scores[-eff_window:]
    return all(s < setting.threshold for s in recent)
