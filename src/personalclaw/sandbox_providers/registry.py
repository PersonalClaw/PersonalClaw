"""The flat sandbox-provider registry — name → live provider instance.

Mirrors :mod:`personalclaw.sync_transports.registry` and ``channel_transports``: the ``none``
builtin self-registers on import (:func:`register_builtin_providers`), and an installed
``sandbox`` app is registered on enable / removed on disable by
:class:`personalclaw.providers.registry.SandboxTypeHandler`. Spawn sites resolve the configured
backend by name through :func:`resolve_provider`: no name is ``none``, and a name that is not
registered is refused, never ``none``.
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
    """Register the always-present ``none`` provider and the core-native ``docker`` tier.
    Idempotent.

    ``none`` and ``docker`` are core built-ins, registered here rather
    than through the extension system: ``none`` is always available; ``docker`` self-gates via its
    cached daemon probe, so registering it unconditionally is safe (an unavailable ``docker`` name
    refuses at ``wrap`` with a typed error, it does not silently downgrade). Installed ``sandbox``
    apps (a future ``podman``/``byoi``, or ``lima``) are NOT registered here — the extension system
    owns their lifecycle via ``SandboxTypeHandler`` (enable/disable), one source of truth each.
    """
    from personalclaw.sandbox_providers.docker import DockerSandboxProvider
    from personalclaw.sandbox_providers.none import NoneSandboxProvider

    register_provider(NoneSandboxProvider())
    register_provider(DockerSandboxProvider())


def resolve_provider(name: str = "") -> "SandboxProvider":
    """Return the named provider; the ``none`` builtin when no tier is named.

    A NAMED tier that is not registered raises :class:`SandboxUnavailableError`, the same refusal
    a registered tier gives when its runtime is down. It used to resolve to ``none``, so whatever
    asked for isolation ran on the host instead, with nothing to say so: an agent session resumed
    after a restart while its tier's app was off, a second opinion inheriting a stalled run's
    tier. Ensures the builtins are registered first.
    """
    from personalclaw.sandbox_providers.base import SandboxUnavailableError
    from personalclaw.sandbox_providers.none import NONE_PROVIDER_NAME, NoneSandboxProvider

    if NONE_PROVIDER_NAME not in _providers:
        register_builtin_providers()
    if not name or name == NONE_PROVIDER_NAME:
        return _providers.get(NONE_PROVIDER_NAME) or NoneSandboxProvider()
    provider = _providers.get(name)
    if provider is None:
        raise SandboxUnavailableError(
            what=f"{name} sandbox requested but not installed",
            why="no sandbox tier by that name is installed and turned on.",
            fix="turn on the app that provides it, or choose a different sandbox tier.",
        )
    return provider
