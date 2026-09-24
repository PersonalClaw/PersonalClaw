"""The app-contributed notification-delivery registry (MULTI-TENANCY-ENTITY `TSE2-5`).

Mirrors ``inbox_providers/registry.py`` exactly: the ``notification`` provider-type handler
(``providers/registry.py::NotificationTypeHandler``) registers an installed app's backend here
on enable and removes it on disable. **Holds INSTANCES, not classes**, for the reason that
module gives — the manifest factory has already run and may close over the app's own
config/credentials, so re-instantiating is not possible.

Keyed by the provider's own ``delivery_name``, because that is the string a note records in
``routed_to`` and the key a disable must remove. This module deliberately imports nothing from
``providers/`` — the dependency runs one way, handler → registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from personalclaw.notification_providers.base import NotificationDeliveryProvider

logger = logging.getLogger(__name__)

_backends: dict[str, "NotificationDeliveryProvider"] = {}


def register_provider(provider: "NotificationDeliveryProvider") -> str:
    """Register an app-contributed delivery backend under its own ``delivery_name``.

    Returns the name it was registered under so the caller can log/deregister by the same key
    it actually used.
    """
    name = str(getattr(provider, "delivery_name", "") or "")
    if not name:
        raise ValueError(
            "an app-contributed notification delivery backend must expose a non-empty "
            "delivery_name"
        )
    _backends[name] = provider
    return name


def unregister_provider(name: str) -> None:
    """Remove a backend. A disabled/uninstalled app must leave NO phantom route still
    claiming it can reach a teammate — a note recorded as ``routed_to: <gone app>`` is worse
    than one visibly withheld, because it reads as delivered."""
    _backends.pop(name, None)


def list_provider_names() -> list[str]:
    """Every registered backend name, for debug/doctor surfaces."""
    return sorted(_backends)


def deliver_to_addressee(note: Mapping[str, Any], addressee: str) -> str:
    """Hand *note* to the first backend that says it addresses *addressee*.

    Returns the accepting backend's name, or ``""`` when nobody took it — which is the
    ordinary case on a single-user install with no delivery app, and is exactly why a
    foreign-addressed note is *also* persisted locally rather than only routed. "Nobody could
    route it" must read as "visible here, not fired here", never as "delivered".

    **First acceptance wins, and a decline is not an error.** ``addresses`` is the provider's
    own routing statement, so asking every backend and taking the first yes is the whole
    decision; core has no basis for preferring one route to another. A backend that raises is
    logged and skipped, because one broken app must not be able to suppress a second app's
    working route — the fail-open posture the whole notify path already takes.
    """
    target = (addressee or "").strip().lower()
    if not target:
        return ""
    for name, backend in list(_backends.items()):
        try:
            if not backend.addresses(target):
                continue
            if backend.deliver(note):
                return name
        except Exception:
            logger.warning(
                "notification delivery backend %r failed for addressee %r",
                name,
                target,
                exc_info=True,
            )
    return ""
