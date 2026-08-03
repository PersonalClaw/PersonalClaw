"""Run outcomes → template refinement: the acceptance discipline (LEARN-R2 / §3.1 — S73).

The flagship spoke, and the one with the most ways to go wrong. An optimizer that edits templates
from
run outcomes random-walks them under judge noise unless acceptance is strict, so §3.1's
"acceptance discipline" section is longer than its mechanism section — and this module is the
discipline, expressed as pure decisions the refiner pipeline applies.

The four gates, each with the failure it prevents:

* **Failure clustering first, LLM second.** A zero-cost pass over the Run Ledger groups failures by
  shared mechanism and ranks them by frequency × unresolvedness. The refiner proposes against the
  TOP cluster only. Without it, an LLM reads a hundred unrelated failures and proposes something
  plausible about none of them.
* **Median of 3 critic runs, with an epsilon margin.** §3.1 is blunt: single-run acceptance is
  provably indistinguishable from noise". The median of three is what makes a score a measurement.
* **Held-out replay (GateOK).** An accepted edit must IMPROVE its target and may regress every
  other cluster by at most epsilon. An edit that fixes one failure by breaking two is a regression
  that looks like progress on the metric it was written against.
* **The frozen region.** The refiner may touch prompts, retries, and gates. It may NEVER touch the
  template id, its triggers, or its surfacing metadata — those decide WHEN a template runs, and a
  self-editing system that can change its own trigger conditions is one whose behaviour drifts
  without anyone approving the drift.

**Measured before writing.** Every prerequisite is in place: `journal.LEDGER_KINDS` carries all
five events the refiner reads (`step_completed`/`step_failed`/`step_skipped`/`gate_resolved`/
`run_abandoned`) plus `user_edited_mid_flight` — the "gold" signal §3.1 names, because a repeated
identical hand-fix is a user telling you what the template should have said. And `mutations.OpKind`
is a
CLOSED vocabulary of ten ops, so a diff is expressed in the engine's own terms rather than a second
edit language that would need its own validator.

Pure functions. Nothing here calls a model, writes a template, or files a proposal — the pipeline
does that, and keeping the decisions separable is what makes them testable without a judge in the
loop.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Ledger kinds the refiner reads. Verified against `journal.LEDGER_KINDS` by a test, because a
#: renamed event would starve the refiner silently — it sees zero failures and propose
#: nothing, which is indistinguishable from a healthy template.
EVIDENCE_KINDS: tuple[str, ...] = (
    "step_completed",
    "step_failed",
    "step_skipped",
    "gate_rejected",
    "gate_resolved",
    "run_abandoned",
    "user_edited_mid_flight",
)

#: Minimum runs of evidence before ANY diff may be proposed (§3.1's power discipline). Below this a
#: "pattern" is one bad afternoon, and a template edited from it is a template edited from noise.
MIN_RUNS_FOR_EVIDENCE = 3

#: Critic runs whose MEDIAN decides. Three, not one: §3.1 says single-run judge acceptance is
#: "provably indistinguishable from noise", and three is the smallest set with a median that
#: resists a single outlier.
CRITIC_RUNS = 3

#: How much the median must beat the current version by. Without a margin, judge jitter alone
#: accepts roughly half of all no-op diffs.
CRITIC_EPSILON = 0.05

#: The four named check scores every diff is judged on (§3.1). Named as data so a critic prompt and
#: the acceptance check cannot disagree about what was scored.
CHECK_SCORES: tuple[str, ...] = (
    "grounded_in_evidence",
    "preserves_existing_value",
    "specificity_and_reusability",
    "safe_to_publish",
)

#: How much a NON-target cluster may regress on held-out replay. 1% — small enough that a real
#: regression trips it, loose enough that judge noise on an unrelated cluster does not.
GATEOK_REGRESSION_EPS = 0.01

#: The minimum improvement on the TARGET cluster for an accept. Below this the edit is churn: a new
#: version, a new diff to review, and no measurable difference.
MIN_TARGET_IMPROVEMENT = 0.02

#: Session-level stop rules (§3.1). A learning cycle that stops improving should STOP, not keep
#: spending: `k` consecutive rounds inside `eps` means the optimizer has converged or is stuck, and
#: both end the same way.
STAGNATION_ROUNDS = 5
STAGNATION_EPS = 0.001

#: Template fields the refiner may NEVER mutate. `id`/`triggers`/surfacing metadata decide WHEN a
#: template runs; a self-editing system that can change its own trigger conditions drifts without
#: anyone approving the drift. This is a denylist rather than an allowlist because the engine's node
#: config is open-ended — an allowlist would silently forbid a legitimate prompt edit next month.
FROZEN_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "triggers",
        "trigger",
        "surface_mode",
        "surfacing",
        "keywords",
        "negative_triggers",
        "version",
    }
)

#: Ops a template diff may use, as a SUBSET of the engine's own `OpKind`. Deliberately narrow: the
#: run-control ops (`rewind`/`run_from`/`fork`/`skip`) act on a LIVE run, not on a stored template,
#: so a diff containing one is a category error rather than a risky edit.
DIFF_OPS: frozenset[str] = frozenset({"update_node", "insert", "delete", "move", "set_input"})


class RiskTier(str, Enum):
    """Deterministic risk tier by edit TYPE (§3.1, batch-5).

    Proposal Inbox metadata ONLY — for ordering, filtering, and bulk-accept ergonomics. §3.1 is
    explicit that any "auto" tier is guardrail-violating: human-installs is absolute, so
    there is deliberately no `AUTO` member.
    """

    LOW = "low"
    REVIEW = "review"
    MANUAL_ONLY = "manual_only"


#: Which tier each op earns. Routing/params are mechanical; a prompt edit changes what the model is
#: told and deserves a read; a delete is destructive and never gets bulk-accept ergonomics.
_OP_RISK: dict[str, str] = {
    "set_input": RiskTier.LOW.value,
    "move": RiskTier.LOW.value,
    "update_node": RiskTier.REVIEW.value,
    "insert": RiskTier.REVIEW.value,
    "delete": RiskTier.MANUAL_ONLY.value,
}


def risk_tier(ops: list[dict[str, Any]]) -> str:
    """The tier for a whole diff: the RISKIEST op in it.

    Max rather than average, because a diff is accepted or rejected as a unit — a destructive
    delete bundled with four parameter tweaks is a destructive diff, and averaging hands it
    bulk-accept ergonomics.
    """
    tiers = [_OP_RISK.get(str(op.get("op", "")), RiskTier.MANUAL_ONLY.value) for op in ops or []]
    if not tiers:
        return RiskTier.MANUAL_ONLY.value
    order = [RiskTier.LOW.value, RiskTier.REVIEW.value, RiskTier.MANUAL_ONLY.value]
    return max(tiers, key=order.index)


# ── failure clustering (the front half) ──

#: Tokens stripped when deriving a failure signature. Run-specific noise — ids, paths, numbers, and
#: timestamps — makes every failure look unique, which defeats clustering.
_NOISE_RE = re.compile(
    r"""
    (\b[0-9a-f]{8,}\b)              # hashes / run ids
    | (\b\d+(\.\d+)?(ms|s|m|h)?\b)  # numbers, durations
    | (/[\w./-]+)                   # paths
    | (\b\d{4}-\d{2}-\d{2}[\w:.+-]*) # timestamps
    """,
    re.VERBOSE | re.IGNORECASE,
)


def failure_signature(text: str) -> str:
    """A stable signature for a failure message.

    Noise-stripped and normalized so two failures from the same MECHANISM collide. Without this
    every failure carries its own run id and path, so a hundred instances of one bug cluster into a
    hundred clusters of one — and the refiner proposes against a cluster of size 1, which is the
    power-discipline floor it is supposed to respect.
    """
    if not text:
        return ""
    stripped = _NOISE_RE.sub(" ", str(text).lower())
    words = [w for w in re.findall(r"[a-z_]{3,}", stripped)]
    return " ".join(words[:12])


@dataclass
class Cluster:
    """A group of failures sharing a mechanism."""

    signature: str
    node: str = ""
    count: int = 0
    runs: list[str] = field(default_factory=list)
    #: Runs where this failure was later resolved (a retry succeeded, or the user fixed it by
    #: hand). `unresolvedness` reads this: a failure that resolves itself is a worse refiner target
    #: than one that never does, however often it occurs.
    resolved: int = 0
    #: Verbatim `user_edited_mid_flight` ops seen against this node — §3.1's "gold": a repeated
    #: identical hand-fix is the user saying what the template should say.
    hand_fixes: list[str] = field(default_factory=list)

    @property
    def unresolvedness(self) -> float:
        """Share of occurrences that never resolved, in [0, 1]."""
        if self.count <= 0:
            return 0.0
        return max(0.0, (self.count - self.resolved)) / self.count

    @property
    def rank(self) -> float:
        """frequency × unresolvedness (§3.1's ranking).

        The product, not the sum: a frequent failure that self-heals is not worth an edit, and
        neither is a permanent failure that happened once. Only the conjunction is a target.
        """
        return self.count * self.unresolvedness

    @property
    def distinct_runs(self) -> int:
        return len(set(self.runs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "node": self.node,
            "count": self.count,
            "resolved": self.resolved,
            "unresolvedness": round(self.unresolvedness, 4),
            "rank": round(self.rank, 4),
            "distinct_runs": self.distinct_runs,
            "hand_fixes": list(self.hand_fixes),
        }


def cluster_failures(events: list[dict[str, Any]]) -> list[Cluster]:
    """Cold-pass clustering over ledger events, ranked worst-first. Pure, zero LLM calls.

    §3.1's tier discipline: this runs BEFORE any model is touched, so a template with no failure
    pattern costs nothing to examine. Events of unknown kinds are ignored rather than guessed at —
    the ledger is append-only and gains kinds over time, and a refiner that reacted to an event it
    does not understand would propose against a signal nobody designed.
    """
    clusters: dict[tuple[str, str], Cluster] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind", "") or "")
        if kind not in EVIDENCE_KINDS:
            continue
        node = str(event.get("node") or event.get("path") or "")
        run_id = str(event.get("run_id", "") or "")

        if kind in ("step_failed", "run_abandoned", "gate_rejected"):
            signature = failure_signature(
                str(event.get("error") or event.get("reason") or event.get("user_comment") or kind)
            )
            key = (signature, node)
            entry = clusters.setdefault(key, Cluster(signature=signature, node=node))
            entry.count += 1
            entry.runs.append(run_id)
        elif kind == "step_skipped":
            # A repeatedly SKIPPED step is a failure of the template, not of the run: the user
            # keeps saying this step should not be here. Clustered as its own mechanism so the
            # proposal is a deletion rather than a prompt rewrite.
            key = (f"skipped {node}", node)
            entry = clusters.setdefault(key, Cluster(signature=key[0], node=node))
            entry.count += 1
            entry.runs.append(run_id)
        elif kind == "user_edited_mid_flight":
            ops = event.get("ops")
            rendered = ", ".join(sorted(str(o) for o in ops)) if isinstance(ops, list) else str(ops)
            key = (f"hand-fixed {node}", node)
            entry = clusters.setdefault(key, Cluster(signature=key[0], node=node))
            entry.count += 1
            entry.runs.append(run_id)
            entry.hand_fixes.append(rendered)
        elif kind == "step_completed":
            # A completion RESOLVES any failure clustered on the same node: the mechanism stopped
            # biting. Counted rather than removed, because "fails then succeeds on retry" is a real
            # (lower-priority) target — a flaky step is worth a retry op, just not the top slot.
            for (_sig, cnode), entry in clusters.items():
                if cnode == node:
                    entry.resolved += 1

    return sorted(clusters.values(), key=lambda c: (-c.rank, -c.count, c.signature))


def top_cluster(
    clusters: list[Cluster], *, min_runs: int = MIN_RUNS_FOR_EVIDENCE
) -> Cluster | None:
    """The cluster worth proposing against, or None when nothing has enough evidence.

    Enforces the power-discipline floor HERE rather than downstream, so an under-evidenced cluster
    never reaches a model at all — the LLM tier is the expensive one, and a proposal built from two
    runs would be rejected later anyway after paying for it.
    """
    for cluster in clusters or []:
        if cluster.distinct_runs >= max(1, min_runs) and cluster.rank > 0:
            return cluster
    return None


# ── the frozen region ──


@dataclass
class OpVerdict:
    """Whether one typed op is a legal template edit."""

    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


def check_op(op: dict[str, Any]) -> OpVerdict:
    """Whether the refiner may apply this op to a stored template. Fails CLOSED.

    Three refusals, each a different category of wrong:

    * an op OUTSIDE `DIFF_OPS` — the run-control ops act on a live run, not a template, so including
      one is a category error;
    * an op touching a FROZEN field — those decide when a template runs, and letting a self-editing
      system change its own trigger conditions is drift nobody approved;
    * an unrecognized op name — validated against the engine's own `OpKind` so a typo cannot be a
      silently-ignored no-op inside an accepted diff.
    """
    from personalclaw.workflows.mutations import OpKind

    name = str(op.get("op", "") or "")
    if not name:
        return OpVerdict(allowed=False, reason="the op has no `op` name")
    known = {k.value for k in OpKind}
    if name not in known:
        return OpVerdict(
            allowed=False,
            reason=f"{name!r} is not one of the engine's ops ({', '.join(sorted(known))})",
        )
    if name not in DIFF_OPS:
        return OpVerdict(
            allowed=False,
            reason=f"{name!r} acts on a live RUN, not a stored template — a template diff cannot "
            "contain it",
        )
    touched = set()
    for container in ("fields", "config", "set", "patch"):
        value = op.get(container)
        if isinstance(value, dict):
            touched |= {str(k) for k in value}
    field_name = op.get("field")
    if field_name:
        touched.add(str(field_name))
    frozen = sorted(touched & FROZEN_FIELDS)
    if frozen:
        return OpVerdict(
            allowed=False,
            reason=f"touches the frozen region ({', '.join(frozen)}) — the refiner edits "
            "prompts, "
            "retries and gates, never what makes a template fire",
        )
    return OpVerdict(allowed=True)


def check_diff(ops: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Whether a whole diff is applicable. Returns `(ok, refusals)`.

    ALL-or-nothing: one illegal op rejects the diff rather than being dropped from it. A partially
    applied diff is a template the refiner did not propose and the user did not review — nobody
    authored the thing that would land.
    """
    refusals: list[str] = []
    if not ops:
        return False, ["the diff is empty"]
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            refusals.append(f"op {index} is not an object")
            continue
        verdict = check_op(op)
        if not verdict.allowed:
            refusals.append(f"op {index} ({op.get('op', '?')}): {verdict.reason}")
    return (not refusals), refusals


# ── the median-of-3 critic ──


@dataclass
class CriticScore:
    """One critic run's four named check scores.

    Missing scores default to 0.0, the reject-by-default §3.1 requires: an LLM that failed to
    produce a parseable score has not endorsed anything, and treating an absent score as neutral
    would let a parse failure pass a diff.
    """

    scores: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """Mean of the four named checks, absent ones counting as 0."""
        return sum(float(self.scores.get(name, 0.0)) for name in CHECK_SCORES) / len(CHECK_SCORES)

    @property
    def complete(self) -> bool:
        return all(name in self.scores for name in CHECK_SCORES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": {name: round(float(self.scores.get(name, 0.0)), 4) for name in CHECK_SCORES},
            "total": round(self.total, 4),
            "complete": self.complete,
        }


def median(values: list[float]) -> float:
    """The median. Written out rather than imported so the tie rule is visible.

    An even-length list averages the middle two — but the critic always runs an ODD number of times
    (`CRITIC_RUNS = 3`) precisely so the median is an actual observation rather than a synthetic
    midpoint between two disagreeing judges.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass
class CriticVerdict:
    """The critic's decision for one diff."""

    accepted: bool
    median: float
    baseline: float
    reason: str = ""
    runs: int = 0

    @property
    def margin(self) -> float:
        return self.median - self.baseline

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "median": round(self.median, 4),
            "baseline": round(self.baseline, 4),
            "margin": round(self.margin, 4),
            "runs": self.runs,
            "reason": self.reason,
        }


