"""The introspection checklist: what is running, costing, risky, blocked (§6.4, R5/R6/R9 — S53).

The hub and cockpit must answer nine questions **from structured state alone**: what is
running now and
why, what changed, what is blocked, what needs approval, what failed, what is costing money, what is
risky, what will happen next if the user says nothing, and — the one that makes unattended
supervision
trustworthy — whether the checks that passed were real checks.

Everything here is a PROJECTION over the existing journal. `journal.ledger()` already
reads the event
stream and `journal.run_totals()` already aggregates one run; this module adds the cross-run and
per-template views, and it deliberately adds no metrics store. The plan's own words: "pass-rate,
failure distribution and latency percentiles are queries over this — not a separate
metrics store."

Two facts about the real event stream shape everything below, both verified in code rather than
assumed:

1. **`GATE_REJECTED` is declared and emitted NOWHERE.** A said-no metric reading it would
report zero
   rejections for every template and flag all of them as fake checks — a warning badge on
   the entire
   library, which is the same as no badge. Pass/reject is derived from `GATE_RESOLVED`'s own
   `approved` field, which the controller does write on both paths.
2. **A gate that has never run is not a gate that never rejects.** The fake-check warning requires a
   MINIMUM sample before it fires, because "0 rejections in 0 runs" and "0 rejections in
   40 runs" are
   different claims and only the second is evidence.

Pure functions over event lists. The caller reads the journal; these decide what the numbers mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Runs of a template before its 100%-pass rate counts as evidence of a fake check. Below this,
#: "never rejected" is a sample-size artifact — and a badge that fires on the third run of a new
#: template teaches the user to ignore badges before the metric has ever been right.
FAKE_CHECK_MIN_RUNS = 10

#: Verification debt above this fraction is worth surfacing. Not zero: a plan legitimately contains
#: zero-token actions whose output IS the check (S42's contract lint exempts them), so a 0% target
#: would flag correct structure — the rule that fires on correct work is the rule that gets
#: suppressed wholesale.
VERIFICATION_DEBT_WARN = 0.5

#: Percentiles the template cards show. p50 answers "what does this usually cost"; p95 answers "what
#: is the bad case". A mean would hide both — one runaway run moves it, and nothing tells
#: you whether
#: the typical run is cheap.
PERCENTILES = (50, 95)


def percentile(values: list[float], pct: int) -> float:
    """Nearest-rank percentile. Deterministic and dependency-free.

    Nearest-rank rather than interpolated: with the handful of runs a personal instance accumulates,
    an interpolated p95 invents a value between two real runs, and "the bad case cost $0.37" is more
    useful when $0.37 is a run that actually happened.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(1, min(len(ordered), round(pct / 100.0 * len(ordered))))
    return float(ordered[rank - 1])


@dataclass
class RunStats:
    """One run's economics and shape, projected from its ledger.

    `first_byte_ms` is separated from total duration because they answer different
    questions: latency
    to first output is what a watching user feels, and total duration is what a scheduler budgets.
    A single "duration" would conflate a slow start with slow work.
    """

    run_id: str
    tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    steps_completed: int = 0
    steps_failed: int = 0
    steps_cached: int = 0
    duration_secs: float = 0.0
    first_byte_ms: float = 0.0
    models: list[str] = field(default_factory=list)
    #: Nodes that completed with no executed evidence behind them — verification DEBT. The number
    #: LEARNING-FLYWHEEL's evaluator consumes from this surface.
    unverified_steps: int = 0

    @property
    def verification_debt(self) -> float:
        """Fraction of completed steps with nothing verifying them.

        Zero completed steps yields 0.0, not a division error and not 1.0: a run that completed
        nothing has no debt, and reporting full debt would put a red number on a run that has not
        yet done anything wrong.
        """
        if self.steps_completed <= 0:
            return 0.0
        return round(self.unverified_steps / self.steps_completed, 4)

    @property
    def cache_hit_rate(self) -> float:
        total = self.steps_completed + self.steps_cached
        return round(self.steps_cached / total, 4) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tokens": self.tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_cached": self.steps_cached,
            "duration_secs": round(self.duration_secs, 3),
            "first_byte_ms": round(self.first_byte_ms, 1),
            "models": list(self.models),
            "unverified_steps": self.unverified_steps,
            "verification_debt": self.verification_debt,
            "cache_hit_rate": self.cache_hit_rate,
        }


