"""Channel sender-trust API — the owner's surface over who may talk to the agent (EA-7).

``channel_trust`` holds the store, the pairing codes and the gate every inbound message
crosses. This is where the owner sees and changes it, for every chat channel alike:

* ``GET /api/channels/trust`` — every chat channel that is set up, plus any the store still
  knows, each with its display name, its policies, its paired senders, its tracked groups and
  the untracked groups that messaged the agent. No secret is projected (see
  :func:`~personalclaw.channel_trust.provider_trust`).
* ``PUT /api/channels/trust/{provider}/policies`` — ``{dm?, group?}``. Opening DMs to anyone
  loosens a security setting, so it answers ``400 confirmation_required`` until the request
  carries ``"confirm": true`` (the SPA asks with :func:`consent_required`'s sentence).
* ``POST /api/channels/trust/{provider}/channels`` — track a group (``{channel_id, name?}``);
  ``DELETE …/channels/{channel_id}`` stops tracking it.
* ``POST /api/channels/trust/{provider}/pairing`` — mint a sender's pairing code, returned ONCE
  in that answer; ``DELETE`` cancels it.
* ``DELETE /api/channels/trust/{provider}/senders/{sender_id}`` — revoke one sender.

Granting access is still a deliberate act, never a text field: a sender is let in by the code
they send or by the owner's Allow on the unknown-sender notification, and the page offers Track
only for a group that already messaged the agent. The whole subtree is owner-only
(``OWNER_ONLY_API_PATHS``).

Revoke and untrack are deliberately NOT idempotent at this layer even though the store's calls
are: removing something that is not there answers 404, so the UI learns its list is stale
instead of reporting a successful change to nothing.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote

from aiohttp import web

from personalclaw import channel_trust
from personalclaw.http_errors import consent_required, json_error
from personalclaw.request_validation import json_object_body
from personalclaw.safety_flags import confirm_granted

logger = logging.getLogger(__name__)

#: A group id is the vendor's, opaque to core; bounded so a request cannot store a novel.
_CHANNEL_ID_MAX = 256
_CHANNEL_NAME_MAX = 200


def _chat_channels() -> dict[str, str]:
    """Every registered chat channel (the dashboard itself is not one), key → display name."""
    from personalclaw.channel_transports import WEBUI_TRANSPORT, get_transport, list_transports

    names: dict[str, str] = {}
    for name in list_transports():
        transport = get_transport(name) if name != WEBUI_TRANSPORT else None
        if transport is not None:
            names[name] = str(getattr(transport, "display_name", "") or name)
    return names


def _entry(provider: str, channels: dict[str, str]) -> dict[str, Any]:
    return {
        **channel_trust.provider_trust(provider),
        "display_name": channels.get(provider) or provider,
        "registered": provider in channels,
    }


def _provider(request: web.Request) -> str:
    """The route's provider when it is a chat channel that is set up, else ""."""
    provider = request.match_info.get("provider", "")
    return provider if provider in _chat_channels() else ""


def _unknown() -> web.Response:
    return json_error("channel_trust_provider_unknown", status=404)


async def api_channel_trust(request: web.Request) -> web.Response:
    """GET /api/channels/trust — the whole sender-trust posture, per chat channel."""
    channels = _chat_channels()
    providers = sorted(set(channels) | set(channel_trust.list_providers()))
    return web.json_response(
        {
            "providers": [_entry(p, channels) for p in providers],
            "dm_policies": list(channel_trust.DM_POLICIES),
            "group_policies": list(channel_trust.GROUP_POLICIES),
            "default_dm_policy": channel_trust.DEFAULT_DM_POLICY,
            "default_group_policy": channel_trust.DEFAULT_GROUP_POLICY,
            "pairing_code_ttl_secs": channel_trust.PAIRING_CODE_TTL_SECS,
        }
    )


async def api_channel_trust_policies(request: web.Request) -> web.Response:
    """PUT /api/channels/trust/{provider}/policies — set the DM and/or group policy."""
    provider = _provider(request)
    if not provider:
        return _unknown()
    body = await json_object_body(request)
    dm, group = body.get("dm"), body.get("group")
    if dm is None and group is None:
        return json_error("invalid_request", message="Send dm, group, or both.", status=400)
    for kind, value, allowed in (
        ("dm", dm, channel_trust.DM_POLICIES),
        ("group", group, channel_trust.GROUP_POLICIES),
    ):
        if value is not None and value not in allowed:
            return json_error(
                "invalid_request",
                message=f"{kind} must be one of: {', '.join(allowed)}.",
                status=400,
            )
    before = channel_trust.trust_policies(provider)
    if dm == "open" and before.get("dm") != "open" and not confirm_granted(body):
        where = channel_trust.channel_display_name(provider)
        return consent_required(
            "dm",
            f"anyone who messages your bot on {where} can talk to your agent, and it reads "
            "what they write as your own instructions",
            title=f"Let anyone message your agent on {where}?",
        )
    policies = channel_trust.set_trust_policies(provider, dm=dm, group=group)
    logger.info("channel trust: policies for provider=%s now %s", provider, policies)
    return web.json_response({"ok": True, "provider": provider, "policies": policies})


async def api_channel_trust_track(request: web.Request) -> web.Response:
    """POST /api/channels/trust/{provider}/channels — track one group."""
    provider = _provider(request)
    if not provider:
        return _unknown()
    body = await json_object_body(request)
    channel_id = str(body.get("channel_id", "") or "").strip()
    name = str(body.get("name", "") or "").strip()
    if not channel_id or len(channel_id) > _CHANNEL_ID_MAX or any(c.isspace() for c in channel_id):
        return json_error(
            "invalid_request", message="channel_id must be the group's id.", status=400
        )
    channel_trust.track(provider, channel_id, name[:_CHANNEL_NAME_MAX])
    logger.info("channel trust: tracked a group on provider=%s", provider)
    return web.json_response({"ok": True, "provider": provider, "channel_id": channel_id})


async def api_channel_trust_untrack(request: web.Request) -> web.Response:
    """DELETE /api/channels/trust/{provider}/channels/{channel_id} — stop tracking one group."""
    provider = request.match_info["provider"]
    channel_id = unquote(request.match_info["channel_id"])
    if not channel_trust.is_tracked_channel(provider, channel_id):
        return json_error("channel_trust_channel_unknown", status=404)
    channel_trust.untrack(provider, channel_id)
    logger.info("channel trust: stopped tracking a group on provider=%s", provider)
    return web.json_response({"ok": True, "provider": provider, "channel_id": channel_id})


async def api_channel_trust_pairing_start(request: web.Request) -> web.Response:
    """POST /api/channels/trust/{provider}/pairing — mint a sender's code and return it once."""
    provider = _provider(request)
    if not provider:
        return _unknown()
    code = channel_trust.create_pairing_code(provider)
    status = channel_trust.provider_trust(provider)
    logger.info("channel trust: sender pairing code minted for provider=%s", provider)
    # The code is a credential for the next ten minutes: nothing may cache this answer.
    return web.json_response(
        {
            "code": code,
            "expires_at": status["pairing_expires_at"],
            "ttl_secs": channel_trust.PAIRING_CODE_TTL_SECS,
        },
        headers={"Cache-Control": "no-store"},
    )


async def api_channel_trust_pairing_cancel(request: web.Request) -> web.Response:
    """DELETE /api/channels/trust/{provider}/pairing — cancel the outstanding sender code."""
    provider = request.match_info["provider"]
    cancelled = channel_trust.cancel_pairing_code(provider)
    return web.json_response({"ok": True, "provider": provider, "cancelled": cancelled})


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
