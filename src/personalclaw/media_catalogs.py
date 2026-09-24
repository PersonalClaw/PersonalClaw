"""Provider-contributed media-model catalogs (STT / TTS / image-gen).

The OpenAI-compatible audio + images *protocol* clients live in core
(``stt/openai_provider.py``, ``tts/openai_provider.py``, ``image_gen/openai_provider.py``)
and speak to ANY openai-compatible endpoint — that is a core capability. But WHICH
concrete models an endpoint serves, and the sensible unpinned default, is
VENDOR-specific knowledge (OpenAI serves ``whisper-1`` / ``gpt-image-1`` / ``dall-e-*``;
Alibaba serves ``qwen-image-*``; a generic gateway serves whatever the user pins).

Rather than hard-code OpenAI's catalog in the core adapters, that vendor data is
CONTRIBUTED here, keyed by provider TYPE, by the provider's own app bundle
(``apps/openai-models`` registers OpenAI's audio/image catalog on load). Core adapters
look their catalog up by the config provider's ``type``; a type with no contributed
catalog (a bring-your-own gateway) advertises no curated models and requires a pinned
model — exactly the previous ``_is_openai_endpoint()`` behavior, now provider-declared
instead of host-sniffed.

Data-only (no logic): each entry is an :class:`MediaCatalog` of model rows + an
optional default model id, per capability (``stt`` / ``tts`` / ``image_gen``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MediaModel:
    """One vendor model row for a media capability.

    ``extra`` carries capability-specific fields (image sizes, supports_edit, …) so
    core adapters stay generic — they pass ``extra`` through to their own model dataclass.
    """

    name: str
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaCatalog:
    """A provider type's curated models + unpinned default for ONE capability."""

    models: tuple[MediaModel, ...] = ()
    default_model: str = ""


# capability ("stt"|"tts"|"image_gen") → provider type → catalog
_catalogs: dict[str, dict[str, MediaCatalog]] = {"stt": {}, "tts": {}, "image_gen": {}}


def register_media_catalog(capability: str, provider_type: str, catalog: MediaCatalog) -> None:
    """Contribute a vendor catalog for ``capability`` under ``provider_type``.

    Called by a provider app on load (e.g. openai-models registers OpenAI's stt/tts/
    image catalogs under type ``openai``). Idempotent — last registration wins."""
    _catalogs.setdefault(capability, {})[provider_type] = catalog


def get_media_catalog(capability: str, provider_type: str) -> MediaCatalog | None:
    """The catalog contributed for ``provider_type`` under ``capability``, or None
    when no app has contributed one (a bring-your-own endpoint → no curated models,
    caller requires a pinned model)."""
    return _catalogs.get(capability, {}).get(provider_type)


def unregister_media_catalogs(provider_type: str) -> None:
    """Drop every capability's catalog for ``provider_type`` (app disable/uninstall)."""
    for by_type in _catalogs.values():
        by_type.pop(provider_type, None)
