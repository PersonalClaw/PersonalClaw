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

import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.ledger import hash_value

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


# ── trajectory signature (PP-7) ────────────────────────────────────────────────

#: Runs of a template before a shift to a worse-performing signature class counts as evidence
#: rather than noise. Mirrors FAKE_CHECK_MIN_RUNS in spirit: "the third run took a new path" is not
#: a regression, it is a template that has barely run. Below this floor the detector stays silent.
TRAJECTORY_REGRESSION_MIN_RUNS = 10

#: A regime — the runs on one signature class, before or after a shift — must be at least this many
#: for its failure rate to be evidence. One run on a new path is an anecdote, and a failure rate
#: over a single run is 0% or 100% — neither is a measurement.
TRAJECTORY_REGRESSION_MIN_CLASS_RUNS = 3

#: The failure-rate jump between the old regime and the new one worth surfacing. A class that fails
#: a hair more often than the one before it is drift, not a regression — the signal fires on a
#: MATERIAL shift so the surface that carries it stays worth reading.
TRAJECTORY_REGRESSION_MIN_DELTA = 0.25

#: The ledger events that place a node on a run's decision PATH, each carrying a verdict. The path
#: is the ordered sequence of these: which nodes ran, in what order, how each resolved, and which
#: branch legs the engine skipped. Deliberately NOT deduped by path — a rewind re-runs nodes and
#: appends their terminal events again, and those extra tuples are exactly what makes a rewound
#: run's signature distinguishable from a clean one's.
_TRAJECTORY_STEP_KINDS = frozenset(
    {
        "step_completed",
        "step_failed",
        "step_skipped",
        "step_cached",
        "gate_resolved",
        "judge_verdict",
    }
)

_FIXED_VERDICTS = {"step_failed": "failed", "step_skipped": "skipped", "step_cached": "cached"}


def _trajectory_verdict(kind: str, event: dict[str, Any]) -> str:
    """The verdict one path-shaping event carries.

    A completed step's verdict is its terminal state — a `degraded` success took a different path
    than a clean one and must not collapse into it. A gate's is approve/reject; a judge's is its
    own verdict token. Every other terminal kind carries a fixed verdict so the sequence is a path.
    """
    if kind == "step_completed":
        return str(event.get("state") or "done")
    if kind == "gate_resolved":
        return "gate:approved" if event.get("approved") else "gate:rejected"
    if kind == "judge_verdict":
        return "judge:" + str(event.get("verdict") or "").upper()
    return _FIXED_VERDICTS[kind]