def judge(
    scores: list[CriticScore],
    *,
    baseline: float,
    epsilon: float = CRITIC_EPSILON,
    required_runs: int = CRITIC_RUNS,
) -> CriticVerdict:
    """The median-of-3 acceptance decision. Rejects by default.

    Two independent refusals:

    * **Too few runs.** Fewer than `required_runs` scores means there is no median. §3.1: single
      run acceptance is indistinguishable from noise, so a short critic pass rejects rather than
      falling back to a mean.
    * **Margin below epsilon.** Judge jitter alone would otherwise accept roughly half of all no-op
      diffs, and each accepted no-op is a new version, a new review, and no improvement.
    """
    if len(scores) < max(1, required_runs):
        return CriticVerdict(
            accepted=False,
            median=0.0,
            baseline=baseline,
            runs=len(scores),
            reason=f"only {len(scores)} critic run(s); {required_runs} needed for a median "
            "resists a single outlier",
        )
    totals = [s.total for s in scores]
    mid = median(totals)
    if mid <= baseline + epsilon:
        return CriticVerdict(
            accepted=False,
            median=mid,
            baseline=baseline,
            runs=len(scores),
            reason=f"median {mid:.3f} does not beat the current version ({baseline:.3f}) by more "
            f"than {epsilon:.3f} — inside judge noise",
        )
    return CriticVerdict(accepted=True, median=mid, baseline=baseline, runs=len(scores))


