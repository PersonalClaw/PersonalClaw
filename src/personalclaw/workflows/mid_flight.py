"""Applying mid-flight mutations (WF2-R2 / R20).

`RunController.submit_mutation` validates a batch against the spec the queue will leave
(`projected_spec`) and QUEUES it (`queue_mutation`, in memory and beside the run, so a restart does
not lose it — `reload_mutations`); `drain_mutations` applies the queue in order under the lock,
between scheduling steps — the designated safe point — preparing every batch again against the spec
the batch before it left and the state it now meets. A committed batch swaps in its candidate spec
and applies its state effects: a rewind or `run_from` resets its binding closure, a skip marks a
subtree, a fork branches a child run, and done nodes whose inputs changed are flagged stale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import attention
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import mutations, store
from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.engine import release_execution_claim
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    Node,
    now_stamp,
    spec_path,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


def queue_mutation(ctl: RunController, result: mutations.BatchResult, actor: str) -> None:
    """Queue a validated batch for the drain point, in memory AND on disk (ledger 249).

    On disk because the queue can outlive the controller: a PAUSED run applies its edits when it
    is resumed (`submit_mutation` does not wake it), and a gateway restart in between took an
    edit held only in the controller's memory with it — the run resumed without it and said
    nothing. The record is the ops as submitted and who submitted them; a new controller prepares
    them again against the spec it meets (`reload_mutations`), as the drain re-verifies anyway.
    """
    ctl._pending_mutations.append((result, actor))
    _save_queue(ctl)


def _raw_ops(result: mutations.BatchResult) -> list[dict[str, Any]]:
    """A batch's ops as submitted — what the queue keeps, and what the drain prepares again."""
    return [op.raw or op.to_dict() for op in result.ops]


def _save_queue(ctl: RunController) -> None:
    store.write_pending_mutations(
        ctl.run.id,
        [{"ops": _raw_ops(result), "actor": actor} for result, actor in ctl._pending_mutations],
    )


def projected_spec(ctl: RunController) -> dict[str, Any]:
    """The spec the run will have once the queue drains: the last queued batch's candidate, or
    the run's own spec when nothing is queued (ledger 293).

    A batch is checked against THIS, not against the spec the run has now: two edits made while a
    run is paused apply one after the other, so the second has to be valid on the spec the first
    leaves — which is what lets it change a step the first one added. Each queued batch was itself
    prepared against the projection when it was queued, so the last ok one's candidate IS the
    projection. A batch a restart found no longer applying (not ok) projects nothing.
    """
    spec = ctl.spec
    for result, _actor in ctl._pending_mutations:
        if result.ok and result.spec is not None:
            spec = result.spec
    return spec


def reload_mutations(ctl: RunController) -> list[tuple[mutations.BatchResult, str]]:
    """The edits an earlier controller queued on this run and never applied, in their order, each
    prepared against the spec the ones before it will leave. A batch that no longer prepares is
    kept, not ok, so the drain journals it rejected rather than it vanishing."""
    queued: list[tuple[mutations.BatchResult, str]] = []
    spec = ctl.spec
    for entry in store.read_pending_mutations(ctl.run.id):
        ops = entry.get("ops")
        if not isinstance(ops, list):
            continue
        result = mutations.prepare_batch(ops, spec, ctl.instances, effects=ctl._effects)
        if result.ok and result.spec is not None:
            spec = result.spec
        queued.append((result, str(entry.get("actor") or "user")))
    return queued


def drain_mutations(ctl: RunController) -> None:
    """Apply queued batches, in order. Called under the lock, between scheduling steps.

    Each batch is PREPARED AGAIN here, against the spec the batch before it left and the state the
    run is in now — never swapped in as the candidate it was queued with (ledger 293). That
    candidate was computed from the spec at submit, so two edits made while a run was paused each
    carried a spec without the other's change, and committing the second put back the spec from
    before the first: the first edit vanished. Preparing again is also the re-verification
    (WF2-R2 TOCTOU): nodes complete while a user reads a preview, so a node that was pending at
    submit may be frozen by now, and a batch that no longer applies is rejected and journaled as
    rejected — a silently dropped batch is indistinguishable from an applied one.

    The queue on disk is cleared BEFORE any batch applies: an edit applies at most once, so a
    crash mid-drain loses the batches not yet applied rather than applying a committed one again
    on the next start.
    """
    if not ctl._pending_mutations:
        return
    queued = list(ctl._pending_mutations)
    ctl._pending_mutations.clear()
    _save_queue(ctl)
    for submitted, actor in queued:
        result = mutations.prepare_batch(
            _raw_ops(submitted), ctl.spec, ctl.instances, effects=ctl._effects
        )
        if not result.ok:
            _reject(ctl, result, actor, result.issues)
            continue
        commit_mutation(ctl, result, actor)


