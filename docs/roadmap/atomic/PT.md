# PERSONALITY-THEMES — atomic plans

**Source plan:** [`PERSONALITY-THEMES`](../plans/PERSONALITY-THEMES.md)  
**Code:** `PT`  
**Source status:** in_progress

6 atoms: S1 identity/registry/persona layer is DONE (1 atom); S2 remains as 3 independent feature atoms (sound cues, shell element, error treatments) + 1 proofs/a11y-test atom + 1 end-to-end validation atom. No cross-plan dependencies — the APP-PLATFORM-EVOLUTION seam is an out-of-scope forward hook.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PT-1` | ✅ | S1: personality registry + identity behaviors + persona-snippet backend generalization | — | personalities.ts registry + closed behavior block typechecks with personalclaw/retro-terminal/claw-arcade entries; new phosphor scheme passes schemeContrast.test.ts (AA both modes) without weakening it; PersonalityProvider swaps document.title/favicon/data-personality/wordmark and restores byte-identical defaults on deactivate (survives reload); rename picker is propose-don't-write (unchecked leaves agent.bot_name untouched, checked patches it); backend _PERSONA_THEMES closed set replaces the hardcoded lumon branch with lumon as entry #1 and rejects unknown theme values; personalityA11y.test.ts structural tests green; make lint + make test + web typecheck/vitest/build green (per 2026-07-28 execution log) |
| `PT-2` | ⬜ | S2: soundCues synth + master toggle (default OFF) + cue wiring at the three cue points | `PT-1` | web/src/design/soundCues.ts lazily creates one gesture-gated AudioContext and plays the closed cue set (turn_complete, approval_needed, error); a master toggle (default OFF) added to the Personality picker; cues wired at turn-settled (ChatPage), approval-requested (useApprovalToasts), and error toast (Toaster); no sound ever plays with toggle off, under prefers-reduced-motion, or when document.hidden (tests mock the media query); CI grep confirms zero audio files shipped in the bundle |
| `PT-3` | ⬜ | S2: shell-element closed registry + TerminalStrip scanline component mounted at App shell | `PT-1` | SHELL_ELEMENTS closed {id -> lazy component} map added to personalities.ts; web/src/ui/personality/TerminalStrip.tsx renders at the App-shell slot only under its personality (aria-hidden, pointer-events-none, static under reduced-motion following DotGlow discipline); axe a11y pass unchanged; reduced-motion renders a static frame |
| `PT-4` | ✅ | S2: error-treatment variants on ErrorBoundary + IncidentBanner (skin-only) | `PT-1` | ErrorBoundary fallback and IncidentBanner accept an optional visual variant id from the personality context (visual skin only: same copy, same role=alert, same actions, AA-checked); forced error under each personality renders its treatment; under a standard scheme both are pixel-identical to today |
| `PT-5` | ⬜ | S2: finish claw-arcade proof + extend personalityA11y.test.ts for the new closed maps | `PT-2`, `PT-3`, `PT-4` | claw-arcade proof fleshed out (expressiveness preset via runtime dials, sparkle dot shape, coin-blip cue); personalityA11y.test.ts extended to go red on unknown base scheme, dangling shellElement/errorTreatment id, and any cue declared without the master-toggle gate; both proof personalities fully switchable |
| `PT-6` | ⬜ | S2: V2 end-to-end user validation + full CI gate across both personalities and all modes | `PT-2`, `PT-3`, `PT-4`, `PT-5` | Full as-a-user tour (dev home) of both personalities across chat/settings/error states, sounds on and off, reduced-motion on and off, dark and light; switching back to a standard scheme leaves zero residue (title, favicon, name, DOM); npm run typecheck && npm test && npm run build + e2e a11y all green |

## Atom scopes

### `PT-1` — S1: personality registry + identity behaviors + persona-snippet backend generalization

**Status:** done

Design 'S1 — The personality registry + identity behaviors' and 'S1 — Persona snippet generalization'; Task breakdown Session 1 (T1.1 registry/types + phosphor scheme, T1.2 PersonalityProvider, T1.3 display-name offer flow, T1.4 persona generalization, V1); Contracts C1/C3/C4/C5

**Done when:** personalities.ts registry + closed behavior block typechecks with personalclaw/retro-terminal/claw-arcade entries; new phosphor scheme passes schemeContrast.test.ts (AA both modes) without weakening it; PersonalityProvider swaps document.title/favicon/data-personality/wordmark and restores byte-identical defaults on deactivate (survives reload); rename picker is propose-don't-write (unchecked leaves agent.bot_name untouched, checked patches it); backend _PERSONA_THEMES closed set replaces the hardcoded lumon branch with lumon as entry #1 and rejects unknown theme values; personalityA11y.test.ts structural tests green; make lint + make test + web typecheck/vitest/build green (per 2026-07-28 execution log)

### `PT-2` — S2: soundCues synth + master toggle (default OFF) + cue wiring at the three cue points

**Status:** todo

Design 'S2 — Sound cues ...' (soundCues.ts); Task breakdown Session 2 T2.1; Contract C2 (CueRecipe/playCue)

**Done when:** web/src/design/soundCues.ts lazily creates one gesture-gated AudioContext and plays the closed cue set (turn_complete, approval_needed, error); a master toggle (default OFF) added to the Personality picker; cues wired at turn-settled (ChatPage), approval-requested (useApprovalToasts), and error toast (Toaster); no sound ever plays with toggle off, under prefers-reduced-motion, or when document.hidden (tests mock the media query); CI grep confirms zero audio files shipped in the bundle

### `PT-3` — S2: shell-element closed registry + TerminalStrip scanline component mounted at App shell

**Status:** todo

Design 'S2 ...' (shellElement); Task breakdown Session 2 T2.2; Contract C1 (SHELL_ELEMENTS closed map)

**Done when:** SHELL_ELEMENTS closed {id -> lazy component} map added to personalities.ts; web/src/ui/personality/TerminalStrip.tsx renders at the App-shell slot only under its personality (aria-hidden, pointer-events-none, static under reduced-motion following DotGlow discipline); axe a11y pass unchanged; reduced-motion renders a static frame

### `PT-4` — S2: error-treatment variants on ErrorBoundary + IncidentBanner (skin-only)

**Status:** done

Design 'S2 ...' (errorTreatment); Task breakdown Session 2 T2.3

**Done when:** ErrorBoundary fallback and IncidentBanner accept an optional visual variant id from the personality context (visual skin only: same copy, same role=alert, same actions, AA-checked); forced error under each personality renders its treatment; under a standard scheme both are pixel-identical to today

### `PT-5` — S2: finish claw-arcade proof + extend personalityA11y.test.ts for the new closed maps

**Status:** todo

Design 'S2 ...' (both proofs) and 'A11y invariants'; Task breakdown Session 2 T2.4; Contract C5

**Done when:** claw-arcade proof fleshed out (expressiveness preset via runtime dials, sparkle dot shape, coin-blip cue); personalityA11y.test.ts extended to go red on unknown base scheme, dangling shellElement/errorTreatment id, and any cue declared without the master-toggle gate; both proof personalities fully switchable

### `PT-6` — S2: V2 end-to-end user validation + full CI gate across both personalities and all modes

**Status:** todo

Task breakdown Session 2 V2

**Done when:** Full as-a-user tour (dev home) of both personalities across chat/settings/error states, sounds on and off, reduced-motion on and off, dark and light; switching back to a standard scheme leaves zero residue (title, favicon, name, DOM); npm run typecheck && npm test && npm run build + e2e a11y all green