def run_stats(run_id: str, events: list[dict[str, Any]]) -> RunStats:
    """Project one run's ledger into stats.

    Takes the event list rather than reading the journal, so the arithmetic is testable
    without a run
    on disk — and so a caller that already has the ledger does not read it twice.

    Verified steps are counted by BINDING, not by adjacency: a node whose output a later
    gate consumed
    is verified even if three nodes sit between them. Counting "the next node is a gate"
    would report
    a correctly-verified reviewer as debt, and a debt number that flags correct structure
    gets ignored.
    """
    stats = RunStats(run_id=run_id)
    models: list[str] = []
    verified_nodes: set[str] = set()
    completed_nodes: list[str] = []
    first_ts: float | None = None
    last_ts: float | None = None
    first_output_ts: float | None = None

    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        ts = _epoch(event.get("ts"))
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if kind == "step_completed":
            stats.steps_completed += 1
            stats.tokens += int(event.get("tokens", 0) or 0)
            stats.cost_usd += float(event.get("cost_usd", 0.0) or 0.0)
            stats.cached_tokens += int(event.get("cached_tokens", 0) or 0)
            model = str(event.get("model") or "")
            if model and model not in models:
                models.append(model)
            node_id = str(event.get("node_id") or "")
            if node_id:
                completed_nodes.append(node_id)
            if first_output_ts is None and ts is not None:
                first_output_ts = ts
        elif kind == "step_failed":
            stats.steps_failed += 1
        elif kind == "step_cached":
            stats.steps_cached += 1
        elif kind == "gate_resolved":
            # A resolved gate verifies whatever it consumed. `verifies` is written when the engine
            # knows the binding; the gate's own node id is recorded regardless so a caller can still
            # attribute the check.
            for target in event.get("verifies") or []:
                verified_nodes.add(str(target))
    stats.models = models
    stats.unverified_steps = len([n for n in completed_nodes if n not in verified_nodes])
    if first_ts is not None and last_ts is not None:
        stats.duration_secs = max(0.0, last_ts - first_ts)
    if first_ts is not None and first_output_ts is not None:
        stats.first_byte_ms = max(0.0, (first_output_ts - first_ts) * 1000.0)
    return stats