def trajectory_steps(events: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """The ordered (node, lane, verdict) tuples a run's ledger describes — its decision PATH.

    A PURE projection over the event list, in journal order, with no store of its own: PP-7's whole
    claim is that the path is already fully recorded and only needs reading. `lane` is read from the
    `step_started` a node emitted (the one event that carries it); a node with no recorded lane — an
    untaken branch leg the engine skipped without launching — contributes "" rather than a guess,
    which is deterministic and so keeps the projection pure.

    NOT deduped by path (unlike replay's last-write-wins fold, which reconstructs a FINAL
    trajectory): a signature must tell a rewound run apart from a clean one, and a rewind's mark on
    the ledger is precisely the re-execution events it appends.
    """
    lane_by_path: dict[str, str] = {}
    steps: list[tuple[str, str, str]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        if kind == "step_started":
            path = str(event.get("instance_path") or "")
            if path:
                lane_by_path[path] = str(event.get("lane") or "")
            continue
        if kind not in _TRAJECTORY_STEP_KINDS:
            continue
        path = str(event.get("instance_path") or "")
        node = str(event.get("node_id") or "") or path
        steps.append((node, lane_by_path.get(path, ""), _trajectory_verdict(kind, event)))
    return steps


@dataclass
class TrajectorySignature:
    """One run's trajectory, projected from its ledger: the ordered path plus its hash.

    `signature` is the CLASS — two runs that took the same path hash equal, and that equality is the
    whole query "which runs of this template went a different way". `steps` is kept so a surface can
    show the path, not just its fingerprint.
    """

    run_id: str
    signature: str
    steps: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "signature": self.signature,
            "length": self.length,
            "steps": [{"node": n, "lane": lane, "verdict": v} for n, lane, v in self.steps],
        }


def trajectory_signature(run_id: str, events: list[dict[str, Any]]) -> TrajectorySignature:
    """Project a run's ledger into its trajectory signature — a PURE function of the events.

    The signature is `hash_value` over the ordered tuple list, reusing the codebase's one content
    hash rather than minting a parallel scheme (the same 16-hex digest `FailureSignature.input_hash`
    and a node's `prompt_hash` use). Same events in, same signature out, every time: computing it
    twice over a frozen ledger returns the same string, which is the purity bar.
    """
    steps = trajectory_steps(events)
    return TrajectorySignature(run_id=run_id, signature=hash_value(steps), steps=steps)


def _most_common_signature(runs: list[tuple[str, bool]]) -> str:
    """The signature class most runs took. Ties broken by the signature string so the choice is
    deterministic across calls — a nondeterministic tiebreak would leak into the regression's own
    output and break its purity."""
    counts: dict[str, int] = {}
    for sig, _ in runs:
        counts[sig] = counts.get(sig, 0) + 1
    return max(sorted(counts), key=lambda s: counts[s]) if counts else ""


@dataclass
class TrajectoryRegression:
    """A template whose recent runs shifted to a signature class that fails more often."""

    template: str
    current_signature: str
    prior_signature: str
    current_failure_rate: float
    prior_failure_rate: float
    current_runs: int
    prior_runs: int

    def message(self) -> str:
        return (
            f"{self.template}'s recent runs shifted to a new path (`{self.current_signature}`) "
            f"that fails {self.current_failure_rate:.0%} of the time, up from "
            f"{self.prior_failure_rate:.0%} on the path it took before (`{self.prior_signature}`)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "current_signature": self.current_signature,
            "prior_signature": self.prior_signature,
            "current_failure_rate": round(self.current_failure_rate, 4),
            "prior_failure_rate": round(self.prior_failure_rate, 4),
            "current_runs": self.current_runs,
            "prior_runs": self.prior_runs,
            "message": self.message(),
        }


def trajectory_regression(
    template: str,
    runs: list[tuple[str, bool]],
    *,
    min_runs: int = TRAJECTORY_REGRESSION_MIN_RUNS,
    min_class_runs: int = TRAJECTORY_REGRESSION_MIN_CLASS_RUNS,
    min_delta: float = TRAJECTORY_REGRESSION_MIN_DELTA,
) -> TrajectoryRegression | None:
    """Fire when a template's runs have SHIFTED to a signature class that fails more often.

    `runs` is (signature, failed) per run, OLDEST first. The detector finds the contiguous tail of
    most-recent runs sharing the newest signature — the new regime — and compares its failure rate
    to the runs before it. It fires only when the shift is real (the prior regime's dominant path
    differs from the new one) and the new path fails materially more.

    Sample-gated like `gate_stats`, and for the same reason: "the last two runs took a new path and
    both failed" is not evidence a template regressed — it is a template that has barely run. Below
    `min_runs` total, or with either regime under `min_class_runs`, the detector stays silent.
    Dropping those floors to zero is what turns a young template's first new path into a false
    alarm, which is exactly the failure this gate exists to prevent.
    """
    clean = [(str(s), bool(f)) for s, f in (runs or []) if s]
    if len(clean) < min_runs:
        return None
    current_sig = clean[-1][0]
    tail: list[tuple[str, bool]] = []
    for sig, failed in reversed(clean):
        if sig != current_sig:
            break
        tail.append((sig, failed))
    prior = clean[: len(clean) - len(tail)]
    if len(tail) < min_class_runs or len(prior) < min_class_runs:
        return None
    # A genuine shift: the path the template USED to take must differ from the one it moved to.
    prior_dominant = _most_common_signature(prior)
    if prior_dominant == current_sig:
        return None
    current_rate = sum(1 for _, f in tail if f) / len(tail)
    prior_rate = sum(1 for _, f in prior if f) / len(prior)
    if current_rate - prior_rate < min_delta:
        return None
    return TrajectoryRegression(
        template=template,
        current_signature=current_sig,
        prior_signature=prior_dominant,
        current_failure_rate=current_rate,
        prior_failure_rate=prior_rate,
        current_runs=len(tail),
        prior_runs=len(prior),
    )


#: Runs before an EDGE'S decision distribution counts as evidence — deliberately the SAME bar as
#: the said-no badge. A `branch` case unseen across three routings is UNSAMPLED, not dead, and a
#: dead-case flag that fires on the third run of a new template is the same noise `gate_stats`'s
#: sample gate exists to prevent — the badge that fires before the metric has ever been right is
#: the one that teaches a reader to ignore the surface. Reusing `FAKE_CHECK_MIN_RUNS` rather than
#: minting a second threshold keeps ONE sample bar across the whole surface: a branch and a gate on
#: the same template must not disagree about what "enough runs" means.
EDGE_STATS_MIN_RUNS = FAKE_CHECK_MIN_RUNS

#: A branch's cases live at the instance path `<branch>.cases[<label>]` (see `tick._visit_branch`).
#: This is the only place the selected case survives in the EVENT STREAM: the branch node's own
#: `{"case": label}` output is offloaded behind an `output_ref`, and its declined edges are held in
#: memory and never journaled. But the taken case runs (a non-`step_skipped` event in its subtree)
#: and every untaken case is SKIPPED with its whole subtree (`controller._skip`) — so the routing is
#: recoverable from paths alone, which keeps this a pure projection over the event list like
#: `gate_stats`, with no output-store read and no new ledger kind.
_CASE_SEGMENT = re.compile(r"\.cases\[(?P<label>[^\]]+)\]")


def _branch_routing(
    events: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """One run's `branch` routing, as (cases_seen, cases_taken) keyed by branch instance path.

    A case is SEEN if its subtree appears at all (taken or skipped); it is TAKEN if any event in its
    subtree is not a `step_skipped`. Nested branches attribute each `.cases[...]` segment to its own
    immediate prefix, so `outer.cases[a].inner.cases[b]` records `a` for `outer` and `b` for the
    inner branch independently. Reading "taken" as "has a non-skip event" rather than "the case root
    emitted a step" is what makes a CONTAINER case work: a structural container root emits nothing
    of its own, but its children do — and an untaken container's whole subtree is skipped, so it has
    no non-skip event to confuse the count.
    """
    seen: dict[str, set[str]] = {}
    taken: dict[str, set[str]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        path = str(event.get("instance_path") or "")
        if ".cases[" not in path:
            continue
        is_skip = str(event.get("kind") or "") == "step_skipped"
        for match in _CASE_SEGMENT.finditer(path):
            branch_path = path[: match.start()]
            label = match.group("label")
            seen.setdefault(branch_path, set()).add(label)
            if not is_skip:
                taken.setdefault(branch_path, set()).add(label)
    return seen, taken


@dataclass
class BranchStats:
    """Case distribution for one `branch` selector, across a template's routed runs.

    `cases` enumerates EVERY case the branch ever exposed — a never-taken case is a real `0`, not an
    absent key, because a projection that only listed cases it had seen taken could never report the
    dead one. `routed_runs` counts only the runs where the branch actually routed (an outer branch
    can skip this one entirely), so the sample gate measures decisions the selector made, not runs
    of the template.
    """

    path: str
    cases: dict[str, int] = field(default_factory=dict)
    routed_runs: int = 0

    def never_taken(self, *, min_runs: int = EDGE_STATS_MIN_RUNS) -> list[str]:
        """Cases no routed run has ever selected — but only once there is a real sample.

        Below `min_runs` this is empty on purpose: a case unseen over three routings is unsampled,
        and "dead" and "not yet reached" are different facts. Reporting the first as the second is
        exactly how a legible surface stops being read.
        """
        if self.routed_runs < min_runs:
            return []
        return sorted(label for label, count in self.cases.items() if count == 0)

    def degenerate_warning(self, *, min_runs: int = EDGE_STATS_MIN_RUNS) -> str:
        """The warning when a real alternative exists but the selector always makes the same choice.

        Requires a real sample AND more than one declared case: a branch with a single case is a
        spec shape, not a selector doing no work. "" when there is no evidence, mirroring
        `GateStats.fake_check_warning` so a reader learns one rule for the whole surface.
        """
        if self.routed_runs < min_runs or len(self.cases) < 2:
            return ""
        chosen = [(label, count) for label, count in self.cases.items() if count > 0]
        if len(chosen) == 1 and chosen[0][1] == self.routed_runs:
            others = len(self.cases) - 1
            # `others` is at least 1: the guard above requires two or more declared cases. So the
            # singular is the COMMON shape here (a two-case branch), not an edge — which is why a
            # `case(s)` hedge read worst on exactly the branch a reader is most likely to hit. The
            # count is dropped when it is 1, because "its other 1 case" is worse than either plural.
            alternatives = (
                "its one other case is declared but never chosen"
                if others == 1
                else f"its other {others} cases are declared but never chosen"
            )
            return (
                f"`{self.path}` routed to `{chosen[0][0]}` in all {self.routed_runs} runs that "
                f"reached it — {alternatives}, so the selector is doing no work"
            )
        return ""

    def warnings(self, *, min_runs: int = EDGE_STATS_MIN_RUNS) -> list[str]:
        """This branch's findings for the template card — degenerate first, then dead cases."""
        out: list[str] = []
        degenerate = self.degenerate_warning(min_runs=min_runs)
        if degenerate:
            out.append(degenerate)
        dead = self.never_taken(min_runs=min_runs)
        if dead:
            joined = ", ".join(f"`{label}`" for label in dead)
            out.append(
                f"`{self.path}` never took {joined} across {self.routed_runs} runs — a case the "
                "selector has never reached is dead unless a future input routes to it"
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "cases": dict(self.cases),
            "routed_runs": self.routed_runs,
            "never_taken": self.never_taken(),
            "degenerate_warning": self.degenerate_warning(),
        }


@dataclass
class JudgeStats:
    """Verdict distribution for one judge gate, across a template's runs.

    Derived from `JUDGE_VERDICT` (the judge's own raw verdict), so it reports the FULL vocabulary a
    judge used — where `gate_stats` collapses the same gate to approve/reject. The two are
    complementary: a judge can pass every gate (no said-no) while returning the same verdict every
    time, and only this surface shows the second.
    """

    node_id: str
    verdicts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.verdicts.values())

    def degenerate_warning(self, *, min_runs: int = EDGE_STATS_MIN_RUNS) -> str:
        """The warning when a judge returns one verdict over a real sample — a do-nothing selector.

        Same sample discipline as everything else on this surface: one outcome over three calls is a
        young judge, not a broken one, and a badge that fires there is the noise the gate exists to
        avoid.
        """
        if self.total < min_runs:
            return ""
        chosen = [(verdict, count) for verdict, count in self.verdicts.items() if count > 0]
        if len(chosen) == 1:
            return (
                f"`{self.node_id}` returned `{chosen[0][0]}` on all {self.total} verdicts — a "
                "judge with one outcome over this many calls is not discriminating"
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "verdicts": dict(self.verdicts),
            "total": self.total,
            "degenerate_warning": self.degenerate_warning(),
        }


@dataclass
class EdgeStats:
    """The edge-decision projection (PP-8): per-`branch` case and per-judge verdict distributions.

    A pure projection over a template's runs, alongside `gate_stats` on the same surface and under
    the same sample gate. It answers the graph-engineering question `branch`/`gate`/`judge` records
    left unaskable: a selector that has taken one case every time, or a case no run has ever
    reached, was journaled per-run and never aggregated.
    """

    branches: dict[str, BranchStats] = field(default_factory=dict)
    judges: dict[str, JudgeStats] = field(default_factory=dict)

    def warnings(self, *, min_runs: int = EDGE_STATS_MIN_RUNS) -> list[str]:
        """Every edge finding, for folding into the template card beside the said-no warnings."""
        out: list[str] = []
        for path in sorted(self.branches):
            out.extend(self.branches[path].warnings(min_runs=min_runs))
        for node_id in sorted(self.judges):
            warning = self.judges[node_id].degenerate_warning(min_runs=min_runs)
            if warning:
                out.append(warning)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "branches": {path: b.to_dict() for path, b in self.branches.items()},
            "judges": {node_id: j.to_dict() for node_id, j in self.judges.items()},
        }


def edge_stats(runs: list[list[dict[str, Any]]]) -> EdgeStats:
    """Aggregate `branch` case and judge verdict distributions across a template's runs.

    Takes a LIST of runs' event lists rather than one run's, because a distribution over a single
    run is not a distribution — "always case A" is only a finding once there is a history, and the
    cross-run count is the whole point. Pure over the events: the caller reads the sibling ledgers
    (it already does, for the template card) and this decides what the numbers mean.
    """
    seen_all: dict[str, set[str]] = {}
    routed_runs: dict[str, int] = {}
    case_counts: dict[str, dict[str, int]] = {}
    for events in runs or []:
        seen, taken = _branch_routing(events)
        for branch_path, labels in seen.items():
            seen_all.setdefault(branch_path, set()).update(labels)
        for branch_path, labels in taken.items():
            if not labels:
                continue  # the branch itself was skipped this run — it did not route
            routed_runs[branch_path] = routed_runs.get(branch_path, 0) + 1
            counts = case_counts.setdefault(branch_path, {})
            for label in labels:
                counts[label] = counts.get(label, 0) + 1

    branches: dict[str, BranchStats] = {}
    for branch_path, labels in seen_all.items():
        counts = case_counts.get(branch_path, {})
        branches[branch_path] = BranchStats(
            path=branch_path,
            cases={label: counts.get(label, 0) for label in sorted(labels)},
            routed_runs=routed_runs.get(branch_path, 0),
        )

    judges: dict[str, JudgeStats] = {}
    for events in runs or []:
        for event in events or []:
            if not isinstance(event, dict):
                continue
            if str(event.get("kind") or "") != "judge_verdict":
                continue
            node_id = str(event.get("node_id") or "")
            if not node_id:
                continue
            verdict = str(event.get("verdict") or "")
            stats = judges.setdefault(node_id, JudgeStats(node_id=node_id))
            stats.verdicts[verdict] = stats.verdicts.get(verdict, 0) + 1

    return EdgeStats(branches=branches, judges=judges)


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
            f"{stats.steps_completed} step{'s' if stats.steps_completed != 1 else ''} "
            f"completed, {stats.steps_failed} failed, {stats.steps_cached} served from cache"
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
            f"{stats.steps_failed} step{'s' if stats.steps_failed != 1 else ''} failed — check "
            f"what the run did with {'its output' if stats.steps_failed == 1 else 'their outputs'}"
        )
    return section


