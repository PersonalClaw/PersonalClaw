"""Channels (comms) API — transport-management surface.

A management surface over the registered :class:`ChannelTransportProvider`
instances (Web UI + Slack today; Telegram/Discord later): list/get with health,
connect/disconnect, and a "test" probe. Inbound routing lives elsewhere — a
channel app's receiver in its own bundle, Web UI in the chat runner.

All handlers delegate to a stateless :class:`ChannelManager` that reads the live
transport registry, so enabling/disabling the ``slack-channel`` extension is
immediately reflected here.
"""

import logging

from aiohttp import web

from personalclaw.channel_transports.manager import ChannelManager

logger = logging.getLogger(__name__)


def _mgr(request: web.Request) -> ChannelManager:
    return ChannelManager(state=request.app.get("state"))


async def api_channels_list(request: web.Request) -> web.Response:
    """GET /api/channels — all comms transports with info + health."""
    transports = await _mgr(request).list()
    return web.json_response({"channels": transports})


async def api_channel_get(request: web.Request) -> web.Response:
    """GET /api/channels/{name} — one transport's info + health."""
    entry = await _mgr(request).get(request.match_info["name"])
    if entry is None:
        return web.json_response({"error": "unknown transport"}, status=404)
    return web.json_response(entry)


async def api_channel_connect(request: web.Request) -> web.Response:
    """POST /api/channels/{name}/connect — bring the transport online."""
    res = await _mgr(request).connect(request.match_info["name"])
    status = 200 if res.get("ok") else 400
    return web.json_response(res, status=status)


async def api_channel_disconnect(request: web.Request) -> web.Response:
    """POST /api/channels/{name}/disconnect — take the transport offline."""
    res = await _mgr(request).disconnect(request.match_info["name"])
    status = 200 if res.get("ok") else 400
    return web.json_response(res, status=status)


async def api_channel_test(request: web.Request) -> web.Response:
    """POST /api/channels/{name}/test — active probe (e.g. Slack auth.test)."""
    res = await _mgr(request).test(request.match_info["name"])
    return web.json_response(res)
