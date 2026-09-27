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

This module is the loop itself — launch, settle (`_apply`) and the terminal write
(`_finish`). What each step DECIDES lives beside it, one responsibility per module, as
functions over the controller: `run_admission`, `stage_settlement`, `step_dispatch`,
`node_bindings`, `iteration_context`, `loop_iteration`, `loop_convergence`,
`gate_answers`, `mid_flight`, `effect_boundary`, `task_projection`, `run_start`,
`run_finish`, and the stall clock in `liveness`. They are reached only through this
object — its tick loop and `resume` — so the split adds no writer: WF2-R10's one writer
is still the controller, filed by what it decides.

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

from personalclaw import review_triage
from personalclaw.guardrails.calls import CallLog, capture_model_calls
from personalclaw.workflows import (
    attention,
)
from personalclaw.workflows import context as context_mod
from personalclaw.workflows import (
    effect_boundary,
    execution_hints,
    gate_answers,
    gate_policy,
    iteration_context,
)
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import (
    liveness,
    longrun,
    loop_iteration,
    mid_flight,
    mutations,
    node_bindings,
    run_admission,
    run_finish,
    run_start,
    stage_settlement,
    step_dispatch,
    store,
    task_projection,
)
from personalclaw.workflows.effects import EffectRecord, EffectStatus, effect_history
from personalclaw.workflows.engine import NodeResult, node_commits_effects
from personalclaw.workflows.failure_taxonomy import with_breaker_window
from personalclaw.workflows.journal import CacheKey, Journal, inputs_hash, spec_region_hash
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    Failure,
    FailureClass,
    InstanceState,
    ItemErrorPolicy,
    Node,
    NodeInstance,
    NodeKind,
    RunStatus,
    WorkflowRun,
    now_stamp,
    run_ending,
    spec_path,
    stamp_epoch,
    walk,
)
from personalclaw.workflows.resilience import (
    Attempt,
    BreakerState,
    attempt_from_failure,
    check_budget,
    escalation_artifact,
)
from personalclaw.workflows.step_usage import NOT_RECORDED, NOTHING_SENT, measured
from personalclaw.workflows.tick import (
    Frontier,
    Limits,
    ReadyNode,
    derive_state,
    frontier,
    item_error_policy,
    reap_watchers,
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
    #: The dashboard state, for the ATTENTION path only (WF2-R7): a waiting gate raises a
    #: durable inbox item + one notification. Separate from `publish` on purpose — `publish`
    #: is the live event stream every open view folds, this is the "tell the human, durably"
    #: path a closed browser must still reach. Without it a 3am scheduled run could park on a
    #: gate and never be mentioned anywhere.
    attention_state: Any = None
    model_tiers: dict[str, str] = field(default_factory=dict)
    lane_limits: Limits = field(default_factory=Limits)
    node_timeout_total: int = 900
    node_timeout_stall: int = 300
    cwd: str = ""
    #: The run supervisor, for `subworkflow` nesting (WF2-R13). A child run must be driven by the
    #: same supervisor that will adopt it on restart, so it is threaded through rather than looked
    #: up from a global — which would also make nesting untestable without a gateway.
    supervisor: Any = None
    #: `(command, output_id) -> (ok, detail)` — effect teardown execution. Injected so
    #: tests never run real teardown subprocesses; production defaults to the
    #: subprocess runner in `effects.run_teardown`.
    teardown_runner: Any = None
    #: The memory service the run-end learner writes through (LEARNING-FLYWHEEL §3.3). Left
    #: None on purpose in every test and CLI path: the run-end capture spoke is inert unless
    #: this is a service with a live vector store, exactly as `self_model_observer.observe_turn`
    #: no-ops without `has_vector`. So a terminal-run controller test never touches the real
    #: home, and production wires `MemoryService.over_vector_store(self.vector_memory)` in.
    memory: Any = None
    #: `() -> float` — the wall clock, as a seam (PP-6). The controller's scheduling decisions
    #: (`_wake_due_nodes` resolving a parked node, and the `now` a `wait` computes its deadline
    #: against) read through this rather than `time.time()` directly, so a replay can substitute
    #: the run's OWN recorded clock and reach the same node in the same order. `frontier()` stays
    #: pure — it reads no clock at all; the nondeterminism lives here, which is why the seam does
    #: too. None means the real wall clock; every production and test path leaves it None.
    clock: Any = None


@dataclass
class _InFlight:
    """One launched node. `started` feeds the total-timeout clock; `last_progress` feeds
    the stall clock — two knobs, because a long operation is fine and silence is not."""

    task: asyncio.Task
    ready: ReadyNode
    started: float
    last_progress: float
    cache_key: CacheKey
    #: Every guarded model call the node's dispatch made (`guardrails.calls`) — what an action
    #: provider's calls used, and, at a cancel, how many were cut off mid-generation.
    calls: CallLog = field(default_factory=CallLog)


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
        #: The wall-clock seam (PP-6). Every scheduling-decision clock read routes through this so a
        #: replay can hand the controller the run's OWN recorded clock; None is the real wall clock,
        #: which is what every production and test path uses. The stall/duration clocks stay on
        #: `time.time()` on purpose — they measure how long real work took, not when a parked node
        #: was allowed to advance, so a recorded clock must not rewrite them.
        self._clock = self.services.clock or time.time
        #: Bindings this run has already projected into Tasks (TASKS-SOPS §1, S61f). The
        #: controller is the single writer for its own run, so this is the dedup set
        #: `plan_materialization` compares against — a per-node read of the per-entity JSON
        #: store would be one file scan per settled node.
        self._projected: list[Any] = []
        #: In-flight projection writes, so teardown does not orphan them and a test can await
        #: settlement instead of sleeping.
        self._projection_writes: set[Any] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._inflight: dict[str, _InFlight] = {}
        self._declined_edges: set[str] = self._collect_declined_edges()
        self._iterations: dict[str, int] = {}
        self._dry_streaks: dict[str, int] = {}
        #: Item paths whose WIP=1 refusal has already been journaled (R5b). In memory only:
        #: the record it dedupes is a scheduling note, and re-journaling one after a resume is
        #: harmless next to carrying a second persisted set to keep in sync.
        self._wip_logged: set[str] = set()
        #: Resource name → the `holder` string this controller claimed it with (PP-12). Recorded so
        #: the release is by the SAME identity that claimed: `pool.release` refuses a non-holder,
        #: and reconstructing the holder at release time is how a released-by-nobody lease strands.
        self._held_leases: dict[str, str] = {}
        #: Whether the on-disk lease records this run already holds have been re-adopted. Once per
        #: controller: it is a directory scan, and the answer cannot change without this object
        #: being the one that changed it.
        self._leases_adopted: bool = False
        #: Paths whose PP-12 admission hold is already journaled, deduped exactly like
        #: `_wip_logged` — the frontier re-derives every tick, and one baking step would otherwise
        #: write a record per tick for the whole bake window.
        self._admission_logged: set[str] = set()
        #: Wall clock at which a PP-12 hold could next change its mind. A bake floor expires at a
        #: time the engine can NAME, and a lease at its TTL, so the tick loop sleeps until then
        #: instead of spinning: `_next_wake_delay` returns None for a run with nothing WAITING and
        #: nothing in flight, and the loop's `sleep(0)` fallback would busy-wait through the window.
        self._admission_wake: float = 0.0
        #: `(spec_version, declares)` — whether ANY node declares a PP-12 admission key. Cached per
        #: spec version rather than per construction because a mid-flight mutation can add one.
        self._admission_declared: tuple[int, bool] | None = None
        #: `<step path>@<epoch>` keys whose metric rollback is already queued, so one regressed step
        #: queues one rewind instead of one per tick until the drain lands.
        self._rollbacks_queued: set[str] = set()
        #: `<foreach path>@<epoch>` keys whose collected-failure record is already in the ledger
        #: (WV-13). Seeded from the ledger on first use rather than left empty like
        #: `_wip_logged`: this record's payload is a COUNT of failed items, and a resumed run
        #: that wrote it twice would tell a reader the fan-out failed twice.
        self._items_collected: set[str] | None = None
        #: Steering (LOOPS-EVOLUTION R14), keyed by the iterated container's path. The durable
        #: queue lives on `run.extra["steering_queue"]` (written by `service.steer_run`); the tick
        #: consumes it at the iteration boundary and parks the rendered re-plan block HERE until
        #: the next iteration's prompt picks it up. Single-use: cleared once injected, so a resume
        #: cannot replay a mid-run instruction — the same discipline the human-input continuations
        #: follow.
        self._steering_inject: dict[str, str] = {}
        #: Context lifecycle (WF2-R6), keyed by the iterated container's path. Held in memory for
        #: the CURRENT run and journaled on every write, so a resumed or rewound run rebuilds them
        #: from the ledger rather than losing them — see `iteration_context.rehydrate_context`.
        self._handoffs: dict[str, context_mod.Handoff] = {}
        self._carryover: dict[str, context_mod.Carryover] = {}
        self._decisions: dict[str, list[context_mod.Decision]] = {}
        #: The project Session Brief (KNOWLEDGE-SYNTHESIS §5.3), built ONCE at run start and
        #: exposed as `{{brief.text}}` / `{{brief.items}}`. Once because it is injected into every
        #: node's context: rebuilding per node would query the store dozens of times per run for
        #: an answer that cannot change mid-run.
        #:
        #: RUN context only. Knowledge is never ambiently injected into CHAT — it enters a chat
        #: session through the composer @-picker or the agent's `knowledge_search` tool, both of
        #: which are the user asking. Nothing here is reachable from a chat-context path.
        self._brief: Any = None
        #: The run's worker model, resolved ONCE (see `step_dispatch._worker_model`) — the family a
        #: `cross_model` judge gate must avoid (WF2LOO-11). The active selection does not change
        #: mid-run, so re-resolving per gate would re-read the model store for an answer that cannot
        #: change.
        self._worker_model_cache: str | None = None
        #: node id -> the fraction each of that node's prompt compactions freed (WV-12). Read by
        #: `context_compaction.should_compact`: two consecutive compactions that each freed <10%
        #: mean compaction has stopped helping this node, and it stops paying a summarizer for it.
        #:
        #: Keyed by node ID, not by instance PATH, on purpose. A loop body's iteration 40 is a
        #: different path than iteration 39, so a path key would hand every iteration a fresh
        #: empty history — and a long-horizon loop is exactly the shape whose prompt grows the
        #: same way every cycle. Keying by id is what makes the rule able to observe repetition
        #: at all.
        self._compaction_saves: dict[str, list[float]] = {}
        #: Long-run watcher state (KNOWLEDGE-SYNTHESIS §4.1), keyed by the loop's path. Journaled
        #: on every cycle and replayed on resume: held only in memory it would reset on every
        #: gateway restart, which is precisely when a months-long watcher is most likely to be
        #: interrupted — and a reset seen-set silently re-processes everything it already paid for.
        self._seen: dict[str, longrun.SeenSet] = {}
        #: path -> the attempts already made. Feeds the correction hint on the next try,
        #: and the escalation artifact when retries run out.
        self._attempts: dict[str, list[Attempt]] = {}
        #: loop path -> breaker evidence. Cheap counters; the breaker costs no model call.
        self._breakers: dict[str, BreakerState] = {}
        #: Whether the 80% budget warning has already been emitted (once per run, not
        #: once per node — repeating it every node would bury the signal).
        self._budget_warned = False
        #: path -> effect records, folded from the ledger at construction so a RESUMED
        #: run knows which effects already committed. Without the rehydrate, a crash
        #: between commit and completion double-fires on resume — the exact hole the
        #: ledger closes (WF2-R1).
        self._effects: dict[str, list[EffectRecord]] = effect_history(run.id)
        #: Validated batches awaiting the tick loop's drain point. A queue rather than
        #: direct application: a handler applying a mutation mid-launch would make two
        #: writers of run state (WF2-R10).
        self._pending_mutations: list[tuple[mutations.BatchResult, str]] = []
        #: instance path → what a person answered when that step parked on them, held for the ONE
        #: dispatch the answer starts (`gate_answers.settle_parked_step` writes it,
        #: `step_dispatch.execute` pops it into `ActionContext.answer`). In memory, like the
        #: steering injection: a restart between the answer and that dispatch loses it, and the
        #: step then asks again rather than claiming an answer it cannot show.
        self._park_answers: dict[str, Any] = {}
        #: Run-scoped "always allow" decisions (WF2-R7). Cleared on rewind: remembering
        #: across one would auto-approve the very step the user rewound to reconsider.
        self._allow_memory = gate_policy.AllowMemory()
        #: Monotonic SSE sequence. Separate from the journal's `seq`: the journal counts
        #: persisted records, this counts published events, and conflating them would make a
        #: consumer's gap detection fire on every unpublished journal write.
        self._event_seq = 0
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
        by_path = {path: node for path, node in walk(self.root)}
        for path, inst in self.instances.items():
            if inst.state not in SUCCESS_STATES:
                continue
            node = by_path.get(spec_path(path))
            if node is None or not node.id:
                continue
            self._outputs[node.id] = store.read_output(self.run.id, path)

    # ── public lifecycle ──

    async def start(self) -> None:
        """Launch the tick loop as a background task.

        The terminal event is CLEARED here. It is set when a loop exits, so a controller
        restarted in place after a rewind (the run went terminal, a mutation reset part of
        it, work remains) would otherwise have `run_to_completion` return the previous
        run's status immediately without waiting for the new work.
        """
        if self._task and not self._task.done():
            return
        self._terminal.clear()
        self._task = asyncio.create_task(self._tick_loop())

    async def run_to_completion(self, *, timeout: float = 0.0) -> RunStatus:
        """Blocking mode: drive to terminal, drain the projection writes, return the status.

        The drain is load-bearing, not tidiness. Measured (S61g): a projected Task write is
        scheduled
        on the loop from the SYNC settle path, and returning at terminal left it pending — so a
        caller that awaited this and then closed its loop lost the board row entirely, with the run
        reporting
        `complete` and the ledger showing no `task_materialized`. The row is the user-
        visible half of
        running a workflow; dropping it silently is the worst available outcome.
        """
        await self.start()
        if timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._terminal.wait(), timeout=timeout)
        else:
            await self._terminal.wait()
        await self.drain_projection_writes()
        return self.run.status

    async def drain_projection_writes(self, *, timeout: float = 10.0) -> None:
        """Await the in-flight projected-Task writes.

        Bounded: a hung task store must not hold a finished run open forever.

        The pending writes are NOT cancelled on timeout. Measured (S61g): `asyncio.wait_for` cancels
        the awaitable it wraps, so the obvious `wait_for(gather(...))` spelling silently kills the
        very writes it was waiting for — and a cancelled write may ALREADY have created the task,
        which loses the id without undoing the row. Waiting on SHIELDED handles leaves the
        real tasks
        running, so the next projection rebuild (§1's normal path) still recovers them.
        """
        pending = [h for h in list(self._projection_writes) if not h.done()]
        if not pending:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*(asyncio.shield(h) for h in pending), return_exceptions=True),
                timeout=timeout,
            )

    async def wait_for_terminal(
        self,
        *,
        timeout: float = 0.0,
        progress_every: float = 2.0,
        on_progress: Any = None,
    ) -> RunStatus:
        """Blocking-mode wait that RETURNS when a human is needed.

        `run_to_completion` waits for the tick loop to exit. That is right for a run that
        will finish on its own, but a blocking chat tool must also return when the run parks
        on `needs_input`: nobody can answer the gate while the turn that would surface it is
        still blocked, so waiting for terminal there is a guaranteed deadlock — the tool
        holds the turn, the turn can't render the ask, the ask never gets answered.

        `on_progress` fires every `progress_every` seconds with the current node states, so
        the FE widget updates live during the tool's execution instead of showing nothing
        until the end.
        """
        await self.start()
        deadline = (time.monotonic() + timeout) if timeout > 0 else 0.0

        while True:
            if self._terminal.is_set():
                break
            # needs_input is a STOPPING point for a blocking caller, not a terminal state.
            # Returning here is what makes the ask reachable.
            if self.run.status == RunStatus.NEEDS_INPUT:
                break
            remaining = max(0.0, deadline - time.monotonic()) if deadline else 0.0
            if deadline and remaining <= 0:
                break
            wait = min(progress_every, remaining) if deadline else progress_every
            try:
                await asyncio.wait_for(self._terminal.wait(), timeout=wait)
                break
            except asyncio.TimeoutError:
                if on_progress is not None:
                    try:
                        on_progress(self.progress_snapshot())
                    except Exception:
                        # A broken observer must never affect the run it is watching.
                        logger.debug("blocking-mode progress callback failed", exc_info=True)
        return self.run.status

    def progress_snapshot(self) -> dict[str, Any]:
        """Node states for a live progress tick. Cheap: reads memory, not disk."""
        return {
            "run_id": self.run.id,
            "status": self.run.status.value,
            "tokens": self.run.total_tokens,
            "nodes": [
                {"instance_path": path, "state": inst.state.value}
                for path, inst in sorted(self.instances.items())
            ],
        }

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
            if store.cancel_requested(self.run.id):
                # A cancel that reached a loop which was NOT running — a paused run woken by
                # `service.cancel_run` to apply it. Honoured BEFORE `_prepare`, which would flip the
                # row back to RUNNING and journal a start for a run that is about to end.
                async with self._lock:
                    await self._cancel_inflight()
                    await self._finish(RunStatus.CANCELLED)
                return
            if not await self._prepare():
                # The run was refused before any node ran (a fatal `workspace:` declaration or a
                # contended named workspace). `_prepare` already wrote the terminal status through
                # `_finish`, so scheduling anything now would run nodes for a failed run.
                return
            while True:
                async with self._lock:
                    if await self._step():
                        break
                if self._inflight:
                    await self._await_progress()
                else:
                    delay = self._next_wake_delay()
                    if delay is None:
                        # A dispatched stage is live work with no awaitable and no deadline, so
                        # neither `_inflight` nor `_next_wake_delay` can report it — and the
                        # `sleep(0)` below then polls it as fast as the event loop allows.
                        # MEASURED on a single otherwise-idle run holding one RUNNING stage:
                        # 16737 `_step` calls and as many `SubagentManager.get` lookups in a 9.00s
                        # window (1859/s), 6.14s CPU over 9.00s wall = 68% of one core, for a
                        # subagent that will take minutes; with this delay, 3 calls and 0.19s CPU
                        # (2%, flat — the same figure a 3s window costs, i.e. setup, not ticks).
                        # `min()` is unnecessary: this delay IS `TICK_WAKE_SECS`, which
                        # is also `_next_wake_delay`'s clamp ceiling, so any deadline it reports
                        # is already the sooner of the two.
                        delay = self._dispatched_poll_delay()
                    if delay is None:
                        # No in-flight work, no scheduled wake, nothing being polled: the next
                        # _step call decides completion or deadlock. Yield rather than spin.
                        await asyncio.sleep(0)
                    else:
                        await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a controller crash must not leave a silent zombie
            logger.exception("workflow run %s: controller crashed", self.run.id)
            if _is_engine_install_fault(exc):
                # The ENGINE could not be imported, so this process never got far enough to
                # learn anything about the run. Writing FAILED here would be a verdict on the
                # run based on evidence about the installation — measured live: a gateway left
                # running from a deleted worktree adopted a healthy run, threw
                # `cannot import name 'provisioning' from 'personalclaw.workflows'`, and wrote
                # `failed` over work that then completed successfully seconds later under a
                # current process.
                #
                # Left untouched, the run stays RUNNING and is re-adopted on the next poll —
                # by a process whose code can actually import, which is the outcome that
                # matters. That is not an unbounded zombie: `audit.STALE_RUNNING_SECS` is the
                # existing backstop for a RUNNING run nobody is driving, and it reports the
                # run honestly instead of inventing a failure for it.
                logger.error(
                    "workflow run %s: left RUNNING — this process cannot import the engine "
                    "(%s). It is stale relative to the run's own state; a current process "
                    "will adopt it.",
                    self.run.id,
                    exc,
                )
            else:
                async with self._lock:
                    await self._finish(RunStatus.FAILED, error=f"engine error: {exc}"[:500])
        finally:
            self._terminal.set()

    async def _prepare(self) -> bool:
        """Pre-flight: pre-charge the budget from the ledger, stamp start, journal it.

        Returns False when the run was REFUSED before any node ran — today only a fatal
        `workspace:` declaration does that, and `run_start.provision_workspace` has already written
        the terminal status. The tick loop stops rather than scheduling into a workspace that could
        not be honored.
        """
        resumed = bool(self.run.started_at)
        totals = journal_mod.run_totals(self.run.id)
        # Budget pre-charge (WF2-R4 #1): a resumed run inherits its own spend. Without
        # this a crash loop mints a fresh budget each time and spends without bound.
        tokens_recorded = self._inherit_ledger_tokens(totals)
        token_cap = int(getattr(self.run.budget, "max_tokens", 0) or 0)
        if resumed and token_cap and not tokens_recorded:
            # UNKNOWN is not free. A capped resume whose earlier steps never recorded their token
            # count cannot be safely pre-charged, so pause before the first new node is scheduled.
            # Removing the cap remains the explicit way to continue when no measurement exists.
            await self._finish(
                RunStatus.PAUSED,
                error="token spend unrecorded; cannot pre-charge token budget",
            )
            return False
        # Context lifecycle (WF2-R6): rebuild handoffs/carryover/decisions from the ledger. This is
        # the whole reason they are journaled — a resumed run that lost them would restart its next
        # iteration blind, re-deriving what a previous one already verified, which is the exact
        # failure the mechanism exists to prevent.
        iteration_context.rehydrate_context(self)
        iteration_context.rehydrate_loop_progress(self)
        if resumed:
            stage_settlement.requeue_orphaned_stages(self)
        async with self._lock:
            if not self.run.started_at:
                self.run.started_at = now_stamp()
            self.run.status = RunStatus.RUNNING
            self._save_run()
        self.journal.run_started(
            self.run.workflow_name,
            inputs=self.run.inputs,
            spec_version=self.run.spec_version,
            resumed=resumed,
            owner_username=self.run.owner_username,
            origin_harness=self.run.origin_harness,
        )
        run_start.enforce_inherited_mode(self)
        provisioned = await run_start.provision_workspace(self)
        run_start.bind_project_memory_cwd(self)
        self._publish("workflow_run_update", {"status": self.run.status.value})
        return provisioned

    def _inherit_ledger_tokens(self, totals: dict[str, Any]) -> bool:
        """Carry a measured ledger token total onto the run row; never turn absence into zero.

        ``run.total_tokens`` is intentionally still an integer: it is incremented by live node
        results and persisted in the existing run row. The ledger aggregate's
        ``tokens_recorded`` sibling decides whether it is safe to overwrite that row from replay.
        False leaves the prior row untouched and lets the resume pre-charge fail closed when a
        token cap exists.
        """
        tokens_recorded = totals.get("tokens_recorded") is True
        tokens = totals.get("tokens")
        if not tokens_recorded or tokens is None:
            return False
        self.run.total_tokens = max(self.run.total_tokens, int(tokens))
        return True

    async def _step(self) -> bool:
        """One scheduling step under the lock. Returns True when the run is terminal.

        This is also the designated safe point for mid-flight mutation (Slice 4): the
        lock is held and no node is mid-launch.
        """
        if store.cancel_requested(self.run.id):
            await self._cancel_inflight()
            await self._finish(RunStatus.CANCELLED)
            return True

        # A sticky PAUSE, read at the same point as a cancel and for the same reason: an intent a
        # request handler recorded is applied by the ONE writer, on a step, with the lock held.
        # PAUSED is not terminal — the loop simply stops here, and `wake()` restarts it on resume.
        if store.pause_requested(self.run.id):
            await self._pause_inflight()
            await self._finish(RunStatus.PAUSED)
            return True

        # Mutations drain HERE — lock held, nothing mid-launch (WF2-R20 safety #1). Before
        # the frontier, so an applied edit is reflected in this step's scheduling rather
        # than a tick later.
        mid_flight.drain_mutations(self)

        self._wake_due_nodes()

        # A dispatched stage's subagent may have finished since the last step. Settled BEFORE
        # the watcher reap for the same reason the reap precedes the frontier: a stage
        # finishing IS the "accompanied work complete" a watcher is reaped for, so settling it
        # second would delay every reap by a tick.
        stage_settlement.reconcile_dispatched_stages(self)

        # Watchers are reaped BEFORE the frontier, so a reaped watcher is already terminal in
        # this step's derivation and the run completes on the same tick its work finished.
        # After the frontier it would take an extra tick, and on the last tick of a run,
        # never — the completion check would have already read the watcher as RUNNING.
        self._reap_watchers()

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

        # PP-12 admission: the two rules the frontier structurally cannot apply, because both need
        # a clock and one needs the disk. Skipped entirely for a spec that declares none of their
        # keys — the same code path as before this existed, which is what "additive" has to mean.
        admitted = await run_admission.admit_ready(self, fr.ready)
        if admitted is None:
            return True

        for item in admitted:
            if item.path in self._inflight:
                continue
            await self._launch(item)

        if fr.blocked and not self._inflight:
            await self._finish(RunStatus.FAILED, error=f"run deadlocked: {fr.block_reason}")
            return True

        if not self._inflight and not fr.ready and not fr.deferred:
            waiting = [p for p, i in self.instances.items() if i.state == InstanceState.WAITING]
            asks = [p for p in waiting if gate_answers.awaits_human(self, p)]
            # A step awaiting a HUMAN — a gate, or an action that stopped for one — surfaces as
            # needs_input IMMEDIATELY (WF2-R7): a run that parks quietly for 45s and only then
            # surfaces is a run nobody knows to answer. Surfacing and terminating are separate,
            # though — see below.
            for path in asks:
                gate_answers.ensure_continuation(self, path)
            if asks and self.run.status != RunStatus.NEEDS_INPUT:
                gate_answers.surface_needs_input(self)
            if waiting and self._next_wake_delay() is None:
                # Nothing will wake this run: no deadline, no in-flight work. NOW it is
                # terminal. With a deadline still pending the loop keeps ticking so the
                # unattended timeout can actually fire — a surfaced run is waiting, not
                # finished.
                await self._finish(RunStatus.NEEDS_INPUT)
                return True
        return False

    def resume(
        self,
        token: str,
        answer: Any,
        *,
        responder: str = "",
        channel: str = "",
        always_allow: bool = False,
    ) -> dict[str, Any]:
        """Answer a waiting gate. The out-of-band entry point (widget, inbox, HTTP, chat).

        The answer is VALIDATED before the token is consumed: rejecting afterwards would
        have already destroyed the token, leaving a dead link and an unanswered gate. Then
        the token is consumed ATOMICALLY, so a double-click or a retried POST cannot replay
        one approval into two actions.

        `channel` marks a REMOTE reply. A remote answer must come from the run's owner —
        without that binding, a shared channel is a privilege-escalation path where anyone
        who can type can approve someone else's deployment (WF2-R7).
        """
        from personalclaw.workflows.human_input import (
            Ask,
            consume_continuation,
            expired_item,
            load_continuation,
        )

        allowed, why = gate_policy.may_answer(self.run, responder=responder, channel=channel)
        if not allowed:
            # Checked BEFORE the token is touched, and deliberately terse: replying with
            # the gate's content to a shared channel would leak it to everyone in it.
            logger.info("workflow %s: refusing remote gate answer — %s", self.run.id, why)
            return {"ok": False, "code": "WF_RESUME_NOT_OWNER", "message": why}

        cont = load_continuation(self.run.id, token)
        if cont is None:
            return {"ok": False, "code": "WF_RESUME_UNKNOWN_TOKEN"}
        if cont.expired:
            consume_continuation(self.run.id, token)
            item = expired_item(cont)
            self._publish("workflow_needs_input", item)
            return {"ok": False, "code": "WF_RESUME_EXPIRED", "item": item}

        # The `revise` verb (UP): "change step 3, then carry on" — neither an approval nor a
        # rejection. Recognised HERE, alongside `validate_answer` and for the same reason: a revise
        # naming a step that does not exist must leave the token intact so the reviewer can correct
        # the name, and a check placed after the claim would have destroyed it already.
        revise = gate_answers.parse_revise(answer)
        if revise is not None:
            step_ref, comment = revise
            return gate_answers.resume_revise(
                self, cont, token, step_ref, comment, responder=responder, channel=channel
            )

        ask = Ask.from_dict(cont.ask)
        problem = ask.validate_answer(answer)
        if problem:
            # Validated BEFORE consuming: the token survives so the user can correct it.
            return {"ok": False, "code": "WF_RESUME_INVALID_ANSWER", "message": problem}

        claimed = consume_continuation(self.run.id, token)
        if claimed is None:
            # Another resume won the race. Exactly one answer applies.
            return {"ok": False, "code": "WF_RESUME_ALREADY_USED"}

        inst = self._instance(cont.instance_path)
        if inst.epoch != cont.epoch:
            # The node was rewound under the token: applying it would land the answer in
            # the wrong epoch, which is worse than refusing.
            return {"ok": False, "code": "WF_RESUME_STALE_EPOCH"}

        filled = ask.apply_defaults(answer)
        approved = gate_answers.is_approved(ask, filled)
        if approved and always_allow and not ask.rerun:
            # Run-scoped, keyed by (operation, target) — and cleared on rewind, so it can
            # never auto-approve a step the user rewound to reconsider. Never for a step that
            # parked (`ask.rerun`): what it waits for is a person's act, which no remembered
            # answer can perform the next time it parks.
            node = dict(walk(self.root)).get(spec_path(cont.instance_path))
            self._allow_memory.remember(node.config if node else {}, cont.node_id)
        inst.wake_at = 0.0
        if ask.rerun:
            gate_answers.settle_parked_step(self, cont, inst, approved=approved, answer=filled)
        elif approved:
            inst.state = InstanceState.DONE
            ref, preview = self.journal.store_output(
                cont.instance_path, {"answer": filled, "approved": True}
            )
            inst.output_ref = ref
            if cont.node_id:
                self._outputs[cont.node_id] = preview
            inst.completed_at = now_stamp()
        else:
            inst.state = InstanceState.FAILED
            inst.failure = Failure(
                failure_class=FailureClass.USER,
                cause_plain="the gate was denied",
                remediation="adjust the work the gate rejects, then re-run from this node",
                terminal_reason="denied",
            )
            inst.completed_at = now_stamp()
        # The resolution half. AFTER the claim is won and the epoch verified, so the ledger records
        # answers that actually applied — emitting before the claim would log an approval for a race
        # the caller lost, and the audit would show two people approving one gate.
        self.publish_confirmation_resolved(
            cont.instance_path,
            cont.node_id,
            confirmation_id=gate_answers.stable_confirmation_id(
                self.run.id, cont.node_id or cont.instance_path, cont.epoch
            ),
            verb="approve" if approved else "reject",
            approved=approved,
            resolved_by=responder or channel or "dashboard",
        )
        self.journal.write(
            journal_mod.GATE_RESOLVED,
            instance_path=cont.instance_path,
            node_id=cont.node_id,
            epoch=cont.epoch,
            approved=approved,
            answer=filled,
            # §4.4 human-attention accounting: how long this ask held human attention,
            # explicit rather than re-derived from the continuation record. Only the
            # human path carries it — an auto-approved gate cost no attention.
            resolved_after_secs=(
                round(max(0.0, time.time() - cont.created_at), 3) if cont.created_at else 0.0
            ),
        )
        # Judge/human divergence → Run Ledger (LOOPS-EVOLUTION R3). If this gate had a judge
        # verdict and the human's decision contradicts it, record the direction: a judge that
        # PASSed work a human then rejected is a `false_pass`; a judge that REJECTed work a human
        # then approved is a `false_reject`. This is the calibration signal — a verdict that
        # followed a human override is not clean evidence about the template, and without the event
        # a refiner cannot tell the difference (see judge_calibration.DivergenceRecord).
        gate_answers.emit_judge_divergence(self, cont.instance_path, cont.node_id, approved)
        self.run.attention = None
        # The run has work again, so it is no longer surfaced as blocked. Written BEFORE the
        # loop restarts so a status read between the two never reports a stale needs_input.
        if self.run.status == RunStatus.NEEDS_INPUT:
            self.run.status = RunStatus.RUNNING
        self._persist_state()
        self._save_run()
        self._publish(
            "workflow_gate_resolved",
            {"node_id": cont.node_id, "instance_path": cont.instance_path, "approved": approved},
        )
        # Close the inbox row this gate raised. A row that outlives its gate is worse than no
        # row: the user opens it, finds nothing to answer, and stops trusting the surface.
        # Scoped to the node, so a run with two concurrent gates keeps the other one open.
        attention.resolve_gate_item(self.services.attention_state, self.run.id, cont.node_id)
        self._publish("workflow_run_update", {"status": self.run.status.value})
        # RESTART the tick loop. Answering a gate is the ONLY way a needs_input run gets work
        # again, and the loop that would schedule it has already exited — without this the
        # answer lands, the node flips DONE, and the run sits there forever with its
        # downstream nodes never launched. Found by driving the real UI: every unit test
        # called `run_to_completion` by hand afterwards and so never saw it.
        self._resume_loop()
        return {"ok": True, "approved": approved, "node_id": cont.node_id}

    def _resume_loop(self) -> None:
        """Relaunch the tick loop if it has exited and the run is not terminal.

        Scheduled rather than awaited: `resume` is called from a request handler, and
        blocking that handler until the run finishes would turn every approval into a
        long-poll.
        """
        if self.run.is_terminal:
            return
        if self._task is not None and not self._task.done():
            return  # still running — it will pick the woken node up on its next tick
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (a sync caller in a test): the watchdog adopts the run instead.
            logger.debug("workflow %s: no running loop to resume on", self.run.id)
            return
        self._terminal.clear()
        self._task = asyncio.create_task(self._tick_loop())

    # ── mid-flight mutation (WF2-R2 / R20) ──

    def submit_mutation(
        self,
        raw_ops: list[dict[str, Any]],
        *,
        actor: str = "user",
        confirm: bool = False,
        expect_version: int | None = None,
    ) -> dict[str, Any]:
        """Validate a batch and QUEUE it; the tick loop applies it.

        Returns the preview and issues synchronously — a caller needs to see the cascade
        before it lands, and a batch that cannot pass validation should not reach the queue
        at all. Nothing here writes run state: this is a handler, and handlers request
        while the loop decides (WF2-R10).

        A cascade that re-runs completed work needs `confirm=True`. Without the gate, a
        one-line prompt edit could silently re-run (and re-bill) a dozen finished stages.

        Queued means APPLIED AT THE NEXT TICK, so two runs have to be dealt with here:

        * a finished run is one attempt and is never re-entered — a retry is a fork — so its
          edit is refused with that sentence. Its controller stays registered until the
          supervisor's next poll, and in that window a batch used to answer `queued: true` and
          sit forever in a queue no loop would drain;
        * a run parked on a question has no tick loop (`_step` finished it `needs_input` and the
          loop exited, since nothing but an answer wakes it), so the queue is drained by WAKING
          the loop, the way a cancel wakes it. A rewind confirmed at a gate otherwise waited for
          as long as the gate did. Waking is a request, not a write: the loop applies the batch
          at its drain point, as for a running run. A PAUSED run is not woken — its next step
          would pause again before draining — so its edit applies when it is resumed, the
          pause → edit → resume the run page offers.
        """
        if self.run.is_terminal:
            message = (
                f"run is already {self.run.status.value} — a finished run is not edited; fork "
                "it to run it again"
            )
            return {
                "ok": False,
                "code": "WF_RUN_ALREADY_TERMINAL",
                "message": message,
                "issues": [{"code": "WF_RUN_ALREADY_TERMINAL", "message": message, "node_id": ""}],
                "preview": mutations.CascadePreview().to_dict(),
            }
        if expect_version is not None and int(expect_version) != int(self.run.spec_version):
            return {
                "ok": False,
                "issues": [
                    {
                        "code": "WF_MUT_VERSION_MISMATCH",
                        "message": (
                            f"spec is at version {self.run.spec_version}, not "
                            f"{expect_version} — refetch and reapply"
                        ),
                        "node_id": "",
                    }
                ],
                "preview": mutations.CascadePreview().to_dict(),
            }

        result = mutations.prepare_batch(raw_ops, self.spec, self.instances, effects=self._effects)
        body = result.to_dict()
        if not result.ok:
            return body
        if result.preview.needs_confirmation and not confirm:
            body["ok"] = False
            body["needs_confirmation"] = True
            body["code"] = "WF_MUT_CONFIRM_REQUIRED"
            body["message"] = (
                "this batch re-runs completed nodes "
                f"({', '.join(result.preview.rerun[:5])}); resubmit with confirm_cascade=true"
            )
            body["issues"] = [
                {
                    "code": "WF_MUT_CONFIRM_REQUIRED",
                    "message": body["message"],
                    "node_id": "",
                }
            ]
            return body
        self._pending_mutations.append((result, actor))
        body["queued"] = True
        if self.run.status == RunStatus.NEEDS_INPUT:
            self._resume_loop()
        return body

    def _skip(self, path: str) -> None:
        """Mark a whole subtree skipped. The subtree matters: skipping only the case root
        would leave its children pending, and a derived container state would then read
        the branch as unfinished forever."""
        node = dict(walk(self.root)).get(spec_path(path))
        paths = [path]
        if node is not None:
            paths = [
                path if sub == "root" else f"{path}{sub[len('root'):]}" for sub, _n in walk(node)
            ]
        for target in paths:
            inst = self._instance(target)
            if inst.state in TERMINAL_STATES:
                continue
            inst.state = InstanceState.SKIPPED
            inst.completed_at = now_stamp()
            self.journal.step_skipped(
                target, node.id if node else "", epoch=inst.epoch, actor="engine"
            )
        self._persist_state()

    def _dispatched_poll_delay(self) -> float | None:
        """How long the idle tick branch may sleep while out-of-band work is live, or None.

        `TICK_WAKE_SECS` — the SAME bound in-flight work gets from `_await_progress`'s
        `asyncio.wait(timeout=...)`, because a spawned subagent is in-flight work that merely
        does not live in `_inflight`. Not a new knob: a second constant here would be a second
        answer to "how often does this run re-derive its frontier".

        The cost is stated plainly, because it is not symmetric with `_await_progress`: there,
        `TICK_WAKE_SECS` is only a CEILING (a finished task wakes the wait immediately), while a
        polled subagent has no such signal, so this is real settle LATENCY — up to five seconds
        per stage, measured at 5.33s for a stage whose fake finished one lookup in. Against a
        stage that takes minutes that is under 2%, and buying it back means a second writer
        nudging the controller from the completion side, which is exactly what WF2-R10 (`_apply`
        is the only place a node reaches terminal) exists to prevent.

        Deliberately NOT folded into `_next_wake_delay`, even though that is where a deadline
        belongs. `_step` (:926) uses that method as the "nothing will wake this run" oracle for
        parking a gated run at NEEDS_INPUT, so teaching it about a live subagent would change
        WHEN a run parks — a lifecycle rule, not a cadence fix. That oracle's blind spot (a run
        holding both an unanswered gate and a live stage parks as NEEDS_INPUT and orphans the
        stage) is real and stays open; it is a policy call about what a parked run owes its
        in-flight workers, not something to settle inside a spin fix.
        """
        if not stage_settlement.awaiting_out_of_band_work(self):
            return None
        return TICK_WAKE_SECS

    def _reap_watchers(self) -> None:
        """Stop `until_cancelled` watchers whose accompanied work has finished.

        CANCELLED, not DONE: the watcher did not reach a natural end, and recording it as a
        success would make a run that was cut short indistinguishable from one that finished
        its cadence. But it also must not fail the run — being reaped is the DESIGNED end of
        a watcher, so `container_outcome` under `join: any` reads a cancelled watcher
        alongside a succeeded worker as DONE.

        A reaped watcher's in-flight body node is cancelled too. Without that, a watcher
        parked in a 5-minute `wait` would keep the run alive for the rest of that wait after
        its reason to exist was already gone.
        """
        paths = reap_watchers(
            self.root, {p: i.state for p, i in self.instances.items()}, iterations=self._iterations
        )
        if not paths:
            return
        for path in paths:
            inst = self._instance(path)
            if inst.state in TERMINAL_STATES:
                continue
            inst.state = InstanceState.CANCELLED
            inst.completed_at = now_stamp()
            node = dict(walk(self.root)).get(spec_path(path))
            node_id = node.id if node else ""
            self.journal.write(
                journal_mod.WATCHER_REAPED,
                instance_path=path,
                node_id=node_id,
                iterations=int(self._iterations.get(path, 0)),
                reason="accompanied_work_complete",
            )
            for sub in [p for p in self.instances if p.startswith(f"{path}.")]:
                sub_inst = self.instances[sub]
                if sub_inst.state in TERMINAL_STATES:
                    continue
                sub_inst.state = InstanceState.CANCELLED
                sub_inst.completed_at = now_stamp()
            for entry in [
                e for p, e in self._inflight.items() if p == path or p.startswith(f"{path}.")
            ]:
                entry.task.cancel()
            for key in [p for p in self._inflight if p == path or p.startswith(f"{path}.")]:
                self._inflight.pop(key, None)
            self._publish(
                "workflow_node_done",
                {
                    "node_id": node_id,
                    "instance_path": path,
                    "status": InstanceState.CANCELLED.value,
                    "degraded_reason": "watcher_reaped",
                },
            )
        self._persist_state()

    def _frontier(self) -> Frontier:
        states = {p: i.state for p, i in self.instances.items()}
        fr = frontier(
            self.root,
            states,
            limits=self.services.lane_limits,
            declined_edges=self._declined_edges,
            outputs=self._outputs,
            inputs=self.run.inputs,
            iterations=self._iterations,
            running_lanes=self._running_lanes(),
            # WIP=1 (LOOPS-EVOLUTION R5b). Read from the spec's `runtime_hints.execution`
            # every tick rather than cached at construction, because a mid-flight spec edit
            # can turn the invariant on and a cached flag would keep scheduling under the
            # old rule while the template said otherwise.
            single_active_feature=execution_hints.from_runtime_hints(
                self.spec.get("runtime_hints")
            ).single_active_feature,
        )
        self._journal_wip_holds(fr)
        self._journal_collected_items(states)
        return fr

    def _journal_wip_holds(self, fr: Frontier) -> None:
        """Record a WIP=1 refusal once per held item (R5b).

        Written to the ledger because a refusal nobody can read is indistinguishable from a
        scheduler that lost the item — "why has feature 2 not started?" has to be answerable
        from the run's own record. Deduped by path: the frontier re-derives every tick, and
        one held item would otherwise write a record per tick for as long as it waits.
        """
        for path in fr.wip_held:
            if path in self._wip_logged:
                continue
            self._wip_logged.add(path)
            self.journal.write(
                journal_mod.DECISION,
                instance_path=path,
                node_id="",
                decision="wip_limit_held",
                detail=(
                    "single_active_feature is declared: this item was not started while "
                    "another item of the same fan-out is still in flight"
                ),
            )

    def _journal_collected_items(self, states: dict[str, InstanceState]) -> None:
        """Write each `on_item_error: collect` fan-out's per-item failures once it is terminal.

        This is the DATA half of COLLECT (WV-13). The outcome half — run every item, then let the
        failures fail the run — lives in `tick.foreach_outcome`; on its own it produces a FAILED
        run whose reader has to know a fan-out's item-path shape to work out WHICH items broke.

        Journaled rather than published as the container's `output`, and that is a deliberate
        NON-invention: containers have no output surface at all. `self._outputs` is keyed by node
        id and written only where a LEAF completes (a dispatch result, a resolved `wait`, an
        answered gate), and a container deliberately has no stored instance — its state is always
        derived, so a rewind cannot leave a stale verdict behind. Publishing under the foreach's
        node id would make `{{nodes.<foreach>.output}}` resolve in memory and then resolve to
        nothing after a restart, because rehydration reads `inst.output_ref` and there is no
        instance to read. A ledger record is where this run already keeps per-node truth.

        Every collect fan-out is re-examined each tick because a container's state is derived,
        never stored: there is no "it just became terminal" edge to hook. The spec is re-walked
        rather than scanned once at construction for the same reason `_frontier` re-reads the WIP
        hint every tick — a mid-flight mutation can add a fan-out. Deduped by `path@epoch`, so a
        rewound-and-re-run fan-out gets a second, honest record.
        """
        for path, node in walk(self.root):
            if node.kind != NodeKind.FOREACH:
                continue
            if item_error_policy(node) != ItemErrorPolicy.COLLECT:
                continue
            if self._items_collected is None:
                # Seeded on the first CANDIDATE, not on the first tick: the overwhelming majority
                # of runs contain no collect fan-out at all, and they must not pay a ledger read
                # to discover that.
                self._items_collected = {
                    str(rec.get("instance_path", "")) + "@" + str(rec.get("epoch", 0))
                    for rec in journal_mod.ledger(self.run.id, kinds={journal_mod.ITEMS_COLLECTED})
                }
            key = f"{path}@{self._run_epoch()}"
            if key in self._items_collected:
                continue
            outcome = derive_state(
                node,
                path,
                states,
                declined_edges=self._declined_edges,
                outputs=self._outputs,
                inputs=self.run.inputs,
                iterations=self._iterations,
            )
            if outcome not in TERMINAL_STATES:
                continue
            self._items_collected.add(key)
            failures = self._item_failures(path)
            if not failures:
                continue  # a clean fan-out has nothing to collect
            self.journal.items_collected(
                path,
                node.id,
                epoch=self._run_epoch(),
                outcome=outcome.value,
                failures=failures,
            )

    def _item_failures(self, container_path: str) -> list[dict[str, Any]]:
        """Every failed instance inside one fan-out, in item order.

        Read off the instances rather than off the ledger: the instance IS the run's durable
        per-node state, and it already carries the typed `Failure` and the `item_label` that
        makes an entry name its item ("auth.py") instead of an index nobody can resolve back to
        a value. A nested container inside the body contributes its failing leaves under the
        same item index, which is the right attribution — the item failed because they did.
        """
        prefix = f"{container_path}.body#"
        out: list[dict[str, Any]] = []
        by_path = dict(walk(self.root))
        for path in sorted(self.instances):
            inst = self.instances[path]
            if not path.startswith(prefix) or inst.state != InstanceState.FAILED:
                continue
            index = path[len(prefix) :].split(".", 1)[0]
            if not index.isdigit():
                continue
            node = by_path.get(spec_path(path))
            out.append(
                {
                    "item_index": int(index),
                    "item_label": inst.item_label,
                    "instance_path": path,
                    "node_id": node.id if node else "",
                    "failure_class": (inst.failure.failure_class.value if inst.failure else ""),
                    "cause": inst.failure.cause_plain if inst.failure else "",
                }
            )
        out.sort(key=lambda entry: (entry["item_index"], entry["instance_path"]))
        return out

    def _running_lanes(self) -> dict[str, int]:
        used: dict[str, int] = {}
        for entry in self._inflight.values():
            used[entry.ready.lane] = used.get(entry.ready.lane, 0) + 1
        return used

    # ── launching ──

    async def _launch(self, item: ReadyNode) -> None:
        """Dispatch one node, serving a cache hit when the journal has one."""
        ctx = node_bindings.context_for(self, item)
        resolved_view = node_bindings.resolved_inputs(self, item, ctx)
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
            inst.completed_at = now_stamp()
            # …and on the INSTANCE, not only in the ledger. The `step_cached` event below is the
            # durable record, but a status read would have to scan the whole ledger to answer
            # "was this row cached?" — so the projection is stamped here, one of the two places
            # that decide a node's outcome-origin (the fresh dispatch below is the other).
            inst.cached = True
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
                    "node_epoch": inst.epoch,
                    "cached": True,
                },
            )
            return

        if not await effect_boundary.effect_preflight(self, item, inst):
            return

        inst.state = InstanceState.RUNNING
        inst.started_at = now_stamp()
        inst.attempt += 1
        # The other half of the cache-origin stamp. Cleared here rather than at each of the six
        # rewind reset sites: every path to a terminal state runs through this dispatch, so a
        # re-run after a rewind cannot leave the previous epoch's `cached` behind.
        inst.cached = False
        # The declared-schema notice (#3545) is per ATTEMPT for the same reason. A spawned stage
        # settles out of band through several paths and only its DONE path sets it, so a rewound
        # stage that then fails must not keep the previous attempt's notice on its row.
        inst.schema_shortfall = ""
        inst.model_substituted = []
        if item.has_item and not inst.item_label:
            # Stamped once, at first launch. The items list is re-resolved from a binding on
            # every tick, so after an upstream output changes the label would be unrecoverable
            # — and a retry must show the item it originally got, not whatever now sits at that
            # index.
            inst.item_label = _item_label(item.item)
        if item.item_total:
            # The fan-out's denominator, from the frontier's own `len(items)`. Stamped on EVERY
            # dispatch rather than once, unlike the label: the label must keep the item it
            # originally got, whereas the denominator must describe the list the engine is
            # iterating NOW — so a rewind over a shorter list re-stamps what it re-runs instead of
            # leaving a wider total behind. Persisting it is what lets `service._nodes_of` report
            # the same number as the `workflow_node_started` event below (#3403).
            inst.item_total = int(item.item_total)
        if node_commits_effects(item.node):
            # ATTEMPTED goes down BEFORE dispatch: a crash between here and the outcome
            # must leave evidence the effect MAY have fired (WF2-R1).
            effect_boundary.record_effect(self, item.node, item.path, inst, EffectStatus.ATTEMPTED)
        self._persist_state()
        self.journal.step_started(item.path, item.node.id, epoch=inst.epoch, lane=item.lane)
        self._publish(
            "workflow_node_started",
            {
                "node_id": item.node.id,
                "instance_path": item.path,
                # The NODE's epoch, under the node key. Publishing it as `epoch` would
                # override the envelope's RUN epoch and make this event look superseded to a
                # consumer whose folded epoch came from a rewound sibling — `node_started`
                # and `node_done` for the same node would then disagree about the run.
                "node_epoch": inst.epoch,
                # Per-item foreach context (WF2-R5): what a "[3/12] refactor auth.py" row
                # needs. A fan-out of twelve otherwise renders as twelve identical rows
                # distinguishable only by an index suffix — technically correct and useless
                # for telling which item is stuck.
                **self._item_context(item),
            },
        )

        now = time.time()
        # The task COPIES the context at creation, so a log bound around `create_task` is the
        # dispatch's own: every guarded model call it makes, however deep, is recorded there.
        with capture_model_calls() as calls:
            task = asyncio.create_task(step_dispatch.execute(self, item, ctx))
        self._inflight[item.path] = _InFlight(
            task=task, ready=item, started=now, last_progress=now, cache_key=key, calls=calls
        )

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
            liveness.enforce_stall_timeouts(self)
            for path, entry in list(self._inflight.items()):
                if not entry.task.done():
                    continue
                self._inflight.pop(path, None)
                try:
                    result = entry.task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    from personalclaw.workflows.failure_taxonomy import classify_exception

                    result = NodeResult(state=InstanceState.FAILED, failure=classify_exception(exc))
                self._apply(entry, result)
            self._persist_state()
            # `_apply` writes the RUN ROW as well as instance state — `total_tokens` (:3331) and
            # `agent_count` (the RUNNING branch) both live there — and `_persist_state` cannot see
            # either, so without this flush a live run's usage counters stayed in memory until some
            # unrelated caller happened to save. `service.status()` reads the store, so that is the
            # difference between a running run showing its spend and showing zero.
            self._save_run()

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
            # A spawned stage: `dispatch_stage` returns as soon as the subagent is live
            # (`engine.py:777`), so this node's real completion arrives out of band and
            # `stage_settlement.reconcile_dispatched_stages` settles it. The old comment here said
            # "the watchdog reconciles it" — it did not: `watchdog._reap_if_finished` reaps at the
            # RUN level and requires every instance to be terminal already, so it could never
            # be what moved an instance off RUNNING.
            #
            # The subagent id goes on the INSTANCE, for the reason `wake_at` does: it is
            # persisted with run state, so a restart that re-adopts this run can still ask who
            # was doing the work. Stashing it in `_outputs` (as this branch used to) was wrong
            # twice — that dict is keyed by NODE id, so a `foreach` fan-out of stages collided
            # under `setdefault` and kept only the first leaf's id, and it put an engine
            # internal into the namespace a downstream `{{nodes.X}}` binding reads.
            #
            # The execution CLAIM rides along for the same reason and is released by the same
            # settle (#3533): `dispatch_stage` takes it before the spawn and cannot give it back,
            # because the spawn is still live when it returns. Recorded here rather than
            # re-derived, because the holder is a fresh uuid per attempt.
            inst.state = InstanceState.RUNNING
            if isinstance(result.output, dict):
                spawned = str(result.output.get("subagent_id", "") or "")
                # 🔴 `run.agent_count`'s ONLY writer. Before this the field had exactly one
                # assignment in the tree — `WorkflowRun.from_dict` reading its own persisted zero
                # (`models.py:1146`) — so it was a declared column, a `to_dict` key and a SQLite
                # DEFAULT 0 that nothing ever incremented. Every run ever recorded reports
                # `agent_count: 0`, which is why the owner's eight-node run did.
                #
                # Counted at the SPAWN, not at the settle, and that is the whole reason it lives in
                # this branch rather than in `stage_settlement.reconcile_dispatched_stages` beside
                # the token roll-up: a run holding three live subagents must not report zero agents
                # while they work. `dispatch_stage` is the only dispatcher that can reach here — the
                # `ast` rail in `test_workflows_stage_completion` pins RUNNING to it — so this is
                # once per subagent the run actually started.
                #
                # Gated on the id CHANGING, so it is exactly-once per distinct child: a re-applied
                # RUNNING result (or a retry that re-dispatches the same node) must not inflate the
                # count, and a re-adopted run whose instance already carries the id adds nothing.
                if spawned and spawned != inst.subagent_id:
                    self.run.agent_count += 1
                inst.subagent_id = spawned
            inst.claim_target = result.claim_target
            inst.claim_holder = result.claim_holder
            return

        if result.state == InstanceState.WAITING:
            # Gate policy first (WF2-R7): an unattended run auto-approves low-risk gates so
            # it is actually unattended, and a remembered "always allow" honours a decision
            # the user already made. A DESTRUCTIVE gate still asks — an unreviewed
            # destructive action is worse than a stalled run.
            #
            # GATES only. The policy answers the question a gate asks; a `wait` is parked on the
            # clock and an ACTION that parked is waiting for a person to do something (sign in,
            # raise a budget) that no policy can do for them. Applied to every WAITING result, it
            # marked a `risk: caution` browse step in a scheduled run done with `approved: true`
            # for a sign-in nobody made.
            verdict = (
                gate_policy.decide(
                    item.node.config or {},
                    item.node.id,
                    origin_kind=self.run.origin.kind,
                    mode=self.run.mode,
                    memory=self._allow_memory,
                )
                if item.node.kind == NodeKind.GATE
                else None
            )
            if verdict is not None and verdict.approved:
                inst.state = InstanceState.DONE
                inst.completed_at = now_stamp()
                ref, preview = self.journal.store_output(
                    item.path, {"approved": True, "auto": verdict.decision.value}
                )
                inst.output_ref = ref
                if item.node.id:
                    self._outputs[item.node.id] = preview
                self.journal.write(
                    journal_mod.GATE_RESOLVED,
                    instance_path=item.path,
                    node_id=item.node.id,
                    epoch=inst.epoch,
                    approved=True,
                    answer={"auto": True},
                    policy=verdict.to_dict(),
                )
                self._publish(
                    "workflow_gate_resolved",
                    {
                        "node_id": item.node.id,
                        "instance_path": item.path,
                        "approved": True,
                        "policy": verdict.to_dict(),
                    },
                )
                return
            inst.state = InstanceState.WAITING
            # Wait-entry edge activation (WF2-R18): registering at entry rather than
            # completion is what stops a fan-out's join firing on its fast leg alone.
            self._decline(inst, result.declined_edges)
            # The deadline goes on the INSTANCE so it is persisted with run state. A
            # memory-only deadline is lost on restart, and every waiting run then parks
            # forever with nothing scheduled to wake it.
            inst.wake_at = float(result.wake_at or 0.0)
            if item.node.kind == NodeKind.ACTION:
                # An action that PARKED keeps what it produced (browse's notes, the sign-in
                # handoff's card) and states why it stopped. Both persist with the step, so the
                # run page, `ensure_continuation`'s card and the answer that runs it again all read
                # the same record. Not bound downstream: WAITING is not a success state.
                inst.output_ref = self.journal.store_output(item.path, result.output)[0]
                inst.degraded_reason = result.degraded_reason
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

        # What this attempt's model calls used, for the row that ends it and the run's charge —
        # whichever way it ended, a retried attempt included (`step_usage`).
        usage = measured(entry.calls, estimate=result.tokens)
        inst.model_substituted = list(usage.substitutions)  # "ran on X instead of Y", for the row
        # Retry, when the failure class says it is worth spending on. The attempt is
        # RECORDED before the retry so the next one can be corrected rather than blind —
        # a blind retry re-sends the same prompt and reproduces the same failure.
        if result.state == InstanceState.FAILED:
            failure = result.failure or Failure()
            record = attempt_from_failure(
                inst.attempt, failure, tokens=usage.billable(result.tokens), duration_secs=duration
            )
            self._attempts.setdefault(item.path, []).append(record)
            if self._should_retry(item, inst, result):
                if node_commits_effects(item.node):
                    # Same epoch, same idempotency key: the receiver can dedupe. The
                    # RETRIED record keeps the ledger honest about how many dispatches
                    # the external system may have seen.
                    effect_boundary.record_effect(
                        self, item.node, item.path, inst, EffectStatus.RETRIED
                    )
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
                    usage=usage,
                    attempt=inst.attempt,
                    retries_exhausted=False,
                )
                self.run.total_tokens += usage.billable(result.tokens)
                return

        inst.state = result.state
        inst.completed_at = now_stamp()
        inst.degraded_reason = result.degraded_reason
        # What this node's declared `schema` asked for and did not get (#3545). Carried onto the
        # instance beside `degraded_reason` rather than folded into it: a shortfall is not a
        # degradation — the node did its work and produced an output the run goes on to use — and
        # reusing that field would flip the row's rendering and lose the distinction.
        inst.schema_shortfall = result.schema_shortfall
        # A retry cannot run while the provider's breaker is open: record when it can.
        inst.failure = with_breaker_window(result.failure, entry.calls.providers)
        inst.tokens = usage.billable(result.tokens)
        self._decline(inst, result.declined_edges)

        # An action provider may ASK rather than finish (WF2-R7). Checked before the
        # success bookkeeping: a clarification is not an answer, and recording it as a
        # completed output would let a downstream binding consume the question.
        if item.node.kind == NodeKind.ACTION and result.state in SUCCESS_STATES:
            ask = gate_policy.clarification_from_output(result.output)
            if ask is not None:
                inst.state = InstanceState.WAITING
                inst.completed_at = None
                ask.setdefault("node_id", item.node.id)
                self.run.attention = ask
                self._publish(
                    "workflow_attention",
                    {"node_id": item.node.id, "kind": ask.get("kind"), "ask": ask},
                )
                return

        effect_boundary.record_terminal_effect(
            self, item.node, item.path, inst, result.state, result.output
        )

        if item.node.kind == NodeKind.SUBWORKFLOW and isinstance(result.output, dict):
            child_id = str(result.output.get("child_run_id", "") or "")
            if child_id:
                # The genealogy link, in the LEDGER (WF2-R13) — written whether the child
                # succeeded or not, because "which child run did this node spawn?" is exactly the
                # question a failed nesting raises. The run row's `parent_run_id` records the same
                # edge, but only the ledger says WHICH NODE spawned it, which is what a rewind of
                # that node needs in order to know what it is invalidating.
                self.journal.child_run_attach(self.run.id, child_id, item.node.id)

        # Judge gate verdict → Run Ledger (LOOPS-EVOLUTION R3, criterion 3). A judge gate's
        # NodeResult carries `judge_evidence` (see engine.dispatch_gate); emit here at the settle,
        # for BOTH pass and reject, so a refiner reading the ledger sees every judge call with its
        # evidence chain and discard status — and criterion 3 ("judges reject at least once with
        # evidence over parity runs") is provable from the ledger rather than only from tests.
        out = result.output if isinstance(result.output, dict) else {}
        if "judge_evidence" in out:
            self.journal.write(
                journal_mod.JUDGE_VERDICT,
                instance_path=item.path,
                node_id=item.node.id,
                epoch=inst.epoch,
                template=str(self.run.workflow_name or ""),
                verdict=out.get("verdict", ""),
                status=out.get("judge_status", "kept"),
                evidence=out.get("judge_evidence", {}),
            )

        # Review findings → Run Ledger (EXECUTION-ISOLATION §7, EI-9). ANY stage whose output
        # carries `findings` is a review-producing stage — the `{{block:finding-record}}` prompt
        # contract is what makes it one, not the node kind, so `audit-sweep`'s parallel infer nodes
        # and a judge gate both land here without either declaring anything new.
        #
        # Emitted RAW (the reviewer's own claimed `location`), one row per finding. Anchor
        # validation deliberately does NOT happen here: §7 requires findings validated against the
        # ACTUAL diff "before render", and the diff at settle time is not the diff the human will
        # be looking at — the worker keeps working. Validating once at emit and storing the verdict
        # would bake in an answer that goes stale, which is the exact wrong-line failure the
        # validation exists to prevent. The panel re-anchors on every read.
        # A `findings` key in a DICT output, or a JSON string that mentions one — an `infer` node
        # returns its JSON as text, and gating on dicts alone would make the emit fire for
        # `transform` nodes and stay silent for the LLM reviewers §7 is actually about. The cheap
        # substring test comes first so a megabyte of prose is not JSON-parsed on every settle.
        raw_out = result.output
        if isinstance(raw_out, dict) or (isinstance(raw_out, str) and '"findings"' in raw_out):
            for finding in review_triage.parse_findings(
                raw_out, run_id=self.run.id, node_id=item.node.id or item.path
            ):
                self.journal.write(
                    journal_mod.REVIEW_FINDING,
                    instance_path=item.path,
                    node_id=item.node.id,
                    epoch=inst.epoch,
                    template=str(self.run.workflow_name or ""),
                    **finding.to_dict(),
                )

        if result.state in SUCCESS_STATES:
            ref, preview = self.journal.store_output(item.path, result.output)
            inst.output_ref = ref
            if item.node.id:
                self._outputs[item.node.id] = preview
            # The run row keeps the dispatcher's estimate as a FLOOR when the provider reported no
            # usage — a budget must still see the spend — while the ledger says "not recorded".
            self.run.total_tokens += int(inst.tokens)
            self.journal.step_completed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                cache_key=entry.cache_key.to_str(),
                state=result.state,
                duration_secs=duration,
                tokens=usage.tokens,
                retries=max(0, inst.attempt - 1),
                model=usage.model,
                provider=usage.provider,
                cost_usd=usage.cost_usd,
                model_calls_open=usage.calls_cut_off,
                degraded_reason=result.degraded_reason,
                # The prompt the PROVIDER received, with the fact of a substitution beside it
                # (#3166). `result.resolved_prompt` is post-scan since the dispatcher reads it back
                # from `guardrails.wire`, so what gets persisted is what the redactor produced —
                # the record and the wire agree. Nothing re-scans here: `redact_credentials` is not
                # idempotent over a composed line, so a second pass at the recording seam could
                # garble the very text it was meant to protect.
                resolved_prompt_ref=node_bindings.store_prompt(
                    self, item.path, result.resolved_prompt
                ),
                resolved_prompt_redacted=result.prompt_redacted,
                resolved_prompt_scan=result.prompt_scan_categories,
                output_ref=ref,
                schema_shortfall=result.schema_shortfall,
                model_substituted=usage.substitutions,
            )
            task_projection.project_task(self, item, inst, result)
        else:
            if result.output is not None:
                # A FAILED node's output is normally nothing worth keeping — but some failures
                # carry the only pointer to the work they left behind. A failed `subworkflow`
                # hands back its `child_run_id`, and dropping it tells the user a nested run
                # failed with no way to find it. Stored on the instance, not in `_outputs`: a
                # downstream binding must still see this node as having produced nothing.
                # NOT `_preview` as the throwaway name: that is a module-level function used a few
                # lines below, and shadowing it made every failing node crash the tick.
                ref, _unused = self.journal.store_output(item.path, result.output)
                inst.output_ref = ref
            self.run.total_tokens += int(inst.tokens)
            self.journal.step_failed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                failure=result.failure or Failure(),
                usage=usage,
                attempt=inst.attempt,
                retries_exhausted=True,
                signature={
                    "failing_node": item.node.id,
                    "layer": "execution",
                    "reason": (result.failure.failure_class.value if result.failure else ""),
                    "input_hash": entry.cache_key.inputs_hash,
                },
            )
            # Retries are spent — or there were none to spend (no budget, or a class a retry
            # cannot fix), and "every retry was spent" on a single attempt is a false sentence.
            # Produce the typed escalation artifact rather than just dying (WF2-R4).
            self._escalate(
                item.path,
                item.node.id,
                reason="retries_exhausted" if inst.attempt > 1 else "not_retried",
                detail=(result.failure.cause_plain if result.failure else ""),
            )

        loop_iteration.advance_loop(self, item.path, item.node.id)
        self._publish(
            "workflow_node_done",
            {
                "node_id": item.node.id,
                "instance_path": item.path,
                "status": result.state.value,
                "node_epoch": inst.epoch,
                "degraded_reason": result.degraded_reason,
                "output_preview": _preview(result.output),
                # Only when there is something to name (#3545), the way `cached` rides only on a
                # hit: the fold clears the row on an event without it, which is what lets a re-run
                # that now honours its schema drop yesterday's notice.
                **(
                    {"schema_shortfall": result.schema_shortfall} if result.schema_shortfall else {}
                ),
                **({"model_substituted": inst.model_substituted} if inst.model_substituted else {}),
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

    # ── wake / budget / persistence ──

    def _wake_due_nodes(self) -> None:
        """Resolve WAITING nodes whose deadline has passed.

        The controller resolves them rather than re-dispatching, because a dispatcher is
        stateless: re-entering `dispatch_wait` would recompute `now + duration` and the
        node would wait forever, one full duration at a time. The controller is the state
        owner and already knows why the node parked, so it decides here.
        """
        now = self._clock()
        for path, inst in list(self.instances.items()):
            if inst.state != InstanceState.WAITING or not inst.wake_at:
                continue
            if inst.wake_at > now:
                continue
            crossed = inst.wake_at
            inst.wake_at = 0.0
            node = dict(walk(self.root)).get(spec_path(path))
            kind = node.kind if node else None
            # The one load-bearing wall-clock read a run's trajectory depends on: THIS value, read
            # through the seam, is what let the parked node advance. Journaled as the nondeterminism
            # envelope (PP-6) so a replay resolves it against the recorded clock, not a live one.
            self.journal.clock_read(
                path, node.id if node else "", epoch=inst.epoch, clock=now, wake_at=crossed
            )
            if kind == NodeKind.WAIT:
                # The deadline WAS the work. Reaching it is success.
                inst.state = InstanceState.DONE
                inst.completed_at = now_stamp()
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
            inst.completed_at = now_stamp()
            self.journal.step_failed(
                path,
                node.id if node else "",
                epoch=inst.epoch,
                failure=failure,
                usage=NOTHING_SENT,
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
        if self._admission_wake:
            # A bake floor and a lease TTL both expire at a nameable moment (PP-12). Without this
            # the tick loop's no-deadline path sleeps zero and spins through the whole window —
            # a held step is not WAITING, so nothing else here would report its deadline.
            deadlines.append(self._admission_wake)
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
            inst.completed_at = now_stamp()
            # What the step had spent when the cancel landed. Read NOW, before the task sees its
            # CancelledError: every call still open is a generation this cancel cut off, and what
            # the finished ones reported is then a floor. Without the row a run cancelled
            # mid-generation had no ledger events at all, and Introspect said nothing cost money.
            usage = measured(entry.calls)
            self.run.total_tokens += usage.billable()
            self.journal.step_cancelled(
                entry.ready.path, entry.ready.node.id, epoch=inst.epoch, usage=usage
            )
        self._inflight.clear()
        # A DISPATCHED stage is in flight too, and it is the one `_inflight` never holds (see
        # `stage_settlement.reconcile_dispatched_stages`): its subagent kept working after the run
        # was cancelled, and a spawn still waiting on approval stayed in the approvals queue —
        # measured 2026-09-25, a cancelled run's `spawn:` approval was approvable, and approving it
        # spawned a subagent for a run that no longer existed. Stopping the subagent ends both:
        # `SubagentManager.cancel` cancels the waiting task, whose `finally` ends the pending
        # approval as `cancelled`, and the subagent's error names the run's ending.
        nodes = dict(walk(self.root))
        why = f"Cancelled: the workflow run {run_ending(RunStatus.CANCELLED)}"
        for path in await stage_settlement.stop_dispatched_stages(self, reason=why):
            inst = self._instance(path)
            inst.state = InstanceState.CANCELLED
            inst.completed_at = now_stamp()
            node = nodes.get(spec_path(path))
            # Its model calls ran in the subagent, outside this controller's call log, so what it
            # spent is "not recorded" (`None`) rather than a zero claiming it was free.
            self.journal.step_cancelled(
                path, node.id if node else "", epoch=inst.epoch, usage=NOT_RECORDED
            )
        self._persist_state()

    async def _pause_inflight(self) -> None:
        """Withdraw the work in flight, so a paused run does nothing more until it is resumed.

        "Pause — in-flight steps finish" is what the run page used to promise, and on a loop it is
        not a pause: a stage can run for many minutes and write files the whole time, and the
        loop measured 2026-09-25 wrote a finding 3.5 minutes after the user saw "Paused". So a
        pause STOPS it:

        * a stage that already finished is settled first — its output is real work;
        * a dispatched stage still running has its subagent stopped and goes back to PENDING at
          the SAME epoch, so a resume re-dispatches it (the committed-effect gate passes a
          same-epoch retry by design) and its withdrawn attempt is not counted as one;
        * an awaited node is cancelled and reset the same way.
        """
        stage_settlement.reconcile_dispatched_stages(self)
        withdrawn = list(
            await stage_settlement.stop_dispatched_stages(
                self, reason="Stopped: the run was paused"
            )
        )
        for entry in list(self._inflight.values()):
            entry.task.cancel()
            withdrawn.append(entry.ready.path)
        self._inflight.clear()
        for path in withdrawn:
            inst = self._instance(path)
            inst.state = InstanceState.PENDING
            inst.subagent_id = ""
            inst.started_at = None
            inst.attempt = max(0, inst.attempt - 1)
        self._persist_state()

    def wake(self) -> None:
        """Restart the tick loop of a controller whose loop has exited (a paused run).

        What the service calls after clearing a sticky pause (resume) or writing a sticky cancel:
        both are intents the TICK LOOP applies, and a paused run's loop is not running to read
        them. A no-op for a live loop — it reads the intent on its next step anyway.
        """
        self._resume_loop()

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
            self.run.completed_at = now_stamp()
            if self.run.started_at:
                self.run.elapsed_seconds = max(
                    0.0, stamp_epoch(self.run.completed_at) - stamp_epoch(self.run.started_at)
                )
        if status in TERMINAL_RUN_STATUSES:
            # An ended run's waits end with it — the gate a cancel caught stops reading
            # `waiting` and its token stops being offered — BEFORE the state below is persisted.
            gate_answers.close_waits(self)
        totals = journal_mod.run_totals(self.run.id)
        self._inherit_ledger_tokens(totals)
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
        if status in TERMINAL_RUN_STATUSES:
            # An ended run has nothing to resume, so a pause intent it carried (cancelled while
            # paused) is not left behind to read as "paused" by anything that checks it.
            store.clear_pause(self.run.id)
            # PP-12: give the resources back. A lease that outlives its run strands the resource
            # until the TTL runs down, and the next run would sit held by a holder that no longer
            # exists — the one failure mode a named holder is supposed to make impossible.
            run_admission.release_held_leases(self)
            # A run that ended answers its own outstanding questions by ending: nothing about
            # it is actionable now. Leaving the rows open would put a permanently unanswerable
            # gate in the inbox — cancel a run mid-gate and the question survives the run.
            # NEEDS_INPUT is deliberately not terminal here: that run is waiting, not finished.
            # The same for every approval still listed under it, whatever the ending.
            attention.resolve_run_items(self.services.attention_state, self.run.id)
            attention.cancel_run_approvals(
                self.services.attention_state, self.run.id, run_ending(status)
            )
            # A run started as a loop says it ended, the way a loops-table loop does — after the
            # resolve above, so the "needs a decision" row it may raise is not closed with the
            # run's other rows.
            attention.announce_loop_end(self.services.attention_state, self.run, status)
            if status == RunStatus.COMPLETE:
                run_finish.revise_project_overview(self)
            run_finish.capture_run_end(self)
        self._publish("workflow_run_update", {"status": status.value, "error": error})
        if status in TERMINAL_RUN_STATUSES:
            await run_finish.drain_overlap_queue(self)

    def _item_context(self, item: ReadyNode) -> dict[str, Any]:
        """The per-item fields a foreach row renders (WF2-R5): `[i/total] label`.

        Empty for a non-iterated node, so the payload does not carry meaningless keys — a
        consumer branching on presence is simpler than one branching on a null.

        The label is a SHORT stringification of the item, not the item: a fan-out over
        twenty-field dicts would put twenty JSON blobs in the event stream, and a row can only
        show a line anyway. The full value stays available through the node's output.
        """
        if not item.has_item:
            return {}
        out: dict[str, Any] = {}
        if item.iter_index is not None:
            out["item_index"] = item.iter_index
            # The RESOLVED item count, carried from the frontier — not a scan of the instance
            # map. The map is read at DISPATCH, when it holds only the items dispatched so far,
            # so counting it made the denominator track the numerator: a twelve-item fan-out
            # streamed no marker, `[2/2]`, `[3/3]` … `[12/12]`, which is precisely the failure
            # the field exists to prevent (#3403). The count was chosen to avoid a total going
            # stale after a rewind re-expands the fan-out; re-stamping on every dispatch
            # (`_launch`) buys that without paying the incompleteness.
            if item.item_total and item.item_total > 1:
                out["item_total"] = int(item.item_total)
        label = _item_label(item.item)
        if label:
            out["item_label"] = label
        return out

    # ── TASKS-SOPS projection events (S61e) ──
    #
    # Thin wrappers over `_publish` + the matching journal kind, so the LIVE stream and the
    # REPLAYABLE ledger carry the same fact under the same name. A consumer folding the stream and
    # one reconstructing from history would otherwise need two vocabularies for one event — and the
    # second one always drifts.

    def publish_task_materialized(
        self,
        path: str,
        node_id: str,
        *,
        task_id: str,
        fingerprint: str = "",
        refreshed: bool = False,
    ) -> None:
        self.journal.task_materialized(
            path, node_id, task_id=task_id, fingerprint=fingerprint, refreshed=refreshed
        )
        self._publish(
            "workflow_task_materialized",
            {
                "instance_path": path,
                "node_id": node_id,
                "task_id": task_id,
                "refreshed": bool(refreshed),
            },
        )

    def publish_confirmation_pending(
        self, path: str, node_id: str, *, confirmation_id: str, kind: str = "approval"
    ) -> None:
        self.journal.confirmation_pending(path, node_id, confirmation_id=confirmation_id, kind=kind)
        self._publish(
            "workflow_confirmation_pending",
            {
                "instance_path": path,
                "node_id": node_id,
                "confirmation_id": confirmation_id,
                "confirmation_kind": kind,
            },
        )

    def publish_confirmation_resolved(
        self,
        path: str,
        node_id: str,
        *,
        confirmation_id: str,
        verb: str,
        approved: bool,
        resolved_by: str = "",
    ) -> None:
        self.journal.confirmation_resolved(
            path,
            node_id,
            confirmation_id=confirmation_id,
            verb=verb,
            approved=approved,
            resolved_by=resolved_by,
        )
        self._publish(
            "workflow_confirmation_resolved",
            {
                "instance_path": path,
                "node_id": node_id,
                "confirmation_id": confirmation_id,
                "verb": verb,
                "approved": bool(approved),
            },
        )

    def publish_task_verified(
        self, path: str, node_id: str, *, task_id: str, passed: bool | None, criterion: str = ""
    ) -> None:
        """Emit a verification outcome. `passed` is the TRISTATE — see `journal.task_verified`."""
        self.journal.task_verified(
            path, node_id, task_id=task_id, passed=passed, criterion=criterion
        )
        self._publish(
            "workflow_task_verified",
            {
                "instance_path": path,
                "node_id": node_id,
                "task_id": task_id,
                "passed": passed is True,
                "unrunnable": passed is None,
            },
        )

    def publish_cascade_blocked(
        self, path: str, node_id: str, *, blocked_task_ids: list[str], cause: str
    ) -> None:
        """ONE event for the whole cascade, matching §1's debounce.

        N events for one upstream failure would make the run look like it failed N times, and the
        notification layer already collapses them — two different collapse points would disagree.
        """
        self.journal.cascade_blocked(path, node_id, blocked_task_ids=blocked_task_ids, cause=cause)
        self._publish(
            "workflow_cascade_blocked",
            {
                "instance_path": path,
                "node_id": node_id,
                "blocked_task_ids": list(blocked_task_ids),
                "cause": cause,
            },
        )

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        """Publish one event, stamped with the identity a consumer needs to fold safely.

        Three fields are added HERE rather than at each of the twelve call sites, because a
        call site that forgot one would produce an event the FE cannot dedup or supersede —
        and that is invisible until a rewind duplicates a row (WF2-R11):

        * `event_id` — deterministic (`<run>-evt-<n>`), so a re-emit is an idempotent no-op
          rather than a second row.
        * `seq` — monotonic per run, so a consumer can detect a gap or an out-of-order
          delivery instead of silently folding backwards.
        * `epoch` — the run's current epoch, so an event from a superseded epoch (a rewind
          landed while it was in flight) is DROPPED instead of resurrecting stale state.
          A payload that already carries a node-specific epoch keeps it.
        """
        fn = self.services.publish
        if fn is None:
            return
        self._event_seq += 1
        body: dict[str, Any] = {
            "run_id": self.run.id,
            "event_id": f"{self.run.id}-evt-{self._event_seq}",
            "seq": self._event_seq,
            "epoch": self._run_epoch(),
            **payload,
        }
        try:
            fn(event, body)
        except Exception:  # a broken observer must never kill a run
            logger.debug("workflow %s: publish %s failed", self.run.id, event, exc_info=True)

    def _run_epoch(self) -> int:
        """The run's current epoch — the max across instances.

        A rewind bumps only the region it resets, so the RUN's epoch is the highest any node
        has reached. Using a per-node epoch as the run's would let an untouched node's stale
        value mark a fresh event as superseded.
        """
        return max((i.epoch for i in self.instances.values()), default=0)


# ── module helpers ───────────────────────────────────────────────────────────


#: Max characters of a foreach item's label. A row shows one line, and a fan-out over long
#: strings would otherwise put kilobytes of prose in the event stream for no gain.
_ITEM_LABEL_MAX = 60


def _item_label(item: Any) -> str:
    """A short, human-readable label for one foreach item.

    Prefers a NAMED field when the item is a dict, because a fan-out over records is the common
    case and `{"path": "auth.py", …}` should read as `auth.py`, not as its JSON. Falls back to
    a truncated stringification — something is always better than an index alone, which is
    what the row already shows.
    """
    if isinstance(item, dict):
        for key in ("label", "name", "title", "path", "id"):
            value = item.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return _clip(str(value))
        return _clip(", ".join(f"{k}={v}" for k, v in list(item.items())[:3]))
    if isinstance(item, (list, tuple)):
        # A container's contents are not a label; its size is the only honest summary.
        return f"{len(item)} items"
    if item is None:
        return ""
    return _clip(str(item))


def _clip(text: str) -> str:
    text = " ".join(text.split())  # a newline inside a row breaks the layout
    return text if len(text) <= _ITEM_LABEL_MAX else text[: _ITEM_LABEL_MAX - 1] + "…"


def _is_engine_install_fault(exc: BaseException) -> bool:
    """Whether `exc` says the ENGINE ITSELF could not be imported, not that a run failed.

    The distinction is the whole point: an `ImportError` naming a `personalclaw` module means
    this PROCESS is stale (its code was deleted or predates the run's state), so it knows
    nothing about the run and must not render a verdict on it. Every other exception — a
    provider error, a bad spec, a third-party import that a node genuinely needs — IS about
    the run and still terminally fails it. Widening this to all `ImportError`s would silently
    convert real run failures into runs that never finish.

    Keyed on `ImportError.name` rather than the message: the attribute is populated for both
    shapes that occur here (`from personalclaw.x import y` sets it to `personalclaw.x`, a
    missing module sets it to the module), and matching message text would break the moment
    CPython rewords it. `name` can be None for a hand-raised `ImportError`, which reads as
    "not attributable to the engine" — the conservative answer, since it keeps the existing
    fail-loudly behaviour for anything we cannot positively identify.
    """
    if not isinstance(exc, ImportError):
        return False
    name = getattr(exc, "name", None) or ""
    return name == "personalclaw" or name.startswith("personalclaw.")


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
