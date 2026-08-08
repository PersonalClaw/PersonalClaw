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

#: Scenarios that MUST be present on disk — dropping one from the tree is itself a failure,
#: not merely "one fewer scenario". These are the §2.3 required set members whose absence is
#: how a gate silently rots: the WF2 journal-format gate lives entirely in the two workflow
#: scenarios, so if either recording vanished the journal would be ungated with nothing to
#: say so (SV-5, Success Criterion #4). ``required_scenarios`` already treats every recording
#: on disk as present-and-required; this NAMED set adds the "and these two must EXIST" half,
#: which a disk scan alone cannot express (an absent dir scans as absent, not as failing).
REQUIRED_SCENARIOS = frozenset(
    {
        "workflow-journal-projection",
        "rewind-during-stream",
    }
)


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

    # Event-fold law (WF2-R11, the SV-5 gate). A baseline that pins a ``fold`` terminal state
    # is asserting the exact state the journal→SSE projection reconstructs. An EXACT compare,
    # not a threshold: the fold law is a byte-equal invariant, so any drift — a renamed event
    # kind, a dropped guard, a changed terminal state — must fail, which is precisely what
    # gates the journal format before a Slice 3+ consumer relies on it. A baseline that pins a
    # fold but the recording no longer produces one is also a failure (the projection events
    # vanished from the trace).
    base_fold = baseline.metrics.get("fold")
    if base_fold is not None:
        if m.fold is None:
            failures.append("fold invariant missing: scenario recorded no workflow projection")
        elif m.fold != base_fold:
            failures.append(
                "event-fold law broke: folded terminal state diverged from baseline "
                f"(baseline {base_fold} != folded {m.fold})"
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

    # A NAMED required scenario absent from disk fails the run outright (SV-5, SC#4). This is
    # distinct from the per-baseline "recording missing" check below: that fires only when a
    # baseline ENTRY exists, so deleting both the recording AND its baseline would otherwise
    # be a silent scenario drop — the exact rot the required set exists to prevent. Skipped
    # when a baseline entry still exists (the per-baseline loop reports that case), so a
    # scenario is never failed twice for the same absence.
    for scen in sorted(REQUIRED_SCENARIOS):
        if scen not in present and scen not in baselines:
            results.append(
                GateResult(
                    scenario=scen,
                    ok=False,
                    failures=["required scenario recording missing from harness/traces/"],
                )
            )

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
