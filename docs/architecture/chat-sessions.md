# Chat & Sessions

How a message becomes a turn: the session model, the dashboard chat pipeline,
persistence, and the memory-privacy modes. Paths are relative to
`PersonalClaw/src/personalclaw/`.

## Session model

A session is one conversation thread, whatever surface it lives on (dashboard
chat, channel thread, loop worker, webhook, subagent).

- **`session.py` — `SessionManager`.** Owns live session state. Each session
  has a FIFO message queue (`deque` of pending messages) guarded by a
  semaphore, so messages arriving on the same channel thread are serialized —
  a turn finishes before the next queued message starts.
- **`session_map.py` — the persistent session↔thread map.** Stored at
  `~/.personalclaw/session_map.json` (atomic tmp+rename writes). Each entry
  carries `sid`, `thread_ts`, `channel_id` — generic keys, no channel-vendor
  shape assumed. `set_channel_link` / `get_channel_link` are the one API for
  linking a dashboard session to a channel thread; a reverse index maps
  `thread_ts` → session key.
- **`session_restrictions.py` — memory modes.** Two restriction registries,
  kept in core because any surface can request them:
  - **temporary** — blank-slate thread: memory READS suppressed
    (`blocks_reads`) *and* writes suppressed.
  - **incognito** — ephemeral: memory WRITES suppressed, reads allowed.

  `is_restricted()` (either mode) gates the after-turn learning path, session
  listing/search, and memory recall (see
  [knowledge-memory.md](knowledge-memory.md)). Restricted sessions never write
  lessons (`after_turn_review.py` checks `session.is_restricted`).
- **`session_workspace.py` / `session_pid.py`** — per-session working
  directory resolution and process-id tracking.

## History & persistence

- **`history.py`** — one JSONL file per session at
  `~/.personalclaw/sessions/{safe_key}.jsonl`. **The file is the user's
  record and nothing shortens it**: there is no size cap, no rotation and no
  background rewrite, at any size. What bounds cost is what a reader takes —
  `recent()` and `history_for_model()` return a window.
- **Background compression shortens what the model reads, never the file**
  (`bg_compress.py`). An idle chat gets a derived record beside its
  transcript, `{safe_key}.summary.json`, naming the span it covers — counted
  in turns (`summarized`, `reduced`), so it applies to the file and to a
  resident session's buffer alike — and a digest of that span. `model_view()`
  is the one transform: while the digest still matches, the model reads the
  summary in place of the oldest turns and the next ones capped; the moment a
  turn in the span changes, it reads the turns as written. The dashboard applies
  it to the turns a fresh runtime is handed (`prior_turns_transcript`); a reader
  with no live session reads `history_for_model()`. `model_window()` is the one
  message budget both use, and it keeps a summary first. Deleting a chat
  deletes its record, and the pass drops any record whose transcript is gone.
- **`sessions/archive/`** holds lines EARLIER versions trimmed out of chats
  (2 MB rotation and the old background rewrite). Nothing writes it and nothing
  prunes it — for those chats a batch can be the only copy — and neither the
  startup/shutdown workspace sweep (`cleanup_stale_sessions`) nor any write
  path touches it; a batch is removed when its chat is deleted. Archive *reads*
  are redacted through `redact_credentials` / `redact_exfiltration_urls` before
  anything leaves the store.
- **`resolve_history_key()`** resolves whether a bare key is a channel-thread
  key or lives in the `dashboard:` namespace *by asking the store* — core
  assumes no key shape and names no provider.
- **`dashboard/chat_persistence.py`** — the dashboard-side persistence
  contract over the JSONL store (message append, metadata, variants).
  Model-to-provider matching is data-driven via
  `catalog.model_family_provider_types(model)` — no vendor names at the call
  site, and unknown model families are never restricted.
