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
  on the next page view. **Maintenance** — retention cleanup, dismissed-set
  pruning, and the feedback retire-candidate check — is no longer a second
  cadence in this loop: it is the remediation engine's
  `inbox.maintenance` job, driven by a measured `inbox_maintenance_backlog`
  deficit off the live store (`resilience/remediation.py`). `run_maintenance`
  is the implementation the engine drives, bounced onto the loop that owns the
  store via `run_maintenance_threadsafe` so nothing mutates it off-thread.
- **AI drafts** write on behalf of the operator (the `dashboard.user_name`
  identity), not the bot.
- **Sources** — `inbox_providers/` ships native push + filesystem sources;
  the seam is entry-point discoverable (`provider_registry.py`) and apps
  contribute their own. A **channel app is expected to register one**: the
  vendor-completeness pattern makes the channel transport and the inbox message
  source two seams of one bundle, so messages arriving while no session is live
  still reach the Inbox. See
  [build-a-channel-app.md](../guides/build-a-channel-app.md) for the checklist.
- **Settings** live solely in
  `~/.personalclaw/entity_settings/inbox.json` (`auto_cleanup_enabled`,
  `retention_days`) with type- and range-guarded PUTs in
  `providers/entity_routes.py`. **Alerting is no longer here:** the former
  `alert_keywords`/`alert_on_name_mention` fields were retired and generalized
  into per-kind rule `conditions` (below), so the same keyword / name-mention
  escalation now applies to loop requests and proposals, not just messages.
  `inbox.evaluate_alert()` reads the `inbox/alert` rule's conditions. This store is
  the one entity-settings file that fails **closed**: `auto_cleanup_enabled`'s
  default *runs a delete*, so a file that cannot be parsed suppresses cleanup and
  logs why, rather than resolving to the 90-day default and deleting items a stored
  `false`/`3650` said to keep. An **absent** file is unaffected and still means
  "first run, use the defaults" — see `load_inbox_settings()`.

## The shared inbox (multi-owner attribution)

