"""Provider health view — DERIVED, not collected (AUTONOMY-GUARDRAILS §2.5).

Breaker states + consecutive-failure counts + latency percentiles + failure-mode
distribution, computed from ``model_calls.jsonl`` (the attempt audit) plus the
in-memory breaker registry. No telemetry infrastructure — a Settings panel derived
from files already on disk. Powers ``GET /api/models/health``.
"""

from __future__ import annotations

from personalclaw.guardrails.audit import read_recent
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


def provider_health(limit: int = 2000) -> dict:
    """Derive the per-provider health summary from the recent audit + breaker state.

    Returns ``{providers: [{name, breaker_state, consecutive_failures, calls,
    passed, failed, pass_rate, p50_ms, p90_ms, p99_ms, failure_modes: {mode: n},
    degraded}], generated_from: n}``. A provider with an OPEN breaker but no recent
    audit rows still appears (breaker state alone), and vice-versa."""
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
    return {"providers": providers, "generated_from": len(rows)}
