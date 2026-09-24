"""Channel transports — the comms-transport registry.

A small in-memory registry of live :class:`ChannelTransportProvider` instances,
keyed by transport name. Two population paths:

- **Web UI** — the always-present in-app transport, registered at boot by
  :func:`register_default_transports` (it is not an extension).
- **Slack (and future Telegram/Discord)** — registered/unregistered by the
  extension system: enabling the ``slack-channel`` extension runs
  ``ChannelTypeHandler.register`` → :func:`register_transport`; disabling it
  runs ``deregister`` → :func:`unregister_transport`.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.channel_transports.base import ChannelTransportProvider

logger = logging.getLogger(__name__)

_transports: "dict[str, ChannelTransportProvider]" = {}


def register_transport(provider: "ChannelTransportProvider") -> None:
    _transports[provider.name] = provider


def unregister_transport(name: str) -> None:
    _transports.pop(name, None)


def get_transport(name: str) -> "ChannelTransportProvider | None":
    return _transports.get(name)


def list_transports() -> list[str]:
    return list(_transports.keys())


def register_default_transports() -> None:
    """Register the always-present in-app Web UI transport. Idempotent.

    Slack is NOT registered here — the extension system owns its lifecycle via
    ``ChannelTypeHandler`` (enable/disable). This keeps one source of truth for
    every extension-backed transport.
    """
    from personalclaw.channel_transports.webui import WebUITransport

    register_transport(WebUITransport())
