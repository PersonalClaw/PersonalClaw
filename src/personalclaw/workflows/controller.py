"""The run controller — one conductor per run, and the only writer of run state.

Everything about this module follows from one rule: **a run has exactly one writer**
(WF2-R10). The tick loop under `self._lock` is it. Nothing else — not a dispatcher, not
a watchdog, not an HTTP handler — writes a terminal status. Handlers request; the loop
decides and writes.

That rule is what makes the harder features tractable later. Mid-flight mutation (Slice
4) can only be safe if there is a well-defined moment when no node is being scheduled;
here that moment is "between scheduling steps, holding the lock". Crash recovery can only
be correct if terminal writes are serialized, or a resumed run and a still-dying task
race to disagree about the outcome.

The loop is deliberately boring:

    while not terminal:
        drain cancel intent
        compute frontier (pure)
        launch admitted work
        await *something* finishing
        apply results, persist state

`asyncio.wait(FIRST_COMPLETED)` is what keeps it responsive without polling: a fast
transform does not wait behind a ten-minute stage. `WAITING` nodes hold no lane and are
woken by deadline, so a run parked on an approval for six hours costs nothing.

**Budget pre-charge (WF2-R4 invariant).** A resumed run inherits spend from its ledger
before scheduling anything. Minting a fresh budget on resume would turn a crash loop into
unbounded spend — the exact failure the cap exists to prevent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import NodeResult, dispatch
from personalclaw.workflows.journal import CacheKey, Journal, inputs_hash, spec_region_hash
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    Failure,
    FailureClass,
    InstanceState,
    Node,
    NodeInstance,
    NodeKind,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.resilience import (
    Attempt,
    BreakerState,
    attempt_from_failure,
    check_breaker,
    check_budget,
    error_signature,
    escalation_artifact,
    retry_prompt,
)
from personalclaw.workflows.tick import (
    Frontier,
    Limits,
    ReadyNode,
    frontier,
    loop_should_continue,
)

logger = logging.getLogger(__name__)

#: How long a tick waits for in-flight work before re-deriving the frontier. Bounded so a
#: WAITING deadline or an externally-answered gate is noticed promptly.
TICK_WAKE_SECS = 5.0

#: Terminal-state map from a derived root outcome to the run's status.
_ROOT_TO_RUN = {
    InstanceState.DONE: RunStatus.COMPLETE,
    InstanceState.DEGRADED: RunStatus.COMPLETE,
    InstanceState.NO_CHANGE: RunStatus.COMPLETE,
    InstanceState.SKIPPED: RunStatus.COMPLETE,
    InstanceState.FAILED: RunStatus.FAILED,
    InstanceState.SCOPE_VIOLATION: RunStatus.FAILED,
    InstanceState.BLOCKED: RunStatus.FAILED,
    InstanceState.ESCALATED: RunStatus.ESCALATED,
    InstanceState.CANCELLED: RunStatus.CANCELLED,
    InstanceState.DISCARDED: RunStatus.CANCELLED,
}


@dataclass
class EngineServices:
    """Injected collaborators. Every one is optional and defaulted so the controller can
    be driven in a test with no gateway: an engine that can only run inside a live server
    does not get unit-tested, and then its edge cases are discovered in production."""

    subagents: Any = None
    completion: Any = None
    get_provider: Any = None
    verify: Any = None
    #: `(event, payload) -> None` — SSE/WS publication. Never `state.notify`, which is
    #: the user-notification gate (mute/severity/quiet-hours) and would eat engine events.
    publish: Any = None
    model_tiers: dict[str, str] = field(default_factory=dict)
    lane_limits: Limits = field(default_factory=Limits)
    node_timeout_total: int = 900
    node_timeout_stall: int = 300
    cwd: str = ""


@dataclass
class _InFlight:
    """One launched node. `started` feeds the total-timeout clock; `last_progress` feeds
    the stall clock — two knobs, because a long operation is fine and silence is not."""

    task: asyncio.Task
    ready: ReadyNode
    started: float
    last_progress: float
    cache_key: CacheKey


class RunController:
    """Drives one run to a terminal state.

    Construct, then `await start()` (or `await run_to_completion()` for blocking mode).
    The instance is not reusable across runs — genealogy, epoch and journal state are all
    per-run, and sharing one controller would let a rewind on run A serve stale cache to
    run B.
    """

    def __init__(
        self,
        run: WorkflowRun,
        spec: dict[str, Any],
        *,
        services: EngineServices | None = None,
        depth: int = 0,
    ) -> None:
        self.run = run
        self.spec = spec
        self.services = services or EngineServices()
        self.depth = depth
        self.root: Node = Node.from_dict(spec.get("root") or {"kind": "sequence"})
        self.instances: dict[str, NodeInstance] = store.read_state(run.id)
        self.journal = Journal(run.id)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._inflight: dict[str, _InFlight] = {}
        self._declined_edges: set[str] = self._collect_declined_edges()
        self._iterations: dict[str, int] = {}
        self._dry_streaks: dict[str, int] = {}
        #: path -> the attempts already made. Feeds the correction hint on the next try,
        #: and the escalation artifact when retries run out.
        self._attempts: dict[str, list[Attempt]] = {}
        #: loop path -> breaker evidence. Cheap counters; the breaker costs no model call.
        self._breakers: dict[str, BreakerState] = {}
        #: Whether the 80% budget warning has already been emitted (once per run, not
        #: once per node — repeating it every node would bury the signal).
        self._budget_warned = False
        self._outputs: dict[str, Any] = {}
        self._terminal = asyncio.Event()
        self._load_outputs()

    # ── construction helpers ──

    def _collect_declined_edges(self) -> set[str]:
        edges: set[str] = set()
        for inst in self.instances.values():
            edges.update(inst.declined_edges)
        return edges

    def _load_outputs(self) -> None:
        """Rehydrate node-id → output for binding resolution.

        Only SUCCESS states contribute. A failed node's partial output must not resolve
        as if it were a real answer — that is how a downstream prompt ends up confidently
        summarizing an error message.
        """
        by_path = {path: node for path, node in _walk(self.root)}
        for path, inst in self.instances.items():
            if inst.state not in SUCCESS_STATES:
                continue
            node = by_path.get(_base_path(path))
            if node is None or not node.id:
                continue
            self._outputs[node.id] = store.read_output(self.run.id, path)

    # ── public lifecycle ──

    async def start(self) -> None:
        """Launch the tick loop as a background task."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._tick_loop())

    async def run_to_completion(self, *, timeout: float = 0.0) -> RunStatus:
        """Blocking mode: drive to terminal and return the status."""
        await self.start()
        if timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._terminal.wait(), timeout=timeout)
        else:
            await self._terminal.wait()
        return self.run.status

    async def stop(self) -> None:
        """Cancel the loop and every in-flight node. Does NOT write a terminal status:
        a stop is a process-lifecycle event, and a gateway shutdown must leave the run
        resumable rather than falsely marked failed."""
        for entry in list(self._inflight.values()):
            entry.task.cancel()
        self._inflight.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def request_cancel(self) -> None:
        """Record a STICKY cancel intent. Sticky because a cancel issued while the
        gateway is down must still be honoured on restart, so it is a file, not memory."""
        store.request_cancel(self.run.id)

    # ── the tick loop ──

    async def _tick_loop(self) -> None:
        try:
            await self._prepare()
            while True:
                async with self._lock:
                    if await self._step():
                        break
                if self._inflight:
                    await self._await_progress()
                else:
                    delay = self._next_wake_delay()
                    if delay is None:
                        # No in-flight work and no scheduled wake: the next _step call
                        # decides completion or deadlock. Yield rather than spin.
                        await asyncio.sleep(0)
                    else:
                        await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a controller crash must not leave a silent zombie
            logger.exception("workflow run %s: controller crashed", self.run.id)
            async with self._lock:
                await self._finish(RunStatus.FAILED, error=f"engine error: {exc}"[:500])
        finally:
            self._terminal.set()

    async def _prepare(self) -> None:
        """Pre-flight: pre-charge the budget from the ledger, stamp start, journal it."""
        resumed = bool(self.run.started_at)
        totals = journal_mod.run_totals(self.run.id)
        # Budget pre-charge (WF2-R4 #1): a resumed run inherits its own spend. Without
        # this a crash loop mints a fresh budget each time and spends without bound.
        self.run.total_tokens = max(self.run.total_tokens, int(totals.get("tokens", 0)))
        async with self._lock:
            if not self.run.started_at:
                self.run.started_at = _now()
            self.run.status = RunStatus.RUNNING
            self._save_run()
        self.journal.run_started(
            self.run.workflow_name,
            inputs=self.run.inputs,
            spec_version=self.run.spec_version,
            resumed=resumed,
        )
        self._publish("workflow_run_update", {"status": self.run.status.value})

    async def _step(self) -> bool:
        """One scheduling step under the lock. Returns True when the run is terminal.

        This is also the designated safe point for mid-flight mutation (Slice 4): the
        lock is held and no node is mid-launch.
        """
        if store.cancel_requested(self.run.id):
            await self._cancel_inflight()
            await self._finish(RunStatus.CANCELLED)
            return True

        self._wake_due_nodes()

        fr = self._frontier()

        if fr.complete and not self._inflight:
            status = _ROOT_TO_RUN.get(fr.outcome or InstanceState.DONE, RunStatus.COMPLETE)
            await self._finish(status)
            return True

        self._check_budget_warning()

        if self._budget_exceeded():
            # SOFT budget: pause resumably rather than fail. The user can extend and
            # resume; killing the run would discard completed work.
            await self._finish(RunStatus.PAUSED, error="budget cap reached")
            return True

        # Untaken branch paths become SKIPPED before scheduling. A skipped node is
        # terminal, which is what satisfies a downstream `needs` edge instead of leaving
        # a join waiting on a leg that will never run (WF2-R18).
        for path in fr.to_skip:
            self._skip(path)

        for item in fr.ready:
            if item.path in self._inflight:
                continue
            await self._launch(item)

        if fr.blocked and not self._inflight:
            await self._finish(RunStatus.FAILED, error=f"run deadlocked: {fr.block_reason}")
            return True

        if not self._inflight and not fr.ready and not fr.deferred:
            waiting = [p for p, i in self.instances.items() if i.state == InstanceState.WAITING]
            if waiting and self._next_wake_delay() is None:
                # Parked on a human/external signal with no deadline: needs_input, which
                # is a real surfaced state, not a hang.
                await self._finish(RunStatus.NEEDS_INPUT)
                return True
        return False

    def _skip(self, path: str) -> None:
        """Mark a whole subtree skipped. The subtree matters: skipping only the case root
        would leave its children pending, and a derived container state would then read
        the branch as unfinished forever."""
        node = dict(_walk(self.root)).get(_base_path(path))
        paths = [path]
        if node is not None:
            from personalclaw.workflows.models import walk

            paths = [
                path if sub == "root" else f"{path}{sub[len('root'):]}" for sub, _n in walk(node)
            ]
        for target in paths:
            inst = self._instance(target)
            if inst.state in TERMINAL_STATES:
                continue
            inst.state = InstanceState.SKIPPED
            inst.completed_at = _now()
            self.journal.step_skipped(
                target, node.id if node else "", epoch=inst.epoch, actor="engine"
            )
        self._persist_state()

    def _frontier(self) -> Frontier:
        return frontier(
            self.root,
            {p: i.state for p, i in self.instances.items()},
            limits=self.services.lane_limits,
            declined_edges=self._declined_edges,
            outputs=self._outputs,
            inputs=self.run.inputs,
            iterations=self._iterations,
            running_lanes=self._running_lanes(),
        )

    def _running_lanes(self) -> dict[str, int]:
        used: dict[str, int] = {}
        for entry in self._inflight.values():
            used[entry.ready.lane] = used.get(entry.ready.lane, 0) + 1
        return used

    # ── launching ──

    async def _launch(self, item: ReadyNode) -> None:
        """Dispatch one node, serving a cache hit when the journal has one."""
        ctx = self._context_for(item)
        resolved_view = self._resolved_inputs(item, ctx)
        inst = self._instance(item.path)
        key = CacheKey(
            path=item.path,
            epoch=inst.epoch,
            inputs_hash=inputs_hash(resolved_view),
            spec_hash=spec_region_hash(item.node.to_dict()),
        )

        hit = self.journal.lookup(key)
        if hit:
            # Resume/rewind cache hit (WF2-A1). Emitted, not silent: "did my edit re-run
            # anything?" must be answerable from the ledger.
            state = InstanceState(str(hit.get("state", "done")))
            inst.state = state
            inst.output_ref = str(hit.get("output_ref", "") or "")
            inst.completed_at = _now()
            if item.node.id:
                self._outputs[item.node.id] = store.read_output(self.run.id, item.path)
            self.journal.step_cached(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                cache_key=key.to_str(),
                state=state,
                output_ref=inst.output_ref,
            )
            self._persist_state()
            self._publish(
                "workflow_node_done",
                {
                    "node_id": item.node.id,
                    "instance_path": item.path,
                    "status": state.value,
                    "cached": True,
                },
            )
            return

        inst.state = InstanceState.RUNNING
        inst.started_at = _now()
        inst.attempt += 1
        self._persist_state()
        self.journal.step_started(item.path, item.node.id, epoch=inst.epoch, lane=item.lane)
        self._publish(
            "workflow_node_started",
            {
                "node_id": item.node.id,
                "instance_path": item.path,
                "epoch": inst.epoch,
            },
        )

        now = time.time()
        task = asyncio.create_task(self._execute(item, ctx))
        self._inflight[item.path] = _InFlight(
            task=task, ready=item, started=now, last_progress=now, cache_key=key
        )

    def _with_retry_hint(self, item: ReadyNode) -> Node:
        """On a retry, hand the dispatcher a node whose prompt carries the correction.

        Returns the node UNCHANGED on a first attempt and for kinds with no prompt, so
        the common path pays nothing. A copy is returned rather than mutating the spec
        node: the spec is shared across every instance of a `foreach` body, and editing it
        in place would leak one item's failure into every sibling's prompt.
        """
        attempts = self._attempts.get(item.path)
        if not attempts:
            return item.node
        prompt = (item.node.config or {}).get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return item.node
        import copy

        node = copy.deepcopy(item.node)
        node.config["prompt"] = retry_prompt(prompt, attempts)
        return node

    async def _execute(self, item: ReadyNode, ctx: BindingContext) -> NodeResult:
        """Run a dispatcher under the total-timeout knob.

        A timeout here is a REAL kill, not a decorative config value — the studied
        cautionary case is an engine that shipped a no-op node timeout nobody noticed,
        because timeouts only ever execute under failure.
        """
        total = self.services.node_timeout_total
        node = self._with_retry_hint(item)
        coro = dispatch(
            node,
            ctx,
            now=time.time(),
            subagents=self.services.subagents,
            depth=self.depth,
            run_id=self.run.id,
            cwd=self.services.cwd,
            tiers=self.services.model_tiers,
            completion=self.services.completion,
            get_provider=self.services.get_provider,
            verify=self.services.verify,
        )
        if total and total > 0:
            try:
                return await asyncio.wait_for(coro, timeout=total)
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
        return await coro

    async def _await_progress(self) -> None:
        """Wait for the first in-flight node to finish, then apply every finished one.

        FIRST_COMPLETED rather than ALL: a fast transform must not wait behind a
        ten-minute stage, or the run's effective concurrency collapses to its slowest
        node.
        """
        tasks = [e.task for e in self._inflight.values()]
        if not tasks:
            return
        await asyncio.wait(tasks, timeout=TICK_WAKE_SECS, return_when=asyncio.FIRST_COMPLETED)
        async with self._lock:
            self._enforce_stall_timeouts()
            for path, entry in list(self._inflight.items()):
                if not entry.task.done():
                    continue
                self._inflight.pop(path, None)
                try:
                    result = entry.task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    from personalclaw.workflows.engine import _classify_exception

                    result = NodeResult(
                        state=InstanceState.FAILED, failure=_classify_exception(exc)
                    )
                self._apply(entry, result)
            self._persist_state()

    def _enforce_stall_timeouts(self) -> None:
        """Kill nodes that have gone silent. The stall knob is separate from the total
        knob on purpose: a long-but-progressing node survives, a wedged one does not."""
        stall = self.services.node_timeout_stall
        if not stall or stall <= 0:
            return
        now = time.time()
        for path, entry in list(self._inflight.items()):
            if now - entry.last_progress < stall:
                continue
            entry.task.cancel()
            self._inflight.pop(path, None)
            inst = self._instance(path)
            failure = Failure(
                failure_class=FailureClass.TIMEOUT,
                cause_plain=f"no progress for {stall}s (timeout_stall)",
                remediation="the node produced no progress events; check the provider "
                "or raise workflows.default_node_timeout_stall_secs",
                recoverable=True,
            )
            inst.state = InstanceState.FAILED
            inst.failure = failure
            inst.completed_at = _now()
            self.journal.step_failed(
                path,
                entry.ready.node.id,
                epoch=inst.epoch,
                failure=failure,
                attempt=inst.attempt,
                retries_exhausted=True,
            )
            self._publish(
                "workflow_node_done",
                {
                    "node_id": entry.ready.node.id,
                    "instance_path": path,
                    "status": InstanceState.FAILED.value,
                },
            )

    def note_progress(self, path: str) -> None:
        """Feed the stall clock. Called by the dispatch layer when a node emits progress
        — that is what makes `timeout_stall` mean "silent", not merely "slow"."""
        entry = self._inflight.get(path)
        if entry:
            entry.last_progress = time.time()

    # ── applying results ──

    def _apply(self, entry: _InFlight, result: NodeResult) -> None:
        """Write one node's outcome. The ONLY place a node reaches terminal (WF2-R10)."""
        item = entry.ready
        inst = self._instance(item.path)
        duration = max(0.0, time.time() - entry.started)

        if result.state == InstanceState.READY:
            # Capacity backpressure, not an outcome: reset to pending so the next tick
            # re-derives it as ready rather than treating it as finished.
            inst.state = InstanceState.PENDING
            return

        if result.state == InstanceState.RUNNING:
            # An async node (a spawned stage) whose completion arrives later. Stay
            # RUNNING; the watchdog reconciles it.
            inst.state = InstanceState.RUNNING
            if result.output:
                self._outputs.setdefault(item.node.id, result.output)
            return

        if result.state == InstanceState.WAITING:
            inst.state = InstanceState.WAITING
            # Wait-entry edge activation (WF2-R18): registering at entry rather than
            # completion is what stops a fan-out's join firing on its fast leg alone.
            self._decline(inst, result.declined_edges)
            # The deadline goes on the INSTANCE so it is persisted with run state. A
            # memory-only deadline is lost on restart, and every waiting run then parks
            # forever with nothing scheduled to wake it.
            inst.wake_at = float(result.wake_at or 0.0)
            if result.ask:
                self.run.attention = dict(result.ask)
                self._publish(
                    "workflow_attention",
                    {
                        "node_id": item.node.id,
                        "kind": result.ask.get("kind"),
                        "ask": result.ask,
                    },
                )
            return

        # Retry, when the failure class says it is worth spending on. The attempt is
        # RECORDED before the retry so the next one can be corrected rather than blind —
        # a blind retry re-sends the same prompt and reproduces the same failure.
        if result.state == InstanceState.FAILED:
            failure = result.failure or Failure()
            record = attempt_from_failure(
                inst.attempt, failure, tokens=result.tokens, duration_secs=duration
            )
            self._attempts.setdefault(item.path, []).append(record)
            if self._should_retry(item, inst, result):
                inst.state = InstanceState.PENDING
                self.journal.write(
                    journal_mod.STEP_ATTEMPT,
                    instance_path=item.path,
                    node_id=item.node.id,
                    epoch=inst.epoch,
                    **record.to_dict(),
                )
                self.journal.step_failed(
                    item.path,
                    item.node.id,
                    epoch=inst.epoch,
                    failure=failure,
                    attempt=inst.attempt,
                    retries_exhausted=False,
                )
                return

        inst.state = result.state
        inst.completed_at = _now()
        inst.degraded_reason = result.degraded_reason
        inst.failure = result.failure
        inst.tokens = result.tokens
        self._decline(inst, result.declined_edges)

        if result.state in SUCCESS_STATES:
            ref, preview = self.journal.store_output(item.path, result.output)
            inst.output_ref = ref
            if item.node.id:
                self._outputs[item.node.id] = preview
            self.run.total_tokens += int(result.tokens)
            self.journal.step_completed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                cache_key=entry.cache_key.to_str(),
                state=result.state,
                duration_secs=duration,
                tokens=result.tokens,
                retries=max(0, inst.attempt - 1),
                model=result.model,
                provider=result.provider,
                cost_usd=result.cost_usd,
                degraded_reason=result.degraded_reason,
                resolved_prompt_ref=self._store_prompt(item.path, result.resolved_prompt),
                output_ref=ref,
            )
        else:
            self.journal.step_failed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                failure=result.failure or Failure(),
                attempt=inst.attempt,
                retries_exhausted=True,
                signature={
                    "failing_node": item.node.id,
                    "layer": "execution",
                    "reason": (result.failure.failure_class.value if result.failure else ""),
                    "input_hash": entry.cache_key.inputs_hash,
                },
            )
            # Retries are spent. Produce the typed escalation artifact rather than just
            # dying: five named options let a human act, where a bare "it failed" leaves
            # them to invent the next move (WF2-R4).
            self._escalate(
                item.path,
                item.node.id,
                reason="retries_exhausted",
                detail=(result.failure.cause_plain if result.failure else ""),
            )

        self._advance_loop(item)
        self._publish(
            "workflow_node_done",
            {
                "node_id": item.node.id,
                "instance_path": item.path,
                "status": result.state.value,
                "degraded_reason": result.degraded_reason,
                "output_preview": _preview(result.output),
            },
        )

    def _should_retry(self, item: ReadyNode, inst: NodeInstance, result: NodeResult) -> bool:
        """Only retryable classes, only within the declared budget.

        The scheduler consults `retryable` on the failure envelope rather than a blanket
        count: retrying a USER or PERMISSION error burns budget to reach the same
        failure.
        """
        failure = result.failure
        if failure is None or not failure.retryable:
            return False
        retry_cfg = (item.node.config or {}).get("retry") or {}
        max_attempts = retry_cfg.get("max_attempts", 1)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            max_attempts = 1
        no_retry = retry_cfg.get("no_retry_modes") or []
        if isinstance(no_retry, list) and failure.failure_class.value in [str(m) for m in no_retry]:
            return False
        return inst.attempt < max_attempts

    def _escalate(self, path: str, node_id: str, *, reason: str, detail: str = "") -> None:
        """Record the escalation artifact and surface it as run attention.

        Journaled AND surfaced: journaling alone leaves an unattended run looking merely
        failed, and surfacing alone loses the evidence a later reader needs.
        """
        artifact = escalation_artifact(
            node_id, reason=reason, detail=detail, attempts=self._attempts.get(path, [])
        )
        # The artifact already carries `node_id` and `kind`; splatting it alongside
        # explicit kwargs would collide on both.
        self.journal.write(
            journal_mod.STEP_ESCALATED,
            instance_path=path,
            **{k: v for k, v in artifact.items() if k != "kind"},
        )
        self.run.attention = artifact
        self._publish(
            "workflow_attention",
            {"node_id": node_id, "kind": "escalation", "ask": artifact},
        )

    def _check_budget_warning(self) -> None:
        """Emit the 80% warning ONCE per run, so a user can extend before work stops."""
        cap = getattr(self.run.budget, "max_tokens", 0) or 0
        verdict = check_budget(self.run.total_tokens, int(cap))
        if verdict.warn and not self._budget_warned:
            self._budget_warned = True
            self._publish(
                "workflow_run_update",
                {
                    "status": self.run.status.value,
                    "budget_warning": verdict.reason,
                    "spent": verdict.spent,
                    "cap": verdict.cap,
                },
            )

    def _decline(self, inst: NodeInstance, edges: list[str]) -> None:
        if not edges:
            return
        inst.declined_edges = sorted(set(inst.declined_edges) | set(edges))
        self._declined_edges.update(edges)

    def _advance_loop(self, item: ReadyNode) -> None:
        """Advance a loop's iteration counter when its body finished an iteration.

        The counter lives here rather than in the frontier because advancing it is a
        WRITE, and the frontier is pure. `loop_should_continue` keeps the decision itself
        pure and testable.
        """
        parent_path, iteration = _loop_parent(item.path)
        if parent_path is None:
            return
        node = dict(_walk(self.root)).get(parent_path)
        if node is None or node.kind != NodeKind.LOOP:
            return
        output = self._outputs.get(item.node.id)
        if _is_dry(output):
            self._dry_streaks[parent_path] = self._dry_streaks.get(parent_path, 0) + 1
        else:
            self._dry_streaks[parent_path] = 0

        # Feed the breaker, then consult it BEFORE the next iteration. Deterministic and
        # LLM-free: a loop thrashing on the same error is the most common autonomous-run
        # failure, and paying a model to notice it would be slower and less reliable.
        inst = self._instance(item.path)
        breaker = self._breakers.setdefault(parent_path, BreakerState())
        breaker.record(
            signature=error_signature(inst.failure) if inst.failure else "",
            output=output,
            tokens=inst.tokens,
        )
        verdict = check_breaker(node, breaker)
        if verdict.tripped:
            loop_inst = self._instance(parent_path)
            # ESCALATED, deliberately NOT FAILED: "I gave up and a human must decide" is a
            # different fact from "this broke", and collapsing them loses what the user
            # needs to act on.
            loop_inst.state = InstanceState.ESCALATED
            loop_inst.completed_at = _now()
            self.journal.iteration(
                parent_path,
                node.id,
                iteration=iteration,
                outcome=f"breaker:{verdict.reason}",
                error_signature=breaker.error_signatures[-1] if breaker.error_signatures else "",
                tokens=inst.tokens,
            )
            self._escalate(parent_path, node.id, reason=verdict.reason, detail=verdict.detail)
            return

        ctx = BindingContext(
            inputs=self.run.inputs,
            node_outputs=self._outputs,
            iter_index=iteration,
            last_output=output,
            has_last=True,
        )
        keep_going, reason = loop_should_continue(
            node,
            iteration=iteration + 1,
            last_output=output,
            dry_streak=self._dry_streaks.get(parent_path, 0),
            ctx=ctx,
        )
        self.journal.iteration(
            parent_path,
            node.id,
            iteration=iteration,
            outcome=reason or "continue",
            error_signature="",
            tokens=0,
        )
        if keep_going:
            self._iterations[parent_path] = iteration + 1
        else:
            loop_inst = self._instance(parent_path)
            loop_inst.state = InstanceState.DONE
            loop_inst.completed_at = _now()

    # ── binding context ──

    def _context_for(self, item: ReadyNode) -> BindingContext:
        return BindingContext(
            inputs=dict(self.run.inputs),
            node_outputs=dict(self._outputs),
            item=item.item,
            has_item=item.has_item,
            iter_index=item.iter_index,
            secret_resolver=_secret_resolver,
        )

    def _resolved_inputs(self, item: ReadyNode, ctx: BindingContext) -> dict[str, Any]:
        """What actually reached this node — the cache's `inputs_hash` input.

        Only the node's declared dependencies, not the whole output map: hashing every
        output would invalidate the cache whenever any unrelated node finished, making
        resume useless.
        """
        from personalclaw.workflows.bindings import node_deps

        deps = node_deps(item.node.config or {})
        view: dict[str, Any] = {dep: self._outputs.get(dep) for dep in sorted(deps)}
        if item.has_item:
            view["__item"] = item.item
        if item.iter_index is not None:
            view["__iter"] = item.iter_index
        return view

    def _store_prompt(self, path: str, prompt: str) -> str:
        """Persist the fully-resolved prompt and return its ref.

        Required for trajectory replay (§5): the acceptance bar is that prompt → tool
        calls → output is reconstructable from ledger events alone.
        """
        if not prompt:
            return ""
        return store.write_output(self.run.id, f"{path}::prompt", prompt)

    # ── wake / budget / persistence ──

    def _wake_due_nodes(self) -> None:
        """Resolve WAITING nodes whose deadline has passed.

        The controller resolves them rather than re-dispatching, because a dispatcher is
        stateless: re-entering `dispatch_wait` would recompute `now + duration` and the
        node would wait forever, one full duration at a time. The controller is the state
        owner and already knows why the node parked, so it decides here.
        """
        now = time.time()
        for path, inst in list(self.instances.items()):
            if inst.state != InstanceState.WAITING or not inst.wake_at:
                continue
            if inst.wake_at > now:
                continue
            inst.wake_at = 0.0
            node = dict(_walk(self.root)).get(_base_path(path))
            kind = node.kind if node else None
            if kind == NodeKind.WAIT:
                # The deadline WAS the work. Reaching it is success.
                inst.state = InstanceState.DONE
                inst.completed_at = _now()
                node_id = node.id if node else ""
                # Persisted, not just in-memory: a restart re-reads outputs from disk, and
                # an in-memory-only value would come back None and break a binding on it.
                ref, preview = self.journal.store_output(path, {"waited": True})
                inst.output_ref = ref
                if node_id:
                    self._outputs[node_id] = preview
                self.journal.step_completed(
                    path,
                    node_id,
                    epoch=inst.epoch,
                    cache_key="",
                    state=InstanceState.DONE,
                    output_ref=ref,
                )
                self._publish(
                    "workflow_node_done",
                    {
                        "node_id": node.id if node else "",
                        "instance_path": path,
                        "status": InstanceState.DONE.value,
                    },
                )
                continue
            # A gate that timed out. Unattended runs must surface this rather than wedge
            # forever, and it is NOT a pass — nobody approved anything (WF2-R7).
            failure = Failure(
                failure_class=FailureClass.TIMEOUT,
                cause_plain="gate timed out with no answer",
                remediation="answer the gate from the run view, or raise its timeout_secs",
                terminal_reason="timed_out_unattended",
            )
            inst.state = InstanceState.FAILED
            inst.failure = failure
            inst.completed_at = _now()
            self.journal.step_failed(
                path,
                node.id if node else "",
                epoch=inst.epoch,
                failure=failure,
                attempt=inst.attempt,
                retries_exhausted=True,
            )
            self._publish(
                "workflow_node_done",
                {
                    "node_id": node.id if node else "",
                    "instance_path": path,
                    "status": InstanceState.FAILED.value,
                    "degraded_reason": "timed_out_unattended",
                },
            )
        self._persist_state()

    def _next_wake_delay(self) -> float | None:
        deadlines = [
            i.wake_at
            for i in self.instances.values()
            if i.state == InstanceState.WAITING and i.wake_at
        ]
        if not deadlines:
            return None
        return max(0.05, min(TICK_WAKE_SECS, min(deadlines) - time.time()))

    def _budget_exceeded(self) -> bool:
        cap = getattr(self.run.budget, "max_tokens", 0) or 0
        return bool(cap) and self.run.total_tokens >= int(cap)

    def _instance(self, path: str) -> NodeInstance:
        inst = self.instances.get(path)
        if inst is None:
            inst = NodeInstance(path=path)
            self.instances[path] = inst
        return inst

    def _persist_state(self) -> None:
        store.write_state(self.run.id, self.instances)

    def _save_run(self) -> None:
        store.save(self.run)

    async def _cancel_inflight(self) -> None:
        for entry in list(self._inflight.values()):
            entry.task.cancel()
            inst = self._instance(entry.ready.path)
            inst.state = InstanceState.CANCELLED
            inst.completed_at = _now()
        self._inflight.clear()
        self._persist_state()

    async def _finish(self, status: RunStatus, *, error: str = "") -> None:
        """Write the run's terminal status. The single terminal writer (WF2-R10)."""
        self.run.status = status
        self.run.error_message = error
        if status in (
            RunStatus.COMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.ESCALATED,
        ):
            self.run.completed_at = _now()
            if self.run.started_at:
                self.run.elapsed_seconds = max(
                    0.0, _epoch(self.run.completed_at) - _epoch(self.run.started_at)
                )
        totals = journal_mod.run_totals(self.run.id)
        self.run.total_tokens = max(self.run.total_tokens, int(totals.get("tokens", 0)))
        self._save_run()
        self._persist_state()
        self.journal.run_finished(
            status.value,
            elapsed_secs=self.run.elapsed_seconds,
            tokens=self.run.total_tokens,
            error=error,
        )
        if status == RunStatus.CANCELLED:
            store.clear_cancel(self.run.id)
        self._publish("workflow_run_update", {"status": status.value, "error": error})

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        fn = self.services.publish
        if fn is None:
            return
        body = {"run_id": self.run.id, **payload}
        try:
            fn(event, body)
        except Exception:  # a broken observer must never kill a run
            logger.debug("workflow %s: publish %s failed", self.run.id, event, exc_info=True)


