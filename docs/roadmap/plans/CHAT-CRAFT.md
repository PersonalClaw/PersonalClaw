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
**S4a (screen-snip) DONE 2026-08-16** as atom `CC-4` — `ui/SnipOverlay.tsx` + the shared
`ui/composer/displayCapture.ts` (the app's ONE `getDisplayMedia` call site, converged with MI-4's
screen share). S4's polish/docs/validation wrap-up (`CC-6`) and T4.5 optimizer polish (`CC-5`) are
still open. The 2026-07-29 amendment (Branch affordance F1.1/F1.2 + the chat entry to the planning
walkthrough F1.3/F1.4) is **NOT started**.
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

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md); new-route errors use the §2.2 envelope — existing routes keep their `{"error": "<msg>"}` shape, imitate-the-neighbor)

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
- **Gate/migration:** none — clean break under the pre-1.0 banner for the `rewound` field (tolerant reads; old sessions unchanged); everything else is additive config/UI.
- **Explicit non-goals:** no timeline-branching UI (tail restore = fork), no queue reorder endpoint (promote-via-interrupt only), no cross-session find (plan 50 owns that), no always-on capture, no new model bindings.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

- **2026-08-16 — CC-4 DONE (S4a screen-snip).** Branch `feature-cc4-screen-snip`, one
  commit. Shipped: `ui/composer/displayCapture.ts` (the shared display-capture module),
  `ui/SnipOverlay.tsx` + `.doc.ts` (the crop step), the composer entry renamed to
  **"Capture screen area"** with provider routing, and the mac-path attachment fix below.

  **MI-4 convergence decision: ONE acquisition, two products — extracted, not duplicated.**
  MULTIMODAL-IO's MI-4 had already landed `useScreenShare` (a display stream held open for
  a session, one budgeted frame per send, in memory for a single turn). CC-4 wants the
  opposite shape: one frame, capture stopped immediately, cropped, uploaded as an ordinary
  attachment. Different products, same acquisition — so `getDisplayMedia`, the offscreen
  `<video>` frame source, the canvas draw and the track teardown moved into
  `displayCapture.ts`, and `useScreenShare` now consumes them. There is exactly **one**
  `getDisplayMedia` call site in `web/src`, pinned by a census test that also asserts its
  own non-vacuity (a third atom cannot quietly add a second acquisition). A second
  acquisition was considered and rejected: the two features differ in *lifetime policy*,
  not in how a frame is obtained, and the teardown is the one part that must never fork.
  `SHARE_MAX_EDGE` (1568) stays a share-only budget — a snip keeps native resolution,
  because softening text before OCR reads it is the one thing this pipeline must not do.

  **SnipOverlay premise: the atom's wording was stale; the component is NEW here.** The
  `done_when` reads "mac keeps native screencapture -i with SnipOverlay as fallback" as
  though `SnipOverlay` existed. It did not — Contract C5 *specifies* it (`SnipOverlay.tsx`)
  and CC-4 is where it gets built. Recorded rather than assumed: nothing was missing, the
  clause is forward-referencing.

  **DISCOVERY → fixed in scope: the macOS native capture's attachment chip was a lie.**
  `POST /api/screenshot` writes into `~/.personalclaw/screenshots/`, but
  `chat_runner._inject_attachment_content` only extracted paths under `uploads/`. So the
  native path produced a visible attachment chip whose content the model was never told
  about — and its path is not in the prompt text either, so the agent could not even go
  read it. With one composer entry now selecting between two providers, that made the same
  button mean two different things, so the roots list gained `screenshots/` (and the
  prefix test gained a separator, so a `uploads-old/` sibling can no longer masquerade as
  an attachment root). `api_screenshot` also kicks off extraction at capture time, the same
  head start an upload gets. Four tests cover it, one end-to-end through the real
  extraction graph. **DEVIATION:** this is a Python change beyond the atom's FE scope,
  taken because "one decision point, two providers" is false if the two providers produce
  different outcomes behind one label.

  **DISCOVERY (fixed, real-browser-only): the crop preview's dim/bright inversion.** The
  selected region is drawn as a second copy of the frame clipped to the selection. With a
  percentage `margin-top` offset it rendered *exactly as dark as the area outside it* —
  because percentage margins resolve against the containing block's INLINE size, so a
  90px-tall crop was displaced by 300% of its *width*. jsdom computes no layout, so no
  component test could have seen it; it showed up in the first real screenshot. Fixed with
  a `transform: translate()` (percentages there resolve against the element's own box) and
  converted into a testable claim: `cropViewStyle()` is a pure function with a rail that
  asserts the offset is a transform and never a margin.

  **DEVIATION (small):** the frame is capped by deriving its width from a 58vh budget.
  A 4K capture otherwise filled the sheet and pushed the size readout and the Attach
  button out of view — a crop UI that hides its own confirm button is not one.

  **Drove as a user** (isolated home `/private/tmp/cc4-home`, port 10077, real Chrome):
  · macOS/native leg — the entry appears as "Capture screen area", clicking it really
  shells out (`screencapture -i /private/tmp/cc4-home/screenshots/screenshot_*.png`
  observed in the process table); cancelling it leaves no chip, no error line and does NOT
  fall through to the browser picker. The OS crosshair itself could not be *completed* from
  a browser-driven session (no way to drive an OS-level overlay), so the completed native
  capture is covered by the pytest that runs the real extraction graph on a PNG in
  `screenshots/`, not by a click. · browser leg — this machine is mac, so the platform
  predicate was **temporarily overridden in source** (`usePlatform() && 'linux'`, rebuilt,
  reverted before commit) and `getDisplayMedia` was stubbed to return a real
  `canvas.captureStream()`. Everything downstream was the real code: overlay mounts with
  focus inside the dialog, **the track was `ended` and `stream.active === false` before the
  overlay was even touched** (`stop()` observed once per acquisition — a real-browser check,
  not a jsdom one); Alt+ArrowLeft moved the readout 1280→1256; Escape closed it leaving no
  chip and no error; a drag over the "crop me: ACORN-7742" line gave 760×90 px; Attach
  produced `uploads/<32-hex>_screen-snip-*.png`, **760×90 on disk, mode 0600**, and an
  ordinary removable attachment chip. Zero console errors.

  **Honest limit on "OCR'd content".** `GET /api/attachment-extract` for the uploaded snip
  returned the structural descriptor (`Image: … (760×90, PNG, 15 KB) — no extractable text
  content.`), because OCR is a model-backed node (`ocr`/`vision`, use case
  `image_modality`) and no vision model is bound in that dev home. The wiring is proven;
  the OCR *text* is a property of the configured model, not of this atom. Noted so CC-6
  does not re-litigate it. (Cosmetic, unfixed: `_structural_descriptor` prints the stored
  filename with its random prefix rather than `display_name`.)

  **Falsifications** (mutate the live line, run the covering test, restore): removing
  `stopStream(acquired)` from `grabOneFrame`'s `finally` →
  `expected [ +0, +0, +0 ] to deeply equal [ 1, 1, 1 ]`; deleting the darwin branch from
  `chooseCaptureProvider` → `expected 'browser' to be 'native'`; dropping the overlay's
  Escape handler → `expected "spy" to be called 1 times, but got 0 times`; dropping
  `screenshots` from the injection roots → `assert 'NATIVE SNIP TEXT' in '…snip.png\n\nBROWSER
  SNIP TEXT…'`.

  **Gate:** `npm ci`, web typecheck clean, **304 files / 3148 tests green** (repo-wide
  ratchets included — the `aria-modal` census in `dialogFocusContract.test.tsx` gained
  `ui/SnipOverlay.tsx`, and `screenShareIndicator.test.tsx`'s track-stop rail was repointed
  at the shared module rather than weakened), web build clean (92 ui-docs components),
  `make lint` clean, 100 targeted pytest passed with the real-home rail reporting
  `~/.personalclaw unchanged`. `docs/design/consistency-audit.json` restored after the web
  runs dirtied it (pre-existing drift on `main`).

  **Not done here, deliberately:** the DESKTOP-CAPABILITIES S2 bridge (this atom leaves the
  documented seam at `grabOneFrame`, naming `window.pclawDesktop.capabilities` /
  `screen_capture` / `probe()` / `request()` as the swap point — a comment, not a `TODO`,
  and not an implementation); the a11y/SEL/mobile sweep and the docs guide, which are CC-6.

- **2026-08-17 — CC-7 DONE (Amendment (a) Branch — F1.1, F1.2, VF branch portion).** Branch
  `feature-cc7-branch-mechanic`, one commit. Shipped: `web/src/pages/chat/branchLineage.ts`
  (new pure module), the `visibleIndex` stamp in `hydrateTurns`, `forked_from` +
  `forked_from_title` on the session-detail endpoint, the "Branched from" header chip, the
  "Session branched" confirmation, and the Branch vocabulary on the message action bars.

  **PREMISE CORRECTION: F1.1's affordance already shipped, and it was WIRED TO THE WRONG
  INDEX.** The amendment reads as though the branch affordance had to be built. It did not
  exist as a gap — `MessageActions.tsx` already rendered a `GitBranch` "Fork from here"
  button on **both** roles, `canFork` already hid it on non-persistent sessions, and
  `ChatPage.forkAt` already called the endpoint and navigated to the child. So the atom's
  first clause was satisfied on arrival. What was NOT satisfied is the clause it depends on:
  *"calls the existing POST .../fork **at that index**"*. `forkAt` passed the turn's array
  position, on the strength of an in-code comment asserting *"Here every turn is
  user/assistant, so it's just the turn index."* **That comment is false on any real
  transcript.** `hydrateTurns` performs two collapses — it drops native ReAct loop
  re-injections (`chatTypes.ts` "loop re-injection") and merges consecutive assistant
  messages into one turn via `lastAssistant()` — while the backend's visible list
  (`chat_fork.py:119`, `role in ("user","assistant")`) counts every one of them. The two
  indices therefore agree only on a transcript with no tool use and single-message answers,
  and the gap COMPOUNDS: each collapse adds one. Measured on the validation transcript
  (one re-injection + a three-message answer), clicking Branch on the final answer sent
  index 3 where the truth was 5 — and because the endpoint happily forks at 3, the user got
  a plausible-looking branch cut two messages early, with no error anywhere. Branching the
  merged answer was worse: the naive index 1 lands on the *re-injected user message*, so
  the "take this analysis in two directions" case — the amendment's own stated common case
  — produced a branch containing **no answer at all**.

  Fix: `hydrateTurns` stamps each turn with `visibleIndex`, the backend coordinate of the
  **last** message folded into it (last, not first, because `at_message_index` is inclusive
  — an assistant turn must carry its whole answer), and `branchIndexOf` reads it, deriving
  a coordinate for live WS-built turns from the nearest stamp. Verified live: branching the
  merged answer sent `at_index=4`, the user turn `at_index=5` (SEL `chat.session_fork`).

  **Where the breadcrumb comes from, and why it survives a reload.** `forked_from` was
  already persisted (`chat_persistence.py:581`) and restored on load, but **no endpoint
  served it** — so the only place a child could have learned its parent was the navigation
  that created it, which a refresh destroys. The session-detail endpoint now returns
  `forked_from` plus `forked_from_title`, resolved live-then-disk at read time; ChatPage
  reads it in the same effect that already restores `memory_mode`, which is exactly the
  reload path. Deliberately a read, not a copy: renaming the parent updates the breadcrumb
  (the child's own `"Fork of X"` title is the frozen copy, and stays frozen). Two flat
  fields rather than a nested object, imitating the neighbouring `memory_mode`
  (AGENTS.md: success envelopes imitate the neighbour). `forked_from_title: ""` with a
  non-empty `forked_from` means the origin is gone, which is what lets the chip degrade to
  unlinked text instead of a link into nothing.

  **No confirmation dialog, deliberately.** Branch is one click from a hover icon and a
  write, which normally argues for a confirm. It is right here because Branch DUPLICATES —
  it creates a new session and cannot overwrite anything in the current one. Rewind, which
  *replaces* a timeline, does confirm. The confirmation is therefore a post-hoc toast, and
  it goes through the shell `notify()` rather than ChatPage's inline strip because
  navigating to the child unmounts the surface that raised it.

  **DEVIATION (vocabulary): "Fork from here" → "Branch from here".** The amendment names
  the mechanic **Branch** and the breadcrumb **"Branched from"**; shipping a button labelled
  "Fork" beside it would split the user-facing vocabulary across one mechanic. Endpoint
  paths, `forked_from`, and the rewind mechanic's own "Restore as fork" (genuinely a
  restore-as-fork) are untouched — this is a label change, not a contract change.

  **DISCOVERY (not fixed — out of atom scope, no user-visible symptom found).** The fork
  endpoint reads history with `conversation_log.read_messages(...)` while session-detail
  uses `read_messages_chained(...)`. On a session whose history has been consolidated or
  rotated, the two see different message counts, so the FE coordinate (derived from detail)
  could miss the endpoint's list. Not observed in the drive, and changing what a shipped
  fork copies is a behaviour change beyond a frontend affordance atom. Filed here rather
  than patched.

  Also unchanged deliberately: `editResend` still passes the turn position as its index. It
  is a *fallback* there — the backend locates the target by `ts` first — and altering the
  rewind path's targeting is S1's contract, not this atom's.

  **Validated as a user** (isolated home, port 10288, seeded transcript containing both a
  loop re-injection and a three-message answer): branched from an assistant answer
  (`at_index=4`, full answer carried), from a user message (`at_index=5`, ends unanswered),
  the same answer a second time (distinct session, identical transcript, parent untouched),
  and a branch of a branch (breadcrumb reads "Branched from Fork of Q3 record thread" — the
  intermediate, not the root). Hard reload with cache bypass: breadcrumb still present. The
  branch button measured on the live DOM at effective opacity 0 at rest and 1 while focused
  (`focus-within:opacity-100`, read after a 300ms transition settle), `tabIndex >= 0`, not
  inside `aria-hidden`, accessible name "Branch from here" — so the hover affordance has a
  real keyboard route. Zero console errors across the drive. No primitive-adoption ratchet
  trip (the new chip uses the `ui/` `Button`).

---

- **2026-08-17 — CC-5 DONE (Amendment (2026-07-26) T4.5 — optimizer polish).** Branch
  `feature-cc5-optimizer-context`, one commit. Shipped:
  `web/src/pages/chat/optimizerContext.ts` (new pure module + 9 unit tests), the
  `UNCHANGED` instruction and a context-aware example in the bundled
  `task-prompt_optimizer.md`, and a boundary-aware context clip plus tolerant token
  detection in `dashboard/handlers/optimizer.py`. The plan's 2026-07-26 recon held up
  exactly: this was polish, not surgery.

  **The UNCHANGED contract was a live reader of an unwritten token.** The handler has
  honored `optimized.upper() == "UNCHANGED"` since it shipped, but nothing anywhere ever
  *asked* a model for that token — rule 2 said "return it unchanged", and, worse, the
  file's **final example demonstrated echoing the input verbatim**. Examples are the
  strongest instruction in a prompt file, so the one place the contract was shown taught
  its opposite: an already-good prompt cost a full echo, and then only avoided a spurious
  `changed:true` because of the whitespace-insensitive comparison further down. Rule 2 now
  names the exact token, the closing example answers `"UNCHANGED"`, and a test asserts the
  echo example is *gone* rather than merely that the token is present.

  **Fail direction for a non-token reply: toward keeping the user's draft, and the exact
  match was the dangerous version.** `_is_unchanged_reply()` reads the reply's first word
  after stripping quotes/emphasis/trailing punctuation, so `UNCHANGED.`, `**UNCHANGED**`
  and `UNCHANGED — already specific` all keep the draft. Measured on the pre-change check:
  a model that complies but adds a period made the handler return
  `{"optimized": "UNCHANGED.", "changed": true}` — the literal token pasted over the
  prompt the user wrote. A rewrite that merely *contains* the word ("leaving the public API
  unchanged") is still a rewrite; that case is asserted, because a substring check here is
  how an optimizer ships inert. **Unrecognizable prose is still accepted as a rewrite** —
  there is no way to tell "rewritten prompt" from "answer to the prompt" without a
  classifier, and refusing all prose would make the feature inert for any model with a
  preamble. Recoverability carries that case instead: `preOptimize` restores the exact
  draft, and `/optimize` records the original on the bubble.

  **DEVIATION (authorized by T4.5's own "then raise it in the same task, one knob"): the
  cap moved from 2000 to 4129.** Ten turns at ~400 chars cannot fit in 2000 — the
  arithmetic makes the cap the binding constraint, which is the condition the task named.
  `MAX_CONTEXT_CHARS` is now *derived* (`10 × (400 + len("assistant: ") + len("…")) + 9
  joining newlines`) from the same three numbers the composer budgets against, so
  "survives the cap" is true by construction, and a test reads `CTX_MAX_TURNS` /
  `CTX_TURN_CHARS` **out of the TypeScript file** rather than trusting the comment — the
  only honest way to hold one number across a language boundary. The FE test asserts the
  worst case is *exactly* 4129, not merely under it.

  **What the truncation drops is the whole point.** `context[-MAX:]` keeps the correct END
  but lands mid-line, so the oldest survivor arrives decapitated — a fragment attributed to
  nobody, which is worse than omitting it. `_clip_context()` snaps forward past that partial
  line and drops the turn whole; a blob with no line boundary at all (the loop composer
  passes `''` today, but an app could send anything) keeps the raw tail, since there is no
  attribution left to lose. That vacuity case is asserted so the snap can't quietly stop
  matching. One turn per line is load-bearing for this: embedded newlines are flattened in
  the composer, or the handler could cut mid-turn while still looking boundary-aligned.

  **Fence:** `ChatPage.tsx` is owned by CC-7's open branch, so the touch there is four
  lines and nothing else — one import, the two `ctx` builders (which were the *whole*
  200-chars-unlabeled assembly, now one call), and one `requestAnimationFrame` focus
  restore in `revertOptimize`, copied verbatim from the pattern the same file already uses
  in three places. Without it, clearing `preOptimize` unmounts the revert button and focus
  falls to `<body>` — you reverted in order to keep typing.

  **Validated as a user — and one leg is honestly NOT real.** A fresh dev home has **no
  model provider bound**, so a true optimize round trip cannot resolve. Nothing here claims
  a live model obeyed the new instruction; the model's half was stubbed in the page. What
  IS real, driven on the isolated home (port 10366, seeded five-turn transcript about
  `src/net/client.py`): the gateway serving the built SPA, the transcript hydrating all
  five turns, and the composer's Optimize button issuing the real request. **The request
  body carried exactly the intended context** — five lines, every one matching
  `^(user|assistant): `, chronological with `user: ok noted` last, 412 chars. Drafting
  "add a test for that file from earlier" and optimizing replaced the draft; the
  "Optimized — revert to original" control appeared; clicking it restored the exact
  pre-optimize string and left `document.activeElement === .cm-content`. A stubbed
  `changed:false` reply (what the handler returns for a bare `UNCHANGED`) left the draft
  untouched and raised no revert control — the correct no-op. Zero console errors across
  the drive. **The token contract's plumbing is proven by fixtures; the prompt's
  persuasiveness is not, and cannot be by these means** — the prompt-text assertions only
  prove the instruction is present, exact, and no longer contradicted by its own example.

  **A stale service worker nearly faked the focus falsification.** Removing the focus line
  and rebuilding produced a page still serving `index-f50fSUIN.js` while `dist/index.html`
  pointed at `index-DEv5WFbo.js` — the SW cache had 510 assets and served the OLD bundle,
  so the "mutated" run would have reproduced the passing result and read as the line being
  unnecessary. Caught by comparing the loaded script hash against `dist/index.html`. After
  unregistering the SW, clearing caches and a cache-busted document URL, the mutated bundle
  loaded and `document.activeElement` was `BODY` after revert. The restored build's hash
  returns to `index-f50fSUIN.js`, which is what the earlier passing run was on.

  DoD: `make lint` clean, `tests/test_optimizer.py` 30 passed, web typecheck + 3498 tests
  (342 files) + build green. Five falsifications recorded: reversed newest-last → 5 FE tests
  red (`expected [ 'user: NEWEST', …(2) ] to deeply equal [ 'user: OLDEST', …(2) ]`);
  head-truncation → 3 backend tests red (`assert 'turn19 ' in 'assistant: turn1 zzz…'`);
  labels dropped → 8 FE tests red; tolerant token check reverted to exact-match → 3 contract
  cases red, and the handler returns `{"optimized": "UNCHANGED.", "changed": true}`; focus
  line removed and rebuilt → `document.activeElement` is `BODY` after revert on the live
  DOM.

- **2026-08-18 — CC-6 DONE (S4 wrap-up — T4.3 + T4.4).** Branch `feature-cc6-chat-wrapup`,
  one commit. The seven mechanics, enumerated from `:41`-`:47` before starting: (1) true
  rewind, (2) queue interrupt-now, (3) find-in-conversation, (4) quote-reply, (5) follow-up
  chips, (6) smooth streaming reveal, (7) screen-snip.

  **a11y — `aria-live` EXISTED and said the wrong thing.** `FindBar`'s counter carried
  `aria-live="polite"`, so any rail that greps the attribute passed — but its content was the
  glyph `3/17`, read out as digits and a slash, and `0/0` for no result. The counter is now
  `aria-hidden` beside a worded `role="status"` sibling mounted from first render
  (`findAnnouncement()` → `Match 3 of 17` / `No matches`). Deliberately NOT `ListControls`'
  `ResultAnnouncement`: its `No matching ${noun}` template renders "No matching **matches**"
  with find's honest noun, and it has no concept of WHICH match you are on, which cycling
  must re-announce. The chips had the same shape of gap — they arrive from a WS event 1-3s
  after the reply, a purely visual change — so `followupAnnouncement()` fills a second
  always-mounted region in `ChatPage` (separate from `srAnnounce` so streaming narration and
  chips arrival cannot overwrite each other), cleared on dismissal so no stale claim survives.

  **Keyboard traversal — two real gaps, driven not declared.** Escape was bound on the INPUT
  only, so from Previous/Next/Close (three of the bar's four tab stops, each keeping its tab
  stop by `IconButton` design) the one key that leaves a transient bar did nothing → moved to
  the container. Closing dropped focus on `<body>` because the bar autofocuses its field and
  nothing handed focus back → mount captures `document.activeElement` and restores it on
  unmount (guarded on `isConnected`). Also `↑`/`↓` never cycled despite the S2 design text
  naming them — they moved the caret and the bar looked stuck on match 1.

  **PREMISE CORRECTION on mobile — the first justification was wrong, and the browser said
  so.** The claim written first ("at 390px the pill has nowhere to shrink to and the search
  field is squeezed out") is false. Measured in Chrome, both layouts at both widths:
  390px `w-fit` → bar **344px, FITS** (left 30, right 374 < 390), input 147px; docked → 358px,
  input 161px. So at iPhone-12-to-15 width the old bar was fine and docking is a 14px polish
  gain. At **320px** (iPhone SE 1) `w-fit` → 344px and **OVERFLOWS** (`left 0, right 344 > 320`,
  left gutter eaten); docked → 288px, fits, input 91px. The real finding is a **~344px
  intrinsic floor** (field + counter + three 28px buttons) that cannot shrink, so the fix is
  real below ~360px and cosmetic at 390px. All four places that carried the overstated claim
  (component comment, test comment, guide, CHANGELOG) were corrected to the measured numbers.
  Chips already wrapped (`flex-wrap`), so nothing was needed there.

  **Snip on iOS Safari — PARTIAL, and an owner taste call.** The done_when says "snip hidden
  on iOS Safari". `displayCaptureSupported()` correctly hides the BROWSER path where
  `getDisplayMedia` is absent, but `chooseCaptureProvider(platform, supported)` returns
  `'native'` whenever `platform === 'darwin'` **regardless of `supported`** — and `platform`
  is the GATEWAY's, not the browser's. So from iOS Safari pointed at a macOS gateway the entry
  is still offered and still works: `screencapture -i` runs on the Mac. That is the
  remote-access model working, not a control that can only fail, so hiding it would delete a
  working capability. Left as-is, documented precisely in the guide instead. Hidden as
  specified on a non-darwin gateway.

  **SEL audit — `tests/test_chat_craft_sel_audit.py` (15 tests), counted both directions.**
  Four of the seven have a security-relevant server action, each exactly one event and each
  non-zero: `chat.rewind` (1 per rewind; 2 rewinds → 2), `chat.fork_rewound` (1),
  `dashboard_interrupt` (1, with `dashboard_stop` == 0 so the two verbs stay distinguishable),
  `chat_followups` (1 per generation, `metadata.count == 2`). Snip rides the **existing**
  `upload.file` (1, `resources: files:1`) and the whole log after a snip upload is exactly
  `['upload.file']` — asserted against `chat.snip`/`snip`/`screen_capture`/`screenshot`/
  `display_capture`, none minted. End-to-end, one of each = **exactly four events**. Refusal
  paths log nothing (a 400 interrupt, a generation that parsed to no chips). Find, quote and
  the reveal are client-only, so zero is the CORRECT count — asserted structurally (no
  endpoint, no `api.`/`fetch(` in `findMatches.ts`/`FindBar.tsx`/`useStreamCoalescer.ts`/the
  quote path) rather than as a bare zero, which reads identically to a forgotten emitter.

  **Docs + CHANGELOG (T4.4).** New `docs/guides/chat-surface.md` — "Working inside a chat",
  all seven with where-to-find-it, the capability condition on snip, and a closing table of
  what each one records; linked from README's Documentation list. Class-B CHANGELOG entry
  under `### Changed` for the `rewound` message field: leads with what a user notices, names
  the clean break in both directions (old chats load unchanged; a new chat opened by an older
  PersonalClaw will not show retained endings), carries the `personalclaw snapshot` advice.

  **Falsifications** (mutate the live line, confirm it applied, observe the red, restore from
  a file copy): `findAnnouncement` returning `''` → 7 tests red
  (`expected '' to be 'Match 1 of 17'`); the rewind `log_api_access` duplicated → 3 red
  (`expected exactly 1 chat.rewind event, got 2`; `assert 4 == 2`); container Escape removed →
  `Escape from "Find in conversation" did not close the bar: expected "spy" to be called 1
  times, but got 0 times`; focus-return removed → `expected <body><div></div>`; `ArrowDown`
  dropped → `expected 'Match 1 of 2' to be 'Match 2 of 2'`; mobile docking disabled →
  `expected 'sticky top-2 z-30 flex items-center g…' to contain 'w-auto'`.

  **Also fixed, because it blocked the work:** jsdom ships no `matchMedia`, so rendering ANY
  `useIsMobile` consumer throws on first render (the hook reads the query unguarded). Added
  the stub to `web/src/test/setup.ts` beside the existing `ResizeObserver` one, matching
  nothing so it renders the desktop/full-motion/dark branch — which is exactly what every
  production reader already falls back to, so it changes no existing expectation. Installed
  only when absent and left configurable, so tests with their own stub still win.

  **Gate:** `make lint` clean (black 1759 files, isort, flake8, mypy 903 files);
  **`make test` 21907 passed / 30 skipped / 12 xfailed, exit 0**; targeted pytest 57 passed
  (SEL audit + `test_docs_lint_baseline` + rewind/interrupt/followups/upload); web typecheck
  clean, **368 files / 3733 tests green** (repo-wide ratchets included — no ratchet loosened),
  web build clean (95 ui-docs components). `docs/design/consistency-audit.json` restored after
  the web runs dirtied it — its `driftHits` stayed at 8 and `filesWithDrift` at 7, only
  `filesScanned` moved 512→513 for the new test file, so this change adds zero design drift.
  Real-home rail: `~/.personalclaw` untouched (validation ran against
  `.dev-home` on port 10471, `--seed demo-home`).

- **2026-08-18 — CC-6 remainder: five of six clauses re-verified SHIPPED, one gap closed,
  one PARTIAL carried forward.** Branch `feature-cc6-chat-wrapup-remainder`, one commit
  (`dcd91e35`). The atom was still `todo` after the entry above landed via a squash batch, so
  each done_when clause was re-measured against `main` rather than read off that entry. The
  seven mechanics, re-enumerated from `:41`-`:47`: (1) true rewind, (2) queue interrupt-now,
  (3) find-in-conversation, (4) quote-reply, (5) follow-up chips, (6) smooth streaming reveal,
  (7) screen-snip.

  **CONFIRMED SHIPPED (measured, not inferred):** *aria-live* — `FindBar.tsx:159` glyph is
  `aria-hidden` beside the worded `role="status"` at `:163`, and the chips region is live in the
  host at `ChatPage.tsx:2808` (`followupAnnouncement(streaming ? 0 : followups.length)`), not
  merely exported. *SEL* — `tests/test_chat_craft_sel_audit.py` 15 passed; its client-only
  assertions are structural with a real floor (`assert "api." not in body` over the
  `quoteToComposer` body at `:396`-`:403`), so a forgotten emitter cannot read as a correct
  zero. *Mobile docking* — `FindBar.tsx:140` switches on `useIsMobile()`, and that hook
  (`web/src/app/useIsMobile.ts:12-18`) is `matchMedia`-reactive with a `change` listener, so
  the docking is NOT mount-gated and survives rotation; chips wrap via `flex-wrap`
  (`FollowupChips.tsx:41`). *Docs* — `docs/guides/chat-surface.md` carries seven numbered
  sections plus the recording table, linked from `README.md:222`. *CHANGELOG* — the class-B
  `rewound` note with `personalclaw snapshot` advice is at `CHANGELOG.md:1768`-`1770`. That
  sits under `## [0.1.3]` rather than `## [Unreleased]`, which was checked and is **correct,
  not drift**: CC-1 landed 2026-07-27 and 0.1.3 released 2026-07-30, so the field shipped in
  that release.

  **The one real gap — chips keyboard traversal was a CENSUS, not a traversal.** The clause
  pairs "FindBar/chips", and the bar's half was genuinely driven (`userEvent.tab()` through
  four stops, Escape from every stop, focus return). The chips' half was
  `expect(getAllByRole('button')).toHaveLength(4)` — a role query finds elements `Tab` may
  never land on and says nothing about what activating one does. The load-bearing fact it
  could not see: the chip label sends on `onDoubleClick`, which has **no keyboard equivalent**,
  so the send glyph is the ONLY keyboard route to sending a suggestion. Had it lost its tab
  stop or its `onClick`, sending would have become mouse-only with every existing assertion
  still green. Three driven cases added to `findBarA11y.test.tsx` (18 → 21): Tab reaches label
  then glyph per chip asserted as an ORDER with a `not.toContain('body')` vacuity floor (an
  unfocusable tree would otherwise record four "passes"), Enter on the label picks without
  sending, Enter on the glyph sends exactly once without picking.

  **PARTIAL carried forward, deliberately not reversed: "snip hidden on iOS Safari".**
  `chooseCaptureProvider` (`web/src/ui/composer/displayCapture.ts:72`-`:79`) returns `'native'`
  whenever `platform === 'darwin'` regardless of `supported`, and `platform` is the GATEWAY's,
  so from iOS Safari pointed at a macOS gateway the entry is still offered. Re-examined rather
  than rubber-stamped, and the prior call holds for a reason that entry did not state: the same
  branch already routes **Chrome-on-Linux → macOS gateway** to the native path, so hiding it
  for iOS Safari alone would make the platform rule inconsistent with a CC-4 decision that has
  landed. It is honestly documented at `docs/guides/chat-surface.md:141`-`:151`, including that
  the crosshair appears on the Mac. Hidden as specified on a non-darwin gateway. Reading the
  clause literally, CC-6 is **DONE with one documented deviation**, not silently complete.

  **Falsifications** (mutate the live source, `grep -n` to confirm it applied, observe the red,
  restore from a `/tmp` file copy — never `git checkout`): glyph `onClick` → no-op →
  `expected "spy" to be called with arguments: [ 'run the tests' ] … Number of calls: 0`; label
  `onClick` also sends → `expected "spy" to not be called at all, but actually been called 1
  times`; label/glyph order swapped → `expected [ 'Send: draft the summary', …(3) ] to deeply
  equal [ 'draft the summary', …(3) ]` plus both focus assertions red, proving the ORDER (not
  just presence) is what is asserted. Tree clean after restore; the 13 benign pre-existing
  probe matches unchanged.

  **Gate:** `make lint` exit 0 (black 1789 files unchanged, isort, flake8, mypy 919 files);
  targeted pytest **61 passed** (`test_chat_craft_sel_audit`, `test_chat_rewind`,
  `test_chat_followups`, `test_rewind_to_turn_api`, `test_docs_lint_baseline`,
  `test_nav_resolve_links` — every path confirmed to exist first); web typecheck exit 0; full
  web suite from the repo root **412 files / 4154 tests passed** (global design ratchets
  included); web build exit 0. `docs/design/consistency-audit.json` regenerated by the web runs
  and deliberately left uncommitted. No real-home writes — no gateway was started this session.

- **2026-08-19 — `CC-8` DONE (amendment (b), F1.3 + F1.4).** Chat plan-mode entry bound to the
  EXISTING walkthrough. New `dashboard/chat_plan.py` (~470 lines) persists a
  `planning.session.PlanSession` for a CHAT owner — `project_id` carries the chat key — plus a
  small chat-side *binding* record (`resume_task_mode`, `parked`, `parked_messages`): turn
  bookkeeping, not plan state. Every transition routes through the shared module verbatim:
  `submit_artifact` (the plan-mode turn's reply becomes `artifact["markdown"]`), `edit_artifact`,
  `comment_step`, `approve_step`, and `current_step`/`is_complete` for "is the gate open".
  Six routes (`GET plan-session`, `POST plan/activate|edit|comment|approve|cancel`) mirror
  `handlers/loop_routes.py:820-960`'s read → mutate → write discipline and use the §2.2 error
  envelope. Turn-end hook: one line in `chat_runner.py`'s queue-empty branch beside
  `maybe_offer_check_work`. FE: a "Plan this first" `MenuRow` in the composer "+" menu and
  `ui/chat/ChatPlanGate.tsx` (the gate strip above the composer — Edit / Comment & redraft /
  Approve & run it / Cancel), reusing the already-shipped `PlanSession`/`PlanStep` wire types.
  `chat_utils.apply_task_mode` is now the ONE write path for a session's task mode (both the
  session posture and the runtime's), and `api_chat_task_mode` adopted it.

  **Two decisions worth naming.** (1) The task-mode pill now **refuses** to leave `plan` while a
  step awaits review (409 `plan_awaiting_approval`, exits are Approve and Cancel) — without it the
  no-execute guarantee was one click from decorative, since nothing else re-checks the walkthrough.
  (2) `ui/PlanningWalkthrough.tsx` is deliberately **not** reused for the chat gate: it is the
  full-PAGE walkthrough (TopBar, back contract, and a left rail streaming a *hidden planner
  session's* tool calls over the WS). A chat's plan is drafted by the chat's own visible turn, so
  the chat needs the gate half inline and none of the page shell. The shared *state machine* — the
  clause the plan actually names, and its "two planners is the failure mode" risk — is shared in
  full; only presentation differs. Recorded here rather than assumed.

  **Falsifications** (mutate the live line by index with a precondition assert so a no-op is
  impossible, `grep -n` to confirm, `py_compile`, observe the red, restore from a `/tmp` file copy
  — never `git checkout`): `task_modes.py:347` plan deny → `return ""` reddens the gate test with
  `assert 'plan mode' in 'out:'` (the tool *ran*); `chat_utils.py:109` runtime push → `pass` reds
  identically, proving the test rests on the `runtime.py:1020` call site and not on a session
  attribute; `planning/session.py:236` `edit_artifact` merge → wholesale replace → `KeyError:
  'structured'`; `planning/session.py:167` `approve_step` guard → `if True` → `assert 200 == 409`;
  park truncating one message → both park/resume transcript assertions red; `_dispatch` clearing
  the transcript first → the resume prefix assertion red; the turn-end hook auto-activating →
  `assert True is False` on the manual-only test. Seven mutations, seven reds, all five clauses
  covered. Tree clean after restore (md5-verified per file); the 13 benign pre-existing probe
  matches unchanged and none is ours.

  **Gate:** `make lint` exit 0 (black 1817 files, isort, flake8, mypy 928 files); targeted pytest
  **462 passed** across 10 files (each path `[ -f ]`-confirmed first: `test_chat_plan_mode`,
  `test_task_modes`, `test_native_runtime`, `test_dashboard_chat`, `test_loop_plan_walkthrough`,
  `test_config_roundtrip`, `test_chat_utils`, `test_chat_branch_mechanic`,
  `test_chat_runner_procedural_wiring`, `test_planning_runner`); full `make test`
  **22733 passed / 30 skipped / 12 xfailed**; web typecheck exit 0; full web suite
  **420 files / 4323 tests passed**; web build exit 0. `src/personalclaw/reference/{index,routes}.md`
  regenerated (`python -m personalclaw.manifest_reference`) for the six new routes — the
  `test_agent_reference` drift guard caught it, and two docstring first lines were reworded so the
  generated summaries read as whole phrases. `docs/design/consistency-audit.json` committed with
  its +2 `outline-none` (the gate's two textareas, same shape as the loop gate); `driftHits` still 8.

  **DISCOVERY for `CC-6`.** `docs/guides/chat-surface.md` is authored as "the seven things", and
  `CC-7`'s Branch mechanic shipped without adding to it — so with plan mode there are now **nine**
  mechanics and the guide covers seven. Deliberately not half-edited here: `CC-6` owns that guide's
  framing (its `done_when` still says "all seven"), and rewriting the intro copy from another atom
  would leave the wrap-up chasing a doc it no longer authored. `CC-6` should read "all nine" and add
  Branch + Plan mode; its audit table also owes rows for `chat.plan_activate` / `chat.plan_approve`
  (both logged via `sel().log_api_access` from `chat_plan.py`).

  **Not done, honestly:** no user-level validation drive. The atom's `done_when` is fully covered by
  tests, but the amendment's `VF` row (edit the plan markdown by hand in the real UI, approve,
  watch execution follow the edited plan, activate mid-task) has NOT been performed — no gateway
  was started this session. `VF` stays open.

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

### Amendment task table (extends this plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

### 2026-08-19 — `CC-8` driven as a user, and held at `todo` — **PARTIAL**

The five `done_when` clauses are implemented, tested and falsified (7 mutations, 7 reds). I then drove
the real gateway rather than shipping on tests alone, and the drive is what decides the status.

**Setup.** The branch's own build, served through the `static/dist` symlink, on an isolated home
(`PERSONALCLAW_HOME=/private/tmp/cc8-home`, **plus** `PERSONALCLAW_WORKSPACE` and a seeded
`workspace_dir` — `PERSONALCLAW_HOME` alone does not confine the workspace), `AUTH_MODE=none` so the
gateway binds loopback only. Chrome DevTools, port 10011.

**What the drive confirmed.**

- The affordance exists where the code says: composer → **Add** → *"Plan this first — Draft a plan for
  review — nothing runs until you approve it"*. It is **absent on a brand-new chat** and appears once
  the chat has started, the same gating its sibling Auto-nudge row uses. Nothing offered it; I chose it.
- Clicking it created a **real shared `planning.session.PlanSession`** — `project_id` = the chat key,
  one `chat_plan` step (`status: running`, empty `artifact`), `binding.resume_task_mode: "agent"` —
  and the composer pill flipped to **Task mode: Plan**. That is the "no second state machine" clause
  observed in the persisted record, not inferred from a test double.
- The gate panel **renders nothing before a draft exists**, which is its documented behaviour.
- An out-of-order edit is refused by the backend, not by the UI: `POST /plan/edit` on a step that is
  not awaiting review returns **409 `step_not_awaiting_review`** in the §2.2 envelope. So the artifact
  cannot be written into a step the state machine has not opened for review.
- The task-mode write path round-trips (`POST /api/chat/task-mode` → 200, mode persisted). The new
  **409 `plan_awaiting_approval`** guard correctly did *not* fire, because no step was awaiting —
  exactly its stated condition.
- Console over the whole drive: **two errors, both my own probes** (the deliberate 409 and a
  deliberately wrong 404 path). No JS error from the feature.

**Why the atom stays `todo` — the clause I could not drive.** The draft → review → edit → approve →
resume half needs **one completed plan-mode model turn**, and no chat model works on this machine:

- Bedrock (the dev home's configured chat model) — credentials expired: `aws sts get-caller-identity`
  returns `InvalidClientTokenId`.
- Ollama is up, but its only chat model `gemma4:12b` **returns empty completions**: 200 tokens
  generated (`eval_count: 200`, 7.0s) with `response: ""` and `thinking: ""`. A first real chat turn
  through the gateway died on `httpx.ReadTimeout` before that was diagnosed.

Pulling a working small model would mutate the owner's local Ollama install, which is an owner
decision, not a tick's. So the amendment's `VF` row (hand-edit the plan in the UI, approve, watch
execution follow the edited plan, activate mid-task) is **recorded unmet**, and `CC-8` stays `todo`
with its implementation merged. **Unblocking it is one owner action:** refresh AWS credentials, or
`ollama pull` a model that emits text.

### 2026-08-19 — `CC-8` CLOSED (`todo` → `done`): the `VF` row is driven, and the drive found two defects

My 2026-08-19 entry above held this atom at `todo` for one reason — "no chat model works on this
machine" — and **that reason was wrong.** `gemma4:12b` is a *thinking* model: asked for three words
with a 64-token budget it returned `done_reason: "length"` with `content: ""`, because the whole
budget went into the reasoning trace. Given room it answers normally (`"Hello to you."`, 764 tokens,
25 s). A valid-but-empty response was a **truncated transfer**, not a broken model — the same trap
the platform's own constrained-decoding note describes.

**Two provider bugs stood between that model and a driveable turn** (both fixed in
PersonalClawApps#47, found here, by driving):

1. `_timeout_or_default` — the factory accepted a timeout only `if isinstance(raw, (int, float))`,
   but Settings stores provider options as **strings** (`"timeout_secs": "120"`), so every configured
   timeout was silently replaced by the 60 s default.
2. The entry factory read `options["timeout"]` while `timeout_secs` is the name `app.json`'s schema
   **declares** and the README documents — a declared knob with no reader on the chat path. With
   `timeout_secs="1800"` a plan-mode turn still died on `httpx.ReadTimeout` after exactly **61 s**.

With both fixed the same turn completes in **119 s**.

**Then the `VF` drive found the real defect in this atom, which 19 unit tests could not see.** On a
mid-task re-plan the turn-end hook scanned the whole transcript backwards for "the newest assistant
message", and handed `chat-plan-2` the **previous** step's draft — the *pre-edit* text, sha
`a6aabd07` against step 1's edited `1353ceb5` — while the re-plan turn's actual reply
(`"I will answer your question directly in one step."`) was dropped. A user re-planning mid-task
would review and approve a plan they never asked for, and `_resume_prompt` would carry that stale
text into the resumed run. Fixed with a `draft_from` boundary recorded at activation: a step accepts
only a reply produced after it opened, and an older message is **not** a fallback (the gate honestly
says "Drafting…" instead). Re-driven on the fixed build, same prompts: `chat-plan-2` now holds the
re-plan's own reply.

**Second defect, same drive:** "Save edits" persisted the edit but left the panel in edit mode, so the
only way back to Approve was a button labelled **Cancel** — on the one surface whose whole job is a
deliberate review. Saving now returns to review; a failed save keeps the editor open with the text
intact.

**The `VF` row, as driven** (isolated home + `PERSONALCLAW_WORKSPACE` + seeded `workspace_dir`,
`AUTH_MODE=none`, the branch's own build through the `static/dist` symlink):

| clause | observed |
|---|---|
| composer affordance, manual only | **Add → "Plan this first"**; absent on a fresh chat, present once started |
| opens the EXISTING walkthrough | a real `planning.session.PlanSession`: `project_id` = chat key, one `chat_plan` step, `resume_task_mode` recorded |
| editable markdown artifact | hand-edited in the UI; `STEP EDITED BY THE OPERATOR` persisted through `PS.edit_artifact` |
| approve/comment route through it | `Approve & run it` → step `approved`, `complete: true` |
| the gate, not the prompt | task-mode switch while awaiting → **409 `plan_awaiting_approval`**, mode held at `plan` |
| out-of-order edit refused | `POST /plan/edit` on a non-awaiting step → **409 `step_not_awaiting_review`** |
| mid-turn activation parks | `parked: true`, `parked_messages: 7`, transcript intact, a `chat-plan-2` step opened |
| resumes on approval | `resumed: true`, `task_mode` restored to `agent`, park keys cleared, transcript 8 messages, nothing lost |

Console over the whole drive: two errors, both my own deliberate probes. Real-home rail, scoped to
the drive window with a positive control: **0** files in `~/.personalclaw`, **0** in
`~/workplace/personalclaw-workspace`.

One behaviour worth recording rather than "fixing": a message sent while a parked turn is still
unwinding returns `{"ok": true, "queued": true}` and runs when the turn ends. Parking asks the
in-flight turn to stop; it does not kill it mid-generation. The transcript stays coherent, so this is
the honest shape, not a defect.
