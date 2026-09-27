"""Applying mid-flight mutations (WF2-R2 / R20).

`RunController.submit_mutation` validates a batch and QUEUES it; `drain_mutations` applies the queue
under the lock, between scheduling steps — the designated safe point — re-verifying every batch
against the state it now meets. A committed batch swaps in the candidate spec and applies its state
effects: a rewind or `run_from` resets its binding closure, a skip marks a subtree, a fork branches
a child run, and done nodes whose inputs changed are flagged stale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw.workflows import attention
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import mutations, store
from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.engine import release_execution_claim
from personalclaw.workflows.human_input import drop_continuations
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


def drain_mutations(ctl: RunController) -> None:
    """Apply queued batches. Called under the lock, between scheduling steps.

    Each batch is RE-VERIFIED here (WF2-R2 TOCTOU): nodes complete while a user reads a
    preview, so a node that was pending at submit may be frozen by now. Re-validating
    against current state is the only way to catch that, and `validate_batch` is pure
    so running it twice costs nothing.
    """
    if not ctl._pending_mutations:
        return
    queued = list(ctl._pending_mutations)
    ctl._pending_mutations.clear()
    for result, actor in queued:
        try:
            root = Node.from_dict(ctl.spec.get("root") or {})
        except ValueError:
            logger.warning("workflow %s: spec unreadable, dropping mutation", ctl.run.id)
            continue
        issues = mutations.validate_batch(result.ops, root, ctl.instances)
        if issues:
            # The state moved under the preview. Rejected, and journaled as rejected —
            # a silently dropped batch is indistinguishable from an applied one.
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
            continue
        commit_mutation(ctl, result, actor)


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
        # in the wrong epoch. The question is WITHDRAWN, so its Inbox row closes with it:
        # left open, it offers the dropped token, and the re-ask's row dedupes onto it (same
        # run, path and epoch) instead of carrying the live one. Reachable since a parked run
        # applies a rewind at once; before, a rewind at a gate never ran while the gate waited.
        if drop_continuations(ctl.run.id, instance_prefix=path) and node is not None and node.id:
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
