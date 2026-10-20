"""Rolling routing-stats fold — ``routing_stats.json``.

A router must not scan ``model_calls.jsonl`` per call, so this maintains an incremental fold keyed
``(use_case → query_class → "provider:model_id" ref)``, updated by the same code path that appends
each attempt audit line. The aggregates are conservative online estimates (exponential moving
averages with a small alpha) so one bad night never flips a policy; ``n`` counts
total samples for the downstream confidence floor.

Per (use_case, query_class, ref) the fold keeps: ``n``, ``success_rate`` (EMA of ``passed``),
``feedback`` + ``feedback_n`` (EMA of a [0,1] signal — 0 with feedback_n=0 while no feedback
reaches the fold; the score then collapses onto success_rate, renormalized), ``avg_ms``
(EMA latency), ``avg_cost_usd`` + ``priced_n`` (EMA of what a call the ref served cost, over the
``priced_n`` calls something priced — with none, the ref has no price yet, which is not free),
``score`` (0.60·success + 0.40·feedback, renormalized to success when no feedback yet), and
``updated_at``.

**Why the fold holds no percentiles:** ``p50_ms``/``p95_ms`` would be the obvious fold fields,
but true percentiles can't be maintained incrementally from an EMA. The telemetry route derives
per-model rows from ``routing_stats.json`` + a bounded tail of ``model_calls.jsonl``, so
p50/p95 are a READ-TIME derivation there; the fold keeps ``avg_ms``.
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
from personalclaw.guardrails.audit import row_priced

logger = logging.getLogger(__name__)

#: File under the home; small JSON, atomic_write (the universal convention).
_STATS_FILE = "routing_stats.json"
#: Bump when the fold's schema changes. Mirrors the classifier version so a consumer can tell
#: which vocabulary the buckets were folded under.
STATS_VERSION = 1
#: EMA smoothing. Small so a single outlier attempt barely moves an established rate.
_ALPHA = 0.2
#: Scoring weights. Feedback collapses onto success_rate (renormalized) when feedback_n=0.
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


def priced_samples(row: dict[str, Any]) -> int:
    """How many of a fold row's calls its ``avg_cost_usd`` averages, the calls something priced.

    Zero means the ref has no price yet. A row folded before the fold counted them is read by what
    it carries: a positive average was folded from priced calls, and a zero one says nothing,
    since the calls nothing priced were folded in at $0 then.
    """
    if "priced_n" in row:
        try:
            return max(0, int(row.get("priced_n") or 0))
        except (TypeError, ValueError):
            return 0
    try:
        positive = float(row.get("avg_cost_usd", 0.0) or 0.0) > 0.0
    except (TypeError, ValueError):
        return 0
    return int(row.get("n", 0) or 0) if positive else 0


def save_stats(home: Path, stats: dict[str, Any]) -> None:
    atomic_write(_stats_path(home), json.dumps(stats, indent=2, sort_keys=True) + "\n")


def fold_record(stats: dict[str, Any], rec: dict[str, Any], *, now: str = "") -> dict[str, Any]:
    """Fold one attempt row (an ``AttemptRecord.to_json_line`` dict) into ``stats`` in place.

    Keyed by ``(use_case, query_class, ref)``. A row missing a ``use_case`` or ``query_class`` (an
    unclassified call — routing can't attribute it to a class) is SKIPPED. ``feedback`` is not
    on the audit row, so it stays 0/feedback_n=0 and the score collapses onto
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
    # A price of this ref is what a call it SERVED cost, when something priced it. A refusal or a
    # failure says nothing about the price of the model's calls, and an unpriced call's $0 is no
    # price at all: folded in, either made a model nothing prices read as free.
    priced = bool(passed) and row_priced(rec)

    if row is None:
        # First sample seeds the EMAs with the observed values (no prior to blend).
        row = {
            "n": 0,
            "success_rate": passed,
            "feedback": 0.0,
            "feedback_n": 0,
            "avg_ms": latency,
            "avg_cost_usd": 0.0,
            "priced_n": 0,
        }
    priced_n = priced_samples(row)
    row["n"] = int(row.get("n", 0)) + 1
    if row["n"] == 1:
        row["success_rate"] = passed
        row["avg_ms"] = latency
    else:
        row["success_rate"] = round(_ema(float(row["success_rate"]), passed), 4)
        row["avg_ms"] = round(_ema(float(row["avg_ms"]), latency), 1)
    if priced:
        priced_n += 1
        # The first price seeds the cost EMA: a ref with none had no cost to blend with.
        row["avg_cost_usd"] = (
            round(cost, 6)
            if priced_n == 1
            else round(_ema(float(row.get("avg_cost_usd", 0.0) or 0.0), cost), 6)
        )
    row["priced_n"] = priced_n
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
        return
    _check_for_gap(stats, rec, home=home)


def _check_for_gap(stats: dict[str, Any], rec: dict[str, Any], *, home: Path) -> None:
    """New evidence just landed — ask whether it has outgrown what routing does.

    This is the fold write's one non-observability job, and the trigger point for the whole
    propose-don't-write path: see :mod:`personalclaw.routing.gap` for why the gap is detected here
    rather than at route time or on a timer. It runs AFTER the fold is durable (a proposal must
    never be enqueued for a fold state that failed to save) and in its OWN ``try``, so a detector
    failure is logged as a detector failure rather than masquerading as a lost fold — and, like
    everything else on this path, never reaches the model call.
    """
    use_case = str(rec.get("use_case", "") or "")
    query_class = str(rec.get("query_class", "") or "")
    if not use_case or not query_class:
        return
    try:
        from personalclaw.routing.gap import detect_gap

        detect_gap(stats, use_case, query_class, home=home)
    except Exception:  # noqa: BLE001 — a proposal check must never break the call either
        logger.warning("routing proposal check failed", exc_info=True)


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
