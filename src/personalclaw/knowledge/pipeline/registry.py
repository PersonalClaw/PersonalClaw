"""Node registry + use-case model resolution for the ingestion engine (#30).

Nodes register under ``(node_type, backend)`` (mirrors OpenForge's
``register_backend``). A model-backed node resolves its provider through a
Settings>Models **use-case** at run-time — whatever model the user selected for
that use-case is used (for image understanding with nothing selected, a chat model
that takes images); if none serves, the node is skipped gracefully (never a hard
item failure).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.knowledge.pipeline.outcomes import PhaseOutcome
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


async def resolve_runnable(node_type: str, preferred: str) -> tuple["ProcessingNode", str] | None:
    """The backend for *node_type* that can run now, preferring *preferred*.

    One node type may have several alternative implementations — a model-backed one and an
    engine-backed one for ``ocr``, pdfplumber vs pymupdf for a reader. "Runnable" means both
    halves hold: a model serves the node's own use-case (:func:`unserved_reason` is empty;
    a ``None`` use-case always is) AND :func:`node_available` says its dependency is present.

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
        if not await unserved_reason(node.uses_use_case) and node_available(node):
            return node, backend
    return None


async def unserved_reason(use_case: str | None) -> str:
    """Why no model serves *use_case* right now, in the words the item shows, or ``""`` when one
    does (so a model-backed node can run).

    A ``None`` use-case (pure-python node) is always served. Image understanding asks the
    platform's image reader (``providers.image_input.image_reader``): its binding, else a chat
    model that takes images — a fallback the bridge's no-instantiate probe cannot see, because
    whether a model takes images is its catalog's answer — and says what it says. Every other use
    case is :func:`unserved_reason_sync`'s. A probe that raises is "unserved", so the executor
    skips the node and marks the item partial rather than hard-failing.
    """
    if not use_case:
        return ""
    try:
        from personalclaw.providers.image_input import IMAGE_USE_CASE, image_reader

        if use_case == IMAGE_USE_CASE:
            reader = await image_reader()
            return "" if reader.ref else reader.reason
    except Exception:
        logger.debug("image reader check failed", exc_info=True)
        return _unchecked(use_case)
    return unserved_reason_sync(use_case)


def unserved_reason_sync(use_case: str) -> str:
    """:func:`unserved_reason` for a use case the bridge's probe answers (every one but image
    understanding): ``""`` when it resolves, else a sentence naming the use case as the Models
    page does and saying whether nothing is chosen for it or the chosen model cannot run."""
    from personalclaw.knowledge.pipeline.outcomes import use_case_name

    name = use_case_name(use_case)
    try:
        from personalclaw.providers.provider_bridge import can_resolve_use_case
        from personalclaw.providers.use_cases import active_model_refs

        if can_resolve_use_case(use_case):
            return ""
        if active_model_refs(use_case):
            return f"The {name} model chosen in Settings → Models can't run right now."
        return f"No {name} model is set up."
    except Exception:
        logger.debug("use-case resolvability check failed for %s", use_case, exc_info=True)
    return _unchecked(use_case)


def _unchecked(use_case: str) -> str:
    from personalclaw.knowledge.pipeline.outcomes import use_case_name

    return f"The {use_case_name(use_case)} model couldn't be checked."


async def why_not_runnable(
    node_type: str,
    preferred: str,
    *,
    use_case: str | None = None,
    unserved: str = "",
    pinned: bool = False,
) -> "PhaseOutcome":
    """The skip for *node_type* when no backend for it can run: what each one is missing.

    *use_case* and *unserved* describe the preferred backend as the executor resolved it: the
    use case it asked for and, when that is why it cannot run, the probe's answer; with no
    *unserved*, the preferred backend is the one whose own dependency is missing. A user-PINNED
    backend is the only one considered, as it is the only one the executor would run. Every
    other registered backend adds its own reason and fix, so an ``ocr`` step that a model OR an
    installed engine could have run names both.
    """
    from personalclaw.knowledge.pipeline import outcomes

    others = [] if pinned else [b for b in backends_for(node_type) if b != preferred]
    missing = []
    for backend in [preferred, *others]:
        node = NODE_REGISTRY.get((node_type, backend))
        if node is None:
            continue
        if backend == preferred:
            needed = use_case
            reason = unserved or await unserved_reason(needed)
        else:
            needed = node.uses_use_case
            reason = await unserved_reason(needed)
        if reason:
            missing.append(outcomes.no_model(needed or "", reason))
        elif not node_available(node):
            missing.append(_unavailable(node))
    if not missing:
        return outcomes.skipped("This step isn't available in this install.")
    return outcomes.either(missing)


def _unavailable(node: "ProcessingNode") -> "PhaseOutcome":
    """What *node* says when its own dependency is missing: its ``unavailable_outcome`` when it
    declares one (the engine-backed OCR names the app that adds an engine), else a sentence
    naming the step."""
    from personalclaw.knowledge.pipeline import outcomes

    declared = getattr(node, "unavailable_outcome", None)
    if callable(declared):
        try:
            said = declared()
        except Exception:
            logger.debug("unavailable_outcome failed for %s", node.node_type, exc_info=True)
        else:
            if isinstance(said, outcomes.PhaseOutcome):
                return said
    return outcomes.skipped(
        f"What {outcomes.step_name(node.node_type)} runs on isn't available in this install."
    )
