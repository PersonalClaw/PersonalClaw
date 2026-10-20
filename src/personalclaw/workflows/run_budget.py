"""What a run is held to, and what it has spent against it.

A run's caps are its `RunBudget` (``WorkflowRun.budget``): tokens and dollars, ``0`` for no cap.
A definition declares them as ``defaults.budget`` and a run of it starts with them
(:func:`declared`). A run that another run starts, a subworkflow's or a `run-workflow` step's,
starts held inside what that run has left in each dimension, and so does a run an automation's
action starts, inside what its per-run cap has left (:func:`for_run`): a nested budget can narrow
the one it runs within and never replace it. A fork continues its parent and keeps its
caps (`checkpoints.fork_run`).

What a run has spent is what its step rows booked (`step_usage.charge`, kept on the run's row)
and what the runs its steps started and left running booked (:func:`spent`), recursively. A
subworkflow's run is the work of the step that waits for it, and is booked on that step's row
(:func:`child_usage`), so it is counted once.

The caps are soft: the run is held to them between steps (`resilience.check_budget`), so the
steps already running when a cap is reached finish and are booked, and runs started side by side
can together pass a cap by what each was allowed, as parallel steps can.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import store
from personalclaw.workflows.models import RunBudget, RunStatus, WorkflowRun
from personalclaw.workflows.resilience import RunSpend, check_budget
from personalclaw.workflows.step_usage import StepUsage

if TYPE_CHECKING:
    from personalclaw.approval_answer import Principal

#: How deep :func:`spent` follows the runs a run's steps started. A run tree is shallow (a
#: subworkflow nests at most three deep, `engine.MAX_SUBWORKFLOW_DEPTH`), and a cycle in the
#: parent links of a damaged store must not recurse without end.
MAX_DEPTH = 8

#: The caps a budget holds, by the names a definition declares them and a resume raises them by.
CAPS: tuple[str, ...] = ("max_tokens", "max_cost")


def cap_problem(key: str, value: Any) -> str:
    """Why *value* cannot be the cap *key* (one of :data:`CAPS`), or ``""`` when it can: a cap is
    a number of 0 or more, ``0`` for none, and a token cap a whole one."""
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None or not math.isfinite(number) or number < 0:
        return f"{key} must be a number of 0 or more (0 is no cap)"
    if key == "max_tokens" and number != int(number):
        return "max_tokens must be a whole number of tokens"
    return ""


def unreadable(raw: Any) -> str:
    """Why *raw*, a definition's ``defaults.budget``, holds caps no run can be held to, or ``""``
    when a run can: an object whose caps (:data:`CAPS`) are each a number :func:`cap_problem`
    takes. A key that names no cap holds nothing, and is no reason here."""
    if raw is None:
        return ""
    if not isinstance(raw, dict):
        return "budget must be an object of max_tokens and max_cost"
    for key in CAPS:
        if key in raw and (why := cap_problem(key, raw[key])):
            return f"budget {why}"
    return ""


def declared(spec: dict[str, Any] | None, *, over: RunBudget | None = None) -> RunBudget:
    """The caps a definition declares for its runs (``defaults.budget``), or none, with each cap
    *over* sets (a start's own, such as a loop's dollar limit) in place of the declared one.

    Raises ValueError, saying why, for caps no run can be held to (:func:`unreadable`): read as
    none, a cap its author set would hold nothing and say nothing. A saved definition never has
    them (`validator`); one a definition provider hands over unchecked can."""
    defaults = spec.get("defaults") if isinstance(spec, dict) else None
    raw = defaults.get("budget") if isinstance(defaults, dict) else None
    if why := unreadable(raw):
        raise ValueError(f"its definition's {why}")
    caps = RunBudget.from_dict(raw or {})
    if over is None:
        return caps
    return RunBudget(
        max_tokens=over.max_tokens or caps.max_tokens, max_cost=over.max_cost or caps.max_cost
    )


#: What to change when a definition's caps cannot be read, as a step that would start its run says.
UNREADABLE_FIX = "set each cap in its `defaults.budget` to a number of 0 or more, 0 for none"


def child_caps(spec: dict[str, Any] | None, *, parent: WorkflowRun | None) -> tuple[RunBudget, str]:
    """The caps a run another run's step starts is held to (:func:`for_run` of its definition's,
    inside what *parent* has left), and ``""``; or none and why its definition's caps cannot be
    read, which its step fails with rather than start a run that holds them as none."""
    try:
        return for_run(declared(spec), parent=parent), ""
    except ValueError as exc:
        return RunBudget(), str(exc)


def spent(run: WorkflowRun, *, depth: int = 0) -> RunSpend:
    """What *run* has booked against its caps: its own step rows, and what the runs its steps
    started and left running booked.

    ``unpriced`` counts the steps no price covered in all of them, since the dollar figure leaves
    each out. Only *run*'s own are its owner's to let it go on past: a run it started is held to
    its own (`for_run` gave it a dollar cap whenever *run* has one), so its unknown steps pause
    it rather than the run that started it, and count here as already answered for."""
    tokens = int(run.total_tokens)
    dollars = float(run.total_cost_usd)
    unpriced = int(run.unpriced_steps)
    allowed = min(int(run.unpriced_allowed), unpriced)
    if depth < MAX_DEPTH:
        for child in store.started_runs(run.id):
            theirs = spent(child, depth=depth + 1)
            tokens += theirs.tokens
            dollars += theirs.dollars
            unpriced += theirs.unpriced
            allowed += theirs.unpriced
    return RunSpend(
        tokens=tokens, dollars=round(dollars, 6), unpriced=unpriced, unpriced_allowed=allowed
    )


def for_run(own: RunBudget, *, parent: WorkflowRun | None = None) -> RunBudget:
    """The caps a run starts held to: its own (*own*, its definition's), inside what the run that
    started it (*parent*) has left, and inside what the run scope it was started within has left
    (an automation's per-run dollar cap, ``guardrails.budgets.held_within``). The run outlives
    that scope, so what is left of it becomes the run's own cap, held where the run books its
    steps."""
    held = own
    if parent is not None and not parent.budget.is_unlimited:
        so_far = spent(parent)
        held = held.within(parent.budget.left_after(so_far.tokens, so_far.dollars))
    return held.within(_left_in_scope())


def _left_in_scope() -> RunBudget:
    """What the run scope bound where a run starts has left, as a run's caps: none when no scope
    is bound, or its ceiling sets none."""
    from personalclaw.guardrails.budgets import current_run_budget, current_run_key, get_meter

    key = current_run_key()
    ceiling = current_run_budget()
    if not key or ceiling.is_unlimited:
        return RunBudget()
    used = get_meter().run_totals(key)
    return RunBudget(max_tokens=ceiling.max_tokens, max_cost=ceiling.max_dollars).left_after(
        used.tokens, used.dollars
    )


def child_usage(usage: StepUsage, output: Any) -> StepUsage:
    """What a subworkflow step used: what its run booked, which the step's own call log sees only
    part of (a stage's subagent runs outside it). *usage* as measured when the step started no
    run. When no price covered some of what that run spent, the step's cost is unknown, as any
    step's is whose calls had no price: never the part that was priced, read as all of it."""
    child_id = str(output.get("child_run_id", "") or "") if isinstance(output, dict) else ""
    child = store.get(child_id) if child_id else None
    if child is None:
        return usage
    theirs = spent(child)
    return replace(
        usage, tokens=theirs.tokens, cost_usd=None if theirs.unpriced else theirs.dollars
    )


def shown(run: WorkflowRun) -> dict[str, Any]:
    """A run's caps and what it has spent against them (:func:`spent`), as its page shows them:
    its caps (``0`` = none), what its steps and the runs they started booked, how many of its
    steps no price covered (the dollar figure is then a floor), and whether it is paused at a
    cap, which its owner lifts by raising it as she resumes (:func:`resume_refusal`)."""
    so_far = spent(run)
    return {
        "budget": run.budget.to_dict(),
        "spend": {
            "tokens": so_far.tokens,
            "dollars": so_far.dollars,
            "unpriced_steps": so_far.unpriced,
        },
        "at_budget": run.status == RunStatus.PAUSED and check_budget(run.budget, so_far).over,
    }


def caps_from(raw: Any, current: RunBudget) -> tuple[RunBudget | None, str]:
    """The caps a resume asks for (:data:`CAPS`), over *current* for any it leaves out, or
    ``None`` and why it cannot be read. Strict, as a write is: a cap that is not a number, or is
    negative, or a key that names no cap, is refused rather than read as none."""
    if not isinstance(raw, dict):
        return None, "budget must be an object of max_tokens and max_cost"
    unknown = sorted(set(raw) - set(CAPS))
    if unknown:
        return None, f"budget names no cap called {', '.join(unknown)}: use max_tokens, max_cost"
    if why := unreadable(raw):
        return None, why
    return RunBudget.from_dict({**current.to_dict(), **raw}), ""


def resume_refusal(run: WorkflowRun, *, by: Principal, budget: Any) -> dict[str, Any] | None:
    """Whether a cleared pause can go on within the run's caps: ``None`` when it can, with the
    caps it goes on with recorded for its next step (`store.request_budget`), else the service
    result that refuses it.

    The caps are *budget*'s, over the run's own (:func:`caps_from`). Changing them, or going on
    past a step its dollar cap could not count, is its owner's decision alone, refused and audited
    for anyone else (`approval_answer.check`), as an answer to its gate is: a run's agent cannot
    lift its own run's cap. Her resume lets it go on past every such step so far, which the run
    then leaves out of its dollar figure and says so. A run that would still be at a cap is
    refused with what it spent, and stays paused."""
    from personalclaw import approval_answer

    caps = run.budget
    if budget is not None:
        parsed, problem = caps_from(budget, run.budget)
        if parsed is None:
            return {"ok": False, "code": "WF_RUN_BUDGET_INVALID", "message": problem}
        caps = parsed
    so_far = spent(run)
    unknown = so_far.unpriced_waiting if caps.max_cost > 0 else 0
    if caps != run.budget or unknown:
        refused = approval_answer.check(
            by, what=f"budget:{run.id}", asked_by=approval_answer.run(run.id).label
        )
        if refused:
            return {"ok": False, "code": "WF_RESUME_NOT_OWNER", "message": refused}
    going_on = check_budget(caps, replace(so_far, unpriced_allowed=so_far.unpriced))
    if going_on.over:
        return {
            "ok": False,
            "code": "WF_RUN_AT_BUDGET",
            "message": f"It is at {going_on.at}. Raise that budget above what it has spent, or "
            "set it to 0 for none, to resume it.",
            "budget": caps.to_dict(),
            "spend": {"tokens": so_far.tokens, "dollars": so_far.dollars},
        }
    if caps != run.budget or unknown:
        store.request_budget(run.id, {**caps.to_dict(), "unpriced_allowed": run.unpriced_steps})
    return None
