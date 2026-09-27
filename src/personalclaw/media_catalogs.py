"""Provider-contributed media-model catalogs (STT / TTS / image-gen).

The OpenAI-compatible audio + images *protocol* clients live in core
(``stt/openai_provider.py``, ``tts/openai_provider.py``, ``image_gen/openai_provider.py``)
and speak to ANY openai-compatible endpoint — that is a core capability. But WHICH
concrete models an endpoint serves is VENDOR-specific knowledge (OpenAI serves
``whisper-1`` / ``gpt-image-1`` / ``dall-e-*``; Alibaba serves ``qwen-image-*``; a generic
gateway serves whatever the user pins).

Rather than hard-code OpenAI's catalog in the core adapters, that vendor data is
CONTRIBUTED here, keyed by provider TYPE, by the provider's own app bundle
(``apps/openai-models`` registers OpenAI's audio/image catalog on load). Core adapters
look their catalog up by the config provider's ``type``; a type with no contributed
catalog (a bring-your-own gateway) advertises no curated models.

A catalog lists the models Settings → Models can offer to bind, and core reads the image one:
the image adapter lists it (``image_gen/openai_provider.py``). Speech models are listed by the
provider's own model catalog. A catalog names no default: like chat, a media call names its
model (the binding's), and one that names none is refused (``require_model``). The catalog's
default used to be sent in its place, and was all core read of a speech catalog.

Data-only (no logic): each entry is an :class:`MediaCatalog` of model rows, per capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw import app_code


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
    """A provider type's curated models for ONE capability."""

    models: tuple[MediaModel, ...] = ()


# capability ("image_gen") → provider type → catalog
_catalogs: dict[str, dict[str, MediaCatalog]] = {"image_gen": {}}


def register_media_catalog(capability: str, provider_type: str, catalog: MediaCatalog) -> None:
    """Contribute a vendor catalog for ``capability`` under ``provider_type``.

    Called by a provider app on load (e.g. openai-models registers OpenAI's image catalog
    under type ``openai``). Idempotent — last registration wins. Taken back when the app that
    registered it is unloaded."""
    by_type = _catalogs.setdefault(capability, {})
    by_type[provider_type] = catalog

    def _forget() -> None:
        if by_type.get(provider_type) is catalog:
            del by_type[provider_type]

    app_code.keep(_forget)


def get_media_catalog(capability: str, provider_type: str) -> MediaCatalog | None:
    """The catalog contributed for ``provider_type`` under ``capability``, or None
    when no app has contributed one (a bring-your-own endpoint → no curated models)."""
    return _catalogs.get(capability, {}).get(provider_type)
