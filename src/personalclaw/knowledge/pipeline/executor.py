"""PipelineExecutor — runs a conditional DAG over one item.

Walks the graph in topological order. A node runs only if **all its incoming edges
are satisfied** (an edge is satisfied when its source ran successfully AND, for a
conditional edge, the source's ``classification`` matches ``when``). Each successful
node's output is fed to its successors and (when ``pooled``) appended to the item's
extracted-content pool. A failed or skipped node never aborts the whole item — the
graph continues wherever its dependencies are still met, and the item ends
``done`` (all ran), ``partial`` (some skipped/failed), or ``failed`` (nothing ran).

Concurrency: nodes whose dependencies are all satisfied at the same wave run
concurrently (``asyncio.gather``). Each node has a budget (the node spec's, or one scaled to the
media's length) on its OWN time: time the event loop could not run is not counted. A node out of
its budget is cancelled, which kills any program it started, and fails saying so, marked to run
again; a cancelled run (the gateway stopping) says which node it stopped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from personalclaw.cancellation import OutOfTime, wait_for_unpaused
from personalclaw.knowledge.pipeline import outcomes as oc
from personalclaw.knowledge.pipeline.graph import PipelineGraph
from personalclaw.knowledge.pipeline.outcomes import PhaseOutcome
from personalclaw.knowledge.pipeline.registry import (
    get_node,
    node_available,
    resolve_runnable,
    unserved_reason,
    why_not_runnable,
)
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput, PoolRow

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of running a graph over one item."""

    outputs: dict[str, NodeOutput] = field(default_factory=dict)  # node_type → output
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    #: Nodes whose incoming CONDITIONAL edge did not match — the branch was not taken.
    #: Distinct from ``skipped`` (a real degradation: a model or engine that should have
    #: been there was not), because a graph with an either/or branch would otherwise
    #: report every clean run as ``partial``. The document graph's scan branch made that
    #: concrete: a text-layer PDF leaves ``pdf_rasterize`` and ``ocr`` untaken, and
    #: calling that "partially ingested" would tell the user something was missing from a
    #: document that was read completely.
    not_taken: list[str] = field(default_factory=list)
    #: What became of every node the run reached — status, reason and fix
    #: (:mod:`~personalclaw.knowledge.pipeline.outcomes`). The lists above decide the item's
    #: status; this is what a consumer tells a user. A node in ``not_taken`` is
    #: ``not_applicable`` when its branch was not chosen, and ``skipped`` (with the fix of the
    #: step it waited on) when the step it needed did not run.
    outcomes: dict[str, PhaseOutcome] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.ran:
            return "failed"
        if self.skipped or self.failed:
            return "partial"
        return "done"

    def pooled_outputs(self) -> list[NodeOutput]:
        return [o for o in self.outputs.values() if o.success and o.pooled and o.text]

    def pool_rows(self) -> list[PoolRow]:
        """Extra self-named pool rows contributed by successful nodes (see
        :class:`~personalclaw.knowledge.pipeline.types.PoolRow`). Emitted in node
        completion order, then in each node's declared row order — the same ordering
        discipline ``pooled_outputs`` already relies on."""
        return [row for out in self.outputs.values() if out.success for row in out.pool_rows]