# ── §4.4 human-attention accounting (EVALUATION-SUBSTRATE, atom ES-16) ────────
#
# Autonomy's honest objective is attention saved without outcome regression. The events
# below already exist in the journal; this section is a QUERY over them — per the plan's
# own discipline, "computed by ledger query, stored nowhere new". A human-answered gate,
# a mid-flight edit, and a judge/human divergence each cost one unit of attention; an
# auto-approved gate deliberately costs none (nobody looked at it).

#: Half-life for the pending-attention debt decay, in days. A week: an intervention last
#: night should weigh on today's graduation question; one from a month ago should not.
ATTENTION_DEBT_HALF_LIFE_DAYS = 7.0

#: Runs before a trend verdict counts as evidence — same reasoning as FAKE_CHECK_MIN_RUNS:
#: "rising over 2 runs" is a sample-size artifact, not a signal.
ATTENTION_TREND_MIN_RUNS = 6


def _is_attention_event(event: dict[str, Any]) -> bool:
    """Did this event cost human attention?

    Human-answered gates (``GATE_RESOLVED`` without the ``auto`` answer marker),
    mid-flight edits, and judge/human divergences. Auto-approved gates are excluded on
    purpose: the whole point of the metric is what still NEEDS the user.
    """
    kind = str(event.get("kind") or "")
    if kind in ("user_edited_mid_flight", "judge_divergence"):
        return True
    if kind == "gate_resolved":
        answer = event.get("answer")
        return not (isinstance(answer, dict) and answer.get("auto"))
    return False


