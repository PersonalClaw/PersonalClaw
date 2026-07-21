"""PipelineExecutor — runs a conditional DAG over one item (#30).

Walks the graph in topological order. A node runs only if **all its incoming edges
are satisfied** (an edge is satisfied when its source ran successfully AND, for a
conditional edge, the source's ``classification`` matches ``when``). Each successful
node's output is fed to its successors and (when ``pooled``) appended to the item's
extracted-content pool. A failed or skipped node never aborts the whole item — the
graph continues wherever its dependencies are still met, and the item ends
``done`` (all ran), ``partial`` (some skipped/failed), or ``failed`` (nothing ran).

Concurrency: nodes whose dependencies are all satisfied at the same wave run
concurrently (``asyncio.gather``). Per-node timeout from the node spec.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from personalclaw.knowledge.pipeline.graph import PipelineGraph
from personalclaw.knowledge.pipeline.registry import can_resolve_use_case, get_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of running a graph over one item."""

    outputs: dict[str, NodeOutput] = field(default_factory=dict)  # node_type → output
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.ran:
            return "failed"
        if self.skipped or self.failed:
            return "partial"
        return "done"

    def pooled_outputs(self) -> list[NodeOutput]:
        return [o for o in self.outputs.values() if o.success and o.pooled and o.text]


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
            for lst in (result.ran, result.failed, result.skipped):
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

    async def _run_one(self, node_type: str, ctx: NodeContext, result: ExecutionResult) -> None:
        spec = self._graph.nodes[node_type]
        params = self._params_for(node_type) or {}
        if not params.get("enabled", spec.enabled):
            result.skipped.append(node_type)
            self._notify(node_type, "skipped")
            return
        if not self._edges_satisfied(node_type, result):
            result.skipped.append(node_type)
            self._notify(node_type, "skipped")
            return

        backend = params.get("backend") or spec.backend
        use_case = params.get("use_case", spec.uses_use_case)
        node = get_node(node_type, backend)
        if node is None:
            logger.warning("no node registered for (%s, %s)", node_type, backend)
            result.skipped.append(node_type)
            self._notify(node_type, "skipped")
            return
        # Model-backed node with no active model → graceful skip (item goes partial).
        if not can_resolve_use_case(use_case):
            logger.info("skipping node %s — use-case %s has no active model", node_type, use_case)
            result.skipped.append(node_type)
            self._notify(node_type, "skipped")
            return

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
            timeout_s = self._scaled_timeout(node_type, spec, use_case, ctx)
        try:
            out = await asyncio.wait_for(node.run(inputs, ctx), timeout=timeout_s)
        except asyncio.TimeoutError:
            result.outputs[node_type] = NodeOutput(
                node_type=node_type, backend=backend, success=False, error="timeout"
            )
            result.failed.append(node_type)
            self._notify(node_type, "failed")
            return
        except Exception as exc:  # a node bug must not abort the item
            logger.exception("knowledge node %s failed", node_type)
            result.outputs[node_type] = NodeOutput(
                node_type=node_type, backend=backend, success=False, error=str(exc)
            )
            result.failed.append(node_type)
            self._notify(node_type, "failed")
            return
        result.outputs[node_type] = out
        if out.success:
            result.ran.append(node_type)
            self._notify(node_type, "done")
        else:
            result.failed.append(node_type)
            self._notify(node_type, "failed")

    # Model-backed media nodes whose work scales with media length. Their timeout
    # grows with the source's duration so a long video/audio can finish; pure-python
    # nodes (av_split, frame_extract, exif) keep the flat spec default.
    _DURATION_SCALED_NODES = frozenset(
        {"transcription", "video_classify", "ocr", "vision", "video_consolidate"}
    )
    # Seconds of node budget per second of media, per node. Transcription is the
    # heaviest (even segmented, each segment is a model call); the others sample.
    _BUDGET_PER_MEDIA_SEC = 2.0
    _MAX_NODE_TIMEOUT_S = 3600.0  # hard ceiling — a genuinely stuck node still dies

    def _scaled_timeout(self, node_type: str, spec, use_case, ctx: NodeContext) -> float:
        base = float(spec.timeout_s)
        if node_type not in self._DURATION_SCALED_NODES:
            return base
        dur = self._media_duration(ctx)
        if dur <= 0:
            return base
        scaled = base + dur * self._BUDGET_PER_MEDIA_SEC
        return min(self._MAX_NODE_TIMEOUT_S, max(base, scaled))

    def _media_duration(self, ctx: NodeContext) -> float:
        """Probe the source media's duration in seconds via ffprobe (cached per run).
        Returns 0 when unavailable (no ffprobe / not media / probe failure)."""
        cached = getattr(self, "_dur_cache", None)
        if cached is not None:
            return cached
        dur = 0.0
        path = ctx.file_path or ""
        if path:
            import shutil
            import subprocess

            ffprobe = shutil.which("ffprobe")
            if ffprobe:
                try:
                    out = subprocess.run(
                        [
                            ffprobe,
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    dur = float((out.stdout or "").strip() or 0)
                except (ValueError, OSError, subprocess.SubprocessError):
                    dur = 0.0
        self._dur_cache = dur
        return dur

    def _notify(self, node_type: str, phase: str) -> None:
        if self._on_node:
            try:
                self._on_node(node_type, phase)
            except Exception:
                logger.debug("pipeline on_node callback failed", exc_info=True)
