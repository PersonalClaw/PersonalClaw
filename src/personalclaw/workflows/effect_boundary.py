"""The effect ledger at execution time (WF2-R1).

`effects` owns the ledger's shape and rules; this is where the controller applies them. An
effect-committing dispatch records ATTEMPTED before it runs and its terminal verdict after, and a
node whose effect COMMITTED in an earlier epoch is refused — or its declared teardown runs first —
before it dispatches again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from personalclaw.workflows.effects import (
    EffectRecord,
    EffectStatus,
    committed_effect,
    committed_effect_refusal,
    effect_key,
    output_id_of,
    redo_blocked,
    run_teardown,
    teardown_refusal,
)
from personalclaw.workflows.engine import node_commits_effects
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeInstance,
    now_stamp,
)
from personalclaw.workflows.step_usage import NOTHING_SENT
from personalclaw.workflows.tick import ReadyNode

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController


def effect_key_for(ctl: RunController, path: str, inst: NodeInstance) -> str:
    return effect_key(ctl.run.id, path, inst.epoch, ctl._effects.get(path, []))


def record_effect(
    ctl: RunController,
    node: Node,
    path: str,
    inst: NodeInstance,
    status: EffectStatus,
    *,
    key: str = "",
    **fields: Any,
) -> None:
    """Journal one effect event and fold it into the in-memory history, so a
    same-process re-read agrees with what a resumed process would reconstruct.

    `key` overrides the derived key for records about a PRIOR epoch's effect — a
    COMPENSATED must carry the committed effect's own key, or `committed_effect`
    can never match the two and the boundary never clears.
    """
    record = EffectRecord(
        instance_path=path,
        idempotency_key=key or effect_key_for(ctl, path, inst),
        effect_status=status,
        epoch=inst.epoch,
        node_id=node.id,
        provider=str((node.config or {}).get("provider", "") or ""),
        output_id=str(fields.get("output_id", "") or ""),
        compensation_ref=str(fields.get("compensation_ref", "") or ""),
    )
    ctl.journal.effect(
        path,
        idempotency_key=record.idempotency_key,
        effect_status=status.value,
        epoch=inst.epoch,
        node_id=record.node_id,
        provider=record.provider,
        output_id=record.output_id,
        compensation_ref=record.compensation_ref,
        detail=str(fields.get("detail", "") or ""),
    )
    ctl._effects.setdefault(path, []).append(record)


def record_terminal_effect(
    ctl: RunController,
    node: Node,
    path: str,
    inst: NodeInstance,
    state: InstanceState,
    output: Any,
) -> None:
    """Record the terminal effect verdict for every effect-committing dispatcher."""
    if not node_commits_effects(node):
        return
    if state in SUCCESS_STATES and state != InstanceState.NO_CHANGE:
        # COMMITTED captures the teardown ref AT COMMIT TIME: a later spec edit
        # must not change what tears down an already-provisioned resource.
        record_effect(
            ctl,
            node,
            path,
            inst,
            EffectStatus.COMMITTED,
            output_id=output_id_of(output),
            compensation_ref=str((node.config or {}).get("teardown", "") or ""),
        )
    elif state == InstanceState.NO_CHANGE:
        # The dispatcher reported `skip` — nothing fired, and the ledger says so.
        record_effect(ctl, node, path, inst, EffectStatus.SKIPPED)


async def effect_preflight(ctl: RunController, item: ReadyNode, inst: NodeInstance) -> bool:
    """The committed-effect boundary, enforced before a tool-bearing node re-executes.

    Returns False when the node was refused (a terminal state was written). Membership
    follows the actual dispatcher, so pure dispatches pass through.
    A same-epoch retry passes too — it reuses the same idempotency key, which an
    idempotent receiver dedupes, so it is the retry contract working, not a
    double-fire.
    """
    if not node_commits_effects(item.node):
        return True
    committed = committed_effect(ctl._effects.get(item.path, []))
    if committed is None or committed.epoch == inst.epoch:
        return True
    if redo_blocked(item.node.config or {}, committed, inst.epoch):
        inst.state = InstanceState.BLOCKED
        inst.completed_at = now_stamp()
        inst.failure = committed_effect_refusal(item.node.id or item.path, committed.epoch)
        ctl.journal.step_failed(
            item.path,
            item.node.id,
            epoch=inst.epoch,
            failure=inst.failure,
            usage=NOTHING_SENT,
            attempt=inst.attempt,
            retries_exhausted=True,
        )
        ctl._persist_state()
        ctl._publish(
            "workflow_node_done",
            {
                "node_id": item.node.id,
                "instance_path": item.path,
                "status": InstanceState.BLOCKED.value,
                "degraded_reason": "committed_effect",
            },
        )
        return False
    # redo_effects: true — tear down the committed resource first, then proceed.
    if committed.compensation_ref:
        runner = ctl.services.teardown_runner
        ok, detail = await run_teardown(
            committed.compensation_ref, committed.output_id, runner=runner
        )
        if not ok:
            # A failed teardown leaves an UNKNOWN external state; proceeding would
            # stack a second resource on top of a live first one.
            inst.state = InstanceState.BLOCKED
            inst.completed_at = now_stamp()
            inst.failure = teardown_refusal(detail)
            ctl.journal.step_failed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                failure=inst.failure,
                usage=NOTHING_SENT,
                attempt=inst.attempt,
                retries_exhausted=True,
            )
            ctl._persist_state()
            return False
        record_effect(
            ctl,
            item.node,
            item.path,
            inst,
            EffectStatus.COMPENSATED,
            key=committed.idempotency_key,
            output_id=committed.output_id,
            compensation_ref=committed.compensation_ref,
            detail=detail[:500],
        )
    return True
