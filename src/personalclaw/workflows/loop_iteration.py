"""What happens at a loop's iteration boundary.

The counter advance, the `until_dry` streak, the breaker fed and asked, steering
consumed between iterations, the long-run seen-set
and the continue/stop decision. Both settle paths reach it — `RunController._apply` for an awaited
dispatch and `stage_settlement.reconcile_dispatched_stages` for a spawned stage — which is why
`advance_loop` takes the settled leaf's path and id rather than a `ReadyNode`. What a TRIPPED
breaker means is `loop_convergence`'s question.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import iteration_context
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import longrun, loop_convergence, node_bindings, supervisor_policy
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.loop_middleware import InterruptQueue
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    LoopMode,
    Node,
    NodeKind,
    loop_parent,
    now_stamp,
    spec_path,
    walk,
)
from personalclaw.workflows.resilience import (
    BreakerState,
    BreakerVerdict,
    check_breaker,
    error_signature,
)
from personalclaw.workflows.tick import derive_state, loop_should_continue

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController


#: Breaker reasons that are a DECLARED BUDGET being reached, not a stall.
#:
#: The distinction decides who answers the trip. A loop that thrashes is recoverable — that is
#: what the escalation ladder is for, and failing it binary is the bug PP-15 fixes. A loop that
#: reached the `max_iterations` or token cap ITS AUTHOR SET is not thrashing and has nothing
#: cheaper to try: spending a fresh session and a model switch on a satisfied budget would
#: re-run the work the cap existed to bound. So a spent budget skips the ladder and ends the
#: loop — complete when its own exit test was met or its judge accepted the last iteration, and
#: escalated, naming the budget, when it would otherwise have gone on — and only thrash reaches
#: the ladder.
BUDGET_TRIPS = frozenset({"max_iterations", "token_cap"})


def _consume_steering(ctl: RunController, parent_path: str, node: Node, iteration: int) -> None:
    """Consume mid-run steering at the loop boundary (LOOPS-EVOLUTION R14).

    The durable queue (`run.extra["steering_queue"]`, written by `service.steer_run`) is
    drained HERE — under the lock, between iterations, exactly like `mid_flight.drain_mutations`.
    Mid- iteration injection would race the worker's own state; the boundary is where the next
    iteration can actually act on the instruction. Single-use: the queue is cleared and the rendered
    block parked on `_steering_inject` for the next iteration's prompt, so a resume cannot replay
    it. Journaled so a refiner can tell a human-steered verdict from an autonomous one — without the
    event the two are indistinguishable (see journal.STEERING).
    """
    pending = ctl.run.extra.get("steering_queue")
    if not isinstance(pending, list) or not pending:
        return
    ctl.run.extra["steering_queue"] = []
    queue = InterruptQueue()
    for entry in pending:
        text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
        queue.push(text, now=time.time())
    consumed = queue.consume(now=time.time())
    texts = [i.text for i in consumed]
    if not texts:
        ctl._save_run()
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
    # Persist the drained queue immediately: a crash between here and the next tick must not
    # resurrect an instruction the ledger already records as consumed.
    ctl._save_run()


def _loop_node_under_overlay(ctl: RunController, node: Node) -> Node:
    """The loop node with the run's ``max_cycles`` override applied as its iteration cap.

    A template is SHARED across runs, so a per-instance cycle budget cannot live in it (OWNER
    RULING 2); the run's overlay carries it, and this is where it meets the one config key the
    engine bounds iterations by. A copy, never an edit: the spec every other reader walks must
    keep the template's declaration. No override (or ``0``) returns the node unchanged.
    """
    cap = supervisor_policy.loop_iteration_cap(ctl.run.policy_overrides)
    if not cap:
        return node
    return replace(node, config={**(node.config or {}), "max_iterations": cap})


def advance_loop(ctl: RunController, path: str, node_id: str) -> None:
    """Advance a loop's iteration counter when its body finished an iteration.

    The counter lives here rather than in the frontier because advancing it is a
    WRITE, and the frontier is pure. `loop_should_continue` keeps the decision itself
    pure and testable.

    Takes the settled leaf's PATH and ID rather than a `ReadyNode`, because there are two
    settle paths and only one of them has a `ReadyNode` in hand. `_apply` settles an awaited
    dispatch; `stage_settlement.reconcile_dispatched_stages` settles a spawned `stage`, whose
    awaited coroutine already returned at the spawn. Both must advance the loop — see that method's
    own note on why a dispatched stage is not an `_inflight` entry.
    """
    parent_path, iteration = loop_parent(path)
    if parent_path is None:
        return
    node = dict(walk(ctl.root)).get(spec_path(parent_path))
    if node is None or node.kind != NodeKind.LOOP:
        return
    # The run's own cycle budget, applied to the ONE node both iteration-cap readers below
    # consult (`check_breaker` and `loop_should_continue` both read `max_iterations`).
    node = _loop_node_under_overlay(ctl, node)
    if not _iteration_complete(ctl, node, parent_path, iteration):
        # A CONTAINER-bodied loop calls this once per leaf. Advancing on the first one
        # would end the iteration mid-cycle: the wait would complete, the counter would
        # move, and the synthesize stage after it would be scheduled into the NEXT
        # iteration's path — where nothing had produced its inputs.
        return
    output = ctl._outputs.get(node_id)
    if _iteration_is_dry(ctl, node, parent_path, iteration, output):
        ctl._dry_streaks[parent_path] = ctl._dry_streaks.get(parent_path, 0) + 1
    else:
        ctl._dry_streaks[parent_path] = 0

    # Feed the breaker, then consult it BEFORE the next iteration. Deterministic and
    # LLM-free: a loop thrashing on the same error is the most common autonomous-run
    # failure, and paying a model to notice it would be slower and less reliable.
    #
    # This `check_breaker` is the SOLE trip DETECTOR (LOOPS-EVOLUTION R-de-dup): nothing
    # below re-counts what it counted. What changed in PP-15 is what a trip MEANS. It used to
    # mean "escalate to a human", which made the declared five-rung ladder unreachable — the
    # engine failed binary after two consecutive errors. Now a trip is the question, and
    # `loop.tick.evaluate` — the ONE convergence core, shared with the loop kinds and driven
    # by this node's `SupervisorPolicy` — gives the answer: wait, nudge, take a rung, replan,
    # or surface. `loop_middleware.check_middleware`, which used to hold a second copy of
    # that reasoning over a mutable cursor, is deleted.
    inst = ctl._instance(path)
    breaker = ctl._breakers.setdefault(parent_path, BreakerState())
    breaker.record(
        signature=error_signature(inst.failure) if inst.failure else "",
        output=output,
        tokens=inst.tokens,
    )
    verdict = check_breaker(node, breaker)
    # A spent budget is not a stall, and it is not the answer yet either: whether this
    # iteration finished the loop is asked first, below.
    spent = verdict.tripped and verdict.reason in BUDGET_TRIPS
    if verdict.tripped and not spent:
        _journal_breaker_trip(
            ctl, parent_path, node, iteration, breaker, inst.tokens, verdict.reason
        )
        if loop_convergence.converge_loop(
            ctl,
            parent_path,
            node,
            iteration,
            breaker_reason=verdict.reason,
            breaker_detail=verdict.detail,
        ):
            return

    # Consume steering BEFORE the continue decision, so a mid-run instruction reaches the next
    # iteration's prompt (R14). Drained even when the loop is about to end — a dropped
    # instruction the user cannot see is indistinguishable from one that was silently ignored,
    # so the STEERING event is journaled regardless; only the injection needs a next iteration.
    _consume_steering(ctl, parent_path, node, iteration)

    # ONE definition of `{{last.output}}`, shared with the body (`node_bindings._last_output`). The
    # loop's own `condition` used to read the LAST SETTLED LEAF's output instead, which is a
    # different value the moment the body is a container: `goal-pursuit-verifiable` ends its body on
    # `judge` and tests `{{last.output.command_passed}}`, a key only its `fix` stage emits, so that
    # condition could never resolve and the loop exited `condition_unresolvable` every time. A leaf
    # body layers exactly one mapping, so nothing changes there.
    layered, _ = node_bindings.iteration_output(ctl, parent_path, iteration)
    ctx = BindingContext(
        inputs=ctl.run.inputs,
        node_outputs=ctl._outputs,
        node_artifacts=node_bindings.node_artifacts(ctl),
        iter_index=iteration,
        last_output=layered,
        has_last=True,
    )
    keep_going, reason = loop_should_continue(
        node,
        iteration=iteration + 1,
        last_output=output,
        dry_streak=ctl._dry_streaks.get(parent_path, 0),
        ctx=ctx,
    )
    # The budget stops a loop only when the loop would otherwise go on: its own exit test ran
    # first (`loop_should_continue`), and a loop that met it on the last iteration its budget
    # allows has finished. The budget used to be checked first, so exactly those loops ended
    # escalated instead of complete.
    budget = None
    if keep_going and spent:
        budget = verdict
    elif not keep_going and reason == "max_iterations":
        budget = BreakerVerdict(True, "max_iterations")
    if budget is not None:
        accepted = _judge_accepted(ctl, node, parent_path, iteration)
        if accepted:
            # The judge accepted the iteration the budget ended on: the loop's done. One the
            # judge did not accept, or with no judge, genuinely ran out of budget.
            keep_going, reason = False, "judge_done"
        else:
            _journal_breaker_trip(
                ctl, parent_path, node, iteration, breaker, inst.tokens, budget.reason
            )
            loop_convergence.surface_loop(
                ctl,
                parent_path,
                node,
                reason=budget.reason,
                detail=_budget_detail(node, budget.reason, breaker, judged=accepted is not None),
            )
            return
    ctl.journal.iteration(
        parent_path,
        node.id,
        iteration=iteration,
        outcome=reason or "continue",
        error_signature="",
        tokens=0,
    )
    # Mark the seen-set only now — AFTER the iteration succeeded. Marking at read time
    # means a cycle that dies mid-synthesis has already suppressed items it never
    # processed, and nothing will ever surface them again.
    _mark_seen(ctl, parent_path, node, output)
    # Capture what this iteration hands to the next, BEFORE the counter advances.
    # Journaled rather than held in memory so a rewind to this iteration replays the handoff
    # it actually had, instead of reconstructing one from a transcript that no longer exists —
    # which is the summarization failure handoffs exist to replace.
    iteration_context.capture_iteration_context(ctl, parent_path, node, iteration, output)
    if keep_going:
        ctl._iterations[parent_path] = iteration + 1
        return

    # The loop is out of iterations. If it is STILL thrashing it did not FINISH — it ran out
    # of room while failing, and `DONE` would hand the user a complete run full of garbage.
    #
    # This restores the terminal outcome the binary handling gave for free. Under the old
    # code a thrash escalated on its FIRST trip, so it could never reach its last iteration;
    # now the ladder deliberately keeps it running, and a loop whose iteration budget is
    # smaller than the ladder's attempt budget would otherwise walk off the end reporting
    # success. Re-asking the SOLE detector is how the two endings are told apart without
    # inventing a second piece of state: anything tripping here was tripping earlier too.
    final = check_breaker(node, breaker)
    if final.tripped and final.reason not in BUDGET_TRIPS:
        loop_convergence.surface_loop(
            ctl, parent_path, node, reason=final.reason, detail=final.detail
        )
        return

    loop_inst = ctl._instance(parent_path)
    loop_inst.state = InstanceState.DONE
    loop_inst.completed_at = now_stamp()


def _journal_breaker_trip(
    ctl: RunController,
    parent_path: str,
    node: Node,
    iteration: int,
    breaker: BreakerState,
    tokens: int,
    reason: str,
) -> None:
    """Journal the iteration a breaker trip ended, a stall it caught or a budget it spent.

    One writer for both, so the ``breaker:<reason>`` outcome is spelled in one place
    (``tests/test_audit_outcome_families.py`` counts each site it cannot read statically)."""
    ctl.journal.iteration(
        parent_path,
        node.id,
        iteration=iteration,
        outcome=f"breaker:{reason}",
        error_signature=breaker.error_signatures[-1] if breaker.error_signatures else "",
        tokens=tokens,
    )


def _judge_accepted(
    ctl: RunController, node: Node, parent_path: str, iteration: int
) -> bool | None:
    """Did this iteration's judge accept it? ``None`` when no judge ruled on a whole iteration.

    The judge is the body stage that declares ``judge_contract``, and "accepted" is its
    validated ``passed``: a PASS the contract upheld (`judge_contract.JudgeVerdict.passed`), not
    a PASS that scored none of its rubric. Read only from a judge whose instance for THIS
    iteration succeeded, the rule `_progress_value` states for why; the last one in document
    order wins. An iteration in which any body node FAILED has no ruling here: a judge that
    passed it was ruling on work that did not finish, and `surface_loop` tells that story.
    """
    if node.body is None:
        return None
    base = f"{parent_path}.body@{iteration}"
    if any(
        inst.state is InstanceState.FAILED
        for path, inst in ctl.instances.items()
        if path == base or path.startswith(f"{base}.")
    ):
        return None
    accepted: bool | None = None
    for sub, child in walk(node.body):
        if not child.id or not (child.config or {}).get("judge_contract"):
            continue
        inst = ctl.instances.get(base if sub == "root" else f"{base}{sub[len('root'):]}")
        if inst is None or inst.state not in SUCCESS_STATES:
            continue
        out = ctl._outputs.get(child.id)
        accepted = isinstance(out, dict) and out.get("passed") is True
    return accepted


def _budget_detail(node: Node, reason: str, breaker: BreakerState, *, judged: bool) -> str:
    """The sentence a loop that ran out of budget escalates with: the budget, by name.

    ``judged`` says whether a judge ruled on the iteration the budget ended on, which is what
    tells "the judge did not accept it" apart from "its own exit test was not met".
    """
    cfg = node.config or {}
    if reason == "token_cap":
        cap = cfg.get("max_tokens")
        spent = f"{cap:,} tokens" if isinstance(cap, int) else "tokens"
        used = f" ({breaker.tokens:,} used)"
        budget = f"It used its budget of {spent}{used}"
    else:
        cap = cfg.get("max_iterations")
        count = cap if isinstance(cap, int) and cap > 0 else breaker.iterations
        budget = f"It used its budget of {count} cycle{'' if count == 1 else 's'}"
    if judged:
        return f"{budget}, and the judge did not accept the last one."
    return f"{budget} before its exit condition was met."


def _iteration_complete(ctl: RunController, node: Node, parent_path: str, iteration: int) -> bool:
    """Has this loop iteration's WHOLE body reached a terminal state?

    Derived through the same `frontier` machinery the scheduler uses, so "the body finished"
    means exactly what it means everywhere else. A leaf body is trivially complete on its own
    completion; a container body is complete only when its children are.
    """
    if node.body is None:
        return True
    state = derive_state(
        node.body,
        f"{parent_path}.body@{iteration}",
        {p: i.state for p, i in ctl.instances.items()},
        declined_edges=ctl._declined_edges,
        outputs=ctl._outputs,
        inputs=ctl.run.inputs,
        iterations=ctl._iterations,
    )
    return state in TERMINAL_STATES


def _iteration_is_dry(
    ctl: RunController, node: Node, parent_path: str, iteration: int, output: Any
) -> bool:
    """Did this iteration surface nothing new? Feeds the `until_dry` streak.

    TWO rules, and which applies is the TEMPLATE's declaration, not the engine's guess:

    * the loop declares `progress_field` → **that field decides**, wherever inside the
      iteration it was emitted (`_progress_reading` states the per-type rule);
    * it declares none → the whole last output decides (`_is_dry`), byte-for-byte what
      every loop did before this. Most loops declare none, and none of them change.

    A declared field this iteration did not emit falls back to the whole-output rule
    instead of counting as dryness. Deliberate direction: treating an absent field as
    "nothing new" would end the user's loop after `streak` iterations because the body
    forgot a key — silently truncating real work. Paying for one more iteration and
    learning nothing is the cheaper mistake, and it is visible; a truncated run is not.
    """
    field = str((node.config or {}).get("progress_field", "") or "")
    if not field:
        return _is_dry(output)
    found, value = _progress_value(ctl, node, parent_path, iteration, field)
    if not found:
        return _is_dry(output)
    reading = _progress_reading(value)
    if reading == _UNREADABLE:
        # A type with no rule is not evidence of dryness (e.g. an oversize output whose
        # inline preview is a `result_omitted` stub). Same direction as absence.
        return _is_dry(output)
    return reading == _DRY


def _progress_value(
    ctl: RunController, node: Node, parent_path: str, iteration: int, field: str
) -> tuple[bool, Any]:
    """This iteration's value for `field` as `(found?, value)`.

    `found` is separate from the value because ``None`` is a legitimate DRY reading —
    "the body said nothing" — and absence is not; collapsing them would make a missing
    key end the loop.

    Scans the loop BODY, not just the output `advance_loop` is holding. That output is
    the last leaf to finish, and both shipped templates that declare a progress field
    put it on the FIRST stage of a sequence body and end each iteration on a judge stage
    whose schema has no such key — so reading only the last leaf would leave this control
    inert for exactly the templates that asked for it.

    Restricted to nodes whose instance for THIS iteration succeeded: `ctl._outputs` is
    keyed by node id, so a body node that did not run this time still holds the PREVIOUS
    iteration's value, and reading that would report last iteration's progress as this
    one's. The last match in document order wins — the iteration's latest word on its
    own progress.
    """
    if node.body is None:
        return False, None
    base = f"{parent_path}.body@{iteration}"
    found, value = False, None
    for sub, child in walk(node.body):
        if not child.id:
            continue
        inst = ctl.instances.get(base if sub == "root" else f"{base}{sub[len('root'):]}")
        if inst is None or inst.state not in SUCCESS_STATES:
            continue
        out = ctl._outputs.get(child.id)
        if isinstance(out, dict) and field in out:
            found, value = True, out[field]
    return found, value


def _mark_seen(ctl: RunController, parent_path: str, node: Node, output: Any) -> None:
    """Record what a successful cycle consumed, and journal the whole set.

    Only for loops that actually accumulate — a `counted` loop over three review passes
    has no items and no reason to carry a seen-set. Keyed by the loop's path so two
    watchers in the same run keep independent sets: a shared one would have each watcher
    suppressing the other's novel items, and the symptom (a watcher that mysteriously
    finds nothing) points nowhere near the cause.
    """
    cfg = node.config or {}
    if str(cfg.get("mode", "") or "") != LoopMode.UNTIL_CANCELLED.value:
        return
    items = longrun._flatten_outputs([output])
    if not items:
        return
    seen = ctl._seen.setdefault(parent_path, longrun.SeenSet())
    added = seen.mark_all(items)
    if not added:
        return
    ctl.journal.write(
        journal_mod.SEEN_SET,
        instance_path=parent_path,
        node_id=node.id,
        seen=seen.to_dict(),
        added=added,
        total=len(seen),
    )


def _is_dry(output: Any) -> bool:
    """Did an iteration surface anything new, judged by its WHOLE output?

    The rule for a loop that declares no `progress_field`. Unchanged: an empty or absent
    output is dry, anything else is progress.
    """
    if output is None:
        return True
    if isinstance(output, (list, dict, str)):
        return len(output) == 0
    return False


#: One reading of a loop's declared `progress_field`. A CLOSED set of three — `_progress_
#: reading` returns exactly one of them, and `unreadable` exists precisely so that no value
#: falls into a default branch that guesses.
_DRY = "dry"


_PROGRESS = "progress"


_UNREADABLE = "unreadable"


def _progress_reading(value: Any) -> str:
    """Classify ONE value of a loop's declared `progress_field`: dry, progress, unreadable.

    The rule, stated once: **a declared progress field is dry when its value is the field's
    own expression of "nothing"** — zero, blank, empty, false, or null. Per type, exhaustively:

    * ``None`` → dry. The body answered the question with "nothing".
    * ``bool`` → ``False`` dry, ``True`` progress. A boolean field IS the answer; checked
      before ``int`` because ``bool`` is an ``int`` subclass and would otherwise be read as
      "1 finding" / "0 findings" by accident.
    * ``int`` / ``float`` → dry iff ``== 0``. This is the shipped `new_findings_count: 0`
      case. A NEGATIVE count is progress, not dryness: a nonsensical count is not evidence
      that nothing happened, and reading it as dryness would cut the loop short.
    * ``str`` → dry iff blank after ``strip()``. A whitespace-only summary of what is new
      says nothing is new.
    * ``bytes`` / ``bytearray`` → dry iff empty.
    * ``list`` / ``tuple`` / ``set`` / ``frozenset`` / ``dict`` → dry iff empty. Nothing
      collected.
    * any other type → **unreadable**. There is no rule for it, so this refuses to call it
      dry and hands the decision back to the whole-output fallback. Not swallowed as
      "progress": the caller can tell "I read the field and it said nothing" from "I could
      not read the field", and only the first may end a loop.
    """
    if value is None:
        return _DRY
    if isinstance(value, bool):
        return _PROGRESS if value else _DRY
    if isinstance(value, (int, float)):
        return _DRY if value == 0 else _PROGRESS
    if isinstance(value, str):
        return _DRY if not value.strip() else _PROGRESS
    if isinstance(value, (bytes, bytearray)):
        return _DRY if len(value) == 0 else _PROGRESS
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return _DRY if len(value) == 0 else _PROGRESS
    return _UNREADABLE
