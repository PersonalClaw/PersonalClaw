"""§3.5 (LEARN-R17): trajectory-variance tier migration — agentic ↔ fixed.

A two-way ``tier_migration`` PROPOSAL producer over the Run Ledger. Distinct from §1's promotion
ladder (which is about entity KIND) and from the curator's scope-widening (which also files a
``TIER_MIGRATION`` but is about *surfacing* scope): this is the execution TIER *within* a template
— whether its steps run as agentic LLM stages or as fixed deterministic actions.

The plan recorded this clause BLOCKED three times, on the same two absent inputs. Both are present
now, so the producer is finally buildable **without touching a core contract**:

* **Tier is DERIVED from structure, never stored.** ``WorkflowDef`` has no execution-tier field and
  this does NOT add one — a new state-shape field would collide with PLATFORM-PRIMITIVES PP-16
  (Loop→WorkflowRun), which is exactly the kind of clean break the roadmap wants paid deliberately,
  not smuggled in behind a detector. Instead :func:`classify_tier` walks the run's own pinned spec
  (``store.read_spec``) and reads each node's ``kind``: a template is ``agentic`` when it holds
  a STAGE/INFER node — the model-tunable, token-consuming stages ``models.LLM_KINDS`` names — and
  ``fixed`` when its work nodes are all zero-token ACTION/TRANSFORM dispatch with no model node at
  all. The spec is the exact structure that produced the trajectories below.

* **Cross-run variance is a pure ledger PROJECTION (PP-7).** ``introspection.trajectory_signature``
  already collapses a run into its decision-path hash and ``service.introspect`` already aggregates
  the sibling distribution. This reads the same ``(signature, failed)`` history
  ``introspection.trajectory_regression`` consumes — the machinery the earlier BLOCKED notes said
  did not exist yet.

The heuristic (§3.5, stated so the thresholds are tunable):

* **agentic → fixed (distill):** an agentic template whose runs took the SAME path almost every time
  and mostly SUCCEEDED is not using its latitude — the same steps in the same order every run is a
  fixed procedure paying an agent's price. Propose distilling those stages into a deterministic
  template (the ~5× cheaper tier); projected LLM savings ride the proposal as evidence.
* **fixed → agentic (promote):** a fixed template whose deterministic steps FAIL repeatedly — and so
  deviate across many trajectory classes — cannot handle the domain's variance. Propose promoting to
  an agentic stage that can adapt where the rigid step cannot; the reliability gap is evidence.

Detection is zero-LLM and zero-embedding (pure ledger statistics, §3.5). A draft is a PENDING
proposal the human installs, deduped by the shared content fingerprint like every other proposer via
``proposals.enqueue``. Nothing here installs — the module PROPOSES; the human decides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

TIER_AGENTIC = "agentic"
TIER_FIXED = "fixed"
#: Neither an agentic nor a purely-deterministic template — an empty tree, containers only, or a mix
#: we should not guess about (a lone ``visualize`` makes a model call but is not an agentic stage).
#: No proposal is ever filed for an unknown tier: migrating a shape we cannot classify is a guess.
TIER_UNKNOWN = "unknown"

DIRECTION_DISTILL = "agentic_to_fixed"
DIRECTION_PROMOTE = "fixed_to_agentic"

#: Minimum terminal sibling runs before a cross-run variance claim is evidence. A "the agent always
#: does the same thing" needs a real sample — the same discipline ``introspection``'s cross-run
#: badges enforce. Set below their 10-run said-no floor because a tier-migration draft is a
#: reversible PENDING proposal, not a standing badge on the library, and because a repeatedly
#: failing deterministic template should not have to fail ten times before anyone is asked.
TIER_MIGRATION_MIN_RUNS = 5

#: Share of runs on the single dominant trajectory at or above which an agentic template counts as
#: low-variance ("negligible branching", §3.5) and therefore a DISTILL candidate.
DISTILL_MIN_DOMINANCE = 0.9

#: An agentic template that fails more often than this is NOT a distill candidate: freezing a flaky
#: agentic path into a rigid one bakes the failure in. §3.5 distills what already WORKS.
DISTILL_MAX_FAILURE_RATE = 0.2

#: Failing runs (≥) before a fixed template's repeated deviation is a PROMOTE signal — §3.5's "≥M
#: failures … across K runs". Mirrors the ≥3 pattern floor used elsewhere in the subsystem
#: (``mining.MIN_TRACE_FREQUENCY``, the procedural ≥3-failure synthesis).
PROMOTE_MIN_FAILURES = 3

#: Failure rate (≥) at which a fixed template's deterministic steps are judged unable to handle the
#: domain's variance. Either this OR trajectory deviation (more than one path class) trips the
#: promote signal, so §3.5's "repeatedly failing" and the task's "frequent deviation" both fire it.
PROMOTE_MIN_FAILURE_RATE = 0.4

#: The ~5× agentic:deterministic cost ratio §3.5 cites, used only to PROJECT the evidence figure — a
#: projection labelled as such on the proposal, never presented as a measured number.
AGENTIC_COST_MULTIPLE = 5.0

#: How many recent sibling runs the wiring scan reads. Bounded like ``mining.TRACE_SCAN_LIMIT`` for
#: the same reason: this runs on a real box with a real ledger, and an unbounded scan turns a
#: background run-end pass into a stall.
SIBLING_SCAN_LIMIT = 60


# ── tier classification (derived from structure, never a stored field) ──


def _node_kinds(spec: Any) -> list[str]:
    """Every node ``kind`` in a spec tree, in no particular order.

    Accepts a run's pinned spec ``dict`` (``store.read_spec`` → ``{"root": {...}}`` or a bare node
    dict), a ``WorkflowDef`` (has ``.root``), or a ``Node`` (has ``.child_nodes``). Reading ``kind``
    straight off the raw structure keeps this a pure projection — reconstructing a ``WorkflowDef``
    would drag the whole model layer in for a question three string comparisons answer.
    """
    out: list[str] = []

    def _walk_dict(node: Any) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if isinstance(kind, str) and kind:
            out.append(kind)
        for child in node.get("children") or []:
            _walk_dict(child)
        _walk_dict(node.get("body"))
        for case in (node.get("cases") or {}).values():
            _walk_dict(case)
        _walk_dict(node.get("default"))

    def _walk_node(node: Any) -> None:
        kind = getattr(node, "kind", None)
        value = getattr(kind, "value", kind)
        if isinstance(value, str) and value:
            out.append(value)
        for child in node.child_nodes():
            _walk_node(child)

    if isinstance(spec, dict):
        root = spec.get("root")
        _walk_dict(root if isinstance(root, dict) else spec)
    elif hasattr(spec, "child_nodes"):
        _walk_node(spec)
    elif hasattr(spec, "root"):
        _walk_node(spec.root)
    return out


def classify_tier(spec: Any) -> str:
    """Derive a template's execution TIER from its node structure. Pure; adds no core field.

    * :data:`TIER_AGENTIC` — the tree holds a STAGE or INFER node (``models.LLM_KINDS``): the
      model-tunable stages whose tokens a distillation removes.
    * :data:`TIER_FIXED` — one or more deterministic WORK nodes (ACTION/TRANSFORM) and NO
      model-consuming node at all (no STAGE/INFER/VISUALIZE): a purely deterministic procedure.
    * :data:`TIER_UNKNOWN` — neither, so nothing should be migrated on a guess.

    Bound to the engine's own kind algebra (imported from ``models``) not a local copy of the kind
    list, so a new LLM kind added there is classified agentic here without a second edit.
    """
    try:
        from personalclaw.workflows.models import LLM_KINDS, NodeKind
    except Exception:  # pragma: no cover - import failure is not a runtime path
        logger.debug("tier_migration: models unavailable for classification", exc_info=True)
        return TIER_UNKNOWN

    llm_values = {k.value for k in LLM_KINDS}
    model_values = llm_values | {NodeKind.VISUALIZE.value}
    deterministic_work = {NodeKind.ACTION.value, NodeKind.TRANSFORM.value}

    kinds = set(_node_kinds(spec))
    if kinds & llm_values:
        return TIER_AGENTIC
    if (kinds & deterministic_work) and not (kinds & model_values):
        return TIER_FIXED
    return TIER_UNKNOWN


# ── the pure decision ──


@dataclass
class TierMigration:
    """One proposed execution-tier migration, with the ledger evidence that earned it.

    A value object: :func:`tier_migration` decides and returns one (or ``None``), and
    :func:`file_tier_migration` files it. The split mirrors ``curator.promotion_suggestions`` /
    ``file_promotion_suggestions`` — the decision is pure and testable without a proposal store.
    """

    template: str
    direction: str
    from_tier: str
    to_tier: str
    runs: int
    distinct_paths: int
    dominant_share: float
    failure_rate: float
    failing_runs: int
    mean_cost_usd: float = 0.0
    projected_saving_usd: float = 0.0

    def title(self) -> str:
        if self.direction == DIRECTION_DISTILL:
            return f"Distill {self.template} to a fixed template — it never varies"[:120]
        return f"Promote {self.template} to an agentic stage — it keeps failing"[:120]

    def evidence_line(self) -> str:
        return (
            f"{self.runs} terminal runs, {self.distinct_paths} distinct trajectory path(s), "
            f"{self.failing_runs} failed. Pure ledger statistics — no model was consulted."
        )

    def body(self) -> str:
        if self.direction == DIRECTION_DISTILL:
            saving = (
                f" Projected saving ≈ ${self.projected_saving_usd:.4f}/run"
                if self.projected_saving_usd > 0
                else ""
            )
            return (
                f"`{self.template}` is an AGENTIC template, but its last {self.runs} terminal runs "
                f"took the SAME path {self.dominant_share:.0%} of the time and succeeded "
                f"{1.0 - self.failure_rate:.0%} of the time. The agent is not using its latitude — "
                "the same steps in the same order every run is a fixed procedure paying an agent's "
                "price.\n\n"
                "Proposed: DISTILL those agentic stages into a deterministic (fixed) template."
                f"{saving} (agentic execution costs ~{AGENTIC_COST_MULTIPLE:g}× a deterministic "
                "step, so the LLM cost of those stages goes away).\n\n"
                f"Evidence: {self.evidence_line()}"
            )
        return (
            f"`{self.template}` is a FIXED (deterministic) template, but its last {self.runs} "
            f"terminal runs FAILED {self.failure_rate:.0%} of the time ({self.failing_runs} of "
            f"{self.runs}) and deviated across {self.distinct_paths} distinct path(s). "
            "A rigid step that keeps failing cannot handle the domain's variance.\n\n"
            "Proposed: PROMOTE the failing step(s) to an agentic stage, so the agent can adapt "
            f"where the fixed step cannot — trading ~{AGENTIC_COST_MULTIPLE:g}× the per-step cost "
            "for the reliability the rigid step lacks.\n\n"
            f"Evidence: {self.evidence_line()}"
        )


def tier_migration(
    template: str,
    tier: str,
    runs: list[tuple[str, bool]],
    *,
    mean_cost_usd: float = 0.0,
    min_runs: int = TIER_MIGRATION_MIN_RUNS,
    distill_min_dominance: float = DISTILL_MIN_DOMINANCE,
    distill_max_failure_rate: float = DISTILL_MAX_FAILURE_RATE,
    promote_min_failures: int = PROMOTE_MIN_FAILURES,
    promote_min_failure_rate: float = PROMOTE_MIN_FAILURE_RATE,
) -> TierMigration | None:
    """Decide whether a template should migrate tier, from its cross-run trajectory variance.

    ``runs`` is ``(trajectory_signature, failed)`` per terminal run — the exact shape
    ``introspection.trajectory_regression`` consumes, so the caller reuses the history it already
    builds. ``tier`` is :func:`classify_tier`'s output for the same template. Deterministic and
    side-effect free: same inputs, same verdict, so it can be unit-tested with fake stats and run on
    every terminal run without a model call.

    Returns a :class:`TierMigration` or ``None``. ``None`` is the common, correct answer — an
    unremarkable template (moderate variance, few failures) should not generate a proposal, the same
    way ``trajectory_regression`` stays silent below its floors.
    """
    clean = [(str(sig), bool(failed)) for sig, failed in (runs or []) if sig]
    total = len(clean)
    if total < max(1, min_runs) or tier not in (TIER_AGENTIC, TIER_FIXED):
        return None

    counts: dict[str, int] = {}
    for sig, _failed in clean:
        counts[sig] = counts.get(sig, 0) + 1
    distinct = len(counts)
    dominant = max(counts.values())
    dominant_share = dominant / total
    failing = sum(1 for _sig, failed in clean if failed)
    failure_rate = failing / total

    if tier == TIER_AGENTIC:
        # Distill: nearly always the same path AND mostly successful. A low-variance path that keeps
        # failing is a bug to fix, not a procedure to freeze — hence the failure ceiling.
        if dominant_share >= distill_min_dominance and failure_rate <= distill_max_failure_rate:
            projected = (
                round(mean_cost_usd * (1.0 - 1.0 / AGENTIC_COST_MULTIPLE), 6)
                if mean_cost_usd > 0
                else 0.0
            )
            return TierMigration(
                template=template,
                direction=DIRECTION_DISTILL,
                from_tier=TIER_AGENTIC,
                to_tier=TIER_FIXED,
                runs=total,
                distinct_paths=distinct,
                dominant_share=dominant_share,
                failure_rate=failure_rate,
                failing_runs=failing,
                mean_cost_usd=mean_cost_usd,
                projected_saving_usd=projected,
            )
        return None

    # tier == TIER_FIXED — promote: repeatedly failing, which for a deterministic template also
    # shows up as trajectory deviation (failed/skipped verdicts are part of the signature). Either
    # a high failure rate or more than one path class trips it, once there are enough failures.
    if failing >= promote_min_failures and (
        failure_rate >= promote_min_failure_rate or distinct > 1
    ):
        return TierMigration(
            template=template,
            direction=DIRECTION_PROMOTE,
            from_tier=TIER_FIXED,
            to_tier=TIER_AGENTIC,
            runs=total,
            distinct_paths=distinct,
            dominant_share=dominant_share,
            failure_rate=failure_rate,
            failing_runs=failing,
            mean_cost_usd=mean_cost_usd,
        )
    return None


# ── filing (mirrors curator.file_promotion_suggestions + the shared fingerprint dedup) ──


def file_tier_migration(
    mig: TierMigration,
    *,
    session_key: str = "",
    run_id: str = "",
    evidence_refs: list[str] | None = None,
) -> str:
    """File one migration as a PENDING ``TIER_MIGRATION`` proposal. Returns its id or ``""``.

    Routed through the SAME human-gated ``proposals.enqueue`` path every other proposer uses, so the
    tier signal lands in the one queue a human reviews — never a self-install. The content
    fingerprint dedups repeats: re-filing the same finding REINFORCES the pending row rather than
    stacking a second, and a rejected one is silently skipped, so a per-run cadence cannot nag.

    ``evidence_refs`` (the sibling run ids) ride the manifest, NOT the fingerprinted body, so
    volatile ids strengthen the evidence without defeating the dedup.
    """
    from personalclaw.learning.proposals import ChangeManifest, Kind, enqueue

    if mig.direction == DIRECTION_DISTILL:
        failure_pattern = (
            f"zero trajectory variance across {mig.runs} runs "
            f"({mig.dominant_share:.0%} on one path)"
        )
        root_cause = "an agentic template that never varies is a fixed procedure at agent cost"
        targeted_fix = "distill the agentic stages into a deterministic (fixed) template"
    else:
        failure_pattern = (
            f"deterministic steps failed {mig.failure_rate:.0%} of runs "
            f"({mig.failing_runs} of {mig.runs})"
        )
        root_cause = "a rigid deterministic step cannot handle the domain's variance"
        targeted_fix = "promote the failing step(s) to an agentic stage"

    manifest = ChangeManifest(
        component=f"workflow:{mig.template}",
        failure_pattern=failure_pattern,
        evidence_refs=list(evidence_refs or []),
        root_cause=root_cause,
        targeted_fix=targeted_fix,
        predicted_fixes=(
            [f"lower per-run LLM cost for {mig.template}"]
            if mig.direction == DIRECTION_DISTILL
            else [f"lower failure rate for {mig.template}"]
        ),
    )

    try:
        _verdict, prop = enqueue(
            kind=Kind.TIER_MIGRATION.value,
            title=mig.title(),
            body=mig.body(),
            # The template (not direction) is the dedup target: a template is agentic OR fixed,
            # so only one direction is ever live for it, and keying on the template lets a later
            # accept/reject decision about "migrating this template" match whichever way it pointed.
            target=f"tier:{mig.template}",
            provenance="inferred",
            source_cadence="run_end",
            run_id=run_id,
            session_key=session_key,
            change_manifest=manifest,
            evidence_strength="correlated",
            # More runs behind the signal, more confidence — capped so a pure-statistics inference
            # never presents as certainty.
            confidence=min(0.85, 0.4 + 0.05 * mig.runs),
            tags=["tier_migration", mig.direction],
            # The variance is measured over the whole sibling history, so the run count IS the
            # evidence count; min_evidence=1 because the multi-run floor already lives in
            # `tier_migration`'s own `min_runs` gate.
            occurrences=mig.runs,
            min_evidence=1,
        )
    except Exception:
        logger.debug("tier_migration: filing for %s failed", mig.template, exc_info=True)
        return ""
    return prop.id if prop is not None else ""
