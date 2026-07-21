"""Web UI channel transport — the dashboard websocket as a peer transport.

The dashboard already routes browser-originated messages through the chat runner
(inbound) and broadcasts assistant output over the websocket fan-out (outbound).
This adapter exposes that outbound path through :class:`ChannelTransportProvider`
so the comms manager can present the Web UI alongside external channel
transports (Slack, …). It is always
"connected" once the dashboard server is up.

The dashboard ``DashboardState`` lives in the gateway aiohttp app
(``app["state"]``); there is no module-global accessor. The comms manager binds
the live state onto this transport (:meth:`bind_state`) before probing health or
sending, so this adapter never reaches for a global that may not exist.
"""

from typing import TYPE_CHECKING, Any

from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelTransportProvider,
    OutboundMessage,
)

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState


class WebUITransport(ChannelTransportProvider):
    """Native dashboard websocket transport."""

    def __init__(self) -> None:
        self._connected = False
        self._state: "DashboardState | None" = None

    def capabilities(self) -> ChannelCapabilities:
        # The dashboard chat UI: rich markdown, attachments, edits, typing; inbound
        # via the chat runner (the canonical WebUI consumer). No emoji reactions.
        return ChannelCapabilities(
            inbound=True,
            threads=False,
            attachments=True,
            edits=True,
            rich_text=True,
            typing_indicator=True,
        )

    @property
    def name(self) -> str:
        return "webui"

    @property
    def display_name(self) -> str:
        return "Web UI"

    def bind_state(self, state: "DashboardState | None") -> None:
        """Inject the live dashboard state (called by the comms manager)."""
        self._state = state

    async def connect(self) -> bool:
        # The dashboard websocket lives in the gateway aiohttp app; nothing to
        # spin up here. Mark connected so the management surface reflects that
        # the in-app transport is available.
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, message: OutboundMessage) -> bool:
        """Deliver a message to a dashboard chat session."""
        state = self._state
        if state is None:
            return False
        session = state._sessions.get(message.channel_id)
        if session is None:
            return False
        session.append("assistant", message.text, "msg msg-assistant")
        state.push_sessions_update()
        return True

    @property
    def connected(self) -> bool:
        # Operational readiness, not an explicit-connect flag: the in-app websocket
        # lives in the gateway and is available whenever the dashboard state is bound —
        # so `connected` must agree with health() (ready when bound). Deriving it from
        # _state (not the _connected flag that only an explicit /connect sets) fixes the
        # dot-vs-label contradiction (green "ready" dot + "Not connected" label) for this
        # always-on transport.
        return self._state is not None

    async def health(self) -> dict[str, Any]:
        if self._state is None:
            return {"state": "offline", "detail": "dashboard not started"}
        return {"state": "ready", "detail": "in-app websocket"}


def create_provider(config: dict[str, Any] | None = None) -> "WebUITransport":
    return WebUITransport()
