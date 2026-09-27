"""Comms ChannelManager — management + visibility over registered transports.

This is the comms management surface (distinct from goal-loop orchestration). It does NOT route
inbound messages — a channel app's inbound receiver lives in its own bundle
(e.g. Slack Socket-Mode in ``slack-channel``), Web UI inbound stays in the chat
runner — and it does not start or stop receivers either: that is
:func:`~personalclaw.channel_transports.reconcile_inbound`, run on every registry change. Its job
is the management surface the Channels page drives: list transports with health,
connect/disconnect, and run a "test" probe. It sends nothing: text reaches a channel through the
delivery handle the channel registers (:mod:`personalclaw.channel_delivery`), which masks it.

It reads the live transport registry (``channel_transports`` module dict), which
is populated by the extension system (``ChannelTypeHandler`` registers Slack on
enable) plus the always-present in-app Web UI transport. A channel's health is
:func:`~personalclaw.channel_transports.channel_health`: its receiver's start while that is in
flight or has failed, otherwise the transport's own probe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.channel_transports import (
    WEBUI_TRANSPORT,
    _describe,
    app_of,
    channel_health,
    get_transport,
    list_transports,
    settled,
)

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
        """All registered transports with static info + their health.

        Waits for a pending reconciliation first, so a listing read right after an enable or a
        save reports the receiver that change started rather than the moment before it."""
        await settled()
        out: list[dict[str, Any]] = []
        for name in list_transports():
            t = self._resolve(name)
            if t is None:
                continue
            out.append(await self._entry(t))
        return out

    async def get(self, name: str) -> dict[str, Any] | None:
        await settled()
        t = self._resolve(name)
        if t is None:
            return None
        return await self._entry(t)

    async def _entry(self, t: Any) -> dict[str, Any]:
        """A channel's info, its health, the app it came from, and its owner.

        ``owner`` is the id core reaches the owner by on this channel (``reach_owner`` opens a DM
        with it) and which key it came from: ``channel`` (its own) or ``shared`` (the key every
        channel wrote before, which may hold another platform's id). ``{"id": ""}`` is the status
        saying nothing core sends the owner reaches anyone here. The Web UI has no owner to reach.
        """
        from personalclaw.config.credentials import owner_id_source

        entry = t.info()
        entry["health"] = await channel_health(t)
        entry["app"] = app_of(t.name)
        if t.name != WEBUI_TRANSPORT:
            owner, source = owner_id_source(t.name)
            entry["owner"] = {"id": owner, "source": source}
        return entry

    async def connect(self, name: str) -> dict[str, Any]:
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        ok = await t.connect()
        return {"ok": ok, "health": await channel_health(t)}

    async def disconnect(self, name: str) -> dict[str, Any]:
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        await t.disconnect()
        return {"ok": True, "health": await channel_health(t)}

    async def test(self, name: str) -> dict[str, Any]:
        """The transport's active probe — never green while its status on the page is not.

        A transport's ``test()`` agrees with its OWN ``health()``, which cannot know that core's
        start of its receiver is still running or has failed; that part of the status is core's,
        so core holds the Test answer to it too."""
        t = self._resolve(name)
        if t is None:
            return {"ok": False, "detail": "unknown transport"}
        try:
            result = await t.test()
        except Exception as e:  # said the way a failed receiver start is: credentials masked
            return {"ok": False, "detail": _describe(e)}
        health = await channel_health(t)
        if result.get("ok") and health.get("state") != "ready":
            probe = str(result.get("detail") or "").rstrip(".")
            status = str(health.get("detail") or health.get("state") or "")
            return {"ok": False, "detail": f"{probe}, but {status}" if probe else status}
        return result