def attention_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One run's attention events, in stream order."""
    return [e for e in events if _is_attention_event(e)]


def attention_debt(
    event_times: list[float], *, now: float, half_life_days: float = ATTENTION_DEBT_HALF_LIFE_DAYS
) -> float:
    """Decayed pending-attention debt: recent interventions weigh ~1, old ones fade.

    Exponential half-life decay over event ages. Pure arithmetic over timestamps the
    events already carry — no store, no state. Events with no usable timestamp are
    skipped rather than guessed: a fabricated age would silently skew the trend the
    graduation proposal cites.
    """
    import math

    half_life_secs = max(1.0, half_life_days * 86400.0)
    debt = 0.0
    for ts in event_times:
        if not ts or ts > now:
            continue
        debt += math.pow(2.0, -((now - ts) / half_life_secs))
    return round(debt, 4)


def attention_trend(per_run_counts: list[int], *, min_runs: int = ATTENTION_TREND_MIN_RUNS) -> str:
    """``rising`` / ``falling`` / ``flat`` / ``""`` (insufficient sample).

    First-half vs second-half means over the per-run series, oldest first. Deliberately
    coarse: the consumer is a one-word chip on a proposal and the Learning page, not a
    statistics engine, and a verdict that needed explanation would not be glanceable.
    """
    if len(per_run_counts) < min_runs:
        return ""
    half = len(per_run_counts) // 2
    older = per_run_counts[:half]
    newer = per_run_counts[-half:]
    older_mean = sum(older) / len(older)
    newer_mean = sum(newer) / len(newer)
    if newer_mean > older_mean * 1.25 and newer_mean - older_mean >= 0.5:
        return "rising"
    if newer_mean < older_mean * 0.75 and older_mean - newer_mean >= 0.5:
        return "falling"
    return "flat"


