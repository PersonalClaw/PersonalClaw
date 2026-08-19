# CHAT-CRAFT — atomic plans

**Source plan:** [`CHAT-CRAFT`](../plans/CHAT-CRAFT.md)  
**Code:** `CC`  
**Source status:** in_progress

Decomposed CHAT-CRAFT (CC) into 8 atoms: 7 done (S1-S3, shipped as one feature-chat-craft commit 2026-07-27; S4's screen-snip 2026-08-16; the Branch mechanic and the optimizer polish 2026-08-17; the chat plan-mode entry 2026-08-19) and 1 todo (S4 polish/docs/validation wrap-up). No blocking cross-plan edges; one intra-plan edge (wrap-up needs all seven surfaces).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CC-1` | ✅ (#feature-chat-craft (2026-07-27)) | S1: true rewind (fork-and-swap under same slot) + queue interrupt-now | — | editing any past user turn with rewind:true snapshots the discarded tail onto user_msg['rewound'] (cap 5, redacted), truncates, sessions.reset rebuilds provider context from the truncated transcript, chat_rewound WS + SEL chat.rewind fire, and the orphaned /interrupt is wired with a queue_promoted echo + 'Interrupt now' QueueStack action |
| `CC-2` | ✅ (#feature-chat-craft (2026-07-27)) | S2: find-in-conversation bar + attributed quote toolbar | — | findMatches.ts pure scanner (<10ms on 500 turns) + FindBar.tsx (Cmd/Ctrl+F, count/cycle/scroll via turnNodes, CSS Custom Highlight paint that never re-parses markdown) shipped, and SelectionQuote grows into a Quote+Copy toolbar emitting attributed blockquotes with selectionchange positioning |
| `CC-3` | ✅ (#feature-chat-craft (2026-07-27)) | S3: follow-up chips (WS-push) + word-boundary smooth streaming | — | chat_followups.py + task-followups prompt emit 2-3 chips via the _bg lite session (gated on config/restriction/queue/error, silent with no model bound, cancel-on-next-dispatch), FollowupChips.tsx renders them, CoalescerCore.tick() snaps to word/CJK boundaries with catch-up override, and followup_chips + stream_reveal config fields are 5-point round-tripped |
| `CC-4` | ✅ (#feature-cc4-screen-snip (2026-08-16)) | Screen-snip into chat: getDisplayMedia frame-grab + crop + mac path selection | — | on non-mac browsers a '+'-menu 'Capture screen area' does getDisplayMedia one-frame grab (tracks stopped immediately) -> crop overlay -> PNG blob -> existing api.uploadFiles pipeline -> attachment chip with OCR'd content on send; mac keeps native screencapture -i with SnipOverlay as fallback; a documented DESKTOP-CAPABILITIES S2 seam comment marks where the Electron bridge later replaces getDisplayMedia; feature-detect hides the entry where the API is absent (iOS Safari) |
| `CC-5` | ✅ (#feature-cc5-optimizer-context (2026-08-17)) | Optimizer polish: richer role-labeled context + explicit UNCHANGED contract | — | optimize/optimizeAndSend assemble role-labeled (~10 turns, ~400 chars/turn, newest-last) context that survives the handler's [-2000:] cap; the bundled task-prompt_optimizer.md instructs the exact UNCHANGED token so an already-specific prompt returns changed:false with a short reply (fixture asserts it); a vague prompt referencing 'that file from earlier' optimizes using the labeled referent; the existing revert path is confirmed/tested (restores the exact pre-optimize draft, focus returns) |
| `CC-6` | ⬜ | S4 wrap-up: a11y/SEL/mobile polish + docs + CHANGELOG + full validation gate | `CC-1`, `CC-2`, `CC-3`, `CC-4` | FindBar/chips keyboard traversal + aria-live clean, SEL shows one event per security-relevant action across all seven mechanics (snip rides existing upload SEL), mobile degrades sanely (find docked, chips wrap, snip hidden on iOS Safari), the chat-surface docs guide covers all seven, a class-B CHANGELOG entry for the rewound field carries snapshot advice, and the full local gate is green (make lint / targeted pytest / make test / web typecheck+test+build) |
| `CC-7` | ✅ (#feature-cc7-branch-mechanic (2026-08-17)) | Branch mechanic: hover branch affordance on any message + 'Branched from' breadcrumb | — | a hover branch icon on any past message (user or assistant) calls the existing POST .../fork at that index and navigates to the child; branching from either role, the same message repeatedly, and a branch-of-a-branch all work; the child shows a 'Branched from' breadcrumb reading persisted forked_from that links to the origin; the fork-cap 429 surfaces as a readable message; app-scoped/non-persistent sessions hide the affordance via the endpoint's existing refusals; no primitive-adoption ratchet trips |
| `CC-8` | ⬜ | Chat plan-mode entry + mid-task re-planning bound to the existing planning gate | — | a composer affordance opens the EXISTING planning/session.py walkthrough as an editable Markdown artifact (no second state machine), approve/comment route through it, activation is manual-only (quick tasks unaffected), the no-execute guarantee is enforced by the existing plan task-mode gate (test asserts a mutating tool is denied while awaiting approval, not the prompt), and activating mid-turn parks the run awaiting approval without losing the transcript and resumes on approval |

## Atom scopes

### `CC-1` — S1: true rewind (fork-and-swap under same slot) + queue interrupt-now

**Status:** done (PR #feature-chat-craft (2026-07-27))

Session 1 (T1.1-T1.3, V1); Design S1; Contracts C1, C2

**Done when:** editing any past user turn with rewind:true snapshots the discarded tail onto user_msg['rewound'] (cap 5, redacted), truncates, sessions.reset rebuilds provider context from the truncated transcript, chat_rewound WS + SEL chat.rewind fire, and the orphaned /interrupt is wired with a queue_promoted echo + 'Interrupt now' QueueStack action

### `CC-2` — S2: find-in-conversation bar + attributed quote toolbar

**Status:** done (PR #feature-chat-craft (2026-07-27))

Session 2 (T2.1-T2.3, V2); Design S2; Contracts C5

**Done when:** findMatches.ts pure scanner (<10ms on 500 turns) + FindBar.tsx (Cmd/Ctrl+F, count/cycle/scroll via turnNodes, CSS Custom Highlight paint that never re-parses markdown) shipped, and SelectionQuote grows into a Quote+Copy toolbar emitting attributed blockquotes with selectionchange positioning

### `CC-3` — S3: follow-up chips (WS-push) + word-boundary smooth streaming

**Status:** done (PR #feature-chat-craft (2026-07-27))

Session 3 (T3.1-T3.5, V3); Design S3; Contracts C3, C4, C5

**Done when:** chat_followups.py + task-followups prompt emit 2-3 chips via the _bg lite session (gated on config/restriction/queue/error, silent with no model bound, cancel-on-next-dispatch), FollowupChips.tsx renders them, CoalescerCore.tick() snaps to word/CJK boundaries with catch-up override, and followup_chips + stream_reveal config fields are 5-point round-tripped

### `CC-4` — Screen-snip into chat: getDisplayMedia frame-grab + crop + mac path selection

**Status:** done (PR #feature-cc4-screen-snip (2026-08-16))

Session 4 T4.1, T4.2; Design S4(a); Contract C5 (SnipOverlay.tsx)

**Done when:** on non-mac browsers a '+'-menu 'Capture screen area' does getDisplayMedia one-frame grab (tracks stopped immediately) -> crop overlay -> PNG blob -> existing api.uploadFiles pipeline -> attachment chip with OCR'd content on send; mac keeps native screencapture -i with SnipOverlay as fallback; a documented DESKTOP-CAPABILITIES S2 seam comment marks where the Electron bridge later replaces getDisplayMedia; feature-detect hides the entry where the API is absent (iOS Safari)

### `CC-5` — Optimizer polish: richer role-labeled context + explicit UNCHANGED contract

**Status:** done (PR #feature-cc5-optimizer-context (2026-08-17))

Amendment (2026-07-26) T4.5

**Done when:** optimize/optimizeAndSend assemble role-labeled (~10 turns, ~400 chars/turn, newest-last) context that survives the handler's [-2000:] cap; the bundled task-prompt_optimizer.md instructs the exact UNCHANGED token so an already-specific prompt returns changed:false with a short reply (fixture asserts it); a vague prompt referencing 'that file from earlier' optimizes using the labeled referent; the existing revert path is confirmed/tested (restores the exact pre-optimize draft, focus returns)

### `CC-6` — S4 wrap-up: a11y/SEL/mobile polish + docs + CHANGELOG + full validation gate

**Status:** todo

Session 4 T4.3, T4.4, V4; Design S4(b)

**Done when:** FindBar/chips keyboard traversal + aria-live clean, SEL shows one event per security-relevant action across all seven mechanics (snip rides existing upload SEL), mobile degrades sanely (find docked, chips wrap, snip hidden on iOS Safari), the chat-surface docs guide covers all seven, a class-B CHANGELOG entry for the rewound field carries snapshot advice, and the full local gate is green (make lint / targeted pytest / make test / web typecheck+test+build)

### `CC-7` — Branch mechanic: hover branch affordance on any message + 'Branched from' breadcrumb

**Status:** done

Amendment (2026-07-29) (a) Branch; F1.1, F1.2 + VF branch portion

**Done when:** a hover branch icon on any past message (user or assistant) calls the existing POST .../fork at that index and navigates to the child; branching from either role, the same message repeatedly, and a branch-of-a-branch all work; the child shows a 'Branched from' breadcrumb reading persisted forked_from that links to the origin; the fork-cap 429 surfaces as a readable message; app-scoped/non-persistent sessions hide the affordance via the endpoint's existing refusals; no primitive-adoption ratchet trips

### `CC-8` — Chat plan-mode entry + mid-task re-planning bound to the existing planning gate

**Status:** done

Amendment (2026-07-29) (b) Plan Mode; F1.3, F1.4 + VF plan portion

**Done when:** a composer affordance opens the EXISTING planning/session.py walkthrough as an editable Markdown artifact (no second state machine), approve/comment route through it, activation is manual-only (quick tasks unaffected), the no-execute guarantee is enforced by the existing plan task-mode gate (test asserts a mutating tool is denied while awaiting approval, not the prompt), and activating mid-turn parks the run awaiting approval without losing the transcript and resumes on approval

