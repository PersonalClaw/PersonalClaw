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
import calendar
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from personalclaw import project_context
from personalclaw.knowledge import session_brief
from personalclaw.ledger import outcomes
from personalclaw.loop import tick as convergence
from personalclaw.loop.tick import Action as StepAction
from personalclaw.workflows import (
    attention,
    conditions,
)
from personalclaw.workflows import context as context_mod
from personalclaw.workflows import (
    execution_hints,
    gate_policy,
)
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import (
    judge_calibration,
    longrun,
    mutations,
    ownership,
    pool,
    revision,
    store,
    supervisor_policy,
)
from personalclaw.workflows.admission import (
    AdmissionRequest,
    AdmissionState,
    MetricGate,
    Scope,
    compose,
    default_policies,
)
from personalclaw.workflows.bindings import BindingContext, BindingError, node_deps
from personalclaw.workflows.effects import (
    EffectRecord,
    EffectStatus,
    committed_effect,
    effect_history,
    idempotency_key,
    output_id_of,
    redo_blocked,
    run_teardown,
)
from personalclaw.workflows.engine import (
    DEFAULT_MODEL_TIERS,
    NodeResult,
    dispatch,
    resolve_axis_model,
)
from personalclaw.workflows.human_input import drop_continuations
from personalclaw.workflows.journal import CacheKey, Journal, inputs_hash, spec_region_hash
from personalclaw.workflows.judge_contract import hints_from_dict as judge_hints_from_dict
from personalclaw.workflows.loop_middleware import (
    InterruptQueue,
    call_fingerprint,
    classify_failure,
)
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    Failure,
    FailureClass,
    InstanceState,
    ItemErrorPolicy,
    LoopMode,
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
from personalclaw.workflows.scope import ScopeMode
from personalclaw.workflows.scope import allowed_write_paths as scope_allowed
from personalclaw.workflows.scope import diff as scope_diff
from personalclaw.workflows.scope import enforces_scope, scope_mode
from personalclaw.workflows.scope import snapshot as scope_snapshot
from personalclaw.workflows.scope import watch_roots as scope_watch_roots
from personalclaw.workflows.supervisor_policy import tick_config as convergence_config
from personalclaw.workflows.tick import (
    Frontier,
    Limits,
    ReadyNode,
    derive_state,
    frontier,
    item_error_policy,
    loop_should_continue,
    reap_watchers,
)

logger = logging.getLogger(__name__)

#: A loop/foreach iteration marker: `@2` or `#3`. Anchored on the digits so a `#` inside a node
#: id cannot be mistaken for one.
_INSTANCE_MARKER_RE = re.compile(r"[@#]\d+")

#: A LOOP iteration marker specifically (`@2`), capturing the number. Distinct from the foreach
#: marker (`#3`) because only a loop has an iteration counter to advance.
_LOOP_MARKER_RE = re.compile(r"@(\d+)")

#: How long a tick waits for in-flight work before re-deriving the frontier. Bounded so a
#: WAITING deadline or an externally-answered gate is noticed promptly.
TICK_WAKE_SECS = 5.0

#: How long an escalated gate gets to be answered before its outcome is graded (PP-9). A day,
#: because a gate raised overnight is answered in the morning and grading it sooner would call a
#: sleeping user an unlanded interruption. Nothing expires at this point — the gate keeps waiting;
#: only the BET about whether interrupting was worth it closes.
ESCALATION_ANSWER_HORIZON_SECS = 24 * 3600.0

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


#: How many convergence decisions per loop the run row keeps. Bounded, because an unbounded
#: decision log on a run row is a slow leak that reads like an audit trail (PP-15).
_CONVERGENCE_LOG_MAX = 50

