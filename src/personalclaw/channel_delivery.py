"""ChannelDelivery — the outbound handle the gateway uses to deliver results.

The gateway delivers cron/heartbeat/subagent results and interactive approval
prompts to whatever channel a session came from. That delivery is
channel-specific (Slack renders mrkdwn + Block Kit ack buttons + threads), so the
rendering lives in the channel's own bundle, not core. The gateway holds an
optional :class:`ChannelDelivery` handle — registered by the active channel
transport at boot (``start_inbound``) — and calls these high-level methods with
PLAIN text + structured intent; the implementation renders channel-specifically.

When no channel is configured the handle is ``None`` and the gateway delivers to
the dashboard only. This is the outbound half of the core↔channel seam
(:class:`~personalclaw.gateway_services.GatewayServices` is the inbound half).
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


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
        caller. ``sessions`` is the live SessionManager for cross-surface reconcile."""
        ...
