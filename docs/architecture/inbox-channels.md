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
- **Sorting** (`inbox_sorting.py`) — a message arrives unsorted (`classification` `""`, shown
  as "Not sorted yet"), and the service's `InboxSorter` gives it its verdict in the background:
  woken by every ingest, a held message from someone new and every poll tick, it waits a moment
  for the rest of a burst, then sends the open unsorted messages to the background model in
  batches of at most `BATCH_MAX`, one call per batch, each message fenced on its own, through
  the prompt bound for `inbox_classify` (`task-inbox-classify`). Each verdict lands with the
  prompt that made it (`classified_by`); a message the answer left out, an answer that could
  not be read, or a failed call puts the reason on the row (`classify_error`) instead of a
  verdict, and `POST /api/inbox/{id}/sort` sends it again. A message is sent once: never again
  after it has a verdict or a failure, and an answer lands only on a row still unsorted, so a
  verdict she gave while the model read it stands. Nothing is sent while `inbox.sort_messages`
  is off (Settings → Inbox → Sort new messages) or incident mode is on, and a call the daily
  spend ceiling refuses, or that no background model can take (none is set up, or the bound one
  is resting after failing), leaves its messages waiting; each is a hold whose sentence is
  `health.sorting.held` in `/api/inbox/status`, and the next tick tries again. Only messages
  are sorted: a row that is not one (a notice, a proposal, a note) carries no verdict, and an
  agent's own post (`post_to_inbox`) keeps the kind the agent gave it and is never read, since
  its text may come from a Temporary or Incognito chat.
- **Every judgment names its maker.** `inbox.judgment_producers` says which prompt produced a
  row's verdict (`classified_by`) and draft (`drafted_by`, written by Generate draft and cleared
  when she saves her own text); her own verdict (`confidence` `user`) and a draft she wrote name
  none. The thumbs read it (`redact_item`'s `feedback_producers`), and `POST /api/feedback` for
  `inbox_classification` / `inbox_draft` / `inbox_digest` records the producer it names, whatever
  the body claims, or refuses with `409 feedback_no_judgment`. A row stored before this carried
  a stamped verdict nobody made; reading it clears that (`_drop_unmade_verdict`), once.
- **AI drafts** write on behalf of the operator (the `dashboard.user_name`
  identity), not the bot. The reply panel's "What should the reply say?" field is the
  owner's instruction for one draft: `POST /api/inbox/{id}/draft {"instructions"}` (text of
  at most `DRAFT_INSTRUCTIONS_MAX_CHARS`; longer is refused with `instructions_too_long`
  before the model runs) hands it to the model after the `inbox_draft` prompt, outside the
  fence around the sender's text, so it is followed whatever prompt is bound for drafting.
  A draft is one model call with no tools, so what it stands on is read first
  (`reply_grounding.ground`), and only her words choose it: each file they name by its file
  name (a note the knowledge library's watched folders took in, at that path or ending with
  it, else a file the agent's file tools read in the workspace), or, naming none, the
  library's best matches for her words. Each note goes to the model fenced as data, after the
  sender's text. A file the message names is never read. A named file that cannot be read
  (nothing by that name, more than one note matching it, no text) stops the draft before the
  model runs: `422 draft_source_unread` names each file and why, and nothing is written, since
  a draft written around it would guess or promise it on her behalf. Every draft is written
  under the same rules, whatever prompt is bound: say for her only what she said or her notes
  say, leave each question nothing of hers answers to her, marked `[your answer: …]` where its
  answer goes (`reply_answers.placeholder`), and when nothing else could be written, answer
  `ASK:` with one question, which the panel shows while writing nothing. A model can ignore its
  rules, so a written draft is checked (`reply_answers.check`): one more background call, given
  the message, her words, the notes and the reply in numbered parts, each fenced as data, names
  each part that answers for her with what none of them say, and each becomes a placeholder
  unless its words are plainly her words' or her notes'. The check never discards a draft; one
  that cannot be made is said (`drafting.unchecked`) and the draft is kept as written. The panel
  lists what is left for her, read from the draft as she edits, and `POST /api/inbox/send`
  refuses a reply that still holds a placeholder (`422 reply_has_open_answer`, naming each),
  since the sender would get the mark. A word limit in her words ("120 words max") is asked for
  over the whole reply, an over-long draft is asked for once more within it (never cut), and the
  panel counts the draft against it; the shortened draft is the one checked. The route answers
  `{"item", "drafting"}`; the row's `context_summary` says what the draft stood on, and the SEL
  row names the notes the model was given.
- **Agents read it** with `inbox_list` (`agents/native/inbox_tool_defs.py`): the open items
  (`OPEN_STATUSES`), newest first, with what each is, who raised it, when, and its text, the
  sender and text fenced as untrusted data. It declares itself a read, so a read-only automation
  (a scheduled briefing) may call it, and it marks nothing seen. `post_to_inbox` is the write.
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
  **A message's files** are its `files` (`IncomingMessage.files`, `ChannelMessage.files`:
  `personalclaw.attachments.Attachment`, the name, type and bytes as the message gave
  them), never text in its body. Core keeps them with the row (`attachments.keep`, under
  `attachments/<row id>/`, each file `0600`, at most `MAX_COUNT` files of `MAX_BYTES` each;
  one past either is listed with why it was not kept) and removes them with the row. The
  row lists each one by name, type and size (`redact_item`), and
  `GET /api/inbox/{id}/attachments/{aid}` serves it as a file to save
  (`application/octet-stream`, `nosniff`, a sandboxing CSP): never by the type the sender
  declared, so nothing a message carries renders in the dashboard. A draft is told what the
  message came with inside the message's fence; Investigate and an inbox trigger's run get
  each attachment's text (`attachments.reading`), each in a fence of its own, with an image
  named and not read. A linked channel's message from someone trusted carries its files as
  the turn's attached files (`attachments.keep_for_chat`, under `uploads/`); a fenced
  sender's do not ride into the turn.
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