An inbox item carries `owner_username` and `origin_harness` — the **same two fields, same
names, same defaults** as `WorkflowRun` (`workflows/models.py`), reused rather than
re-invented. They are stamped at one seam, `InboxStore.add`, from
`identity.current_username()` and `durability.shards.machine_id`; a value already set is
preserved (that is how a source hands over a teammate's item) and `load()` never re-stamps,
so re-reading the store cannot silently re-attribute history.

**Two predicates, because "mine" and "theirs" are different questions.**

| Predicate | Question | Unattributed item |
|---|---|---|
| `InboxItem.belongs_to(owner)` | "does this count as MINE?" — the counters and `?mine=1` | counts as the owner's |
| `InboxItem.authored_by(user)` | "show me only *that* owner's items" — `?owner=` | matches nobody |

Using `belongs_to` for per-owner filtering would put every unattributed row under every
owner's filter; using `authored_by` for the counter would stop counting the owner's own
pre-attribution items. `inbox.owner_view()` is the one implementation of the counter scope,
so the counter and the filter cannot diverge — the F3 failure mode in
[shared-store-provider-conformance.md](shared-store-provider-conformance.md).

**The listing shows everything; the counters do not.** `GET /api/inbox` returns items from
every owner (hiding foreign rows would orphan any surface deep-linking to one). Only
`my_pending_count` / `my_total_count` on `GET /api/inbox/status` are owner-scoped;
`pending_count` / `total_count` remain the shared totals. `GET /api/inbox/owners` is the
census that drives the filter chips, built from what is in the store rather than from a
list of known users, so a chip never appears with nothing behind it.

**Foreign content is fenced AND labelled, never trusted as owner intent.**
`fence_message_for_prompt` wraps every item's external text in `<untrusted_content>`
(`security.fence_untrusted`) — that was already true for all items — and now additionally
carries `identity.contributor_label()`, the one `" (from <handle>)"` form shared with
semantic memory. Fencing says "this is data"; the label says "and it is not yours". The UI
mirrors both: a foreign row renders inside a labelled quote block rather than as the owner's
own text.

**Attribution is not client-writable.** `owner_username` / `origin_harness` are deliberately
absent from `_UPDATABLE_FIELD_TYPES` (pinned equal to the HTTP allowlist
`handlers_inbox._UPDATABLE_FIELDS`), so `PUT /api/inbox/{id}` cannot re-attribute a
teammate's item to the owner and launder foreign content into owner intent.

### Known limitation: the item store is last-writer-wins

`InboxStore.save()` serialises its whole in-memory `items` dict over `inbox.json`. Two
processes each holding an `InboxStore` will therefore lose the earlier writer's new items:
whoever saves last wins, and the loss is silent. This is the F4 failure mode
([shared-store-provider-conformance.md](shared-store-provider-conformance.md) clause 3) —
declared here rather than papered over, which is what that clause requires of a
last-writer-wins store. It is not a problem for the shipped single-gateway topology (one
process owns the store, and `InboxService` bounces mutations onto the loop that owns it),
and it is exactly what a future multi-writer shared inbox must fix — with a
sibling-preserving read-modify-write — before it can claim a merge-safe semantic.

## Notifications

`DashboardState.notify()` (`dashboard/state.py`) is the **single choke point**
for user-facing notifications. Two layers of policy apply, in this order.

**1. The global gate** — `notification_posture()`
(`providers/entity_routes.py`), outermost, with `notification_allowed()` as its boolean
form:

- severity comes from the **registry** (`notification_kinds`), not a local table — one
  declaration per kind, so what the rules matrix SHOWS as a row's severity is what filters
  it at delivery;
- `min_severity` compares against that rank;
- midnight-wrapping quiet hours (severity-3 bypasses);
- `mute_all`;
- suppressed means **dropped entirely** (not queued); a gate failure fails
  open (a broken settings file must not silence the system).
- **one gradation**: inside quiet hours an `attention=True` kind returns `quiet` rather than
  `drop`, and `notify()` records it as a `badge` — persisted, counted, auditable, silent. A
  loop that needed an answer overnight used to leave no trace in the notification log at
  all, while its durable inbox row still counted toward the badge.

Preferences persist in `entity_settings/notifications.json` with enum/HH:MM
domain-guarded PUTs.

**2. The per-kind rule** — `notification_rules.py`. Every notification is a
registered `(source, kind)` pair (`notification_kinds.py`); each pair resolves
to a rule:

- **mode** — `never` (drop), `badge` (persist without a toast), `immediate`
  (persist, broadcast, and raise a toast in the SPA — `lib/notificationToasts.ts`), `digest`
  (batch into `digest_queue.jsonl` for the scheduled summary);
- **targets** — `dashboard` today; `native` raises a real OS notification whenever the
  desktop shell reports the capability; `push` sends a content-free `{kind, item_id}`
  ping to a registered device; `channel_dm` is the one target still accepted and
  persisted but **inert** — nothing in `notify()` consumes it, and the matrix dims it
  accordingly. (`ChannelDelivery.deliver_notification` exists and is live, but the heartbeat
  path in `gateway.py` calls it directly; it is not wired to this target.)
- **conditions** — keywords / name-mention that **escalate** a quieter mode to
  `immediate`. Escalation is capped at `immediate` and never adds targets the
  user didn't choose. Name-mention matches the **user's** name (Settings →
  Account → Your name), resolved once by `identity.operator_name()` for
  `notify()` and both inbox ingestion paths, never the assistant's. With no name
  given, including the `Operator` placeholder that skipping setup stores, it
  never matches.

Rules live in `entity_settings/notification_rules.json` with a guarded
`PUT /api/notifications/rules`; the matrix is Settings → Notifications →
Per-kind delivery. **Rules refine delivery for notifications that already
passed the gate — they can never resurrect a suppressed one**, so `mute_all`
still means mute. Every failure path (missing file, malformed JSON, unknown
mode/target) falls back to the registry default, which is `immediate`: a policy
layer that cannot read its own config must not be able to silence the system.
An unregistered pair resolves to `system/generic` with a warning rather than
raising.

**3. The addressee** — `notification_addressing.py`. The first two layers answer
*should this be delivered* and *how loudly*; neither could answer *to whom*, so
every note went to **this** dashboard by construction. Once a shared store
contributes rows somebody else owns — a workflow run
(`workflows/models.py:1021`, a `TEXT NOT NULL` column at `workflows/store.py:141`) and an
inbox item (`inbox.py:355`) each carry an `owner_username` — that is wrong: a teammate's
inbox item wanting attention fired a toast at whoever was sitting here.

- a note's `addressee` is the same `owner_username` slug everything else
  carries — **not** a second owner vocabulary. `inbox.emit_attention_item`
  supplies the item's own owner, because the notification is a *view* of the
  item;
- **foreign-addressed is visible-but-not-fired**, the shipped foreign-*trigger*
  posture applied to the attention path. `triggers/ownership.py` withholds a
  foreign row from the ARM read (`triggers/provider.py::armable`) while the
  LISTING read keeps it; here `_append_notification` is the listing (the bell
  and `GET /api/notifications` still show the row, marked
  `withheld_reason: foreign_addressee`) and the fire half — WS broadcast,
  `native`, `push`, the digest — is what the addressee gates;
- empty addressee, or no configured username, reads as the owner's, so an
  install with no shared source behaves exactly as before;
- **delivery is pluggable**: a foreign-addressed note is offered to registered
  `type=notification` providers (`notification_providers/`, published as
  `sdk/notification.py`), and the accepting backend's name is recorded in
  `routed_to`. With none installed `routed_to` is `""` — "nobody could reach
  them" must never read as "delivered".

The decision sits after `never` and before the three delivery modes: `digest`
and `badge` are local deliveries too, and a foreign note in the morning digest
is a foreign note fired one day late.

Unread counts are *derived* from unacked log entries; deletes broadcast
`notification_removed`. Notification metadata may carry a `channel_link` —
built via `ChannelDelivery.build_thread_link`, never by core string-formatting
a vendor URL.

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

`apps/slack-channel/slack_runtime/` is the full worked example of a channel
provider. The modules that carry the contract:

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

- Building a new channel (the must/should/may obligation tables, trust and
  pairing, the conformance kit, vendor completeness):
  [build-a-channel-app.md](../guides/build-a-channel-app.md)
- Session model & memory modes on channel threads:
  [chat-sessions.md](chat-sessions.md)
- The boundary judgments behind the channel split:
  [provider-boundary.md](provider-boundary.md)
- How a channel app is installed and sandboxed:
  [app-platform.md](app-platform.md)
