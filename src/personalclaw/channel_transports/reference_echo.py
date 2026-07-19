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
            inbound=True, threads=True, rich_text=True, max_text_len=4000,
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
        await self._inbound.put(ChannelMessage(
            channel_id=message.channel_id, text=f"echo: {message.text}",
            sender=self.name, thread_id=message.thread_id, ts=time.time(),
        ))
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

    # Test/demo helper — inject an inbound message as if it arrived externally.
    async def _simulate_inbound(self, text: str, channel_id: str = "ref") -> None:
        await self._inbound.put(ChannelMessage(channel_id=channel_id, text=text, sender="user", ts=time.time()))