def _reject(
    ctl: RunController, result: mutations.BatchResult, actor: str, issues: list[mutations.Issue]
) -> None:
    ctl.journal.write(
        journal_mod.MUTATION_REJECTED,
        actor=actor,
        ops=[o.to_dict() for o in result.ops],
        issues=[i.to_dict() for i in issues],
    )
    ctl._publish(
        "workflow_mutation_rejected",
        {"issues": [i.to_dict() for i in issues], "actor": actor},
    )


def commit_mutation(ctl: RunController, result: mutations.BatchResult, actor: str) -> None:
    """Swap in the candidate spec, apply state effects, journal the batch."""
    if result.spec is None:
        return
    ctl.spec = result.spec
    ctl.run.spec_version += 1
    ctl.root = Node.from_dict(ctl.spec.get("root") or {"kind": "sequence"})
    store.write_spec(ctl.run.id, ctl.spec)
    store.write_spec_history(
        ctl.run.id,
        ctl.run.spec_version,
        mutations.history_record(
            result.ops,
            actor=actor,
            version=ctl.run.spec_version,
            spec=ctl.spec,
            preview=result.preview,
            owner_username=ctl.run.owner_username,
            origin_harness=ctl.run.origin_harness,
        ),
    )
    ctl.journal.user_edited_mid_flight([o.to_dict() for o in result.ops])

    for op in result.ops:
        if op.kind in (mutations.OpKind.REWIND, mutations.OpKind.RUN_FROM):
            _apply_reentry(ctl, op, result.preview)
        elif op.kind == mutations.OpKind.SKIP:
            _skip_by_id(ctl, op.node_id)
        elif op.kind == mutations.OpKind.SET_INPUT:
            ctl.run.inputs.update(op.overrides)
        elif op.kind == mutations.OpKind.FORK:
            _apply_fork(ctl, op)

    # Nodes whose inputs changed but which are NOT being re-run (WF2-R2 #3). Flagged
    # rather than silently serving an answer computed from inputs that no longer exist.
    _flag_stale(ctl, result.preview)
    ctl._save_run()
    ctl._persist_state()
    ctl._publish(
        "workflow_spec_updated",
        {
            "spec_version": ctl.run.spec_version,
            "actor": actor,
            "preview": result.preview.to_dict(),
        },
    )