# ── the held-out replay gate (GateOK) ──


@dataclass
class GateResult:
    """The held-out replay verdict for one diff."""

    passed: bool
    target_delta: float = 0.0
    worst_regression: float = 0.0
    regressed: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "target_delta": round(self.target_delta, 4),
            "worst_regression": round(self.worst_regression, 4),
            "regressed": list(self.regressed),
            "reason": self.reason,
        }


def gate_ok(
    *,
    target: str,
    before: dict[str, float],
    after: dict[str, float],
    eps: float = GATEOK_REGRESSION_EPS,
    min_improvement: float = MIN_TARGET_IMPROVEMENT,
) -> GateResult:
    """GateOK: improve the target cluster, regress nothing else by more than `eps`. Pure.

    The machine-checkable form of §3.1's held-out replay. Both halves are load-bearing:

    * requiring TARGET improvement stops a diff being accepted for a coincidental gain elsewhere; *
    bounding OTHER clusters stops an edit that fixes one failure by breaking two — a regression
    that
      looks like progress on the metric it was written against.

    A target absent from either map FAILS the gate rather than scoring 0. An unmeasured target
    means the replay did not exercise the thing the diff claims to fix, so there is no evidence
    either way, and
    "no evidence" must not read as "no regression".
    """
    if target not in before or target not in after:
        return GateResult(
            passed=False,
            reason=f"the target cluster {target!r} was not scored in the held-out replay, so the "
            "diff's claimed improvement is unmeasured",
        )
    delta = after[target] - before[target]
    regressed: list[str] = []
    worst = 0.0
    for cluster, prior in before.items():
        if cluster == target:
            continue
        current = after.get(cluster)
        if current is None:
            # A cluster the replay stopped scoring is treated as a regression: it may have started
            # erroring outright, and silence is not a pass.
            regressed.append(cluster)
            worst = max(worst, 1.0)
            continue
        drop = prior - current
        if drop > eps:
            regressed.append(cluster)
            worst = max(worst, drop)

    if delta < min_improvement:
        return GateResult(
            passed=False,
            target_delta=delta,
            worst_regression=worst,
            regressed=regressed,
            reason=f"the target cluster improved by only {delta:.3f}; {min_improvement:.3f} is the "
            "floor below which the edit is churn",
        )
    if regressed:
        return GateResult(
            passed=False,
            target_delta=delta,
            worst_regression=worst,
            regressed=sorted(regressed),
            reason=f"{len(regressed)} other cluster(s) regressed by up to {worst:.3f} (max allowed "
            f"{eps:.3f}) — fixing one failure by breaking others is not an improvement",
        )
    return GateResult(passed=True, target_delta=delta, regressed=[])