### The item store keeps what another writer wrote

`inbox.json` has more writers than the running service: a sync bringing another machine's items
in, a restore's merge or an import bringing items back, and an `InboxStore` opened elsewhere in
the process. So `InboxStore.save()` is a read-modify-write under the file's lock
(`record_files.written`): it writes the items it holds with, on top, every item another writer
added, changed or removed since the store last read or wrote the file — unless the store changed
that same item itself, in which case its own change stands. `InboxStore.refresh()` takes those
items in (`record_files.taken_in`), and the inbox API calls it before it reads, so an item a sync
brought shows up without a restart. The earlier writer's new items are no longer lost to whoever
saves last; two writers editing one item still resolve it last-writer-wins for that item alone,
the F4 case [shared-store-provider-conformance.md](shared-store-provider-conformance.md) clause 3
asks a store to declare.

Two homes compare an item without where each is with it (`inbox.item_what_it_is`): its status,
triage, draft, sent time and star are each machine's, so reading or answering one on both is
never an edit to review.

## Pending approvals: one registry, every surface

A tool call waiting on a human decision — from a chat, a subagent, a workflow stage, a room
member, or an MCP server's elicitation — is **one entry** in `DashboardState._pending_approvals`
(`dashboard/approval_state.py`, the registry `DashboardState` mixes in), and every surface
reads that entry:

| Surface | Reads |
|---|---|
| Home's approvals count and **To triage**, Mission Control, the phone companion, the workflow run view, a loop's cockpit, a room's view, the desktop tray, the agent-activity feed | `GET /api/approvals` — the entries, verbatim |
| The chat card, the out-of-context nudge | the `approval` WS frame — the same entry |
| The phone push, the `ApprovalRequest` lifecycle hook | fired from the registration |
| The Inbox, and that row's notification (the bell, Notifications) | an `agent_request` row raised through `emit_attention_item`, `refs = {approval: <registry id>, session}`; its notification carries the same refs |

The entry carries enough to act on: which chat (`session`, `session_title`), which agent
(`agent`), and what it wants to do (`tool`, redacted `tool_input`/`tool_purpose`, `risk`,
`is_read_only`: whether the call is established as a read, true or false, from
`task_modes.reads_only`, and `blast_radius`: what the call can touch, from the same reading
(`approval_brief.call_blast_radius`), which every surface shows as it is). `_hold_approval` is the one registration and writes all of the above
together; there is no path by which a surface learns of an approval another does not list. A chat
approval used to broadcast its own frame and register nowhere else, so it reached only its own
chat.

