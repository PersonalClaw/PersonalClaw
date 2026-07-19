# Inbox & Channels

Two seams connect PersonalClaw to the outside conversational world: the
**inbox** (things arriving for the user's attention) and **channels**
(bidirectional messaging surfaces like Slack — implemented entirely by apps
against core protocols). Paths are relative to
`PersonalClaw/src/personalclaw/`.

## Inbox

- **`inbox.py`** — the item store. **`inbox_service.py`** — the service loop:
  polls sources every 60 seconds, ingests with dedup and mute/dismiss filters
  (muted threads are dropped at ingestion), and evaluates **alerts at
  ingestion time** for both push and poll paths (`evaluate_alert` →
  `notify_inbox_alert`), so an alerting item notifies immediately rather than
  on the next page view. Maintenance (retention cleanup) runs every 6 hours
  (`_MAINTENANCE_EVERY_SECS`).
- **AI drafts** write on behalf of the operator (the `dashboard.user_name`
  identity), not the bot.
- **Sources** — `inbox_providers/` ships native push + filesystem sources;
  the seam is entry-point discoverable (`provider_registry.py`) so apps *may*
  contribute sources, but none do today by decision: channels are channel
  providers, not inbox sources.
- **Settings** live solely in
  `~/.personalclaw/entity_settings/inbox.json` (`alert_keywords`,
  `alert_on_name_mention`, `auto_cleanup_enabled`, `retention_days`) with
  type- and range-guarded PUTs in `providers/entity_routes.py`. Name-mention
  matching treats each name part as a whole word.

## Notifications

`DashboardState.notify()` (`dashboard/state.py`) is the **single choke point**
for user-facing notifications. `notification_allowed()`
(`providers/entity_routes.py`) gates it:

- severity rank map with `min_severity`;
- midnight-wrapping quiet hours (severity-3 bypasses);
- `mute_all`;
- suppressed means **dropped entirely** (not queued); a gate failure fails
  open (a broken settings file must not silence the system).

Preferences persist in `entity_settings/notifications.json` with enum/HH:MM
domain-guarded PUTs. Unread counts are *derived* from unacked log entries;
deletes broadcast `notification_removed`. Notification metadata may carry a
`channel_link` — built via `ChannelDelivery.build_thread_link`, never by core
string-formatting a vendor URL.

## Channels: the two core seams

Core owns two protocols and **zero vendor code**:

### Inbound — `channel_transports/`

`base.py` defines `ChannelTransportProvider`; `manager.py` is the registry.
The gateway iterates `list_transports()` and calls each transport's
`start_inbound(services)` with the `GatewayServices` object
(`gateway_services.py` — sessions, context builder, conversation log,
consolidator, cron service, subagent manager, channel history, dashboard
state, config, owner id). Two implementations ship in-tree: `webui.py` (the
dashboard itself as a transport) and `reference_echo.py` (a minimal example).

### Outbound — `channel_delivery.py`

The `ChannelDelivery` protocol: `open_dm`, `deliver_text`, `deliver_rich`,
`deliver_cron_result`, `deliver_notification`, `deliver_chat_mirror`,
`deliver_subagent_reply`, `resolve_user_name`, `resolve_user_profile`,
`channel_info`, `list_reply_channels`, `is_tracked_channel`,
`build_thread_link`, `upload_attachment`, and streaming primitives
(`start_stream` / `append_stream_task` / `stop_stream`), plus
`request_approval`. Everything the gateway sends outward flows through the
registered implementation.

### Vendor-blind grammar

The delivery vocabulary names no vendor anywhere in core:

- background results route via `deliver="channel[:<chan>:<ts>]"`;
- the `notify` MCP tool's session enum is `["origin", "channel"]`
  (`mcp_core.py`);
- `/api/send-message` responds `{"ok", "channel", "session"}`;
- Security Event Log labels use `downstream_service="channel"`;
- chat-history rows carry `origin="channel"` (see
  [chat-sessions.md](chat-sessions.md)).

## The reference channel app: `apps/slack-channel`

`apps/slack-channel/slack_runtime/` (14 modules) is the full worked example of
a channel provider:

- `transport.py` — implements `start_inbound`;
- `runtime.py` — a facade proxying `GatewayServices`;
- `delivery.py` — `SlackDelivery`, the `ChannelDelivery` implementation
  (including the vendor deep link behind `build_thread_link`);
- `events.py` / `handler.py` / `interactions.py` — inbound event routing;
- `blocks.py` / `format.py` / `files.py` — vendor message formats;
- `allowlist.py` / `enterprise.py` — access control;
- `settings.py` — the app-owned `SlackSettings` store with a loud, retry-safe
  `migrate_from_core()` (all channel config lives app-side; core's config
  loader defines no channel dataclasses).

Why it's shaped this way — and which small Slack-named constants deliberately
remain in core — is covered in [provider-boundary.md](provider-boundary.md).

## Channel-thread ↔ session linking

- The persistent map is core: `session_map.py` `set_channel_link` /
  `get_channel_link` (generic `thread_ts`/`channel_id` keys). Channel apps go
  through these calls; they never touch the map file.
- Dashboard-side link/handoff routes are `dashboard/chat_channel.py`
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`) — provider-blind, `ChannelDelivery` only.
- `sync_bridge.py` hands a dashboard conversation off to a channel thread
  (`handoff_to_channel`); `voice_reply.py` uploads TTS voice replies
  (`upload_voice_to_channel`).
- `channel_history.py` keeps a rolling per-channel message window
  (`observe_max_messages` / `observe_ttl_hours` — generic top-level config
  keys).

## Related docs

- Session model & memory modes on channel threads:
  [chat-sessions.md](chat-sessions.md)
- The boundary judgments behind the channel split:
  [provider-boundary.md](provider-boundary.md)
- How a channel app is installed and sandboxed:
  [app-platform.md](app-platform.md)
