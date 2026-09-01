"""Reference channel transport (#41) — a complete, minimal adapter.

This is the worked example referenced by ``docs/ADDING_A_CHANNEL.md``: the smallest
transport that exercises the full normalized contract (#40) — outbound ``send``,
declared ``capabilities``, an inbound ``receive`` loop emitting :class:`ChannelMessage`,
plus ``connect``/``disconnect``/``health``. It "echoes" — outbound sends are recorded
and surfaced back as inbound messages — so it's runnable + testable with no external
system. Copy this file, rename, and swap the echo internals for your real client.

NOT registered by default (it's a teaching reference, not a live channel).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

logger = logging.getLogger(__name__)


class ReferenceEchoTransport(ChannelTransportProvider):
    """A self-contained reference transport. Outbound messages are echoed to the
    inbound queue, so a contributor can see the full round-trip without a real
    external system."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._connected = False
        self._inbound: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self.sent: list[OutboundMessage] = []  # exposed for tests/inspection
        self._services: Any = None  # bound at start_inbound; drives the trust seam

    # ── identity ──
    @property
    def name(self) -> str:
        return "reference-echo"

    @property
    def display_name(self) -> str:
        return "Reference (Echo)"

    # ── capability declaration (#40) ──
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            inbound=True,
            threads=True,
            rich_text=True,
            max_text_len=4000,
        )

    # ── lifecycle ──
    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── outbound ──
    async def send(self, message: OutboundMessage) -> bool:
        if not self._connected:
            return False
        self.sent.append(message)
        # Echo: surface the outbound text back as an inbound message.
        await self._inbound.put(
            ChannelMessage(
                channel_id=message.channel_id,
                text=f"echo: {message.text}",
                sender=self.name,
                thread_id=message.thread_id,
                ts=time.time(),
            )
        )
        return True

    # ── inbound (normalized) ──
    async def receive(self) -> AsyncIterator[ChannelMessage]:
        """Yield normalized inbound messages. A real adapter would translate its
        client's native events into :class:`ChannelMessage` here."""
        while self._connected:
            try:
                msg = await asyncio.wait_for(self._inbound.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            yield msg

    # ── inbound lifecycle (drives the trust seam) ──
    async def start_inbound(self, services: Any) -> None:
        """Bind the gateway services handle so the trust gate can raise owner
        notifications on the dashboard. A real transport would also start its receiver
        loop here; the echo transport is driven by tests/demo via ``handle_inbound``."""
        self._services = services

    async def stop_inbound(self) -> None:
        self._services = None

    async def handle_inbound(self, msg: ChannelMessage, *, is_dm: bool = True) -> "TrustDecision":
        """Hand one inbound :class:`ChannelMessage` to the platform's guarded door (EA-7).

        This is the reference wiring every channel app copies, and it is deliberately ONE
        call: ``services.deliver_channel_inbound(...)``. The transport does not decide
        whether to consult sender trust and *cannot forget to* — the door applies
        ``channel_trust.guard_inbound`` before the content can reach a session, redeems a
        pairing code when the message is one, fences non-owner group content, and routes the
        turn. This file used to hand-roll that sequence itself, which is precisely the
        "trust by convention" shape :mod:`personalclaw.channel_inbound` exists to end: a
        copy of it in every transport is a copy that can drift or be omitted.

        What stays transport-side is the OUTBOUND half, because rendering is channel-
        specific: a non-empty ``canned_reply`` on the verdict is text this transport
        delivers back to the sender in its own format.

        The return value reports what happened so a demo/test can inspect the full
        round-trip without a live external system."""
        if self._services is None:
            # Fail-CLOSED: no services handle means no guarded door, so nothing is
            # delivered. Never fall back to routing the message unchecked.
            return TrustDecision(allowed=False, reason="no_services")

        verdict = await self._services.deliver_channel_inbound(self.name, msg, is_dm=is_dm)
        if verdict.canned_reply:
            await self.send(OutboundMessage(channel_id=msg.channel_id, text=verdict.canned_reply))
        return TrustDecision(
            allowed=verdict.allowed,
            reason=verdict.reason,
            paired=bool(verdict.meta.get("paired")),
            notified=verdict.fired_notification,
            delivered_text=(verdict.fenced_text or msg.text) if verdict.allowed else "",
        )

    # Test/demo helper — inject an inbound message as if it arrived externally.
    async def _simulate_inbound(self, text: str, channel_id: str = "ref") -> None:
        await self._inbound.put(
            ChannelMessage(channel_id=channel_id, text=text, sender="user", ts=time.time())
        )


@dataclass
class TrustDecision:
    """What :meth:`ReferenceEchoTransport.handle_inbound` did with one message.

    A plain record so a demo/test can assert the full trust round-trip (did it become a
    turn? paired via a code? owner notified? what text entered the session) with no
    external system.

    ``allowed`` means **this message became an agent turn** — which is why a message that
    was itself a valid pairing code reports ``allowed=False, paired=True``: the sender is
    trusted from now on, but a pairing code is not a question for the agent to answer.
    """

    allowed: bool
    reason: str = ""
    paired: bool = False
    notified: bool = False
    delivered_text: str = ""
