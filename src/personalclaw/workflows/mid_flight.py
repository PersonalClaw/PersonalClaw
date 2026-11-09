"""Applying mid-flight mutations.

`RunController.submit_mutation` validates a batch against the spec the queue will leave
(`projected_spec`) and QUEUES it (`queue_mutation`, in memory and beside the run, so a restart does
not lose it — `reload_mutations`); `drain_mutations` applies the queue in order under the lock,
between scheduling steps — the designated safe point — preparing every batch again against the spec
the batch before it left and the state it now meets. A committed batch swaps in its candidate spec
and applies its state effects: a rewind or `run_from` resets its binding closure, a skip marks a
subtree, a fork branches a child run, and done nodes whose inputs changed are flagged stale.

A batch that would let a step do more than it does — approve its own tool calls, or change things
where it only read — is refused before it is queued, unless the owner's own yes covers it
(:func:`posture_refusal`): the rule every save of a definition is held to, for a step that would act
on it the moment it runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw import record_files
from personalclaw.workflows import attention
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import mutations, store, supervisor_policy
from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.engine import release_execution_claim
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    ItemErrorPolicy,
    LoopMode,
    Node,
    NodeKind,
    loop_mode,
    now_stamp,
    skipped_on_ask,
    spec_path,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


def queue_mutation(ctl: RunController, result: mutations.BatchResult, actor: str) -> None:
    """Queue a validated batch for the drain point, in memory AND on disk.

    On disk because the queue can outlive the controller: a PAUSED run applies its edits when it
    is resumed (`submit_mutation` does not wake it), and a gateway restart in between took an
    edit held only in the controller's memory with it — the run resumed without it and said
    nothing. The record is the ops as submitted and who submitted them; a new controller prepares
    them again against the spec it meets (`reload_mutations`), as the drain re-verifies anyway.

    Written before it is queued in memory: a run whose queue on disk cannot be read refuses the
    edit (``record_files.Unreadable``) rather than write it over the edits the file holds, and an
    edit refused there is not applied either.
    """
    _save_queue(ctl.run.id, [*ctl._pending_mutations, (result, actor)])
    ctl._pending_mutations.append((result, actor))


def _raw_ops(result: mutations.BatchResult) -> list[dict[str, Any]]:
    """A batch's ops as submitted — what the queue keeps, and what the drain prepares again."""
    return [op.raw or op.to_dict() for op in result.ops]


def _save_queue(run_id: str, pending: list[tuple[mutations.BatchResult, str]]) -> None:
    store.write_pending_mutations(
        run_id, [{"ops": _raw_ops(result), "actor": actor} for result, actor in pending]
    )


def posture_refusal(
    name: str,
    before: dict[str, Any],
    result: mutations.BatchResult,
    *,
    owner_allowed: bool,
) -> dict[str, Any] | None:
    """The refusal of the edit *result* of the running workflow *name*, whose spec it was
    prepared against is *before*, when it would let a step do more than the step does now
    (``automation_posture.workflow_loosenings``) and *owner_allowed* does not say her yes covers
    it; ``None`` otherwise.

    Whoever submits it: an agent's ``workflow_edit``, a loop's replan, the owner's own edit
    without her ``confirm``. A step of a running workflow acts on what it says the moment it runs,
    so an edit is held to the rule every save of a definition is, and an agent is told where the
    owner gives that yes. Nothing is queued."""
    from personalclaw.automation_posture import workflow_loosenings

    if owner_allowed or result.spec is None:
        return None
    loosened = workflow_loosenings(
        name, current_root=before.get("root"), new_root=result.spec.get("root") or {}
    )
    if not loosened:
        return None
    steps = "; ".join(f"“{step.label}”" for step in loosened)
    message = (
        f"this edit would let its steps {steps} approve their own tool calls or change things, "
        "not only read, and a step of a running workflow does that only on your owner's own yes, "
        "given where they are shown the step: save the change into the workflow's definition "
        "(workflow_author asks them), then start a new run"
    )
    return {
        "ok": False,
        "code": "WF_MUT_NEEDS_OWNER_YES",
        "message": message,
        "field": loosened[0].field,
        "consent": loosened[0].consent,
        "change": loosened[0].change,
        "caution": loosened[0].caution,
        "steps": [step.to_dict() for step in loosened],
        "issues": [{"code": "WF_MUT_NEEDS_OWNER_YES", "message": message, "node_id": ""}],
    }