def _epoch(raw: Any) -> float | None:
    """Parse a journal timestamp to epoch seconds, or None.

    Tolerant by construction: a journal is append-only history written by several code paths over
    time, and a stats projection that raised on one unparseable timestamp would lose the whole run's
    economics to a formatting detail.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return None


@dataclass
class GateStats:
    """Said-no metrics for one gate, across runs.

    `rejects` is the number that matters. A gate with a 100% pass rate over a real sample is
    statistical evidence of a fake check — it is not verifying anything, it is decorating the plan
    with the appearance of verification, which is worse than no gate because a reviewer counts it.
    """

    node_id: str
    passes: int = 0
    rejects: int = 0
    retries_consumed: int = 0

    @property
    def total(self) -> int:
        return self.passes + self.rejects

    @property
    def pass_rate(self) -> float:
        return round(self.passes / self.total, 4) if self.total else 0.0

    def fake_check_warning(self, *, min_runs: int = FAKE_CHECK_MIN_RUNS) -> str:
        """The warning, or "" when there is no evidence for one.

        "0 rejections in 0 runs" and "0 rejections in 40 runs" are different claims, and only the
        second is evidence. A badge that fired on the third run of a new template would teach the
        user to ignore badges before the metric had ever been right.
        """
        if self.total < min_runs:
            return ""
        if self.rejects == 0:
            return (
                f"`{self.node_id}` passed {self.passes}/{self.total} times and has never "
                "rejected — a 100% pass rate over this many runs is evidence it is not checking"
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "passes": self.passes,
            "rejects": self.rejects,
            "retries_consumed": self.retries_consumed,
            "total": self.total,
            "pass_rate": self.pass_rate,
            "fake_check_warning": self.fake_check_warning(),
        }


def gate_stats(events: list[dict[str, Any]]) -> dict[str, GateStats]:
    """Per-gate said-no statistics, derived from `GATE_RESOLVED`'s `approved` field.

    NOT from `GATE_REJECTED`: that event kind is declared in `journal.py` and emitted nowhere, so a
    metric reading it would report zero rejections for every gate in the library and flag
    all of them
    as fake checks — a warning on everything, which is the same as a warning on nothing. The
    controller writes `approved` on both the auto-approve and the human-resolution paths, so it is
    the field that actually distinguishes a pass from a reject.
    """
    out: dict[str, GateStats] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        node_id = str(event.get("node_id") or "")
        if not node_id:
            continue
        if kind == "gate_resolved":
            stats = out.setdefault(node_id, GateStats(node_id=node_id))
            if event.get("approved"):
                stats.passes += 1
            else:
                stats.rejects += 1
        elif kind == "step_attempt":
            # Only for a node that IS a gate. Measured: attributing every `step_attempt` created an
            # entry for any retried node, so `publish` (an action) appeared in the gate table with
            # `total: 0` and a 0.0 pass rate — a row that reads as a gate which has never passed
            # anything. A said-no table listing non-gates is one a reviewer stops trusting.
            if node_id not in out:
                continue
            # Attempt 1 is the first try, not a retry. Counting it would report a retry on every
            # node that ever ran.
            if int(event.get("attempt", 1) or 1) > 1:
                out[node_id].retries_consumed += 1
    return out


@dataclass
class TemplateCard:
    """The hub's per-template economics card.

    p50 and p95 rather than a mean: a mean hides both the typical case and the bad one,
    since a single
    runaway run moves it and nothing tells you whether the usual run is cheap.
    """

    template: str
    runs: int = 0
    cost_p50: float = 0.0
    cost_p95: float = 0.0
    duration_p50: float = 0.0
    duration_p95: float = 0.0
    failure_rate: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "runs": self.runs,
            "cost_p50": round(self.cost_p50, 6),
            "cost_p95": round(self.cost_p95, 6),
            "duration_p50": round(self.duration_p50, 2),
            "duration_p95": round(self.duration_p95, 2),
            "failure_rate": self.failure_rate,
            "warnings": list(self.warnings),
        }


def template_card(
    template: str, runs: list[RunStats], warnings: list[str] | None = None
) -> TemplateCard:
    """Aggregate a template's runs into its card.

    A template with ONE run still gets a card, with p50 == p95 == that run. Withholding
    the card until
    a sample accumulated would leave the newest template — the one most likely to be
    surprising —
    invisible on the surface that exists to answer "what is costing money".
    """
    card = TemplateCard(template=template, runs=len(runs), warnings=list(warnings or []))
    if not runs:
        return card
    costs = [r.cost_usd for r in runs]
    durations = [r.duration_secs for r in runs]
    card.cost_p50 = percentile(costs, 50)
    card.cost_p95 = percentile(costs, 95)
    card.duration_p50 = percentile(durations, 50)
    card.duration_p95 = percentile(durations, 95)
    failed = len([r for r in runs if r.steps_failed > 0])
    card.failure_rate = round(failed / len(runs), 4)
    return card


#: The nine questions §6.4 requires the surfaces to answer from structured state alone. Named here
#: rather than in a UI comment so the checklist is checkable: `checklist_gaps` reports which of them
#: the supplied state cannot answer, which is what makes this a contract instead of an aspiration.
CHECKLIST = (
    ("running", "what is running now, and why"),
    ("changed", "what changed"),
    ("blocked", "what is blocked"),
    ("approval", "what needs my approval"),
    ("failed", "what failed"),
    ("cost", "what is costing money"),
    ("risky", "what is risky"),
    ("next", "what will happen next if I say nothing"),
    ("proof", "were the checks that passed real checks"),
)


def checklist_gaps(answers: dict[str, Any]) -> list[str]:
    """Which checklist questions the supplied state cannot answer.

    Returned rather than logged, because this doubles as the validation script for the
    implementation
    sessions: a surface that renders eight of nine is a surface with a specific hole, and
    naming it is
    what turns "glanceable" from a taste claim into a check.

    A key present with an EMPTY value counts as answered — "nothing is blocked" is an answer, and
    treating it as a gap would make an idle instance look broken.
    """
    return [f"{key}: {question}" for key, question in CHECKLIST if key not in (answers or {})]


@dataclass
class ProofSection:
    """The cockpit's Proof section: summary, before/after, evidence.

    Separate from the run's output on purpose. "What did my machine do while I slept"
    needs PROOF, not
    prose — and a summary that doubles as the evidence is a summary nobody can check.
    """

    summary: str = ""
    verified_steps: int = 0
    total_steps: int = 0
    evidence_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return round(self.verified_steps / self.total_steps, 4) if self.total_steps else 0.0

    @property
    def honest(self) -> bool:
        """Whether the section can be shown without a caveat.

        A Proof section with no evidence and no warning is the worst possible surface: it looks like
        proof. Either there is evidence, or the absence is stated.
        """
        return bool(self.evidence_files) or bool(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "verified_steps": self.verified_steps,
            "total_steps": self.total_steps,
            "coverage": self.coverage,
            "evidence_files": list(self.evidence_files),
            "warnings": list(self.warnings),
            "honest": self.honest,
        }


def proof_section(stats: RunStats, *, evidence_files: list[str] | None = None) -> ProofSection:
    """Build the Proof section from a run's stats, adding the caveats its numbers earn.

    The warnings are the point. A run with high verification debt and a confident summary is exactly
    the shape that makes unattended work untrustworthy — the output looks finished, and
    nothing says
    how much of it was checked.
    """
    section = ProofSection(
        summary=(
            f"{stats.steps_completed} step(s) completed, {stats.steps_failed} failed, "
            f"{stats.steps_cached} served from cache"
        ),
        verified_steps=max(0, stats.steps_completed - stats.unverified_steps),
        total_steps=stats.steps_completed,
        evidence_files=list(evidence_files or []),
    )
    if stats.verification_debt > VERIFICATION_DEBT_WARN:
        section.warnings.append(
            f"{stats.verification_debt:.0%} of completed steps had nothing verifying them — "
            "the output may be right, but this run did not establish that"
        )
    if not evidence_files:
        section.warnings.append(
            "no evidence files were captured, so this section is a claim about the run rather than "
            "proof of it"
        )
    if stats.steps_failed:
        section.warnings.append(
            f"{stats.steps_failed} step(s) failed — check what the run did with their outputs"
        )
    return section
