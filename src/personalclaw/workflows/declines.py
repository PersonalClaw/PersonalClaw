"""Her Deny inside a step: what the step keeps, what its loop does next, and what the run says.

A `stage`'s subagent asks before a call it may not make on its own, and its owner can answer Deny.
That answer is hers for the work: the subagent goes on (or ends) without the call, and what its
calls came to carries what she declined (`SubagentInfo.declined_calls`, `subagent_tier`). This
module carries it from there.

**The step keeps it.** A settled stage records the calls she declined in its attempt
(`NodeInstance.declined`, :func:`keep`). Its row on the run page says so under it (:func:`caption`),
and the page lists every such step under "Declined by you" (:func:`listed`), after the run has
ended too (while its loop waits, the cycle it waits on is said by the line that says why).

**Its loop does not ask her again by itself.** A loop's next cycle runs the same step again, with
the judge's critique of this one, and the critique of work she declined is to do it: the same call
put to her again. So a cycle she declined something in ends there, honestly (:func:`end_cycle`):
the steps left in it are not run ("not run: you declined write_file (notes/plan.md) in “work”"),
the cycle goes on the run's ledger as ``declined`` with the sentence that says so ("Cycle 2 ended
at “work”: you declined write_file (notes/plan.md)."), neither the loop's dry streak nor its
breaker reads it, and the loop waits for her: the run pauses, its page and one Inbox item saying
so, until she tells it what to do instead, resumes it or stops it (:func:`waiting_words`). What she
steers it with while it waits reaches the cycle her Resume runs (:func:`carry_on`). A loop with no
cycle left after that one does not wait: a counted loop has run its count, and a loop at its cycle
budget stops at it, saying the last cycle ended at her Deny.

**What it is not.** A Deny on a step's START declines the step and ends the run there
(`gate_answers.decline`). An ask nobody answered, or one with nowhere to put to her, is not hers.
A step outside a loop goes on as it would: nothing runs it again to ask her the same thing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.declined_calls import named, said, waits_for_you
from personalclaw.workflows import (
    attention,
    ending_sentence,
    iteration_context,
    loop_convergence,
    store,
)
from personalclaw.workflows.models import (
    LoopMode,
    Node,
    instance_order,
    loop_parent,
    spec_path,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)

#: What a run that waits for its owner after her Deny keeps on its row (`run.extra`) while it
#: waits: the loop, the cycle that ended at her Deny, and the sentence that says so.
WAIT_KEY = "declined_wait"


def keep(ctl: RunController, path: str, inst: Any, info: Any) -> None:
    """Keep on the settled stage *inst*, at *path*, the calls its owner declined in its attempt
    (*info*'s ``declined_calls``), and, inside a loop's cycle, end the cycle there: every step
    after it in the cycle is not run, saying why."""
    steps = [dict(s) for s in getattr(info, "declined_calls", None) or [] if isinstance(s, dict)]
    inst.declined = steps
    loop_path, iteration = loop_parent(path)
    if not steps or loop_path is None:
        return
    nodes = dict(walk(ctl.root))
    cycle = f"{loop_path}.body@{iteration}."
    reason = f"not run: you declined {said(steps)} in {_named(nodes, path, inst)}"
    for later in ending_sentence.followers(ctl, path, nodes):
        if later.startswith(cycle):
            ctl._skip(later, reason=reason)


def end_cycle(ctl: RunController, loop_path: str, node: Node, iteration: int, output: Any) -> bool:
    """End cycle *iteration* of the loop *node* at *loop_path*, whose body has just finished,
    when its owner declined a call in it; whether it did (its iteration boundary reads nothing
    else of it then). *node* is the loop as the run bounds it (its own ``max_cycles``).

    The cycle's `iteration` row carries what she declined and the sentence (``declined``,
    ``detail``), and its outcome is how the loop goes on from it: ``declined`` when it waits for
    her, else the word any loop ends with there (:func:`_no_cycle_left`). The handoff it wrote is
    kept, as at any boundary. Then the loop waits for her: its counter moves on to the next
    cycle, the run asks for its pause (applied before the next cycle can start,
    `RunController._step`) and says why (:func:`waiting_words`), and one Inbox item says so.
    Steering queued on the run stays queued in its folder until her Resume takes it into that
    cycle (:func:`carry_on`), so neither a restart while it waits nor the order she steered in
    loses any. A counted loop that has run its count is done instead, and one at its cycle budget
    stops at it, saying so: each takes its steering as any loop that ends does, and its counter
    stays on the cycle it ended on."""
    found = _declined_in(ctl, loop_path, iteration)
    if not found:
        return False
    nodes = dict(walk(ctl.root))
    first = found[0][0]
    steps = [step for _path, kept in found for step in kept]
    sentence = ended_at(_named(nodes, first, ctl.instances.get(first)), iteration, steps)
    left = _no_cycle_left(node, iteration)
    if left:
        iteration_context.consume_steering(ctl, loop_path, node, iteration)
    ctl.journal.iteration(
        loop_path,
        node.id,
        iteration=iteration,
        outcome=_no_cycle_left(node, iteration) or iteration_context.DECLINED_CYCLE,
        detail=sentence,
        declined=list(dict.fromkeys(named(step) for step in steps)),
    )
    iteration_context.capture_iteration_context(ctl, loop_path, node, iteration, output)
    if left == "max_iterations":
        loop_convergence.surface_loop(
            ctl, loop_path, node, reason=left, detail=f"{_spent(node, iteration)} {sentence}"
        )
        return True
    if left:
        # A counted loop that has run its count: done, as it would have been after any last cycle.
        loop_convergence.finish_loop(ctl, loop_path, node, iteration)
        return True
    ctl._iterations[loop_path] = iteration + 1
    cycle = iteration + 1
    ctl.run.extra[WAIT_KEY] = {"loop": loop_path, "cycle": cycle, "said": sentence}
    store.request_pause(ctl.run.id)
    attention.raise_declined_wait(ctl.services.attention_state, ctl.run, cycle=cycle, said=sentence)
    logger.info("workflow %s waits for its owner after her Deny: %s", ctl.run.id, sentence)
    return True


def waiting_words(ctl: RunController) -> str:
    """What a run paused while its loop waits for its owner after her Deny says: the sentence its
    cycle ended with, and what she can do; "" for any other pause."""
    wait = ctl.run.extra.get(WAIT_KEY)
    if not isinstance(wait, dict):
        return ""
    noun, stop = ("loop", "stop") if ctl.run.loop_kind else ("run", "cancel")
    return f"{wait.get('said') or ''} {waits_for_you(noun, stop)}".strip()


def waits(run: Any) -> bool:
    """Whether *run* is paused because its loop waits for its owner after her Deny."""
    return isinstance((getattr(run, "extra", None) or {}).get(WAIT_KEY), dict)


def carry_on(ctl: RunController) -> None:
    """Her Resume of a run whose loop waited for her after her Deny: the steering queued on it,
    before the wait and during it, is taken at the boundary it waited at, so it reaches the cycle
    her Resume runs; the wait's Inbox item closes as handled, and the run stops saying it waits. A
    no-op for any other start."""
    wait = ctl.run.extra.pop(WAIT_KEY, None)
    if not isinstance(wait, dict):
        return
    loop_path = str(wait.get("loop") or "")
    try:
        cycle = int(wait.get("cycle") or 0)
    except (TypeError, ValueError):
        cycle = 0
    node = dict(walk(ctl.root)).get(spec_path(loop_path)) if loop_path else None
    if node is not None:
        iteration_context.consume_steering(ctl, loop_path, node, max(0, cycle - 1))
    attention.resolve_declined_wait(ctl.services.attention_state, ctl.run.id, cycle=cycle)
    ctl.run.error_message = ""


def ended_at(name: str, iteration: int, steps: list[Any]) -> str:
    """The sentence a loop's cycle ends with at its owner's Deny: ``Cycle 2 ended at “work”: you
    declined write_file (notes/plan.md).``"""
    return f"Cycle {iteration + 1} ended at {name}: you declined {said(steps)}."


def caption(steps: list[Any]) -> str:
    """What a step's row says under it when its owner declined calls of its attempt, or ""."""
    return f"You declined {said(steps)}." if steps else ""


