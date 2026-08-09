"""The flat sandbox-provider registry — name → live provider instance.

Mirrors :mod:`personalclaw.sync_transports.registry` and ``channel_transports``: the ``none``
builtin self-registers on import (:func:`register_builtin_providers`), and an installed
``sandbox`` app is registered on enable / removed on disable by
:class:`personalclaw.providers.registry.SandboxTypeHandler`. Spawn sites resolve the configured
backend by name through :func:`get_provider`, falling back to ``none``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.sandbox_providers.base import SandboxProvider

_providers: dict[str, "SandboxProvider"] = {}


def register_provider(provider: "SandboxProvider") -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> "SandboxProvider | None":
    return _providers.get(name)


def list_providers() -> list[str]:
    return list(_providers.keys())


def register_builtin_providers() -> None:
    """Register the always-present ``none`` provider. Idempotent.

    Installed ``sandbox`` apps are NOT registered here — the extension system owns their
    lifecycle via ``SandboxTypeHandler`` (enable/disable), keeping one source of truth per
    extension-backed provider.
    """
    from personalclaw.sandbox_providers.none import NoneSandboxProvider

    register_provider(NoneSandboxProvider())


def resolve_provider(name: str = "") -> "SandboxProvider":
    """Return the named provider, or the ``none`` builtin as the always-available fallback.

    A sandbox is a best-effort bound: an unknown/unavailable name must never BLOCK a spawn, so
    this resolves to ``none`` rather than raising. Ensures the builtin is registered first.
    """
    from personalclaw.sandbox_providers.none import NONE_PROVIDER_NAME, NoneSandboxProvider

    if NONE_PROVIDER_NAME not in _providers:
        register_builtin_providers()
    provider = _providers.get(name) if name else None
    if provider is None:
        provider = _providers.get(NONE_PROVIDER_NAME) or NoneSandboxProvider()
    return provider
