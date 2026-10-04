"""What a loop does about a tripped breaker: ONE decision, `loop.tick.evaluate`.

`resilience.check_breaker` stays the sole trip DETECTOR; this module is the response. A trip is the
question and the convergence core — driven by the loop node's `SupervisorPolicy`, with the run's
sparse overlay composed on top — gives the answer: wait, nudge, take a rung, replan, or hand the
loop to a human. The ladder position is persisted on the run row, so a resumed run answers the way
the same run would have before the restart.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from personalclaw.loop import tick as convergence
from personalclaw.workflows import ending_sentence, supervisor_policy
from personalclaw.workflows.loop_middleware import call_fingerprint, classify_failure
from personalclaw.workflows.models import InstanceState, Node, now_stamp
from personalclaw.workflows.node_bindings import iteration_output
from personalclaw.workflows.resilience import BreakerState
from personalclaw.workflows.supervisor_policy import tick_config as convergence_config

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController


#: How many convergence decisions per loop the run row keeps. Bounded, because an unbounded
#: decision log on a run row is a slow leak that reads like an audit trail.
_CONVERGENCE_LOG_MAX = 50


def _supervisor_policy(ctl: RunController, node: Node) -> supervisor_policy.SupervisorPolicy:
    """The loop's declared convergence policy, or the default posture.

    This is the call that makes `SupervisorPolicy` load-bearing: the thresholds
    `evaluate` reads come from the TEMPLATE's `supervisor:` block, not from constants
    buried in the engine. A node that declares none gets the default policy, whose
    values reproduce what the engine did before it was consulted.

    The run's sparse overlay composes ON TOP (PP-16 seam 4d, OWNER RULING 2): the
    template stays the shared default source — it structurally cannot hold a
    per-instance setting — and `run.policy_overrides` carries only the knobs THIS run
    overrode. A run with an empty overlay gets the template's policy object unchanged,
    so the sparse common case costs nothing.
    """
    declared = supervisor_policy.parse_supervisor_policy((node.config or {}).get("supervisor"))
    return supervisor_policy.apply_policy_overrides(declared, ctl.run.policy_overrides)


def _convergence_ledger(ctl: RunController, parent_path: str) -> dict[str, Any]:
    """This loop's persisted convergence position, on the run row.

    `run.extra` and not the event ledger, deliberately. The position has to be PERSISTED —
    an in-memory cursor is exactly what the deleted `check_middleware` kept, and it makes
    the ladder a property of this PROCESS's uptime, so the same run answers differently
    before and after a crash. But it must not be written as an `iteration` row either: that
    kind means "the loop body ran once", and a decision *about* the body is not another
    body run. Recording it there inflates every consumer's iteration count, including the
    `max_iterations` cap a user set.
    """
    book = ctl.run.extra.setdefault("convergence", {})
    if not isinstance(book, dict):
        book = {}
        ctl.run.extra["convergence"] = book
    entry = book.setdefault(parent_path, {})
    if not isinstance(entry, dict):
        entry = {}
        book[parent_path] = entry
    return entry


def _convergence_state(
    ctl: RunController, parent_path: str, node: Node, breaker: BreakerState, *, stall: str = ""
) -> convergence.TickState:
    """Assemble this loop's convergence snapshot. Pure over what it is handed.

    Two sources, each the one that owns its half:

    * **The failure evidence comes from the BREAKER.** `breaker.error_signatures` is the
      record the trip detector already collected, so the stall tier fires on the trip
      instead of waiting to re-observe the same thing N more times. Re-counting the
      failures here would be a second detector — the redundancy the R-de-dup ruling
      forbids — and it would also make the response arrive later than the detection.
    * **The ladder position comes from persisted run state** (`_convergence_ledger`), so a
      resumed run re-derives the same rung rather than restarting at the cheapest one.

    A loop whose body has stopped failing has no signatures, so the stall tiers are vacuous
    and `evaluate` falls through to the progress branches — the `reset_after_success`
    behaviour, obtained structurally rather than by remembering to call it.
    """
    book = _convergence_ledger(ctl, parent_path)
    signatures = [s for s in breaker.error_signatures if s]
    critique = ctl.run.extra.get("plan_critique")
    return convergence.TickState(
        step_index=0,
        step_started_at=0.0,
        # The workflows loop node's "call" is the failing NODE plus its failure signature:
        # the same node failing the same way repeatedly IS the identical-call signal,
        # expressed in what the breaker actually holds.
        call_fingerprints=tuple(call_fingerprint(node.id, sig) for sig in signatures),
        failure_classes=tuple(classify_failure(sig).value for sig in signatures),
        nudges_issued=int(book.get("nudges", 0) or 0),
        escalations_taken=int(book.get("escalations", 0) or 0),
        attempts_at_rung=int(book.get("attempts", 0) or 0),
        recoverable_waits=int(book.get("recoverable_waits", 0) or 0),
        replans_taken=int(book.get("replans", 0) or 0),
        plan_critique=critique.strip() if isinstance(critique, str) else "",
        stall_confirmed=stall,
    )


def _record_convergence(
    ctl: RunController,
    parent_path: str,
    cfg: convergence.TickConfig,
    state: convergence.TickState,
    decision: convergence.Decision,
) -> None:
    """Persist the counter advance `tick.applied` derives, plus a bounded audit log.

    `tick.applied` is the pure write half: it says what the counters BECOME, and this is the
    one place that puts them on disk. Splitting it that way is what keeps the position
    re-derivable — the decision never advances anything itself.
    """
    after = convergence.applied(cfg, state, decision)
    book = _convergence_ledger(ctl, parent_path)
    book["nudges"] = after.nudges_issued
    book["escalations"] = after.escalations_taken
    book["attempts"] = after.attempts_at_rung
    book["recoverable_waits"] = after.recoverable_waits
    book["replans"] = after.replans_taken
    # Bounded: an unbounded decision log on the run row is a slow leak that looks like an
    # audit trail.
    log = book.setdefault("log", [])
    if isinstance(log, list):
        log.append(decision.to_dict())
        del log[:-_CONVERGENCE_LOG_MAX]
    ctl._save_run()


def _replan_ops(
    ctl: RunController, node: Node, decision: convergence.Decision, *, attempt: int
) -> list[dict[str, Any]]:
    """The REAL mutation batch a `REPLAN` queues.

    Not a retry with the critique stapled to the prompt — that is what this replaces, and it
    is indistinguishable from the failing attempt in the spec, in `spec_history` and to a
    human reading either. An `insert` CHANGES the plan: the run's remaining steps now include
    a step that re-derives them from the critique, the spec version bumps, and the change is
    auditable. Placed immediately after the loop in the root sequence, so it is the next
    thing the run does with the work the critique rejected.
    """
    root = ctl.spec.get("root") or {}
    parent_id = ""
    at: int | None = None
    children = root.get("children")
    if isinstance(children, list):
        for i, child in enumerate(children):
            if isinstance(child, dict) and child.get("id") == node.id:
                # The realistic shape: the loop is a step in a sequence, so the replan step
                # is the next step — literally "the remaining steps changed".
                parent_id, at = str(root.get("id") or ""), i + 1
                break
    if at is None:
        body = root.get("body") if root.get("id") == node.id else None
        if isinstance(body, dict) and isinstance(body.get("children"), list):
            # The loop IS the root: its remaining work is its own further iterations, so the
            # replan step goes at the FRONT of the body and runs before the rejected work
            # is repeated.
            parent_id, at = str(body.get("id") or ""), 0
        else:
            # No structural target this batch could edit without inventing a container. A
            # replan that cannot land is not a replan, and quietly applying it somewhere
            # else would change a different part of the plan than the critique named.
            return []
    op: dict[str, Any] = {
        "op": "insert",
        "node": {
            "kind": "infer",
            "id": f"{node.id}__replan{attempt}",
            "name": "re-derive remaining steps",
            "config": {
                "prompt": (
                    "The plan for the remaining work was judged unsound. Critique:\n"
                    f"{decision.replan_directive}\n\n"
                    "Re-derive the remaining steps to satisfy the critique. Do not repeat "
                    "the rejected approach."
                )
            },
        },
        "note": f"PP-15 replan {attempt}: {decision.reason}",
        "index": at,
    }
    if parent_id:
        op["parent_id"] = parent_id
    return [op]


def converge_loop(
    ctl: RunController,
    parent_path: str,
    node: Node,
    iteration: int,
    *,
    breaker_reason: str,
    breaker_detail: str,
) -> bool:
    """Ask the ONE convergence core what to do about a tripped loop. `True` = the run stops.

    Replaces the BINARY trip handling. The breaker still detects the stall — it remains the
    sole trip authority, and nothing here re-counts what it counted — but "a stall was
    detected" and "therefore a human must look at this" were the same line, which made every
    middle rung of the declared ladder unreachable. Now the trip is the QUESTION and
    `evaluate` gives the answer: wait, nudge, change strategy, replan, or surface.
    """
    policy = _supervisor_policy(ctl, node)
    cfg = convergence_config(policy)
    breaker = ctl._breakers.setdefault(parent_path, BreakerState())
    state = _convergence_state(ctl, parent_path, node, breaker, stall=breaker_reason)
    # `time.time()`, NOT `now_stamp()`: the run clock is an ISO string and `evaluate` does
    # arithmetic on `now` (the dwell branch). Passing the display clock here is a TypeError
    # at the first tripped breaker — the one path a happy-path test never reaches.
    decision = convergence.evaluate(cfg, state, time.time())
    _record_convergence(ctl, parent_path, cfg, state, decision)
    ctl._publish(
        "workflow_loop_converged",
        {"instance_path": parent_path, "node_id": node.id, **decision.to_dict()},
    )

    if decision.action is convergence.Action.REPLAN:
        ops = _replan_ops(ctl, node, decision, attempt=state.replans_taken + 1)
        result = (
            ctl.submit_mutation(ops, actor="supervisor", confirm=True)
            if ops
            else {"ok": False, "issues": [{"code": "WF_REPLAN_NO_TARGET"}]}
        )
        if not result.get("queued"):
            # A replan that could not be queued is not a replan. Surfacing beats looping on
            # a plan the engine has just declared unsound.
            surface_loop(
                ctl,
                parent_path,
                node,
                reason=decision.reason,
                detail="The plan was judged unsound, and the change it needed could not be made",
            )
            return True
        # Consumed, so the next tick does not re-decide REPLAN against the same critique and
        # spend the whole budget re-deriving one plan.
        ctl.run.extra.pop("plan_critique", None)
        ctl._save_run()
        return False

    if decision.surfaced:
        surface_loop(
            ctl,
            parent_path,
            node,
            reason=decision.reason or breaker_reason,
            detail=decision.detail or breaker_detail,
        )
        return True

    if decision.nudge_text:
        existing = ctl._steering_inject.get(parent_path)
        ctl._steering_inject[parent_path] = (
            f"{existing}\n\n{decision.nudge_text}" if existing else decision.nudge_text
        )
    return False


def surface_loop(
    ctl: RunController, parent_path: str, node: Node, *, reason: str, detail: str
) -> None:
    """Hand a loop to a human. ESCALATED, deliberately NOT FAILED: "I gave up and a human
    must decide" is a different fact from "this broke", and collapsing them loses what the
    user needs to act on.

    *reason* and *detail* are what stopped it as its caller saw it — the budget it reached, the
    breaker's verdict. Why it stopped is not always that, and `ending_sentence.loop_stop` decides
    it once, for every surface: a loop whose cycles FAILED stops on its first failed step (#3524 —
    measured on a `general-project` run whose five of six iterations failed on a binding, the
    banner blamed its ceiling and the reader shrank a task that was never the problem), and a loop
    whose judge would not decide stops on the judge's own reason rather than on the budget that ran
    out beside it.
    """
    reason, stop = ending_sentence.loop_stop(ctl, parent_path, node, reason=reason, detail=detail)
    loop_inst = ctl._instance(parent_path)
    loop_inst.state = InstanceState.ESCALATED
    loop_inst.completed_at = now_stamp()
    ctl._escalate(parent_path, node.id, reason=reason, stop=stop)


def finish_loop(ctl: RunController, parent_path: str, node: Node, iteration: int) -> None:
    """End a loop DONE after cycle *iteration*, recording what it produced.

    A loop's output is what its last cycle produced, layered the way `{{last.output}}` and the
    loop's own `condition` read a cycle (`node_bindings.iteration_output`): the value it stopped
    on. It is recorded the way every step's output is, stored with the loop's instance and entered
    under the loop's id, so a step after the loop can read `{{nodes.<loop>.output}}` and a resumed
    run reads the same value back (`RunController._load_outputs`). Only a loop that ended done
    records one: a loop handed to a person (`surface_loop`) produced no result to read on, and a
    step that reads it is not run.
    """
    loop_inst = ctl._instance(parent_path)
    loop_inst.state = InstanceState.DONE
    loop_inst.completed_at = now_stamp()
    produced, _present = iteration_output(ctl, parent_path, iteration)
    loop_inst.output_ref, preview = ctl.journal.store_output(parent_path, produced)
    if node.id:
        ctl._outputs[node.id] = preview
