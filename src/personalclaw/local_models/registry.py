"""The unified local-model provider registry.

One flat name→provider map, populated STRUCTURALLY by the app loader
(``ModelTypeHandler``) for every ``type: model`` provider that implements the
:class:`LocalModelProvider` management contract — core holds no per-provider
knowledge. Drives the download surface: per-provider cards, download jobs, and
``/api/models/available`` surfacing. Inference resolution lives in the per-use-case
registries (stt/tts/diarization/embedding), which are unaffected.

Providers register once when their app is enabled and unregister when disabled, so
this map is authoritative and does NOT get cleared on config changes (unlike the
per-use-case registries, whose remote adapters are rebuilt from config).
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.local_models.provider import LocalModel, LocalModelProvider

logger = logging.getLogger(__name__)

_providers: dict[str, LocalModelProvider] = {}
# The use-case capabilities each provider's app declared in its manifest — folded onto
# that provider's models when the model doesn't name its own (so a whisper model shows
# under STT, a pyannote model under diarization, an ollama model under chat/embedding).
_capabilities: dict[str, list[str]] = {}


def to_local_model(m: Any, *, capabilities: list[str] | None = None) -> LocalModel:
    """Adapt any provider's catalog entry to the uniform :class:`LocalModel`.

    The management surface speaks one shape. A provider's ``list_models`` may return a
    :class:`LocalModel` already, or a domain dataclass (SttModel / TtsVoice /
    EmbeddingModel / DiarizationModel) that shares ``name``/``size_mb``/``description``/
    ``downloaded`` — we read those structurally and fold the provider's declared
    ``capabilities`` (the use-cases it serves) onto the model unless the model names its
    own. Domain-only fields (dimension, language, …) stay on the domain object for
    inference; management never needs them.
    """
    if isinstance(m, LocalModel):
        if capabilities and not m.capabilities:
            m.capabilities = list(capabilities)
        return m
    return LocalModel(
        name=getattr(m, "name", ""),
        size_mb=float(getattr(m, "size_mb", 0) or 0),
        description=getattr(m, "description", ""),
        downloaded=bool(getattr(m, "downloaded", False)),
        capabilities=list(getattr(m, "capabilities", None) or capabilities or []),
        gated=bool(getattr(m, "gated", False)),
        source=getattr(m, "source", ""),
    )


def register_provider(
    provider: LocalModelProvider,
    capabilities: list[str] | None = None,
    *,
    name: str | None = None,
) -> None:
    """Register (or replace) a local-model provider + the capabilities it serves.

    ``name`` overrides the registry key (defaults to ``provider.name``). The app loader
    passes the APP name (``faster-whisper``, ``sentence-transformers``, …) so the
    download surface, the ``provider:model`` binding refs, and the Providers UI all key
    on the SAME identifier — the provider's internal ``.name`` (``faster_whisper`` /
    ``native``) can differ, and the inference registries already accept both."""
    key = name or provider.name
    _providers[key] = provider
    _capabilities[key] = list(capabilities or [])
    logger.debug("local-model provider registered: %s (caps=%s)", key, capabilities)


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _capabilities.pop(name, None)


def get_provider(name: str) -> LocalModelProvider | None:
    return _providers.get(name)


def capabilities_for(name: str) -> list[str]:
    """The use-case capabilities the named provider's app declared."""
    return list(_capabilities.get(name, []))


def list_providers() -> list[LocalModelProvider]:
    """All registered local-model providers (order of registration)."""
    return list(_providers.values())


def registered() -> list[tuple[str, LocalModelProvider]]:
    """All ``(registry_key, provider)`` pairs — the key is the app name the download
    surface + binding refs use (may differ from ``provider.name``)."""
    return list(_providers.items())


def _key_for(provider: LocalModelProvider) -> str:
    """The registry key a provider is stored under (its app name, which may differ
    from ``provider.name``). Falls back to ``provider.name``."""
    for k, p in _providers.items():
        if p is provider:
            return k
    return getattr(provider, "name", "")


async def catalog_for(provider: LocalModelProvider) -> list[LocalModel]:
    """The provider's models as uniform :class:`LocalModel`s (fail-soft → [])."""
    try:
        raw = await provider.list_models()
    except Exception:
        logger.debug("list_models failed for %s", getattr(provider, "name", "?"), exc_info=True)
        return []
    caps = capabilities_for(_key_for(provider))
    return [to_local_model(m, capabilities=caps) for m in raw]


