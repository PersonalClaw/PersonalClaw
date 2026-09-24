"""The app-facing notification-delivery contract (MULTI-TENANCY-ENTITY `TSE2-5`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class NotificationDeliveryProvider(ABC):
    """A backend that can deliver a notification to somebody this harness cannot reach.

    Three members, deliberately no more:

    * :attr:`delivery_name` — the registry key, the provider's OWN name (not the app's), so a
      note records who took it and a disabled app is removed by the key it registered under.
      Mirrors ``MessageSourceProvider.source_name``.
    * :meth:`addresses` — *"can you reach this username?"*. Asked before the note is handed
      over, so a provider that only knows two teammates is never given a third's note. This is
      the **routing** decision and it belongs to the provider: core knows the addressee string
      and nothing else about who that is.
    * :meth:`deliver` — take the note. Returns whether it was accepted, because
      :func:`personalclaw.notification_providers.registry.deliver_to_addressee` records the
      accepting provider on the note and must not claim a delivery that did not happen.

    **The note arrives whole, title and body included.** That is the same content an ``inbox``
    or ``channel`` provider already handles, and the trust model is the same: an installed app
    with declared permissions. It is deliberately NOT the content-free boundary
    :mod:`personalclaw.push` enforces — that one exists because a third-party push *service*
    sees the payload, which is a different party from an app the owner installed.

    **Synchronous on purpose.** It is called from ``DashboardState.notify``, which every
    emitter in the system calls and which is not async. A provider that needs the network
    should hand off to its own thread and return, exactly as ``push.deliver_async`` does —
    ``notify`` must never block on a delivery backend.
    """

    @property
    @abstractmethod
    def delivery_name(self) -> str: ...

    @abstractmethod
    def addresses(self, username: str) -> bool: ...

    @abstractmethod
    def deliver(self, note: Mapping[str, Any]) -> bool: ...