# ── module helpers ───────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0.0


def _walk(root: Node) -> list[tuple[str, Node]]:
    from personalclaw.workflows.models import walk

    return walk(root)


def _base_path(path: str) -> str:
    """Strip the foreach/loop instance suffix, yielding the SPEC path.

    `root.body#3` and `root.body@2` are instances of the same spec node; the state map is
    keyed by instance, but the spec lookup needs the shared path.
    """
    for sep in ("#", "@"):
        idx = path.rfind(sep)
        if idx > 0:
            return path[:idx]
    return path


def _loop_parent(path: str) -> tuple[str | None, int]:
    """`root.children[0].body@2` → `("root.children[0]", 2)`."""
    idx = path.rfind("@")
    if idx <= 0:
        return None, 0
    try:
        iteration = int(path[idx + 1 :])
    except ValueError:
        return None, 0
    body = path[:idx]
    if not body.endswith(".body"):
        return None, 0
    return body[: -len(".body")], iteration


def _is_dry(output: Any) -> bool:
    """Did an iteration surface anything new? Feeds `until_dry` termination."""
    if output is None:
        return True
    if isinstance(output, (list, dict, str)):
        return len(output) == 0
    return False


def _preview(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (dict, list)):
        import json

        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)[:limit]
        return text[:limit]
    return value


def _secret_resolver(key: str) -> str:
    """Resolve `{{secret:KEY}}` from the credential store.

    Injected rather than imported at the binding layer so unit tests never touch real
    credentials, and so the resolution point is a single auditable seam.

    An unknown name returns "" rather than raising: `resolve()` treats an empty secret as
    a resolution failure and reports it with the binding's own error message, which is
    more actionable than a bare `KeyError` from two layers down.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.credentials import CredentialStore

    try:
        cred = CredentialStore(config_dir()).resolve(key)
    except KeyError:
        return ""
    return cred.secret or ""
