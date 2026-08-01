"""The workflow watchdog — crash recovery and the supervisor poll.

A controller drives a run only while its process lives. The watchdog covers everything
that outlives a process: a gateway killed mid-run, a stage subagent that finished after
its controller died, a cancel issued while the gateway was down.

Its job is narrow by design. It does NOT schedule nodes — that is the controller's, and
having two schedulers is how a run gets two writers. The watchdog decides only *which
runs need a controller*, then hands off.

Three responsibilities:

**Adoption.** At startup, every run the store thinks is live has no controller. Each is
adopted: a fresh controller resumes from the journal, and completed nodes come back as
cache hits rather than re-running. Without this a gateway restart silently abandons runs
in RUNNING forever — the state a user reads as "still working" while nothing is.

**Orphan reaping.** A run whose nodes are all terminal but whose status never got written
(the process died between the last node and the terminal write) is finished off from its
derived outcome. This is the one place a terminal status is written outside a tick loop,
and it is safe precisely because there is no controller: adoption comes first, so a run
with a live controller is never reaped.

**Sticky cancel.** A CANCEL file is honoured even for a run with no controller.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from personalclaw import shutdown_event
from personalclaw.workflows import store
from personalclaw.workflows.coalescer import EventCoalescer
from personalclaw.workflows.controller import _ROOT_TO_RUN, EngineServices, RunController
from personalclaw.workflows.models import (
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    InstanceState,
    Node,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.tick import frontier

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 5

#: Terminal status strings, for the publish-side flush check. The enum values rather than the
#: enum: the payload carries a serialized status, not a member.
_TERMINAL_VALUES = frozenset(s.value for s in TERMINAL_RUN_STATUSES)


def registry_key(run_id: str) -> str:
    """The per-run SSE registry key — one hub per run, matching `loop:<id>`."""
    return f"workflow:{run_id}"


class WorkflowWatchdog:
    """Supervises workflow runs across process lifetimes.

    `start()` on gateway startup, `stop()` on shutdown. Owns the controller registry, so
    every lookup of "is this run being driven?" has one answer.
    """

    def __init__(self, state: Any = None, services: EngineServices | None = None) -> None:
        self._state = state
        self._services = services or EngineServices()
        self._task: asyncio.Task | None = None
        self._controllers: dict[str, RunController] = {}
        #: Consecutive poll failures. A persistently broken poll should be loud in the
        #: log rather than silently retrying forever.
        self._consec_errors = 0
        #: Coalesced delivery (WF2-R11 batch-5). ONE coalescer for the whole supervisor, so
        #: its windows are keyed by observer across every run rather than one debounce state
        #: per run — a browser watching two runs should not get two independent timers
        #: fighting over the same connection.
        self._coalescer = EventCoalescer(self._raw_publish)

    # ── lifecycle ──

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("workflow watchdog started")

    async def stop(self) -> None:
        for controller in list(self._controllers.values()):
            with contextlib.suppress(Exception):
                await controller.stop()
        self._controllers.clear()
        # Flush before the loop goes away: a pending batch's timer would never fire, and the
        # events in it are the LAST ones a watching client sees before shutdown.
        with contextlib.suppress(Exception):
            self._coalescer.flush_all()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    # ── controller registry ──

    def controller(self, run_id: str) -> RunController | None:
        return self._controllers.get(run_id)

    def register(self, controller: RunController) -> None:
        """Adopt a controller created elsewhere (a chat tool starting a run), so the
        watchdog does not later adopt the same run a second time."""
        self._controllers[controller.run.id] = controller

    def forget(self, run_id: str) -> bool:
        """Drop a finished run's controller from the registry. True if one was held.

        For deletion: nothing may hold a handle to a run whose row is about to disappear, or
        the next poll would try to reconcile a run that no longer exists. Only ever called for
        a TERMINAL run (`service.delete_run` refuses otherwise), so this cannot orphan a live
        tick loop.
        """
        return self._controllers.pop(run_id, None) is not None

    async def launch(
        self, run: WorkflowRun, spec: dict[str, Any], *, depth: int = 0
    ) -> RunController:
        """Create, register and start a controller for a run."""
        existing = self._controllers.get(run.id)
        if existing is not None:
            return existing
        controller = RunController(run, spec, services=self._services_for(run), depth=depth)
        self._controllers[run.id] = controller
        await controller.start()
        return controller

    def _services_for(self, run: WorkflowRun) -> EngineServices:
        """Per-run services — the publish callback is run-scoped so events land on the
        right SSE hub."""
        base = self._services
        return EngineServices(
            subagents=base.subagents,
            completion=base.completion,
            get_provider=base.get_provider,
            verify=base.verify,
            publish=self._publisher(run.id),
            # The attention path (WF2-R7): a waiting gate raises a durable inbox item + one
            # notification. Handed the state directly rather than a callback, because
            # `emit_attention_item` owns the item↔notification pairing and a callback here
            # would be a second place that could get it wrong.
            attention_state=self._state,
            # Itself, for `subworkflow` nesting (WF2-R13): a child run has to be driven by the
            # supervisor that will also adopt it after a restart. Passing a different one would
            # leave the child un-reconciled, which is exactly the two-writers hazard the
            # controller registry exists to prevent.
            supervisor=self,
            model_tiers=dict(base.model_tiers),
            lane_limits=base.lane_limits,
            node_timeout_total=base.node_timeout_total,
            node_timeout_stall=base.node_timeout_stall,
            cwd=base.cwd,
        )

    def _publisher(self, run_id: str) -> Any:
        """Build the run-scoped event publisher.

        Deliberately NOT `state.notify(...)`: that is the user-notification gate behind
        mute/severity/quiet-hours, and routing engine events through it would let a
        quiet-hours setting silently eat a run's entire event stream. Genuinely
        user-facing moments (needs_input, failure) go through `notify` separately.

        Every event goes through the coalescer, which batches the high-frequency per-node
        chatter and passes everything else straight through (WF2-R11 batch-5). A run's last
        events are flushed on termination by `_flush_run`, so nothing is stranded in a
        window whose timer outlives the run.
        """
        if self._state is None:
            return None

        key = registry_key(run_id)

        def publish(event: str, payload: dict[str, Any]) -> None:
            self._coalescer.publish(key, event, payload)
            if event == "workflow_run_update" and payload.get("status") in _TERMINAL_VALUES:
                # A terminal run publishes nothing further, so any batch still accumulating
                # behind this status would sit until its timer fired — on a stream the FE has
                # already closed (a terminal run's SSE closes immediately). Flush now.
                self._coalescer.flush(key)

        return publish

    def _raw_publish(self, key: str, event: str, payload: Any) -> None:
        """The coalescer's sink: the actual SSE write plus the WS refetch signal."""
        state = self._state
        if state is None:
            return
        try:
            registry = getattr(state, "workflow_sse", None)
            if callable(registry):
                registry().publish(key, event, payload)
        except Exception:
            logger.debug("workflow sse publish failed", exc_info=True)
        try:
            broadcast = getattr(state, "_broadcast", None)
            if callable(broadcast) and event == "workflow_run_update":
                # WS envelopes are refetch SIGNALS, not payloads (the DashboardLive
                # convention) — the client refetches on receipt.
                run_id = key.split(":", 1)[1] if ":" in key else key
                broadcast({"type": "workflow_run_update", "run_id": run_id})
        except Exception:
            logger.debug("workflow ws broadcast failed", exc_info=True)

    # ── poll ──

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await self._poll_once()
                self._consec_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self._consec_errors += 1
                logger.warning(
                    "workflow watchdog poll failed (%d consecutive)",
                    self._consec_errors,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECS)
            except asyncio.TimeoutError:
                continue

    async def _poll_once(self) -> None:
        # Drop finished controllers first so a completed run does not look adopted and
        # block its own reaping.
        for run_id, controller in list(self._controllers.items()):
            if controller.run.is_terminal:
                self._controllers.pop(run_id, None)

        for run in store.active_runs():
            if store.cancel_requested(run.id):
                await self._honor_cancel(run)
                continue
            live = self._controllers.get(run.id)
            if live is None:
                await self._adopt(run)
                continue
            if live.run.is_terminal:
                self._controllers.pop(run.id, None)

    async def _adopt(self, run: WorkflowRun) -> None:
        """Resume a run with no live controller.

        Reaping is checked BEFORE launching: a run whose work is all terminal needs a
        status write, not a controller, and launching one would spin up a tick loop only
        to have it immediately conclude.
        """
        spec = store.read_spec(run.id)
        if spec is None:
            await self._fail(run, "run spec is missing or unreadable")
            return
        if self._reap_if_finished(run, spec):
            return
        logger.info("workflow watchdog adopting run %s (%s)", run.id, run.status.value)
        with contextlib.suppress(Exception):
            await self.launch(run, spec)

    def _reap_if_finished(self, run: WorkflowRun, spec: dict[str, Any]) -> bool:
        """Finish a run whose nodes are all terminal but whose status never got written.

        The only terminal write outside a tick loop, and safe only because it runs when
        no controller exists for the run.
        """
        try:
            root = Node.from_dict(spec.get("root") or {})
        except ValueError:
            return False
        instances = store.read_state(run.id)
        if not instances:
            return False
        states = {p: i.state for p, i in instances.items()}
        if any(st not in TERMINAL_STATES for st in states.values()):
            return False
        fr = frontier(root, states, outputs={}, inputs=run.inputs)
        if not fr.complete:
            return False
        status = _ROOT_TO_RUN.get(fr.outcome or InstanceState.DONE, RunStatus.COMPLETE)
        run.status = status
        run.completed_at = run.completed_at or _now()
        store.save(run)
        logger.info("workflow watchdog reaped orphaned run %s → %s", run.id, status.value)
        return True

    async def _honor_cancel(self, run: WorkflowRun) -> None:
        """Honour a sticky cancel. A controller cancels itself on its next step; a run
        with none is finalized here — a cancel issued while the gateway was down must not
        be lost."""
        controller = self._controllers.get(run.id)
        if controller is not None:
            return  # its own tick loop will see the intent and write the terminal status
        run.status = RunStatus.CANCELLED
        run.completed_at = run.completed_at or _now()
        store.save(run)
        store.clear_cancel(run.id)
        logger.info("workflow watchdog cancelled run %s (no live controller)", run.id)

    async def _fail(self, run: WorkflowRun, reason: str) -> None:
        run.status = RunStatus.FAILED
        run.error_message = reason
        run.completed_at = run.completed_at or _now()
        store.save(run)
        logger.warning("workflow run %s failed: %s", run.id, reason)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── retention ────────────────────────────────────────────────────────────────


