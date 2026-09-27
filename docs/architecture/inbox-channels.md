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
  **What is polled** is one list, `inbox_providers.source_catalog()`, re-read every
  tick: every source an app registered (`inbox_providers.registry`), then any
  `personalclaw.message_source_providers` entry point no app took the name of, each
  while its own `MessageSourceProvider.polling_enabled()` says so. An installed inbox
  app's always does (Mail Inbox, Slack: enabling the app is the owner's say-so). The
  built-in drop folder (`<home>/inbox/incoming/`) is registered by the native
  `filesystem-inbox` app, which is locked on, so its `polling_enabled()` reads
  `inbox.enabled` instead: any program on the machine can drop a file there. Before
  this, the gateway polled only the drop folder, so an installed Mail Inbox did
  nothing. Each source is polled on its own: one that raises
  keeps its checkpoints, its exception's message becomes its row in
  `/api/inbox/status` (the Inbox banner shows it), and the others are polled as if it
  were not there. A reply to a polled row goes to the source it came from
  (`api_inbox_send` → `send_reply`); only a reply the source sent closes the row, and
  one it did not send comes back as a 409 with its reason, the text kept as the draft.
  A sent reply stamps the row's `replied_at`, which is how the open item says "Sent".
  **A polled row's id** is `inbox_service.polled_item_id`: `{source}_{key}_{ts}`, the key
  the message's own id at its source (`IncomingMessage.id`: a Message-ID, a Slack ts)
  hashed with the source and channel, or its content when it has none. It was
  `{channel}_{ts}`, and two mails to one address in the same second shared it. Muting a
  row's thread writes `InboxItem.thread_key`: its thread id, or for the first message of
  a thread its own id at the source, which is what the replies name as their thread.
  **Watched channels.** Every source's `poll` is handed `inbox.watched_channels`; a source
  that reads it sets `watches_channels = True` (Slack's), and Settings → Inbox shows the
  list ("Channels to read"), named by those sources, while one is polled.
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

### One status transition

A row moves between states in exactly one place, `inbox.set_item_status(state, store, items,
status)`, and `InboxStore.update` refuses a `status`. A move is three writes: the row
(persisted once per batch); one `inbox_item_updated` frame per moved row, because the Inbox page,
Home and Mission Control read the Inbox on its frames rather than polling it; and, when a row
**closes** (leaves `OPEN_STATUSES`), the bell: every notification that is a view of the row
(`meta.inbox_item`, stamped by `emit_attention_item` and `notify_inbox_alert`) is marked read
through `DashboardState.ack_item_notifications`, one rewrite and one `notification_ack` frame for
the batch. Reading a row (PENDING → SEEN) is announced and leaves the bell alone. Every closer
goes through it: the Inbox routes (PUT, dismiss-all, send, proposal apply, restore, seen),
`resolve_attention_items` (approvals, workflow gates, loops, rooms), the inbox-op action
provider, and the skill, learning and session-organize proposal resolvers. The link used to run
one way, so a note handled in the Inbox (and every other closed row) left its notification
unread. A row raised by `emit_attention_item` is announced as `inbox_new_item` the same way.

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

## Pending approvals: one registry, every surface

A tool call waiting on a human decision — from a chat, a subagent, a workflow stage, or an MCP
server's elicitation — is **one entry** in `DashboardState._pending_approvals`
(`dashboard/approval_state.py`, the registry `DashboardState` mixes in), and every surface
reads that entry:

| Surface | Reads |
|---|---|
| Home's approvals count and **To triage**, Mission Control, the phone companion, the workflow run view, the desktop tray, the agent-activity feed | `GET /api/approvals` — the entries, verbatim |
| The chat card, the out-of-context nudge | the `approval` WS frame — the same entry |
| The phone push, the `ApprovalRequest` lifecycle hook | fired from the registration |
| The Inbox | an `agent_request` row raised through `emit_attention_item`, `refs = {approval: <registry id>, session}` |

The entry carries enough to act on: which chat (`session`, `session_title`), which agent
(`agent`), and what it wants to do (`tool`, redacted `tool_input`/`tool_purpose`, `risk`,
`is_read_only`). `_hold_approval` is the one registration and writes all of the above together;
there is no path by which a surface learns of an approval another does not list. A chat
approval used to broadcast its own frame and register nowhere else, so it reached only its own
chat.

Mission Control labels each card by the work that asked (`attentionLanes.approvalRaisedBy` and
`inboxRaisedBy`): an approval by its trigger, else by its session key's owner (a workflow's step,
a loop's worker, a trigger's session, an MCP server, a chat), and an Inbox row by the refs its
emitter stamped (`workflow`, `trigger_park`, `loop`, the control bridge). An Inbox row's sender
is its notification pair's source, which is `loop` for a workflow's gate, a trigger's question
and the control bridge's confirm alike, so it is shown only when the refs name no work.