class _ManagerBackedLocalProvider(LocalModelProvider):
    """Adapt a config-provider's :class:`ModelManager` catalog to the local-model
    contract, so a config-based downloadable provider (ollama) gets a download card +
    availability surfacing exactly like a bundled app. Management delegates to the
    manager (list/search/pull/delete); ``pull_model``'s stream is drained into a bool
    for the shared byte-progress job runner. ``searchable`` because a ModelManager owns a
    remote installable catalog (search_catalog)."""

    searchable = True

    def __init__(self, provider_name: str, display: str, manager: Any) -> None:
        self._name = provider_name
        self._display = display or provider_name
        self._mgr = manager

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display

    async def is_available(self) -> bool:
        try:
            return (await self._mgr.test_connection()).ok
        except Exception:
            return False

    @staticmethod
    def _to_local(mi: Any, *, downloaded: bool) -> LocalModel:
        return LocalModel(
            name=getattr(mi, "name", "") or getattr(mi, "id", ""),
            size_mb=round((getattr(mi, "size", 0) or 0) / (1024 * 1024), 1),
            description=(getattr(mi, "extra", {}) or {}).get("parameter_size", "")
            or getattr(mi, "description", ""),
            downloaded=downloaded,
            capabilities=list(getattr(mi, "capabilities", []) or []),
            source="ollama.com",
        )

    async def list_models(self) -> list[LocalModel]:
        return [self._to_local(mi, downloaded=True) for mi in await self._mgr.list_models()]

    async def search_models(self, query: str) -> list[LocalModel]:
        return [
            self._to_local(mi, downloaded=False) for mi in await self._mgr.search_catalog(query)
        ]

    async def download_model(self, model_name: str) -> bool:
        try:
            async for frame in self._mgr.pull_model(model_name):
                if getattr(frame, "error", ""):
                    logger.warning("pull %s/%s failed: %s", self._name, model_name, frame.error)
                    return False
            return True
        except Exception:
            logger.warning("pull %s/%s raised", self._name, model_name, exc_info=True)
            return False

    async def delete_model(self, model_name: str) -> bool:
        try:
            await self._mgr.delete_model(model_name)
            return True
        except Exception:
            logger.warning("delete %s/%s raised", self._name, model_name, exc_info=True)
            return False


def register_config_model_managers() -> None:
    """Register every config.json model provider whose catalog is a ``ModelManager``
    (owns local model download/management — ollama) into the local-model registry, so
    it gets a unified download card + availability surfacing. Providers that only
    discover (no management axis) are skipped — they surface via the config path only.
    Idempotent: safe to call on each config change."""
    from personalclaw.llm.catalog import ModelManager
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    for entry in registry.list_entries():
        try:
            catalog = registry.build_catalog(entry)
        except Exception:
            catalog = None
        if isinstance(catalog, ModelManager):
            caps = [
                getattr(c, "value", c)
                for c in (getattr(entry, "declared_capabilities", None) or [])
            ]
            register_provider(
                _ManagerBackedLocalProvider(entry.name, entry.name, catalog),
                capabilities=caps,
            )


# Use-cases whose models are LOCALLY DOWNLOADED + managed on the user's machine. A
# provider serving only remote/hosted use-cases (image_gen via FAL, chat via a hosted
# API) is NOT a local-model provider even though it may inherit no-op download/delete
# stubs from a base ABC. (chat/embedding ARE here because ollama serves them locally.)
_DOWNLOADABLE_CAPS = frozenset({"stt", "tts", "embedding", "diarization", "chat"})


def is_local_model_provider(obj: object, capabilities: list[str] | None = None) -> bool:
    """Whether ``obj`` participates in the local-model download surface.

    Two gates, both required:
    1. It implements the management contract (list/download/delete + name/display_name)
       — either by subclassing :class:`LocalModelProvider` or duck-typing it.
    2. It's a LOCAL provider: it either subclasses :class:`LocalModelProvider` explicitly,
       or (for not-yet-migrated apps) its declared capabilities intersect the locally-
       downloadable set. This excludes hosted providers (FAL image-gen) that inherit
       download/delete stubs from their base ABC but download nothing.
    """
    has_contract = all(
        hasattr(obj, attr)
        for attr in ("name", "display_name", "list_models", "download_model", "delete_model")
    )
    if not has_contract:
        return False
    if isinstance(obj, LocalModelProvider):
        return True
    caps = set(capabilities or [])
    return bool(caps & _DOWNLOADABLE_CAPS)
