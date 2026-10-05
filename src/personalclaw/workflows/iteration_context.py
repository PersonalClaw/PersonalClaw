"""The context lifecycle across a loop's iterations.

A `session: fresh` iteration starts clean, so what the previous one learned has to be handed over
explicitly: its `handoff`, the merged `carryover` buckets and its `decisions`. This module captures
them from an iteration's OWN output, journals every write, rebuilds them — and each loop's iteration
counter — from the ledger on start and resume, takes the steering queued for the next iteration
(`consume_steering`), and renders the block a fresh iteration's prompt starts from, together with
any steering parked for it.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import context as context_mod
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import longrun, store
from personalclaw.workflows.loop_middleware import InterruptQueue
from personalclaw.workflows.models import Node, loop_parent, walk
from personalclaw.workflows.tick import ReadyNode

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)

#: The outcome an `iteration` row carries for a cycle its owner ended with a Deny, after which the
#: loop waits for her (`declines.end_cycle`): it went on past the cycle, as past a `continue`, and
#: its counter moved on. A loop that ends with such a cycle writes the word it ends with instead.
DECLINED_CYCLE = "declined"


def consume_steering(ctl: RunController, parent_path: str, node: Node, iteration: int) -> None:
    """Consume mid-run steering at the loop boundary.

    The durable queue (`store.queue_steering`, written by `service.steer_run`) is taken HERE —
    under the lock, between iterations, exactly like `mid_flight.drain_mutations`. Mid-iteration
    injection would race the worker's own state; the boundary is where the next iteration can
    actually act on the instruction. Single-use: the queue is emptied and the rendered block parked
    on `_steering_inject` for the next iteration's prompt, so a resume cannot replay it. Journaled
    so a refiner can tell a human-steered verdict from an autonomous one — without the event the
    two are indistinguishable (see journal.STEERING). A loop that waited for its owner after her
    Deny is at that boundary again when she resumes it (`declines.carry_on`), so what she queued
    while it waited reaches the cycle her Resume runs.

    The queue is taken before the event is journaled: a crash between the two loses an
    instruction rather than replaying one the ledger already records as consumed.
    """
    pending = store.take_steering(ctl.run.id)
    if not pending:
        return
    queue = InterruptQueue()
    for entry in pending:
        queue.push(str(entry.get("text", "") or ""), now=time.time())
    consumed = queue.consume(now=time.time())
    texts = [i.text for i in consumed]
    if not texts:
        return
    block = queue.as_steering_prompt(consumed)
    # Append if a block is already parked (two steers before the next iteration read either):
    # the newest instruction goes last, the same order `consume` preserves.
    existing = ctl._steering_inject.get(parent_path)
    ctl._steering_inject[parent_path] = f"{existing}\n\n{block}" if existing else block
    ctl.journal.write(
        journal_mod.STEERING,
        instance_path=parent_path,
        node_id=node.id,
        iteration=iteration,
        count=len(texts),
        texts=texts,
    )
    ctl._publish(
        "workflow_steering_consumed",
        {"instance_path": parent_path, "node_id": node.id, "count": len(texts)},
    )


def with_carried_context(ctl: RunController, node: Node, item: ReadyNode) -> Node:
    """Prepend the previous iteration's handoff/carryover/decisions to a fresh session's prompt.

    This is what makes `session: fresh` mean something. Without it the policy is a label: the
    iteration starts clean and also starts BLIND, re-deriving what the previous one verified —
    which is worse than the continuous session it replaced.

    Prepended, not appended: it is context the reader needs BEFORE the instruction, and a model
    that reads the task first has already begun planning without the constraints.

    A copy, never a mutation — the spec node is shared across every instance of an iterated
    body, and editing it in place would leak iteration 3's context into iteration 1's prompt on
    a rewind. Same reasoning as `step_dispatch._with_retry_hint`.
    """
    prompt = (node.config or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return node
    carried = _carried_context(ctl, item)
    if not carried:
        return node
    import copy

    out = copy.deepcopy(node)
    out.config["prompt"] = f"{carried}\n\n---\n\n{prompt}"
    return out


def capture_iteration_context(
    ctl: RunController, parent_path: str, node: Node, iteration: int, output: Any
) -> None:
    """Journal the handoff, carryover and decision an iteration produced.

    Read from the iteration's OWN OUTPUT rather than inferred: a node that wants to hand
    something to the next iteration says so in a `handoff` / `carryover` / `decision` key, and
    inferring one from prose would produce exactly the lossy summary the mechanism replaces.
    A node that says nothing hands over nothing, which is correct — a fabricated handoff is
    worse than none, because the next iteration would trust it.

    Never raises: a run must not die because a bookkeeping write failed.
    """
    if not isinstance(output, dict):
        return
    inst = ctl._instance(parent_path)
    try:
        handoff = context_mod.Handoff.from_dict(output.get("handoff"))
        if not handoff.empty:
            ctl._handoffs[parent_path] = handoff
            ctl.journal.handoff(
                parent_path,
                node.id,
                epoch=inst.epoch,
                iteration=iteration,
                handoff=handoff.to_dict(),
            )

        fresh = context_mod.Carryover.from_dict(output.get("carryover"))
        if not fresh.empty:
            # MERGED into what the loop already carried, not replaced: the buckets accumulate
            # across iterations, and an iteration that only touched one file must not erase the
            # nine the previous ones verified.
            merged = ctl._carryover.get(parent_path, context_mod.Carryover()).merge(fresh)
            ctl._carryover[parent_path] = merged
            ctl.journal.carryover(
                parent_path,
                node.id,
                epoch=inst.epoch,
                iteration=iteration,
                buckets=merged.to_dict(),
            )

        raw_decisions = output.get("decisions")
        if isinstance(output.get("decision"), dict):
            raw_decisions = [output["decision"]]
        for raw in raw_decisions if isinstance(raw_decisions, list) else []:
            decision = context_mod.Decision.from_dict(raw if isinstance(raw, dict) else None)
            if decision.empty:
                continue
            ctl._decisions.setdefault(parent_path, []).append(decision)
            ctl.journal.decision(
                parent_path, node.id, epoch=inst.epoch, decision=decision.to_dict()
            )

        # A node that made a measurable bet says so in a `pending_outcome`
        # key {subject, metric, horizon_secs, baseline}. It is journaled as an OPEN
        # question at decision time — a single/list of dicts, mirroring `decision` —
        # and the curator's resolver measures ground truth once the horizon elapses.
        # Kept separate from `decision` on purpose: a Decision is a settled choice, a
        # pending outcome is a claim about the future the run cannot yet evaluate.
        raw_pending = output.get("pending_outcomes")
        if isinstance(output.get("pending_outcome"), dict):
            raw_pending = [output["pending_outcome"]]
        for raw in raw_pending if isinstance(raw_pending, list) else []:
            if not isinstance(raw, dict):
                continue
            subject = str(raw.get("subject") or "").strip()
            metric = str(raw.get("metric") or "").strip()
            if not subject or not metric:
                continue
            try:
                horizon = float(raw.get("horizon_secs") or 0.0)
                baseline = float(raw.get("baseline") or 0.0)
            except (TypeError, ValueError):
                continue
            ctl.journal.pending_outcome(
                parent_path,
                node.id,
                epoch=inst.epoch,
                subject=subject,
                metric=metric,
                horizon_secs=horizon,
                baseline=baseline,
            )
    except Exception:
        logger.debug(
            "run %s: could not capture iteration context at %s",
            ctl.run.id,
            parent_path,
            exc_info=True,
        )


def rehydrate_context(ctl: RunController) -> None:
    """Rebuild the context lifecycle from the ledger on start/resume.

    Replays in ledger ORDER, so the last handoff per container wins and the carryover arrives
    already merged (each write journaled the merged state, so the final one is complete). A
    rewind's records are naturally excluded because a rewind archives the region's journal
    entries — the replay sees only what is still live.

    Never raises: a resumed run must start even if its ledger is partly unreadable. It would
    start context-blind, which is worse than nothing but far better than not starting.
    """
    try:
        records = journal_mod.ledger(
            ctl.run.id,
            kinds={
                journal_mod.HANDOFF,
                journal_mod.CARRYOVER,
                journal_mod.DECISION,
                journal_mod.SEEN_SET,
            },
        )
    except Exception:
        logger.debug("run %s: could not read the context ledger", ctl.run.id, exc_info=True)
        return
    for rec in records:
        path = str(rec.get("instance_path", "") or "")
        if not path:
            continue
        kind = rec.get("kind")
        try:
            if kind == journal_mod.HANDOFF:
                ctl._handoffs[path] = context_mod.Handoff.from_dict(rec)
            elif kind == journal_mod.CARRYOVER:
                # Each journaled bucket set is already the MERGED state at that point, so the
                # later record replaces rather than re-merges — re-merging would be harmless but
                # would quietly double the dedup work on every resume.
                ctl._carryover[path] = context_mod.Carryover.from_dict(rec)
            elif kind == journal_mod.DECISION:
                decision = context_mod.Decision.from_dict(rec)
                if not decision.empty:
                    ctl._decisions.setdefault(path, []).append(decision)
            elif kind == journal_mod.SEEN_SET:
                # Each record carries the WHOLE set at that point, so the last one wins —
                # the same reason as CARRYOVER. A delta encoding would be smaller but would
                # make a partially-unreadable ledger reconstruct a WRONG set rather than an
                # old one, and an old seen-set only costs tokens.
                ctl._seen[path] = longrun.SeenSet.from_dict(rec.get("seen") or {})
        except Exception:
            logger.debug("run %s: skipping unreadable context record", ctl.run.id)


def rehydrate_loop_progress(ctl: RunController) -> None:
    """Rebuild every loop's iteration counter from the ledger on start/resume.

    🔴 ``_iterations`` lived in memory only, and it is what the frontier reads to know WHICH
    iteration of a loop's body is current. A controller built by a restarted gateway started
    every loop back at iteration 0 — whose body was already terminal — so nothing was runnable
    and the run FAILED "run deadlocked: no runnable nodes and none in flight". Measured on a
    General loop paused in its second iteration, the gateway restarted, then resumed: failed
    five seconds after Resume. Every run whose loop was past its first iteration when the
    process ended had the same fate.

    The counter advances exactly when ``loop_iteration.advance_loop`` journals a ``continue``
    iteration, or a cycle its owner ended with a Deny (:data:`DECLINED_CYCLE`), so those records
    are its durable form: the next iteration of each loop is one past the last one that went on.
    Replayed like ``rehydrate_context`` — a rewind archives the rows it undoes, so only live
    history counts — and never raises, for the same reason. ``max`` keeps a same-process restart
    of the tick loop (a resume) from moving a counter backwards.

    The dry streak and breaker evidence are NOT journaled and start empty: a resumed
    ``until_dry`` loop may run up to ``streak`` more iterations before it can stop dry, which
    costs cycles rather than correctness.
    """
    try:
        rows = journal_mod.ledger(ctl.run.id, kinds={journal_mod.ITERATION})
    except Exception:
        logger.debug("run %s: could not read the iteration ledger", ctl.run.id, exc_info=True)
        return
    for rec in rows:
        path = str(rec.get("instance_path", "") or "")
        iteration = rec.get("iteration")
        went_on = rec.get("outcome") in ("continue", DECLINED_CYCLE)
        if not path or not isinstance(iteration, int) or not went_on:
            continue
        ctl._iterations[path] = max(int(ctl._iterations.get(path, 0)), iteration + 1)


def _carried_context(ctl: RunController, item: ReadyNode) -> str:
    """The context block a `session: fresh` iteration starts from, or "".

    Only for a node INSIDE an iterated container: a top-level node has no previous iteration to
    inherit from, and injecting an empty block would teach a model the section is noise.

    `session: continuous` returns "" deliberately — a continuous session already HAS the
    previous iteration's context in its transcript, and prepending a handoff to it would say
    everything twice.
    """
    parent_path, _iteration = loop_parent(item.path)
    if parent_path is None:
        return ""
    node = dict(walk(ctl.root)).get(parent_path)
    if node is None:
        return ""
    # Steering reaches BOTH session policies: a mid-run instruction must land even in a
    # `continuous` loop, whose transcript otherwise carries no fresh block. Consume it single-
    # use — once it is in a prompt, a re-render must not repeat it. It goes LAST, after the
    # handoff/carryover, because it is the newest and highest-priority instruction.
    steer = ctl._steering_inject.pop(parent_path, "")
    if context_mod.session_policy(node.config) != context_mod.SESSION_FRESH:
        return steer
    carried = context_mod.render_context(
        handoff=ctl._handoffs.get(parent_path),
        carryover=ctl._carryover.get(parent_path),
        decisions=ctl._decisions.get(parent_path),
    )
    if steer:
        return f"{carried}\n\n{steer}" if carried else steer
    return carried