def prune_runs(workflow_name: str, *, keep: int = 100) -> int:
    """Prune old runs for one def, oldest first.

    Pinned runs are never pruned, and the row is deleted only after its directory sweep
    succeeds — the reverse order would orphan megabytes of journal with no row left to
    find them by.
    """
    runs, _total = store.list_runs(workflow_name=workflow_name, limit=10_000)
    prunable = [r for r in runs if r.is_terminal and not r.pinned]
    if len(prunable) <= keep:
        return 0
    # `list_runs` returns newest-first, so the tail is the oldest.
    doomed = prunable[keep:]
    removed = 0
    for run in doomed:
        if _sweep_run_dir(run.id):
            store.delete(run.id)
            removed += 1
    return removed


def _sweep_run_dir(run_id: str) -> bool:
    """Delete a run directory, refusing any path that escapes the runs root.

    The containment check is not paranoia: `run_id` reaches here from a stored row, and a
    row is not a trust boundary. A `..`-shaped id must delete nothing.
    """
    import shutil

    try:
        root = store.runs_root().resolve()
        target = store.run_dir(run_id).resolve()
    except OSError:
        return False
    if target == root or root not in target.parents:
        logger.error("refusing to sweep %s — outside the runs root", target)
        return False
    if not target.exists():
        return True
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        logger.warning("could not sweep run dir %s", target, exc_info=True)
        return False
