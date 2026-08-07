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
        """Run one inbound :class:`ChannelMessage` through the core trust seam (CE-1 V1).

        This is the reference wiring every channel app copies: BEFORE a message enters a
        session, call ``channel_trust.guard_inbound``. An unknown DM sender gets the canned
        pairing reply echoed back (and the owner is notified once); a paired sender's text —
        or a raw pairing code they send — flows on. A tracked-group message is fenced. The
        return value reports what happened so a demo/test can inspect the full round-trip
        without a live external system."""
        from personalclaw import channel_trust

        state = getattr(self._services, "dashboard_state", None)
        # A DM whose text IS an active pairing code redeems it and starts the conversation.
        if is_dm and not channel_trust.is_allowed_sender(self.name, msg.sender):
            candidate = (msg.text or "").strip()
            if candidate.isdigit() and channel_trust.redeem_pairing_code(
                self.name, msg.sender, candidate
            ):
                await self.send(
                    OutboundMessage(channel_id=msg.channel_id, text="Paired — you can talk now.")
                )
                return TrustDecision(allowed=True, paired=True)

        verdict = channel_trust.guard_inbound(
            state,
            self.name,
            msg.sender,
            channel_id=msg.channel_id,
            is_dm=is_dm,
            text=msg.text,
        )
        if not verdict.allowed:
            if verdict.canned_reply:
                await self.send(
                    OutboundMessage(channel_id=msg.channel_id, text=verdict.canned_reply)
                )
            return TrustDecision(
                allowed=False, reason=verdict.reason, notified=verdict.fired_notification
            )
        # Allowed: a tracked-group message is delivered as FENCED data, a DM as-is.
        text = verdict.fenced_text or msg.text
        return TrustDecision(allowed=True, delivered_text=text)

    # Test/demo helper — inject an inbound message as if it arrived externally.
    async def _simulate_inbound(self, text: str, channel_id: str = "ref") -> None:
        await self._inbound.put(
            ChannelMessage(channel_id=channel_id, text=text, sender="user", ts=time.time())
        )


@dataclass
class TrustDecision:
    """What :meth:`ReferenceEchoTransport.handle_inbound` did with one message.

    A plain record so a demo/test can assert the full trust round-trip (allowed? paired via
    a code? owner notified? what text would enter the session) with no external system."""

    allowed: bool
    reason: str = ""
    paired: bool = False
    notified: bool = False
    delivered_text: str = ""
