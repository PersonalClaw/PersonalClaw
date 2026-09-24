"""Comms ChannelManager — management + visibility over registered transports.

This is the comms management surface (distinct from goal-loop orchestration). It does NOT route
inbound messages — a channel app's inbound receiver lives in its own bundle
(e.g. Slack Socket-Mode in ``slack-channel``), Web UI inbound stays in the chat
runner. Its job is the management surface the Channels page
drives: list transports with health, connect/disconnect, and run a "test"
probe. Outbound ``send`` is delegated straight to the named transport.

It reads the live transport registry (``channel_transports`` module dict), which
is populated by the extension system (``ChannelTypeHandler`` registers Slack on
enable) plus the always-present in-app Web UI transport.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.channel_transports import get_transport, list_transports
from personalclaw.channel_transports.base import OutboundMessage

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


class ChannelManager:
    """Read-through manager over the registered comms transports.

    ``state`` is the live ``DashboardState`` (from ``request.app["state"]``);
    it is bound onto transports that need it (e.g. the Web UI transport) before
    each probe, so transports never reach for a non-existent module global.
    """

    def __init__(self, state: "DashboardState | None" = None) -> None:
        self._state = state

    def _resolve(self, name: str):
        t = get_transport(name)
        if t is not None and hasattr(t, "bind_state"):
            t.bind_state(self._state)  # type: ignore[attr-defined]
        return t

    async def list(self) -> list[dict[str, Any]]:
        """All registered transports with static info + a health probe."""
        out: list[dict[str, Any]] = []
        for name in list_transports():
            t = self._resolve(name)
            if t is None:
                continue
            entry = t.info()
            try:
                entry["health"] = await t.health()
            except Exception as e:  # a transport's probe must never break the list
                entry["health"] = {"state": "error", "detail": str(e)[:200]}
            out.append(entry)
        return out

    async def get(self, name: str) -> dict[str, Any] | None:
        t = self._resolve(name)
        if t is None:
            return None
        entry = t.info()
        try:
            entry["health"] = await t.health()
        except Exception as e:
            entry["health"] = {"state": "error", "detail": str(e)[:200]}
        return entry

    async def connect(self, name: str) -> dict[str, Any]:
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        ok = await t.connect()
        return {"ok": ok, "health": await t.health()}

    async def disconnect(self, name: str) -> dict[str, Any]:
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        await t.disconnect()
        return {"ok": True, "health": await t.health()}

    async def test(self, name: str) -> dict[str, Any]:
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        try:
            return await t.test()
        except Exception as e:
            return {"ok": False, "detail": str(e)[:200]}

    async def send(self, name: str, message: OutboundMessage) -> bool:
        t = self._resolve(name)
        if t is None:
            return False
        return await t.send(message)
