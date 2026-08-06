"""Routing telemetry read-model (MODEL-ROUTING-TELEMETRY §1.5, MRT-1d).

The data behind ``GET /api/models/telemetry``: per-model rows a Pareto/efficiency view renders,
derived ON REQUEST from the O(1) fold (:mod:`routing.stats`) plus a bounded tail of
``model_calls.jsonl`` (the fold keeps EMA aggregates; true p50/p95 can't be EMA'd, so they're
derived here from the recent rows — the deviation documented in ``routing.stats``).

Per (use_case, query_class) it returns one row per candidate ref:
``{ref, n, success, feedback, avg_cost_usd, p50_ms, p95_ms, on_frontier}``. ``on_frontier`` = not
dominated by another ref on (quality↑, latency↓, cost↓) — a small dominance check over ≤ dozens of
rows, NOT an optimizer. Nothing here routes, scores for a decision, or mutates state: it reads two
files and shapes a view. Pure given its inputs (the fold dict + the JSONL rows are passed in by the
route), so it is trivially testable without a running gateway.
"""

from __future__ import annotations

from typing import Any


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """The ``pct`` (0..100) percentile of ``sorted_vals`` by nearest-rank. Empty → 0.0."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 1)
    # Nearest-rank: rank = ceil(pct/100 * n), clamped to [1, n], 1-indexed.
    import math

    rank = max(1, min(len(sorted_vals), math.ceil((pct / 100.0) * len(sorted_vals))))
    return round(sorted_vals[rank - 1], 1)


def _latencies_by_ref(
    audit_rows: list[dict], use_case: str, query_class: str
) -> dict[str, list[float]]:
    """Collect per-ref latency samples from the JSONL tail for one (use_case, query_class).

    Ref spelling matches the fold's (``provider:model``), so percentiles join to fold rows.
    Only rows carrying a positive latency count — a failed attempt with no latency is not a
    latency sample."""
    out: dict[str, list[float]] = {}
    for row in audit_rows:
        if row.get("use_case") != use_case or row.get("query_class") != query_class:
            continue
        ref = f"{row.get('provider', '')}:{row.get('model', '')}"
        latency = float(row.get("latency_ms", 0.0) or 0.0)
        if latency > 0.0:
            out.setdefault(ref, []).append(latency)
    return out


def _dominates(a: dict, b: dict) -> bool:
    """Does row ``a`` DOMINATE row ``b``? — a is no worse on every axis and strictly better on
    at least one. Axes: quality (``success`` ↑ better), latency (``p50_ms`` ↓ better), cost
    (``avg_cost_usd`` ↓ better). A row with no latency samples (p50==0) is treated as unknown
    latency and can't dominate on that axis, so it never falsely knocks a measured row off."""
    a_p50 = a["p50_ms"] if a["p50_ms"] > 0 else float("inf")
    b_p50 = b["p50_ms"] if b["p50_ms"] > 0 else float("inf")
    no_worse = (
        a["success"] >= b["success"] and a_p50 <= b_p50 and a["avg_cost_usd"] <= b["avg_cost_usd"]
    )
    strictly_better = (
        a["success"] > b["success"] or a_p50 < b_p50 or a["avg_cost_usd"] < b["avg_cost_usd"]
    )
    return no_worse and strictly_better


def _mark_frontier(rows: list[dict]) -> None:
    """Set ``on_frontier`` on each row: True unless some OTHER row dominates it. In place."""
    for i, row in enumerate(rows):
        dominated = any(_dominates(other, row) for j, other in enumerate(rows) if j != i)
        row["on_frontier"] = not dominated


def telemetry_rows(
    stats: dict[str, Any], audit_rows: list[dict], use_case: str, query_class: str
) -> list[dict]:
    """Per-ref telemetry rows for one (use_case, query_class), frontier-marked.

    ``stats`` is a loaded ``routing_stats.json`` (:func:`routing.stats.load_stats`); ``audit_rows``
    is a bounded JSONL tail (:func:`guardrails.audit.read_recent`). Fold rows supply n / success /
    feedback / cost; the JSONL tail supplies p50/p95 latency. Rows are id-sorted for a stable view.
    """
    by_class = stats.get("use_cases", {}).get(use_case, {}).get(query_class, {})
    lat = _latencies_by_ref(audit_rows, use_case, query_class)
    rows: list[dict] = []
    for ref, agg in sorted(by_class.items()):
        samples = sorted(lat.get(ref, []))
        rows.append(
            {
                "ref": ref,
                "n": int(agg.get("n", 0)),
                "success": round(float(agg.get("success_rate", 0.0)), 4),
                "feedback": round(float(agg.get("feedback", 0.0)), 4),
                "avg_cost_usd": round(float(agg.get("avg_cost_usd", 0.0)), 6),
                "p50_ms": _percentile(samples, 50),
                "p95_ms": _percentile(samples, 95),
            }
        )
    _mark_frontier(rows)
    return rows