def listed(run_id: str) -> list[str]:
    """What its owner declined in the run, step by step in the order they ran, as its page lists
    it under "Declined by you": a loop's cycle as it ended (:func:`ended_at`), any other step by
    name ("You declined write_file (notes/plan.md) in “draft”.")."""
    spec = store.read_spec(run_id) or {}
    try:
        nodes = dict(walk(Node.from_dict(spec.get("root") or {})))
    except ValueError:
        return []
    instances = store.read_state(run_id)
    out: list[str] = []
    for path in sorted(instances, key=instance_order):
        inst = instances[path]
        if not inst.declined:
            continue
        name = _named(nodes, path, inst)
        loop_path, iteration = loop_parent(path)
        if loop_path is not None:
            out.append(ended_at(name, iteration, inst.declined))
        else:
            out.append(f"You declined {said(inst.declined)} in {name}.")
    return out


def _declined_in(ctl: RunController, loop_path: str, iteration: int) -> list[tuple[str, list]]:
    """The steps of the loop's cycle whose owner declined calls in them, in the order they run,
    each with what. A step of a loop nested in the cycle is that loop's to answer for."""
    base = f"{loop_path}.body@{iteration}"
    return sorted(
        (
            (path, list(inst.declined))
            for path, inst in ctl.instances.items()
            if inst.declined
            and (path == base or path.startswith(f"{base}."))
            and loop_parent(path)[0] == loop_path
        ),
        key=lambda found: instance_order(found[0]),
    )


def _no_cycle_left(node: Node, iteration: int) -> str:
    """Why the loop runs no cycle after *iteration* by its count or its budget alone, as the word
    any loop ends with there (``counted_complete``, ``max_iterations``), or "".

    Only those two: the rest of a loop's exit test reads what its cycles produced, and a cycle she
    declined something in is no evidence either way."""
    cfg = node.config or {}
    try:
        mode = LoopMode(str(cfg.get("mode", "counted") or "counted"))
    except ValueError:
        mode = LoopMode.COUNTED
    if mode == LoopMode.COUNTED:
        n = cfg.get("n")
        if iteration + 1 >= (n if isinstance(n, int) and n > 0 else 1):
            return "counted_complete"
    cap = cfg.get("max_iterations")
    if isinstance(cap, int) and cap > 0 and iteration + 1 >= cap:
        return "max_iterations"
    return ""


def _spent(node: Node, iteration: int) -> str:
    cap = (node.config or {}).get("max_iterations")
    count = cap if isinstance(cap, int) and cap > 0 else iteration + 1
    return f"It used its budget of {count} cycle{'' if count == 1 else 's'}."


def _named(nodes: dict[str, Node], path: str, inst: Any) -> str:
    return ending_sentence.step_name(nodes.get(spec_path(path)), inst, path)
