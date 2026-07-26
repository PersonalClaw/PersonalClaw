"""Replay baselines + gating (§2.3).

Compares a scenario's freshly-computed :class:`~harness.replay.Metrics` against checked-in
baselines with two kinds of threshold:

- **hard thresholds** (absolute ceilings, the ClawX-proven defaults): duplicate_event_rate
  ≤ 0.005, order_violations = 0, reconnect_loss = 0, fanout ≤ 1.2 for single-key streams.
  A stream that legitimately fans out to many keys overrides its fanout ceiling in the
  baseline file.
- **relative drift tolerances** (latency p95 may grow at most +15% over the recorded
  baseline) — catches a gradual regression without pinning to a machine-specific absolute.

A **missing required scenario is a failure** (silently dropping a scenario is how baselines
rot). The baseline file records, per scenario, the accepted metric values + any per-scenario
threshold overrides, with a rationale line required for any loosened threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from harness.replay import Metrics, metrics_for_scenario

# ClawX-proven hard defaults. A baseline entry may override any of these (with a rationale).
DEFAULT_HARD = {
    "duplicate_event_rate_max": 0.005,
    "order_violation_count_max": 0,
    "reconnect_loss_count_max": 0,
    "event_fanout_ratio_max": 1.2,
}
LATENCY_DRIFT_TOLERANCE = 0.15  # p95 may grow at most +15% over the baseline


def _harness_root() -> Path:
    return Path(__file__).resolve().parent


def traces_dir() -> Path:
    return _harness_root() / "traces"


def baselines_path() -> Path:
    return traces_dir() / "baselines.json"


@dataclass
class Baseline:
    """One scenario's accepted metrics + optional per-scenario threshold overrides.

    ``thresholds`` overrides ``DEFAULT_HARD`` keys; ``rationale`` MUST be present when any
    threshold is loosened beyond the default (enforced by :func:`check_baselines`)."""

    scenario: str
    metrics: dict
    thresholds: dict = field(default_factory=dict)
    rationale: str = ""


@dataclass
class GateResult:
    """Outcome of comparing one scenario against its baseline."""

    scenario: str
    ok: bool
    failures: list[str] = field(default_factory=list)


def load_baselines(path: Path | None = None) -> dict[str, Baseline]:
    p = path or baselines_path()
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, Baseline] = {}
    for scen, entry in data.get("scenarios", {}).items():
        out[scen] = Baseline(
            scenario=scen,
            metrics=entry.get("metrics", {}),
            thresholds=entry.get("thresholds", {}),
            rationale=entry.get("rationale", ""),
        )
    return out


def _hard_for(baseline: Baseline) -> dict:
    merged = dict(DEFAULT_HARD)
    merged.update(baseline.thresholds or {})
    return merged


def check_metrics(m: Metrics, baseline: Baseline) -> list[str]:
    """Return the list of threshold failures for ``m`` vs ``baseline`` (empty == pass)."""
    hard = _hard_for(baseline)
    failures: list[str] = []

    if m.duplicate_event_rate > hard["duplicate_event_rate_max"]:
        failures.append(
            f"duplicate_event_rate {m.duplicate_event_rate:.4f} > "
            f"{hard['duplicate_event_rate_max']}"
        )
    if m.order_violation_count > hard["order_violation_count_max"]:
        failures.append(
            f"order_violation_count {m.order_violation_count} > "
            f"{hard['order_violation_count_max']}"
        )
    if m.reconnect_loss_count > hard["reconnect_loss_count_max"]:
        failures.append(
            f"reconnect_loss_count {m.reconnect_loss_count} > "
            f"{hard['reconnect_loss_count_max']}"
        )
    if m.event_fanout_ratio > hard["event_fanout_ratio_max"]:
        failures.append(
            f"event_fanout_ratio {m.event_fanout_ratio:.2f} > {hard['event_fanout_ratio_max']}"
        )

    # Latency drift vs the recorded baseline p95 (per stream).
    base_p95 = baseline.metrics.get("latency_p95", {})
    for stream, p95 in m.latency_p95.items():
        recorded = base_p95.get(stream)
        if recorded is None or recorded <= 0:
            continue
        if p95 > recorded * (1 + LATENCY_DRIFT_TOLERANCE):
            failures.append(
                f"latency_p95[{stream}] {p95:.4f} > baseline {recorded:.4f} +"
                f"{int(LATENCY_DRIFT_TOLERANCE * 100)}%"
            )
    return failures


def required_scenarios(td: Path | None = None) -> list[str]:
    """Scenario dirs that MUST be present + gated: every subdir of traces/ with an
    ``*.ndjson`` file. A scenario dropped from disk fails :func:`check_baselines` because
    its baseline entry has no matching recording."""
    d = td or traces_dir()
    if not d.is_dir():
        return []
    out = []
    for sub in sorted(d.iterdir()):
        if sub.is_dir() and any(sub.glob("*.ndjson")):
            out.append(sub.name)
    return out


def check_baselines(td: Path | None = None) -> list[GateResult]:
    """Gate every baselined scenario against its recording. A baseline whose scenario dir
    is missing fails (missing-scenario-fails); a recording with no baseline entry fails
    (unrecorded new scenario). Loosened thresholds without a rationale fail."""
    d = td or traces_dir()
    baselines = load_baselines(d / "baselines.json")
    present = set(required_scenarios(d))
    results: list[GateResult] = []

    for scen, baseline in baselines.items():
        if scen not in present:
            results.append(
                GateResult(
                    scenario=scen, ok=False, failures=["required scenario recording missing"]
                )
            )
            continue
        if _thresholds_loosened(baseline) and not baseline.rationale.strip():
            results.append(
                GateResult(
                    scenario=scen,
                    ok=False,
                    failures=["threshold loosened beyond default without a rationale line"],
                )
            )
            continue
        m = metrics_for_scenario(d / scen)
        failures = check_metrics(m, baseline)
        results.append(GateResult(scenario=scen, ok=not failures, failures=failures))

    # A recorded scenario with no baseline entry is also a failure (add its baseline).
    for scen in present:
        if scen not in baselines:
            results.append(
                GateResult(
                    scenario=scen, ok=False, failures=["recorded scenario has no baseline entry"]
                )
            )
    return results


def _thresholds_loosened(baseline: Baseline) -> bool:
    """True if any override raises a ceiling above the default (i.e. loosens the gate)."""
    for key, val in (baseline.thresholds or {}).items():
        default = DEFAULT_HARD.get(key)
        if default is not None and val > default:
            return True
    return False
