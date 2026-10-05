"""Running one node's dispatcher under the node's own knobs.

`execute` is the task `RunController._launch` schedules for a node: the retry correction and the
carried iteration context go onto a COPY of the node, the write-scope snapshot is taken for nodes
that opted in, the dispatcher runs under `timeout_total` — a real kill — and the result is
narrowed by the node's declared `success_when`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import conditions, effect_boundary, iteration_context
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import supervisor_policy
from personalclaw.workflows.bindings import BindingContext, BindingError
from personalclaw.workflows.engine import NodeResult, dispatch
from personalclaw.workflows.engine_support import DEFAULT_MODEL_TIERS, resolve_axis_model
from personalclaw.workflows.judge_contract import hints_from_dict as judge_hints_from_dict
from personalclaw.workflows.models import SUCCESS_STATES, Failure, FailureClass, InstanceState, Node
from personalclaw.workflows.resilience import retry_prompt
from personalclaw.workflows.scope import ScopeMode
from personalclaw.workflows.scope import allowed_write_paths as scope_allowed
from personalclaw.workflows.scope import diff as scope_diff
from personalclaw.workflows.scope import enforces_scope, scope_mode
from personalclaw.workflows.scope import snapshot as scope_snapshot
from personalclaw.workflows.scope import watch_roots as scope_watch_roots
from personalclaw.workflows.tick import ReadyNode

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


#: How much of a node's total budget is held back from the DISPATCHER's own internal wait, so the
#: dispatcher — not the outer kill in `execute` — is what times out first.
#:
#: The margin is the whole point, and it is why this is not simply `timeout=total`. `execute`
#: wraps every dispatch in `asyncio.wait_for(coro, timeout=total)`; a dispatcher handed that same
#: `total` for its own bounded wait always loses the race, because its timer is armed strictly
#: later. Losing it costs the honest outcome: `dispatch_subworkflow`'s wait expiring returns
#: DEGRADED carrying `child_run_id` and "subworkflow is still running" (the child is alive and
#: findable), while the outer kill returns a bare FAILED/TIMEOUT that names no child at all. Five
#: seconds is enough for the wrap-up that follows a dispatcher's wait — collecting the child's
#: outputs and assembling the payload — and negligible against the 900s default.
_DISPATCH_WAIT_RESERVE_SECS = 5


def _with_retry_hint(ctl: RunController, item: ReadyNode) -> Node:
    """On a retry, hand the dispatcher a node whose prompt carries the correction.

    Returns the node UNCHANGED on a first attempt and for kinds with no prompt, so
    the common path pays nothing. A copy is returned rather than mutating the spec
    node: the spec is shared across every instance of a `foreach` body, and editing it
    in place would leak one item's failure into every sibling's prompt.
    """
    attempts = ctl._attempts.get(item.path)
    if not attempts:
        return item.node
    prompt = (item.node.config or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return item.node
    import copy

    node = copy.deepcopy(item.node)
    node.config["prompt"] = retry_prompt(prompt, attempts)
    return node


def _worker_model(ctl: RunController) -> str:
    """The concrete model this run's WORKERS resolve to — the family a `cross_model`
    judge gate must avoid.

    A stage spawn records no model synchronously (it returns RUNNING with a
    subagent id and the model is chosen inside the async turn), so the honest,
    available source is the SAME resolution a worker stage performs: its
    `model_tier` maps to a use case (the default `standard` tier → the
    `orchestration` axis that model-less subagent spawns run under), and the
    engine resolves that axis to the head of its active-selection chain. That is
    exactly the model a worker WILL run on, which is what the judge must differ
    from. Memoized: the active selection does not change mid-run, and the family
    is all the check needs.
    """
    if ctl._worker_model_cache is None:
        worker_uc = ctl.services.model_tiers.get("standard", DEFAULT_MODEL_TIERS["standard"])
        ctl._worker_model_cache = resolve_axis_model(worker_uc)
    return ctl._worker_model_cache


async def execute(ctl: RunController, item: ReadyNode, ctx: BindingContext) -> NodeResult:
    """Run a dispatcher under the total-timeout knob.

    A timeout here is a REAL kill, not a decorative config value — the studied
    cautionary case is an engine that shipped a no-op node timeout nobody noticed,
    because timeouts only ever execute under failure.
    """
    total = ctl.services.node_timeout_total
    # The budget a dispatcher's OWN bounded wait gets (#381). Without it `dispatch` fell back
    # to its signature default of 60s, so a `subworkflow` node stopped waiting for its child
    # after exactly a minute no matter what the run's timeout was — the parent recorded
    # "subworkflow is still running" with empty outputs (breaking every downstream binding) for
    # work the child then finished. No configuration reached that 60: `node_timeout_total` is
    # the only knob, and it was applied to the outer kill below and nowhere else, so the node's
    # real budget and the wait it was supposed to bound never met. `0` means "no ceiling" on
    # both sides, matching the `else` branch below and `wait_for_terminal`'s reading of a zero.
    dispatch_wait = max(1, total - _DISPATCH_WAIT_RESERVE_SECS) if total and total > 0 else 0
    node = _with_retry_hint(ctl, item)
    # The carried context goes on AFTER the retry hint, so a retried fresh iteration gets both
    # the correction and the handoff. Order between them does not matter — they are appended to
    # different ends — but dropping either on a retry would be a real loss.
    node = iteration_context.with_carried_context(ctl, node, item)
    # Write-scope snapshot BEFORE the node runs. Only for nodes that opted
    # in: the walk is real work, and a fan-out of fast transforms must not each pay
    # for a tree scan.
    allowed = scope_allowed(node.config or {}, ctl.services.cwd)
    # The WATCHED set is wider than the ALLOWED set by necessity: an escape lands
    # outside what is allowed, so snapshotting only the allowed paths would make a
    # violation undetectable by construction.
    watched = scope_watch_roots(node.config or {}, ctl.services.cwd)
    before = scope_snapshot(watched) if enforces_scope(node.config or {}) else None
    coro = dispatch(
        node,
        # Through the clock seam, not `time.time()`: a `wait` computes its deadline
        # against this `now`, and `_wake_due_nodes` later resolves that deadline against the
        # same seam — the two clock reads that decide a parked node's fate must come from ONE
        # clock, or a replay's recorded `now` would set a deadline the recorded wake never
        # crosses. Every non-wait dispatcher ignores `now`.
        ctx,
        now=ctl._clock(),
        subagents=ctl.services.subagents,
        depth=ctl.depth,
        run_id=ctl.run.id,
        # The owning project, for an ACTION node's provenance:
        # `knowledge-persist` files its item under this container. Passed from the run
        # record rather than resolved provider-side so one seam owns the attribution.
        project_id=ctl.run.project_id,
        # This instance's engine key, for an ACTION node's ledger provenance. The controller is
        # the only layer that knows it, and a provider must stamp it rather than its node id:
        # `inspect_node` slices the run ledger by `instance_path`, so a row carrying a bare node
        # id is durably written and invisible in the runs surface. Same `item.path` the stall
        # clock below is bound to, so a row and its progress notes agree on which instance ran.
        instance_path=item.path,
        # The effect's identity, for an ACTION to hand its receiver: the key the ledger's
        # ATTEMPTED record carries, so a retry is recognisable as the attempt it retries.
        idempotency_key=effect_boundary.effect_key_for(ctl, item.path, ctl._instance(item.path)),
        cwd=ctl.services.cwd,
        tiers=ctl.services.model_tiers,
        completion=ctl.services.completion,
        get_provider=ctl.services.get_provider,
        verify=ctl.services.verify,
        # The node's OWN budget for a bounded internal wait, minus the reserve above (#381).
        # Read by the two dispatchers that wait on something: `subworkflow`'s child-run wait and
        # `action`'s provider call. Both previously got the signature default of 60s — a ceiling
        # no setting could move, and one the `subworkflow` docstring explicitly disclaims ("the
        # wait is bounded by the node's timeout").
        timeout=dispatch_wait,
        # For `subworkflow`: the child must be driven by the SAME supervisor that will adopt
        # it after a restart, so it is passed through rather than resolved from a global.
        supervisor=ctl.services.supervisor,
        # Feeds the STALL clock. Bound to this instance's path so a dispatcher cannot
        # accidentally refresh a sibling's clock — and passed at all because without it
        # `timeout_stall` fires on any node slower than the window, which makes the two timeout
        # knobs one knob and kills a node that is visibly working.
        on_progress=lambda path=item.path: ctl.note_progress(path),
        # The worker model a `cross_model` judge gate must differ from. Resolved
        # from the run's worker axis; only the JUDGE branch reads it, so a run with no
        # cross_model gate pays nothing.
        worker_model=_worker_model(ctl),
        # The spec's `runtime_hints.judge`, parsed by the contract's own lenient parser
        # — the same split `execution_hints.from_runtime_hints` does for the
        # execution half. Parsed per dispatch rather than cached: it is a dict walk over a
        # handful of keys, and caching it would have to be invalidated by a live mutation of
        # the spec, which is a correctness risk in exchange for nothing measurable.
        judge_hints=judge_hints_from_dict(
            (ctl.spec.get("runtime_hints") or {}).get("judge")
            if isinstance(ctl.spec.get("runtime_hints"), dict)
            else None
        ),
        # This node's compaction history. `setdefault` so the list IDENTITY is stable
        # across iterations — the ladder appends to it in place, and handing out a fresh copy
        # each call would record saves nobody ever reads, leaving the anti-thrashing rule
        # permanently looking at an empty history.
        compaction_saves=ctl._compaction_saves.setdefault(node.id, []),
        # The user's explicit unattended grant, read off the run's overlay at EVERY dispatch
        # rather than cached: the overlay is the run row's, and the row is what a restart
        # re-reads. A stage consults it (`dispatch_stage`), and a gate: it waits the owner's
        # approval window unless nobody is there to answer (`human_input.gate_timeout_secs`).
        unattended=supervisor_policy.unattended_grant(ctl.run.policy_overrides),
        # A person's answer to this step's last park (`gate_answers.settle_parked_step`), POPPED:
        # it belongs to the one dispatch the answer started, so a later retry of the same step
        # cannot claim an answer nobody gave it.
        answer=ctl._park_answers.pop(item.path, None),
        # Her Allow of this attempt's start, when a restart or a pause cut the attempt off: the
        # stage starts on it if it asks the same thing (`engine.dispatch_stage`).
        approved_start=(
            ctl._instance(item.path).approved_request,
            ctl._instance(item.path).approved_at,
        ),
    )
    if total and total > 0:
        try:
            result = await asyncio.wait_for(coro, timeout=total)
        except asyncio.TimeoutError:
            return NodeResult(
                state=InstanceState.FAILED,
                failure=Failure(
                    failure_class=FailureClass.TIMEOUT,
                    cause_plain=f"node exceeded timeout_total ({total}s)",
                    remediation="raise workflows.default_node_timeout_total_secs, or "
                    "split this node into smaller steps",
                    recoverable=True,
                ),
            )
    else:
        result = await coro
    if before is not None:
        result = _check_write_scope(ctl, node, result, before, allowed, watched)
    return _check_success_when(ctl, node, result, ctx)


def _check_success_when(
    ctl: RunController, node: Node, result: NodeResult, ctx: BindingContext
) -> NodeResult:
    """Apply a node's declared `success_when` predicate.

    **It can only NARROW success, never widen it.** A node that already failed stays
    failed — otherwise `success_when` would be a way to bless a broken node, and the
    first template to discover that would use it as one.

    The use it exists for is INVERTED semantics: `code-project`'s reproduction stage
    must not count as done because it ran. Reproducing the bug (or documenting why it is
    infeasible) IS the success condition, and a stage that quietly failed to reproduce
    and moved on to editing is the exact "no repro, straight to a fix" pattern this
    guards against.

    Evaluated against the node's OWN output, bound as `output.*`. Written WITHOUT `{{}}`
    braces on purpose: `resolve_config` resolves every braced binding in a config
    *before* the node runs, and at that moment `output` does not exist yet — a braced
    form would fail the node with a binding error instead of testing it.
    """
    raw = (node.config or {}).get("success_when")
    expr = str(raw or "").strip()
    if not expr:
        return result
    if result.state not in SUCCESS_STATES:
        return result

    probe = replace(ctx, self_output=result.output, has_self_output=True)
    try:
        met = conditions.evaluate(expr, probe)
    except BindingError as exc:
        # An unevaluable predicate is a FAILURE, not a pass: "I could not tell whether
        # this succeeded" must never read as "it succeeded".
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"success_when could not be evaluated: {exc}",
                remediation=(
                    "reference a field the node's schema actually produces, e.g. "
                    "`output.some_flag`"
                ),
            ),
        )
    if met:
        return result
    return NodeResult(
        state=InstanceState.FAILED,
        output=result.output,
        degraded_reason=result.degraded_reason,
        failure=Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=f"the node ran but its success condition is false: {expr}",
            remediation=(
                "the node did not achieve what it was declared to achieve — read its "
                "output and either satisfy the condition or change the declaration"
            ),
        ),
    )


def _check_write_scope(
    ctl: RunController,
    node: Node,
    result: NodeResult,
    before: Any,
    allowed: list[str],
    watched: list[str],
) -> NodeResult:
    """Diff the tree and flag writes that escaped the declared scope.

    DETECTIVE, not preventive: a real sandbox is the OS-seatbelt layer this leaves
    room for. `warn` records and continues; `reject` flips the node to
    `scope_violation` so the escape cannot pass as a clean success.
    """
    report = scope_diff(before, scope_snapshot(watched), allowed)
    if report.clean:
        return result
    mode = scope_mode(node.config or {})
    ctl.journal.write(
        journal_mod.STEP_SCOPE,
        instance_path="",
        node_id=node.id,
        mode=mode,
        **report.to_dict(),
    )
    logger.warning(
        "workflow %s node %s wrote outside its declared scope: %s",
        ctl.run.id,
        node.id or "?",
        ", ".join(report.violations[:5]),
    )
    if mode != ScopeMode.REJECT:
        # Recorded, outcome preserved — a warn-only default on an existing template is
        # the difference between a useful signal and a broken run.
        return result
    return NodeResult(
        state=InstanceState.SCOPE_VIOLATION,
        output=result.output,
        failure=Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=(
                "node wrote outside its allowed_write_paths: " + ", ".join(report.violations[:5])
            ),
            remediation=(
                "add the path to the node's `allowed_write_paths`, or fix the node so "
                "it writes inside the run workspace"
            ),
            terminal_reason="scope_violation",
        ),
    )
