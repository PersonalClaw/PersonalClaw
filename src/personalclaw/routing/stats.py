"""Rolling routing-stats fold — ``routing_stats.json`` (MODEL-ROUTING-TELEMETRY §1.3, MRT-1c).

A router must not scan ``model_calls.jsonl`` per call, so this maintains an incremental fold keyed
``(use_case → query_class → "provider:model_id" ref)``, updated by the same code path that appends
each attempt audit line. The aggregates are conservative online estimates (exponential moving
averages with a small alpha, per the plan) so one bad night never flips a policy; ``n`` counts
total samples for the downstream confidence floor.

Per (use_case, query_class, ref) the fold keeps: ``n``, ``success_rate`` (EMA of ``passed``),
``feedback`` + ``feedback_n`` (EMA of a [0,1] signal — 0 with feedback_n=0 until the Session-3
feedback extraction lands; the score then collapses onto success_rate, renormalized), ``avg_ms``
(EMA latency), ``avg_cost_usd`` (EMA dollars), ``score`` (§4.2: 0.60·success + 0.40·feedback,
renormalized to success when no feedback yet), and ``updated_at``.

**Deviation from the §1.3 JSON example (documented):** the example shows ``p50_ms``/``p95_ms``
in the fold, but true percentiles can't be maintained incrementally from an EMA. Per §1.5 the
telemetry route (MRT-1d) derives per-model rows "from routing_stats.json + a bounded tail of
``model_calls.jsonl``", so p50/p95 are a READ-TIME derivation there; the fold keeps ``avg_ms``.
This keeps the fold a true O(1) online update, not a growing per-ref latency reservoir.

The fold is rebuildable (:func:`rebuild`) from the (capped/rotated) JSONL, so the fold is the
durable long-horizon record and the JSONL the recent forensic one. Writing is best-effort and
never raises —
a stats-fold failure must not break a model call (the call is the product; this is observability).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: File under the home; small JSON, atomic_write (the universal convention).
_STATS_FILE = "routing_stats.json"
#: Bump when the fold's schema changes. Mirrors the classifier version so a consumer can tell
#: which vocabulary the buckets were folded under.
STATS_VERSION = 1
#: EMA smoothing. Small so a single outlier attempt barely moves an established rate.
_ALPHA = 0.2
#: Scoring weights (§4.2). Feedback collapses onto success_rate (renormalized) when feedback_n=0.
_W_SUCCESS = 0.60
_W_FEEDBACK = 0.40


def ref_of(provider: str, model: str) -> str:
    """The ``active_models.json``-spelling ref for a (provider, model): joined on the first
    colon so a colon-bearing model id (``gpt-oss:20b``) round-trips as ``provider:gpt-oss:20b``."""
    return f"{provider}:{model}"


def _ema(old: float, new: float, alpha: float = _ALPHA) -> float:
    return (1.0 - alpha) * old + alpha * new


def _score(success_rate: float, feedback: float, feedback_n: int) -> float:
    """0.60·success + 0.40·feedback, but with NO feedback yet the feedback weight collapses onto
    success_rate (renormalized) so an unrated ref isn't penalized for a signal it can't have."""
    if feedback_n <= 0:
        return round(success_rate, 4)
    return round(_W_SUCCESS * success_rate + _W_FEEDBACK * feedback, 4)


def _stats_path(home: Path) -> Path:
    return Path(home) / _STATS_FILE


def load_stats(home: Path) -> dict[str, Any]:
    """Read the fold. A missing/corrupt file reads as an empty fold (never fatal)."""
    try:
        data = json.loads(_stats_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {"version": STATS_VERSION, "use_cases": {}}
    if not isinstance(data, dict):
        return {"version": STATS_VERSION, "use_cases": {}}
    data.setdefault("version", STATS_VERSION)
    data.setdefault("use_cases", {})
    return data


def save_stats(home: Path, stats: dict[str, Any]) -> None:
    atomic_write(_stats_path(home), json.dumps(stats, indent=2, sort_keys=True) + "\n")


def fold_record(stats: dict[str, Any], rec: dict[str, Any], *, now: str = "") -> dict[str, Any]:
    """Fold one attempt row (an ``AttemptRecord.to_json_line`` dict) into ``stats`` in place.

    Keyed by ``(use_case, query_class, ref)``. A row missing a ``use_case`` or ``query_class`` (an
    unclassified call — routing can't attribute it to a class) is SKIPPED. ``feedback`` is not yet
    on the audit row (Session 3 wires it), so it stays 0/feedback_n=0 and the score collapses onto
    success_rate. Returns ``stats`` for chaining.
    """
    use_case = str(rec.get("use_case", "") or "")
    query_class = str(rec.get("query_class", "") or "")
    if not use_case or not query_class:
        return stats  # nothing to attribute per (use_case, query_class)
    ref = ref_of(str(rec.get("provider", "")), str(rec.get("model", "")))
    if ref == ":":
        return stats

    buckets = stats.setdefault("use_cases", {})
    by_class = buckets.setdefault(use_case, {}).setdefault(query_class, {})
    row = by_class.get(ref)
    passed = 1.0 if rec.get("passed") else 0.0
    latency = float(rec.get("latency_ms", 0.0) or 0.0)
    cost = float(rec.get("dollars_est", 0.0) or 0.0)

    if row is None:
        # First sample seeds the EMAs with the observed values (no prior to blend).
        row = {
            "n": 0,
            "success_rate": passed,
            "feedback": 0.0,
            "feedback_n": 0,
            "avg_ms": latency,
            "avg_cost_usd": cost,
        }
    row["n"] = int(row.get("n", 0)) + 1
    if row["n"] == 1:
        row["success_rate"] = passed
        row["avg_ms"] = latency
        row["avg_cost_usd"] = cost
    else:
        row["success_rate"] = round(_ema(float(row["success_rate"]), passed), 4)
        row["avg_ms"] = round(_ema(float(row["avg_ms"]), latency), 1)
        row["avg_cost_usd"] = round(_ema(float(row["avg_cost_usd"]), cost), 6)
    row["score"] = _score(
        float(row["success_rate"]), float(row.get("feedback", 0.0)), int(row.get("feedback_n", 0))
    )
    row["updated_at"] = now
    by_class[ref] = row
    return stats


def record_routing_stats(rec: dict[str, Any], *, home: Path, now: str = "") -> None:
    """Fold one attempt into the on-disk stats — the post-attempt hook the audit path calls.

    Best-effort and never raises (mirrors ``record_attempt``): a fold failure must not break a
    model call. Load → fold → save; a lost update on a rare concurrent write self-heals on the
    next fold and on :func:`rebuild`."""
    try:
        stats = load_stats(home)
        stats["version"] = STATS_VERSION
        fold_record(stats, rec, now=now)
        save_stats(home, stats)
    except Exception:  # noqa: BLE001 — observability must never break the call
        logger.warning("routing stats fold failed", exc_info=True)


def rebuild(home: Path, audit_path: Path | None = None) -> int:
    """Refold ``routing_stats.json`` from scratch over ``model_calls.jsonl``.

    The JSONL is capped/rotated (the fold is the durable long-horizon record), so this recovers the
    fold from whatever forensic tail remains — the ``--rebuild-routing-stats`` maintenance path.
    Returns the number of attempt rows folded. Rows are folded in file order so the EMA reflects
    recency the same way the live fold does.
    """
    if audit_path is None:
        from personalclaw.guardrails.audit import _audit_path

        audit_path = _audit_path()
    stats: dict[str, Any] = {"version": STATS_VERSION, "use_cases": {}}
    folded = 0
    try:
        text = Path(audit_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        save_stats(home, stats)
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        before = json.dumps(stats, sort_keys=True)
        fold_record(stats, rec, now=str(rec.get("ts", "")))
        if json.dumps(stats, sort_keys=True) != before:
            folded += 1
    save_stats(home, stats)
    return folded
