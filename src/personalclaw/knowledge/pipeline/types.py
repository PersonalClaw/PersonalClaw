"""Core types for the knowledge ingestion node-graph engine (#30).

A processing pipeline is a **conditional DAG** of :class:`ProcessingNode`s. Each node
consumes the outputs of its upstream nodes and emits a :class:`NodeOutput`; an edge
may be **conditional** on an upstream node's ``classification`` (data-dependent
branching) and the graph supports fan-out, fan-in, and adaptive re-extraction.

This is PClaw's improvement over OpenForge's flat slot-list (which passed data via
sidecar files): nodes here communicate through explicit typed outputs over real edges.

Graphs are **code-owned OO constructs** (per-type :class:`PipelineGraph` subclasses in
``graphs.py``) with their own lifecycles — NOT user-editable data. Users control only
per-node *execution parameters* (enable / backend / use-case / timeout) via config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class PoolRow:
    """One EXTRA extracted-content row a node contributes beyond its own output.

    A node is one STEP, and one step is normally one row. But a step whose product is a
    SET of role-sized views of the same document — the fetch-and-slice ``brief``/``body``/
    ``meta`` cut (WATCHED-SOURCES §5) — needs several rows with names of its own choosing
    (``slice:brief``, not ``document_slice``). Modelling that as three graph NODES was the
    alternative and it is worse: each would have to re-run the same section detection,
    which gives one deterministic cascade three chances to disagree with itself.
    """

    node_type: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    backend: str = ""


@dataclass
class NodeOutput:
    """One node's result, appended to the item's extracted-content pool.

    ``classification`` drives conditional edges; ``artifacts`` carries produced file
    paths (extracted frames, split audio) for downstream nodes; ``segments`` carries
    timestamped pieces (transcript lines). A node that fails sets ``success=False`` +
    ``error`` — the executor records it and continues where the DAG allows.
    """

    node_type: str
    backend: str = ""
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    vectors: list[float] | None = None
    segments: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    classification: str = ""
    success: bool = True
    error: str = ""
    duration_ms: int = 0
    # Set True for outputs that should land in the extracted-content pool (the
    # text bundle insights + chunk/embed read). Pure-structural nodes (a/v split,
    # frame-extract) set False — they only feed downstream nodes.
    pooled: bool = True
    # Extra pool rows this node contributes, each named by the node (see PoolRow).
    # Independent of ``pooled``: a node may contribute rows while pooling nothing of
    # its own, which is what a slicer does — its own text would duplicate the reader's.
    pool_rows: list[PoolRow] = field(default_factory=list)


@dataclass
class NodeContext:
    """Run-time context handed to every node: the item being processed, its working
    paths, and the knowledge store (for nodes that read prior pool entries). A node
    resolves its model via ``uses_use_case`` + the registry's resolver — it does NOT
    reach into config itself."""

    item_id: str
    item_type: str
    file_path: str = ""  # the source file (for file-backed types)
    content: str = ""  # the raw text (for text-backed types)
    url: str = ""  # the source URL (for bookmark types — scraped at ingest)
    work_dir: str = ""  # scratch dir for artifacts (frames, split audio)
    params: dict[str, Any] = field(default_factory=dict)  # per-node execution params


@runtime_checkable
class ProcessingNode(Protocol):
    """A single processing step. Implementations live in ``nodes/`` and register in
    ``NODE_REGISTRY[(node_type, backend)]``.

    ``uses_use_case`` (when set) names a Settings>Models use-case the node resolves
    its model through at run-time; pure-python nodes leave it None. ``run`` receives
    the outputs of this node's direct predecessors (keyed by their ``node_type``)
    plus the :class:`NodeContext`, and returns a :class:`NodeOutput`.
    """

    node_type: str
    backend: str

    @property
    def uses_use_case(self) -> str | None:
        """When set, names a Settings>Models use-case this node resolves its model
        through at run-time; pure-python nodes leave it None. Declared as a read-only
        property so a concrete node can supply it as a plain ``= None`` class attr."""
        ...

    async def run(self, inputs: dict[str, NodeOutput], ctx: NodeContext) -> NodeOutput: ...


@dataclass
class Edge:
    """A directed edge ``from_node → to_node``. When ``when`` is set, the edge is
    only traversed if the source node's ``classification`` equals it (conditional
    branch). ``None`` ``when`` = unconditional.

    A ``loop`` edge is a bounded BACK-edge: it points from a later node to an earlier
    one to re-run a segment of the graph (the adaptive video re-sampling — a
    classifier deciding to sample denser). It is excluded from acyclic validation and
    driven by the executor up to ``max_iters`` times, gated by the same ``when``
    classification. Loop edges are the ONLY permitted cycles."""

    from_node: str
    to_node: str
    when: str | None = None
    loop: bool = False
    max_iters: int = 1
