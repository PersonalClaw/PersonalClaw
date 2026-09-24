"""The template refiner's propose-only tool set — WF2LEA-6 (§3.1 substrate/trust/shape).

The refiner ships as a trigger-fired ``run-workflow`` template
(``workflows/bundled/refine-template``) whose stage runs the reserved ``template-refiner``
agent. That agent holds ONLY the tools named
here, and every one of them is read-or-propose: it can read a template's own run-ledger evidence
and it can FILE a ``template_diff`` proposal — it cannot apply one, install a skill, or write a
template. "Propose-don't-write" is therefore structural, not a matter of prompt discipline: the
apply tools live only on the human-facing review surface (``learning/inbox.require_human`` + the
accept handler), never in this set.

The two tools consume exactly the S73/S79 decision functions:

* ``gather_evidence`` → ``refiner.cluster_safely`` (screens hostile ledger text, S79) +
  ``refiner.fenced_evidence`` (fences it at the model boundary, S79) + ``refiner.top_cluster``
  (the power-floor cluster worth proposing against, S73). The agent reasons over the RESULT.
* ``file_template_diff`` → ``refiner.check_diff`` (the frozen-region + legal-op gate, S73) before
  ``proposals.enqueue`` — an op touching ``id``/``triggers``/surfacing metadata is refused here, so
  a self-editing template can never change WHEN it fires.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.learning import proposals, refiner
from personalclaw.learning.proposals import ChangeManifest

logger = logging.getLogger(__name__)

#: The complete tool set the ``template-refiner`` agent is allowed to hold. Every name is a
#: read (``refiner_evidence``) or a propose (``propose_template_diff``); NONE writes a template,
#: installs a skill, or accepts a proposal. ``test_refiner_agent`` pins this against a denylist,
#: so adding a direct-write tool to the refiner reds the propose-only guarantee.
REFINER_TOOL_NAMES: frozenset[str] = frozenset({"refiner_evidence", "propose_template_diff"})


def gather_evidence(workflow_name: str, *, limit: int = 50) -> dict[str, Any]:
    """Read a template's own run-ledger failures, screened and clustered (READ-ONLY).

    Returns the top failure cluster (the one the refiner should target) and the fenced evidence
    a prompt may carry. Screening drops injection-bearing events before they can steer which
    cluster ranks (S79's BLOCKED→dropped rule); fencing wraps every surviving untrusted field so
    the model boundary is safe. Never writes anything.
    """
    from personalclaw.workflows import journal, store

    events: list[dict[str, Any]] = []
    runs, _ = store.list_runs(workflow_name=workflow_name, limit=limit)
    for run in runs:
        try:
            rows = journal.ledger(run.id, kinds=set(refiner.EVIDENCE_KINDS))
        except Exception:
            logger.debug("refiner: could not read ledger for run %s", run.id, exc_info=True)
            continue
        # STAMP the run key the power floor counts. A ledger record is run-scoped by DIRECTORY
        # (`runs/<run_id>/events.jsonl`) so the writer never repeats `run_id` on a row — it appears
        # only inside the opaque `<run>-evt-<seq>` event id. A consumer that FANS IN across runs
        # must therefore supply it, and this is that consumer: `Cluster.distinct_runs` is a
        # cross-run count, so with every event arriving as `run_id=""` it was permanently 1,
        # `top_cluster` could never clear `MIN_RUNS_FOR_EVIDENCE`, and the `template-refiner` agent
        # took its "no top cluster — STOP and propose nothing" branch on every template forever.
        # Injected here rather than stamped by the writer on purpose: the run id is already the
        # row's storage key, so stamping would duplicate it onto every row of every emitter to
        # serve one reader. `consumer_liveness` fans in over the same ledger the same way.
        # Injection LAST so the authoritative directory wins over any field of the same name.
        events.extend({**row, "run_id": str(run.id)} for row in rows)
    clusters, _screened = refiner.cluster_safely(events)
    top = refiner.top_cluster(clusters)
    fenced = refiner.fenced_evidence(events)

    def _view(c: refiner.Cluster) -> dict[str, Any]:
        data = c.to_dict()
        data["run_ids"] = list(dict.fromkeys(c.runs))[:20]
        return data

    return {
        "workflow": workflow_name,
        "clusters": [_view(c) for c in clusters],
        "top_cluster": None if top is None else _view(top),
        "evidence": fenced,
    }


def file_template_diff(
    workflow_name: str,
    *,
    ops: list[dict[str, Any]],
    rationale: str,
    run_ids: list[str] | None = None,
    predicted_fixes: list[str] | None = None,
) -> dict[str, Any]:
    """File a ``template_diff`` PROPOSAL — never apply it. Returns the outcome, not a template.

    The diff is first checked against the frozen-region + legal-op gate (``refiner.check_diff``):
    an illegal or frozen-field op rejects the WHOLE diff (a partially applied diff is a template
    nobody authored), and nothing is filed. A legal diff is enqueued through the shared
    human-gated queue with its typed ops carried on the change manifest's ``targeted_fix`` (the
    field the inbox already reads to stamp a risk tier), so acceptance can apply it mechanically.

    A filed diff also PRE-REGISTERS its §2 A/B study (``study_id`` in the outcome) — see
    :func:`_preregister_study` for why registering here and running elsewhere is the split.
    """
    if not isinstance(ops, list) or not ops:
        return {"filed": False, "rejected": ["a diff needs at least one op"]}
    legal, reasons = refiner.check_diff(ops)
    if not legal:
        return {"filed": False, "rejected": reasons}

    run_ids = [str(r) for r in (run_ids or [])]
    manifest = ChangeManifest(
        component=workflow_name,
        failure_pattern=rationale,
        evidence_refs=run_ids,
        targeted_fix=ops,  # typed ops the accept path applies (see handlers/learning `_tier_for`)
        predicted_fixes=[str(p) for p in (predicted_fixes or [])],
    )
    # Distinct runs of evidence gate the file: the refiner only proposes against a cluster that
    # cleared the power floor (>=3 distinct runs), so `occurrences` is the honest count.
    occurrences = max(len(set(run_ids)), 1)
    verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE_DIFF.value,
        title=f"Refine {workflow_name}",
        body=rationale,
        target=workflow_name,
        provenance="agent",
        source_cadence="refiner",
        evidence_refs=run_ids,
        change_manifest=manifest,
        tags=["refiner", refiner.risk_tier(ops)],
        occurrences=occurrences,
        min_evidence=refiner.MIN_RUNS_FOR_EVIDENCE,
    )
    return {
        "filed": prop is not None,
        "verdict": verdict.value,
        "proposal_id": getattr(prop, "id", ""),
        "risk_tier": refiner.risk_tier(ops),
        "study_id": _preregister_study(workflow_name, prop, rationale) if prop is not None else "",
    }


def _preregister_study(workflow_name: str, prop: Any, rationale: str) -> str:
    """Pre-register the §2 A/B study for a just-filed template diff (ES-5). Spends nothing.

    THIS is the flywheel's link into the evaluation substrate. Pre-registration must precede
    arm 1 — that is the whole of §2.1's immutability — and it is free, so it belongs here, at
    the moment the diff exists. What deliberately does NOT happen here is the RUN: a study
    over the harvested suite is ``cases x k x 2`` arm calls plus twice that many judge calls,
    and an agent tool call that silently started a three-digit model-call matrix is precisely
    what ES-4's spend preflight exists to prevent. The run is `personalclaw study --run`.

    Best-effort by construction: the proposal is already filed and returning a 500 here would
    strand a legal diff over a missing eval artifact. A failure yields ``""`` — an honest "no
    study" — never a fabricated id.
    """
    try:
        from personalclaw.evals import study_arms

        reg = study_arms.register_template_study(
            workflow_name=workflow_name,
            hypothesis=rationale,
            proposal_id=str(getattr(prop, "id", "") or ""),
        )
        return reg.study_id
    except Exception:  # noqa: BLE001 - a filed proposal must never be lost to an eval artifact
        logger.warning(
            "refiner: could not pre-register a study for %s", workflow_name, exc_info=True
        )
        return ""