A control-bridge action that needs confirming is answered in the Inbox: its row
(`refs.source: control_bridge`, `refs.confirmation`) names the action, what it was asked with and
the client that asked, and Approve runs it once while Deny drops it
(`POST /api/external-access/bridge/confirmations/{id}`). Only you answer it: the client that asked
cannot, and the bridge's own `/confirm` refuses every client.

**The registry id is not the chat's id.** A chat's `request_id` is unique only inside that chat —
an ACP agent's permission request carries the agent's JSON-RPC message id, counted from the same
small integers on every connection — so the registry keys a chat approval
`chat_approval_id(session, request_id)` and keeps the bare id as the entry's `request_id`, which
is what the chat's card, transcript and approve route go on using.

**One decision path.** `POST /api/chat/sessions/{key}/approve` (the card) and
`POST /api/approvals/{id}/{action}` (every surface outside the chat) both answer a chat's approval
through `decide_session_approval`: the same transcript record, the same SEL `tool_approval:<verb>`
row, and the same waiting runner — whose refusal handling (the rest of a refused batch is refused
rather than re-asked, so a Deny cannot be routed around with a second call) is therefore the same
for every door.

**Only you answer.** Each entry records who asked it (`asked_by`: the chat's agent, the app that
started the chat, a subagent, the run whose step asked, the trigger), and `resolve_approval` and
`decide_session_approval` take who is answering (`by`) and hold it to `approval_answer`: you, from
a signed-in session, or you on your paired channel, and never the party that asked. An app's token
answers nothing, not even to relay your answer. The menu-bar companion and the phone answer with
your own sign-in. An agent's tool is refused too. A refusal leaves the approval pending, writes an
`approval.answer_refused` row and answers `403 approval_owner_only`. A decision's
`approval_decision` row names who answered: `you`, or `channel:<provider>`.

**Every end goes through `withdraw_approval`.** An answer, an expiry, a torn-down turn: the entry
leaves the registry, its Inbox row is closed through `resolve_attention_items` on the live store,
and one `approval_resolved` frame (`id`, `request_id`, `session`, `approved`, `outcome`) tells
every open surface. `outcome` is how it ended (`approved`, `rejected`, `expired`, `cancelled`),
so a card can say "cancelled" for a stopped turn instead of reading it as a Deny. No approval
survives a restart, so `close_orphaned_approval_rows` closes, at boot, any row still asking for
one.

**Which chat channel asks.** An approval asks on a chat channel when the `approval/requested`
rule has the `channel_dm` target (`_ask_on_a_channel`, then a link when the channel has no
Approve/Deny), and a subagent's request to start always does (`GatewayOrchestrator._interactive_approval`).
Both resolve the order once, in `channel_delivery`. `approval_providers(origin)` puts the channel
the chat started on FIRST (`DashboardState.channel_provider_for`), and it asks in that chat, since the
person asking is there. After it comes the owner's **Send approvals to** (`agent.approval_channel`,
Settings → Notifications), which is all that decides for a turn with no channel origin (a chat in
PersonalClaw, an unattended run, a trigger): the chosen channel alone — and none while it is not
connected, so no other channel stands in and the approval waits in the dashboard — or, left empty,
every connected channel in name order. `approval_delivery(origin)` is the first of those that knows
the owner and can prompt. Before the setting, the order was the only rule, so with Discord paired
every approval asked on Discord, a Telegram chat's included. A press on the channel and an answer in
the dashboard resolve the same entry.

**How long it waits, and what a denial without an answer leaves.** Every approval that waits
waits one window, the owner's `agent.approval_timeout_minutes` (Settings → Agent defaults →
Approval wait; two hours by default, one minute to one week), read per approval by
`approval_window_secs`. An MCP server's question is cut shorter by its own call ceiling, and a
subagent's or workflow step's approval also ends with that work's time limit. Past the window
the call is denied: it fails closed. An unattended run (a trigger's session, a loop worker, a
channel delivery, a subagent) never waits at all. It declines a call that needs approval at
once, because nobody is there to ask: `chat_runner`'s fail-fast for a runtime that asks, and
the native runtime's own decline, which it marks on the tool result (`TOOL_META_AUTO_DENIED`)
for the chat runner or the subagent manager to see. Either way the call is **denied without an
answer**, and `auto_denials.py` leaves one `system/auto_denied` Inbox item for it.
The item says what was denied, who asked, when and why, and that the call did not run. Its refs
are `auto_denied` (`expired` | `unattended`), `tool` and `session`, plus `chat` when that is a
chat a person answers in, `trigger` when a trigger's action started the work, and, for an
expired one, `call` (a digest of the tool and its redacted input). The trigger travels with the
work: the store-trigger dispatch sets `ActionContext.trigger_id`, the `invoke-agent` and
`run-prompt` actions spawn their agent with it (`SubagentInfo.trigger_id`), and the gateway lists
that agent's approvals under it (`request_approval(trigger=…)`), so the ask and its note both
name the trigger. The chat's steps summary says the same thing as the note: an expired call's
step reads "(denied, no answer)", a stopped turn's "(cancelled)", and only a Deny reads
"(rejected)", in the transcript row and in the audit row's outcome.

