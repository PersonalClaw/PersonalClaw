"""Whether the model serving a chat turn takes an image as pixels, read from the platform's record.

Two facts decide it, both already recorded by the platform, and both must say yes:

* the provider TYPE's :class:`~personalclaw.llm.capabilities.ProviderCapability` —
  ``supports_vision`` is the app's declaration that the wire it speaks carries image parts
  (Anthropic Messages, Bedrock Converse, OpenAI-compatible, Ollama);
* the MODEL's capability tags — ``image_modality`` on the row the provider's own catalog lists
  for it, which is what Settings → Models shows. When the catalog cannot say (no catalog, the
  model is not listed, the listing failed) the tags come from
  :func:`~personalclaw.llm.catalog.infer_capabilities` on the id, the same classifier every
  catalog tags with.

Screen frames and attached images both ask HERE, so the composer's promise and the turn's
delivery come from one answer. A "no" carries its reason as a sentence: it is the product copy
the attachment chip and the screen-share control show, so it names what is missing and never
blames the image.

The model tags are memoized per ``(entry, model)`` for :data:`_TTL_SECS`: a catalog listing can
be a network call, and an image turn must not pay it every time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: How long one catalog answer about a model's tags stands.
_TTL_SECS = 600.0
#: The longest an image turn waits on a catalog listing before it falls back to the id.
_LIST_TIMEOUT_SECS = 8.0

_tags_memo: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}


@dataclass(frozen=True)
class ImageInput:
    """The answer: ``accepted`` is True when an image can ride the turn as pixels.

    ``reason`` is empty when accepted; otherwise it is a user-facing sentence saying why not.
    ``model`` is the model id the answer is about (``""`` when none could be named).
    """

    accepted: bool
    reason: str = ""
    model: str = ""


def clear_cache() -> None:
    """Forget every memoized model answer (tests, and a provider's settings changing)."""
    _tags_memo.clear()


def agent_takes_no_images(label: str) -> ImageInput:
    """The answer for a turn an external agent runtime serves (an ACP CLI owns its own wire)."""
    who = label.strip() or "This agent"
    return ImageInput(False, f"{who} can't be handed an image.")


def agent_label(runtime_id: str) -> str:
    """A person's name for the agent runtime ``runtime_id`` (``acp:claude-code``), or ``""``.

    The runtime entry's own ``runtime_label`` when it has one, else the id after ``acp:``.
    """
    rid = (runtime_id or "").strip()
    if not rid:
        return ""
    try:
        from personalclaw.llm.registry import get_default_registry

        label = str(get_default_registry().get_entry(rid).options.get("runtime_label") or "")
    except Exception:  # noqa: BLE001 — an unregistered runtime is named by its id
        label = ""
    return label.strip() or rid.split(":", 1)[-1]


async def image_input(served_ref: str) -> ImageInput:
    """Whether ``served_ref`` (``"<entry>:<model>"``) takes images, from the platform's record.

    An empty ref, a bare id with no entry, or an unknown entry answers no: the provider type is
    what says whether its wire carries an image, and a type nobody can name has not said so.
    """
    from personalclaw.providers.use_cases import split_ref

    parsed = split_ref(served_ref or "")
    if not parsed or not parsed[0] or not parsed[1] or parsed[1].lower() == "auto":
        return ImageInput(False, "No chat model is chosen yet.")
    entry_name, model = parsed
    try:
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        entry = registry.get_entry(entry_name)
        carries = registry.capability_of(entry.type).supports_vision
    except Exception:  # noqa: BLE001 — an unknown entry or type is "has not declared it"
        logger.debug("image input: no capability record for %r", entry_name, exc_info=True)
        return ImageInput(False, f"{model} can't take images.", model)
    if not carries:
        return ImageInput(False, f"{model} can't take images.", model)
    tags = await _model_tags(registry, entry, model)
    if "image_modality" not in tags:
        return ImageInput(False, f"{model} can't take images.", model)
    return ImageInput(True, "", model)


async def _model_tags(registry, entry, model: str) -> frozenset[str]:
    """The capability tags recorded for ``model`` on ``entry``: its catalog row, else its id."""
    from personalclaw.llm.catalog import infer_capabilities

    key = (entry.name, model)
    now = time.monotonic()
    hit = _tags_memo.get(key)
    if hit is not None and now - hit[0] < _TTL_SECS:
        return hit[1]
    tags: frozenset[str] | None = None
    catalog = registry.build_catalog(entry)
    if catalog is not None:
        try:
            rows = await asyncio.wait_for(catalog.list_models(), timeout=_LIST_TIMEOUT_SECS)
            row = next((r for r in rows if model in (r.id, r.name)), None)
            if row is not None and row.capabilities:
                tags = frozenset(row.capabilities)
        except Exception:  # noqa: BLE001 — an unlistable catalog falls back to the id
            logger.debug("image input: catalog listing failed for %r", entry.name, exc_info=True)
    if tags is None:
        tags = frozenset(infer_capabilities(model))
    _tags_memo[key] = (now, tags)
    return tags
