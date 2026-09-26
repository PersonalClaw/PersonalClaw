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
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from personalclaw.channel_transports.base import ChannelTransportProvider

logger = logging.getLogger(__name__)

_transports: "dict[str, ChannelTransportProvider]" = {}

#: ``id(transport)`` → ``(transport, services)`` for every transport whose inbound receiver the
#: gateway started. Keyed by the INSTANCE (and holding it, so the id stays unique): a rebuilt
#: transport is a new object, and only the one whose receiver is actually running may hand it on.
_inbound: "dict[int, tuple[ChannelTransportProvider, Any]]" = {}

#: The one in-app transport. It is how the dashboard itself talks, not a channel through which a
#: remote owner can be reached, so no "is a channel configured" question counts it.
WEBUI_TRANSPORT = "webui"


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


async def start_inbound(transport: "ChannelTransportProvider", services: Any) -> None:
    """Start ``transport``'s inbound receiver on ``services``, and remember that it runs.

    The gateway drives every transport through here at boot. Remembering the handle is what lets
    a transport rebuilt from saved settings (:func:`hand_over_inbound`) take the receiver over.
    """
    await transport.start_inbound(services)
    _inbound[id(transport)] = (transport, services)


async def hand_over_inbound(pairs: "Iterable[tuple[Any, Any]]") -> None:
    """Move each running inbound receiver from a rebuilt transport to its replacement.

    ``pairs`` is what :meth:`ProviderRegistry.rebuild` returns. Rebuilding a channel used to
    leave the OLD instance's receiver connected on the old token while the new instance, which
    outbound now used, had none — so a saved token never reached inbound, and nothing stopped the
    orphan. A transport whose inbound never started (enabled after boot) has nothing to move.
    When the saved settings build no transport (the rebuild failed; the app shows the error), the
    old receiver is still stopped: its app is no longer registered, so nothing else could.
    """
    from personalclaw.channel_transports.base import ChannelTransportProvider

    for old, new in pairs:
        running = _inbound.pop(id(old), None)
        if running is None:
            continue
        _old, services = running
        try:
            await old.stop_inbound()
        except Exception:
            logger.warning(
                "channel %s: stopping the replaced receiver failed", old.name, exc_info=True
            )
        if not isinstance(new, ChannelTransportProvider):
            logger.warning(
                "channel %s: its saved settings built no transport, so its receiver is stopped",
                old.name,
            )
            continue
        try:
            await start_inbound(new, services)
        except Exception:
            logger.warning(
                "channel %s: starting the receiver on the saved settings failed",
                new.name,
                exc_info=True,
            )


async def configured_channels(
    transports: "Iterable[ChannelTransportProvider] | None" = None,
) -> list[str]:
    """Display names of the external channels that report themselves configured.

    Asked of each channel's OWN ``health()``, because only the app knows what it needs: a state of
    ``offline`` is a transport saying it has nothing to connect with (no token, no account), while
    ``ready`` and ``error`` (half-up — outbound works, inbound waits) both mean it is configured.
    Core used to answer this from two Slack credential NAMES, which put one vendor in core and
    could not see a Slack configured through its settings.

    ``transports`` defaults to the registered ones (the gateway, or a CLI command that booted the
    provider registry). A probe that raises is not a configured channel, and says why.
    """
    names: list[str] = []
    candidates = (
        list(transports)
        if transports is not None
        else [t for t in (get_transport(n) for n in list_transports()) if t is not None]
    )
    for transport in candidates:
        if transport.name == WEBUI_TRANSPORT:
            continue
        try:
            state = (await transport.health()).get("state")
        except Exception:
            logger.warning("channel %s: health probe failed", transport.name, exc_info=True)
            continue
        if state != "offline":
            names.append(transport.display_name)
    return names