- **The transcript buffer is the whole file.** `save_session_to_history`
  rewrites a session's file from `_ChatSession.messages`, so the buffer holds
  every message the file holds and nothing trims it. Every path that loads a
  persisted chat (boot restore, opening one from disk, resume) goes through
  `_seed_transcript`, which loads the whole file with each line's `cls` and
  `meta`. The buffer holds transcript entries only: a streamed answer is ONE
  `streaming` entry however many chunks it arrives in (`stream_chunk`), settled
  in place into an `assistant` entry (`finish_stream`), and the end-of-turn
  marker goes to live readers only (`signal_done`). An approval is written once
  it is decided. The save records `message_count` in the metadata line, which
  `ConversationLog.list_sessions` serves as the chat list's count.
- **Who started a conversation.** `_ChatSession.created_by_app` is the app whose
  token started it, or empty for yours, and it is the one thing an app's reach
  into a conversation is decided on (`apps/permissions.ROUTE_AUTHZ` rows that
  carry `owns`, enforced by `dashboard/server.py::app_permission_middleware`).
  It is set only from a verified app identity, written to the metadata line as
  `created_by_app`, and read back in `DashboardState.get_or_create_session`, the
  one place every restore path mints a session, so a restart keeps an app's
  conversation the app's and never makes one of yours an app's. It is not
  `_app`: that is an origin tag (`loop`, a channel's provider name, an app's
  name) saying where a conversation came from, which the chat list groups by,
  and an app can share its name. An app's conversation runs under the app's
  `agent` grant, never under your approval switches, whoever sends the message
  (`chat_runner.app_conversation_posture`).

## The dashboard chat pipeline

`dashboard/chat_runner.py` is the turn engine. A turn flows:

1. **Prompt-mention expansion** — a leading `@name key=value` expands a saved
   prompt via `_expand_prompt_mention` (user prompts live at
   `~/.personalclaw/prompts/`, snippets at `prompt_snippets/`; the composer's
   @-menu suggests prompts only at message start).
2. **Context assembly** — `context.py` (`ContextBuilder`) builds the system
   context: the runtime values `{{bot_name}}` and `{{user_name}}` (live-resolved
   from `agent.bot_name` and `dashboard.user_name`, Settings → Account), memory
   context, and — for channel-linked sessions — the `channel-thread-context`
   snippet. A fresh runtime is restored ONLY from the session's own turns before
   the one being sent (`chat_persistence.prior_turns_transcript`) — never the
   in-flight message, never another session's transcript — with the chat's
   background summary standing in for its oldest turns while it still describes
   them (`history.model_view`). `context_engine.py` and
   `context_compaction.py` manage sizing and compaction.
3. **Agent resolution** — an agent's own `system_prompt` governs when it has one;
   the default agent ships with none, so its prompt is the one bound in Settings →
   Prompts for the turn's context (`chat`, or `background` for unattended runs).
   The agent's voice and the task-mode posture (`system_prompt_suffix`) are layered
   ON TOP of whichever prompt resolved — never a replacement (see `build_message`).
4. **Model resolution** — the `chat` use-case binding from
   `active_models.json`, unless the agent pins a model or the composer
   overrides per-session (the `model` kwarg threads through
   `llm/registry.py` `registry.build`; every factory honors it).
5. **Streaming + persistence** — chunks stream over the dashboard WebSocket;
   the finished turn is saved by rewriting the session JSONL from the buffer.
   Every exit from a turn, an error included, first settles the answer
   streamed so far (`_flush_segment`), so a partial answer is kept like a
   finished one.

Around the engine:

- **`dashboard/chat_handlers.py`** — session listing/history. Channel-linked
  rows carry `origin="channel"` (computed at list-time from the session map,
  never persisted); the frontend `ChatPage.tsx` switches tabs on that literal.
- **`dashboard/chat_title.py`** — auto-title plus optional auto-tagging in ONE
  background LLM call (config `dashboard.auto_tag_sessions`);
  `chat_retag.py` is the batch re-tag job (cancellable, board-triggered).
- **`dashboard/chat_folders.py` / `chat_tags.py`** — organization; persisted
  in `folders.json` / `tags.json`.