def _apply_reentry(ctl: RunController, op: mutations.Op, preview: mutations.CascadePreview) -> None:
    """Reset the binding closure so it re-runs.

    `rewind` resets the seed node AND its consumers; `run_from` resets only the
    consumers, leaving the seed's output in place — that is the whole distinction
    ("redo the synthesis with the same gathered data").

    The outputs are ARCHIVED, not deleted: a rewind that discarded the prior answer
    would make the edit irreversible, and the attic is what lets a reader see what the
    run used to say.
    """
    targets = set(preview.rerun)
    if op.kind == mutations.OpKind.RUN_FROM:
        targets.discard(op.node_id)
    paths = [path for path, node in walk(ctl.root) if node.id in targets for _ in (0,)]
    # A remembered "always allow" must not survive a rewind: it would auto-approve the
    # very step the user rewound in order to reconsider it.
    ctl._allow_memory.clear()
    epoch = mutations.next_epoch(ctl.instances, paths, force=op.force)
    for path in paths:
        inst = ctl._instance(path)
        if inst.output_ref:
            store.archive_output(ctl.run.id, path, ctl.run.spec_version)
        if inst.state in TERMINAL_STATES and inst.claim_target:
            # The attempt this resets is over, so its no-double-execution claim goes back. A stage
            # that settled DONE keeps its claim (`stage_settlement`), and the re-run asked for here
            # is that same instance: it met its own lease, read DEGRADED ("another worker holds the
            # claim on this node … not executing twice"), and a confirmed Re-run ran nothing for
            # the claim's whole TTL (#3533's shape). A RUNNING attempt keeps its claim: its
            # subagent is still executing, and a second execution beside it is what the claim is
            # for.
            release_execution_claim(inst.claim_target, inst.claim_holder)
            inst.claim_target = ""
            inst.claim_holder = ""
        inst.state = InstanceState.PENDING
        inst.epoch = epoch
        inst.output_ref = ""
        inst.failure = None
        inst.degraded_reason = ""
        inst.completed_at = None
        inst.wake_at = 0.0
        inst.attempt = 0
        node = dict(walk(ctl.root)).get(spec_path(path))
        if node is not None and node.id:
            # Drop the cached output so a binding cannot resolve a stale value between
            # the reset and the re-run.
            ctl._outputs.pop(node.id, None)
        ctl.journal.invalidate_prefix(path)
        # An answer given to this step's last park belongs to the dispatch it started, not to
        # the re-run a rewind asks for.
        ctl._park_answers.pop(path, None)
        # A pending approval for a node about to re-run would resume a step that no
        # longer exists in that form — drop the token rather than let it land
        # in the wrong epoch. The question is WITHDRAWN: its confirmation closes `withdrawn`
        # (ledger 249), and its Inbox row closes with it — left open, it offers the dropped
        # token, and the re-ask's row dedupes onto it (same run, path and epoch) instead of
        # carrying the live one. Reachable since a parked run applies a rewind at once; before,
        # a rewind at a gate never ran while the gate waited.
        from personalclaw.workflows.gate_answers import withdraw_asks

        withdrawn = withdraw_asks(ctl, reason="the step was rewound", instance_prefix=path)
        if withdrawn and node is not None and node.id:
            attention.resolve_gate_item(ctl.services.attention_state, ctl.run.id, node.id)


def _apply_fork(ctl: RunController, op: mutations.Op) -> None:
    """Branch a child run. THIS run is untouched — that is the whole point of fork.

    The child is left in DRAFT: starting it is the caller's decision, because a fork is
    usually created to be edited before it runs ("try a stricter judge"). Auto-starting
    would race the edit it exists to receive.
    """
    from personalclaw.workflows.checkpoints import fork_run

    try:
        result = fork_run(
            ctl.run,
            ctl.spec,
            ctl.instances,
            checkpoint_id=op.checkpoint_id,
            note=op.note,
            now=now_stamp(),
        )
    except ValueError as exc:
        ctl.journal.write(
            journal_mod.MUTATION_REJECTED,
            actor="engine",
            ops=[op.to_dict()],
            issues=[{"code": "WF_MUT_UNKNOWN_CHECKPOINT", "message": str(exc), "node_id": ""}],
        )
        return
    ctl.journal.write(
        journal_mod.CHILD_RUN_ATTACH,
        parent_run_id=ctl.run.id,
        child_run_id=result.child.id,
        node_id=op.node_id,
    )
    ctl._publish("workflow_forked", result.to_dict())


def _skip_by_id(ctl: RunController, node_id: str) -> None:
    for path, node in walk(ctl.root):
        if node.id == node_id:
            ctl._skip(path)


def _flag_stale(ctl: RunController, preview: mutations.CascadePreview) -> None:
    """Journal `inputs_stale` for done nodes outside the re-run set (WF2-R2 #3)."""
    rerun = set(preview.rerun)
    for path, node in walk(ctl.root):
        if not node.id or node.id in rerun:
            continue
        inst = ctl.instances.get(path)
        if inst is None or inst.state not in SUCCESS_STATES:
            continue
        if not (node_deps(node.config or {}) & rerun):
            continue
        ctl.journal.write(
            journal_mod.INPUTS_STALE,
            instance_path=path,
            node_id=node.id,
            epoch=inst.epoch,
            stale_deps=sorted(node_deps(node.config or {}) & rerun),
        )
