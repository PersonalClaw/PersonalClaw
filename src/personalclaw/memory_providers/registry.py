"""Memory provider registry."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_providers: dict[str, Any] = {}


def register_provider(name: str, provider: Any) -> None:
    _providers[name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> Any:
    return _providers.get(name)


def list_providers() -> list[str]:
    return list(_providers.keys())


def get_default_provider() -> Any:
    """Return the native memory provider — the SQLite+FAISS record/vector store
    (``VectorMemoryStore``), the L2 seam implementation.

    Note: per-session memory is normally resolved through
    ``ContextBuilder.get_memory_for`` (cwd-partitioned) and wrapped by a
    ``MemoryService``; this default exists for the registry's named-provider
    lookup. The markdown projection (``MemoryStore``) is composed by the service,
    not a provider itself (post-M2)."""
    if "native" not in _providers:
        from personalclaw.vector_memory import VectorMemoryStore

        store = VectorMemoryStore()
        store.init()
        register_provider("native", store)
    return _providers["native"]


def create_default_provider(config=None):
    """Extension factory for the default memory provider."""
    return get_default_provider()
