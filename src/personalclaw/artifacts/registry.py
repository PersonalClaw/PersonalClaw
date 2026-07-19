"""Artifact provider registry.

Every caller (REST handlers, MCP tools, CLI, UI) resolves a provider through
``get_provider(name)`` — never a module singleton — so a future remote/object
-store backend registers here and works with zero caller change. The bundled
``NativeArtifactProvider`` is registered lazily via ``_ensure_native()``.
"""

from __future__ import annotations

import logging

from personalclaw.artifacts.provider import ArtifactProvider

logger = logging.getLogger(__name__)

_providers: dict[str, ArtifactProvider] = {}


def register_provider(provider: ArtifactProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def _ensure_native() -> None:
    """Idempotently register the bundled native filesystem provider."""
    if "native" not in _providers:
        from personalclaw.artifacts.native import NativeArtifactProvider

        register_provider(NativeArtifactProvider())


def get_provider(name: str | None = None) -> ArtifactProvider | None:
    """Resolve a provider by name; falsy name → the native default."""
    _ensure_native()
    return _providers.get(name or "native")


def list_providers() -> list[str]:
    _ensure_native()
    return list(_providers.keys())