- **`dashboard/chat_channel.py`** — channel link/handoff routes
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`) — provider-blind, built on
  `ChannelDelivery` only (see [inbox-channels.md](inbox-channels.md)).
- **`dashboard/chat_voice.py`** — `POST /api/voice/synthesize`, sentence-
  chunked TTS through `tts.registry.active_voice_params` (whatever TTS
  provider is bound).

## Variant branching (regenerate)

`dashboard/chat_regenerate.py`: regenerating an assistant message preserves
the prior answer as a **variant**. The message's `variants[]` list (capped at
`_MAX_VARIANTS`) plus `variant_idx` are persisted in the session JSONL, so the
user can flip between alternative answers and the choice survives reload. The
backend broadcasts variant switches; the frontend renders prev/next navigation
on the message.

## Forking

`dashboard/chat_fork.py` — `POST /api/chat/sessions/{session}/fork` copies a
session into a new tab. An app may fork only a conversation it started (its
`ROUTE_AUTHZ` row carries `owns`, checked against `created_by_app`), and the
fork is the app's too; one of yours is refused to every app.

## The session map (in-session index) + per-turn telemetry

`dashboard/chat_session_map.py` — `GET /api/chat/sessions/{session}/map` answers a
JSON array of **marks**: one per turn (`user` / `assistant`) plus one per indexable
sub-event inside a turn (`tool`, `approval`, `error`), in turn order, each with
`{markIndex, kind, role, visibleIndex, ts, preview}` and an optional `telemetry`.

- **`visibleIndex` is the jump coordinate** — the index of the turn's LAST message in
  the backend's visible (`user`/`assistant`) list, i.e. the same inclusive
  `at_message_index` that `POST .../fork` and edit-resend speak. It is derived from the
  identical message list `GET /api/chat/sessions/{session}` serves
  (`chat_utils.full_session_messages` → `_prepare_messages`), because an index into any
  other list would address a different message than a fork does.
- **The derivation mirrors the frontend's.** `web/src/pages/chat/sessionMap.ts`
  (`sessionMapMarks`) derives the same shape from the hydrated turns; the endpoint is a
  second SOURCE for one contract, not a second contract. Both reproduce
  `hydrateTurns`'s two collapses — a native-loop prompt re-injection consumes a visible
  slot without producing a turn, and consecutive assistant messages merge into one turn
  keyed on the last message folded in. The map UI draws a coarser view of the same marks:
  one entry per user message (`sessionMapEntries`, which groups the marks by exchange), so
  the typed marks remain the contract even though the rail no longer draws every kind.
- **Two kinds are live-only.** `subagent` and `activity` ride WS streams that are never
  written to the conversation log, so the durable endpoint witnesses
  `user`/`assistant`/`tool`/`error` after a restart, and `approval` once it is decided
  (`_persistable` writes a resolved `permission` row and holds back a pending one).
- **Per-turn telemetry** (cost, tokens, cache split, duration, context %, event and
  tool-call counts, model) is stamped by `chat_runner` onto the turn's LAST assistant
  message as `meta.turn_telemetry`, *before* `save_session_to_history` — that function
  rewrites the transcript file from the buffer, so a later stamp would be in-memory
  only. It rides the same `meta` seam as `memory_citations` / `skills_used`: no new file
  and no new channel. Absent = the turn reported nothing; `priced: false` means the
  model has no price row (never "free"); `context_pct: null` means the provider measured
  nothing (never 0%).

## Channel-linked sessions

A dashboard session can be linked to a channel thread (and vice versa):

- Linking goes through core `session_map.set_channel_link` — the channel app
  never touches the map file directly.
- `sync_bridge.py` implements the dashboard↔channel handoff
  (`handoff_to_channel` over `ChannelDelivery`): the conversation continues in
  the channel with context intact.
- `voice_reply.py` uploads TTS voice replies to the channel
  (`upload_voice_to_channel`); markdown deep links are stripped generically
  before synthesis.

## Prompt entities

`prompt_providers/` is the prompt-catalog subsystem (bundled use-case prompts
plus user prompts). Use-case prompts include e.g. `task-channel-title`
(use case `channel_title`) for naming channel-originated tasks. All bundled
prompts are provider-blind.

## Related docs

- Memory recall/write rules per session mode:
  [knowledge-memory.md](knowledge-memory.md)
- Channel delivery and thread linking: [inbox-channels.md](inbox-channels.md)
- The agent/tool layer a turn can reach: [overview.md](overview.md#capability-seams)