def projected_spec(ctl: RunController) -> dict[str, Any]:
    """The spec the run will have once the queue drains: the last queued batch's candidate, or
    the run's own spec when nothing is queued.

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
    run is in now — never swapped in as the candidate it was queued with. That
    candidate was computed from the spec at submit, so two edits made while a run was paused each
    carried a spec without the other's change, and committing the second put back the spec from
    before the first: the first edit vanished. Preparing again is also the re-verification
    for TOCTOU: nodes complete while a user reads a preview, so a node that was pending at
    submit may be frozen by now, and a batch that no longer applies is rejected and journaled as
    rejected — a silently dropped batch is indistinguishable from an applied one.

    The queue on disk is cleared BEFORE any batch applies: an edit applies at most once, so a
    crash mid-drain loses the batches not yet applied rather than applying a committed one again
    on the next start.
    """
    if not ctl._pending_mutations:
        return
    queued = list(ctl._pending_mutations)
    try:
        _save_queue(ctl.run.id, [])
    except record_files.Unreadable:
        # The queue on disk cannot be read now, so it is neither cleared nor drained: an edit
        # applies at most once, and one the file still holds would apply again once it is read.
        # They wait, in memory, for a drain that can clear it.
        return
    ctl._pending_mutations.clear()
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

    # Nodes whose inputs changed but which are NOT being re-run. Flagged
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
        # Her Allow of the start was for the attempt this resets; the re-run asks her again, for
        # the reason the "always allow" above does not survive a rewind.
        inst.approved_request = ""
        inst.approved_at = 0.0
        # So were the calls she declined in it (`declines`): the re-run records its own.
        inst.declined = []
        node = dict(walk(ctl.root)).get(spec_path(path))
        # Drop the cached output so a binding cannot resolve a stale value between the reset and
        # the re-run: by id, and by instance path for a step inside a fan-out item.
        ctl.forget_output(path, node.id if node is not None else "")
        ctl.journal.invalidate_prefix(path)
        # An answer given to this step's last park belongs to the dispatch it started, not to
        # the re-run a rewind asks for.
        ctl._park_answers.pop(path, None)
        # A pending approval for a node about to re-run would resume a step that no
        # longer exists in that form — drop the token rather than let it land
        # in the wrong epoch. The question is WITHDRAWN: its confirmation closes `withdrawn`,
        # and its Inbox row closes with it — left open, it offers the dropped
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
    """Skip the step *node_id* names where it runs at its own path. One inside a loop or a
    foreach runs only under the paths each cycle and item opens, which the scheduler skips as it
    opens them (the skip is on the spec): a row at its definition path would be one no cycle or
    item ran."""
    holders = fan_out_holders(ctl.root)
    for path, node in walk(ctl.root):
        if node.id != node_id or holders.get(path):
            continue
        if node.is_container and _under_way(ctl, path):
            # A group of steps with steps under way in it: the scheduler lets those finish and
            # skips the rest (`tick._skip_as_asked`), so none is marked skipped while it runs.
            continue
        ctl._skip(path, reason=ASKED_SKIP)


def _under_way(ctl: RunController, path: str) -> bool:
    """Whether any step under instance path *path* is running or waiting."""
    busy = (InstanceState.RUNNING, InstanceState.WAITING)
    return any(p.startswith(f"{path}.") and i.state in busy for p, i in ctl.instances.items())


# ── a skip asked mid-run: where it holds, and what it reaches ────────────────

#: What a step a skip asked for mid-run says under its row, wherever it was skipped.
ASKED_SKIP = "not run: skipped as asked"


def cycle_ran_nothing(ctl: RunController, prefix: str) -> bool:
    """Was every step of the loop cycle at *prefix* skipped, at least one of them as asked?"""
    states = [
        inst
        for p, inst in ctl.instances.items()
        if p == prefix or p.startswith(f"{prefix}.") or p.startswith(f"{prefix}#")
    ]
    return (
        bool(states)
        and all(inst.state == InstanceState.SKIPPED for inst in states)
        and any(inst.degraded_reason == ASKED_SKIP for inst in states)
    )


