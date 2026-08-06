"""The flat sync-transport registry — name → live provider instance.

Mirrors :mod:`personalclaw.channel_transports` and ``action_providers/registry.py``: the
``sync`` provider-type handler (`providers/registry.py::SyncTypeHandler`) registers an
installed transport here on enable and removes it on disable. The sync cycle resolves the
configured transport by name through :func:`get_transport`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.sync_transports.base import SyncTransportProvider

_transports: dict[str, SyncTransportProvider] = {}


def register_transport(provider: SyncTransportProvider) -> None:
    _transports[provider.name] = provider


def unregister_transport(name: str) -> None:
    _transports.pop(name, None)


def get_transport(name: str) -> SyncTransportProvider | None:
    return _transports.get(name)


def list_transports() -> list[str]:
    return list(_transports.keys())