class PipelineExecutor:
    """Run a :class:`PipelineGraph` for one item.

    *params_for* (node_type → execution-param dict) layers user config
    over the graph defaults: ``enabled``, ``backend``, ``use_case``, ``timeout_s``.
    *on_node* (node_type, phase) is called for SSE progress (phase ∈
    queued|running|done|skipped|failed).
    """

    def __init__(self, graph: PipelineGraph, *, params_for=None, on_node=None):
        self._graph = graph
        self._params_for: Callable[[str], dict] = params_for or (lambda nt: {})
        self._on_node = on_node
        #: The source media's length in seconds, probed once per run for the budgets.
        self._dur_cache: float | None = None

    async def run(self, ctx: NodeContext) -> ExecutionResult:
        result = ExecutionResult()
        order = self._graph.topo_order()
        # First pass: the forward DAG, wave-by-wave.
        await self._run_subset(order, ctx, result)

        # Bounded adaptive loops: after the forward pass, any loop back-edge whose
        # source classification == its `when` (e.g. video_classify → 'needs-denser')
        # re-runs the loop BODY (the nodes from the loop target forward to the loop
        # source) up to max_iters, so the classifier can request denser sampling
        # around content-heavy regions. Each re-run passes the source node's region
        # hints via ctx.params so the sampler can tighten only where needed.
        for le in self._graph.loop_edges():
            iters = 0
            looped = False
            while iters < le.max_iters:
                src = result.outputs.get(le.from_node)
                if src is None or not src.success or src.classification != le.when:
                    break  # loop condition no longer met → converged (or never met)
                iters += 1
                looped = True
                # Hand the source's region hints to the loop body for this iteration.
                body = self._loop_body(le.to_node, le.from_node)
                loop_ctx = self._ctx_with_loop(ctx, le, iters, src)
                self._reset_nodes(body, result)
                await self._run_subset([n for n in order if n in body], loop_ctx, result)
                self._notify(le.from_node, "loop")  # UI: mark an iteration occurred
            # After the loop settles, the loop source's classification may have changed
            # (needs-denser → a terminal verdict), so its FORWARD descendants that were
            # skipped on the classification they saw earlier must be re-evaluated with
            # the final verdict. Re-run the downstream subset (excludes the loop body).
            if looped:
                body = self._loop_body(le.to_node, le.from_node)
                downstream = [n for n in self._forward_descendants(le.from_node) if n not in body]
                self._reset_nodes(downstream, result)
                await self._run_subset([n for n in order if n in downstream], ctx, result)
        return result

    def _reset_nodes(self, nodes, result: ExecutionResult) -> None:
        """Drop a node set's recorded outputs/phases so a re-run can re-resolve them."""
        for nt in nodes:
            result.outputs.pop(nt, None)
            result.outcomes.pop(nt, None)
            for lst in (result.ran, result.failed, result.skipped, result.not_taken):
                while nt in lst:
                    lst.remove(nt)

    def _forward_descendants(self, start: str) -> set[str]:
        """All nodes reachable from `start` via forward edges (excluding `start`)."""
        seen: set[str] = set()
        stack = [e.to_node for e in self._graph.successors(start)]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            for e in self._graph.successors(n):
                stack.append(e.to_node)
        return seen

    async def _run_subset(
        self, order: list[str], ctx: NodeContext, result: ExecutionResult
    ) -> None:
        """Run the given nodes (a topological sub-order) wave-by-wave — a node is ready
        when every FORWARD predecessor within this subset has resolved."""
        subset = set(order)
        resolved: set[str] = {n for n in self._graph.nodes if n not in subset}
        remaining = list(order)
        while remaining:
            wave = [n for n in remaining if self._deps_resolved(n, resolved)]
            if not wave:  # defensive — acyclic forward graph guarantees progress
                logger.warning("knowledge pipeline stalled; remaining=%s", remaining)
                for n in remaining:
                    result.skipped.append(n)
                    result.outcomes[n] = oc.skipped("It never became ready to run.")
                break
            coros = [self._run_one(n, ctx, result) for n in wave]
            await asyncio.gather(*coros)
            resolved.update(wave)
            remaining = [n for n in remaining if n not in resolved]

    def _loop_body(self, start: str, end: str) -> set[str]:
        """Nodes reachable from `start` (the loop target) via forward edges without
        passing `end` (the loop source), plus `end` itself — the segment a loop
        iteration re-runs."""
        body: set[str] = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in body:
                continue
            body.add(n)
            if n == end:
                continue  # don't traverse past the loop source
            for e in self._graph.successors(n):
                stack.append(e.to_node)
        body.add(end)
        return body

    def _ctx_with_loop(self, ctx: NodeContext, le, iteration: int, src) -> NodeContext:
        """A per-iteration context carrying the loop's region hints + iteration index
        so the frame sampler tightens density only around the flagged timestamps."""
        params = dict(ctx.params or {})
        params["loop_iteration"] = iteration
        params["dense_regions"] = (src.metadata or {}).get("dense_regions", [])
        return NodeContext(
            item_id=ctx.item_id,
            item_type=ctx.item_type,
            file_path=ctx.file_path,
            content=ctx.content,
            url=ctx.url,
            work_dir=ctx.work_dir,
            params=params,
        )

    def _deps_resolved(self, node_type: str, resolved: set[str]) -> bool:
        return all(e.from_node in resolved for e in self._graph.predecessors(node_type))

    def _edges_satisfied(self, node_type: str, result: ExecutionResult) -> bool:
        """A node runs iff it has at least one satisfied incoming path (or is a root).

        Each incoming edge is satisfied when its source ran successfully and — if
        conditional — the source's classification matches ``when``. A node with NO
        predecessors (root) is always eligible.
        """
        preds = self._graph.predecessors(node_type)
        if not preds:
            return True
        for e in preds:
            src = result.outputs.get(e.from_node)
            if src is None or not src.success:
                continue
            if e.when is None or src.classification == e.when:
                return True
        return False

    def _untaken_outcome(self, node_type: str, result: ExecutionResult) -> PhaseOutcome:
        """Why a node none of whose incoming edges is satisfied did not run.

        When a step before it RAN and chose another branch (a classification its edge does not
        match), this one does not apply to the item. Otherwise every step it needs was skipped,
        failed or not needed, and it says which and why, carrying that step's fix.
        """
        upstream: dict[str, PhaseOutcome] = {}
        for e in self._graph.predecessors(node_type):
            src = result.outputs.get(e.from_node)
            if src is not None and src.success:
                return oc.branch_not_taken(oc.step_name(e.from_node))
            upstream.setdefault(
                e.from_node,
                result.outcomes.get(e.from_node) or oc.skipped("It did not run."),
            )
        return oc.waited_on([(oc.step_name(nt), o) for nt, o in upstream.items()])

    def _skip(self, node_type: str, result: ExecutionResult, outcome: PhaseOutcome) -> None:
        result.skipped.append(node_type)
        result.outcomes[node_type] = outcome
        self._notify(node_type, outcome.status)

    def _fail(
        self,
        node_type: str,
        result: ExecutionResult,
        out: NodeOutput,
        outcome: PhaseOutcome | None = None,
    ) -> None:
        result.outputs[node_type] = out
        result.failed.append(node_type)
        result.outcomes[node_type] = outcome or oc.failed(out.error or "It did not finish.")
        self._notify(node_type, "failed")

    async def _run_one(self, node_type: str, ctx: NodeContext, result: ExecutionResult) -> None:
        spec = self._graph.nodes[node_type]
        params = self._params_for(node_type) or {}
        if not params.get("enabled", spec.enabled):
            self._skip(node_type, result, oc.skipped("This step is turned off."))
            return
        if not self._edges_satisfied(node_type, result):
            # Not a degradation of THIS step, so it is kept out of ``skipped`` — see
            # ``ExecutionResult.not_taken``. Its outcome still says why: a branch not chosen is
            # not applicable, a step that waited on a skipped one carries that one's fix.
            outcome = self._untaken_outcome(node_type, result)
            result.not_taken.append(node_type)
            result.outcomes[node_type] = outcome
            self._notify(node_type, outcome.status)
            return

        pinned = bool(params.get("backend"))
        backend = params.get("backend") or spec.backend
        use_case = params.get("use_case", spec.uses_use_case)
        node = get_node(node_type, backend)
        if node is None:
            logger.warning("no node registered for (%s, %s)", node_type, backend)
            self._skip(node_type, result, oc.skipped("This step isn't available in this install."))
            return
        # Model-backed node no model serves → graceful skip (item goes partial).
        unserved = await unserved_reason(use_case)
        if unserved:
            # …unless ANOTHER registered backend for this node type can run. One node type may
            # have alternative implementations (`ocr` is model-backed by default and
            # engine-backed when an OCR app is installed), and skipping a step whose work IS
            # available just because the DEFAULT route needs a model the user never bound is
            # the graceful-skip path overreaching. A user-PINNED backend is authoritative and
            # never substituted; only the graph's default is reconsidered.
            alt = None if pinned else await resolve_runnable(node_type, backend)
            if alt is None:
                logger.info("skipping node %s — %s", node_type, unserved)
                self._skip(
                    node_type,
                    result,
                    await why_not_runnable(
                        node_type, backend, use_case=use_case, unserved=unserved, pinned=pinned
                    ),
                )
                return
            node, backend = alt
            # The substitute resolves its OWN use-case (an engine backend has none), so the
            # spec's use-case no longer describes this run.
            use_case = node.uses_use_case
            logger.info(
                "node %s: use-case %s unresolved → running runnable backend %r instead",
                node_type,
                spec.uses_use_case,
                backend,
            )
        elif not node_available(node):
            # The use-case resolves but the backend's own dependency does not (an engine app
            # was disabled mid-session). Same substitution, same graceful skip if none runs.
            alt = None if pinned else await resolve_runnable(node_type, backend)
            if alt is None:
                logger.info("skipping node %s — backend %r is unavailable", node_type, backend)
                self._skip(
                    node_type,
                    result,
                    await why_not_runnable(node_type, backend, use_case=use_case, pinned=pinned),
                )
                return
            node, backend = alt
            use_case = node.uses_use_case

        self._notify(node_type, "running")
        inputs = {
            e.from_node: result.outputs[e.from_node]
            for e in self._graph.predecessors(node_type)
            if e.from_node in result.outputs and result.outputs[e.from_node].success
        }
        # A user-set timeout is authoritative; otherwise model-backed media nodes get
        # a duration-scaled budget (a 90-min video's transcription can't finish in the
        # flat 120s, even segmented) with a hard ceiling. Pure-python nodes keep the
        # spec default.
        if "timeout_s" in params:
            timeout_s = float(params["timeout_s"])
        else:
            if node_type in self._DURATION_SCALED_NODES:
                await self._probe_media(ctx)
            timeout_s = self._scaled_timeout(node_type, spec, use_case, ctx)
        try:
            # On the step's own clock: time the event loop could not run (another step's engine
            # holding the interpreter lock) is not the step's. Out of time, the step is cancelled,
            # and the cancel kills a program it started (`_run_cmd`, a model's child process).
            out = await wait_for_unpaused(
                node.run(inputs, ctx), timeout_s, what=f"knowledge step {node_type}"
            )
        except OutOfTime:
            # A sentence, because it is what the item's status line reads ("transcription:
            # …"); the bare word "timeout" said neither how long nor that the step was stopped.
            # Marked to run again: a step stopped for its time may finish on another run.
            budget = _spoken_duration(timeout_s)
            logger.warning(
                "knowledge step %s of item %s was stopped: it did not finish within %s",
                node_type,
                ctx.item_id,
                budget,
            )
            stopped = f"It did not finish within {budget}, so it was stopped."
            self._fail(
                node_type,
                result,
                NodeOutput(node_type=node_type, backend=backend, success=False, error=stopped),
                oc.stopped(stopped),
            )
            return
        except asyncio.CancelledError:
            # The whole run was cancelled (the gateway stopping): the step's program is already
            # gone with it; this says which step it was.
            logger.warning(
                "knowledge step %s of item %s was stopped before it finished: its run was "
                "cancelled",
                node_type,
                ctx.item_id,
            )
            raise
        except Exception as exc:  # a node bug must not abort the item
            logger.exception("knowledge node %s failed", node_type)
            self._fail(
                node_type,
                result,
                NodeOutput(node_type=node_type, backend=backend, success=False, error=str(exc)),
            )
            return
        if out.success:
            result.outputs[node_type] = out
            result.ran.append(node_type)
            result.outcomes[node_type] = oc.done()
            self._notify(node_type, "done")
        else:
            self._fail(node_type, result, out)

    # Model-backed media nodes whose work scales with media length. Their timeout
    # grows with the source's duration so a long video/audio can finish; pure-python
    # nodes (av_split, frame_extract, exif) keep the flat spec default. Diarization reads
    # the whole recording as transcription does, so it scales too: on the flat 120s a long
    # meeting's speakers were never told apart.
    _DURATION_SCALED_NODES = frozenset(
        {"transcription", "diarization", "video_classify", "ocr", "vision", "video_consolidate"}
    )
    # Seconds of node budget per second of media, per node. Transcription is the
    # heaviest (even segmented, each segment is a model call); the others sample.
    _BUDGET_PER_MEDIA_SEC = 2.0
    _MAX_NODE_TIMEOUT_S = 3600.0  # hard ceiling — a genuinely stuck node still dies

    def _scaled_timeout(self, node_type: str, spec, use_case, ctx: NodeContext) -> float:
        """The node's budget: the spec's, grown with the media's length (:meth:`_probe_media`)
        for a node whose work scales with it."""
        base = float(spec.timeout_s)
        if node_type not in self._DURATION_SCALED_NODES:
            return base
        dur = self._dur_cache or 0.0
        if dur <= 0:
            return base
        scaled = base + dur * self._BUDGET_PER_MEDIA_SEC
        return min(self._MAX_NODE_TIMEOUT_S, max(base, scaled))

    async def _probe_media(self, ctx: NodeContext) -> None:
        """Read the source media's length once per run, off the event loop (``media_seconds``:
        0 when there is no ffprobe, or nothing it can read). The blocking probe this replaces
        held the loop, and every request with it, for as long as ffprobe took."""
        if self._dur_cache is None:
            from personalclaw.knowledge.pipeline.nodes.media_nodes import media_seconds

            self._dur_cache = await media_seconds(ctx.file_path or "")

    def _notify(self, node_type: str, phase: str) -> None:
        if self._on_node:
            try:
                self._on_node(node_type, phase)
            except Exception:
                logger.debug("pipeline on_node callback failed", exc_info=True)


def _spoken_duration(seconds: float) -> str:
    """A node budget as a person says it: "90 seconds", "14 minutes", "3 hours"."""
    whole = int(round(seconds))
    if whole < 120:
        return f"{whole} seconds"
    minutes = round(whole / 60)
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{round(minutes / 60)} hours"