#: Instance states the scheduler does not skip in a cycle or item it opened: done, or under way
#: (`tick._visit` returns for a running or waiting step before it reads the skip).
_NOT_SKIPPED = TERMINAL_STATES | {InstanceState.RUNNING, InstanceState.WAITING}


def fan_out_holders(root: Node) -> dict[str, str]:
    """Spec path → the nearest loop or foreach whose body it is in, for each step in one, from the
    walk's own structure (``validator._parent_slots``' rule: a label may hold a dot)."""
    parent: dict[str, tuple[str, str]] = {}
    for path, node in walk(root):
        for i, _child in enumerate(node.children):
            parent[f"{path}.children[{i}]"] = (path, "children")
        if node.body is not None:
            parent[f"{path}.body"] = (path, "body")
        for label in node.cases:
            parent[f"{path}.cases[{label}]"] = (path, "cases")
        if node.default_case is not None:
            parent[f"{path}.default"] = (path, "default")
    holders: dict[str, str] = {}
    for path in parent:
        up = path
        while up in parent:
            up, slot = parent[up]
            if slot == "body":
                holders[path] = up
                break
    return holders


def skip_unreachable(ctl: RunController, to_skip: list[str]) -> None:
    """Mark each of the frontier's *to_skip* skipped: an untaken branch's path, a step whose
    input can never come, and a step a skip asked for mid-run in each cycle and item it opens
    in, which says so (:data:`ASKED_SKIP`)."""
    by_path = dict(walk(ctl.root)) if to_skip else {}
    for path in to_skip:
        node = by_path.get(spec_path(path))
        ctl._skip(path, reason=ASKED_SKIP if node and skipped_on_ask(node) else "")


def skip_reach(ctl: RunController, node_id: str) -> dict[str, Any] | None:
    """What a skip of *node_id* reaches now (``instances``), and whether a loop or foreach it is
    inside has still to open it in some cycle or item (``later``). None when no step has the id.

    A step outside any loop or foreach is reached unless it has finished (:func:`_skip_by_id`
    skips it at its own path, a waiting one included). Inside one it is reached in each cycle or
    item that opened it where the scheduler will still skip something of it
    (:func:`_unit_reached`)."""
    by_path = dict(walk(ctl.root))
    found = [path for path, node in by_path.items() if node.id == node_id]
    if not found:
        return None
    holders = fan_out_holders(ctl.root)
    states = {p: inst.state for p, inst in ctl.instances.items()}
    instances: list[str] = []
    later = False
    for path in found:
        node = by_path[path]
        holder = holders.get(path, "")
        if not holder:
            if node.is_container:
                reached = _unit_reached(ctl, node, path, states)
            else:
                reached = states.get(path, InstanceState.PENDING) not in TERMINAL_STATES
            if reached:
                instances.append(path)
            continue
        units = _opened_units(path, states)
        instances += [u for u in units if _unit_reached(ctl, node, u, states)]
        later = later or _opens_again(ctl, holder, holders, by_path, units)
    return {"node_id": node_id, "instances": sorted(instances), "later": later}


def _opened_units(path: str, states: dict[str, InstanceState]) -> list[str]:
    """The instance paths the step at spec path *path* has opened at, one per cycle or item: each
    one with a row at it or under it (a group of steps has no row of its own)."""
    units: set[str] = set()
    for row in states:
        spec = spec_path(row)
        if spec != path and not spec.startswith(f"{path}."):
            continue
        for cut in [i for i, ch in enumerate(row) if ch == "."] + [len(row)]:
            if spec_path(row[:cut]) == path:
                if row[:cut] != path:
                    units.add(row[:cut])
                break
    return sorted(units)


