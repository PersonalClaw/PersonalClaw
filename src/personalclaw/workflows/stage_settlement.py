"""Settling `stage` nodes, whose work runs in a spawned subagent rather than an awaited task.

`dispatch_stage` spawns and returns RUNNING at once, so a stage never settles through
`RunController._apply`: the controller keeps the subagent's id on the instance and asks
`SubagentManager.get` for its verdict on every step (`reconcile_dispatched_stages`). This module is
everything that treats that out-of-band work — the ONE predicate for which instances are behind a
polled worker, the settle itself, re-queueing a stage whose subagent ended with an earlier gateway
process, and stopping them when the run is cancelled or paused.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import effect_boundary, loop_iteration
from personalclaw.workflows.engine import (
    NodeResult,
    apply_judge_contract,
    apply_schema_notice,
    parse_json_loose,
    release_execution_claim,
)
from personalclaw.workflows.judge_contract import hints_from_dict as judge_hints_from_dict
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    Node,
    now_stamp,
    spec_path,
    walk,
)
from personalclaw.workflows.step_usage import subagent_usage

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


def awaiting_out_of_band_work(ctl: RunController) -> list[str]:
    """Paths whose node is RUNNING behind a worker this controller must POLL, not await.

    ONE predicate, two consumers: `reconcile_dispatched_stages` asks the manager for each
    one's verdict, and `_dispatched_poll_delay` is why the tick loop wakes up to ask at all.
    Re-deriving the condition at the second site is how the two drift, and the drift is not
    cosmetic: a node polled by a reconciler the loop does not know to wake for gets polled at
    `asyncio.sleep(0)` frequency — measured at 1224-1859 `SubagentManager.get` lookups per
    second and 65-68% of one core for a SINGLE otherwise-idle run. So the delay is derived
    from the same set the reconciler walks.

    The membership test is `RUNNING` + a persisted foreign key, not a node-kind list. A kind
    list would be a second vocabulary to keep in step with the dispatchers (and
    `test_workflows_stage_completion` already ratchets that `dispatch_stage` is the only
    producer of RUNNING); "the instance carries a handle to work happening elsewhere" is the
    property both consumers actually need.
    """
    return [
        path
        for path, inst in ctl.instances.items()
        if inst.state == InstanceState.RUNNING and inst.subagent_id
    ]


def reconcile_dispatched_stages(ctl: RunController) -> None:
    """Settle `stage` nodes whose spawned subagent has finished.

    `dispatch_stage` spawns and returns RUNNING immediately (``engine.py:777``), so by the
    time that result is applied the awaited asyncio task is ALREADY done: `_await_progress`
    pops the entry out of `_inflight` (:2686) before `_apply` sees it, and the tick loop
    then takes the idle branch (:573) that never calls `_await_progress` again.
    `liveness.enforce_stall_timeouts` is reachable only from there (:2682), so it stopped being able
    to observe the node at all; `node_timeout_total` never could, because it bounds the
    awaited dispatcher coroutine (:2539) and for a stage that coroutine ends at the spawn.
    The result was a node that stayed RUNNING with no `step_completed` after its subagent
    had reported `done: True` — which hung every template containing a `stage`.

    **`_inflight` is deliberately NOT the home for a dispatched stage.** Every consumer of
    that dict assumes `entry.task` is a live awaitable: `_await_progress` selects on it with
    FIRST_COMPLETED, and both the stall sweep and `_cancel_inflight` cancel it. Re-inserting
    an already-finished task would make `asyncio.wait` return instantly on every tick and
    re-apply the same RUNNING result forever; parking a never-resolving placeholder there
    instead would mean inventing a fake task to satisfy a signature. A spawned subagent is
    not an awaited task. What the controller legitimately owns is the ID of the work, and
    that is instance state — exactly like `wake_at`.

    **One source of truth for liveness.** "Has this subagent finished?" stays owned by
    `SubagentManager.get` (``subagent.py:1632``), the same lookup the sessions surface reads
    (``dashboard/handlers/sessions.py:388``). Nothing is registered here. That also settles
    the HUNG case without a second deadline: the manager's own reaper (``subagent.py:739``)
    force-kills a child past `_default_timeout` and records the verdict as `done=True` with
    `error="Reaped after ..."` (:791-793). That deadline already existed and was merely
    unread — so this method is a READER, not a new timeout.
    """
    manager = ctl.services.subagents
    if manager is None or not hasattr(manager, "get"):
        return
    settled = False
    remembered = False
    for path in awaiting_out_of_band_work(ctl):
        inst = ctl._instance(path)
        try:
            info = manager.get(inst.subagent_id)
        except Exception:
            # A failed lookup is not a verdict on the node. Logged and retried next tick:
            # letting a transient error in an OBSERVER kill work that is proceeding is the
            # shape where a safety control becomes an outage.
            logger.debug("run %s: subagent lookup failed for %s", ctl.run.id, path, exc_info=True)
            continue
        if info is not None and _remember_allowed_start(inst, info):
            remembered = True
        if info is None or not getattr(info, "done", False):
            # UNKNOWN or still working — no verdict either way. `None` is the post-restart
            # shape (a fresh manager knows no ids): reading it as "finished" would bless
            # work that never reported, and reading it as "failed" would invent a verdict
            # on no evidence for a run the watchdog may be mid-adoption of.
            # `audit.STALE_RUNNING` (``audit.py:39``) stays the backstop for a RUNNING node
            # nobody is driving.
            continue
        node = dict(walk(ctl.root)).get(spec_path(path))
        node_id = node.id if node else ""
        error = str(getattr(info, "error", "") or "")
        reaped = bool(getattr(info, "reaped", False))
        # 🔴 The child's USAGE, which this method used to leave on the floor: a spawned `stage`
        # returns at `_apply`'s RUNNING branch, before any usage is booked, so a template whose
        # only leaves are stages (`general-project`) charged its run NOTHING. Measured on the
        # owner's instance: run 61899886, eight `done` nodes on a remote provider,
        # `total_tokens: 0`. Charged and journaled for BOTH outcomes, as `_apply` does: a
        # reaped stage burned its whole deadline.
        usage = subagent_usage(info)
        inst.tokens = usage.billable()
        ctl.run.total_tokens += inst.tokens
        if error:
            from personalclaw.subagent_tier import couldnt_do_it

            failure = Failure(
                # The manager reaps on its OWN deadline, so a reaped child is a timeout. A child
                # whose every tool call was refused did nothing it was asked, for want of tools
                # (`subagent_tier.couldnt_do_it`): retrying it under the same tools reaches the
                # same refusals. Anything else it reports is an execution fault: filing that as
                # TIMEOUT would tell the user to raise a limit that was never the problem.
                failure_class=(
                    FailureClass.TIMEOUT
                    if reaped
                    else FailureClass.PERMISSION if couldnt_do_it(error) else FailureClass.INTERNAL
                ),
                # The manager's own sentence, verbatim — it carries the elapsed time and
                # the deadline that was crossed, which a re-worded message would drop.
                cause_plain=error,
                remediation=(
                    "the subagent was force-killed after exceeding its deadline; raise the "
                    "subagent timeout, or split this stage into smaller steps"
                    if reaped
                    else (
                        "give this step the tools its task needs (a read-only step runs only "
                        "what reads), or narrow its task to what its tools can do"
                        if couldnt_do_it(error)
                        else "check the subagent's transcript for the failing turn"
                    )
                ),
                recoverable=True,
            )
            inst.state = InstanceState.FAILED
            inst.failure = failure
            inst.completed_at = now_stamp()
            # `retries_exhausted`, and no `_should_retry` consultation, is not a shortcut:
            # neither TIMEOUT nor INTERNAL is in `RETRYABLE_CLASSES`, so the retry policy
            # would decline both anyway. Re-deriving that here would be a second copy of
            # the rule that could drift from the first.
            ctl.journal.step_failed(
                path,
                node_id,
                epoch=inst.epoch,
                failure=failure,
                usage=usage,
                attempt=inst.attempt,
                retries_exhausted=True,
            )
            # 🔴 The FAILED ATTEMPT IS OVER, so its no-double-execution claim goes back (#3533).
            # This is the only place a spawned stage settles, and the claim's 900s TTL used to
            # be its only way out — so a failed stage went on fencing its own instance, and the
            # retry met its own lease: `another worker holds the claim on this node (held by …
            # for another 899s)`, rendered DEGRADED. DEGRADED is a SUCCESS state, so the run
            # then reported COMPLETE having re-run nothing. Measured that way on a rewind of a
            # reaped stage before this existed.
            #
            # The SUCCESS branch below deliberately keeps its claim: the symptom a retained
            # DONE claim causes is a stage-bodied loop refusing its own next round, and that is
            # fixed by keying the claim per node INSTANCE (#3531) rather than by shortening the
            # window here. Releasing on both outcomes ALSO clears it — measured, the research
            # round loop goes from 1 dispatch to 8 — but it would be a second mechanism for one
            # symptom. A re-run of the same SUCCEEDED instance is a rewind, and the rewind gives
            # the settled attempt's claim back when it resets the instance
            # (`mid_flight._apply_reentry`).
            release_execution_claim(inst.claim_target, inst.claim_holder)
            inst.claim_target = ""
            inst.claim_holder = ""
        else:
            inst.state = InstanceState.DONE
            inst.completed_at = now_stamp()
            stage_result = _settled_stage_output(ctl, node, str(getattr(info, "result", "") or ""))
            output = stage_result.output
            # What the declared `schema` asked for and the subagent did not return (#3545), on
            # the instance, the row below and the event, as `_apply` does for a dispatched node.
            inst.schema_shortfall = stage_result.schema_shortfall
            ref, preview = ctl.journal.store_output(path, output)
            inst.output_ref = ref
            if node_id:
                # NOW the binding namespace gets the subagent's actual output. Until this
                # existed a downstream `{{nodes.X}}` on a stage could only ever have read
                # the placeholder the RUNNING branch left behind.
                ctl._outputs[node_id] = preview
            ctl.journal.step_completed(
                path,
                node_id,
                epoch=inst.epoch,
                # No cache key. The awaited dispatch that owned this node's key returned at
                # the spawn, so there is no key under which THIS output was derived;
                # inventing one would let a later resume serve a cache hit for a subagent
                # result this run never computed.
                cache_key="",
                state=InstanceState.DONE,
                retries=max(0, inst.attempt - 1),
                # The ledger fields the run row's charge above is derived from, so a reader
                # reconciling the two arrives at the same number. `tokens` also decides
                # `run_totals()["tokens_recorded"]`, which is what `_prepare` pre-charges a
                # capped resume from. No `provider`: the subagent does not report one.
                tokens=usage.tokens,
                model=usage.model,
                cost_usd=usage.cost_usd,
                output_ref=ref,
                schema_shortfall=inst.schema_shortfall,
            )
            if node is not None:
                effect_boundary.record_terminal_effect(ctl, node, path, inst, inst.state, output)
        # The attempt is over, and her Allow was for it: another attempt asks again.
        inst.approved_request = ""
        inst.approved_at = 0.0
        # What the stage wrote where the run cannot read it, kept in the run's own folder before
        # anyone is told the step is done — on either outcome: a failed stage's document is still
        # the document as it stands.
        _keep_what_it_wrote(ctl, inst, info, node_id or path)
        # 🔴 The ITERATION COUNTER, which this method used to leave behind. `_apply` advances
        # the loop for an awaited dispatch, and for a spawned `stage` it returns at the RUNNING
        # branch above — so a loop whose body ENDS in a stage settled here, journalled
        # `step_completed`, and then nothing moved: no `iteration` record, no second round, and
        # the tick loop reported "run deadlocked: no runnable nodes and none in flight" after
        # exactly one iteration.
        #
        # Measured with a control (the research port): a
        # `sequence[transform, loop(until_dry, streak 2, body=sequence[stage, stage])]`
        # deadlocks after one iteration with ZERO `iteration` records, while the byte-identical
        # spec with `infer` bodies runs two and ends on `dry_streak`. That is the same symptom
        # `loop_parent`'s docstring records for the container-body bug — deadlock after exactly
        # one iteration — reached by the other of the two settle paths, and it is why no shipped
        # stage-bodied loop template had ever been observed past round one.
        #
        # Safe to call unconditionally: `loop_iteration.advance_loop` no-ops unless this path really
        # is inside a loop body AND `loop_iteration._iteration_complete` says the whole body is
        # terminal, so a stage that is merely the FIRST leaf of a container body still advances
        # nothing.
        loop_iteration.advance_loop(ctl, path, node_id)
        ctl._publish(
            "workflow_node_done",
            {
                "node_id": node_id,
                "instance_path": path,
                "status": inst.state.value,
                "node_epoch": inst.epoch,
                # Only when there is something to name (#3545), as `_apply`'s event does. Empty
                # on every branch but DONE: `_launch` clears it per attempt.
                **({"schema_shortfall": inst.schema_shortfall} if inst.schema_shortfall else {}),
            },
        )
        settled = True
    if settled or remembered:
        ctl._persist_state()
    if settled:
        # The RUN ROW too, not just instance state. `service.status()` is a pure store read
        # (`store.get(run_id)`), so a `total_tokens` that lives only in this object is a number
        # no surface can see until `_finish` happens to flush it — and a run the user is
        # watching would report zero for its whole life. `_persist_state` writes instances
        # only, which is why the counter needs its own flush here.
        ctl._save_run()


def _remember_allowed_start(inst: Any, info: Any) -> bool:
    """Keep on *inst* the owner's Allow of its attempt's start; whether that changed it.

    The approval registry forgets her answer with the process. Kept here, in the state a restart
    reads back, the attempt a restart or a pause cuts off resumes on it, and she is not asked the
    same thing twice (`engine.dispatch_stage`). A start a standing grant approved carries no
    answer of hers, so nothing is kept for it, and a resumed start the answer was handed back to
    carries the same answer, so the time it counts from never moves.
    """
    request_key = str(getattr(info, "request_key", "") or "")
    approved_at = float(getattr(info, "approved_at", 0.0) or 0.0)
    if not request_key or approved_at <= 0:
        return False
    if (inst.approved_request, inst.approved_at) == (request_key, approved_at):
        return False
    inst.approved_request = request_key
    inst.approved_at = approved_at
    return True


def _keep_what_it_wrote(ctl: RunController, inst: Any, info: Any, step: str) -> None:
    """Keep the documents a settled stage wrote in a folder the run does not own.

    The folder is the one the stage's session worked in: the cwd it was spawned with, or — for a
    project-less run, which spawns with none — the workspace every session defaults to
    (`provider_bridge._native_session_cwd`, the ACP spawn's `_resolve_acp_spawn_cwd`). Measured
    from the stage's own start, so only what this stage changed is taken
    (`deliverable.keep_step_documents`).
    """
    from personalclaw.config.loader import default_workspace_dir
    from personalclaw.workflows import deliverable
    from personalclaw.workflows.models import stamp_epoch

    folder = str(getattr(info, "cwd", "") or "") or ctl.services.cwd or default_workspace_dir()
    deliverable.keep_step_documents(
        ctl.run, ctl.spec, folder=folder, since=stamp_epoch(inst.started_at), step=step
    )


def _settled_stage_output(ctl: RunController, node: Node | None, text: str) -> NodeResult:
    """A spawned stage's settled result, its output in the shape its own `config` DECLARES.

    This settle path is the ONLY place a `stage` output is produced — `dispatch_stage` returns
    RUNNING at the spawn — so every seam that reads a stage's declared shape has to be applied
    here or it is inert. Two were:

    * **The declared `schema`.** The output used to be `{"result": "<the subagent's raw
      text>"}` unconditionally, so a stage's declared keys reached no binding, no
      `progress_field` and no judge contract. Measured consequences on the shipped library:
      `general-project` declares `progress_field: meaningful_progress`,
      `loop_iteration._progress_value` never found it, `loop_iteration._iteration_is_dry` fell back
      to the whole-output rule, a non-empty `{"result": …}` is never dry — so `until_dry`
      degenerated into `max_iterations` and the run escalated with "the loop reached its iteration
      ceiling" (#3524's wrong headline). `{{last.output.summary}}` and
      `{{nodes.work.output.summary}}` could not resolve either, which is why closing #3524's `last`
      gap alone only moves the error from `unresolved reference at 'last'` to `unresolved reference
      at 'summary'`. An `infer` node in the same run has always been parsed
      (`engine.parse_json_loose` at its DONE branch) — that asymmetry between two node kinds reading
      the same templates was the whole defect.
    * **`judge_contract`.** `engine.apply_judge_contract` runs at the dispatch seam so "a node
      kind cannot skip it", and a `stage` skipped it anyway: at that seam a stage's result is
      still `RUNNING` with `{"subagent_id": …}`, which the contract declines. ALL SEVEN
      `judge_contract` nodes in the bundled library are stages, so the contract validated
      nothing, ever — the engine's recomputed `overall`, its `valid` flag and its `shortfalls`
      (which three templates bind as `{{last.output.shortfalls}}`) were never produced.
    * **The declared-schema notice (#3545).** The dispatch seam's own
      `engine.apply_schema_notice`, observing the subagent's TEXT rather than the output built
      from it. The `{"result": text}` envelope would be named as a `result` key the worker never
      wrote, and the judge contract writes every key a judge schema declares whatever the model
      said, so the settled output of a judge that answered in prose carries all of them.

    A stage that declares NO schema keeps `{"result": text}` — unstructured output is a real
    thing a stage may return, and that is its shape, not a fallback. A stage that declares one
    and returns unparseable text also keeps it: the binding then fails naming the key it wanted,
    which is what happens today, so this cannot turn a run that passes into one that fails. It
    can only ADD resolvable keys.
    """
    if node is None:
        return NodeResult(state=InstanceState.DONE, output={"result": text})
    cfg = node.config or {}
    parsed: Any = None
    if isinstance(cfg.get("schema"), dict) and cfg["schema"]:
        parsed = parse_json_loose(text)
    output: Any = parsed if isinstance(parsed, dict) else {"result": text}
    # Through the same helper the dispatch seam uses, so there is ONE definition of what a
    # validated verdict is — a second copy here would drift from the gate's.
    settled = apply_judge_contract(
        node,
        NodeResult(state=InstanceState.DONE, output=output),
        judge_hints_from_dict(
            (ctl.spec.get("runtime_hints") or {}).get("judge")
            if isinstance(ctl.spec.get("runtime_hints"), dict)
            else None
        ),
    )
    return apply_schema_notice(node, settled, text)


def requeue_orphaned_stages(ctl: RunController) -> list[str]:
    """Put back in the queue every stage whose subagent ended with an earlier gateway process.

    A dispatched stage keeps its subagent's id in instance state, and
    ``reconcile_dispatched_stages`` polls the manager for its verdict. After a restart the
    subagent is gone — a native one ran in the dead process, an ACP one is killed by the
    orphan sweep — while the state still reads RUNNING with its id, and a fresh manager knows
    no ids. The reconciler rightly invents no verdict for an unknown id, so the stage read
    "still working" until the stale-run audit, and the loop never moved again.

    Called only when a RESUMED run's controller starts, which is what makes "unknown to this
    process's manager" decisive: nothing this controller spawned can be unknown, and a
    subagent another controller in this process spawned is known (one manager per process).
    So this is not a verdict on the work — the stage goes back to PENDING at the same epoch,
    exactly like a paused stage (`_withdraw_inflight`): its attempt is not charged, its
    no-double-execution claim is released (a leftover claim makes the re-run meet its own
    lease, #3533), and the scheduler dispatches it again.
    """
    manager = ctl.services.subagents
    if manager is None or not hasattr(manager, "get"):
        return []
    orphans: list[str] = []
    for path in awaiting_out_of_band_work(ctl):
        inst = ctl._instance(path)
        try:
            known = manager.get(inst.subagent_id) is not None
        except Exception:
            continue  # an unreadable manager is not evidence the work is gone
        if known:
            continue
        release_execution_claim(inst.claim_target, inst.claim_holder)
        inst.claim_target = ""
        inst.claim_holder = ""
        inst.state = InstanceState.PENDING
        inst.subagent_id = ""
        inst.started_at = None
        inst.attempt = max(0, inst.attempt - 1)
        orphans.append(path)
    if orphans:
        logger.info(
            "run %s: re-queued %d stage(s) whose subagent ended with the previous process: %s",
            ctl.run.id,
            len(orphans),
            ", ".join(orphans),
        )
        ctl._persist_state()
    return orphans


async def stop_dispatched_stages(ctl: RunController, *, reason: str) -> list[str]:
    """Stop every dispatched stage's subagent; return the paths whose subagent was stopped.

    A path whose subagent had ALREADY finished (or that this process's manager does not know —
    the post-restart shape) is left RUNNING and not returned: its outcome is real, and the
    reconciler settles it on the next step it gets. Resetting a finished stage would throw its
    work away; cancelling an unknown one is not possible. Each stopped attempt's
    no-double-execution claim is released here, because the attempt is over and nothing else
    will release it before its TTL (#3533's shape).
    """
    manager = ctl.services.subagents
    if manager is None or not hasattr(manager, "cancel"):
        return []
    stopped: list[str] = []
    for path in awaiting_out_of_band_work(ctl):
        inst = ctl._instance(path)
        try:
            cancelled = bool(await manager.cancel(inst.subagent_id, reason=reason))
        except Exception:
            logger.warning(
                "run %s: could not stop the subagent for %s", ctl.run.id, path, exc_info=True
            )
            continue
        if not cancelled:
            continue
        release_execution_claim(inst.claim_target, inst.claim_holder)
        inst.claim_target = ""
        inst.claim_holder = ""
        stopped.append(path)
    return stopped
