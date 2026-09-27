"""Abstract base for channel transport providers.

A *channel transport* carries messages between PersonalClaw and an external
communication system (the dashboard Web UI, Slack, and future Telegram/Discord).
Each transport owns one external system: it connects, sends outbound messages,
and reports its own health. Inbound delivery is transport-specific — see the
note on the inbound seam below.

**Inbound:** transports do NOT own an inbound dispatch loop here. A channel app's
receiver (Slack's Socket Mode, Telegram's long-poll, …) lives in its own bundle, started
through :meth:`ChannelTransportProvider.start_inbound`, and routes inbound messages through
the platform's guarded door; for the Web UI, the dashboard chat runner is the canonical
consumer. WHEN a receiver runs is core's one rule,
:func:`personalclaw.channel_transports.reconcile_inbound`. The ``ChannelManager`` (comms) is
a management + visibility surface over the registered transports, not a second inbound router.
"""

import unicodedata
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

#: The longest target id :meth:`ChannelTransportProvider.validate_target` accepts by default. A
#: chat, channel or user id is far shorter on every platform; anything longer is pasted text.
_TARGET_MAX_LEN = 256


@dataclass
class OutboundMessage:
    """A message to send to an external channel."""

    channel_id: str
    text: str
    thread_id: str = ""
    sender: str = "personalclaw"
    metadata: dict[str, Any] | None = None


@dataclass
class ChannelMessage:
    """The symmetric INBOUND shape (#40) — the dual of :class:`OutboundMessage`.

    A transport that owns an inbound source normalizes its native payload to this,
    so the platform sees one canonical inbound message regardless of channel."""

    channel_id: str
    text: str
    sender: str = ""
    thread_id: str = ""
    message_id: str = ""
    ts: float = 0.0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelCapabilities:
    """What an adapter can do (#40) — machine-readable so the platform can route /
    feature-gate. Defaults are conservative (text-out only, no inbound)."""

    inbound: bool = False  # can receive() messages
    threads: bool = False  # supports thread_id reply chains
    attachments: bool = False  # can send/receive files
    reactions: bool = False  # emoji reactions
    edits: bool = False  # can edit a sent message
    rich_text: bool = False  # markdown / blocks
    typing_indicator: bool = False
    max_text_len: int = 0  # 0 = unbounded
    #: The owner can pair this channel from its Configure page: the page shows a code and whoever
    #: sends it to the bot in a direct message becomes the channel's owner. Declare it only when
    #: both halves hold — the code is redeemed (a DM that is the code crosses the guarded door,
    #: ``services.deliver_channel_inbound``, where core redeems it; a channel whose messages carry
    #: it inside other text hands the code-shaped words to ``redeem_owner_pairing_code``), and the
    #: channel reads its owner with ``owner_id_for`` each time it needs it (a DM, an approval
    #: prompt), so a pairing reaches the running receiver at once instead of at its next start.
    owner_pairing: bool = False
    #: A direct message with this channel is ONE conversation: every message in it reaches core
    #: with the DM's channel id as its ``thread_id``, so core links a chat to the DM itself, not
    #: to a thread inside it. A chat handed off to such a channel continues in the DM; for one
    #: with threads in its DMs, it continues in the thread the handoff opened.
    dm_thread_is_channel: bool = False

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


