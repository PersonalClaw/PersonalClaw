"""Node registry + use-case model resolution for the ingestion engine (#30).

Nodes register under ``(node_type, backend)`` (mirrors OpenForge's
``register_backend``). A model-backed node resolves its provider through a
Settings>Models **use-case** at run-time — whatever model the user selected for
that use-case is used; if none is active the node is skipped gracefully (never a
hard item failure).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.knowledge.pipeline.types import ProcessingNode

logger = logging.getLogger(__name__)

# (node_type, backend) → node instance. Backends for one node_type are alternative
# implementations (e.g. pdf_text via pdfplumber|pymupdf); the active one is chosen
# by per-node execution params, defaulting to the graph's declared backend.
NODE_REGISTRY: dict[tuple[str, str], "ProcessingNode"] = {}


def register_node(node: "ProcessingNode") -> None:
    """Register a node implementation under ``(node_type, backend)``."""
    NODE_REGISTRY[(node.node_type, node.backend)] = node


def get_node(node_type: str, backend: str) -> "ProcessingNode | None":
    return NODE_REGISTRY.get((node_type, backend))


def backends_for(node_type: str) -> list[str]:
    """Every registered backend for *node_type*, in registration order."""
    return [backend for (nt, backend) in NODE_REGISTRY if nt == node_type]


def node_available(node: "ProcessingNode") -> bool:
    """Whether *node* can actually run in this process RIGHT NOW.

    ``available`` is an OPTIONAL member of the node protocol: a pure-python node omits it
    and is always available. A node whose work depends on something installable — an OCR
    engine contributed by a removable app — implements it as a LIVE probe of the thing it
    needs, never a truthiness test on an imported symbol. A probe that raises counts as
    unavailable: this runs on the ingest path and must not be able to fail it.
    """
    probe = getattr(node, "available", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except Exception:
        logger.debug("node availability probe failed for %s", node.node_type, exc_info=True)
        return False


def resolve_runnable(node_type: str, preferred: str) -> tuple["ProcessingNode", str] | None:
    """The backend for *node_type* that can run now, preferring *preferred*.

    One node type may have several alternative implementations — a model-backed one and an
    engine-backed one for ``ocr``, pdfplumber vs pymupdf for a reader. "Runnable" means both
    halves hold: the node's own use-case resolves to an active model (``None`` use-case
    always does) AND :func:`node_available` says its dependency is present.

    The preferred backend wins whenever it is runnable, so this never changes what a working
    install does. Only when the preference cannot run is an alternative tried, in registration
    order; ``None`` means nothing can run and the caller skips the node — which is the
    pre-existing graceful-skip behaviour, unchanged.
    """
    ordered = [preferred] + [b for b in backends_for(node_type) if b != preferred]
    for backend in ordered:
        node = NODE_REGISTRY.get((node_type, backend))
        if node is None:
            continue
        if can_resolve_use_case(node.uses_use_case) and node_available(node):
            return node, backend
    return None


def can_resolve_use_case(use_case: str | None) -> bool:
    """True if a model is active for *use_case* (so a model-backed node can run).

    None use-case (pure-python node) → always True. Resolution failure → False, so
    the executor skips the node and marks the item partial rather than hard-failing.
    """
    if not use_case:
        return True
    try:
        from personalclaw.providers.provider_bridge import can_resolve_use_case as _can

        return bool(_can(use_case))
    except Exception:
        logger.debug("use-case resolvability check failed for %s", use_case, exc_info=True)
        return False
