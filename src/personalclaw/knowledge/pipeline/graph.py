"""PipelineGraph — a code-owned conditional DAG of processing nodes (#30).

Per-type graphs subclass :class:`PipelineGraph` (in ``graphs.py``) and declare their
nodes + edges in ``build()``. The graph is validated at construction (referenced
nodes exist, no cycles via DFS) — the same cycle-reject discipline as the workflow
composition + tasks reconcile layers.

Graphs are OWNED BY CODE (not user data): the topology + lifecycle live here. Users
tune only per-node execution parameters (enable/backend/use-case/timeout) via config,
applied by the executor — they cannot rewire the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from personalclaw.knowledge.pipeline.types import Edge


class PipelineGraphError(ValueError):
    """A malformed graph — unknown node reference or a cycle."""


@dataclass
class NodeSpec:
    """A node slot in a graph: its type + default backend + bound use-case + whether
    it's on by default. The executor resolves the live (backend, use-case, enabled,
    timeout) from user execution-param config layered over these defaults."""

    node_type: str
    backend: str = ""
    uses_use_case: str | None = None
    enabled: bool = True
    timeout_s: float = 120.0


@dataclass
class PipelineGraph:
    """A conditional DAG for one knowledge type. Subclasses fill ``build()``."""

    item_type: str
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)  # nodes with no in-edges (entry)

    def add(self, spec: NodeSpec) -> NodeSpec:
        self.nodes[spec.node_type] = spec
        return spec

    def edge(self, frm: str, to: str, *, when: str | None = None) -> None:
        self.edges.append(Edge(from_node=frm, to_node=to, when=when))

    def loop_edge(self, frm: str, to: str, *, when: str, max_iters: int = 3) -> None:
        """Declare a bounded back-edge (frm → an EARLIER node `to`) that re-runs a
        graph segment while `frm`'s classification == `when`, up to `max_iters`
        times. The only permitted cycle; excluded from acyclic validation + topo
        order (the executor drives it — see PipelineExecutor)."""
        self.edges.append(Edge(from_node=frm, to_node=to, when=when, loop=True, max_iters=max_iters))

    # ── topology ──

    def predecessors(self, node_type: str) -> list[Edge]:
        # Forward-DAG predecessors only — a loop back-edge is not a dependency (it
        # would deadlock the wave scheduler). The executor consults loop edges itself.
        return [e for e in self.edges if e.to_node == node_type and not e.loop]

    def successors(self, node_type: str) -> list[Edge]:
        return [e for e in self.edges if e.from_node == node_type and not e.loop]

    def loop_edges(self) -> list[Edge]:
        """The declared bounded back-edges, driven by the executor."""
        return [e for e in self.edges if e.loop]

    def validate(self) -> None:
        """Referenced nodes must exist; the FORWARD graph must be acyclic (DFS).
        Declared loop back-edges are exempt (they're the intentional bounded cycle)."""
        for e in self.edges:
            if e.from_node not in self.nodes:
                raise PipelineGraphError(f"edge from unknown node {e.from_node!r}")
            if e.to_node not in self.nodes:
                raise PipelineGraphError(f"edge to unknown node {e.to_node!r}")
        self._reject_cycles()
        # roots = nodes with no incoming FORWARD edge (loop back-edges don't count —
        # else the loop target would never be a root and could stall the scheduler).
        if not self.roots:
            targets = {e.to_node for e in self.edges if not e.loop}
            self.roots = [n for n in self.nodes if n not in targets]

    def _reject_cycles(self) -> None:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.nodes}
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            if e.loop:  # bounded back-edge — intentional cycle, not a validation error
                continue
            adj[e.from_node].append(e.to_node)

        def dfs(n: str) -> None:
            color[n] = GREY
            for m in adj[n]:
                if color[m] == GREY:
                    raise PipelineGraphError(f"cycle through {m!r}")
                if color[m] == WHITE:
                    dfs(m)
            color[n] = BLACK

        for n in self.nodes:
            if color[n] == WHITE:
                dfs(n)

    def topo_order(self) -> list[str]:
        """A topological ordering of node types (Kahn). Assumes validated (acyclic)."""
        indeg = {n: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            if e.loop:  # back-edges don't participate in the forward topological order
                continue
            indeg[e.to_node] += 1
            adj[e.from_node].append(e.to_node)
        ready = [n for n, d in indeg.items() if d == 0]
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
        return order


def build_graph(cls: type, item_type: str) -> PipelineGraph:
    """Instantiate + validate a PipelineGraph subclass for *item_type*."""
    g = cls(item_type=item_type)
    g.build()  # type: ignore[attr-defined]
    g.validate()
    return g