def should_stop(
    history: list[float], *, k: int = STAGNATION_ROUNDS, eps: float = STAGNATION_EPS
) -> tuple[bool, str]:
    """Whether a learning cycle has stopped improving and should end. Returns `(stop, reason)`.

    §3.1's session-level stop rule. A cycle proposing after convergence spends budget to
    produce diffs the critic will reject — and the k-round window distinguishes "converged" from
    "one flat round".
    """
    if len(history) < max(2, k):
        return False, ""
    window = history[-k:]
    spread = max(window) - min(window)
    if spread <= eps:
        return True, (
            f"gate score moved {spread:.4f} across {k} rounds (≤{eps}) — converged or "
            "is stuck; either way it should stop rather than keep spending"
        )
    return False, ""


# ── the assembled decision ──


@dataclass
class Decision:
    """Everything that had to be true for a diff to reach a human.

    One object rather than four separate checks at the call site, because §3.1's discipline is a
    CONJUNCTION and a caller that forgot one gate would have a refiner that random-walks templates
    while appearing to be gated.
    """

    surfaced: bool
    tier: str = RiskTier.MANUAL_ONLY.value
    refusals: list[str] = field(default_factory=list)
    critic: CriticVerdict | None = None
    gate: GateResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surfaced": self.surfaced,
            "tier": self.tier,
            "refusals": list(self.refusals),
            "critic": self.critic.to_dict() if self.critic else None,
            "gate": self.gate.to_dict() if self.gate else None,
        }


