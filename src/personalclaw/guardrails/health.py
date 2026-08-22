"""Provider health view — DERIVED, not collected (AUTONOMY-GUARDRAILS §2.5).

Breaker states + consecutive-failure counts + latency percentiles + failure-mode
distribution, computed from ``model_calls.jsonl`` (the attempt audit) plus the
in-memory breaker registry. No telemetry infrastructure — a Settings panel derived
from files already on disk. Powers ``GET /api/models/health``.
"""

from __future__ import annotations

from personalclaw.guardrails.audit import UNATTRIBUTED, read_recent
from personalclaw.guardrails.breaker import all_breakers


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile of a pre-sorted list (0.0 for empty)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 1)
    # nearest-rank: index = ceil(pct/100 * n) - 1, clamped
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * len(sorted_vals) + 0.5)) - 1))
    return round(sorted_vals[k], 1)


def _caller_rollup(rows: list[dict]) -> list[dict]:
    """Per-CALLER rollup of the same audit rows (ACP-AGENT-PARITY `G47`).

    The read side of the ``caller`` column. Grouped by SUBSYSTEM instead of provider, which
    is the grouping that answers "is my expensive background pass alive?" — a question the
    per-provider view structurally cannot answer, because four unattended subsystems share
    one provider and one ``background`` use case. Measured case it exists for: a skill-ladder
    pass dying as ``provider_error`` at 60,010 ms was recorded and named nothing.

    Rows with no bound caller group under :data:`audit.UNATTRIBUTED` rather than being
    dropped — how much of the population is not yet attributed is itself information, and a
    rollup that silently discards rows produces a plausible number for every input.
    Ordered by call volume (the busiest spender first), then by name for stability.
    """
    by_caller: dict[str, list[dict]] = {}
    for r in rows:
        by_caller.setdefault(str(r.get("caller", "")) or UNATTRIBUTED, []).append(r)
    out: list[dict] = []
    for name, crows in by_caller.items():
        latencies = sorted(float(r.get("latency_ms", 0) or 0) for r in crows if r.get("passed"))
        passed = sum(1 for r in crows if r.get("passed"))
        modes: dict[str, int] = {}
        for r in crows:
            if not r.get("passed"):
                m = str(r.get("failure_mode", "provider_error"))
                modes[m] = modes.get(m, 0) + 1
        out.append(
            {
                "name": name,
                "calls": len(crows),
                "passed": passed,
                "failed": len(crows) - passed,
                "pass_rate": round(passed / len(crows), 3) if crows else None,
                "p50_ms": _percentile(latencies, 50),
                "p90_ms": _percentile(latencies, 90),
                "p99_ms": _percentile(latencies, 99),
                "failure_modes": modes,
                "dollars_est": round(sum(float(r.get("dollars_est", 0) or 0) for r in crows), 4),
            }
        )
    out.sort(key=lambda row: (-int(row["calls"]), str(row["name"])))
    return out


def provider_health(limit: int = 2000) -> dict:
    """Derive the per-provider health summary from the recent audit + breaker state.

    Returns ``{providers: [{name, breaker_state, consecutive_failures, calls,
    passed, failed, pass_rate, p50_ms, p90_ms, p99_ms, failure_modes: {mode: n},
    degraded}], callers: [...], generated_from: n}``. A provider with an OPEN breaker but no
    recent audit rows still appears (breaker state alone), and vice-versa. ``callers`` is the
    same population regrouped by SUBSYSTEM (:func:`_caller_rollup`, `G47`) — additive, so
    every existing reader of this payload is unaffected."""
    rows = read_recent(limit)
    breakers = all_breakers()

    # Group audit rows by provider.
    by_provider: dict[str, list[dict]] = {}
    for r in rows:
        name = str(r.get("provider", "")) or "(unknown)"
        by_provider.setdefault(name, []).append(r)

    names = set(by_provider) | set(breakers)
    providers: list[dict] = []
    for name in sorted(names):
        prov_rows = by_provider.get(name, [])
        latencies = sorted(float(r.get("latency_ms", 0) or 0) for r in prov_rows if r.get("passed"))
        passed = sum(1 for r in prov_rows if r.get("passed"))
        failed = len(prov_rows) - passed
        modes: dict[str, int] = {}
        for r in prov_rows:
            if not r.get("passed"):
                m = str(r.get("failure_mode", "provider_error"))
                modes[m] = modes.get(m, 0) + 1
        breaker = breakers.get(name)
        providers.append(
            {
                "name": name,
                "breaker_state": breaker.state().value if breaker else "closed",
                "consecutive_failures": breaker.consecutive_failures if breaker else 0,
                "calls": len(prov_rows),
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / len(prov_rows), 3) if prov_rows else None,
                "p50_ms": _percentile(latencies, 50),
                "p90_ms": _percentile(latencies, 90),
                "p99_ms": _percentile(latencies, 99),
                "failure_modes": modes,
                "degraded": any(r.get("degraded") for r in prov_rows),
            }
        )
    return {
        "providers": providers,
        "callers": _caller_rollup(rows),
        "generated_from": len(rows),
    }
