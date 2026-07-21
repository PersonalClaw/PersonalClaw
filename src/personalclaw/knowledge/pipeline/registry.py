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
