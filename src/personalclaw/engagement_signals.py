"""Engagement-weighted personalization ranking — the signal store.

A consumer-agnostic per-topic engagement multiplier: caller records signals against an
open-vocabulary ``topic_key`` (inbox channel/sender/classification, digest tags, …) and
reads back ``weight_for(topic_key, now) -> float`` — a recency-multiplier that boosts
topics the user engages with (favorite/open/reply) and floors ones they dismiss.

Design (per the plan):
* **Read-time decay, no ticker.** Stored weight decays via the SHARED
  :func:`~personalclaw.preference_facets.decay` kernel (VISION's one-decay-machinery) at
  ~10%/day ⇒ half-life ≈ 6.58d (``ln0.5/ln0.9``). No background task mutates stored state.
* **Warm-up neutral.** A topic with fewer than ``_WARMUP_SIGNALS`` signals returns 1.0
  (cold-start falls back to pure recency — never penalizes an unseen topic).
* **Additive + capped**, and **dismiss FLOORs** (never permanently buries a channel).
* **Consumer-agnostic**: this store knows nothing about inbox/digest — a caller multiplies
  its own recency score by ``weight_for(...)``. Persistence mirrors ``InboxStore``
  (a small JSON under ``config_dir()``, atomic-written).

This is P11 steps 1-2 (store + kernel reuse) — pure infrastructure, gated off by default
(``engagement_ranking_enabled``); the signal-capture wiring + consumers (steps 3-6) layer
on top without touching this file.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.preference_facets import decay

logger = logging.getLogger(__name__)

_STATE_FILE = "engagement.json"

# ~10%/day retention ⇒ half-life = ln(0.5)/ln(0.9) ≈ 6.58 days (the plan's target).
DEFAULT_HALF_LIFE_DAYS = math.log(0.5) / math.log(0.9)

# Per-signal weight deltas (additive onto the stored weight, then clamped to the band).
# favorite = strong boost, open/reply = moderate, dismiss = negative (floored, not buried).
_SIGNAL_DELTA: dict[str, float] = {
    "favorite": 0.6,
    "open": 0.2,
    "reply": 0.3,
    "dismiss": -0.4,
}
# Weight band: never below the floor (a dismissed topic still surfaces, just lower) and
# never runaway-high (a hot topic can't drown everything else).
_WEIGHT_FLOOR = 0.3
_WEIGHT_CEIL = 3.0
_NEUTRAL = 1.0
_WARMUP_SIGNALS = 2  # < this many signals → return neutral 1.0 (cold-start = recency)


class EngagementStore:
    """Per-topic engagement weights with read-time half-life decay. Consumer-agnostic:
    ``record(topic_key, signal)`` accumulates; ``weight_for(topic_key, now=...)`` reads
    the decayed multiplier (neutral until warm-up). JSON-persisted under ``config_dir()``."""

    def __init__(self, path: Path | None = None, *, half_life_days: float | None = None) -> None:
        self._path = path or (config_dir() / _STATE_FILE)
        self._half_life = (
            half_life_days if (half_life_days and half_life_days > 0) else DEFAULT_HALF_LIFE_DAYS
        )
        # topic_key → {weight: float, updated_at: epoch_secs, count: int}
        self._rows: dict[str, dict] = {}

    # ── persistence (mirrors InboxStore) ────────────────────────────────────────
    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                rows = data.get("rows", {}) if isinstance(data, dict) else {}
                self._rows = {str(k): v for k, v in rows.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load engagement state, starting fresh")
                self._rows = {}

    def save(self) -> None:
        try:
            atomic_write(self._path, json.dumps({"rows": self._rows}, indent=2), mode=0o600)
        except OSError:
            logger.warning("Failed to save engagement state")

    # ── record / read ───────────────────────────────────────────────────────────
    def record(self, topic_key: str, signal: str, *, now: float) -> None:
        """Apply a signal to a topic (additive, clamped). Unknown signals are ignored.
        ``now`` is epoch seconds (injected — no wall-clock read here, for testability)."""
        delta = _SIGNAL_DELTA.get(signal)
        if delta is None or not topic_key:
            return
        row = self._rows.get(topic_key)
        # Decay the stored weight toward neutral to `now` BEFORE applying the delta, so
        # accumulation reflects current standing (an old boost has faded by the time a new
        # signal lands). Decay pulls the *deviation from neutral*, not the raw weight.
        cur = self._decayed_weight(row, now) if row else _NEUTRAL
        updated = _clamp(cur + delta, _WEIGHT_FLOOR, _WEIGHT_CEIL)
        self._rows[topic_key] = {
            "weight": updated,
            "updated_at": now,
            "count": int(row.get("count", 0)) + 1 if row else 1,
        }

    def weight_for(self, topic_key: str, *, now: float) -> float:
        """Read-time decayed multiplier for a topic. Neutral 1.0 during warm-up (< N
        signals) or for an unknown topic — so a cold topic ranks on pure recency."""
        row = self._rows.get(topic_key)
        if not row or int(row.get("count", 0)) < _WARMUP_SIGNALS:
            return _NEUTRAL
        return self._decayed_weight(row, now)

    def _decayed_weight(self, row: dict, now: float) -> float:
        """Decay the stored weight's DEVIATION from neutral toward 1.0 by half-life. A
        weight of 1.0 (neutral) never moves; boosts/penalties fade back to neutral."""
        try:
            weight = float(row.get("weight", _NEUTRAL))
            age_days = max(0.0, (now - float(row.get("updated_at", now)))) / 86400.0
        except (TypeError, ValueError):
            return _NEUTRAL
        deviation = weight - _NEUTRAL
        return _NEUTRAL + decay(deviation, age_days, self._half_life)

    def prune(self, *, now: float, min_deviation: float = 0.02) -> int:
        """Drop topics that have decayed back to ~neutral (free storage). Returns count
        pruned. Safe to call from an existing maintenance block (no new scheduler)."""
        stale = [
            k
            for k, r in self._rows.items()
            if abs(self._decayed_weight(r, now) - _NEUTRAL) < min_deviation
        ]
        for k in stale:
            del self._rows[k]
        return len(stale)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def rank_by_engagement(items, *, recency_key, topic_key, store, now):
    """Stably re-rank ``items`` by ``recency_score × weight_for(topic)`` — the ONE blend
    the inbox sort uses today (and the deferred digest-ordering consumer will reuse), so a
    second consumer never inlines its own recency×weight math (no dual paths). Pure: no I/O
    beyond the store reads.

    ``recency_key(item) -> float`` (higher = more recent, the current baseline sort key),
    ``topic_key(item) -> str`` (the engagement topic — inbox channel/sender/classification,
    or a digest tag). Returns a new list, highest-ranked first. A warm-up/unknown topic has
    weight 1.0 → pure recency, so this degrades to the current behavior on cold start."""

    def _score(item):
        base = recency_key(item)
        tk = topic_key(item)
        w = store.weight_for(tk, now=now) if tk else _NEUTRAL
        return base * w

    # Sort by blended score desc; Python's sort is stable so equal scores keep input order.
    return sorted(items, key=_score, reverse=True)