class ChannelTransportProvider(ABC):
    """Provider interface for an external comms transport.

    The platform calls :meth:`send` to deliver outbound messages and
    :meth:`health` to surface connection state on the Channels page. Concrete
    transports declare their own credentials/lifecycle (e.g. Slack tokens from
    the ``slack-channel`` extension instance config).
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def connect(self) -> bool:
        """Initialize the connection to the external system. Returns success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> bool:
        """Send a message to the external channel. Returns success."""
        ...

    @property
    def connected(self) -> bool:
        return False

    def capabilities(self) -> ChannelCapabilities:
        """What this adapter can do (#40). Default: text-out only, no inbound.
        Adapters override to declare threads/attachments/reactions/edits/etc."""
        return ChannelCapabilities()

    def receive(self) -> AsyncIterator[ChannelMessage]:
        """Optional inbound seam (#40): yield normalized :class:`ChannelMessage`s.

        Default raises — most transports keep their existing inbound path (Slack's
        socket receiver, the WebUI chat runner). A new pull-based adapter overrides
        this to emit the canonical inbound shape."""
        raise NotImplementedError(f"{self.name} has no inbound receive loop")

    async def start_inbound(self, services: "Any") -> None:
        """Start this transport's inbound receiver, driving the platform runtime.

        Called by core AFTER its services are up — at gateway boot, and whenever this channel
        is enabled, installed, updated or rebuilt from saved settings — at most once per
        instance, and only while :meth:`health` says the channel is configured (any state but
        ``offline``). It is passed a :class:`~personalclaw.gateway_services.GatewayServices`
        handle (sessions, cron, channel history, dashboard state, config, owner). A transport
        that owns a push receiver (Slack Socket-Mode) connects here and routes inbound
        messages to chat sessions via ``services``. Raising, or not returning within a minute,
        is reported as the channel's health. Default: no inbound (the Web UI drives its own
        inbound through the dashboard chat runner)."""
        return None

    async def stop_inbound(self) -> None:
        """Stop the inbound receiver :meth:`start_inbound` started — all of it.

        Called when this instance stops being the channel that runs: it is disabled or
        uninstalled, it is replaced by a rebuilt or updated instance (which starts only after
        this returns), its health turns ``offline``, or the gateway shuts down. Nothing may keep
        receiving afterwards. Core drops the delivery handle registered under this channel's
        name itself."""
        return None

    async def health(self) -> dict[str, Any]:
        """Readiness probe for the management surface.

        Returns ``{state: "ready"|"offline"|"error", detail: str}``. The default
        derives state from :attr:`connected`; transports with a richer signal
        (credentials present, remote reachable) override this.
        """
        return {
            "state": "ready" if self.connected else "offline",
            "detail": "connected" if self.connected else "not connected",
        }

    async def test(self) -> dict[str, Any]:
        """Active probe triggered from the UI ("Test").

        Default: run :meth:`health`. Transports that can do a cheap round-trip
        (e.g. Slack ``auth.test``) override to prove credentials end-to-end.
        Returns ``{ok: bool, detail: str}``.
        """
        h = await self.health()
        return {"ok": h.get("state") == "ready", "detail": h.get("detail", "")}

    def owner_pairing_hint(self) -> str:
        """How the owner sends the pairing code on this channel, when it is not a direct message
        to the bot: one sentence the Configure page shows over the code, or ``""`` for the
        page's own ("Send this code to your bot in a direct message on …")."""
        return ""

    def validate_target(self, target: str) -> str:
        """Whether this channel can deliver to ``target``: ``""`` if it can, else one sentence why.

        ``target`` is a chat, channel or user id as this channel names it, which is what a
        schedule's "Notify channel" can send results to (``channel:<name>:<target>``). Core knows
        nothing about what an id looks like on any platform and must not, so it asks the channel.
        The sentence is shown to the owner as it is, so say what a valid id looks like.

        The default accepts any non-empty id with no whitespace or control characters, up to 256
        characters. A channel whose ids have a shape overrides it.
        """
        value = str(target or "")
        if not value:
            return f"{self.display_name} needs a chat or channel id to send to."
        if len(value) > _TARGET_MAX_LEN:
            return f"That's too long for a {self.display_name} id."
        if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value):
            return f"A {self.display_name} id has no spaces or control characters."
        return ""

    def info(self) -> dict[str, Any]:
        """Static descriptor (no awaiting) for quick listing."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "connected": self.connected,
            "capabilities": self.capabilities().to_dict(),
        }