The Inbox offers the one next step the call really has, by where it was asked
(`web/src/pages/inbox/DeniedCallRerun.tsx`), and only for an `expired` call, since that one
asked and can be asked again:

- a chat (`refs.chat`): **Ask it to try again** sends a stated message into that chat, so the
  approval is asked again while someone is there;
- a trigger's run (`refs.trigger`): **Run it again** is the trigger's Run now. A trigger that is
  not allowed to run its action (`needs_grant`) offers **Allow and run it again**, which goes
  through the same Allow consent as its page (#3702). One that is switched off and not allowed,
  or that waits for review, is left to its page, because allowing it would switch it on to run
  by itself again. A lifecycle hook is a trigger too (`refs.trigger` is `lifecycle:<id>`, which
  `hooks.run_script_hook` hands its action), but it fires on the agent's own events and has no
  Run now, so its note says when it runs again and opens it;
- a workflow step (`refs.session` is `workflow:<run>:<node>`): **Run this step again** is the run
  page's Re-run (`web/src/pages/workflows/reentry.ts`), while the run is live. A finished run
  never runs again, so its note says so.

An `unattended` note offers none of these. The run that declined it had nobody to ask and
would decline it the same way again, so the note says that instead. An expired note is handled
(`auto_denials.settle_retried`, from `withdraw_approval`) once the same call (`refs.call`) is
asked again where it was asked before, its session or its trigger's run, and someone answers it
either way, or once the same call runs again because a standing grant approved it without asking
(a chat's Trust, YOLO, the agent's own approval mode: `approval_state.settle_granted`). The answer
goes on `refs.retry` (`approved` | `rejected`) and who gave it on `refs.retry_by` (`you`, or the
grant's name from `approval_grants`), so a call a grant ran never reads as your Allow. The note
moves through `inbox.set_item_status`. Sending the retry does not close it, because nothing has
been decided yet.

There is one item per approval, and one per session and tool for the unattended case (per
trigger and tool when a trigger ran it, since each fire runs in a new session), so a run that
retries a declined call leaves one item. The approval's own row still closes. Before this, the
row closing was the whole record, so by morning an approval asked at night was on no surface.
There used to be a second, five-minute window for "unattended" sources, keyed by a substring of
`source`. No caller ever passed one, so it is gone.

**An ended owner ends its approvals.** Cancelling a workflow run stops its dispatched stages
(each subagent cancelled with the run's own reason), and a run that ends for any reason cancels
every approval still under `workflow:<run>:`; stopping a loop stops its worker turns; stopping a
chat turn (`SessionManager.stop_turn`, through the hook `DashboardState` registers) ends that
turn's approvals; cancelling a subagent ends its spawn and tool approvals, which are keyed
`subagent:<id>:<request_id>` so two subagents' requests can never share an id. Each ends as
`cancelled`, with an `approval_cancelled` SEL row. And the decision path checks again
(`approval_owner.owner_ended`, fail-closed): an approval whose run, loop or subagent is gone is
answered **409 `approval_owner_ended`** and nothing runs. On day 8, approving a cancelled run's
leftover spawn approval started the subagent.

**Announced once.** The Inbox row is the durable listing, not a second announcement: the chat
card announces an approval in context and the nudge everywhere else, so the SPA's notification
toast stands down for a note whose `refs` name an approval (it still lands in the bell), and
Home's inbox count and To triage recognise the row as the approval they already list
(`mirroredApprovalId`).

**No model's verdict can hide one.** `system/agent_request`, the kind the row rides, is a
*decision* (`NotificationKind.decision`): work is parked on the answer. So are
`loop/needs_input` (workflow gates, blocked loops, sign-in handoffs, control-bridge
confirmations), `agent/room_paused` and the `approval/requested` ping. A decision is never
`verifiable` — `notification_kinds.register` refuses the pair — so INU-6's second-opinion pass
never runs on one: the row is open and its notification fires the moment it is raised, with no
model call in front of either. `PUT /api/notifications/rules` refuses `verify` for a decision
and says why, and a `verify` stored against one reads as off. Until this, `system/agent_request`
was verifiable: with `verify: true`, every approval's registration waited on a model call made
on the gateway's loop, and a REFUTED filed the row as `filtered` and withheld its notification.

**A proposal's second opinion holds nothing up.** INU-6 still checks a proposal whose rule sets
`verify`, after its row is listed. `emit_attention_item` publishes the row with
`refs.verify: checking`, holds its one notification in `refs.verify_withheld`, and returns;
`notification_verify.verify_in_background` asks the model on a worker thread with its own event
loop and hands the verdict back to the caller's loop, where `inbox.apply_verdict` lands it. A
REFUTED claim files a row nobody has touched under Filtered, its notification still withheld
for Restore. A row the user opened or answered while the model thought stays where they put it:
the verdict is written on it, the row says so, and it is notified only if it is still open and
the claim was not refuted. When the gateway attaches its Inbox, `settle_verification_rows`
delivers a check a restart cut off (as `skipped`) and takes every decision row an earlier
verify filed as `filtered` back out of Filtered: restored, with its notification, if the
decision stands (an approval still in the registry, a folder not yet trusted, a one-tap hold),
handled if not. Before this the emit waited for the model from whatever thread raised the item,
so on the gateway's loop a one-second model stopped the gateway for a second.

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

A producer that must not lose a notice to quiet hours asks before it sends:
`quiet_hours_now()` is the gate's own window test, and the task due-date notice
(`tasks/due_notices.py`) uses it to WAIT — a `drop` posture inside the window
leaves the notice unsent and unrecorded, and the first sweep after the window
sends it. Mute and `min_severity` still mean "not at all" for it.

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
  ping to a registered device; `channel_dm` sends the note to the owner's DM on the first
  connected chat channel that reaches them (`channel_delivery.reach_owner`, from `notify()`),
  and on the `approval/requested` row it asks the approval on a channel — see *Which chat
  channel asks* below.
- **conditions** — keywords / name-mention that **escalate** a quieter mode to
  `immediate`. Escalation is capped at `immediate` and never adds targets the
  user didn't choose. Name-mention matches the **user's** name (Settings →
  Account → Your name), resolved once by `identity.operator_name()` for
  `notify()` and both inbox ingestion paths, never the assistant's. With no name
  given, including the `Operator` placeholder that skipping setup stores, it
  never matches.

Rules live in `entity_settings/notification_rules.json` with a guarded
`PUT /api/notifications/rules`; the matrix is Settings → Notifications →
Per-kind delivery. A rule's two lists change one entry per write —
`targets: {"add"|"remove": name}`, `conditions.keywords: {"add"|"remove": keyword}` —
applied to the rule as stored at that moment, and never as a whole list, so a save
from a matrix opened before another tab's edit (or before the phone turned push on,
`ensure_target`) cannot undo it. **Rules refine delivery for notifications that already
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
`notification_removed`. A note that is recorded without being fired (a `badge`, a foreign
addressee) sends a quiet `notification_logged` frame, so the bell and Home count it at once
without a toast; the bell, Home and the feed re-read on any `notification*` frame and poll only
as a once-a-minute safety net. An app reads back only what it raised (`raised_by_app`, which the proposal
door `POST /api/inbox/proposals` names through `emit_attention_item`, and which meta cannot
supply) and what is about a conversation it started: `GET /api/notifications` answers an app with those, and its socket gets
a `notification*` frame cut to them (`DashboardState.notification_reaches`). The raiser is named
by the producer, never read off the request: an app's request scope is copied into every task
the request starts, so a platform worker one of its requests happened to start would raise your
notes as the app's. A removal is announced before its note leaves the log, because an app's frame
is decided on the note. Notification metadata may carry a `channel_link` —
built via `ChannelDelivery.build_thread_link`, never by core string-formatting
a vendor URL.

## Channels: the two core seams

Core owns two protocols and **zero vendor code**:

### Inbound — `channel_transports/`

`base.py` defines `ChannelTransportProvider`; the package `__init__.py` is the
registry, and `manager.py` the Channels page's view of it. The gateway binds the
`GatewayServices` object (`gateway_services.py` — sessions, context builder,
conversation log, consolidator, cron service, subagent manager, channel history,
dashboard state, config, owner id) at boot, and from then on ONE rule,
`reconcile_inbound`, runs each transport's receiver: it is re-applied whenever a
transport registers or unregisters (install, enable, disable, uninstall, update, a
settings save) and after a Secrets-vault write, and keeps exactly one receiver
(`start_inbound(services)`) per registered channel whose `health()` is not
`offline`, on the instance registered now — stopping (`stop_inbound()`) a replaced,
removed or unconfigured one first, and dropping the delivery handle it registered.
A start runs as its own task, so a slow or failing channel holds up no other; the
Channels page reads `starting` while it runs and the reason when it failed. Two
implementations ship in-tree: `webui.py` (the dashboard itself as a transport, with
no receiver) and `reference_echo.py` (a minimal example).

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
