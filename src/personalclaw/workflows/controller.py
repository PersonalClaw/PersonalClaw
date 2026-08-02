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
from dataclasses import dataclass, field
from typing import Any

from personalclaw.knowledge import session_brief
from personalclaw.workflows import attention
from personalclaw.workflows import context as context_mod
from personalclaw.workflows import gate_policy
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import longrun, mutations, store
from personalclaw.workflows.bindings import BindingContext, node_deps
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
from personalclaw.workflows.engine import NodeResult, dispatch
from personalclaw.workflows.human_input import drop_continuations
from personalclaw.workflows.journal import CacheKey, Journal, inputs_hash, spec_region_hash
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    Failure,
    FailureClass,
    InstanceState,
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
from personalclaw.workflows.tick import (
    Frontier,
    Limits,
    ReadyNode,
    derive_state,
    frontier,
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
        """Blocking mode: drive to terminal and return the status."""
        await self.start()
        if timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._terminal.wait(), timeout=timeout)
        else:
            await self._terminal.wait()
        return self.run.status

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

        for item in fr.ready:
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
        self.journal.write(
            journal_mod.GATE_RESOLVED,
            instance_path=cont.instance_path,
            node_id=cont.node_id,
            epoch=cont.epoch,
            approved=approved,
            answer=filled,
        )
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
        return result

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
        else:
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
        if context_mod.session_policy(node.config) != context_mod.SESSION_FRESH:
            return ""
        return context_mod.render_context(
            handoff=self._handoffs.get(parent_path),
            carryover=self._carryover.get(parent_path),
            decisions=self._decisions.get(parent_path),
        )

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
        if status in TERMINAL_RUN_STATUSES:
            # A run that ended answers its own outstanding questions by ending: nothing about
            # it is actionable now. Leaving the rows open would put a permanently unanswerable
            # gate in the inbox — cancel a run mid-gate and the question survives the run.
            # NEEDS_INPUT is deliberately not terminal here: that run is waiting, not finished.
            attention.resolve_run_items(self.services.attention_state, self.run.id)
        self._publish("workflow_run_update", {"status": status.value, "error": error})

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
