# CHAT-CRAFT

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/CC.md`](../atomic/CC.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Chat Craft — Seven Proven Chat-Surface Mechanics

**Status:** IN PROGRESS — S1-S3 shipped 2026-07-27 (true rewind + queue interrupt-now,
find-in-conversation + quote toolbar, follow-up chips + smooth streaming; see `## Execution log`).
Verified wired: `chat_followups.py` ← `chat_runner.py`, `FindBar`/`FollowupChips` imported into
`ChatPage.tsx`, the `chat_rewound`/`queue_promoted` WS handlers live, and `followup_chips` +
`stream_reveal` round-tripped with FE controls.
**S4 (screen-snip + polish + T4.5 optimizer) NOT started** — no `SnipOverlay.tsx`, no
`getDisplayMedia` anywhere in `web/src`. The 2026-07-29 amendment (Branch affordance F1.1/F1.2 + the
chat entry to the planning walkthrough F1.3/F1.4) is **NOT started**.
⚠️ Note for whoever takes it: the ChatPage serialization this plan's order relied on was already
broken — Agent-Routing and Artifacts-Evolution both landed ChatPage churn first, so re-read the recon
before starting. Status corrected 2026-08-04 by code audit. Created 2026-07-26 (roadmap rev 12; owner
ask: chat-surface gap analysis greenlight)

---

## Context (code recon, 2026-07-26)

- **Rewind today is fork-OR-tail-only.** `dashboard/chat_regenerate.py::api_chat_session_edit_resend` (POST `/api/chat/sessions/{session}/edit-resend`) locates the target user message by `ts` → index → *last-user fallback*, then `del session.messages[index:]`, re-appends the edited text, persists via `_persist_history_off_thread` (a `to_thread` wrapper over `chat_persistence._save_session_to_history(state, session, msgs_snapshot)` — the save rewrites the WHOLE transcript from `session.messages`, not append-only), and dispatches `_run_chat`. The **old tail is discarded** — nothing like the variants mechanism preserves it. The FE (`web/src/pages/ChatPage.tsx::editResend`, ~line 1400) truncates `turns` optimistically and only offers Edit on any user turn via `MessageActions.tsx::UserActions` — so the *UI* already lets you edit any past message, but the replay silently loses the discarded assistant answers. Fork (`chat_fork.py::api_chat_session_fork`) copies visible messages to a NEW session (`state.get_or_create_session`, `forked_from` set, redaction applied) — new slot, new URL. Variants (`chat_regenerate.py`, `_MAX_VARIANTS = 20`) already prove the "retain superseded content on the message dict" pattern: `msg["variants"]: [{content, ts}]` + `variant_idx`, rehydrated by `chat_persistence._attach_variants` and paged by the FE `VariantSwitcher`.
- **Provider context after truncation:** the truncate-and-redispatch trick works because a NEW/reset provider rebuilds context from history — `chat_runner._run_chat` (line ~1291) calls `context.compress_thread_history(conversation_log, session_key, message, state.sessions)` when `is_new and not resumed`. The native runtime owns history in `NativeAgentRuntime._messages` (`agents/native/runtime.py:299`) and does NOT know the dashboard list was truncated; edit-resend today relies on the running provider absorbing the resent prompt as a fresh turn. True rewind therefore needs `state.sessions.reset(session_key)` (`session.py:1107` — kill + recreate) so the next turn's `is_new` path rebuilds provider context from the truncated transcript. That is exactly the "fork-and-swap" pattern: fresh provider session swapped under the same slot key.
- **Queue:** `_ChatSession._queue: list[{"id", "content"}]` (`dashboard/state.py:320`) with `queue_append/insert/pop/remove_by_id/promote` (state.py:475-516). Mid-turn sends route through `chat_handlers.py:148-192`: policy `resilience.mid_turn_policy` (`queue|cancel_and_replace`, `config/loader.py:1263`, PATCH-editable at `handlers/core.py:459`) via `_maybe_cancel_and_replace` (uses `resilience/active_jobs.py::get_tracker/is_cancellable_origin` + debounce), then `queue_mode` steer/followup/collect. Turn-end FIFO drain lives in `chat_runner.py:2770-2838` (`_dequeue_next_message` from `chat_utils.py:584`, honoring `dashboard.merge_queued_messages`). **The manners gap is one wire:** `chat_handlers.py:950::api_chat_session_interrupt` (POST `/interrupt`, body `{queue_id}` → `queue_promote` → `stop_turn(force=False, preserve_queue=True)`) is fully built and route-registered (`server.py:634`) but **no FE caller exists** (`grep interrupt web/src` → only the composer steer comment). FE queue UI: `ChatPage.tsx::QueueStack` (~line 2319) — stacked cards, per-item Cancel (`api.cancelQueued` → DELETE `/queue/{queue_id}`, WS `queue_cancel`) and honest Edit (cancel + refill composer); WS events `queue_push/queue_pop/queue_cancel` already drive it.
- **Find-in-conversation: absent.** No find bar anywhere in `web/src` (grep `FindBar|find-in|Cmd+F` → nothing). The transcript renders as `turns[]` in a `scrollRef` scroll container (`ChatPage.tsx:1970`); `jumpToTurn(turnIndex)` (~line 1530) already scrolls a turn into view via `turnNodes` refs — the scaffolding a match-jumper needs. SESSION-MANAGEMENT's FTS search is *cross*-session; this is a pure client-side in-memory scan of hydrated turns.
- **Quote-reply: 80% built.** `ChatPage.tsx::SelectionQuote` (~line 2461) floats a single `SelectionPill` "Quote" button over a transcript selection; `quoteToComposer` (~line 1518) inserts `> `-prefixed lines into the composer and focuses the CodeMirror `.cm-content`. Missing: attribution (who said it), a Copy affordance beside Quote, and keyboard/touch selection support.
- **Follow-up chips: only the welcome screen has suggestions.** `suggestions.py` — `SuggestionsCache`, `generate_suggestions(state)` streams the bundled `task-suggestions` prompt (`config/prompts/task-suggestions.md` via `prompt_providers.runtime.render_use_case_prompt`) through the shared background lite session (`state.sessions.get_or_create(BACKGROUND_KEY)`; `BACKGROUND_KEY = "_bg"`, agent `personalclaw-lite`, `session.py:342`), rejects any permission request, redacts output, 30-min cache, GET `/api/suggestions` (`server.py:485`). FE: `ChatPage.tsx::SuggestionChips` (~line 129) renders them ONLY on the empty-chat hero. Per-turn chips don't exist. The post-turn hook point is `chat_runner.py:2839-2855` (queue-empty `finally` branch: `chat_done` broadcast + the fire-and-forget `_maybe_auto_title` task — the exact pattern to mirror). Restriction plumbing exists: `session.memory_mode` (`persistent|temporary|incognito`, `state.py:377`) + `session_restrictions.py` (`is_temporary/is_incognito/is_restricted`); `_maybe_auto_title` already skips restricted sessions. Background non-interactive calls are guardrail-wrapped by `guardrails/model_call.py::ModelCallGuard` (breaker + timeout + budgets at the provider-build point) — chips inherit that for free by using `_bg`. No model bound → `get_or_create(BACKGROUND_KEY)` raises at the factory (`_ensure_background` defers quietly) → chips must catch and render nothing.
- **Screen-snip: macOS-native only.** POST `/api/screenshot` (`handlers/files.py:838`) shells out to `screencapture -i` → `~/.personalclaw/screenshots/`, darwin-gated (400 otherwise). FE: `ChatPage.tsx::captureScreenshot` (~line 1704) threads `r.path` into `attachedPaths`, exposed via the composer "+"-menu behind `isMac`. The generic upload pipeline is ready for a browser path: `api.uploadFiles` → POST `/api/upload/file` (`handlers/files.py:653`, per-filetype policy `uploads/policy.py::check_upload`) → `~/.personalclaw/uploads/` → upload-time extraction (`attachment_extract.py::AttachmentExtractor.start`, OCR included) → turn-time injection (`chat_runner.py:886::_inject_attachment_content`) → `TurnAttachments` chips. DESKTOP-CAPABILITIES S2 defines a `screen_capture` bridge capability — S4 leaves a seam, not an implementation.
- **Streaming:** WS `chat_chunk` (`chat_runner.py:1580`) → `ChatPage.tsx` `coalescer` (`useStreamCoalescer`, line 548) → `pages/chat/useStreamCoalescer.ts` — the P15 rAF coalescer with a pure `CoalescerCore` (EMA backlog estimate, `MIN_BUDGET=2`/`MAX_BUDGET=400` chars/frame, `MAX_LAG=1200` catch-up ramp, `drainAll()` on boundaries, `reset()` on `breakText`). So "smooth streaming" is already 70% real; missing: **word-boundary snapping** (reveals cut mid-word), and a **user toggle** — today immediate-mode is only reachable via `runtime.animSpeed === 0` or `prefers-reduced-motion` (`isImmediate()`, useStreamCoalescer.ts:91), not a chat preference.
- **Chat config = `DashboardConfig`** (`config/loader.py:672` — there is no dataclass literally named `ChatConfig`; the chat-surface prefs live here: `merge_queued_messages`, `send_on_enter`, `show_timestamps`, `show_thinking_inline`, `simplified_tool_names`, `confirm_close_session`, all `_meta`-annotated). Write path: GET/PUT `/api/dashboard/config` (`handlers/files.py:2656::api_dashboard_config`, `_allowed` set at :2672, per-field validation, `cfg.save()`). FE: `pages/settings/ChatPanel.tsx` rows + `settingsWidgets.tsx` mirror + `api.ts::dashboardConfig/saveDashboardConfig` (`DashboardConfig` interface ~line 882). `tests/test_config_roundtrip.py` enforces the 5-point contract. New fields here follow that exact path (dataclass+`_meta` → `load()` → `to_dict()` → `_allowed` PUT → ChatPanel row).
- **Existing tests to build beside:** `tests/test_message_queue.py`, `test_queue_cancel.py`, `test_interrupt.py`, `test_merge_queued_messages.py`, `test_chat_undo.py`, `test_fork_mode.py`, `test_config_roundtrip.py`; FE unit tests colocate (`useStreamCoalescer.test.ts`, `coalesceReducers.test.ts`, `sendButtonState.test.ts`).