def evaluate_diff(
    *,
    ops: list[dict[str, Any]],
    scores: list[CriticScore],
    baseline: float,
    target: str,
    before: dict[str, float],
    after: dict[str, float],
) -> Decision:
    """Run every gate. A diff is surfaced only if ALL pass.

    Order is applicability → critic → GateOK, cheapest and most decisive first: an op touching the
    frozen region is unfixable, so paying three critic runs to discover that would be waste.

    Sub-threshold diffs are DROPPED SILENTLY (§3.1) — `Decision` records why in the log, but the
    user sees only defensible proposals. A review queue full of rejected machine guesses trains
    people to stop reading it.
    """
    ok, refusals = check_diff(ops)
    tier = risk_tier(ops)
    if not ok:
        return Decision(surfaced=False, tier=tier, refusals=refusals)

    verdict = judge(scores, baseline=baseline)
    if not verdict.accepted:
        return Decision(surfaced=False, tier=tier, refusals=[verdict.reason], critic=verdict)

    gate = gate_ok(target=target, before=before, after=after)
    if not gate.passed:
        return Decision(
            surfaced=False, tier=tier, refusals=[gate.reason], critic=verdict, gate=gate
        )
    return Decision(surfaced=True, tier=tier, critic=verdict, gate=gate)


# ── evidence manifest ──


