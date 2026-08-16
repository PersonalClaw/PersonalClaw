# PERSONALITY-THEMES — atomic plans

**Source plan:** [`PERSONALITY-THEMES`](../plans/PERSONALITY-THEMES.md)  
**Code:** `PT`  
**Source status:** in_progress

6 atoms: S1 identity/registry/persona layer is DONE (1 atom); S2 remains as 3 independent feature atoms (sound cues, shell element, error treatments) + 1 proofs/a11y-test atom + 1 end-to-end validation atom. No cross-plan dependencies — the APP-PLATFORM-EVOLUTION seam is an out-of-scope forward hook.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PT-1` | ✅ | S1: personality registry + identity behaviors + persona-snippet backend generalization | — | personalities.ts registry + closed behavior block typechecks with personalclaw/retro-terminal/claw-arcade entries; new phosphor scheme passes schemeContrast.test.ts (AA both modes) without weakening it; PersonalityProvider swaps document.title/favicon/data-personality/wordmark and restores byte-identical defaults on deactivate (survives reload); rename picker is propose-don't-write (unchecked leaves agent.bot_name untouched, checked patches it); backend _PERSONA_THEMES closed set replaces the hardcoded lumon branch with lumon as entry #1 and rejects unknown theme values; personalityA11y.test.ts structural tests green; make lint + make test + web typecheck/vitest/build green (per 2026-07-28 execution log) |
| `PT-2` | ✅ | S2: soundCues synth + master toggle (default OFF) + cue wiring at the three cue points | `PT-1` | web/src/design/soundCues.ts lazily creates one gesture-gated AudioContext and plays the closed cue set (turn_complete, approval_needed, error); a master toggle (default OFF) added to the Personality picker; cues wired at turn-settled (ChatPage), approval-requested (useApprovalToasts), and error toast (Toaster); no sound ever plays with toggle off, under prefers-reduced-motion, or when document.hidden (tests mock the media query); CI grep confirms zero audio files shipped in the bundle |
| `PT-3` | ✅ | S2: shell-element closed registry + TerminalStrip scanline component mounted at App shell | `PT-1` | SHELL_ELEMENTS closed {id -> lazy component} map added to personalities.ts; web/src/ui/personality/TerminalStrip.tsx renders at the App-shell slot only under its personality (aria-hidden, pointer-events-none, static under reduced-motion following DotGlow discipline); axe a11y pass unchanged; reduced-motion renders a static frame |
| `PT-4` | ✅ | S2: error-treatment variants on ErrorBoundary + IncidentBanner (skin-only) | `PT-1` | ErrorBoundary fallback and IncidentBanner accept an optional visual variant id from the personality context (visual skin only: same copy, same role=alert, same actions, AA-checked); forced error under each personality renders its treatment; under a standard scheme both are pixel-identical to today |
| `PT-5` | ✅ | S2: finish claw-arcade proof + extend personalityA11y.test.ts for the new closed maps | `PT-2`, `PT-3`, `PT-4` | claw-arcade proof fleshed out (expressiveness preset via runtime dials, sparkle dot shape, coin-blip cue); personalityA11y.test.ts extended to go red on unknown base scheme, dangling shellElement/errorTreatment id, and any cue declared without the master-toggle gate; both proof personalities fully switchable |
| `PT-6` | ⬜ | S2: V2 end-to-end user validation + full CI gate across both personalities and all modes | `PT-2`, `PT-3`, `PT-4`, `PT-5` | Full as-a-user tour (dev home) of both personalities across chat/settings/error states, sounds on and off, reduced-motion on and off, dark and light; switching back to a standard scheme leaves zero residue (title, favicon, name, DOM); npm run typecheck && npm test && npm run build + e2e a11y all green |

## Atom scopes

### `PT-1` — S1: personality registry + identity behaviors + persona-snippet backend generalization

**Status:** done

Design 'S1 — The personality registry + identity behaviors' and 'S1 — Persona snippet generalization'; Task breakdown Session 1 (T1.1 registry/types + phosphor scheme, T1.2 PersonalityProvider, T1.3 display-name offer flow, T1.4 persona generalization, V1); Contracts C1/C3/C4/C5

**Done when:** personalities.ts registry + closed behavior block typechecks with personalclaw/retro-terminal/claw-arcade entries; new phosphor scheme passes schemeContrast.test.ts (AA both modes) without weakening it; PersonalityProvider swaps document.title/favicon/data-personality/wordmark and restores byte-identical defaults on deactivate (survives reload); rename picker is propose-don't-write (unchecked leaves agent.bot_name untouched, checked patches it); backend _PERSONA_THEMES closed set replaces the hardcoded lumon branch with lumon as entry #1 and rejects unknown theme values; personalityA11y.test.ts structural tests green; make lint + make test + web typecheck/vitest/build green (per 2026-07-28 execution log)

### `PT-2` — S2: soundCues synth + master toggle (default OFF) + cue wiring at the three cue points

**Status:** done

Design 'S2 — Sound cues ...' (soundCues.ts); Task breakdown Session 2 T2.1; Contract C2 (CueRecipe/playCue)

**Done when:** web/src/design/soundCues.ts lazily creates one gesture-gated AudioContext and plays the closed cue set (turn_complete, approval_needed, error); a master toggle (default OFF) added to the Personality picker; cues wired at turn-settled (ChatPage), approval-requested (useApprovalToasts), and error toast (Toaster); no sound ever plays with toggle off, under prefers-reduced-motion, or when document.hidden (tests mock the media query); CI grep confirms zero audio files shipped in the bundle

**DONE.** `web/src/design/soundCues.ts` synthesises three earcons — `turn_complete`,
`approval_needed`, `error` — from oscillator + gain envelopes; the closed set is a union type,
so `playCue('ka-ching')` does not compile and `CUES` is a total `Record`, so a fourth member
cannot be added without a recipe. **All three suppressors live inside `playCue`, not at the
three call sites**: a gate a caller can forget to apply is not a gate, and locating them once
means the wiring is three unconditional one-liners with no policy of their own. Sound requires
the localStorage key `soundCues` to hold the literal `'on'` — default-OFF is a property of the
comparison, not a `?? false`, so no truthy leftover (`'1'`, `'true'`) can enable audio by
accident. **One AudioContext, and `playCue` never builds it**: construction sits in one private
function with two gesture-scoped callers (the toggle's own click, and the one-shot
pointerdown/keydown primer `armCueAudio()` that `App` arms at mount), because a context built
without user activation comes back permanently suspended and a second one leaks an audio
thread. A recipe's gain is clamped to `MAX_GAIN` (0.1) so no future cue can startle a
headphone user. **Zero audio files** is a rail, not a claim: `noAudioAssets.test.ts` sweeps
`web/public` + `web/src` + `web/dist` (517 built files at measurement) and separately proves
the cue module reaches for no audio FILE by any route — comments stripped first, because the
module's own header names `HTMLAudioElement` and `new Audio(` to explain why it avoids them.
Cue points: `ChatPage`'s streaming→settled branch in `markStreaming` (the one transition, so a
cue can't fire on a no-op re-render), `useApprovalToasts` **after** its dedupe + active-session
guards (one nudge per approval, silent on a reconnect re-broadcast), and `Toaster` for
`level: 'error'` **only** — the same line the assertive live region draws.

**Falsified 15 ways**, every one red: defaulting the toggle on (4 red); deleting each
suppressor alone (toggle 4, reduced-motion 2, `document.hidden` 1 — the independence is real,
because each suppressor test asserts the *other two* are still clear); eager construction at
import (4) and `playCue` constructing its own context (1); removing / mislocating / commenting
out each of the three cue sites; cueing on every toast level; removing the gain clamp and its
negative floor; planting an audio asset in `web/public` and in the built `web/dist`; and
neutering each vacuity floor (matcher, tree-walk, comment-stripper). Two mutations initially
reded nothing and were covered rather than deleted (the negative-gain floor, the
resume-a-suspended-context-on-enable path). One measured DEFECT, fixed: `armCueAudio()` armed
before asking whether the platform has Web Audio at all, holding a pointerdown+keydown listener
for the life of the page on a browser that could never satisfy it.

### `PT-3` — S2: shell-element closed registry + TerminalStrip scanline component mounted at App shell

**Status:** done

Design 'S2 ...' (shellElement); Task breakdown Session 2 T2.2; Contract C1 (SHELL_ELEMENTS closed map)

**Done when:** SHELL_ELEMENTS closed {id -> lazy component} map added to personalities.ts; web/src/ui/personality/TerminalStrip.tsx renders at the App-shell slot only under its personality (aria-hidden, pointer-events-none, static under reduced-motion following DotGlow discipline); axe a11y pass unchanged; reduced-motion renders a static frame

**DONE.** `design/personalities.ts` gains `ShellElementId` (a literal union) and
`SHELL_ELEMENTS: Record<ShellElementId, LazyExoticComponent<ComponentType>>`, mounted from
`App.tsx`'s main shell through a new `PersonalityShellElement` slot in `app/personality.tsx`;
`ui/personality/TerminalStrip.tsx` is the first entry (`terminal-scanlines`, declared by
`retro-terminal`). **Closed in three independent ways, because the type alone is not a runtime
guarantee:** the literal union makes an unregistered id unwritable in `behavior.shellElement`; the
total `Record` makes a member without an entry a compile error; and `getShellElement` refuses at
runtime for anything arriving from outside the compiler (a stale persisted override, the plan's
forward-hooked app-contributed manifest).

**Measured defect, fixed here — `map[id] ?? null` was not closed at all.** The registry resolver's
first draft copied `getErrorTreatment`'s shape, and the test written to prove closure reddened
immediately: a plain index reads the **prototype chain**, so `getShellElement('constructor')`
returned `Object` — a value React would then try to render as a component. Probing the sibling
confirmed the same hole was already live on `main`: `getErrorTreatment('constructor')` returned
`Object` and `treatmentPaint` then threw `Cannot read properties of undefined (reading 'bg')` —
**inside the ErrorBoundary render path, the one place PT-4's own doc comment says must never
throw**, i.e. one broken page becoming a blank app. Both resolvers now gate on `Object.hasOwn`
and both carry a five-key inherited-id case (`constructor`, `toString`, `hasOwnProperty`,
`__proto__`, `valueOf`).

**The decorative contract is asserted by RENDERING every registry entry, not by reading source**
(`design/shellElements.test.tsx`): `aria-hidden="true"`, `pointer-events-none`, a
`data-shell-element` marker naming its own id, and zero tabbable descendants. The loop covers
present and future members, so a new shell element cannot ship without the contract — and a source
scan would have passed on a component that wrote `aria-hidden` in a comment or on the wrong node.

**Reduced motion is absence, not a frozen animation.** The strip is two layers on purpose:
`.crt-raster` (a static hairline lattice) and `.crt-beam` (one travelling band), both new in
`design/tokens.css` beside the `.blueprint-scan` precedent and both painting through
`--color-on-surface`/`--color-primary` so the raster re-tints with the scheme instead of pinning a
second unthemed green. When the query matches, the beam is **not rendered** — a paused CSS
animation still costs a compositor layer and still reads as stuck. There is deliberately **no**
`prefers-reduced-motion` rule for `.crt-beam` in the CSS: the React gate already makes it
unreachable, so such a rule would be a second, inert mechanism. The gate reads
`design/motion.ts`'s call-time `prefersReducedMotion()` rather than framer-motion's
`useReducedMotion`, whose probe is cached in a module singleton and therefore cannot be proven
static by a test that stubs `matchMedia` after any earlier render in the same file; the two cases
still live in **separate files** (`TerminalStrip.test.tsx` / `TerminalStrip.reducedMotion.test.tsx`,
stub at module scope) so a future switch back to `useReducedMotion` reddens instead of silently
passing.

**"Only under its personality" is asserted as absence AND presence, with both populations proved
non-empty** (`app/personalityShellElement.test.tsx`, driving the real provider →
localStorage → context → Suspense): nothing mounts under `personalclaw`, `claw-arcade`, no stored
personality at all, or a removed-entry id; the strip mounts under `retro-terminal`. The assertion is
on node count and on `container.innerHTML === ''`, so an overlay hidden with `display:none` would
still fail — which matters because it is `fixed inset-0`, a stacking context every standard-scheme
user would otherwise pay for. `z-[55]` is chosen against the measured scale: page content tops out
at `z-50`, the overlay stack starts at `z-[60]` (Modal), so a dialog and a toast stay crisp.

**Lazy is verified in the build, not just declared.** Two rails: every registry value must be a
React lazy type, and no module outside the registry may reference `ui/personality/` statically
(comments stripped — a rail in this repo has already been fooled by a match in prose — plus two
vacuity floors). Falsification found a **real gap in that rail**: the bare side-effect form
`import '../ui/personality/X'` has no `from` clause and slipped through the first regex while
bundling the module just as thoroughly; the pattern now covers `import … from`, `export … from`,
`import type`, and the bare form, each confirmed red. Confirmed against `npm run build`: the entry
chunk contains only the id string `terminal-scanlines`, while `crt-raster`/`crt-beam`/
`data-shell-element` appear **only** in a separate 0.75 kB `TerminalStrip-*.js`. The ~250 bytes of
`.crt-*` CSS do ship in the global stylesheet for everyone, as every other `tokens.css` class does.

Fourteen mutations were run against the live lines; all ten that had to red did, the comment-only
case correctly stayed green, and the two vacuity floors red when nothing declares a shell element.
`axe` is untouched: no e2e route activates a personality, and the element is `aria-hidden` with no
focusable content. Gate: `npm run typecheck` clean, full `npm test --workspace web` 278 files /
2771 tests passed (baseline 274 / 2743), `npm run build` clean.

### `PT-4` — S2: error-treatment variants on ErrorBoundary + IncidentBanner (skin-only)

**Status:** done

Design 'S2 ...' (errorTreatment); Task breakdown Session 2 T2.3

**Done when:** ErrorBoundary fallback and IncidentBanner accept an optional visual variant id from the personality context (visual skin only: same copy, same role=alert, same actions, AA-checked); forced error under each personality renders its treatment; under a standard scheme both are pixel-identical to today

### `PT-5` — S2: finish claw-arcade proof + extend personalityA11y.test.ts for the new closed maps

**Status:** done

Design 'S2 ...' (both proofs) and 'A11y invariants'; Task breakdown Session 2 T2.4; Contract C5

**Done when:** claw-arcade proof fleshed out (expressiveness preset via runtime dials, sparkle dot shape, coin-blip cue); personalityA11y.test.ts extended to go red on unknown base scheme, dangling shellElement/errorTreatment id, and any cue declared without the master-toggle gate; both proof personalities fully switchable

**DONE.** (a) **`behavior.dials`** — the expressiveness preset, declared as four dials
(`expressiveness`, `bounciness`, `dotShape`, `dotPattern`) that name EXISTING tokens via
`PERSONALITY_DIAL_TOKENS` and are applied by writing those tokens through the appearance
store, exactly as the sliders in Settings → Appearance do. Nothing writes `design/runtime.ts`
directly: the store's own bridge stays the single writer, and the preset lands in the user's
saved overrides so they can dial it back and it sticks. A dial the target identity does not
declare is `resetToken`-ed rather than left alone — otherwise the arcade's sparkle would
survive a switch back to the default, which is the residue the provider exists to prevent.
(b) **`behavior.soundCues` re-voices a cue POINT**, and PT-2's `playCue(name)` split into
`CuePoint` (the three moments, closed) and `CueName` (registered voices: the three plus
`coin_blip` and `terminal_bell`). A personality changes what a moment SOUNDS LIKE — it cannot
add a moment, cannot author a tone, and cannot sound anything outside `playCue`, which still
owns all three suppressors. The map is validated at the BOUNDARY (`setCueVoices`) on both
sides, `Object.hasOwn` for the same prototype-chain reason PT-3 measured live. (c) **Both
proofs now exercise every key in the closed block between them** — claw-arcade: amber, sparkle
dots on a diamond lattice, expressiveness 1, a coin blip on a finished turn; retro-terminal:
the opposite temperament on the same four dials (square/grid, expressiveness 0.25, bounciness
0) plus the ASCII BEL on an approval. A rail asserts the union covers the block, so a field
cannot rot unread. (d) **`personalityA11y.test.ts` rebuilt as ten named rails, each proven red
by a fixture that breaks exactly it** (isolation asserted: a fixture may trip no other rail),
with the rail↔fixture mapping asserted total in both directions and a non-empty-population
floor under each of the seven conditional rails. (e) **The cue-gate half reads SOURCE**,
because that is where the bypass lives: `playCue` checks all three suppressors before reaching
`synth`, `playCue` is the only caller of `synth`, `synth` is unexported, and no module in
`web/src` outside the cue module builds an oscillator. 53 tests where there were 11.

### `PT-6` — S2: V2 end-to-end user validation + full CI gate across both personalities and all modes

**Status:** todo

Task breakdown Session 2 V2

**Done when:** Full as-a-user tour (dev home) of both personalities across chat/settings/error states, sounds on and off, reduced-motion on and off, dark and light; switching back to a standard scheme leaves zero residue (title, favicon, name, DOM); npm run typecheck && npm test && npm run build + e2e a11y all green