#: Breaker reasons that are a DECLARED BUDGET being reached, not a stall (PP-15).
#:
#: The distinction decides who answers the trip. A loop that thrashes is recoverable — that is
#: what the escalation ladder is for, and failing it binary is the bug PP-15 fixes. A loop that
#: reached the `max_iterations` or token cap ITS AUTHOR SET is not thrashing and has nothing
#: cheaper to try: spending a fresh session and a model switch on a satisfied budget would
#: re-run the work the cap existed to bound. So budgets keep going straight to the escalation
#: artifact, exactly as before, and only thrash reaches the ladder.
_BUDGET_TRIPS = frozenset({"max_iterations", "token_cap"})


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
        #: from the ledger rather than losing them — see `_rehydrate_context`.
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
        #: The run's worker model, resolved ONCE (see `_worker_model`) — the family a `cross_model`
        #: judge gate must avoid (WF2LOO-11). The active selection does not change mid-run, so
        #: re-resolving per gate would re-read the model store for an answer that cannot change.
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
        #: Run-scoped "always allow" decisions (WF2-R7). Cleared on rewind: remembering
        #: across one would auto-approve the very step the user rewound to reconsider.
        self._allow_memory = gate_policy.AllowMemory()
        #: event-gate path -> re-hold accounting. Bounded, because an unbounded hold is a
        #: wedge that looks like patience.
        self._event_holds: dict[str, gate_policy.HoldState] = {}
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
                        # No in-flight work and no scheduled wake: the next _step call
                        # decides completion or deadlock. Yield rather than spin.
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
        `workspace:` declaration does that, and `_provision_workspace` has already written the
        terminal status. The tick loop stops rather than scheduling into a workspace that could
        not be honored.
        """
        resumed = bool(self.run.started_at)
        totals = journal_mod.run_totals(self.run.id)
        # Budget pre-charge (WF2-R4 #1): a resumed run inherits its own spend. Without
        # this a crash loop mints a fresh budget each time and spends without bound.
        self.run.total_tokens = max(self.run.total_tokens, int(totals.get("tokens", 0)))
        # Context lifecycle (WF2-R6): rebuild handoffs/carryover/decisions from the ledger. This is
        # the whole reason they are journaled — a resumed run that lost them would restart its next
        # iteration blind, re-deriving what a previous one already verified, which is the exact
        # failure the mechanism exists to prevent.
        self._rehydrate_context()
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
        self._enforce_inherited_mode()
        provisioned = await self._provision_workspace()
        self._bind_project_memory_cwd()
        self._publish("workflow_run_update", {"status": self.run.status.value})
        return provisioned

    async def _provision_workspace(self) -> bool:
        """Stand up the run's declared workspace before the first node (WORK-CONTAINERS §4.1).

        Returns False when the run was refused. This is the first production caller of
        `workspace.plan_provisioning` / `worktrees.pending_setup`: before it, a spec's
        `workspace:` block was parsed nowhere, so every run ran in place no matter what its
        template declared — the whole §4.1 mechanism was a decision layer with no call site.

        **A FATAL declaration REFUSES the run.** `parse_workspace` marks an unknown mode and a
        greedy preserve pattern fatal precisely because they cannot be honored, and honoring
        neither means running in a mode nobody chose. An ignored fatal issue is the inert-control
        shape this program keeps finding, so it terminates the run through `_finish` (the single
        terminal writer) instead of degrading quietly.

        **The lock is taken and RELEASED here, not held for the run.** Holding a flock across a
        multi-hour run would tie the workspace to this process's lifetime, so a gateway restart
        would strand it. What the lock actually protects is the provisioning WINDOW — the
        preserve+setup pass, where two processes writing the same tree corrupt each other. Live
        contention refuses the run rather than queueing: two runs interleaving writes in one
        worktree is worse than telling the second one now.

        Idempotent, because `_prepare` runs again on resume: `add_worktree` returns the same path
        for an existing run id (measured), setup is marker-guarded and content-addressed, and
        preserve is an overwriting copy. The second pass is therefore cheap and safe rather than a
        second workspace.

        Guarded on everything except the deliberate refusal: a provisioning bug must cost the
        isolation, never the run — a run that cannot start because a scratch dir failed to `mkdir`
        would be strictly worse than one that runs in the project workspace and says so.
        """
        from personalclaw.config.loader import AppConfig
        from personalclaw.workflows import provisioning

        if not provisioning.declares_workspace(self.spec):
            # No `workspace:` block, no managed workspace. The default mode fills in a block that
            # declared the OTHER fields; it does not opt every run in — see `declares_workspace`
            # for the boot-sweep interaction that measurement caught.
            return True
        try:
            cfg = AppConfig.load().workflows
            spec, issues = provisioning.resolve_spec(
                self.spec, default_mode=cfg.workspace_default_mode
            )
        except Exception:
            logger.debug("run %s: workspace spec unreadable", self.run.id, exc_info=True)
            return True

        fatal = [i for i in issues if i.fatal]
        if fatal:
            reason = "; ".join(i.message for i in fatal)
            self.journal.workspace_provisioned(
                {"ok": False, "issues": [i.to_dict() for i in fatal], "refused": True}
            )
            async with self._lock:
                await self._finish(
                    RunStatus.FAILED, error=f"workspace declaration refused: {reason}"[:500]
                )
            return False

        lock = provisioning.acquire_workspace_lock(self.run.id, name=spec.name)
        if not lock.acquired:
            # A named workspace another live run holds. Refused, not queued — see the docstring.
            self.journal.workspace_provisioned(
                {"ok": False, "contended": True, "degraded_reason": lock.reason}
            )
            async with self._lock:
                await self._finish(RunStatus.FAILED, error=lock.reason[:500])
            return False
        try:
            result = await provisioning.provision(
                spec,
                run_id=self.run.id,
                project_id=self.run.project_id,
                workspace_dir=self._project_workspace(),
                issues=issues,
                runner=self.services.teardown_runner,
            )
        except Exception:
            logger.warning("run %s: workspace provisioning failed", self.run.id, exc_info=True)
            return True
        finally:
            lock.release()

        async with self._lock:
            provisioning.stamp_run(self.run, result, spec)
            self._save_run()
        self.journal.workspace_provisioned(result.to_dict())
        if result.isolated and result.path:
            # The stage dispatcher's cwd, so a code-kind run's subagents actually work IN the
            # worktree. Without this the isolation would be a directory nothing ran in — the
            # mechanism would look provisioned and be decorative.
            self.services.cwd = result.path
        return True

    def _project_workspace(self) -> str:
        """The codebase this run's project binds, or the services cwd.

        A project's `workspace_dir` is the tree a worktree branches from and the tree
        `preserve_patterns` copies out of. Falling back to `services.cwd` keeps a project-less
        run (a chat-launched batch) provisionable — its workspace is simply wherever the gateway
        is rooted, which is what every other cwd-consuming node already assumes.
        """
        pid = self.run.project_id
        if not pid:
            return self.services.cwd
        try:
            from personalclaw.tasks.hierarchy import HierarchyStore

            project = HierarchyStore().get_project(pid)
            bound = str(getattr(project, "workspace_dir", "") or "") if project else ""
            return bound or self.services.cwd
        except Exception:
            logger.debug("run %s: project workspace lookup failed", self.run.id, exc_info=True)
            return self.services.cwd

    def _bind_project_memory_cwd(self) -> None:
        """Default a project-owned run's cwd to the project's `context_dir` (§1.6).

        Memory is partitioned by cwd (`memory_dir_for_cwd`), so a project-owned run whose cwd
        is empty writes everything it learns into the shared `_ext/_default` partition — one
        pile every project's runs stir together. Binding the project's context dir (which §1.2
        already calls "the default cwd fallback for stage nodes", and which the hierarchy store
        documents as "the working area when no external workspace is bound") makes that memory
        project-local for free: no second partitioning mechanism, just the seam that exists.

        Runs LAST in `_prepare`, and only when nothing more specific has claimed the cwd:
        an isolated workspace (`result.path`, set just above) and a caller-supplied
        `services.cwd` are deliberate bindings, and a memory-locality default that overrode
        them would move a code-kind run out of the worktree it was provisioned into.
        """
        if self.services.cwd:
            return
        from personalclaw.memory_locality import project_memory_cwd

        cwd = project_memory_cwd(self.run.project_id)
        if not cwd:
            return
        self.services.cwd = cwd
        logger.info(
            "run %s: cwd bound to project context dir for memory locality (%s)", self.run.id, cwd
        )

    def _enforce_inherited_mode(self) -> None:
        """Apply a restricted origin's memory posture at run start (WORK-CONTAINERS §5.1).

        The run already carries the inherited mode in `extra` (stamped by `start_run`); this is the
        moment it becomes ENFORCED in the process-global `session_restrictions` registry — the fast
        path the knowledge/learning writers consult during the run. The chat layer never marks the
        registry (it enforces off `session.is_restricted` on the LIVE session object, which a
        background run no longer holds), so this is the registry's FIRST writer for a run's keys.

        The mark is made for BOTH the launching session key AND the run-owned key: the origin key is
        what the run-end LearningGate reads (`_capture_run_end` keys `for_session` off
        `origin.session_key`), and the owned key is what any run-scoped write would carry. A
        `temporary` run gets both marks per `restriction_calls`, because `is_temporary` gates reads
        while `is_restricted` gates writes.

        **Durability lives in `run.extra`, not a session JSONL.** A run owns no `ConversationLog`
        file — stage subagents persist under their own `subagent:<id>` keys, and the `workflow:`
        owned string is a provenance ref, not a JSONL key. The run record's `extra["memory_mode"]`
        IS the run's durable metadata head: `start_run` stamps it via `ownership.stamp_run_mode`,
        which is `ownership.durable_metadata` (same `memory_mode` key by construction, not two
        literals). It round-trips through the run record on disk and is what a gateway restart
        replays — after which `_prepare` runs again and re-marks the registry from it, and the
        engine's node-skip + the run-end gate keep reading it. Materializing an owned-session JSONL
        line would create a file no reader consumes (the reindex path forgets restricted sessions on
        sight), so the durable write is deliberately the `extra` head. DEVIATION from the literal
        "JSONL write" phrasing, recorded in the plan's Execution log.

        Idempotent and best-effort: `_prepare` runs once per live controller and again on resume,
        and re-marking a key already in the LRU is a no-op. A NORMAL run does nothing — an
        unrestricted origin has nothing to suppress. Guarded because `_prepare` must not fail the
        run over a registry mutation.
        """
        mode = ownership.run_mode(self.run)
        if mode is ownership.MemoryMode.NORMAL:
            return
        owned = ownership.own_session(self.run.id, "run", inherited_mode=mode)
        try:
            from personalclaw import session_restrictions

            for call in ownership.restriction_calls(owned):
                mark = getattr(session_restrictions, call)
                if self.run.origin.session_key:
                    mark(self.run.origin.session_key)
                mark(owned.key)
        except Exception:
            logger.debug("run %s: registry mark failed", self.run.id, exc_info=True)

    async def _step(self) -> bool:
        """One scheduling step under the lock. Returns True when the run is terminal.

        This is also the designated safe point for mid-flight mutation (Slice 4): the
        lock is held and no node is mid-launch.
        """
        if store.cancel_requested(self.run.id):
            await self._cancel_inflight()
            await self._finish(RunStatus.CANCELLED)
            return True

        # Mutations drain HERE — lock held, nothing mid-launch (WF2-R20 safety #1). Before
        # the frontier, so an applied edit is reflected in this step's scheduling rather
        # than a tick later.
        self._drain_mutations()

        self._wake_due_nodes()

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
        admitted = await self._admit_ready(fr.ready)
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
            gates = [p for p in waiting if self._is_gate(p)]
            # A gate awaiting a HUMAN surfaces as needs_input IMMEDIATELY (WF2-R7): a run
            # that parks quietly for 45s and only then surfaces is a run nobody knows to
            # answer. Surfacing and terminating are separate, though — see below.
            for path in gates:
                self._ensure_continuation(path)
            if gates and self.run.status != RunStatus.NEEDS_INPUT:
                self._surface_needs_input()
            if waiting and self._next_wake_delay() is None:
                # Nothing will wake this run: no deadline, no in-flight work. NOW it is
                # terminal. With a deadline still pending the loop keeps ticking so the
                # unattended timeout can actually fire — a surfaced run is waiting, not
                # finished.
                await self._finish(RunStatus.NEEDS_INPUT)
                return True
        return False

    def _surface_needs_input(self) -> None:
        """Publish the needs-input state without ending the run.

        Split from `_finish` deliberately: a gate with an unattended deadline must be
        VISIBLE now and still able to time out later. Collapsing the two would force a
        choice between surfacing promptly and honouring the timeout.
        """
        self.run.status = RunStatus.NEEDS_INPUT
        self._save_run()
        self._publish("workflow_run_update", {"status": RunStatus.NEEDS_INPUT.value})

    def _is_gate(self, path: str) -> bool:
        """Is this waiting instance a human-input gate (versus a `wait` deadline)?

        A `wait` is parked on the CLOCK and resolves itself, so surfacing it as needs_input
        would ask a human to answer something nobody asked them.
        """
        node = dict(_walk(self.root)).get(_base_path(path))
        if node is None or node.kind != NodeKind.GATE:
            return False
        raw = str((node.config or {}).get("kind", "") or "")
        return raw in ("approval", "event")

    def _ensure_continuation(self, path: str) -> None:
        """Mint a durable resume point for a waiting gate (WF2-R7), once per epoch.

        Idempotent by (path, epoch): a run can pass through `needs_input` repeatedly as the
        watchdog polls it, and a fresh token per poll would leave a pile of live approval
        links for one question — each of them individually valid.
        """
        from personalclaw.workflows.human_input import (
            create_continuation,
            handoff_bundle,
            list_continuations,
        )

        inst = self._instance(path)
        for existing in list_continuations(self.run.id):
            if existing.instance_path == path and existing.epoch == inst.epoch:
                return
        node = dict(_walk(self.root)).get(_base_path(path))
        ask = dict(self.run.attention or {}) if self.run.attention else {}
        outstanding = [
            p for p, i in self.instances.items() if i.state not in TERMINAL_STATES and p != path
        ]
        cont = create_continuation(
            self.run.id,
            node_id=node.id if node else "",
            instance_path=path,
            epoch=inst.epoch,
            resolved_inputs=self._resolved_for_path(path),
            ask=ask,
            handoff=handoff_bundle(
                scope=self.run.workflow_name,
                status="blocked on human input",
                outstanding=outstanding,
                checks_run=[p for p, i in self.instances.items() if i.state in SUCCESS_STATES],
                next_steps=[f"answer the gate at {node.id if node else path}"],
            ),
        )
        self._publish(
            "workflow_needs_input",
            {
                "node_id": cont.node_id,
                "instance_path": path,
                "resume_token": cont.token,
                "ask": cont.ask,
                "handoff": cont.handoff,
                "expires_at": cont.expires_at,
            },
        )
        # The typed CONFIRMATION record's pending half (TASKS-SOPS §4, S61i). Emitted HERE rather
        # than at a second site so it inherits this method's `(path, epoch)` idempotency for free:
        # the watchdog polls a waiting run repeatedly, and a per-poll emission would put one
        # "awaiting approval" row per poll into the ledger for a single question.
        #
        # `confirmation_id` is derived from `(run, gate, epoch)` by `confirmation.request_id`, NOT
        # from the resume token. The token is single-use and rotates on rewind; the ID has to stay
        # stable so `confirmation_pending` and `confirmation_resolved` pair up in the ledger.
        confirmation_id = _confirmation_id(self.run.id, cont.node_id or path, inst.epoch)
        self.publish_confirmation_pending(
            path,
            cont.node_id,
            confirmation_id=confirmation_id,
            kind=_confirmation_kind(node.config if node else {}),
        )
        self._open_escalation_outcome(path, cont.node_id, confirmation_id)
        # …and DURABLY, to the inbox (WF2-R7). The SSE frame above only reaches a view that
        # happens to be open; a scheduled run parking at 3am would otherwise wait in silence
        # forever. Minted alongside the continuation so the two share the (path, epoch)
        # idempotency — one row per question, not one per watchdog poll.
        attention.raise_gate_item(
            self.services.attention_state,
            run_id=self.run.id,
            workflow=self.run.workflow_name,
            node_id=cont.node_id,
            instance_path=path,
            epoch=inst.epoch,
            resume_token=cont.token,
            ask=cont.ask,
            handoff=cont.handoff,
        )

    def _resolved_for_path(self, path: str) -> dict[str, Any]:
        """What this node had already resolved — the field that makes a resume re-enter
        the STEP rather than re-run the enclosing subgraph."""
        node = dict(_walk(self.root)).get(_base_path(path))
        if node is None:
            return {}
        deps = node_deps(node.config or {})
        return {dep: self._outputs.get(dep) for dep in sorted(deps)}

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
        revise = _parse_revise(answer)
        if revise is not None:
            step_ref, comment = revise
            return self._resume_revise(
                cont, token, step_ref, comment, responder=responder, channel=channel
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
        approved = _is_approved(ask, filled)
        if approved and always_allow:
            # Run-scoped, keyed by (operation, target) — and cleared on rewind, so it can
            # never auto-approve a step the user rewound to reconsider.
            node = dict(_walk(self.root)).get(_base_path(cont.instance_path))
            self._allow_memory.remember(node.config if node else {}, cont.node_id)
        inst.wake_at = 0.0
        if approved:
            inst.state = InstanceState.DONE
            ref, preview = self.journal.store_output(
                cont.instance_path, {"answer": filled, "approved": True}
            )
            inst.output_ref = ref
            if cont.node_id:
                self._outputs[cont.node_id] = preview
        else:
            inst.state = InstanceState.FAILED
            inst.failure = Failure(
                failure_class=FailureClass.USER,
                cause_plain="the gate was denied",
                remediation="adjust the work the gate rejects, then re-run from this node",
                terminal_reason="denied",
            )
        inst.completed_at = _now()
        # The resolution half. AFTER the claim is won and the epoch verified, so the ledger records
        # answers that actually applied — emitting before the claim would log an approval for a race
        # the caller lost, and the audit would show two people approving one gate.
        self.publish_confirmation_resolved(
            cont.instance_path,
            cont.node_id,
            confirmation_id=_confirmation_id(
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
        )
        # Judge/human divergence → Run Ledger (LOOPS-EVOLUTION R3). If this gate had a judge
        # verdict and the human's decision contradicts it, record the direction: a judge that
        # PASSed work a human then rejected is a `false_pass`; a judge that REJECTed work a human
        # then approved is a `false_reject`. This is the calibration signal — a verdict that
        # followed a human override is not clean evidence about the template, and without the event
        # a refiner cannot tell the difference (see judge_calibration.DivergenceRecord).
        self._emit_judge_divergence(cont.instance_path, cont.node_id, approved)
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

    def _resume_revise(
        self,
        cont: Any,
        token: str,
        step_ref: str,
        comment: str,
        *,
        responder: str = "",
        channel: str = "",
    ) -> dict[str, Any]:
        """Apply `revise{step_ref, comment}` to exactly one node, then let the run carry on.

        This is the third answer to a waiting gate, and the reason it has to exist: a reviewer who
        wants ONE step changed could previously only reject the whole plan and re-run it, which
        re-rolls the twelve stages nobody complained about (the same argument `revision.py` makes
        about regeneration).

        Mirrors `planning.session.comment_step`'s awaiting_review → running semantics (a comment
        sends the step back for a re-draft rather than accepting the artifact), with the engine's
        own state names: the gate instance goes back to PENDING at the current epoch, not DONE, so
        it re-asks against the revised step. It is deliberately NOT an approval — nothing is marked
        approved, no `always_allow` is remembered, and no `gate_resolved` is journaled, because the
        gate has not been answered yet.

        EVERY refusal here happens before the token is consumed. The whole point of a revise is that
        the reviewer is still deciding, so a rejected revise must leave them able to answer.
        """
        from personalclaw.workflows.human_input import consume_continuation

        allowed, why = _revise_allowed(self.run)
        if not allowed:
            return {"ok": False, "code": "WF_REVISE_NOT_ALLOWED", "message": why}

        root = self.spec.get("root")
        if not isinstance(root, dict):
            return {
                "ok": False,
                "code": "WF_REVISE_NO_SPEC",
                "message": "this run has no readable spec to revise",
            }
        node_id, code, message = revision.resolve_step_ref(root, step_ref)
        if code:
            return {"ok": False, "code": code, "message": message}
        text = str(comment or "").strip()
        if not text:
            return {
                "ok": False,
                "code": "WF_REVISE_NO_COMMENT",
                "message": "a revise must say what to change (`comment`)",
            }

        patch = revision.comment_patch(
            root, node_id, text, requested_by=responder or channel or "user"
        )
        if patch is None:
            return {
                "ok": False,
                "code": "WF_REVISE_NOT_APPLICABLE",
                "message": f"{node_id!r} cannot carry a revision comment",
            }
        merged = revision.merge_patches(self.spec, [patch])
        if not merged.applied:
            return {
                "ok": False,
                "code": "WF_REVISE_REJECTED",
                "message": "; ".join(merged.rejected) or "the revision did not apply",
            }

        # The token is consumed only once the revision is certain to land. A revise answers the
        # gate as surely as an approval does — leaving the token live would let the same reviewer
        # revise twice off one ask, and the second would land on an already-revised step.
        claimed = consume_continuation(self.run.id, token)
        if claimed is None:
            return {"ok": False, "code": "WF_RESUME_ALREADY_USED"}
        inst = self._instance(cont.instance_path)
        if inst.epoch != cont.epoch:
            return {"ok": False, "code": "WF_RESUME_STALE_EPOCH"}

        # ONE spec, written once, journaled with the ops that produced it. `_commit_mutation` is the
        # single writer of `spec.json` + `spec_history/` + `user_edited_mid_flight`, so routing the
        # revision through it is what makes the recorded edit and the executing spec the same
        # document rather than two that agree by convention.
        result = mutations.BatchResult(
            ok=True,
            ops=[
                mutations.Op(
                    kind=mutations.OpKind.UPDATE_NODE,
                    node_id=node_id,
                    node=patch.node,
                    note=f"revise: {text}",
                    raw={"op": "revise", "step_ref": node_id, "comment": text},
                )
            ],
            preview=mutations.CascadePreview(rerun=[node_id]),
            spec=merged.spec,
        )
        self._commit_mutation(result, responder or channel or "user")

        # The revised step re-asks. PENDING at the SAME epoch, matching `_apply_reentry`'s
        # no-force behaviour — and the cache cannot serve the old answer anyway, because the
        # cache key hashes the node's own spec region and the prompt just changed.
        inst.state = InstanceState.PENDING
        inst.output_ref = ""
        inst.failure = None
        inst.completed_at = None
        inst.wake_at = 0.0
        inst.attempt = 0
        if cont.node_id:
            self._outputs.pop(cont.node_id, None)
        self.journal.write(
            journal_mod.GATE_REVISED,
            instance_path=cont.instance_path,
            node_id=cont.node_id,
            epoch=cont.epoch,
            step_ref=node_id,
            comment=text,
            revised_by=responder or channel or "dashboard",
        )
        self.run.attention = None
        if self.run.status == RunStatus.NEEDS_INPUT:
            self.run.status = RunStatus.RUNNING
        self._persist_state()
        self._save_run()
        self._publish(
            "workflow_gate_revised",
            {
                "node_id": cont.node_id,
                "instance_path": cont.instance_path,
                "step_ref": node_id,
            },
        )
        # Same reasoning as the approval path: the gate's inbox row must not outlive the ask it
        # raised. The revised step raises its own row when it re-asks.
        attention.resolve_gate_item(self.services.attention_state, self.run.id, cont.node_id)
        self._publish("workflow_run_update", {"status": self.run.status.value})
        self._resume_loop()
        return {
            "ok": True,
            "revised": True,
            "approved": False,
            "node_id": cont.node_id,
            "step_ref": node_id,
            "spec_version": self.run.spec_version,
        }

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
        """
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
            body["issues"] = [
                {
                    "code": "WF_MUT_CONFIRM_REQUIRED",
                    "message": (
                        "this batch re-runs completed nodes "
                        f"({', '.join(result.preview.rerun[:5])}); resubmit with confirm=true"
                    ),
                    "node_id": "",
                }
            ]
            return body
        self._pending_mutations.append((result, actor))
        body["queued"] = True
        return body

    def _drain_mutations(self) -> None:
        """Apply queued batches. Called under the lock, between scheduling steps.

        Each batch is RE-VERIFIED here (WF2-R2 TOCTOU): nodes complete while a user reads a
        preview, so a node that was pending at submit may be frozen by now. Re-validating
        against current state is the only way to catch that, and `validate_batch` is pure
        so running it twice costs nothing.
        """
        if not self._pending_mutations:
            return
        queued = list(self._pending_mutations)
        self._pending_mutations.clear()
        for result, actor in queued:
            try:
                root = Node.from_dict(self.spec.get("root") or {})
            except ValueError:
                logger.warning("workflow %s: spec unreadable, dropping mutation", self.run.id)
                continue
            issues = mutations.validate_batch(result.ops, root, self.instances)
            if issues:
                # The state moved under the preview. Rejected, and journaled as rejected —
                # a silently dropped batch is indistinguishable from an applied one.
                self.journal.write(
                    journal_mod.MUTATION_REJECTED,
                    actor=actor,
                    ops=[o.to_dict() for o in result.ops],
                    issues=[i.to_dict() for i in issues],
                )
                self._publish(
                    "workflow_mutation_rejected",
                    {"issues": [i.to_dict() for i in issues], "actor": actor},
                )
                continue
            self._commit_mutation(result, actor)

    def _commit_mutation(self, result: mutations.BatchResult, actor: str) -> None:
        """Swap in the candidate spec, apply state effects, journal the batch."""
        if result.spec is None:
            return
        self.spec = result.spec
        self.run.spec_version += 1
        self.root = Node.from_dict(self.spec.get("root") or {"kind": "sequence"})
        store.write_spec(self.run.id, self.spec)
        store.write_spec_history(
            self.run.id,
            self.run.spec_version,
            mutations.history_record(
                result.ops,
                actor=actor,
                version=self.run.spec_version,
                spec=self.spec,
                preview=result.preview,
            ),
        )
        self.journal.user_edited_mid_flight([o.to_dict() for o in result.ops])

        for op in result.ops:
            if op.kind in (mutations.OpKind.REWIND, mutations.OpKind.RUN_FROM):
                self._apply_reentry(op, result.preview)
            elif op.kind == mutations.OpKind.SKIP:
                self._skip_by_id(op.node_id)
            elif op.kind == mutations.OpKind.SET_INPUT:
                self.run.inputs.update(op.overrides)
            elif op.kind == mutations.OpKind.FORK:
                self._apply_fork(op)

        # Nodes whose inputs changed but which are NOT being re-run (WF2-R2 #3). Flagged
        # rather than silently serving an answer computed from inputs that no longer exist.
        self._flag_stale(result.preview)
        self._save_run()
        self._persist_state()
        self._publish(
            "workflow_spec_updated",
            {
                "spec_version": self.run.spec_version,
                "actor": actor,
                "preview": result.preview.to_dict(),
            },
        )

    def _apply_reentry(self, op: mutations.Op, preview: mutations.CascadePreview) -> None:
        """Reset the binding closure so it re-runs.

        `rewind` resets the seed node AND its consumers; `run_from` resets only the
        consumers, leaving the seed's output in place — that is the whole distinction
        ("redo the synthesis with the same gathered data").

        The outputs are ARCHIVED, not deleted: a rewind that discarded the prior answer
        would make the edit irreversible, and the attic is what lets a reader see what the
        run used to say.
        """
        targets = set(preview.rerun)
        if op.kind == mutations.OpKind.RUN_FROM:
            targets.discard(op.node_id)
        paths = [path for path, node in _walk(self.root) if node.id in targets for _ in (0,)]
        # A remembered "always allow" must not survive a rewind: it would auto-approve the
        # very step the user rewound in order to reconsider it (WF2-R7).
        self._allow_memory.clear()
        epoch = mutations.next_epoch(self.instances, paths, force=op.force)
        for path in paths:
            inst = self._instance(path)
            if inst.output_ref:
                store.archive_output(self.run.id, path, self.run.spec_version)
            inst.state = InstanceState.PENDING
            inst.epoch = epoch
            inst.output_ref = ""
            inst.failure = None
            inst.completed_at = None
            inst.wake_at = 0.0
            inst.attempt = 0
            node = dict(_walk(self.root)).get(_base_path(path))
            if node is not None and node.id:
                # Drop the cached output so a binding cannot resolve a stale value between
                # the reset and the re-run.
                self._outputs.pop(node.id, None)
            self.journal.invalidate_prefix(path)
            # A pending approval for a node about to re-run would resume a step that no
            # longer exists in that form (WF2-R7) — drop the token rather than let it land
            # in the wrong epoch.
            drop_continuations(self.run.id, instance_prefix=path)

    def _apply_fork(self, op: mutations.Op) -> None:
        """Branch a child run. THIS run is untouched — that is the whole point of fork.

        The child is left in DRAFT: starting it is the caller's decision, because a fork is
        usually created to be edited before it runs ("try a stricter judge"). Auto-starting
        would race the edit it exists to receive.
        """
        from personalclaw.workflows.checkpoints import fork_run

        try:
            result = fork_run(
                self.run,
                self.spec,
                self.instances,
                checkpoint_id=op.checkpoint_id,
                note=op.note,
                now=_now(),
            )
        except ValueError as exc:
            self.journal.write(
                journal_mod.MUTATION_REJECTED,
                actor="engine",
                ops=[op.to_dict()],
                issues=[{"code": "WF_MUT_UNKNOWN_CHECKPOINT", "message": str(exc), "node_id": ""}],
            )
            return
        self.journal.write(
            journal_mod.CHILD_RUN_ATTACH,
            parent_run_id=self.run.id,
            child_run_id=result.child.id,
            node_id=op.node_id,
        )
        self._publish("workflow_forked", result.to_dict())

    def _skip_by_id(self, node_id: str) -> None:
        for path, node in _walk(self.root):
            if node.id == node_id:
                self._skip(path)

    def _flag_stale(self, preview: mutations.CascadePreview) -> None:
        """Journal `inputs_stale` for done nodes outside the re-run set (WF2-R2 #3)."""
        rerun = set(preview.rerun)
        for path, node in _walk(self.root):
            if not node.id or node.id in rerun:
                continue
            inst = self.instances.get(path)
            if inst is None or inst.state not in SUCCESS_STATES:
                continue
            if not (node_deps(node.config or {}) & rerun):
                continue
            self.journal.write(
                journal_mod.INPUTS_STALE,
                instance_path=path,
                node_id=node.id,
                epoch=inst.epoch,
                stale_deps=sorted(node_deps(node.config or {}) & rerun),
            )

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
            inst.completed_at = _now()
            node = dict(_walk(self.root)).get(_base_path(path))
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
                sub_inst.completed_at = _now()
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

    # ── PP-12 admission: the policies that need a clock and the disk ──────────
    #
    # `frontier()` is pure, so it can apply neither a lease (occupancy that lives on disk, under a
    # TTL) nor a bake floor (elapsed time). Both are still ADMISSION — "may this start now, given
    # persisted state" — so they compose in the same list, through the same `compose()`, against the
    # same `AdmissionRequest`; only the impure inputs are gathered here, by the one object that
    # already owns a clock and the run's state. That split is what keeps the frontier replayable
    # while the new rules still bind for real.

    #: Node config keys the PP-12 policies read. A spec containing none of them never builds an
    #: `AdmissionState` and never asks a question — the pre-PP-12 code path, exactly.
    _ADMISSION_KEYS = ("lease", "min_dwell_secs", "metric_pass")

    #: `root.children[2]` → its parent and index. Sequence children are POSITIONAL in an instance
    #: path (`tick._visit`), which is what makes "the step before this one" answerable at all.
    _CHILD_SEGMENT = re.compile(r"^(?P<parent>.+)\.children\[(?P<index>\d+)\]$")

    async def _admit_ready(self, ready: list[ReadyNode]) -> list[ReadyNode] | None:
        """Apply the lease / bake-floor / metric-gate policies to this tick's ready set.

        Returns what may launch, or `None` when this call FINISHED the run — a metric gate that ran
        out of rollbacks is the loop's `COMPLETE(blocked)`, and a run whose gate has given up must
        say so rather than hold a step forever.
        """
        if not self._declares_admission_keys():
            return list(ready)
        self._adopt_held_leases()
        self._release_settled_leases()
        self._admission_wake = 0.0
        state = self._admission_state(ready)
        wip = execution_hints.from_runtime_hints(
            self.spec.get("runtime_hints")
        ).single_active_feature
        admitted: list[ReadyNode] = []
        stalls: list[str] = []
        for item in ready:
            ok, fatal = self._admit(item, state, wip=wip, stalls=stalls)
            if fatal:
                await self._finish(RunStatus.FAILED, error=fatal)
                return None
            if ok:
                admitted.append(item)
        if (
            not admitted
            and not self._inflight
            and stalls
            and not self._admission_wake
            and not self._pending_mutations
        ):
            # Every refusal this tick was one that cannot change by itself, with nothing running to
            # change it and no rollback queued. Holding forever would be a silent hang; the tick
            # loop's no-deadline path sleeps zero, so it would be a HOT one.
            await self._finish(RunStatus.FAILED, error="; ".join(stalls))
            return None
        return admitted

    def _admit(
        self,
        item: ReadyNode,
        state: AdmissionState,
        *,
        wip: bool,
        stalls: list[str],
    ) -> tuple[bool, str]:
        """One item's verdict: `(may_launch, fatal_error)`.

        The step gate is asked BEFORE the lease is claimed. Reversed, a step held by its metric gate
        would still take the resource and hold it for the whole bake window — a claim nothing is
        going to use.
        """
        claim = self._lease_claim(item.path)
        policies = default_policies(
            self.services.lane_limits or Limits(),
            single_active_feature=wip,
            # The holder is per ITEM, so the snapshot is re-stamped rather than rebuilt: two items
            # of one fan-out must present different identities or the lease would never serialize
            # them (`pool.acquire` treats a same-holder re-acquire as a renewal).
            state=replace(state, holder=claim[1] if claim else ""),
        )
        request = AdmissionRequest(scope=Scope.STEP, key=item.path, node=item.node)
        verdict = compose(policies, request)
        if not verdict.admits(0):
            binding = verdict.binding
            reason = ""
            if isinstance(binding, MetricGate):
                decision = binding.decision(request)
                if decision is not None:
                    reason = decision.reason
                    if decision.action is StepAction.COMPLETE:
                        return False, (
                            f"metric gate on {item.node.id or item.path}: {decision.reason}"
                        )
                    if decision.action is StepAction.ROLLBACK:
                        self._queue_metric_rollback(item, decision)
                    else:
                        stalls.append(
                            f"metric gate on {item.node.id or item.path} holds it: "
                            f"{decision.reason}"
                        )
            self._journal_admission_hold(item, verdict, reason)
            return False, ""
        if claim is None:
            return True, ""

        resource, holder, _scope, ttl = claim
        request = AdmissionRequest(scope=Scope.RESOURCE, key=resource, node=item.node)
        verdict = compose(policies, request)
        if not verdict.admits(0):
            record = state.leases.get(resource)
            self._journal_admission_hold(
                item,
                verdict,
                f"{resource!r} is held by {record.holder!r}" if record else f"{resource!r} is held",
            )
            if record is not None:
                self._note_admission_wake(record.expires_at())
            return False, ""
        lease, error = pool.claim_task(resource, holder=holder, now=state.now, ttl_seconds=ttl)
        if lease is None:
            # The verdict said yes; the flocked compare-and-swap said no. THIS is the authoritative
            # answer — a policy that advised on a stale read and a claim that lost the race are the
            # two halves of one mechanism, and skipping the claim because the advice was positive is
            # exactly the read-then-write S57 measured failing 36 of 40 races.
            self._journal_admission_hold(item, verdict, f"{resource!r} claim lost: {error}")
            self._note_admission_wake(state.now + 1.0)
            return False, ""
        self._held_leases[resource] = holder
        return True, ""

    def _declares_admission_keys(self) -> bool:
        """Whether any node declares a PP-12 key, cached per spec version."""
        cached = self._admission_declared
        version = int(self.run.spec_version)
        if cached is not None and cached[0] == version:
            return cached[1]
        declared = any(
            key in (node.config or {})
            for _path, node in _walk(self.root)
            for key in self._ADMISSION_KEYS
        )
        self._admission_declared = (version, declared)
        return declared

    def _admission_state(self, ready: list[ReadyNode]) -> AdmissionState:
        """Gather the clock-and-disk inputs for this tick's ready set. The only impure step."""
        now = time.time()
        leases: dict[str, pool.Lease] = {}
        ttl = pool.DEFAULT_LEASE_SECS
        since: dict[str, float] = {}
        metrics: dict[str, float] = {}
        floors: dict[str, float] = {}
        rollbacks: dict[str, int] = {}
        for item in ready:
            claim = self._lease_claim(item.path)
            if claim is not None:
                resource, _holder, _scope, ttl = claim
                record = pool.read_lease(resource)
                if record is not None:
                    leases[resource] = record
            config = item.node.config or {}
            if config.get("min_dwell_secs"):
                prior_path, _prior_id = self._prior_step(item.path)
                completed = self._instance(prior_path).completed_at if prior_path else None
                if completed:
                    since[item.path] = _epoch(completed)
            if config.get("metric_pass") is None:
                continue
            value = self._resolve_metric(config.get("metric_from"))
            if value is not None:
                metrics[item.path] = value
            floor = _opt_metric(config.get("metric_floor"))
            if floor is not None:
                floors[item.path] = floor
            prior_path, _prior_id = self._prior_step(item.path)
            if prior_path:
                # `epoch` IS the consecutive-rollback count: every rollback rewinds the prior step,
                # and `mutations.next_epoch` bumps it. Persisted, so the cap survives a restart —
                # an in-memory counter would let a crash-looping run roll back forever.
                rollbacks[item.path] = int(self._instance(prior_path).epoch)
        return AdmissionState(
            now=now,
            leases=leases,
            lease_ttl_secs=ttl,
            since=since,
            metrics=metrics,
            floors=floors,
            rollbacks=rollbacks,
        )

    def _lease_claim(self, path: str) -> tuple[str, str, str, int] | None:
        """`(resource, holder, holder scope, ttl)` for a ready item under a `lease:` declaration.

        The holder scope is what RELEASES the lease: the declaring node itself, or — when the
        declaration is on a container — the ITEM of it this path belongs to, so a `foreach` holding
        `lease: "endpoint"` serializes its items instead of claiming once for the whole fan-out.

        The OUTERMOST declaration wins. One resource per item is deliberate: two would need a claim
        ORDER to stay deadlock-free, and an ordered multi-resource lock manager is the distributed
        substrate this plan's soul guardrail excludes.
        """
        nodes = dict(_walk(self.root))
        segments = path.split(".")
        for i in range(len(segments)):
            prefix = ".".join(segments[: i + 1])
            node = nodes.get(_base_path(prefix))
            if node is None:
                continue
            config = node.config or {}
            resource = str(config.get("lease", "") or "").strip()
            if not resource:
                continue
            scope = prefix
            if i + 1 < len(segments) and "#" in segments[i + 1]:
                scope = ".".join(segments[: i + 2])
            try:
                ttl = int(config.get("lease_ttl_secs") or pool.DEFAULT_LEASE_SECS)
            except (TypeError, ValueError):
                ttl = pool.DEFAULT_LEASE_SECS
            return resource, f"{self.run.id}:{scope}", scope, max(1, ttl)
        return None

    def _prior_step(self, path: str) -> tuple[str, str]:
        """`(instance path, node id)` of the step immediately before `path` in its parent sequence.

        Empty for the first child, or for a node whose parent is not a sequence: a bake floor has
        nothing to measure from and a rollback has nowhere to go, and inventing a target would roll
        back a node the author never put in front of this one.
        """
        match = self._CHILD_SEGMENT.match(path)
        if match is None:
            return "", ""
        index = int(match.group("index"))
        if index == 0:
            return "", ""
        prior = f"{match.group('parent')}.children[{index - 1}]"
        node = dict(_walk(self.root)).get(_base_path(prior))
        return prior, (node.id if node is not None else "")

    def _resolve_metric(self, raw: Any) -> float | None:
        """Resolve `metric_from` against the run's outputs.

        Accepts the dotted form (`verify.score`) and the familiar binding form
        (`{{nodes.verify.output.score}}`) — normalising instead of ignoring, because a metric source
        the engine silently could not read would leave the gate looking enforced while abstaining on
        every tick.
        """
        source = str(raw or "").strip()
        if not source:
            return None
        source = source.strip("{} ").strip()
        if source.startswith("nodes."):
            source = source[len("nodes.") :].replace(".output.", ".", 1)
        cursor: Any = self._outputs
        for key in source.split("."):
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
        return _opt_metric(cursor)

    def _queue_metric_rollback(self, item: ReadyNode, decision: Any) -> None:
        """Roll the prior step back on a metric regression, through the real mutation queue.

        A `rewind` op, not a bespoke reset: `_apply_reentry` already archives outputs, bumps the
        epoch, invalidates the journal region and drops stale approvals, and a second reset path
        would be one that forgets whichever of those it was written before.
        """
        prior_path, prior_id = self._prior_step(item.path)
        if not prior_id:
            return
        key = f"{item.path}@{self._instance(prior_path).epoch}"
        if key in self._rollbacks_queued:
            return
        self._rollbacks_queued.add(key)
        body = self.submit_mutation(
            # `force`, deliberately: without it `mutations.next_epoch` leaves the epoch alone
            # and the journal's inputs-hash tier REPLAYS the step's cached output. A rollback
            # that serves the cache re-produces the metric that failed, so the gate would roll
            # back forever — and the epoch is also the persisted rollback count that caps it.
            [{"kind": "rewind", "node_id": prior_id, "force": True}],
            actor="engine",
            confirm=True,
        )
        self.journal.write(
            journal_mod.DECISION,
            instance_path=item.path,
            node_id=item.node.id,
            decision="metric_rollback",
            detail=(
                f"{decision.reason}; rolling back to {prior_id!r} "
                f"(queued={bool(body.get('queued'))})"
            ),
        )

    def _journal_admission_hold(self, item: ReadyNode, verdict: Any, reason: str) -> None:
        """Record one PP-12 refusal, once. A refusal nobody can read back is indistinguishable from
        a scheduler that lost the node — the same reasoning `_journal_wip_holds` is built on."""
        binding = verdict.binding
        hold = verdict.hold.value or "unrecorded"
        key = f"{item.path}@{hold}"
        if key in self._admission_logged:
            return
        self._admission_logged.add(key)
        self.journal.write(
            journal_mod.DECISION,
            instance_path=item.path,
            node_id=item.node.id,
            decision=f"admission_{hold}",
            detail=(
                f"{getattr(binding, 'name', '') or 'admission'} held this step"
                + (f": {reason}" if reason else "")
            ),
        )

    def _note_admission_wake(self, when: float) -> None:
        """Earliest moment a time-bound hold could change its mind."""
        if when <= 0:
            return
        self._admission_wake = when if not self._admission_wake else min(self._admission_wake, when)

    def _adopt_held_leases(self) -> None:
        """Re-adopt the leases THIS RUN holds, once — the restart half of the release path.

        A fresh controller has no memory of a claim its predecessor made, and only a holder may
        release. The holder string is run-scoped, so the records on disk name this run: adopting
        them is what lets a restarted gateway hand the resource on when the item settles. Without
        it the release would wait for the TTL — correct, but "the endpoint sits idle for fifteen
        minutes" is exactly the outcome a named holder exists to prevent.
        """
        if self._leases_adopted:
            return
        self._leases_adopted = True
        prefix = f"{self.run.id}:"
        root = pool.leases_dir()
        if not root.is_dir():
            return
        for path in sorted(root.glob("*.json")):
            record = pool.read_lease(path.stem)
            if record is not None and record.task_id and record.holder.startswith(prefix):
                self._held_leases[record.task_id] = record.holder

    def _release_settled_leases(self) -> None:
        """Release every lease whose holder scope is finished. The claim is per ITEM, so the release
        is too — holding until the run ends would serialize the whole fan-out on its first item."""
        for resource, holder in list(self._held_leases.items()):
            scope = holder.split(":", 1)[1] if ":" in holder else ""
            if scope and not self._scope_settled(scope):
                continue
            pool.release_task(resource, holder=holder)
            self._held_leases.pop(resource, None)

    def _release_held_leases(self) -> None:
        """Release everything this run holds. Called on the terminal write: a lease outliving its
        run strands the resource until the TTL expires, and the whole point of a named holder is
        that the holder is the one who gives it back."""
        for resource, holder in list(self._held_leases.items()):
            pool.release_task(resource, holder=holder)
        self._held_leases.clear()

    def _scope_settled(self, scope: str) -> bool:
        """Whether every instance at or under `scope` is terminal and none is in flight."""
        if any(path == scope or path.startswith(scope + ".") for path in self._inflight):
            return False
        seen = False
        for path, inst in self.instances.items():
            if path != scope and not path.startswith(scope + "."):
                continue
            seen = True
            if inst.state not in TERMINAL_STATES:
                return False
        return seen

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
        for path, node in _walk(self.root):
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
        by_path = dict(_walk(self.root))
        for path in sorted(self.instances):
            inst = self.instances[path]
            if not path.startswith(prefix) or inst.state != InstanceState.FAILED:
                continue
            index = path[len(prefix) :].split(".", 1)[0]
            if not index.isdigit():
                continue
            node = by_path.get(_base_path(path))
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
                    "node_epoch": inst.epoch,
                    "cached": True,
                },
            )
            return

        if not await self._effect_preflight(item, inst):
            return

        inst.state = InstanceState.RUNNING
        inst.started_at = _now()
        inst.attempt += 1
        if item.has_item and not inst.item_label:
            # Stamped once, at first launch. The items list is re-resolved from a binding on
            # every tick, so after an upstream output changes the label would be unrecoverable
            # — and a retry must show the item it originally got, not whatever now sits at that
            # index.
            inst.item_label = _item_label(item.item)
        if item.node.kind == NodeKind.ACTION:
            # ATTEMPTED goes down BEFORE dispatch: a crash between here and the outcome
            # must leave evidence the effect MAY have fired (WF2-R1).
            self._record_effect(item, inst, EffectStatus.ATTEMPTED)
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
        task = asyncio.create_task(self._execute(item, ctx))
        self._inflight[item.path] = _InFlight(
            task=task, ready=item, started=now, last_progress=now, cache_key=key
        )

    # ── effect ledger (WF2-R1) ──

    def _effect_key(self, item: ReadyNode, inst: NodeInstance) -> str:
        return idempotency_key(self.run.id, item.path, inst.epoch)

    def _record_effect(
        self,
        item: ReadyNode,
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
            instance_path=item.path,
            idempotency_key=key or self._effect_key(item, inst),
            effect_status=status,
            epoch=inst.epoch,
            node_id=item.node.id,
            provider=str((item.node.config or {}).get("provider", "") or ""),
            output_id=str(fields.get("output_id", "") or ""),
            compensation_ref=str(fields.get("compensation_ref", "") or ""),
        )
        self.journal.effect(
            item.path,
            idempotency_key=record.idempotency_key,
            effect_status=status.value,
            epoch=inst.epoch,
            node_id=record.node_id,
            provider=record.provider,
            output_id=record.output_id,
            compensation_ref=record.compensation_ref,
            detail=str(fields.get("detail", "") or ""),
        )
        self._effects.setdefault(item.path, []).append(record)

    async def _effect_preflight(self, item: ReadyNode, inst: NodeInstance) -> bool:
        """The committed-effect boundary, enforced before an action node re-executes.

        Returns False when the node was refused (a terminal state was written). Only
        ACTION nodes are side-effecting dispatches; every other kind passes through.
        A same-epoch retry passes too — it reuses the same idempotency key, which an
        idempotent receiver dedupes, so it is the retry contract working, not a
        double-fire.
        """
        if item.node.kind != NodeKind.ACTION:
            return True
        committed = committed_effect(self._effects.get(item.path, []))
        if committed is None or committed.epoch == inst.epoch:
            return True
        if redo_blocked(item.node.config or {}, committed, inst.epoch):
            inst.state = InstanceState.BLOCKED
            inst.completed_at = _now()
            inst.failure = Failure(
                failure_class=FailureClass.USER,
                cause_plain=(
                    f"node {item.node.id or item.path} has a committed external effect "
                    f"from epoch {committed.epoch}; re-running would fire it again"
                ),
                remediation=(
                    "set `redo_effects: true` on the node to deliberately re-fire "
                    "(a declared teardown runs first), or skip the node"
                ),
                terminal_reason="committed_effect",
            )
            self.journal.step_failed(
                item.path,
                item.node.id,
                epoch=inst.epoch,
                failure=inst.failure,
                attempt=inst.attempt,
                retries_exhausted=True,
            )
            self._persist_state()
            self._publish(
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
            runner = self.services.teardown_runner
            ok, detail = await run_teardown(
                committed.compensation_ref, committed.output_id, runner=runner
            )
            if not ok:
                # A failed teardown leaves an UNKNOWN external state; proceeding would
                # stack a second resource on top of a live first one.
                inst.state = InstanceState.BLOCKED
                inst.completed_at = _now()
                inst.failure = Failure(
                    failure_class=FailureClass.INTERNAL,
                    cause_plain=f"effect teardown failed: {detail}"[:500],
                    remediation="fix the teardown command, or clean up the external "
                    "resource manually and clear redo_effects",
                    terminal_reason="teardown_failed",
                )
                self.journal.step_failed(
                    item.path,
                    item.node.id,
                    epoch=inst.epoch,
                    failure=inst.failure,
                    attempt=inst.attempt,
                    retries_exhausted=True,
                )
                self._persist_state()
                return False
            self._record_effect(
                item,
                inst,
                EffectStatus.COMPENSATED,
                key=committed.idempotency_key,
                output_id=committed.output_id,
                compensation_ref=committed.compensation_ref,
                detail=detail[:500],
            )
        return True

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

    def _with_carried_context(self, node: Node, item: ReadyNode) -> Node:
        """Prepend the previous iteration's handoff/carryover/decisions to a fresh session's prompt.

        This is what makes `session: fresh` mean something. Without it the policy is a label: the
        iteration starts clean and also starts BLIND, re-deriving what the previous one verified —
        which is worse than the continuous session it replaced.

        Prepended, not appended: it is context the reader needs BEFORE the instruction, and a model
        that reads the task first has already begun planning without the constraints.

        A copy, never a mutation — the spec node is shared across every instance of an iterated
        body, and editing it in place would leak iteration 3's context into iteration 1's prompt on
        a rewind. Same reasoning as `_with_retry_hint`.
        """
        prompt = (node.config or {}).get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return node
        carried = self._carried_context(item)
        if not carried:
            return node
        import copy

        out = copy.deepcopy(node)
        out.config["prompt"] = f"{carried}\n\n---\n\n{prompt}"
        return out

    def _worker_model(self) -> str:
        """The concrete model this run's WORKERS resolve to — the family a `cross_model`
        judge gate must avoid (WF2LOO-11).

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
        if self._worker_model_cache is None:
            worker_uc = self.services.model_tiers.get("standard", DEFAULT_MODEL_TIERS["standard"])
            self._worker_model_cache = resolve_axis_model(worker_uc)
        return self._worker_model_cache

    async def _execute(self, item: ReadyNode, ctx: BindingContext) -> NodeResult:
        """Run a dispatcher under the total-timeout knob.

        A timeout here is a REAL kill, not a decorative config value — the studied
        cautionary case is an engine that shipped a no-op node timeout nobody noticed,
        because timeouts only ever execute under failure.
        """
        total = self.services.node_timeout_total
        node = self._with_retry_hint(item)
        # The carried context goes on AFTER the retry hint, so a retried fresh iteration gets both
        # the correction and the handoff. Order between them does not matter — they are appended to
        # different ends — but dropping either on a retry would be a real loss.
        node = self._with_carried_context(node, item)
        # Write-scope snapshot BEFORE the node runs (WF2-R19). Only for nodes that opted
        # in: the walk is real work, and a fan-out of fast transforms must not each pay
        # for a tree scan.
        allowed = scope_allowed(node.config or {}, self.services.cwd)
        # The WATCHED set is wider than the ALLOWED set by necessity: an escape lands
        # outside what is allowed, so snapshotting only the allowed paths would make a
        # violation undetectable by construction.
        watched = scope_watch_roots(node.config or {}, self.services.cwd)
        before = scope_snapshot(watched) if enforces_scope(node.config or {}) else None
        coro = dispatch(
            node,
            # Through the clock seam (PP-6), not `time.time()`: a `wait` computes its deadline
            # against this `now`, and `_wake_due_nodes` later resolves that deadline against the
            # same seam — the two clock reads that decide a parked node's fate must come from ONE
            # clock, or a replay's recorded `now` would set a deadline the recorded wake never
            # crosses. Every non-wait dispatcher ignores `now`.
            ctx,
            now=self._clock(),
            subagents=self.services.subagents,
            depth=self.depth,
            run_id=self.run.id,
            # The owning project, for an ACTION node's provenance (WORK-CONTAINERS §1.6):
            # `knowledge-persist` files its item under this container. Passed from the run
            # record rather than resolved provider-side so one seam owns the attribution.
            project_id=self.run.project_id,
            # This instance's engine key, for an ACTION node's ledger provenance. The controller is
            # the only layer that knows it, and a provider must stamp it rather than its node id:
            # `inspect_node` slices the run ledger by `instance_path`, so a row carrying a bare node
            # id is durably written and invisible in the runs surface. Same `item.path` the stall
            # clock below is bound to, so a row and its progress notes agree on which instance ran.
            instance_path=item.path,
            cwd=self.services.cwd,
            tiers=self.services.model_tiers,
            completion=self.services.completion,
            get_provider=self.services.get_provider,
            verify=self.services.verify,
            # The run's mode decides a gate's deadline: background times out fast and
            # surfaces, blocking waits because a human is right there (WF2-R7).
            mode=self.run.mode,
            # For `subworkflow`: the child must be driven by the SAME supervisor that will adopt
            # it after a restart, so it is passed through rather than resolved from a global.
            supervisor=self.services.supervisor,
            # Feeds the STALL clock (WF2-R5). Bound to this instance's path so a dispatcher cannot
            # accidentally refresh a sibling's clock — and passed at all because without it
            # `timeout_stall` fires on any node slower than the window, which makes the two timeout
            # knobs one knob and kills a node that is visibly working.
            on_progress=lambda path=item.path: self.note_progress(path),
            # The worker model a `cross_model` judge gate must differ from (WF2LOO-11). Resolved
            # from the run's worker axis; only the JUDGE branch reads it, so a run with no
            # cross_model gate pays nothing.
            worker_model=self._worker_model(),
            # The spec's `runtime_hints.judge`, parsed by the contract's own lenient parser
            # (WF2LOO-13) — the same split `execution_hints.from_runtime_hints` does for the
            # execution half. Parsed per dispatch rather than cached: it is a dict walk over a
            # handful of keys, and caching it would have to be invalidated by a live mutation of
            # the spec, which is a correctness risk in exchange for nothing measurable.
            judge_hints=judge_hints_from_dict(
                (self.spec.get("runtime_hints") or {}).get("judge")
                if isinstance(self.spec.get("runtime_hints"), dict)
                else None
            ),
            # This node's compaction history (WV-12). `setdefault` so the list IDENTITY is stable
            # across iterations — the ladder appends to it in place, and handing out a fresh copy
            # each call would record saves nobody ever reads, leaving the anti-thrashing rule
            # permanently looking at an empty history.
            compaction_saves=self._compaction_saves.setdefault(node.id, []),
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
            result = self._check_write_scope(node, result, before, allowed, watched)
        return self._check_success_when(node, result, ctx)

    def _check_success_when(
        self, node: Node, result: NodeResult, ctx: BindingContext
    ) -> NodeResult:
        """Apply a node's declared `success_when` predicate (LOOPS-EVOLUTION R5f).

        **It can only NARROW success, never widen it.** A node that already failed stays
        failed — otherwise `success_when` would be a way to bless a broken node, and the
        first template to discover that would use it as one.

        The use it exists for is INVERTED semantics: `code-project`'s reproduction stage
        must not count as done because it ran. Reproducing the bug (or documenting why it is
        infeasible) IS the success condition, and a stage that quietly failed to reproduce
        and moved on to editing is the exact "no repro, straight to a fix" pattern R5c
        forbids.

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
        self,
        node: Node,
        result: NodeResult,
        before: Any,
        allowed: list[str],
        watched: list[str],
    ) -> NodeResult:
        """Diff the tree and flag writes that escaped the declared scope (WF2-R19).

        DETECTIVE, not preventive: a real sandbox is the OS-seatbelt layer this leaves
        room for. `warn` records and continues; `reject` flips the node to
        `scope_violation` so the escape cannot pass as a clean success.
        """
        report = scope_diff(before, scope_snapshot(watched), allowed)
        if report.clean:
            return result
        mode = scope_mode(node.config or {})
        self.journal.write(
            journal_mod.STEP_SCOPE,
            instance_path="",
            node_id=node.id,
            mode=mode,
            **report.to_dict(),
        )
        logger.warning(
            "workflow %s node %s wrote outside its declared scope: %s",
            self.run.id,
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
                    "node wrote outside its allowed_write_paths: "
                    + ", ".join(report.violations[:5])
                ),
                remediation=(
                    "add the path to the node's `allowed_write_paths`, or fix the node so "
                    "it writes inside the run workspace"
                ),
                terminal_reason="scope_violation",
            ),
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

    def _node_stall_window(self, path: str) -> int:
        """This node's stall window: its own `timeout_stall_secs`, else the run-level default.

        🔴 The per-node override was DECLARED BY FOUR SHIPPED TEMPLATES AND READ BY NOTHING (S147).
        `design-project.refine` asks 600s, `general-project.project` 900s,
        `goal-pursuit-open-ended.work` 900s and `goal-pursuit-verifiable.work` 1200s — and
        `_enforce_stall_timeouts` consulted only `services.node_timeout_stall`, so every one of them
        silently got the 300s default and a legitimately slow node was killed as wedged.

        That is the WRONG DIRECTION to fail in. `timeout_stall` is supposed to mean "silent", not
        "slow" (the heartbeat in `engine._wait_with_progress` exists precisely to keep that
        distinction), and a node whose author measured it needing 20 minutes being cancelled at 5 is
        the failure the knob was added to prevent.

        A node may only RAISE its window, never lower it below the run default: that value is the
        operator's floor for how long a silent node may sit, and letting a template shorten it would
        let a bundled spec tighten an operator's policy. Zero/invalid falls back to the default
        rather than disabling the check — a malformed knob must not switch a safety timeout off.
        """
        node = dict(_walk(self.root)).get(_base_path(path))
        default = int(self.services.node_timeout_stall or 0)
        raw = (node.config or {}).get("timeout_stall_secs") if node is not None else None
        try:
            declared = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            declared = 0
        return max(default, declared) if declared > 0 else default

    def _enforce_stall_timeouts(self) -> None:
        """Kill nodes that have gone silent. The stall knob is separate from the total
        knob on purpose: a long-but-progressing node survives, a wedged one does not.

        The window is PER NODE (`_node_stall_window`) — see that method for the four shipped
        templates whose declared override was inert.
        """
        if not self.services.node_timeout_stall or self.services.node_timeout_stall <= 0:
            return
        now = time.time()
        for path, entry in list(self._inflight.items()):
            stall = self._node_stall_window(path)
            if stall <= 0 or now - entry.last_progress < stall:
                continue
            entry.task.cancel()
            self._inflight.pop(path, None)
            inst = self._instance(path)
            failure = Failure(
                failure_class=FailureClass.TIMEOUT,
                cause_plain=f"no progress for {stall}s (timeout_stall)",
                remediation="the node produced no progress events; check the provider, raise this "
                "node's `timeout_stall_secs`, or raise "
                "workflows.default_node_timeout_stall_secs",
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
            # Gate policy first (WF2-R7): an unattended run auto-approves low-risk gates so
            # it is actually unattended, and a remembered "always allow" honours a decision
            # the user already made. A DESTRUCTIVE gate still asks — an unreviewed
            # destructive action is worse than a stalled run.
            verdict = gate_policy.decide(
                item.node.config or {},
                item.node.id,
                origin_kind=self.run.origin.kind,
                mode=self.run.mode,
                memory=self._allow_memory,
            )
            if verdict.approved:
                inst.state = InstanceState.DONE
                inst.completed_at = _now()
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
                if item.node.kind == NodeKind.ACTION:
                    # Same epoch, same idempotency key: the receiver can dedupe. The
                    # RETRIED record keeps the ledger honest about how many dispatches
                    # the external system may have seen.
                    self._record_effect(item, inst, EffectStatus.RETRIED)
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

        if item.node.kind == NodeKind.ACTION:
            if result.state in SUCCESS_STATES and result.state != InstanceState.NO_CHANGE:
                # COMMITTED captures the teardown ref AT COMMIT TIME: a later spec edit
                # must not change what tears down an already-provisioned resource.
                self._record_effect(
                    item,
                    inst,
                    EffectStatus.COMMITTED,
                    output_id=output_id_of(result.output),
                    compensation_ref=str((item.node.config or {}).get("teardown", "") or ""),
                )
            elif result.state == InstanceState.NO_CHANGE:
                # The provider reported `skip` — nothing fired, and the ledger says so.
                self._record_effect(item, inst, EffectStatus.SKIPPED)

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
            self._project_task(item, inst, result)
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
                "node_epoch": inst.epoch,
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

    def _emit_judge_divergence(
        self, instance_path: str, node_id: str, human_approved: bool
    ) -> None:
        """Record a human overriding a judge on the same gate (LOOPS-EVOLUTION R3).

        Reads this node's last `judge_verdict` from the ledger; emits `judge_divergence` only when
        the human's decision actually contradicts it. No prior judge verdict (an ordinary approval
        gate) → nothing to diverge from, so nothing is written.
        """
        if not node_id:
            return
        verdicts = journal_mod.ledger(self.run.id, kinds={journal_mod.JUDGE_VERDICT})
        mine = [v for v in verdicts if v.get("node_id") == node_id]
        if not mine:
            return
        judge_verdict = str(mine[-1].get("verdict", ""))
        judged_pass = judge_verdict.upper() == "PASS"
        if judged_pass == human_approved:
            return  # judge and human agree — not a divergence
        record = judge_calibration.DivergenceRecord(
            run_id=self.run.id,
            node_id=node_id,
            template=str(self.run.workflow_name or ""),
            judge_verdict=judge_verdict,
            human_verdict="PASS" if human_approved else "REJECT",
        )
        self.journal.write(journal_mod.JUDGE_DIVERGENCE, **record.to_dict())

    def _consume_steering(self, parent_path: str, node: Node, iteration: int) -> None:
        """Consume mid-run steering at the loop boundary (LOOPS-EVOLUTION R14).

        The durable queue (`run.extra["steering_queue"]`, written by `service.steer_run`) is
        drained HERE — under the lock, between iterations, exactly like `_drain_mutations`. Mid-
        iteration injection would race the worker's own state; the boundary is where the next
        iteration can actually act on the instruction. Single-use: the queue is cleared and the
        rendered block parked on `_steering_inject` for the next iteration's prompt, so a resume
        cannot replay it. Journaled so a refiner can tell a human-steered verdict from an
        autonomous one — without the event the two are indistinguishable (see journal.STEERING).
        """
        pending = self.run.extra.get("steering_queue")
        if not isinstance(pending, list) or not pending:
            return
        self.run.extra["steering_queue"] = []
        queue = InterruptQueue()
        for entry in pending:
            text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
            queue.push(text, now=time.time())
        consumed = queue.consume(now=time.time())
        texts = [i.text for i in consumed]
        if not texts:
            self._save_run()
            return
        block = queue.as_steering_prompt(consumed)
        # Append if a block is already parked (two steers before the next iteration read either):
        # the newest instruction goes last, the same order `consume` preserves.
        existing = self._steering_inject.get(parent_path)
        self._steering_inject[parent_path] = f"{existing}\n\n{block}" if existing else block
        self.journal.write(
            journal_mod.STEERING,
            instance_path=parent_path,
            node_id=node.id,
            iteration=iteration,
            count=len(texts),
            texts=texts,
        )
        self._publish(
            "workflow_steering_consumed",
            {"instance_path": parent_path, "node_id": node.id, "count": len(texts)},
        )
        # Persist the drained queue immediately: a crash between here and the next tick must not
        # resurrect an instruction the ledger already records as consumed.
        self._save_run()

    # ── convergence: ONE decision, `loop.tick.evaluate` (PP-15) ──────────────

    def _supervisor_policy(self, node: Node) -> supervisor_policy.SupervisorPolicy:
        """The loop's declared convergence policy, or the default posture.

        This is the call that makes `SupervisorPolicy` load-bearing: the thresholds
        `evaluate` reads come from the TEMPLATE's `supervisor:` block, not from constants
        buried in the engine. A node that declares none gets the default policy, whose
        values reproduce what the engine did before it was consulted.
        """
        return supervisor_policy.parse_supervisor_policy((node.config or {}).get("supervisor"))

    def _convergence_ledger(self, parent_path: str) -> dict[str, Any]:
        """This loop's persisted convergence position, on the run row.

        `run.extra` and not the event ledger, deliberately. The position has to be PERSISTED —
        an in-memory cursor is exactly what the deleted `check_middleware` kept, and it makes
        the ladder a property of this PROCESS's uptime, so the same run answers differently
        before and after a crash. But it must not be written as an `iteration` row either: that
        kind means "the loop body ran once", and a decision *about* the body is not another
        body run. Recording it there inflates every consumer's iteration count, including the
        `max_iterations` cap a user set.
        """
        book = self.run.extra.setdefault("convergence", {})
        if not isinstance(book, dict):
            book = {}
            self.run.extra["convergence"] = book
        entry = book.setdefault(parent_path, {})
        if not isinstance(entry, dict):
            entry = {}
            book[parent_path] = entry
        return entry

    def _convergence_state(
        self, parent_path: str, node: Node, breaker: BreakerState, *, stall: str = ""
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
        book = self._convergence_ledger(parent_path)
        signatures = [s for s in breaker.error_signatures if s]
        critique = self.run.extra.get("plan_critique")
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
        self,
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
        book = self._convergence_ledger(parent_path)
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
        self._save_run()

    def _replan_ops(
        self, node: Node, decision: convergence.Decision, *, attempt: int
    ) -> list[dict[str, Any]]:
        """The REAL mutation batch a `REPLAN` queues.

        Not a retry with the critique stapled to the prompt — that is what this replaces, and it
        is indistinguishable from the failing attempt in the spec, in `spec_history` and to a
        human reading either. An `insert` CHANGES the plan: the run's remaining steps now include
        a step that re-derives them from the critique, the spec version bumps, and the change is
        auditable. Placed immediately after the loop in the root sequence, so it is the next
        thing the run does with the work the critique rejected.
        """
        root = self.spec.get("root") or {}
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

    def _converge_loop(
        self,
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
        policy = self._supervisor_policy(node)
        cfg = convergence_config(policy)
        breaker = self._breakers.setdefault(parent_path, BreakerState())
        state = self._convergence_state(parent_path, node, breaker, stall=breaker_reason)
        # `time.time()`, NOT `_now()`: the run clock is an ISO string and `evaluate` does
        # arithmetic on `now` (the dwell branch). Passing the display clock here is a TypeError
        # at the first tripped breaker — the one path a happy-path test never reaches.
        decision = convergence.evaluate(cfg, state, time.time())
        self._record_convergence(parent_path, cfg, state, decision)
        self._publish(
            "workflow_loop_converged",
            {"instance_path": parent_path, "node_id": node.id, **decision.to_dict()},
        )

        if decision.action is convergence.Action.REPLAN:
            ops = self._replan_ops(node, decision, attempt=state.replans_taken + 1)
            result = (
                self.submit_mutation(ops, actor="supervisor", confirm=True)
                if ops
                else {"ok": False, "issues": [{"code": "WF_REPLAN_NO_TARGET"}]}
            )
            if not result.get("queued"):
                # A replan that could not be queued is not a replan. Surfacing beats looping on
                # a plan the engine has just declared unsound.
                self._surface_loop(parent_path, node, reason=decision.reason, detail=str(result))
                return True
            # Consumed, so the next tick does not re-decide REPLAN against the same critique and
            # spend the whole budget re-deriving one plan.
            self.run.extra.pop("plan_critique", None)
            self._save_run()
            return False

        if decision.surfaced:
            self._surface_loop(
                parent_path,
                node,
                reason=decision.reason or breaker_reason,
                detail=decision.detail or breaker_detail,
            )
            return True

        if decision.nudge_text:
            existing = self._steering_inject.get(parent_path)
            self._steering_inject[parent_path] = (
                f"{existing}\n\n{decision.nudge_text}" if existing else decision.nudge_text
            )
        return False

    def _surface_loop(self, parent_path: str, node: Node, *, reason: str, detail: str) -> None:
        """Hand a loop to a human. ESCALATED, deliberately NOT FAILED: "I gave up and a human
        must decide" is a different fact from "this broke", and collapsing them loses what the
        user needs to act on."""
        loop_inst = self._instance(parent_path)
        loop_inst.state = InstanceState.ESCALATED
        loop_inst.completed_at = _now()
        self._escalate(parent_path, node.id, reason=reason, detail=detail)

    def _advance_loop(self, item: ReadyNode) -> None:
        """Advance a loop's iteration counter when its body finished an iteration.

        The counter lives here rather than in the frontier because advancing it is a
        WRITE, and the frontier is pure. `loop_should_continue` keeps the decision itself
        pure and testable.
        """
        parent_path, iteration = _loop_parent(item.path)
        if parent_path is None:
            return
        node = dict(_walk(self.root)).get(_base_path(parent_path))
        if node is None or node.kind != NodeKind.LOOP:
            return
        if not self._iteration_complete(node, parent_path, iteration):
            # A CONTAINER-bodied loop calls this once per leaf. Advancing on the first one
            # would end the iteration mid-cycle: the wait would complete, the counter would
            # move, and the synthesize stage after it would be scheduled into the NEXT
            # iteration's path — where nothing had produced its inputs.
            return
        output = self._outputs.get(item.node.id)
        if self._iteration_is_dry(node, parent_path, iteration, output):
            self._dry_streaks[parent_path] = self._dry_streaks.get(parent_path, 0) + 1
        else:
            self._dry_streaks[parent_path] = 0

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
        inst = self._instance(item.path)
        breaker = self._breakers.setdefault(parent_path, BreakerState())
        breaker.record(
            signature=error_signature(inst.failure) if inst.failure else "",
            output=output,
            tokens=inst.tokens,
        )
        verdict = check_breaker(node, breaker)
        if verdict.tripped:
            self.journal.iteration(
                parent_path,
                node.id,
                iteration=iteration,
                outcome=f"breaker:{verdict.reason}",
                error_signature=breaker.error_signatures[-1] if breaker.error_signatures else "",
                tokens=inst.tokens,
            )
            if verdict.reason in _BUDGET_TRIPS:
                # A satisfied budget is not a stall. Unchanged pre-PP-15 behaviour.
                self._surface_loop(parent_path, node, reason=verdict.reason, detail=verdict.detail)
                return
            if self._converge_loop(
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
        self._consume_steering(parent_path, node, iteration)

        ctx = BindingContext(
            inputs=self.run.inputs,
            node_outputs=self._outputs,
            node_artifacts=self._node_artifacts(),
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
        # Mark the seen-set only now — AFTER the iteration succeeded. Marking at read time
        # means a cycle that dies mid-synthesis has already suppressed items it never
        # processed, and nothing will ever surface them again (§4.1).
        self._mark_seen(parent_path, node, output)
        # Capture what this iteration hands to the next, BEFORE the counter advances (WF2-R6).
        # Journaled rather than held in memory so a rewind to this iteration replays the handoff
        # it actually had, instead of reconstructing one from a transcript that no longer exists —
        # which is the summarization failure handoffs exist to replace.
        self._capture_iteration_context(parent_path, node, iteration, output)
        if keep_going:
            self._iterations[parent_path] = iteration + 1
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
        if final.tripped and final.reason not in _BUDGET_TRIPS:
            self._surface_loop(parent_path, node, reason=final.reason, detail=final.detail)
            return

        loop_inst = self._instance(parent_path)
        loop_inst.state = InstanceState.DONE
        loop_inst.completed_at = _now()

    def _iteration_complete(self, node: Node, parent_path: str, iteration: int) -> bool:
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
            {p: i.state for p, i in self.instances.items()},
            declined_edges=self._declined_edges,
            outputs=self._outputs,
            inputs=self.run.inputs,
            iterations=self._iterations,
        )
        return state in TERMINAL_STATES

    # ── until_dry dryness (WF2LOO-14) ──

    def _iteration_is_dry(self, node: Node, parent_path: str, iteration: int, output: Any) -> bool:
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
        found, value = self._progress_value(node, parent_path, iteration, field)
        if not found:
            return _is_dry(output)
        reading = _progress_reading(value)
        if reading == _UNREADABLE:
            # A type with no rule is not evidence of dryness (e.g. an oversize output whose
            # inline preview is a `result_omitted` stub). Same direction as absence.
            return _is_dry(output)
        return reading == _DRY

    def _progress_value(
        self, node: Node, parent_path: str, iteration: int, field: str
    ) -> tuple[bool, Any]:
        """This iteration's value for `field` as `(found?, value)`.

        `found` is separate from the value because ``None`` is a legitimate DRY reading —
        "the body said nothing" — and absence is not; collapsing them would make a missing
        key end the loop.

        Scans the loop BODY, not just the output `_advance_loop` is holding. That output is
        the last leaf to finish, and both shipped templates that declare a progress field
        put it on the FIRST stage of a sequence body and end each iteration on a judge stage
        whose schema has no such key — so reading only the last leaf would leave this control
        inert for exactly the templates that asked for it.

        Restricted to nodes whose instance for THIS iteration succeeded: `self._outputs` is
        keyed by node id, so a body node that did not run this time still holds the PREVIOUS
        iteration's value, and reading that would report last iteration's progress as this
        one's. The last match in document order wins — the iteration's latest word on its
        own progress.
        """
        if node.body is None:
            return False, None
        base = f"{parent_path}.body@{iteration}"
        found, value = False, None
        for sub, child in _walk(node.body):
            if not child.id:
                continue
            inst = self.instances.get(base if sub == "root" else f"{base}{sub[len('root'):]}")
            if inst is None or inst.state not in SUCCESS_STATES:
                continue
            out = self._outputs.get(child.id)
            if isinstance(out, dict) and field in out:
                found, value = True, out[field]
        return found, value

    # ── long-run watcher state (KNOWLEDGE-SYNTHESIS §4) ──

    def _mark_seen(self, parent_path: str, node: Node, output: Any) -> None:
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
        seen = self._seen.setdefault(parent_path, longrun.SeenSet())
        added = seen.mark_all(items)
        if not added:
            return
        self.journal.write(
            journal_mod.SEEN_SET,
            instance_path=parent_path,
            node_id=node.id,
            seen=seen.to_dict(),
            added=added,
            total=len(seen),
        )

    # ── context lifecycle (WF2-R6) ──

    def _capture_iteration_context(
        self, parent_path: str, node: Node, iteration: int, output: Any
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
        inst = self._instance(parent_path)
        try:
            handoff = context_mod.Handoff.from_dict(output.get("handoff"))
            if not handoff.empty:
                self._handoffs[parent_path] = handoff
                self.journal.handoff(
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
                merged = self._carryover.get(parent_path, context_mod.Carryover()).merge(fresh)
                self._carryover[parent_path] = merged
                self.journal.carryover(
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
                self._decisions.setdefault(parent_path, []).append(decision)
                self.journal.decision(
                    parent_path, node.id, epoch=inst.epoch, decision=decision.to_dict()
                )

            # LEARN-R18: a node that made a measurable bet says so in a `pending_outcome`
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
                self.journal.pending_outcome(
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
                self.run.id,
                parent_path,
                exc_info=True,
            )

    def _rehydrate_context(self) -> None:
        """Rebuild the context lifecycle from the ledger on start/resume (WF2-R6).

        Replays in ledger ORDER, so the last handoff per container wins and the carryover arrives
        already merged (each write journaled the merged state, so the final one is complete). A
        rewind's records are naturally excluded because a rewind archives the region's journal
        entries — the replay sees only what is still live.

        Never raises: a resumed run must start even if its ledger is partly unreadable. It would
        start context-blind, which is worse than nothing but far better than not starting.
        """
        try:
            records = journal_mod.ledger(
                self.run.id,
                kinds={
                    journal_mod.HANDOFF,
                    journal_mod.CARRYOVER,
                    journal_mod.DECISION,
                    journal_mod.SEEN_SET,
                },
            )
        except Exception:
            logger.debug("run %s: could not read the context ledger", self.run.id, exc_info=True)
            return
        for rec in records:
            path = str(rec.get("instance_path", "") or "")
            if not path:
                continue
            kind = rec.get("kind")
            try:
                if kind == journal_mod.HANDOFF:
                    self._handoffs[path] = context_mod.Handoff.from_dict(rec)
                elif kind == journal_mod.CARRYOVER:
                    # Each journaled bucket set is already the MERGED state at that point, so the
                    # later record replaces rather than re-merges — re-merging would be harmless but
                    # would quietly double the dedup work on every resume.
                    self._carryover[path] = context_mod.Carryover.from_dict(rec)
                elif kind == journal_mod.DECISION:
                    decision = context_mod.Decision.from_dict(rec)
                    if not decision.empty:
                        self._decisions.setdefault(path, []).append(decision)
                elif kind == journal_mod.SEEN_SET:
                    # Each record carries the WHOLE set at that point, so the last one wins —
                    # the same reason as CARRYOVER. A delta encoding would be smaller but would
                    # make a partially-unreadable ledger reconstruct a WRONG set rather than an
                    # old one, and an old seen-set only costs tokens.
                    self._seen[path] = longrun.SeenSet.from_dict(rec.get("seen") or {})
            except Exception:
                logger.debug("run %s: skipping unreadable context record", self.run.id)

    def _carried_context(self, item: ReadyNode) -> str:
        """The context block a `session: fresh` iteration starts from, or "".

        Only for a node INSIDE an iterated container: a top-level node has no previous iteration to
        inherit from, and injecting an empty block would teach a model the section is noise.

        `session: continuous` returns "" deliberately — a continuous session already HAS the
        previous iteration's context in its transcript, and prepending a handoff to it would say
        everything twice.
        """
        parent_path, _iteration = _loop_parent(item.path)
        if parent_path is None:
            return ""
        node = dict(_walk(self.root)).get(parent_path)
        if node is None:
            return ""
        # Steering (R14) reaches BOTH session policies: a mid-run instruction must land even in a
        # `continuous` loop, whose transcript otherwise carries no fresh block. Consume it single-
        # use — once it is in a prompt, a re-render must not repeat it. It goes LAST, after the
        # handoff/carryover, because it is the newest and highest-priority instruction.
        steer = self._steering_inject.pop(parent_path, "")
        if context_mod.session_policy(node.config) != context_mod.SESSION_FRESH:
            return steer
        carried = context_mod.render_context(
            handoff=self._handoffs.get(parent_path),
            carryover=self._carryover.get(parent_path),
            decisions=self._decisions.get(parent_path),
        )
        if steer:
            return f"{carried}\n\n{steer}" if carried else steer
        return carried

    # ── binding context ──

    def _session_brief(self) -> Any:
        """The project brief, built lazily and cached for the run.

        Never raises and never blocks the run: a brief is an enhancement, so a store that cannot
        answer means the run starts without the digest rather than not starting.
        """
        if self._brief is not None:
            return self._brief
        project = str(getattr(self.run, "project_id", "") or "")
        if not project:
            # No project means no scope. An unscoped brief would be "everything the user knows",
            # injected into every run — expensive, and wrong about what the run is for.
            self._brief = session_brief.SessionBrief()
            return self._brief
        try:
            from personalclaw.config.loader import AppConfig
            from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

            path = knowledge_db_path()
            if not path.is_file():
                self._brief = session_brief.SessionBrief(project=project)
                return self._brief
            budget = int(
                getattr(
                    AppConfig.load().knowledge,
                    "session_brief_max_tokens",
                    session_brief.DEFAULT_MAX_TOKENS,
                )
            )
            self._brief = session_brief.build(
                KnowledgeStore(db_path=str(path)), project_id=project, max_tokens=budget
            )
        except Exception:
            logger.debug("run %s: session brief unavailable", self.run.id, exc_info=True)
            self._brief = session_brief.SessionBrief(project=project)
        return self._brief

    def _context_for(self, item: ReadyNode) -> BindingContext:
        watcher_path = self._enclosing_watcher(item.path)
        seen = self._seen.get(watcher_path) if watcher_path else None
        return BindingContext(
            inputs=dict(self.run.inputs),
            node_outputs=dict(self._outputs),
            node_artifacts=self._node_artifacts(),
            item=item.item,
            has_item=item.has_item,
            iter_index=item.iter_index,
            sibling_outputs=self._sibling_outputs(item.path),
            previous_output=self._previous_output(item.path),
            has_previous=self._previous_output(item.path) is not None,
            seen_filter=seen.unseen if seen else None,
            brief=self._session_brief(),
            secret_resolver=_secret_resolver,
        )

    def _node_artifacts(self) -> dict[str, str] | None:
        """node id → artifact ref, for outputs the journal OFFLOADED (WV-11).

        This is the writer that closes the `node_artifacts` seam: `{{nodes.x.artifact}}`
        resolves to a live pointer only for nodes whose output spilled past
        `MAX_INLINE_OUTPUT_BYTES` (or was binary). An offloaded output's `output_ref` does not
        start with `outputs/` — that is exactly the distinction `store.store_output` records
        when it writes to `artifacts/` instead. Derived from instance refs (the durable record),
        not the in-memory preview map, so it survives a restart and a rewind the same way
        `node_outputs` does. Only SUCCESS states contribute — a failed node's leftover ref must
        not resolve as if it were a real artifact.
        """
        by_path = {path: node for path, node in _walk(self.root)}
        artifacts: dict[str, str] = {}
        for path, inst in self.instances.items():
            if inst.state not in SUCCESS_STATES or not inst.output_ref:
                continue
            if inst.output_ref.startswith("outputs/"):
                continue
            node = by_path.get(_base_path(path))
            if node is None or not node.id:
                continue
            artifacts[node.id] = inst.output_ref
        return artifacts or None

    def _sibling_outputs(self, path: str) -> dict[str, list[Any]] | None:
        """Accumulated outputs of the node's siblings inside its enclosing `parallel`.

        A LIST per sibling and not just its current output: a watcher reads a sibling that is
        still producing, so "the output" is the wrong shape — the synthesizer needs the
        accumulation to see a trend, which is the whole reason the binding exists (§4.2).

        Accumulated from the JOURNAL rather than from `self._outputs`, because a loop body
        overwrites its node-id output every iteration: reading the live map would show cycle
        50 and nothing before it, and the window/seen-set machinery would have nothing to
        bound.
        """
        tree = dict(_walk(self.root))
        container = _enclosing_parallel(path, tree)
        if container is None:
            return None
        node = tree.get(_base_path(container))
        if node is None or not node.children:
            return None
        out: dict[str, list[Any]] = {}
        for index, child in enumerate(node.children):
            cpath = f"{container}.children[{index}]"
            if path == cpath or path.startswith(f"{cpath}."):
                continue  # a node is not its own sibling
            if not child.id:
                continue
            out[child.id] = self._accumulated_outputs(cpath)
        return out or None

    def _accumulated_outputs(self, subtree: str) -> list[Any]:
        """Every output a subtree has produced, oldest first.

        Read from the journal's stored outputs so loop iterations accumulate instead of the
        newest overwriting the rest.
        """
        acc: list[Any] = []
        for spath in sorted(
            (p for p in self.instances if p == subtree or p.startswith(f"{subtree}.")),
            key=_natural_key,
        ):
            inst = self.instances[spath]
            if inst.state not in SUCCESS_STATES or not inst.output_ref:
                continue
            # By INSTANCE PATH, not by `output_ref`: the ref is already the run-relative file
            # path, and `read_output` derives the filename from what it is given — passing the
            # ref would hash a hash and read nothing, silently, forever.
            value = store.read_output(self.run.id, spath)
            if value is not None:
                acc.append(value)
        return acc

    def _previous_output(self, path: str) -> Any:
        """The prior successful cycle of the enclosing loop, for diff-aware synthesis.

        Distinct from `{{last.output}}`, which is the previous iteration of the loop the node
        is IN. `previous` is the prior cycle of the whole watcher body, which for a `sequence`
        body is what a synthesis stage actually wants: its own last report, not the output of
        whichever node happened to run before it.
        """
        watcher = self._enclosing_watcher(path)
        if watcher is None:
            return None
        current = int(self._iterations.get(watcher, 0))
        if current <= 0:
            return None
        node = dict(_walk(self.root)).get(watcher)
        if node is None or node.body is None:
            return None
        prior = self._accumulated_outputs(f"{watcher}.body@{current - 1}")
        return prior[-1] if prior else None

    def _enclosing_watcher(self, path: str) -> str | None:
        """The nearest enclosing `until_cancelled` loop path, or None."""
        for candidate, node in _walk(self.root):
            if node.kind != NodeKind.LOOP:
                continue
            if str((node.config or {}).get("mode", "") or "") != LoopMode.UNTIL_CANCELLED.value:
                continue
            if path == candidate or path.startswith(f"{candidate}."):
                return candidate
        return None

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
        now = self._clock()
        for path, inst in list(self.instances.items()):
            if inst.state != InstanceState.WAITING or not inst.wake_at:
                continue
            if inst.wake_at > now:
                continue
            crossed = inst.wake_at
            inst.wake_at = 0.0
            node = dict(_walk(self.root)).get(_base_path(path))
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
        if status in TERMINAL_RUN_STATUSES:
            # PP-12: give the resources back. A lease that outlives its run strands the resource
            # until the TTL runs down, and the next run would sit held by a holder that no longer
            # exists — the one failure mode a named holder is supposed to make impossible.
            self._release_held_leases()
            # A run that ended answers its own outstanding questions by ending: nothing about
            # it is actionable now. Leaving the rows open would put a permanently unanswerable
            # gate in the inbox — cancel a run mid-gate and the question survives the run.
            # NEEDS_INPUT is deliberately not terminal here: that run is waiting, not finished.
            attention.resolve_run_items(self.services.attention_state, self.run.id)
            if status == RunStatus.COMPLETE:
                self._revise_project_overview()
            self._capture_run_end()
        self._publish("workflow_run_update", {"status": status.value, "error": error})
        if status in TERMINAL_RUN_STATUSES:
            await self._drain_overlap_queue()

    async def _drain_overlap_queue(self) -> None:
        """Start the next `on_overlap: queue` run for this def, now that this one has ended.

        The live call site for the queue (WV-14). It belongs here because this is the moment
        the def stops being busy: `_save_run` above has already written the terminal status,
        so `store.active_runs()` no longer counts this run and the drain's own re-check sees
        a free def.

        Awaited inline rather than fired as a task, deliberately: a floating task makes the
        handoff untestable ("did it start?" becomes a race) and can outlive the loop that
        created it. Fully guarded, because `_finish` is the single terminal writer (WF2-R10)
        and MUST NOT raise — a failure here costs the NEXT run's start, never this run's
        recorded outcome, and the watchdog's poll re-drains what this missed.
        """
        supervisor = getattr(self.services, "supervisor", None)
        if supervisor is None:
            return
        try:
            from personalclaw.workflows import overlap

            await overlap.drain(self.run.workflow_name, supervisor)
        except Exception:
            logger.debug("run %s: overlap drain failed", self.run.id, exc_info=True)

    def _capture_run_end(self) -> None:
        """Route a terminal run through the LearningGate → run-end learner (LEARNING-FLYWHEEL §3.3).

        The RUN_END cadence. Best-effort and fully guarded: `_finish` is the single terminal
        writer (WF2-R10) and MUST NOT raise, so a failure here costs a lesson, never the run's
        terminal status.

        Inert unless a memory service with a live vector store was injected into EngineServices —
        every test and CLI path leaves `services.memory` None, so this no-ops there exactly as
        `self_model_observer.observe_turn` no-ops without `has_vector`. The gate is what honors
        success criterion 10: an incognito/temporary session's terminal run is denied here (via
        the session-key restriction registry) and writes nothing through this cadence, and
        `learning.run_end_enabled=False` turns the cadence off without touching the others.
        """
        service = getattr(self.services, "memory", None)
        if service is None or not getattr(service, "has_vector", False):
            return
        try:
            from types import SimpleNamespace

            from personalclaw.config.loader import AppConfig
            from personalclaw.learning import run_end
            from personalclaw.learning.gate import Cadence, LearningGate

            cfg = AppConfig.load().learning
            # The session that started the run is likely gone by terminal time. `for_session` then
            # reads the process-global registry by key (`_enforce_inherited_mode` marked it at
            # start), AND the `is_restricted` flag carried on this namespace — set from the RUN's
            # inherited mode, not hardcoded False. The run record is the durable authority: a
            # registry mark can evict from the bounded LRU over a long run, and reading
            # `is_restricted=False` there would re-open the gate an incognito origin closed. Belt
            # (record) and suspenders (registry), fail-closed by construction.
            restricted = ownership.run_mode(self.run) in ownership.WRITE_SUPPRESSED
            session = SimpleNamespace(
                key=self.run.origin.session_key, is_restricted=restricted, _ephemeral=False
            )
            decision = LearningGate.for_session(session, cfg).decide(
                Cadence.RUN_END, cadence_enabled=bool(getattr(cfg, "run_end_enabled", True))
            )
            if not decision.allowed:
                logger.debug(
                    "run %s: run-end capture gated (%s)", self.run.id, decision.reason.value
                )
                return
            run_end.capture(self.run, service, journal=journal_mod)
        except Exception:
            logger.debug("run %s: run-end capture failed", self.run.id, exc_info=True)

    def _revise_project_overview(self) -> None:
        """Auto-revise the run's project overview on a successful completion (WORK-CONTAINERS §6.1).

        DETERMINISTIC by design: this appends a terse line (run name + terminal status +
        a one-line summary drawn from the run's handoff/summary, else its workflow name) to
        the living overview and records the outcome in the decisions ledger. It does NOT
        call an LLM — the `completion` service is inert in prod, and an LLM-summarized
        overview is an explicit follow-on. This is a DEVIATION from a literal "revise"
        (append, not summarize), recorded in the plan's Execution log.

        Best-effort and fully guarded: `_finish` is the single terminal writer and MUST
        NOT raise, so a failure here costs the overview line, never the terminal status.
        """
        pid = self.run.project_id
        if not pid:
            return
        try:
            name = self.run.workflow_name or "run"
            summary = self._completion_summary()
            line = f"- {name} → {self.run.status.value}"
            if summary:
                line += f": {summary}"
            current = project_context.read_overview(pid)
            new_text = f"{current}\n{line}" if current else line
            project_context.write_overview(pid, new_text)
            project_context.append_ledger(
                pid,
                "decisions",
                f"{name} → {self.run.status.value}",
                link=self.run.id,
            )
        except Exception:
            logger.debug("project overview revision skipped for %s", self.run.id, exc_info=True)

    def _completion_summary(self) -> str:
        """A one-line summary for the overview append: the run's own handoff, else "".

        Pulled from the last recorded handoff's summary — what the run itself said it
        produced — rather than fabricated. A run that said nothing hands over nothing, so
        the line falls back to just name + status, which is honest.
        """
        try:
            handoff = getattr(self.run, "extra", {}).get("summary") if self.run.extra else ""
            if isinstance(handoff, str) and handoff.strip():
                return handoff.strip().splitlines()[0][:200]
        except Exception:
            return ""
        return ""

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
            # DERIVED from the instance map rather than cached at expansion: the expander
            # already created one instance per item, so counting siblings is the same number
            # with no second copy of it to go stale after a rewind re-expands the fan-out.
            base = _base_path(item.path)
            total = sum(1 for p in self.instances if _base_path(p) == base)
            if total > 1:
                out["item_total"] = total
        label = _item_label(item.item)
        if label:
            out["item_label"] = label
        return out

    # ── TASKS-SOPS task projection (S61f) ──

    def _projected_tasks(self) -> list[Any]:
        """What this run has already projected, for `plan_materialization`'s dedup.

        Held in memory on the controller rather than read from the task store per node: the store is
        per-entity JSON, so a read per settled node is one file scan per node, and the controller is
        the single writer for its own run. A restart re-reads from the store via the projection
        rebuild (§1 makes full recompute the normal path), so nothing is lost by not persisting the
        cache itself.
        """
        return [type("_T", (), {"workflow_binding": b})() for b in self._projected]

    def _schedule_verification(self, spec: Any, path: str, node_id: str) -> None:
        """Run a projected node's done-criterion and emit `task_verified`.

        Scheduled, not inline: a criterion is a shell command or a file read (`pytest -q` is the
        canonical authoring shape), and running it inside the sync settle path would block the whole
        tick on someone else's test suite.

        A node with NO criterion schedules nothing. `Task.can_mark_complete`'s rule is that a task
        with no exit criteria is freely completable, and emitting a `task_verified(passed=True)` for
        a node nobody wrote a check for would manufacture evidence that does not exist.

        The emptiness test asks the PARSER, not truthiness. Measured (S61h): `"   "` is truthy, so a
        whitespace-only criterion passed a `if not criterion` guard and then parsed to zero checks —
        which the evaluator correctly reports as UNRUNNABLE, so the node showed a scary "could not
        verify" for a field its author had effectively left blank.
        """
        criterion = getattr(spec, "done_criterion", "")
        from personalclaw.workflows import verified_done as _vd

        checks, problems = _vd.parse_criterion(criterion)
        if not checks and not problems:
            return

        async def _verify_and_emit() -> None:
            passed = await self._run_criterion(spec.done_criterion)
            self.publish_task_verified(
                path,
                node_id,
                task_id="",
                # NOT `bool(passed)`: `None` means the check could not run, and collapsing it to
                # False reports a failure that never happened.
                passed=passed,
                criterion=str(spec.done_criterion)[:200],
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_verify_and_emit())
            return
        handle = loop.create_task(_verify_and_emit())
        self._projection_writes.add(handle)
        handle.add_done_callback(self._projection_writes.discard)

    async def _run_criterion(self, raw: Any) -> bool | None:
        """Evaluate a done-criterion to the TRISTATE `verified_done` expects.

        `None` (could not run) is preserved all the way out, never collapsed to False. The whole
        point of the tristate is that a missing binary is not a failing test: reporting "the check
        failed" for a criterion that never executed sends the user to debug their code when the
        problem is their environment, and §1 projects the two to different blocked kinds for exactly
        that reason.
        """
        from personalclaw.workflows import verified_done as _vd

        checks, problems = _vd.parse_criterion(raw)
        if problems or not checks:
            # An unparseable criterion is UNRUNNABLE, not failed. The author wrote something the
            # engine could not read, which is a different problem from the work being wrong.
            return None
        verdict = _vd.Verdict()
        for check in checks:
            if check.kind is _vd.CheckKind.COMMAND:
                from personalclaw.loop.gates import run_verify_command

                outcome = await run_verify_command(
                    check.command, None, label=f"criterion:{self.run.id}"
                )
                verdict.results.append(
                    _vd.CheckResult(
                        kind=check.kind.value,
                        passed=outcome,
                        weight=check.weight,
                        detail=check.command[:120],
                    )
                )
            else:
                verdict.results.append(_vd.evaluate_file_phrase(check, self._read_criterion_file))
        return verdict.passed

    @staticmethod
    def _read_criterion_file(path: str) -> str | None:
        """Read a file for a `file_phrase` check, or None when it cannot be read.

        None rather than "" — `evaluate_file_phrase` treats an unreadable file as UNRUNNABLE, and an
        empty string would read as "the file exists and the phrase is absent", which is a
        claim about
        content nobody read.
        """
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def _schedule_task_write(self, spec: Any, path: str, node_id: str) -> None:
        """Schedule the projected Task write on the running loop, then emit the event.

        The settle path (`_apply`) is SYNC but runs inside the async tick, and the task provider's
        `create_task` is async — so this follows the controller's established idiom for that shape
        (`asyncio.create_task`, as the tick loop and node dispatch already do) rather than blocking
        the tick on a filesystem write.

        The EVENT fires from the write's completion, not before it, so `task_id` is the real id. An
        event with an empty id would tell a board to render a row it cannot open.

        No running loop (a synchronous unit test, a replay) still projects: the write runs
        inline via
        `asyncio.run`, because a projection that only worked inside a live gateway would be
        untestable exactly where it matters.
        """

        async def _write_and_emit() -> None:
            task_id = await self._write_projected_task(spec)
            self.publish_task_materialized(
                path,
                node_id,
                task_id=task_id,
                fingerprint=spec.binding.fingerprint,
                refreshed=False,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_write_and_emit())
            return
        handle = loop.create_task(_write_and_emit())
        # Tracked so a controller teardown does not leave the write as an orphaned task warning, and
        # so a test can await settlement rather than sleeping.
        self._projection_writes.add(handle)
        handle.add_done_callback(self._projection_writes.discard)

    async def _write_projected_task(self, spec: Any) -> str:
        """Write one projected Task through the task provider. Returns its id, or "" on failure.

        The engine is the ENGINE actor in §1's three-actor matrix, so the write carries
        `managed=True` on the binding and sets the engine-owned fields directly — which is exactly
        what `materialize.reject_write` refuses when anyone ELSE attempts it. The asymmetry is the
        point: one writer for a managed task's status, and a refusal (naming the alternative) for
        every other path.

        Failures return "" rather than raising: the event still fires with an empty task id, which
        is honest (the projection was attempted and did not land) and leaves the next rebuild to
        recover. Raising would fail a node whose work already succeeded.
        """
        try:
            from personalclaw.tasks.registry import create_task

            fields: dict[str, Any] = {
                "title": spec.title or "Untitled step",
                "description": spec.body or "",
                "workflow_binding": {
                    "run_id": spec.binding.run_id,
                    "node_id": spec.binding.node_id,
                    "node_path": spec.binding.node_path,
                    "managed": True,
                    "fingerprint": spec.binding.fingerprint,
                },
            }
            if spec.status:
                fields["status"] = spec.status
            if spec.done_criterion:
                fields["done_criterion"] = spec.done_criterion
            if spec.blocked_kind:
                fields["blocked_kind"] = spec.blocked_kind
            if spec.preview:
                fields["preview"] = spec.preview
            task = await create_task("native", **fields)
            return str(getattr(task, "id", "") or "")
        except Exception:  # noqa: BLE001 - a board row must never fail a successful node
            logger.debug("workflow %s: projected task write failed", self.run.id, exc_info=True)
            return ""

    def _project_task(self, item: Any, inst: Any, result: Any) -> None:
        """Project a settled leaf node into a Task, and emit the event.

        THE call site the projection modules were built for. `materialize` owns every decision here
        — which nodes earn a task, the dedup keys, the fan-out cap — so this method is the plumbing
        and nothing else: it assembles the node dict, asks, and writes.

        Swallows everything. A projection failure must not fail the RUN: the node has already
        succeeded and its output is already journaled, so turning a board-row problem into a run
        failure would lose real work over a presentation concern. The projection is idempotent by
        construction (fingerprint dedup), so the next tick or a rebuild recovers it.
        """
        try:
            from personalclaw.workflows import materialize as _materialize

            # The keys `should_materialize`/`plan_materialization` actually read are `id`, `kind`,
            # `path` and `config` — measured against their source. A `node_id` key (the name the
            # BINDING uses) is silently ignored by both, which would make every node fail the
            # has-an-id refusal and project nothing at all.
            node_dict = {
                "id": item.node.id,
                "path": item.path,
                "kind": item.node.kind.value,
                "config": dict(item.node.config or {}),
            }
            wanted, _why = _materialize.should_materialize(node_dict)
            if not wanted:
                return
            plan = _materialize.plan_materialization(
                self.run.id, [node_dict], existing_tasks=self._projected_tasks()
            )
            for spec in plan.create:
                # Recorded BEFORE the write is scheduled: the dedup set must reflect the intent
                # immediately, or a second settle in the same tick would plan the same task again
                # while the first write is still in flight.
                self._projected.append(spec.binding)
                self._schedule_task_write(spec, item.path, item.node.id)
                # Verification rides the same scheduled path: a criterion is a command or a file
                # read, and running it inline would block the tick on someone else's test suite.
                self._schedule_verification(spec, item.path, item.node.id)
            if plan.existing:
                # A rewind's dedup-merge. Emitted as a REFRESH rather than skipped silently:
                # "did my rewind re-create the board" is a question only the event answers.
                self.publish_task_materialized(
                    item.path,
                    item.node.id,
                    task_id="",
                    fingerprint=_materialize.fingerprint(
                        source_ref="", title=item.node.id, body=""
                    ),
                    refreshed=True,
                )
        except Exception:  # noqa: BLE001 - a board row must never fail a successful node
            logger.debug("workflow %s: task projection failed", self.run.id, exc_info=True)

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

    def _open_escalation_outcome(self, path: str, node_id: str, confirmation_id: str) -> None:
        """Open the escalation's outcome question: we interrupted the user — did it land?

        The `escalation` producer of the general outcome facility (PP-9). `confirmation_pending`
        records that we ASKED; this records the bet that asking was worth it, graded from the run's
        own ledger: a `confirmation_resolved` carrying this `confirmation_id` is the measurement,
        and its `approved` boolean IS the number (approved ⇒ 1.0, rejected ⇒ 0.0 against a baseline
        of 1.0, so a rejected interruption scores −1). A gate nobody ever answers closes as
        `inconclusive` once the horizon passes — the honest reading of an interruption that went
        nowhere, and the one that decays fastest.

        Ledger-sourced on purpose: this resolves on a box with no vector store, because the ground
        truth is an event we wrote ourselves.

        Emitted at the same site as `confirmation_pending` so it inherits that site's
        `(path, epoch)` idempotency — one question per gate, not one per watchdog poll.
        """
        try:
            self.journal.open_outcome(
                producer=outcomes.PRODUCER_ESCALATION,
                subject=f"escalated gate `{node_id or path}` to the user",
                metric=journal_mod.CONFIRMATION_RESOLVED,
                metric_source=outcomes.SOURCE_LEDGER,
                match={"confirmation_id": confirmation_id},
                value_field="approved",
                horizon_secs=ESCALATION_ANSWER_HORIZON_SECS,
                # The bet is an approval: we only stop to ask when we expect a yes.
                baseline=1.0,
                instance_path=path,
                node_id=node_id,
                confirmation_id=confirmation_id,
            )
        except Exception:
            # A gate that is already waiting must not fail because its outcome record did not
            # land — the user still has a question to answer.
            logger.debug("escalation outcome open failed for run %s", self.run.id, exc_info=True)

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


def _confirmation_id(run_id: str, gate_id: str, epoch: int) -> str:
    """The stable confirmation id for one (run, gate, epoch).

    Delegates to `confirmation.request_id` rather than composing a string here. Two id schemes for
    one record is the failure mode where `confirmation_pending` and `confirmation_resolved` never
    pair up in the ledger, and nobody notices until someone asks how long a gate waited.

    The EPOCH is in the key because a rewind SHOULD produce a new confirmation — the question is
    being asked about different work. Deriving from the resume token instead would break that: a
    token is single-use and rotates per poll, so pending and resolved would carry different ids for
    the same question.
    """
    from personalclaw.workflows.confirmation import request_id

    return request_id(run_id, gate_id, epoch)


def _confirmation_kind(node_config: dict[str, Any]) -> str:
    """Which `ConfirmationType` this gate is, as its wire value.

    A destructive gate is NOT the same record as an ordinary approval: §4 gives them different
    expiry policies (auto-reject vs hold) and only the ordinary one may be muted. Reading the
    node's own declared risk keeps that classification with the author who made it, rather than
    inferring it from the prompt text at render time.
    """
    from personalclaw.workflows.confirmation import ConfirmationType

    risk = str((node_config or {}).get("risk_category", "") or "").strip().lower()
    if risk in {"destructive", "destructive_op", "irreversible"}:
        return ConfirmationType.DESTRUCTIVE_CONFIRM.value
    kind = str((node_config or {}).get("kind", "") or "").strip().lower()
    if kind in {"input", "needs_input", "question"}:
        return ConfirmationType.NEEDS_INPUT.value
    return ConfirmationType.APPROVAL.value


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


def _opt_metric(value: Any) -> float | None:
    """A metric, or None when there is not a number here (PP-12).

    Booleans are refused: `True` would read as `1.0` and pass a `metric_pass: 1.0` gate on a field
    that was never a measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str | None) -> float:
    """Parse a UTC `...Z` stamp to a real epoch.

    `calendar.timegm`, NOT `time.mktime`: mktime reads the struct as LOCAL time, which
    shifts a UTC stamp by the machine's offset. Here it is only ever used as a DIFFERENCE
    of two stamps, so equal offsets cancelled and elapsed time came out right — except
    across a DST boundary, where the two offsets differ and the run's duration was off by
    an hour.
    """
    if not ts:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


def _walk(root: Node) -> list[tuple[str, Node]]:
    from personalclaw.workflows.models import walk

    return walk(root)


def _base_path(path: str) -> str:
    """Strip foreach/loop instance MARKERS, yielding the SPEC path.

    `root.body#3` and `root.body@2` are instances of the same spec node; the state map is
    keyed by instance, but the spec lookup needs the shared path.

    Removes each `@N`/`#N` marker in place rather than truncating at the last one. Truncating
    was wrong for any node BELOW an iteration marker:
    `root.children[0].body@0.children[0]` became `root.children[0].body`, so the spec lookup
    returned the body SEQUENCE instead of the wait inside it. Measured live: a `wait` nested in
    a loop body was read as a gate by `_wake_due_nodes` and every cycle failed with "gate timed
    out with no answer" — for a template containing no gate at all. Every container-bodied
    loop and foreach was affected, which is the shape the watcher templates use.
    """
    return _INSTANCE_MARKER_RE.sub("", path)


def _enclosing_parallel(path: str, tree: dict[str, Node]) -> str | None:
    """The path of the nearest enclosing `parallel`, walking OUTWARD.

    Not just the nearest `.children[N]` prefix: a watcher's synthesize stage sits at
    `…children[1].body@3.children[0]`, whose nearest prefix is the BODY SEQUENCE. Stopping
    there returned no siblings for the one node in the whole template that needs them —
    measured, and silent, because a missing `siblings` root reads as "this node has no
    siblings" rather than as an error.

    Nearest-first among genuine parallels, so a nested parallel resolves to the inner one: a
    node's siblings are the legs of ITS parallel, not an outer one's.
    """
    matches = list(re.finditer(r"\.children\[\d+\]", path))
    for match in reversed(matches):
        candidate = path[: match.start()]
        if not candidate:
            continue
        node = tree.get(_base_path(candidate))
        if node is not None and node.kind == NodeKind.PARALLEL:
            return candidate
    return None


def _natural_key(path: str) -> list[Any]:
    """Sort instance paths NUMERICALLY on their indices.

    A plain string sort puts `children[10]` before `children[2]` and `body@10` before `body@2`,
    so "oldest first" silently became wrong at the tenth iteration — the window would keep the
    wrong items and `previous.output` would return the wrong cycle. Ten cycles in is late enough
    that no short test would ever see it.
    """
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", path)]


def _loop_parent(path: str) -> tuple[str | None, int]:
    """`root.children[0].body@2` → `("root.children[0]", 2)`.

    The marker need not END the path. A loop whose body is a CONTAINER puts its leaf work
    deeper — `root.children[1].body@0.children[2]` — and the old form required the path to end
    at `@N`, so `int("0.children[2]")` raised, `_advance_loop` returned silently, the loop never
    advanced, and the run deadlocked after exactly one iteration. Measured live, and five
    shipped templates use container-bodied loops.

    The INNERMOST marker wins, so a loop nested inside another loop's body advances itself
    rather than its parent.
    """
    matches = list(_LOOP_MARKER_RE.finditer(path))
    if not matches:
        return None, 0
    match = matches[-1]
    body = path[: match.start()]
    if not body.endswith(".body"):
        return None, 0
    return body[: -len(".body")], int(match.group(1))


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


def _parse_revise(answer: Any) -> tuple[str, str] | None:
    """Read `revise{step_ref, comment}` out of a gate answer. Returns `(step_ref, comment)` or None.

    Recognised STRUCTURALLY, by the `revise` key rather than by a free-text prefix. A gate whose
    ask is a `text` legitimately receives prose, and sniffing for the word "revise" in it would
    hijack an answer that merely mentioned revising something.

    `answer` is untyped by contract (`WORKFLOW_RESUME_SCHEMA`), so both spellings a caller
    naturally reaches for are accepted: the nested `{"revise": {...}}` a tool emits, and the flat
    `{"revise": true, "step_ref": ..., "comment": ...}` a form posts. An `answer` with no `revise`
    key is None, which is what routes every existing answer down the unchanged approval path.
    """
    if not isinstance(answer, dict) or "revise" not in answer:
        return None
    body = answer.get("revise")
    if isinstance(body, dict):
        source: dict[str, Any] = body
    else:
        # A truthy flag alongside sibling keys. A falsy flag is NOT a revise — `{"revise": false}`
        # against an approval gate is a rejection expressed clumsily, and the approval path's own
        # `validate_answer` is the right thing to tell the caller so.
        if not body:
            return None
        source = answer
    step_ref = str(source.get("step_ref", "") or source.get("step", "") or "").strip()
    comment = str(source.get("comment", "") or source.get("text", "") or "").strip()
    return step_ref, comment


def _revise_allowed(run: Any) -> tuple[bool, str]:
    """Whether this run can take a revision at all.

    A terminal run cannot: there is nothing left to re-run, so a revision would edit a spec that
    will never execute again — which would break the one promise the verb makes, that the recorded
    plan is the plan that runs.
    """
    if getattr(run, "is_terminal", False):
        return False, f"run is already {getattr(getattr(run, 'status', None), 'value', 'finished')}"
    return True, ""


def _is_approved(ask: Any, answer: Any) -> bool:
    """Did the human say yes?

    Only an `approval` ask can DENY — a text or form answer is data, not a verdict, and
    treating an empty string as a denial would fail a gate the user actually answered.
    """
    from personalclaw.workflows.human_input import AskKind

    if ask.kind != AskKind.APPROVAL:
        return True
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        return bool(answer.get("approved"))
    return False


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
