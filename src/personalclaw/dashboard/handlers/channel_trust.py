"""Channel sender-trust API — the owner's read/revoke surface over the allowlist (EA-7).

``channel_trust`` has shipped the store, the pairing codes and the CLI (``personalclaw pair
<provider>``) for some time, and an Allow/Deny action on the unknown-sender notification.
What it never had was a way to answer *"who can talk to my agent right now?"* — the
allowlist was writable from two places and readable from none. An access-control list you
cannot enumerate is one you cannot audit, and the notification that granted access is long
gone from the inbox by the time you want to review it.

Two routes, deliberately only two:

* ``GET /api/channels/trust`` — every provider the store knows, with its policy posture,
  its paired senders and its tracked channels. No secret is projected (see
  :func:`~personalclaw.channel_trust.provider_trust`).
* ``DELETE /api/channels/trust/{provider}/senders/{sender_id}`` — revoke one sender.

Revoke is deliberately NOT idempotent at this layer even though
:func:`~personalclaw.channel_trust.deny_sender` is: a request to revoke a sender who is not
on the list answers ``404 channel_trust_sender_unknown`` so the UI learns its list is stale
instead of reporting a successful revoke of something that was never there. Granting access
stays where it already is — the pairing code and the notification's Allow — because a
grant deserves a deliberate act, not a text field on a settings page.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote

from aiohttp import web

from personalclaw import channel_trust
from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)


async def api_channel_trust(request: web.Request) -> web.Response:
    """GET /api/channels/trust — the whole sender-trust posture, per provider."""
    providers = [channel_trust.provider_trust(p) for p in channel_trust.list_providers()]
    return web.json_response(
        {
            "providers": providers,
            "dm_policies": list(channel_trust.DM_POLICIES),
            "group_policies": list(channel_trust.GROUP_POLICIES),
            "default_dm_policy": channel_trust.DEFAULT_DM_POLICY,
            "default_group_policy": channel_trust.DEFAULT_GROUP_POLICY,
        }
    )


async def api_channel_trust_revoke(request: web.Request) -> web.Response:
    """DELETE /api/channels/trust/{provider}/senders/{sender_id} — revoke one sender.

    ``sender_id`` is percent-decoded because a provider's sender id is opaque to core and
    may legitimately contain characters (an email address, say) that must be escaped in a
    path segment.
    """
    provider = request.match_info["provider"]
    sender_id = unquote(request.match_info["sender_id"])

    if not channel_trust.is_allowed_sender(provider, sender_id):
        return json_error("channel_trust_sender_unknown", status=404)

    channel_trust.deny_sender(provider, sender_id)
    logger.info("channel trust: revoked sender on provider=%s", provider)
    return web.json_response({"ok": True, "provider": provider, "sender_id": sender_id})