## Design

- **S1 — Interaction mechanics: true rewind + queue with manners.** *(a) True rewind* — upgrade edit-resend from destructive truncate to **fork-and-swap under the same slot**: on edit of ANY past user message, the endpoint (same route, extended) snapshots the discarded tail (`messages[index:]` before deletion) onto the edited user message as `rewound: [{messages, ts}]` (the variants pattern at message level, capped like `_MAX_VARIANTS`), truncates, re-appends the edit, persists (`_save_session_to_history` full-rewrite semantics make this atomic-enough), **resets the provider** (`state.sessions.reset(session_key)`) so the next `_run_chat` rebuilds context from the truncated transcript via `compress_thread_history` — slot key, URL, title, folder, tags, side-chat links all untouched. The FE unlocks Edit on non-last user turns (today it works but silently loses answers — after this it's honest), shows a "rewound from here — N messages retained" divider chip on the edited turn, and a disclosure to view (read-only) the retained tail. Restoring a tail = fork it into a new session (reuses `chat_fork.py` wholesale — no in-place timeline branching UI; one active timeline per slot, history preserved). Persisted-shape change (`rewound` on message dicts) = the plan's one clean-break item: tolerant reads (missing key = today's shape), no migration. *(b) Queue with manners* — wire the orphaned `/interrupt`: each `QueueStack` card (except when it's the only affordance-less deep card) gains "Interrupt now" — POST `/interrupt {queue_id}` → `queue_promote` → soft-stop → the existing finally-block drain runs it next. The superseded partial answer keeps the existing `stop_event` rendering. Stack UI stays; add depth badge + per-item timestamps. No backend queue changes beyond a `queue_promoted` WS echo so all clients reorder their strip.
- **S2 — Find-in-conversation + quote-reply.** *(a) Find* — a pure-frontend find bar over the hydrated `turns[]`: Cmd/Ctrl+F (only when the chat page owns focus and a session is open; second press or Esc falls through to the browser) opens a compact bar docked under the chat header; case-insensitive substring match over each turn's text segments (`turnText`) + tool-card titles; count ("3/17"), Enter/Shift+Enter or ↑↓ to cycle; active match scrolled via the existing `turnNodes` map + `scrollIntoView`; matches highlighted via a `<mark>`-injecting text renderer wrapper (highlight only, never re-parse markdown — wrap the rendered text nodes with CSS Custom Highlight API where available, range-walk fallback). Long sessions: matching runs over the already-in-memory turn list (the transcript is fully hydrated by `chatSessionDetail`), debounced 150ms. *(b) Quote-reply* — grow `SelectionQuote` into a floating toolbar: Quote + Copy buttons (same `SelectionPill` idiom, two actions); Quote now emits an attributed blockquote — `> **You said:**` / `> **{agent} said:**` prefix derived from the selection's enclosing turn (resolve via the `turnNodes` ancestor) — inserted by the existing `quoteToComposer`; add `selectionchange`-based positioning so keyboard/touch selections get the toolbar too (today it's mouseup-only).
- **S3 — Follow-up chips + smooth streaming.** *(a) Chips* — after each completed interactive webui turn: in the same queue-empty finally branch that fires `_maybe_auto_title` (`chat_runner.py:~2853`), fire-and-forget `_maybe_followups(state, session)` (new `dashboard/chat_followups.py`): skip if disabled in config, session restricted (`memory_mode != "persistent"` — mirrors `_maybe_auto_title`'s gate), queue non-empty, or turn errored; render the bundled `task-followups` prompt (last user msg + assistant reply tail, truncated) via `render_use_case_prompt`; stream through `BACKGROUND_KEY` exactly like `generate_suggestions` (deny permissions, 20s `wait_for`, redact); parse a JSON array of 2-3 ≤60-char strings; broadcast WS `chat_followups {session, ts, items}`. **Never blocks the turn** — the user's next send cancels the pending task (task handle stored on the session; a new `_run_chat` cancels it) and hides chips. No model bound → `get_or_create` raises → caught → no event → chips simply don't render (the degrade contract). FE: a `FollowupChips` row under the last assistant turn's actions (visual sibling of the hero `SuggestionChips`), click = insert into composer (reuse `insertPrompt`), double-click (or a small send glyph) = send immediately; any user activity (typing 3+ chars, sending, switching session) dismisses. Budget/breaker/timeout ride `ModelCallGuard` on the `_bg` provider — no new guardrail code. *(b) Smooth streaming* — `CoalescerCore.tick()` gains word-boundary snapping: the per-frame reveal cursor backs up to the last whitespace/CJK boundary within the budget window unless backlog > `MAX_LAG` (catch-up keeps priority — it never falls behind; `drainAll()` on boundaries unchanged). New `DashboardConfig.stream_reveal: "smooth"|"immediate"` (default `smooth` = today's animated path) wired through the 5-point contract + a ChatPanel `SegPills` row; `useStreamCoalescer` accepts it via its existing `opts.immediate` knob (`immediate` short-circuits in `isImmediate()`; reduced-motion/`animSpeed===0` still force immediate). Zero backend changes.
- **S4 — Screen-snip into chat + polish/validation (Wave 3).** *(a) Snip* — browser-generic capture beside the mac-native one: "Capture screen area" in the composer "+"-menu (all platforms with `navigator.mediaDevices.getDisplayMedia`) → one-frame grab (getDisplayMedia → `ImageCapture`/video-frame → canvas, tracks stopped immediately — no ongoing capture) → an in-app crop overlay (drag-select on the frozen frame, Esc cancels) → PNG blob → the existing `api.uploadFiles` pipeline (policy check, uploads dir, extraction-at-upload OCR, attachment chip). macOS keeps the native `screencapture -i` path (better UX: OS-level snip) with the browser path as the non-mac/permission-denied fallback; feature-detect + degrade to nothing (menu item hidden) where the API is absent (iOS Safari). A `desktop_capture` note: when DESKTOP-CAPABILITIES S2 lands its `screen_capture` bridge, the Electron shell replaces getDisplayMedia with the consent-gated native picker behind the SAME composer entry — this plan defines the entry point, not the bridge. *(b) Polish/validation* — the cross-cutting sweep: keyboard traversal + `aria-live` for find results and chips; SEL coverage audit (rewind, interrupt-now already log; chips generation logs one `log_tool_invocation(tool_name="chat_followups")` per generation); full user-validation pass of all seven from the dev-home frontend; docs (`docs/` chat guide) + CHANGELOG (class-B note for the `rewound` message field).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md); new-route errors use the §2.2 envelope — existing routes keep their `{"error": "<msg>"}` shape, imitate-the-neighbor)

### C1 — Rewind (extends `chat_regenerate.py::api_chat_session_edit_resend`; same route)
```python
# POST /api/chat/sessions/{session}/edit-resend
# Body (extended): { index?: int, ts?: str, client_ts?: str, content: str, rewind?: bool }
#   rewind=true  → fork-and-swap semantics (any user turn): retain tail + provider reset
#   rewind absent/false → today's last-turn behavior, byte-identical (no shape change)
# Response: { ok: true, rewound: int }   # messages retained into the tail snapshot

# NEW message-level metadata (persisted via _save_session_to_history; tolerant reads —
# clean break under the pre-1.0 banner, no gate/migration):
#   user_msg["rewound"]: [ { "messages": [<message dicts>], "ts": "<ISO>" } ]  # capped at 5
# WS: "chat_rewound" { session, index, retained }   # clients truncate + re-hydrate
```
Provider swap: `await state.sessions.reset(_history_key_for(session.key))` after persist; the next `_run_chat`'s `is_new` path rebuilds context via `compress_thread_history` from the truncated transcript. Refused while `session.running` (409, mirrors undo) and on non-persistent sessions for the tail-restore fork (mirrors `chat_fork.py`).

### C2 — Queue manners (backend exists; one addition)
```python
# EXISTING, consumed as-is: POST /api/chat/sessions/{session}/interrupt  { queue_id?: str }
#   (chat_handlers.py:950 — queue_promote + stop_turn(force=False, preserve_queue=True))
# NEW WS echo on promote: "queue_promoted" { session, queue_id }
# FE api.ts addition:
interruptChat: (session: string, queueId?: string) =>
  post<{ ok: boolean }>(`/api/chat/sessions/${session}/interrupt`, queueId ? { queue_id: queueId } : {})
```

### C3 — Follow-up chips (new `dashboard/chat_followups.py`; no HTTP route — WS-push only)
```python
async def _maybe_followups(state: DashboardState, session: _ChatSession) -> None: ...
#   gates: config off | memory_mode != "persistent" | session._queue | _last_turn_errored
#   model: state.sessions.get_or_create(BACKGROUND_KEY) (lite agent; ModelCallGuard-wrapped);
#          permission requests rejected (suggestions.py pattern); asyncio.wait_for(..., 20)
#   prompt: render_use_case_prompt("followups", {...})  # new bundled config/prompts/task-followups.md
#   output: 2-3 strings, each ≤60 chars, redact_credentials + redact_exfiltration_urls applied
# WS: "chat_followups" { session, ts, items: [str] }
# Session handle: session._followups_task — cancelled by the next _run_chat dispatch.
```

### C4 — Config additions (`DashboardConfig`, `config/loader.py:672` — full 5-point round-trip each)
```python
followup_chips: bool = field(default=True, metadata=_meta(
    "Follow-up suggestions",
    "After each reply, show 2-3 suggested next messages (one small background model "
    "call; never blocks the turn). Skipped for temporary/incognito chats; silent when "
    "no model is bound."))
stream_reveal: str = field(default="smooth", metadata=_meta(
    "Streaming text reveal",
    "smooth: steady word-by-word reveal decoupled from network chunks (never lags). "
    "immediate: render each chunk the instant it arrives.",
    enum=["smooth", "immediate"]))
```
Write path: the `/api/dashboard/config` PUT `_allowed` set (`handlers/files.py:2672`) + GET echo (:2788) + `web/src/lib/api.ts` `DashboardConfig` interface + `pages/settings/ChatPanel.tsx` rows. `tests/test_config_roundtrip.py` covers both.

### C5 — Frontend modules (new files; all under existing lint/token gates)
```
web/src/pages/chat/FindBar.tsx        # bar UI + match state; useFindInTurns(turns, query) hook
web/src/pages/chat/findMatches.ts     # pure match/segment scanner (unit-tested, no DOM)
web/src/pages/chat/FollowupChips.tsx  # chips row; consumes chat_followups WS event
web/src/pages/chat/SnipOverlay.tsx    # getDisplayMedia frame-grab + crop overlay → Blob
web/src/pages/chat/useStreamCoalescer.ts  # MODIFIED: word-boundary snap in CoalescerCore.tick()
```

### Integration points
- **Calls:** `state.sessions.reset` / `stop_turn` / `queue_promote` (session.py, state.py), `_save_session_to_history` + `_attach_variants`-style rehydration (chat_persistence.py), `compress_thread_history` (context.py), `BACKGROUND_KEY` session + `render_use_case_prompt` (suggestions.py pattern), `ModelCallGuard` (guardrails/model_call.py — implicit via `_bg`), `api.uploadFiles`/`check_upload`/`AttachmentExtractor` (snip), `sel().log_api_access`/`log_tool_invocation` (§2.3 — event names `chat.rewind`, `chat_followups`; interrupt/queue events already logged).
- **Called by:** `ChatPage.tsx` (all seven surfaces), `ChatPanel.tsx` (two new settings rows), the WS dispatcher switch (`chat_rewound`, `queue_promoted`, `chat_followups` cases).
- **Storage owned:** the `rewound` message-metadata field (inside existing session JSONL — no new files); `task-followups.md` bundled prompt; two `DashboardConfig` fields.
- **Gate/migration:** none — pre-LIFECYCLE-DOCTRINE clean break for the `rewound` field (tolerant reads; old sessions unchanged); everything else is additive config/UI. Revisit under plan 31's retrofit sweep when it lands.
- **Explicit non-goals:** no timeline-branching UI (tail restore = fork), no queue reorder endpoint (promote-via-interrupt only), no cross-session find (plan 50 owns that), no always-on capture, no new model bindings.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — True rewind + queue with manners

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Rewind backend: extend `api_chat_session_edit_resend` with `rewind: true` — snapshot the discarded tail onto the edited user message (`rewound` list, cap 5, redacted like `_attach_variants`), truncate, persist off-thread, `sessions.reset(session_key)`, broadcast `chat_rewound`; 409 while running; SEL `chat.rewind` | `src/personalclaw/dashboard/chat_regenerate.py`, `chat_persistence.py` (rehydrate `rewound` tolerantly) | editing turn 3 of a 10-turn chat replays from there under the SAME slot key/URL/title; the old tail survives in the message dict; a reload shows the rewound state; old-shape sessions load unchanged (test beside `test_chat_undo.py`) |
| T1.2 | Rewind frontend: unlock Edit on any user turn with a rewind confirm ("replay from here — N later messages kept in history"), rewind divider chip + read-only tail disclosure on the edited turn; tail-restore = `api.forkSession` at that point | `web/src/pages/ChatPage.tsx` (`editResend`), `web/src/pages/chat/MessageActions.tsx`, `chatTypes.ts` (`HistMsg.rewound`) | edit-any-turn works end-to-end from the UI; the retained tail is viewable and forkable; last-turn edit without the flag behaves byte-identically to today |
| T1.3 | Interrupt-now: `api.interruptChat` + an "Interrupt now" action on `QueueStack` cards; backend `queue_promoted` WS echo; superseded partial keeps the existing `stop_event` card | `web/src/lib/api.ts`, `ChatPage.tsx` (`QueueStack` + WS case), `src/personalclaw/dashboard/chat_handlers.py` (echo only) | mid-stream, "Interrupt now" on queued item #2 stops the turn gracefully and runs #2 next (extend `tests/test_interrupt.py`); Cancel and Edit unchanged |
| V1 | Validation: from the dev-home UI — 10-turn chat: rewind at turn 3, verify provider context is the truncated transcript (agent doesn't reference undone turns), tail retained; queue 3 messages, interrupt-now the 3rd, cancel the 2nd; inspect SEL + `gateway.log` + persisted JSONL | — | holds |

### Session 2 — Find-in-conversation + quote-reply

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `findMatches.ts`: pure scanner over `ChatTurn[]` (text segments via `turnText` + tool titles) → `{turnIndex, segIndex, start, end}[]`; unit tests (case folding, multi-match per turn, empty query) | `web/src/pages/chat/findMatches.ts` + colocated test | matcher is correct + fast on a 500-turn fixture (measure; <10ms) |
| T2.2 | `FindBar.tsx` + wiring: Cmd/Ctrl+F opens (chat-focused, session open; Esc/2nd press → browser find), count + next/prev cycling, active-match scroll via `turnNodes`, highlight via CSS Custom Highlight API with range-walk fallback; `aria-live` count | `web/src/pages/chat/FindBar.tsx`, `ChatPage.tsx` | find "docker" in a long chat → highlighted matches, 3/17 counter, Enter cycles + scrolls; markdown rendering untouched (highlight overlays, never re-parses) |
| T2.3 | Quote toolbar: `SelectionQuote` → two-action floating toolbar (Quote + Copy); attributed blockquote (`> **You said:** / > **{agent} said:**` from the enclosing turn via `turnNodes` ancestry) through the existing `quoteToComposer`; `selectionchange` positioning for keyboard/touch | `ChatPage.tsx` (`SelectionQuote`, `quoteToComposer`), `web/src/ui/SelectionPill.tsx` (multi-action variant) | select assistant text → toolbar floats → Quote inserts an attributed blockquote in the composer; works from keyboard selection; Copy copies plain text |
| V2 | Validation: long real session — find/cycle/jump across streamed + tool-card turns; quote from both roles; verify Cmd+F doesn't shadow the browser on non-chat pages; token-lint/theme pass | — | holds |

### Session 3 — Follow-up chips + smooth streaming

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `chat_followups.py` + `task-followups.md` prompt: `_maybe_followups` per C3 (gates: config, restriction, queue, error; `_bg` session; 20s cap; reject permissions; redact; `chat_followups` WS); hook beside `_maybe_auto_title` in the queue-empty finally branch; cancel-on-next-dispatch via `session._followups_task`; SEL `chat_followups` | `src/personalclaw/dashboard/chat_followups.py`, `chat_runner.py` (~:2853), `config/prompts/task-followups.md`, `dashboard/state.py` (task slot) | a completed turn emits 2-3 chips over WS; incognito/temporary sessions and errored turns emit nothing; with no model bound the turn completes normally and no event fires (tests mirror `test_chat_auto_tag.py`) |
| T3.2 | `followup_chips` config field, 5-point wired (dataclass+`_meta`, `load`, `to_dict`, PUT `_allowed`, ChatPanel toggle) | `config/loader.py`, `handlers/files.py`, `web/src/lib/api.ts`, `pages/settings/ChatPanel.tsx`, `tests/test_config_roundtrip.py` | round-trip test green; toggle off → no generation task is even created |
| T3.3 | `FollowupChips.tsx`: chips under the last assistant turn's actions; click = `insertPrompt`, double-click/send-glyph = `send()`; dismissed by typing (3+ chars), sending, session switch, or a new stream; enter-animation per motion tiers | `web/src/pages/chat/FollowupChips.tsx`, `ChatPage.tsx` (WS case + render) | chips appear ~1-3s after a reply, never block or shift the composer; click fills, double-click sends; any activity dismisses |
| T3.4 | Word-boundary snapping in `CoalescerCore.tick()` (back the reveal cursor to the last whitespace/CJK boundary within budget; skip snapping when backlog > `MAX_LAG`); extend the pure-core unit tests (never falls behind, snaps mid-word, catch-up overrides) | `web/src/pages/chat/useStreamCoalescer.ts` + `useStreamCoalescer.test.ts` | streaming reveals whole words at a steady cadence; a large paste still drains within frames (existing MAX_LAG tests stay green) |
| T3.5 | `stream_reveal` config field, 5-point wired + ChatPanel `SegPills` (smooth\|immediate); `ChatPage.tsx` threads it into `useStreamCoalescer`'s `opts.immediate` (reduced-motion/`animSpeed===0` still force immediate) | same file set as T3.2 | toggling to immediate renders chunks instantly; smooth restores the reveal; round-trip test green |
| V3 | Validation: real streamed turns on both reveal modes; chips on persistent vs incognito vs no-model (unbind); confirm the chips call appears in the guardrails audit + budget meter | — | holds |

### Session 4 — Screen-snip + polish/validation (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | `SnipOverlay.tsx`: getDisplayMedia one-frame grab (tracks stopped immediately) → frozen-frame crop overlay (drag, Esc) → PNG Blob → `api.uploadFiles` → `attachedPaths`; feature-detect (hidden where unsupported) | `web/src/pages/chat/SnipOverlay.tsx`, `ChatPage.tsx` ("+"-menu entry, all platforms) | on Linux/Windows browsers: pick surface → crop → attachment chip appears → send injects OCR'd content (upload-time extraction); capture stops the instant the frame is grabbed |
| T4.2 | Capture-path selection + desktop seam: macOS keeps native `screencapture -i` (`captureScreenshot`) with SnipOverlay as fallback on error/denial; a `// DESKTOP-CAPABILITIES S2 seam` comment + doc note marks where the Electron `screen_capture` bridge replaces getDisplayMedia later | `ChatPage.tsx` | mac: native snip unchanged; non-mac: browser path; the seam is documented, not built |
| T4.3 | Polish sweep: FindBar/chips keyboard traversal + focus management + `aria-live`; SEL audit for all seven (rewind, interrupt, chips logged; snip rides the existing upload SEL); mobile behavior (find bar docked, chips wrap, snip hidden on iOS Safari) | S1-S3 surfaces | AT walk-through clean; SEL shows one event per security-relevant action; mobile degrades sanely |
| T4.4 | Docs + CHANGELOG: chat-surface guide section for the seven mechanics; class-B CHANGELOG entry for the `rewound` message field with `personalclaw snapshot` advice | `docs/`, `CHANGELOG.md` | a user can discover every mechanic from the docs; release notes carry the clean-break notice |
| V4 | Validation: the full seven-mechanic pass as a user from the dev-home frontend (UI, console, network, backend logs, persisted state) + `make lint` · targeted `pytest` · `make test` · `cd web && npm run typecheck && npm test && npm run build` | — | holds |

## Owner tasks (real world)
1. **Dogfood rewind semantics** — confirm "one active timeline per slot, restore = fork" matches how you actually use edit-resend before S1 ships; the sibling alternative (in-place timeline switcher) is deliberately not built.
2. **Approve the chips default** (`followup_chips: true`) — it's one extra cheap-model call per interactive turn on your token budget; flip the default to `false` here if that's unwelcome, the mechanic works either way.
3. **Tune `stream_reveal`** on your real instance — decide whether `smooth` earns the default or `immediate` should ship as default with smooth opt-in.
4. **Sanity-check the tail cap** (5 rewind snapshots per message, mirroring `_MAX_VARIANTS`' spirit) against your longest real sessions.

## Risks & open questions
- **Provider reset cost on rewind** — `sessions.reset` kills the ACP subprocess/native runtime and the next turn pays `compress_thread_history`; on a huge session that's a slow first-token. Acceptable (rewind is deliberate, not per-turn); the warm pool (`session.pool_size`) softens ACP respawn. Measure in V1.
- **`rewound` transcript growth** — tails nest full message dicts (tool results included). The cap (5) plus `_save_session_to_history`'s full-rewrite keep it bounded, but a tool-heavy tail could still be large; if V1 shows bloat, store tails with tool-result bodies elided (keep refs — `tool-result/{rid}` already lazy-loads).
- **Highlight-over-markdown fragility** — the find highlighter must never re-render markdown (K44-class regressions). CSS Custom Highlight API is the safe path (Chrome/Safari/FF current); the range-walk fallback needs a test against code blocks + streamed turns.
- **Chips quality on a lite model** — `personalclaw-lite` may produce generic chips. The prompt gets the last exchange only (recency beats breadth); if quality disappoints, the fix is prompt iteration, not a bigger binding (soul guardrail: no new model bindings).
- **getDisplayMedia self-capture UX** — browsers may hide the current tab or prompt awkwardly for "this screen"; the crop-a-frozen-frame flow sidesteps most of it, but per-browser quirks need the V4 matrix (Chrome/Safari/FF, mac/Linux). iOS Safari has no getDisplayMedia → hidden (accepted; MOBILE-COMPANION owns mobile capture).
- **Open:** should the rewind divider offer one-click "restore this tail as the active timeline" (swap rather than fork)? Deferred — it reintroduces branch bookkeeping this plan deliberately avoids; DISCOVERY-file if dogfooding demands it.
- **Open:** does find want to search *collapsed* tool outputs (raw refs fetched lazily via `/tool-result/{rid}`)? v1 searches only what's rendered; noted for a v2 if misses hurt.

## Amendment (2026-07-26 — gap analysis round 2, owner decisions)

**Optimizer polish (owner: "improve the Optimize button, don't build new").** Code recon (2026-07-26): the mechanic is healthier than the ask assumed — polish, not surgery. (1) **The FE already sends context**: both `optimize()` (ChatPage.tsx:1267) and `optimizeAndSend()` (:1280) build `turns.slice(-10).map(tn => turnText(tn).slice(0, 200)).join('\n')` and pass it to `api.optimizePrompt(t, ctx)` → POST `/api/optimizer/optimize` (`api.ts:1480`), and the handler (`dashboard/handlers/optimizer.py`) wraps `context[-2000:]` in a `<context>` block. The real gap is context QUALITY: 200 chars per turn with no role labels, and the tail-truncation to 2000 chars can decapitate it. (2) **An UNCHANGED contract already half-exists**: the bundled prompt (`config/prompts/task-prompt_optimizer.md`) rule 2 says return already-good prompts unchanged and the handler treats a literal `UNCHANGED` reply as no-change — but the prompt never *tells* the model to reply with the `UNCHANGED` token, so "already good" costs a full echo and can differ by whitespace/quoting. (3) **One-step undo already exists**: `preOptimize` state + the "Optimized — revert to original" button (ChatPage.tsx:1778), cleared on the next edit — the gap is only the `/optimize <prompt>` one-shot path, which sends immediately with no revert (deliberate: the bubble shows original + optimized; rewind/edit-resend covers regret there). So: richer context, explicit UNCHANGED contract, keep the undo as-is.

One task row, landing in **S4** (the polish session); **no count change**.

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.5 | Optimizer polish: (a) richer FE context — role-labeled turns (`user:`/`assistant:`), last ~10 turns, ~400 chars/turn, assembled newest-last so the handler's `[-2000:]` keeps the most recent exchange intact (verify against the 2000 cap; no backend change unless the cap itself proves the bottleneck — then raise it in the same task, one knob); (b) explicit already-good→`UNCHANGED` contract in the bundled prompt (instruct the exact token; handler already honors it) so good prompts cost near-zero output and never churn on whitespace; (c) confirm + test the existing revert path (preOptimize survives until next edit; composer keyboard focus returns after revert) rather than building a second undo | `web/src/pages/ChatPage.tsx` (`optimize`/`optimizeAndSend` ctx builder), `src/personalclaw/config/prompts/task-prompt_optimizer.md`, FE test beside the composer tests | an already-specific prompt returns `changed:false` via the `UNCHANGED` token (fixture asserts the short reply); a vague prompt referencing "that file from earlier" optimizes correctly because the labeled context carries the referent; revert restores the exact pre-optimize draft |

## Execution log

- **2026-07-27 — S1-3 DONE (Wave 2).** Shipped Sessions 1-3 as one branch
  (`feature-chat-craft`), one conceptual commit. **S1 (T1.1-T1.3):** true rewind via
  fork-and-swap — `edit-resend` gains `rewind:true` (snapshots the discarded tail onto
  the edited user message's `rewound` chain, capped 5, truncates, `sessions.reset` so
  the next turn rebuilds context from the truncated transcript, `chat_rewound` WS,
  SEL `chat.rewind`); tolerant reads across all three rehydration paths +
  `_save_session_to_history` + `_prepare_messages` (redacted). FE: Rewind action on
  non-last user turns (confirm), `RewindDivider` (kept-count + read-only tail
  disclosure) + restore-as-fork via a new `POST /fork-rewound` (restore = fork, never
  swap — the plan's Open-question resolved in favor of the stated non-goal). Queue
  manners: wired the orphaned `/interrupt` — `queue_promoted` WS echo + "Interrupt now"
  on `QueueStack` cards. **S2 (T2.1-T2.3):** `findMatches.ts` pure scanner (+8 unit
  tests, <10ms on 500 turns); `FindBar.tsx` (Cmd/Ctrl+F, count/cycle/scroll, CSS
  Custom Highlight API paint over live text nodes — never re-parses markdown, range-walk
  is the highlight, `::highlight(pc-find)` token rule); quote toolbar via new
  `SelectionToolbar` primitive (Quote + Copy, attributed blockquote, `selectionchange`
  positioning for keyboard/touch). **S3 (T3.1-T3.5):** `chat_followups.py` +
  `task-followups` bundled prompt (2-3 chips via `_bg` lite session, gated on
  config/restricted/queue/error, silent with no model bound, cancel-on-next-dispatch
  via `session._followups_task`, SEL `chat_followups`); `FollowupChips.tsx` (built from
  QuietButton+IconButton primitives); word-boundary + CJK snapping in
  `CoalescerCore.tick()` (+4 unit tests; catch-up past `MAX_LAG` still wins);
  `followup_chips` + `stream_reveal` config fields wired 5-point (round-trip green) +
  ChatPanel rows. DoD: `make lint` clean (black/isort/flake8/mypy), 43 targeted +
  full backend suite, web typecheck + 251 tests (primitive-adoption + doc-drift
  ratchets satisfied by real primitive adoption, not baseline bumps), reference
  regenerated for the new route. **S4 deferred** (Wave 3 — screen-snip + polish + T4.5
  optimizer). Class-B `rewound` field lands as a plain clean break under the pre-1.0
  banner (tolerant reads, no migration; CHANGELOG note + snapshot advice).

---

## Amendment (2026-07-29 — owner-approved: Branch, and the plan gate for ordinary chat)

**Provenance.** A design gap analysis (2026-07-28/29). Two mechanics were approved for planning: a **Branch** mechanic (fork a session at any message with isolated inherited context) and a **plan-then-act gate**. Both turn out to be **much closer to done than the analysis suggested**, and one of them **directly contradicts an explicit non-goal in this plan's S1**. This amendment resolves that contradiction rather than silently overriding it.

### (a) Branch — reopening a closed decision, deliberately

**S1's stated non-goal (line 99):** *"no timeline-branching UI (tail restore = fork)"*, with the S1 design reasoning *"no in-place timeline branching UI; one active timeline per slot, history preserved."* That was a sound call for the *rewind* mechanic — a rewound tail is superseded content, and offering an in-place branch UI over it would have muddied "one active timeline per slot."

**Why it is worth reopening as a separate mechanic:** Branch is not rewind. Rewind *replaces* a timeline (the old tail becomes superseded); Branch *duplicates* one (both timelines stay live and equal). The framing is the useful part: Branch treats accumulated understanding as a reusable asset — you can spend the same context many times without depleting it. The documented use pattern is a long-running "record" thread as living project memory, with branches spun off to produce individual deliverables — which is a genuinely different workflow from correcting a mistake.

**What already exists (verified — this is a small change, not a subsystem):**
- `dashboard/chat_fork.py` (303 LOC) — `POST /api/chat/sessions/{session}/fork` **already forks at `at_message_index` (inclusive)** into a new session, copying messages with redaction applied, setting `forked_from`, SEL-audited, app-ownership-checked, and guarded by `_MAX_SESSIONS_FOR_FORK` (429 when at cap).
- `POST /api/chat/sessions/{session}/fork-rewound` (`chat_fork.py:202`) — S1 already added tail-restore-as-fork.
- So the backend primitive for Branch **is the shipped fork endpoint**. The gap is purely the **frontend affordance and the lineage breadcrumb**.

**Contract (deliberately minimal):**
- A **branch icon on hover of any past message** (not just user turns — branching from an assistant answer is the common case: "take this analysis in two directions"), calling the existing fork endpoint with that message's index.
- A **"Session branched" confirmation**, and a **"Branched from" breadcrumb** on the child linking to its origin. `forked_from` is already persisted — the breadcrumb is a read of existing state.
- **Branch-a-branch works for free** (a fork of a fork is just another fork) and **the same message may be branched repeatedly** — both are properties of the existing endpoint; the tasks only need to not prevent them.
- **Isolation is already correct:** the fork copies messages into a new session with its own key, so each branch has its own provider session and context. No new isolation work.
- **Explicit non-goals retained:** no tree/graph visualization, no cross-branch diffing, no merge. One active timeline per slot still holds — a branch is a *new slot*, which is exactly why it doesn't violate S1's principle. **This amendment does not change rewind's semantics at all.**
- **Note the honest limitation:** a Branch mechanic cannot meaningfully copy every session type (for example, builder-style sessions where the state isn't a plain transcript). Ours should degrade the same way where a session type can't be meaningfully copied (app-scoped and non-persistent sessions already refuse forking — reuse those refusals rather than inventing new ones).

### (b) Plan Mode for ordinary chat — the gate exists but is loop-bound

**What exists, and it is good:** `src/personalclaw/planning/` implements a stepwise gated walkthrough — ordered `PlanStep`s, a `StepStatus` state machine (`planning/session.py:25-42`: `awaiting_review` → **approve** advances, **comment** returns it to `running` for a re-draft), `PlanComment` accumulating across re-drafts, and the final approved step projecting into execution. Its own docstring: *"blocks on a user gate: the user approves it (advance) or comments (re-run the step with the feedback)."*

**The gap, precisely:** it is reachable **only** through loop routes — `POST /api/loops/{id}/plan/start|retry|approve|comment|edit` and `GET /api/loops/{id}/plan-session` (`dashboard/handlers/loop_routes.py:734-790`). There is **no chat entry point**. So a user in ordinary chat cannot say "plan this before you touch anything," which is exactly where a chat-side plan gate belongs.

**Design clauses worth adopting** (these clauses are the substance):
1. The plan is an **editable Markdown document** — a full Markdown document with goals, steps, and constraints — that the user can rewrite by hand or ask to be revised, and once confirmed **that plan becomes the source of truth.**
2. A **hard no-execute guarantee**: the agent will not start building until the user confirms or dismisses the plan — never before approval.
3. **Manual-only, by design** — the plan gate never activates automatically; quick tasks stay quick.
4. **Mid-task re-planning:** activate it while work is in flight and the agent stops, plans the next phase, and waits.

**Contract:** a chat-side entry to the **existing** planning walkthrough — not a second planner. Composer affordance (a mode/slash entry beside the existing task-mode pills), the plan rendered as an editable Markdown artifact, approve/comment reusing `planning/session.py`'s state machine verbatim, manual-only activation, and the no-execute guarantee enforced by the **existing** task-mode gate (`plan` mode is already read-only and enforced at the tool gate — `task_modes.py`), not by prompt instruction. **Do not build a new gate; bind the existing one to chat.**

### Amendment task table (extends this plan; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| F1.1 | Branch affordance: hover branch icon on ANY past message (user or assistant) → existing `POST .../fork` with that index; "Session branched" confirmation; navigate to the child | `web/src/pages/ChatPage.tsx`, `web/src/pages/chat/MessageActions.tsx`, `web/src/lib/api.ts` | branching from a user AND an assistant message both work; the same message branches repeatedly; a branch of a branch works; no primitive-adoption ratchet trips (use existing `ui/` primitives) |
| F1.2 | "Branched from" breadcrumb on a forked session (reads the already-persisted `forked_from`), linking to the origin; degrade cleanly for app-scoped/non-persistent sessions using the fork endpoint's EXISTING refusals | `web/src/pages/ChatPage.tsx` (session header), tests | the child shows its origin and the link lands on it; a session at the fork cap surfaces the existing 429 as a readable message; a non-forkable session hides the affordance rather than erroring |
| F1.3 | Chat entry to the EXISTING planning walkthrough: composer affordance, plan rendered as an editable Markdown artifact, approve/comment routed through `planning/session.py`'s state machine, **manual-only**, no-execute enforced by the existing `plan` task mode | `web/src/pages/ChatPage.tsx` composer, a chat-side planning route beside `loop_routes.py`'s pattern, `src/personalclaw/planning/` (wiring only — no new state machine), tests | a chat plan can be edited by hand, revised by request, approved, and only then executes; a mutating tool is denied while awaiting approval (test asserts the gate, not the prompt); quick tasks are unaffected (no auto-activation) |
| F1.4 | Mid-task re-planning: activating the plan gate during an in-flight turn stops at the next safe boundary and waits for approval, reusing the existing soft-stop/steer machinery rather than a new interrupt path | the chat runner's existing stop/steer seam, tests | activating mid-turn parks the run awaiting approval without losing the transcript; approving resumes |
| VF | Validation as a user: branch a long conversation from an assistant message, take the two branches in different directions, confirm the breadcrumb on each and that the original is untouched; branch a branch; then plan-gate a fresh request — edit the plan markdown by hand, approve, confirm execution follows the edited plan and that a mutating tool was denied before approval; activate the gate mid-task; full local gate incl. web typecheck/test/build | — | holds |

### Risks
- **Reopening an explicit non-goal.** Documented above rather than glossed: Branch is admitted as a *distinct mechanic on a new slot*, and rewind's "one active timeline per slot" principle is untouched. If an executor finds themselves adding timeline-branching UI *within* a slot, that is out of scope (escalation E6).
- **Session proliferation.** Cheap branching plus `_MAX_SESSIONS_FOR_FORK` means users will hit the cap. The cap is existing behavior and stays; F1.2 only requires the refusal be *readable*. SESSION-MANAGEMENT (50) owns lifecycle/archival — if branching makes the cap a real irritant, that is a DISCOVERY for that plan, not a cap raise here.
- **Two planners is the failure mode for (b).** The planning package is the authority; the chat entry is wiring. Any task that adds plan state, a second state machine, or a prompt-enforced (rather than tool-gate-enforced) no-execute guarantee is defective.