def post_grant_rise(
    per_run: list[tuple[float, int]], granted_at: float, *, min_runs: int = 3
) -> bool:
    """The mechanical demotion signal: did attention RISE after the grant?

    ``per_run`` is (run start timestamp, attention event count), any order. True only
    when both sides have a real sample and the post-grant mean exceeds the pre-grant
    mean — a graduated scope the user keeps intervening in is not actually trusted,
    whatever the record says. This is the SIGNAL; acting on it (revocation) is the
    ladder's decision, not this projection's.
    """
    if not granted_at:
        return False
    before = [n for ts, n in per_run if ts and ts < granted_at]
    after = [n for ts, n in per_run if ts and ts >= granted_at]
    if len(before) < min_runs or len(after) < min_runs:
        return False
    return (sum(after) / len(after)) > (sum(before) / len(before)) + 0.25


@dataclass(frozen=True)
class AttentionStats:
    """The §4.4 summary for one scope (a workflow template), derived per query."""

    scope: str
    runs: int = 0
    attention_events: int = 0
    events_per_run: float = 0.0
    dwell_p50_secs: float = 0.0
    debt: float = 0.0
    trend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "runs": self.runs,
            "attention_events": self.attention_events,
            "events_per_run": self.events_per_run,
            "dwell_p50_secs": self.dwell_p50_secs,
            "debt": self.debt,
            "trend": self.trend,
        }

    def note(self) -> str:
        """One glanceable line for a promotion proposal to cite. Empty when no sample."""
        if not self.runs:
            return ""
        line = f"attention: {self.events_per_run}/run over {self.runs} runs"
        if self.dwell_p50_secs:
            line += f", p50 dwell {self.dwell_p50_secs:g}s"
        if self.trend:
            line += f", trend {self.trend}"
        return line


