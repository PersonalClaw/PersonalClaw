"""ChannelDelivery — the outbound handle the gateway uses to deliver results.

The gateway delivers cron/heartbeat/subagent results and interactive approval
prompts to whatever channel a session came from. That delivery is
channel-specific (Slack renders mrkdwn + Block Kit ack buttons + threads), so the
rendering lives in the channel's own bundle, not core. Each channel transport registers its
handle at boot (``start_inbound``) and core calls these high-level methods with PLAIN text +
structured intent; the implementation renders channel-specifically.

When no channel is configured nothing is registered and the gateway delivers to
the dashboard only. This is the outbound half of the core↔channel seam
(:class:`~personalclaw.gateway_services.GatewayServices` is the inbound half).

**One handle PER PROVIDER — see the registry at the bottom of this module.** A single shared
handle was the shape until #959: with Discord, Slack and Telegram all connected, each
transport wrote the same slot, so the last registration won (apps load alphabetically, so
always Telegram) and every outbound reply went to that one provider carrying another
provider's channel id.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ChannelDelivery(Protocol):
    """Outbound delivery a channel provides to the gateway. All text is PLAIN
    markdown — the implementation renders it to the channel's format."""

    async def open_dm(self, user_id: str) -> str:
        """Open (or resolve) a DM channel with a user; return its channel id."""
        ...

    async def deliver_text(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
        *,
        unfurl_links: "bool | None" = None,
        unfurl_media: "bool | None" = None,
        reply_broadcast: "bool | None" = None,
    ) -> str:
        """Post plain-markdown text to a channel/thread; return the message ts. The
        optional link-preview / broadcast hints are generic messaging concepts a
        channel applies if it supports them (ignored otherwise)."""
        ...

    async def deliver_rich(
        self,
        channel: str,
        payload: "object",
        fallback_text: str,
        *,
        thread_ts: str = "",
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        reply_broadcast: bool = False,
    ) -> str:
        """Deliver a caller-supplied structured/rich payload (e.g. Block Kit) with a
        plain-text fallback; return the message ts. The payload is opaque to core —
        callers pass channel-shaped structures through; a channel that can't render
        rich content falls back to ``fallback_text``."""
        ...

    async def deliver_cron_result(
        self, channel: str, job_name: str, job_id: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a cron job result with the channel's ack affordance; return the
        parent message ts (for threading follow-ups)."""
        ...

    async def deliver_notification(
        self, channel: str, title: str, text: str, thread_ts: str = ""
    ) -> str:
        """Deliver a titled notification (heartbeat/subagent) to a channel/thread."""
        ...

    async def deliver_chat_mirror(self, channel: str, text: str, thread_ts: str = "") -> None:
        """Mirror a dashboard chat reply to a linked channel thread, rendering any
        trailing ``[OPTIONS: …]`` block as the channel's interactive affordance."""
        ...

    async def deliver_subagent_reply(
        self, channel: str, text: str, thread_ts: str = "", elapsed_secs: float = 0.0
    ) -> None:
        """Deliver a subagent's synthesized reply to a channel/thread, with the
        channel's timing affordance (a footer showing how long the run took)."""
        ...

    # ── Owner / channel resolution (provider-agnostic identity lookups) ──
    async def resolve_user_name(self, user_id: str) -> str:
        """Human-readable display name for a channel user id (best-effort; returns
        the id or empty on failure). Used by inbox sender-name resolution."""
        ...

    async def resolve_user_profile(self, user_id: str) -> "dict":
        """Full profile dict for a channel user (name/real_name/title/etc.); ``{}`` on
        failure. Shape is provider-defined — callers read known keys defensively."""
        ...

    async def channel_info(self, channel_id: str) -> "dict":
        """Metadata for a channel (e.g. ``{"name": ..., "is_im": ...}``); ``{}`` on
        failure. Provider-agnostic shape — callers read known keys defensively."""
        ...

    def list_reply_channels(self) -> "list[dict]":
        """Channels this delivery can post replies into, as ``{"id", "name"}`` dicts
        (the channel app's own config decides — tracked/active channels). Used by the
        dashboard's channel picker for link/handoff. May be empty."""
        ...

    def is_tracked_channel(self, channel_id: str) -> bool:
        """Whether *channel_id* is in this channel's outbound allowlist (the app's
        own tracked-channel config). Core consults this for targeted sends."""
        ...

    def build_thread_link(self, channel: str, ts: str) -> str:
        """Deep link to a message/thread on this channel provider (e.g. a
        jump-to-source URL for notifications). Returns "" when the provider has
        no linkable surface. Core never constructs vendor URLs itself — the
        provider owns its own link format."""
        ...

    # ── Attachment + streaming primitives (the surface core used to reach via the
    # raw client). All channel-specific rendering stays in the implementation. ──
    async def upload_attachment(
        self,
        channel: str,
        file_path: str,
        *,
        filename: str = "",
        thread_ts: str = "",
        title: str = "",
        initial_comment: str = "",
    ) -> str:
        """Upload a file to a channel/thread; return the delivered message ts (or "")."""
        ...

    async def start_stream(self, channel: str, thread_ts: str = "", initial_text: str = "") -> str:
        """Begin a live-updating stream message (for tool/progress animation); return
        its ts, or "" if the channel has no streaming affordance."""
        ...

    async def append_stream_task(
        self,
        channel: str,
        stream_ts: str,
        task_id: str,
        title: str,
        status: str,
    ) -> None:
        """Append/update a progress item on an in-flight stream started by
        start_stream. ``status`` is a generic progress state ("in_progress" /
        "complete"). Channels without task-animation may no-op."""
        ...

    async def stop_stream(self, channel: str, stream_ts: str) -> None:
        """Finalize a stream started by start_stream."""
        ...

    async def request_approval(
        self,
        event: "object",
        *,
        source: str,
        parent_session_key: str = "",
        sessions: "object | None" = None,
        on_prompted: "Callable[[object], None] | None" = None,
    ) -> "bool | None":
        """Prompt the owner to approve a tool call on this channel.

        Returns ``True`` (approved) / ``False`` (rejected), or ``None`` if the
        channel can't prompt (no owner/channel) so the gateway falls back to the
        dashboard. Implementations own the channel-specific approval UI + the wait
        for the owner's response, and should coordinate with the dashboard via the
        ``on_prompted`` hook (invoked with the pending record) when provided by the
        caller. ``sessions`` is the live SessionManager for cross-surface reconcile.

        **The approval brief (additive meta).** ``event.tool_meta`` carries the core-
        composed brief under
        :data:`~personalclaw.approval_brief.APPROVAL_BRIEF_META_KEY`, so a channel can
        tell the owner what the call would TOUCH, not just what it is called::

            {"tool": str,              # tool identity, same value as event.title
             "risk": str,              # EFFECTIVE per-invocation risk (not the
                                       #   DECLARED event.risk_level)
             "blastRadius": {"writes": bool, "network": bool,
                             "shell": bool, "readOnly": bool},   # optional
             "blastRadiusLine": str}                             # optional

        Reading it is OPTIONAL and purely additive: the method's arguments are
        unchanged, no existing field or ``tool_meta`` key is replaced, and a channel
        that ignores the key prompts exactly as it did before. Two rules for a
        renderer:

        * ``blastRadius``/``blastRadiusLine`` are ABSENT when nothing could be
          established — show no blast-radius line at all, rather than "nothing
          established", which reads as "nothing happens";
        * every facet is a POSITIVE claim, so render only the ``True`` ones. Never
          enumerate all four with on/off states: a ``False`` means "not established",
          and painting it as "no network" turns absence of evidence into an all-clear.

        The brief is a compact summary for a surface with no room — the dashboard
        remains the rich approval surface, and rendering logic stays in the channel's
        own bundle."""
        ...


# ── the registry: one handle per provider ────────────────────────────────────────────────
#
# Process-level, like `inbox_providers.native_source`'s dashboard-state hook and for the same
# reason: the WRITERS are channel transports reaching core through `GatewayServices`, while the
# READERS are both the gateway (owner notifications, cron results, subagent replies) and the
# dashboard (the chat mirror, the channel-link picker). Two objects held two separate slots for
# one fact before this — `GatewayOrchestrator._channel_delivery` and
# `DashboardState.channel_delivery` — and every shipped transport wrote BOTH, which is how one
# overwrite could take out delivery on two unrelated paths at once.
#
# A dict rather than a list: the routing key is the provider, because a channel id means nothing
# without one. `deliver_text("C123", …)` is answerable only by the provider that issued `C123`.

_REGISTRY: dict[str, "ChannelDelivery"] = {}


def provider_of(delivery: Any) -> str:
    """The provider name for a delivery that did not declare one.

    The protocol has 18 methods and no provider member, so core cannot ask a handle what it is.
    This derives a STABLE, DISTINCT key from the implementation's own module — `discord_runtime`
    → `discord` — which is all the registry needs: two different apps must not collide. It is a
    namespacing fallback, never a semantic guess.

    A transport SHOULD pass `provider=` explicitly (it already passes exactly that string to
    :meth:`GatewayServices.deliver_channel_inbound` on the way in, at the same lifecycle point),
    and then this is not consulted at all. The fallback exists so a core upgrade cannot break an
    app that has not been updated yet: three un-updated apps still land in three distinct keys.
    """
    module = getattr(type(delivery), "__module__", "") or ""
    root = module.split(".")[0]
    for suffix in ("_runtime", "_channel", "_delivery"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root or type(delivery).__name__.lower()


def register(delivery: "ChannelDelivery | None", provider: str = "") -> str:
    """Register (or with ``None``, clear) a provider's outbound handle. Returns the key used.

    ``register(None)`` with no provider clears EVERY handle — the "shutting down, nothing is
    reachable" case. ``register(None, provider="slack")`` clears just that one.
    """
    if delivery is None:
        if provider:
            _REGISTRY.pop(provider, None)
            return provider
        _REGISTRY.clear()
        return ""
    key = provider or provider_of(delivery)
    previous = _REGISTRY.get(key)
    _REGISTRY[key] = delivery
    if previous is not None and previous is not delivery:
        # Same provider re-registering (a reconnect) is normal and quiet at debug. What must
        # never happen silently again is two DIFFERENT providers sharing a key, which is why the
        # log names the key: if a derivation ever collides, this line is the evidence.
        logger.debug("channel delivery re-registered for %s", key)
    return key


def delivery_for(provider: str) -> "ChannelDelivery | None":
    """The handle for one provider, or None when that channel is not connected.

    **The only correct resolver for a reply to an incoming message.** A reply carries the origin
    channel's id, so it is deliverable by exactly one provider; returning a different one is not
    a degraded delivery but a misdirected one. Callers must treat None as "do not send" — never
    as "send via whatever is available".
    """
    return _REGISTRY.get(provider) if provider else None


def owner_reachable() -> "ChannelDelivery | None":
    """Any connected channel that can reach the owner, or None.

    The second resolution policy, and deliberately a different question from
    :func:`delivery_for`: a cron result, a heartbeat summary or a subagent reply is addressed to
    the OWNER, not to a thread — those sites all call `open_dm(owner_id)` and have no origin
    channel to honour. Sorted so the pick is deterministic rather than dict-insertion-ordered,
    which would make the same home behave differently across restarts depending on app load
    order — the property that made #959 hard to see.
    """
    for key in sorted(_REGISTRY):
        return _REGISTRY[key]
    return None


def registered_providers() -> list[str]:
    """The connected providers, sorted. For diagnostics and tests."""
    return sorted(_REGISTRY)
