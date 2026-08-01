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
from personalclaw.workflows.controller import _ROOT_TO_RUN, EngineServices, RunController
from personalclaw.workflows.models import (
    TERMINAL_STATES,
    InstanceState,
    Node,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.tick import frontier

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 5


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
        """
        state = self._state
        if state is None:
            return None

        key = registry_key(run_id)

        def publish(event: str, payload: dict[str, Any]) -> None:
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
                    broadcast({"type": "workflow_run_update", "run_id": run_id})
            except Exception:
                logger.debug("workflow ws broadcast failed", exc_info=True)

        return publish

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