@dataclass
class EvidenceManifest:
    """What a proposal must carry to be falsifiable (§3.1's EVIDENCE rule).

    Without run ids a reviewer cannot check the claim; without the evaluating model they cannot
    weigh it; without `measured_at` they cannot tell whether it still holds. A proposal that cannot
    be checked is an assertion, and the whole point of the ledger is that assertions are checkable.
    """

    metric: str
    value: float
    measured_at: str
    run_ids: list[str] = field(default_factory=list)
    evaluating_model: str = ""
    confidence: float = 0.0

    @property
    def falsifiable(self) -> bool:
        return bool(self.run_ids and self.metric and self.measured_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": round(self.value, 4),
            "measured_at": self.measured_at,
            "run_ids": list(self.run_ids),
            "evaluating_model": self.evaluating_model,
            "confidence": round(self.confidence, 4),
            "falsifiable": self.falsifiable,
        }


def build_manifest(
    *, cluster: Cluster, decision: Decision, measured_at: str, model: str = ""
) -> EvidenceManifest:
    """The manifest for a surfaced diff.

    `confidence` is the critic's margin scaled into [0, 1], not a number the model asserted about
    itself. A self-reported confidence is the same ornamental signal §2.5 rejects for helpfulness —
    this one is derived from the measurement that actually gated the diff.
    """
    margin = decision.critic.margin if decision.critic else 0.0
    return EvidenceManifest(
        metric=f"cluster:{cluster.signature}",
        value=decision.gate.target_delta if decision.gate else 0.0,
        measured_at=measured_at,
        run_ids=sorted(set(cluster.runs))[:20],
        evaluating_model=model,
        confidence=max(0.0, min(1.0, margin / max(1e-9, 1.0 - CRITIC_EPSILON))),
    )


def canary_verdict(*, before: float, after: float, runs: int, min_runs: int = 3) -> str:
    """The post-acceptance verdict for an applied diff (LEARN-R16 predict-then-verify).

    Five outcomes, and `HARMFUL` is the one that matters: §3.1 auto-FILES a revert proposal for it,
    through the queue, never silently. Under `min_runs` the answer is `PENDING` rather
    than a guess — declaring a diff effective after one run is how a lucky run becomes a permanent
    change.
    """
    if runs < max(1, min_runs):
        return "PENDING"
    delta = after - before
    if delta <= -MIN_TARGET_IMPROVEMENT:
        return "HARMFUL"
    if abs(delta) < STAGNATION_EPS:
        return "INEFFECTIVE"
    if delta >= MIN_TARGET_IMPROVEMENT:
        return "EFFECTIVE"
    return "PARTIALLY_EFFECTIVE" if delta > 0 else "MIXED"


def clamp01(value: float) -> float:
    """Bound a score to [0, 1]. NaN reads as 0.0 — the reject-by-default direction."""
    if value is None or math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))