A subagent's start asks the same way a tool call does (`subagent_ask.spawn_ask`): `tool` is
`subagent_run`, `tool_purpose` says what allowing it does ("Starts a subagent on this task."),
`tool_input` is the whole redacted task, and `risk` is `caution`. The Inbox row shows each line
cut at a word (`textfmt.clip_words`), and the decision under it (`ApprovalDecision`) shows the
whole input from the live entry whenever the row could not. The `subagent_run` call itself asks
nobody (`approval_grants.WORK_ASKS`): what it starts asks. A batch asks once for its start, for
all its tasks (`workflows.batch_start`), and its row names it as the batch it is ("A batch of 2
subagent tasks from “Retry ceiling” is waiting…"): `tool_input` names each task and what it may
change. One whose tasks only read asks through the start's relay, so a Trust or YOLO switch
answers it as it answers a start; one whose tasks may change things is answered only by a decision
on it, so such a switch, which answers every pending approval it covers, leaves it asking
(`request_approval(answered_alone=True)`). A batch an app's agent asked for is the app's work
(`apps.app_work`): it never starts on a grant of yours, its ask and each of its tasks' asks name the
app and its scheduled job ("A batch of 3 subagent tasks from the app “Research Lab”'s scheduled job
“advance-campaigns”", "The “…” step of a batch from the app “Research Lab”…"), and so does an ask of
a subagent its agent started ("A subagent of the app “…”"): the entry carries those words
(`whose_work`, `approval_source.whose_work`). In the chat that started the work, the card for an ask
a subagent or a batch raised answers through the approvals queue, with Allow or Deny for that ask
alone (`ApprovalSegment.queued`): the chat's own approve route holds only the chat's own asks.
Such an ask outlives the turn that started the work, so the turn keeps its card out of the work it
folds once it has answered, until the ask is answered (`approvalSegment.waitsPastItsTurn`).

The entry also says where the call came from, in words (`source_label`, from
`approval_source.approval_source_label`): `chat “Trip planning”`, `loop “Fix the README”`,
`workflow “deep-research” · step “sweep”` (the step by its label), `trigger “Friday digest”`,
`subagent of chat “…”`, `batch of chat “…”` (a batch's ask, before any of its tasks exists),
`batch of the app “…” · step “…”` and `subagent of the app “…”` (an app's work, with its scheduled
job when one started it), `MCP server “…”`, `room “…” · member “…”`. Every surface that answers an
approval renders the one approval card
(`pages/chat/ApprovalCard`;
`app/PendingApprovalCard` for the queue surfaces: the workflow run view, Mission Control, Home's To
triage, the Inbox row and its notification): the tool and its risk, what it can touch, its whole
input a click away (Show all), and, away from the work that asked, "From <source_label>", a link
to that work on a surface that lists approvals from everywhere. A channel's prompt is tagged with
the same words (`ChannelDelivery.request_approval`'s `source`), for a chat's approval asked on a
channel and for a background one the gateway asks alike; a loop's worker asks on the chat path,
and its prompt names the loop. The approval's Inbox row keeps the words in `refs.source_label`.

A room member's call asks the same way (`rooms.posture.registry_approver`, bound by the round):
its `session` is the member's own key (`room:<room>:<member>`), its `source` is `room`, and its
Inbox row reads "talk-editor in the room “…” is waiting for your decision on …". The room's view
lists and answers its members' asks (`RoomApprovals`), and the nudge, the Inbox row and a queue
card link to the room (`approvalDestination`). Only a decision on it answers it
(`answered_alone`): a Trust or YOLO switch leaves it asking, because a room's approval posture is
`ask` whatever the rest of the gateway's is. Archiving the room or removing the member ends its
ask as cancelled ("the room that asked for it was archived"), and an answer that arrives anyway is
refused (`dashboard/approval_owner.py`). With no registry to ask through, the call is refused and
the room says so; an ask nobody answered is refused for that, never as a Deny.

Mission Control labels each card by the work that asked (`attentionLanes.inboxRaisedBy`): an
approval by its `source_label`, and an Inbox row by the refs its emitter stamped (`workflow`,
`trigger_park`, `loop`, the control bridge). An Inbox row's sender
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

**Every surface that announces an approval answers it.** The Inbox row and its notification offer
Approve and Deny in place (`web/src/app/ApprovalDecision.tsx`, through
`POST /api/approvals/{id}/{action}`), as To triage, the workflow run view and the phone do. They read
the entry live from `GET /api/approvals`, because a row and its notification outlive the approval
they name: once it has ended they say so and offer neither. A trigger's run asks with no chat behind
it, so these and To triage are where its approval is answered.

**Only you answer.** Each entry records who asked it (`asked_by`: the chat's agent, the app that
started the chat, a subagent, the run whose step asked, the trigger), and `resolve_approval` and
`decide_session_approval` take who is answering (`by`) and hold it to `approval_answer`: you, from
a signed-in session, or you on your paired channel, and never the party that asked. An app's token
answers nothing, not even to relay your answer. The menu-bar companion and the phone answer with
your own sign-in. An agent's tool is refused too. A refusal leaves the approval pending, writes an
`approval.answer_refused` row and answers `403 approval_owner_only`. A decision's
`approval_decision` row names who answered: `you`, or `channel:<provider>`.

**Every end goes through `withdraw_approval`.** An answer, an expiry, a torn-down turn: the entry
leaves the registry, its Inbox row is closed on the live store, and one `approval_resolved` frame
(`id`, `request_id`, `session`, `approved`, `outcome`) tells every open surface. `outcome` is how it
ended (`approved`, `rejected`, `expired`, `cancelled`), so a card can say "cancelled" for a stopped
turn instead of reading it as a Deny. Only an answer makes the row `handled`
(`resolve_attention_items`). One that ended with nobody answering is `expired`
(`expire_attention_items`), and why rides on the row and the frame (`ended`): "nobody answered within
2 hours", "the loop that started its run was stopped", "the gateway stopped before anyone
answered" — the Inbox's `ApprovalDecision` says it in place of a decision. No approval survives a
restart (its answer is awaited in memory), so a gateway that stops ends each one it holds that way,
and `close_orphaned_approval_rows` expires, at boot, any row a killed process left asking for one
("the gateway restarted before anyone answered"). However it closes, the row's text stops asking
(`approval_state.settled_row_text`): "Approval needed: bash … is waiting for your decision on bash"
reads "Denied: bash … asked for your decision on bash. It was denied.", from the same four outcomes
("It was approved.", "It expired: nobody answered within 2 hours.", "It was cancelled: its turn was
stopped.").

**Which chat channel asks.** A chat that started on a chat channel is asked in that chat, whatever
the `approval/requested` rule says (`_asking_channels`, the channel from
`DashboardState.channel_provider_for`): the person asking is there, and its prompt is that chat's
approval card, which PersonalClaw shows for a chat of its own under any rule too. It used to wait for
the rule's `channel_dm` target, which the default rule does not have, so a chat started on Telegram
asked nobody on Telegram. The rule's `channel_dm` target (off under `never`) adds the owner's **Send
approvals to** (`agent.approval_channel`, Settings → Notifications): it asks an approval with no
channel origin (a chat in PersonalClaw, an unattended run, a trigger), and is tried after a chat's
own channel that cannot ask. Without the target no other channel stands in, and the approval waits in
the dashboard. A subagent's request to start always asks (`GatewayOrchestrator._interactive_approval`).
Both askers resolve the order in `channel_delivery`: `approval_providers(origin)` puts the chat's own
channel first, then the chosen channel alone (none while it is not connected, so no other channel
stands in) or, left empty, every connected channel in name order, and `approval_delivery(origin)` is
the first of those that knows the owner and can prompt. A channel that cannot prompt is sent a link
to answer it instead. A press on the channel and an answer in the dashboard resolve the same entry,
and however the approval ends, the channel's prompt is told how (`approved`, `rejected`, `expired`,
`cancelled`), so it says so and takes its buttons off (`ChannelDelivery.request_approval`).

**What a channel's prompt offers.** The answers the chat's card offers, decided by core and handed
over in the brief (`DashboardApprovalState.channel_answers`, the vocabulary in
`channel_delivery`): Allow once and Deny, and Allow for this chat, the card's "This chat", when the
prompt is asked in the chat that is asking (the chat's own channel, in that chat), the call may not
destroy anything (`task_modes.MAY_DESTROY`, where the card withholds its standing answers too) and
the operator ceiling lets a chat's Trust stand. Everywhere else, like Home and the Inbox, it answers
the call alone. A press of Allow for this chat decides through `decide_session_approval` with
`trust`, exactly as the card's does: that chat is trusted, its header shows it, its next calls run
without asking, and no other chat changes (`answer_on_channel`). An answer the prompt did not offer
decides nothing. The card's "This agent" stays the card's: it saves a setting that outlives the
chat.

**How long it waits, and what a denial without an answer leaves.** Every approval that waits
waits one window, the owner's `agent.approval_timeout_minutes` (Settings → Agent defaults →
Approval wait; two hours by default, one minute to one week), read per approval by
`approval_window_secs`. An MCP server's question is cut shorter by its own call ceiling, and an
approval a RUNNING subagent or workflow step asks for also ends with that work's time limit, which
counts from the start of its run: a subagent waiting for its owner to approve its start, or for a
slot, is not running yet and waits the whole window. Past the window
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

**What one answer covers.** A Deny, or an approval nobody answered, also covers the requests the
agent had already sent with the call it answers: they wait right behind it in the stream and are
refused the same way, audited with the reason `batch_rejection`. Anything else the agent reports
first (the refused call's result, the next call's card, a word of text) means it has the answer,
so the next call it makes is asked about again, as the card's "The next tool call asks again"
says (`chat_runner._REFUSALS_OF_ONE_BATCH`). A stopped turn asks nothing more until it ends.

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
chat turn (`SessionManager.stop_turn`, through the hooks `DashboardState` registers) ends that
turn's approvals and what the turn started (`started_work.end_turn`: its batch runs and
subagents, and so what they were asking, and what still waits for her Allow, a batch's start or a
workflow's save, whose ask ends, `owner_allow.end_asks`); cancelling a subagent ends its spawn and tool
approvals, which are keyed `subagent:<id>:<request_id>` so two subagents' requests can never
share an id. Each ends as `cancelled`, with an `approval_cancelled` SEL row. And the decision path
checks again (`approval_owner.owner_ended`, fail-closed): an approval whose run, loop or subagent
is gone is answered **409 `approval_owner_ended`** and nothing runs. On day 8, approving a
cancelled run's leftover spawn approval started the subagent.

**Announced once.** The Inbox row is the durable listing, not a second announcement: the chat
card announces an approval in context and the nudge everywhere else, so the SPA's notification
toast stands down for a note whose `refs` name an approval (it still lands in the bell), and
Home's inbox count and To triage recognise the row as the approval they already list
(`mirroredApprovalId`).

**No model's verdict can hide one.** `system/agent_request`, the kind the row rides, is a
*decision* (`NotificationKind.decision`): work is parked on the answer. So are
`loop/needs_input` (workflow gates, blocked loops, sign-in handoffs, control-bridge
confirmations), `agent/room_paused` and the `approval/requested` ping. A decision is never
`verifiable` — `notification_kinds.register` refuses the pair — so the second-opinion pass
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

## An agent's question to its owner

An agent that needs her decision to go on (which of two approaches, which of several things she
meant) asks it as a question with options, and its call waits for the answer. Whichever runtime
asks, the question goes through one registry, `OwnerQuestions` (`owner_questions.py`, held as
`DashboardState.owner_questions`):

| Runtime | Its question tool | How the question reaches PersonalClaw |
|---|---|---|
| PersonalClaw's own | `ask_user` (the bundled Questions for you app) | the call waits on the registry |
| Claude Code over ACP | `AskUserQuestion` | ACP's form elicitation. PersonalClaw advertises it at `initialize` for a chat's own attended session (its key in the `dashboard:` namespace) of a backend that asks this way (`ACPDialect.asks_through_elicitation`, `AcpClient._owner_answers`), and the adapter then turns the tool on and asks each question with `elicitation/create` (`acp/elicitation.py`). Without the advertisement the adapter keeps the tool off |
| Codex, kiro-cli, any other ACP agent | its own, or none | not at all: no elicitation is advertised to them, so none of their questions waits on her. A call whose tool is option-prompt-shaped by its name and whose input reads as questions is shown as a card that says she cannot answer it here, and the agent's own tool goes on without her (`OwnerQuestions.show_unanswerable`) |

Where she sees it:

- **The chat that asked**: a card with each question's header, its words, its options (one or
  several to choose), a box for her own words where the tool takes them, Send and Skip (the
  `question_card` frame; `pending_questions` on session detail for a page that opens or reloads
  while it waits). Nothing is chosen for her and nothing is sent by arriving, focus or Enter.
- **The Inbox**: a "Needs you" row (`needs_input`, `refs = {question, session, source_label}`, on
  the `system/agent_request` notification), which Home's To triage and Mission Control's Your turn
  list by who asks, in which chat, with a link to answer it there.
- The turn waits on her, so it is not running long: its clock stops (`waiting_on_owner`).

Her answer, `POST /api/chat/sessions/{session}/questions/{question}/answer` with
`{"answers": [{"selected": [option index…], "other": "her own words"}]}` (one per question) or
`{"skip": true}`, reaches the waiting call once. Only she answers: no app may. A second answer is
`409 question_answered`, one after the call stopped waiting `409 question_ended` saying why, and one
the question cannot take `400 question_answer_invalid`. How it ends, on every surface at once:
answered or skipped (the row is handled), nobody answering within her window
(`agent.approval_timeout_minutes`, the one every wait on her has), her Stop ("its turn was
stopped"), or a restart (the row expires saying so). Each ending sends `question_resolved` and
keeps how it went on the asking call's transcript row (`meta.question`), so a reload shows the card
she saw.

The card is the asking call's gate. An agent CLI's question tool runs with no permission request,
so its result would otherwise read as a call the CLI ran without asking her (`ungated_calls.py`):
marked so on its row, audited `ungated`, and, in Ask or Plan mode, a reason to stop the very turn
her answer was for. A call whose own input asks what she was asked for that call
(`OwnerQuestions.was_put`) is none of those. A form asked for any other tool's call excuses nothing,
whatever its shape.

Only the owner of an open chat is asked. Work nobody watches never gets the tool (`ask_user` is
`interactive`, and an ACP session that is not a chat's own attended one is not told it may ask). A call from anywhere else (a
background task, a loop's or a workflow's step, a chat carried on a chat channel) is told to ask in
its reply instead, and an agent CLI's question there is answered `cancel` at once. Any other form an
agent CLI asks over elicitation (a tool server's own form, a model switch) is answered `cancel` too:
she is never asked, so no answer or refusal is claimed for her. A request the ACP client does not
serve at all is refused with method-not-found, so no turn waits on an answer that never comes.

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
- mute and `min_severity` mean **dropped entirely** (`drop`, not queued); a gate failure
  fails open (a broken settings file must not silence the system).
- **quiet hours are "not now", never "not at all"**: they stop a *ping*, and the kind's rule
  decides what that leaves. Inside the window a kind somebody must answer (an attention kind
  ranked SEV_WARNING) returns `quiet` and every other kind `hush`. `notify()` then delivers a
  `digest` rule to the digest queue and a `badge` rule as a badge, exactly as outside the
  window — both already interrupt nobody. An `immediate` note becomes a `badge` (persisted,
  counted, auditable, silent) under `quiet`, or when the user's own condition raised a quieter
  rule to it; any other `immediate` note under `hush` is suppressed. Before this the gate
  dropped every note below error inside the window before the rule was read, so a loop that
  finished at 03:00 under a digest rule left no notice anywhere and the morning digest said
  "nothing queued"; a loop that needed an answer overnight left no trace in the notification
  log at all, while its durable inbox row still counted toward the badge.

A producer whose ping must still ring LATER waits instead of sending: the task due-date
notice (`tasks/due_notices.py`) reads a `hush` posture as "not now" and leaves the notice
unsent and unrecorded, and the first sweep after the window sends it. Mute and
`min_severity` still mean "not at all" for it.

What the rule layer makes of a note that passed the gate — the rule, raised by the user's
conditions, and what quiet hours leave of a ping — is one function,
`notification_rules.rule_outcome`, which `notify()` delivers by. A surface that says what
becomes of a notice asks it too, never a sentence of its own: the triage digest card reads the
gate and `rule_outcome` for its own digest at a moment inside the quiet-hours window and one
outside it (`entity_routes.quiet_window_moments`, `GET /api/proactive/digest`'s `notice`), and
says that the digest pings, shows as a badge, waits for the notification digest, or is not
announced. It used to say "held back from your notifications" for every digest in the window,
which a badge or digest rule made false.

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
  and on the `approval/requested` row it asks the approval on "Send approvals to" — see *Which
  chat channel asks* above (a chat that started on a channel is asked there without it).
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

A row the platform raises carries its notification pair's source as its sender (`app:<name>`,
`loop`), which names a delivery rule, not who asked. Home's To triage, Mission Control's Your turn
and the phone's Inbox name a row by who raised it (`attentionLanes.inboxRaisedBy`): the work its
refs name (a loop, a workflow, a trigger), an app by the name it goes by (`refs.app_display_name`,
which the proposal door keeps on the row from `app_manager.display_name_of`), and a channel message
by its sender.

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

**A message for the owner goes out on the channel they named, when they named one.** With no
channel named, `reach_owner` tries every connected channel in name order until one delivers.
`notify`'s `via`, `send-message`'s `via` and the `send-message` action `automation_create`
makes from `say` + `via` name one channel, and `reach_owner(only=…)` tries that one alone:
when it cannot deliver (not connected, no owner id, a failed send), the message goes to the
Inbox ending "This was for <channel> only, and it could not go out there: …". Another channel
never stands in for the one named. A name that is not a chat channel set up here is refused
with the ones that are (`channel_delivery.named_chat_channel`), so the owner can be asked.

**An id goes out on the channel that issued it.** A chat, channel or user id sent without
naming its channel (`notify`'s and `send-message`'s `channel` and `user`, a heartbeat's
`channel:<chan>:<ts>`, a chat's thread) goes to the one chat channel set up here that takes it:
each is asked whether the id is one of its own (`validate_target`), and for a user id whether it
is the owner's id there (`owner_id_for`), the owner being the one user core messages
(`channel_delivery.channel_of_id`). Core knows no platform's id shape: the only check it makes
itself is the one every id shares, no spaces or control characters and no longer than any
platform's (`id_problem`). An id no channel set up here takes, or more than one takes (an
18-digit number is a Telegram chat and a Discord channel alike), is refused with the channels
to choose from, and nothing is sent; naming the channel (`via`) settles it. The tracked-channel
allowlist asked is that channel's own. It used to go to whichever channel sorted first, which
posted another platform's id there, and only a Slack-shaped id got past the route at all.

**Someone new, on a channel that speaks as the owner.** A channel whose messages go out as
the owner themselves, from their own mailbox, declares `ChannelCapabilities.speaks_as_owner`.
The gate hands it no pairing note for a stranger, and the door holds the stranger's direct
message in the Inbox as someone new (`native_source.hold_from_someone_new`: `refs.someone_new`
names the channel). The owner answers the row: **Reply** goes back through that channel,
threaded under the message, only when the owner presses Send; **Pair** (`POST
/api/inbox/{id}/pair`) lets the sender talk to the agent there from their next message on;
**Ignore** dismisses it. One row per message; a muted thread holds nothing more, and a held
row raises no inbox event, so a sender the gate refused arms no automation. The gate holds the
message first (the door hands it `hold_for_owner`) and composes the owner's once-a-day notice
from what was held: a message the Inbox kept out raises no notice and leaves the window open,
so the notice never names a message that is not there.

**You answer someone new from the notification.** The note the gate raises for someone new
(`channel_trust.note_unknown_sender`, `event: channel.unknown_sender`) offers **Allow** and
**Deny** on the Notifications page (`POST /api/notifications/trust`, owner-only). The sender is
read off the stored note, never the request, and only a sender the gate recorded telling you
about is answered (`channel_trust.owner_was_asked_about`): a note another emitter wrote, or one
an app raised, lets no one in. An Allow asks your consent first, and so does the Inbox's
**Pair**, in the same words (`channel_trust.sender_consent`): whoever is let in is read as you
are. A Deny asks nothing. The answer goes through `channel_trust.apply_trust_action`, which
writes the security audit (`sender_paired` / `sender_denied`), and is recorded on the note
(`trust_answer`, platform-owned), which then offers neither again.

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
  through these calls; they never touch the map file. It is the one place a
  link is kept, and the inbound door reads it (`get_linked_session`), so a
  channel thread continues its chat after a restart, with the chat brought back
  from disk when it is not resident; a chat whose transcript is gone starts
  over. A chat answers on the channel its origin tag names, which rides its
  meta line across restarts; a chat a channel app imports from a thread is made
  as that channel's (`get_or_create_session(app=…)`), as the door makes one.
- Dashboard-side link/handoff routes are `dashboard/chat_channel.py`
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`) — provider-blind, `ChannelDelivery` only.
  A link and a handoff both record the chat through one function
  (`_continue_there`): the thread's key where the inbound door reads it, and the
  channel the chat is now on, so its answers and notices go to the thread and a
  reply there continues it. A thread target opens on the channel `provider`
  names, else the one channel that issued its id (`channel_of_id`); an id two
  channels could take is refused, and nothing is posted.
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
