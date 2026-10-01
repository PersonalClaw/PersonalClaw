"""A channel's owner, on its Configure page: who core reaches you as there, and pairing.

The owner id is what core DMs your heartbeat and cron results, subagent replies, approval prompts
and a chat's handoff to on a channel (``channel_delivery.reach_owner``). It was written only by
``personalclaw setup``, so a channel set up in the UI had none and everything core sent you there
reached nobody. Three routes close that:

* ``GET /api/channels/{name}/owner`` — the owner id core uses on that channel, which key it came
  from, and the name the channel's trust list knows the owner by (``owner_name``, the owner's own
  entry only); whether the channel can pair from here and how the code is sent there
  (``pairing_hint``, when not a DM to the bot), and the pairing's state. Never a code.
* ``POST /api/channels/{name}/owner/pairing`` — mint the code, returned ONCE in this response. The
  owner sends it to the bot in a direct message; the channel's inbound crosses the guarded door,
  where :func:`~personalclaw.channel_trust.guard_inbound` stores the sender's id under the
  channel's own owner key. The page polls the GET until the pairing ends.
* ``DELETE /api/channels/{name}/owner/pairing`` — cancel it.

Pairing is offered only by a channel that declares ``ChannelCapabilities.owner_pairing``: one whose
DMs cross the door and which reads its owner each time it needs it. For any other, a code would be
something nothing redeems, so the POST refuses rather than minting one.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from personalclaw import channel_trust
from personalclaw.channel_transports import WEBUI_TRANSPORT, get_transport
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)


def _channel(request: web.Request) -> Any:
    """The registered chat channel the route names, or None (the Web UI has no owner to pair)."""
    name = request.match_info.get("name", "")
    if not name or name == WEBUI_TRANSPORT:
        return None
    return get_transport(name)


def _pairing_supported(transport: Any) -> bool:
    try:
        return bool(transport.capabilities().owner_pairing)
    except Exception:  # noqa: BLE001 - a broken capabilities() declares nothing
        logger.debug("channel %s: capabilities() failed", transport.name, exc_info=True)
        return False


def _pairing_hint(transport: Any) -> str:
    """How the owner sends the code on this channel, when the channel says (``""``: a DM)."""
    try:
        return str(transport.owner_pairing_hint() or "")
    except Exception:  # noqa: BLE001 - a broken hint leaves the page's own sentence
        logger.debug("channel %s: owner_pairing_hint() failed", transport.name, exc_info=True)
        return ""


def _unknown() -> web.Response:
    return json_error("channel_unknown", status=404)


async def api_channel_owner(request: web.Request) -> web.Response:
    """GET /api/channels/{name}/owner — the channel's owner and its pairing state."""
    transport = _channel(request)
    if transport is None:
        return _unknown()
    owner = channel_trust.owner_ref(transport.name)
    return web.json_response(
        {
            "channel": transport.name,
            "display_name": transport.display_name,
            "owner_id": owner["id"],
            "owner_name": owner["name"],
            "source": owner["source"],
            "pairing_supported": _pairing_supported(transport),
            "pairing_hint": _pairing_hint(transport),
            "pairing": channel_trust.owner_pairing_status(transport.name),
        }
    )


async def api_channel_owner_pairing_start(request: web.Request) -> web.Response:
    """POST /api/channels/{name}/owner/pairing — mint the owner's code and return it once."""
    transport = _channel(request)
    if transport is None:
        return _unknown()
    if not _pairing_supported(transport):
        return json_error(
            "channel_pairing_unsupported",
            message=f"{transport.display_name} cannot pair its owner from the dashboard.",
            status=409,
        )
    code = channel_trust.create_owner_pairing_code(transport.name)
    status = channel_trust.owner_pairing_status(transport.name)
    logger.info("channel %s: owner pairing started", transport.name)
    # The code is a credential for the next ten minutes: nothing may cache the response.
    return web.json_response(
        {
            "code": code,
            "expires_at": status["expires_at"],
            "ttl_secs": channel_trust.PAIRING_CODE_TTL_SECS,
            "pairing": status,
        },
        headers={"Cache-Control": "no-store"},
    )


async def api_channel_owner_pairing_cancel(request: web.Request) -> web.Response:
    """DELETE /api/channels/{name}/owner/pairing — cancel the outstanding owner code."""
    transport = _channel(request)
    if transport is None:
        return _unknown()
    cancelled = channel_trust.cancel_owner_pairing(transport.name)
    return web.json_response(
        {
            "ok": True,
            "cancelled": cancelled,
            "pairing": channel_trust.owner_pairing_status(transport.name),
        }
    )