def attention_stats(
    scope: str, runs: list[tuple[float, list[dict[str, Any]]]], *, now: float
) -> AttentionStats:
    """Compute the per-scope summary from (run started_at, run events) pairs.

    Everything derives from the pairs: counts, per-run series (ordered by run start for
    the trend), dwell p50 from ``resolved_after_secs`` where the human gate path stamped
    it, and the decayed debt from each attention event's own timestamp (falling back to
    the run's start when an event carries none — a bounded approximation, biased old,
    never inventing recency).
    """
    ordered = sorted(runs, key=lambda pair: pair[0] or 0.0)
    per_run_counts: list[int] = []
    dwells: list[float] = []
    event_times: list[float] = []
    total = 0
    for started_at, events in ordered:
        hits = attention_events(events)
        per_run_counts.append(len(hits))
        total += len(hits)
        for e in hits:
            ts = _epoch(e.get("ts")) or started_at or 0.0
            if ts:
                event_times.append(ts)
            raw_dwell = e.get("resolved_after_secs")
            if isinstance(raw_dwell, (int, float)) and raw_dwell > 0:
                dwells.append(float(raw_dwell))
    n = len(ordered)
    return AttentionStats(
        scope=scope,
        runs=n,
        attention_events=total,
        events_per_run=round(total / n, 3) if n else 0.0,
        dwell_p50_secs=round(percentile(dwells, 50), 3) if dwells else 0.0,
        debt=attention_debt(event_times, now=now),
        trend=attention_trend(per_run_counts),
    )