def _unit_reached(
    ctl: RunController, node: Node, unit: str, states: dict[str, InstanceState]
) -> bool:
    """Whether a skip of *node* reaches it at instance path *unit*: the scheduler will still skip
    something of it there. A step by its own row (not run, and not under way); a group of steps by
    its steps there, one with no row or a row the scheduler still skips reached; a loop or foreach
    while it still opens a cycle or item, or something of its current cycle or of an item is
    reached; never one that has ended."""
    if not node.is_container:
        return states.get(unit, InstanceState.PENDING) not in _NOT_SKIPPED
    if _derived(ctl, node, unit, states) in TERMINAL_STATES:
        return False
    if node.kind in (NodeKind.LOOP, NodeKind.FOREACH):
        under = [p for p in states if p.startswith(f"{unit}.")]
        if _opens_more(ctl, node, unit, under):
            return True
        return node.body is not None and any(
            _unit_reached(ctl, node.body, inner, states) for inner in _open_cycles(ctl, node, unit)
        )
    fan_outs: list[str] = []
    for sub, step in walk(node):
        rel = sub[len("root") :]
        if not rel or any(rel.startswith(f"{f}.") for f in fan_outs):
            continue
        if step.kind in (NodeKind.LOOP, NodeKind.FOREACH):
            fan_outs.append(rel)
            if _unit_reached(ctl, step, unit + rel, states):
                return True
        elif not step.is_container:
            if states.get(unit + rel, InstanceState.PENDING) not in _NOT_SKIPPED:
                return True
    return False


def _opens_more(ctl: RunController, node: Node, at: str, units: list[str]) -> bool:
    """Whether loop or foreach *node*, at instance path *at* and not ended, has still to open a
    cycle or item that none of *units* (instance paths) is in. A loop by its cycles, against a
    counted loop's `n` and any loop's cycle budget (`supervisor_policy.loop_cycle_cap`: its
    `max_iterations`, its supervisor's `budget.max_cycles`, or the run's Max cycles), the cycle
    the engine stops at; a loop that is not counted and has no budget does. A foreach by its
    items, resolved as the scheduler resolves them at *at*; one a failed item has halted
    (`on_item_error: halt`) opens none, and a loop whose current cycle holds such a foreach with
    a step still to start opens no other cycle, since that cycle never ends."""
    if node.kind == NodeKind.LOOP:
        current = int(ctl._iterations.get(at, 0))
        cycle = f"{at}.body@{current}"
        if _cycle_halted(ctl, cycle):
            return all(not _in_unit(u, cycle) for u in units)
        cap = supervisor_policy.loop_cycle_cap(node.config, ctl.run.policy_overrides)
        counted = loop_mode(node) == LoopMode.COUNTED
        if not counted and not cap:
            return True
        n = (node.config or {}).get("n")
        total = (n if isinstance(n, int) and n > 0 else 1) if counted else cap
        if cap:
            total = min(total, cap)
        if current + 1 < total:
            return True
        return not any(_in_unit(u, cycle) for u in units)
    items = _items_at(ctl, node, at)
    if items is None:
        return True
    if _halted(ctl, node, at, items):
        return False
    return any(not any(_in_unit(u, f"{at}.body#{i}") for u in units) for i in range(len(items)))


def _open_cycles(ctl: RunController, node: Node, at: str) -> list[str]:
    """The cycles and items loop or foreach *node* at *at* has open: a loop's current cycle, and
    each item of a foreach whose items resolve."""
    if node.kind == NodeKind.LOOP:
        return [f"{at}.body@{int(ctl._iterations.get(at, 0))}"]
    return [f"{at}.body#{i}" for i in range(len(_items_at(ctl, node, at) or []))]


def _items_at(ctl: RunController, node: Node, at: str) -> list[Any] | None:
    from personalclaw.workflows import tick

    return tick.foreach_items(
        node,
        at=at,
        inputs=ctl.run.inputs,
        outputs=ctl._outputs,
        instance_outputs=ctl._instance_outputs,
    )


def _halted(ctl: RunController, node: Node, at: str, items: list[Any]) -> bool:
    """Whether foreach *node* at *at* opens no more items: under `on_item_error: halt` an item
    has failed, and the scheduler opens none after it (`tick._visit_foreach`)."""
    from personalclaw.workflows import tick

    return tick.item_error_policy(node) == ItemErrorPolicy.HALT and any(
        getattr(ctl.instances.get(f"{at}.body#{i}"), "state", None) == InstanceState.FAILED
        for i in range(len(items))
    )


def _cycle_halted(ctl: RunController, cycle: str) -> bool:
    """Whether loop cycle *cycle* holds a foreach that a failed item has halted
    (`on_item_error: halt`) with a step still to start: the halt starts none
    (`tick._visit_foreach`), so the foreach never ends, nor does the cycle, and the loop opens no
    other. A step under way settles without the halt, so a foreach whose items need only those to
    finish ends when they do, and so does the cycle."""
    from personalclaw.workflows import tick

    by_path = dict(walk(ctl.root))
    busy = (InstanceState.RUNNING, InstanceState.WAITING)
    settled = {
        p: InstanceState.DONE if i.state in busy else i.state for p, i in ctl.instances.items()
    }
    for path, inst in ctl.instances.items():
        head, sep, index = path.rpartition(".body#")
        if inst.state != InstanceState.FAILED or not sep or not index.isdigit():
            continue
        node = by_path.get(spec_path(head)) if _in_unit(head, cycle) else None
        if (
            node is not None
            and node.kind == NodeKind.FOREACH
            and tick.item_error_policy(node) == ItemErrorPolicy.HALT
            and _derived(ctl, node, head, settled) not in TERMINAL_STATES
        ):
            return True
    return False


def _derived(
    ctl: RunController, node: Node, path: str, states: dict[str, InstanceState]
) -> InstanceState:
    """*node*'s state at instance path *path*, as the scheduler derives it."""
    from personalclaw.workflows import tick

    return tick.derive_state(
        node,
        path,
        states,
        declined_edges=ctl._declined_edges,
        outputs=ctl._outputs,
        inputs=ctl.run.inputs,
        iterations=ctl._iterations,
        instance_outputs=ctl._instance_outputs,
    )


def _opens_again(
    ctl: RunController,
    holder: str,
    holders: dict[str, str],
    by_path: dict[str, Node],
    units: list[str],
) -> bool:
    """Whether the loop or foreach at spec path *holder* has still to open its step in some cycle
    or item, given the step's cycles and items so far (*units*). Never once the run, or the
    outermost loop or foreach the step is in (the one whose state decides), has ended. One that
    runs only inside another loop or foreach does while an instance of it that has not ended
    still opens a cycle or item the step has not, or while the one it runs in opens another
    instance of it."""
    if ctl.run.is_terminal:
        return False
    decider = holder
    while holders.get(decider):
        decider = holders[decider]
    states = {p: inst.state for p, inst in ctl.instances.items()}
    if _derived(ctl, by_path[decider], decider, states) in TERMINAL_STATES:
        return False
    node = by_path[holder]
    if decider == holder:
        return _opens_more(ctl, node, holder, units)
    opened = _opened_units(holder, states)
    if any(
        _derived(ctl, node, at, states) not in TERMINAL_STATES and _opens_more(ctl, node, at, units)
        for at in opened
    ):
        return True
    return _opens_again(ctl, holders[holder], holders, by_path, opened)


def _in_unit(instance: str, unit: str) -> bool:
    """Whether *instance* is, or is inside, cycle or item *unit* (`….body@2`, `….body#0`)."""
    return instance == unit or instance.startswith(f"{unit}.")


def skip_refusal(
    ctl: RunController, result: mutations.BatchResult
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """For a batch with skips, at every door a skip comes through (``submit_mutation``): what each
    skip reaches (:func:`skip_reach`), and the refusal of the whole batch when one reaches
    nothing, a step already run or under way wherever the run runs it, rather than an answer
    that it was applied."""
    names = [op.node_id for op in result.ops if op.kind == mutations.OpKind.SKIP]
    reaches = [r for r in (skip_reach(ctl, n) for n in names) if r is not None]
    done = [r["node_id"] for r in reaches if not r["instances"] and not r["later"]]
    if not done:
        return None, reaches
    one = len(done) == 1
    message = (
        f"{', '.join(f'“{n}”' for n in done)} {'has' if one else 'have'} already run, or "
        f"{'is' if one else 'are'} under way, wherever this run runs {'it' if one else 'them'}, "
        "so there is nothing left to skip."
    )
    if len(result.ops) > len(done):
        others = any(op.kind != mutations.OpKind.SKIP for op in result.ops)
        message += f" Nothing else in this edit was {'applied' if others else 'skipped'} either."
    refusal = {
        "ok": False,
        "code": "WF_SKIP_REACHES_NOTHING",
        "message": message,
        "issues": [{"code": "WF_SKIP_REACHES_NOTHING", "message": message, "node_id": done[0]}],
    }
    return refusal, reaches


def _flag_stale(ctl: RunController, preview: mutations.CascadePreview) -> None:
    """Journal `inputs_stale` for done nodes outside the re-run set."""
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
