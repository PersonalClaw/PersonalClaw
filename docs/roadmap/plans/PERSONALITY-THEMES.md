# PERSONALITY-THEMES

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PT.md`](../atomic/PT.md) as 6 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Personality Themes — Themes That Carry Behavior, Not Just Color

**Status:** IN PROGRESS — S1 shipped 2026-07-28 (the personality registry + identity behaviors:
`design/personalities.ts` with a CLOSED typed `behavior` block and three entries, the new `phosphor`
scheme inside the existing WCAG sweep, `app/personality.tsx` mounted in `main.tsx` with full restore,
the Settings → Design `PersonalityPicker` with a propose-don't-write rename consent, and the T1.4
backend clean break to a `_PERSONA_THEMES` set imported by `chat_handlers.py` so accepted values and
injectable personas cannot drift).
**S2 (sound cues, shell element, error treatments, proofs) NOT started** — no `design/soundCues.ts`,
no `SHELL_ELEMENTS`/`TerminalStrip.tsx`, no personality variant on `ErrorBoundary`/`IncidentBanner`.
Status corrected 2026-08-04 by code audit. Created 2026-07-26 (roadmap rev 12; owner ask:
sibling-platform gap analysis greenlight — "personality themes as a brand play")

---

## Context (code recon, 2026-07-26)

- **Theme system (frontend):** curated color schemes are `SCHEMES: Scheme[]` in `web/src/design/schemes.ts` (`Scheme {id, label, emoji?, swatch, colors}` — color-token overrides keyed by CSS varName; `DEFAULT_SCHEME = 'coral'`). The appearance store (`web/src/app/appearance.tsx`, `AppearanceProvider`) applies overrides to `<html>` per mode, persists to localStorage key `appearance` (`Overrides {colors, scalars, selects, scheme, widthPreset}`), pools server custom themes (`themeToScheme`), and sets `root.dataset.theme = ov.scheme` + `root.dataset.ui` (the P19 orthogonal attributes — CSS can already key off the active theme id). Dark/light mode is separately owned by `web/src/app/theme.tsx` (`ThemeProvider`, `.light` class + `data-mode`).
- **Token vocabulary:** `web/src/design/tokenRegistry.ts` (`TOKENS: Token[]`, `ColorToken/ScalarToken/SelectToken`; scalars/selects can feed `web/src/design/runtime.ts` — the mutable bridge DotGlow + motion read: `glow, animSpeed, dotShape, dotPattern, bounciness, expressiveness`). `web/src/design/tokens.css` holds the defaults + the density blocks + the two `@media (prefers-reduced-motion: reduce)` rules (`:398` kills all animation/transition durations; `:420` drops glass blur).
- **Server theme store:** `/api/themes` CRUD in `src/personalclaw/dashboard/handlers/agents.py` (routes at `dashboard/server.py:585-589`): JSON files under `config_dir()/themes/<slug>.json`, validated by `_validate_theme_data` against the `_THEME_CSS_VARS` allowlist (kept in exact sync with tokenRegistry color varNames) with `_sanitize_css_value` (positive char allowlist + `_CSS_DANGEROUS_FUNC_RE` denylist — url()/expression() blocked). **Unknown keys are rejected** (`"'{mode}' key '{key}' is not a recognized theme variable"`) — a personality block in a theme file needs an explicit allowlist extension, not a drive-by.
- **A11y rails (must extend, never bypass):** `web/src/design/schemeContrast.test.ts` — structurally asserts EVERY scheme in `SCHEMES` meets WCAG AA (4.5:1) for its accent tokens in both modes, reading the real surface value from `tokens.css`; `web/e2e/a11y.spec.ts` (axe, default scheme); root `MotionConfig reducedMotion="user"` (`web/src/app/App.tsx:133`) + `useReducedMotion` at component level (`ClawMark.tsx:14`, `SidePanel.tsx:46`, `IconButton.tsx:37`).
- **Assistant display name (backend, already wired):** `agent.bot_name` (`config/loader.py:362`, sanitized by `_sanitize_bot_name` :312 — ≤50 chars, markdown/braces stripped), in the `_EDITABLE_CONFIG` PATCH allowlist (`dashboard/handlers/core.py`, `"agent.bot_name"` entry), consumed by `ContextBuilder._bot_name` (`context.py:651` — `AppConfig.load().agent.bot_name or "PersonalClaw"`, the `{{bot_name}}` template var), edited today in `web/src/pages/settings/AccountPanel.tsx` via `api.patchConfig('agent.bot_name', v)`. **The name-override seam exists end-to-end** — this plan drives it from the theme, it does not rebuild it.
- **Persona-per-theme precedent (absorb):** the `lumon` easter egg — `_ChatSession.color_theme` (`dashboard/state.py:376`, validated `{"", "lumon"}` in `chat_handlers.py:86`), `_maybe_inject_persona` (`dashboard/chat_utils.py:438` — appends the bundled `persona-lumon` snippet on first turn), persisted per-session (`chat_persistence.py:489`). Proof that a theme can carry conversational flavor; currently hardcoded to one value and **not connected to the visual theme system at all**.
- **Brand/logo surfaces:** wordmark + mark are `Spark`/`Wordmark` (`web/src/ui/Spark.tsx` — `ClawMark` painted with the scheme `--grad-*` gradient; `Wordmark` takes `label = 'PersonalClaw'` as a prop already), mounted in `NavRail.tsx:138`. Favicon is static (`web/index.html`: `<link rel="icon" href="/claw.svg">`; `web/public/claw.svg`) — no runtime swap exists; nothing in `web/src` touches `document.title` or the favicon link today.
- **Shell mount points for a decorative element:** `TopBar` (`web/src/ui/TopBar.tsx`, used via `ListScaffold.tsx:21` and pages), the app shell in `web/src/app/App.tsx` (ShellCorners + `Toaster` mounts at :308/:382 show the pattern for a shell-level, route-independent element). Error states: per-page `ErrorBoundary` (`web/src/app/ErrorBoundary.tsx` — class component, renders a fallback panel) and `IncidentBanner` (`web/src/app/IncidentBanner.tsx`) are the state-reactive chrome precedents.
- **Audio:** the only Web Audio in the app is TTS playback (`ChatPage.tsx:1426-1440` — `AudioContext` created inside a click gesture, the autoplay-policy-correct pattern). No audio files are shipped; no sound-cue system exists.
- **Gap:** themes are colors only; `bot_name`, the lumon persona, the logo label, and the favicon are four disconnected knobs with no registry tying them into one switchable identity — and no opt-in sound or state-reactive flourish layer exists at all.

## Design

- **S1 — The personality registry + identity behaviors.** A new `web/src/design/personalities.ts` declares `PERSONALITIES: Personality[]` — each entry references a base color `Scheme` (its colors go through the EXISTING scheme mechanism unchanged) and adds a typed, closed `behavior` block: `displayName?` (assistant name), `wordmarkLabel?`, `faviconSvg?` (a bundled asset path under `web/public/`, never a data-URI from user input), `soundCues?` (named Web-Audio synth recipes), `shellElement?` (a registered component id from a closed map — never arbitrary code), `errorTreatment?` (a registered overlay variant id), `personaSnippet?` (a bundled prompt-snippet name, generalizing the lumon mechanism). Applying a personality = `applyScheme(baseSchemeId)` + activating the behavior layer; **standard schemes have no `Personality` entry, so zero personality code runs for them** (the additive guarantee). The active personality id persists in the existing `appearance` localStorage overrides (`personality?: string` — additive key, old payloads load fine). A `PersonalityProvider` (mounted beside `AppearanceProvider` in `App.tsx`) resolves the entry and exposes it via context; it also drives: `document.title` + favicon `<link>` swap (restore defaults on deactivate), `Wordmark label` (already a prop), and — via the existing sanitized seam — `api.patchConfig('agent.bot_name', displayName)` **only on explicit user confirmation** in the activation flow (propose-don't-write: the theme *offers* the rename, a checkbox in the picker confirms it; deactivation offers the restore the same way).
- **S1 — Persona snippet generalization (backend, small).** `_maybe_inject_persona` (`chat_utils.py:438`) generalizes from the hardcoded `"lumon"` to a validated closed set of bundled persona snippets (`persona-<id>` in `prompt_providers/catalog.py`), with the `color_theme` body-field validation in `chat_handlers.py:86` widened to that same set. Clean break: the lumon special case becomes the first entry of the general mechanism (no dual path).
- **S2 — Sound cues, shell element, error treatment, and the two proofs.** `web/src/design/soundCues.ts`: a tiny synth (`playCue(name)`) building oscillator/gain envelopes on a lazily-created `AudioContext` (the ChatPage gesture-gated pattern) — cue points limited to a closed set (`turn_complete`, `approval_needed`, `error`), **opt-in via a master toggle default OFF**, silenced entirely under `prefers-reduced-motion` and when the tab is hidden. `shellElement`: a closed registry map `{id → lazy component}` rendered at the App shell level (the `Toaster` mount pattern, `aria-hidden`, `pointer-events-none`, reduced-motion-static like `DotGlow`). `errorTreatment`: `ErrorBoundary` + `IncidentBanner` read an optional variant class from the personality (visual skin only — same text, same `role="alert"`, same actions; AA-checked). Proofs: **`retro-terminal`** (mono-green scheme fork, `data-ui` nudged toward `cli` density, scanline shell strip, square dot shape, terminal-bell cue, "TERMINAL" wordmark) and **`claw-arcade`** (playful placeholder: bouncy expressiveness preset, sparkle dots, coin-blip cue). Both are placeholders for owner-picked brands — the registry entry shape is the deliverable.
- **A11y invariants (enforced, not promised):** every personality's base scheme goes through `schemeContrast.test.ts` automatically (it iterates `SCHEMES`); a new `personalityA11y.test.ts` structurally asserts each `Personality`: base scheme exists, error treatment preserves `role="alert"` semantics, sound cues declare no autoplay, shell element ids resolve in the closed map. Reduced-motion: shell elements and cues check `useReducedMotion`/the media query themselves *in addition to* the global CSS kill at `tokens.css:398`.
- **Forward hook (mention only, not in scope):** APP-PLATFORM-EVOLUTION may later let apps contribute personality entries through a provider seam (manifest-declared, allowlist-validated server-side like `_THEME_CSS_VARS`); the closed component/cue registries here are designed so that seam can extend them without opening arbitrary-code paths.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Personality registry (`web/src/design/personalities.ts`, new)
```ts
export interface Personality {
  id: string                    // 'retro-terminal' | 'claw-arcade' | …
  label: string
  baseScheme: string            // MUST be an id in SCHEMES (schemes.ts) — colors ride the existing mechanism
  behavior: {
    displayName?: string        // offered agent.bot_name value (user-confirmed, never silent)
    wordmarkLabel?: string      // Wordmark label prop (Spark.tsx)
    faviconHref?: string        // bundled asset under web/public/ only
    personaSnippet?: string     // bundled prompt snippet name ('persona-lumon' generalization)
    soundCues?: Partial<Record<'turn_complete' | 'approval_needed' | 'error', CueRecipe>>
    shellElement?: ShellElementId    // closed union — keys of SHELL_ELEMENTS
    errorTreatment?: ErrorTreatmentId
    uiDensity?: 'comfortable' | 'dense' | 'cli'   // maps to the existing --ui-density select
  }
}
export const PERSONALITIES: Personality[] = [/* retro-terminal, claw-arcade */]
export const SHELL_ELEMENTS: Record<ShellElementId, LazyExoticComponent<…>> = { … } // closed map
```

### C2 — Sound synth (`web/src/design/soundCues.ts`, new — zero audio files)
```ts
export interface CueRecipe { wave: OscillatorType; freqs: number[]; durMs: number; gain: number }
export function playCue(recipe: CueRecipe): void
// Lazily creates ONE AudioContext (the ChatPage.tsx:1438 gesture-gated pattern);
// no-ops when: sounds toggle off (default), prefers-reduced-motion, document.hidden.
```

### C3 — Provider + persistence (additive)
```ts
// appearance.tsx Overrides gains: personality?: string   (localStorage 'appearance'; absent = none)
// PersonalityProvider (web/src/app/personality.tsx, new): resolves the entry, sets
//   document.title / favicon link / data-personality on <html>; restores ALL defaults on deactivate.
// bot_name flows through the EXISTING sanitized PATCH: api.patchConfig('agent.bot_name', v)
//   (core.py "agent.bot_name" allowlist entry, _sanitize_bot_name at loader.py:312) — no new config field.
```

### C4 — Persona-snippet generalization (backend; §2.2 envelope untouched — existing routes)
```python
# dashboard/chat_utils.py — clean-break rename of the lumon special case:
_PERSONA_THEMES: frozenset[str]  # closed set of bundled persona-<id> snippet ids
def _maybe_inject_persona(message: str, color_theme: str, is_new: bool) -> str: ...
    # generalizes: color_theme in _PERSONA_THEMES → render_snippet_block(f"persona-{color_theme}")
# chat_handlers.py:86 validation widens {"", "lumon"} → {""} | _PERSONA_THEMES
```

### C5 — A11y guard tests (structural, red-on-regression)
```ts
// web/src/design/personalityA11y.test.ts (new):
//   every Personality.baseScheme ∈ SCHEMES (⇒ covered by schemeContrast.test.ts's AA sweep);
//   shellElement/errorTreatment ids resolve in their closed maps;
//   no personality declares a cue without the master-toggle gate.
```

### Integration points
- **Calls:** `applyScheme` + the `Overrides` store (`appearance.tsx`), `Wordmark` label prop (`ui/Spark.tsx`), `api.patchConfig('agent.bot_name', …)` (`lib/api.ts` → `handlers/core.py` allowlist), `render_snippet_block` via `_maybe_inject_persona` (`chat_utils.py:438`), `runtime` dials (`design/runtime.ts`) for expressiveness presets, `useReducedMotion`/root `MotionConfig` (`App.tsx:133`).
- **Called by:** `DesignPanel.tsx` (the picker gains a Personality section), `App.tsx` (provider mount + shell-element slot), `ErrorBoundary.tsx`/`IncidentBanner.tsx` (optional treatment variant).
- **Storage owned:** the `personality` key inside the existing `appearance` localStorage payload; two bundled `persona-<id>.md` snippets in `src/personalclaw/config/prompt_snippets/`. No new server files; `/api/themes` custom themes remain colors-only in this plan (personality entries are first-party code — the app-provider seam is the forward hook).
- **Zero telemetry:** activation/cue events are never logged anywhere (not even SEL — this is pure presentation, not security-relevant).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Registry + identity layer + persona generalization

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `personalities.ts` registry + types (C1) with the two proof entries stubbed (base schemes: a mono-green fork added to `SCHEMES` for retro-terminal; an existing scheme for claw-arcade) | `web/src/design/personalities.ts` (new), `web/src/design/schemes.ts` (one added scheme) | registry typechecks; new scheme passes `schemeContrast.test.ts` in both modes without weakening the test |
| T1.2 | `PersonalityProvider`: resolve + apply (title, favicon link swap, `data-personality` attr, wordmark label via context), persist `personality` in the `appearance` overrides, full default-restore on deactivate; standard-scheme path provably untouched | `web/src/app/personality.tsx` (new), `web/src/app/appearance.tsx` (additive key), `web/src/app/App.tsx` (mount), `web/src/ui/Spark.tsx` (consume label from context, default unchanged) | activating swaps title/favicon/wordmark; deactivating restores byte-identical defaults; with no personality active, a DOM/behavior snapshot matches pre-plan (test) |
| T1.3 | Display-name offer flow: activation dialog with an explicit "Also rename the assistant to <name>" checkbox → `api.patchConfig('agent.bot_name', …)`; deactivation offers the restore; never silent (propose-don't-write) | `web/src/pages/settings/DesignPanel.tsx` (Personality picker section + dialog) | unchecked = `bot_name` untouched; checked = `{{bot_name}}` resolves to the new name in a fresh chat; restore offer works |
| T1.4 | Persona generalization (C4): `_PERSONA_THEMES` closed set, `_maybe_inject_persona` generalized, validation widened, lumon becomes entry #1 (clean break — no special case remains); second bundled snippet for retro-terminal | `src/personalclaw/dashboard/chat_utils.py`, `src/personalclaw/dashboard/chat_handlers.py`, `src/personalclaw/prompt_providers/catalog.py`, `src/personalclaw/config/prompt_snippets/persona-retro-terminal.md` (new) | existing lumon tests stay green through the general path; an unknown theme value is still rejected 400 |
| V1 | Validation (as a user): activate retro-terminal → colors + wordmark + favicon + title + (confirmed) name all flip; new chat carries the persona; switch back to coral → everything restores; `schemeContrast` + typecheck + vitest green | — | holds |

### Session 2 — Sound cues + shell element + error treatment + proofs polished

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `soundCues.ts` synth (C2) + master toggle (default OFF) in the Personality picker + cue wiring at the three cue points (turn settled / approval requested / error toast) behind the toggle, reduced-motion, and `document.hidden` gates | `web/src/design/soundCues.ts` (new), `web/src/pages/ChatPage.tsx` (turn-settled hook beside `sessionSkillsEpoch`), `web/src/app/useApprovalToasts.ts`, `web/src/ui/Toaster.tsx` | no sound ever plays with the toggle off or reduced-motion on (tests mock the media query); no audio file exists in the bundle (CI grep) |
| T2.2 | Shell-element closed registry + the retro-terminal scanline strip component (aria-hidden, pointer-events-none, static under reduced-motion — the `DotGlow` discipline), mounted at the App-shell slot | `web/src/design/personalities.ts` (SHELL_ELEMENTS), `web/src/ui/personality/TerminalStrip.tsx` (new), `web/src/app/App.tsx` | element renders only under its personality; axe pass unchanged; reduced-motion = static frame |
| T2.3 | Error-treatment variants: `ErrorBoundary` fallback + `IncidentBanner` accept an optional visual variant from the personality context — skin only (same copy, `role="alert"`, actions) | `web/src/app/ErrorBoundary.tsx`, `web/src/app/IncidentBanner.tsx` | forced error under each personality renders its treatment; under a standard scheme both are pixel-identical to today |
| T2.4 | `personalityA11y.test.ts` (C5) + finish the `claw-arcade` proof (expressiveness preset via `runtime`, sparkle dot shape, coin-blip cue) | `web/src/design/personalityA11y.test.ts` (new), `personalities.ts` | structural test red on: unknown base scheme, dangling element id, ungated cue; both proofs fully switchable |
| V2 | Validation (as a user, dev home): full tour of both personalities across chat/settings/error states, sounds on and off, reduced-motion on and off, dark and light; then a standard scheme — zero residue (title, favicon, name, DOM). `npm run typecheck && npm test && npm run build` + e2e a11y green | — | holds |

## Owner tasks (real world)
1. **Pick the real brands/skins** the placeholders become (retro-terminal + claw-arcade are generic stand-ins) — the registry entry shape is stable; swapping content is a follow-up commit per brand.
2. **Decide the sound-cue default posture** — plan ships master toggle OFF (opt-in); confirm you don't want opt-out instead after dogfooding.
3. **Approve the display-name offer wording** in the activation dialog — it writes `agent.bot_name` (visible in every prompt via `{{bot_name}}`), so the consent copy matters.
4. **Ratify the forward hook**: app-contributed personalities wait for APP-PLATFORM-EVOLUTION's provider seam — no third-party path ships here.

## Risks & open questions
- **Favicon/title residue** after a crash mid-personality (defaults restored on deactivate, but a hard reload under an active personality must re-apply, not leak) — the provider re-applies from the persisted override on mount and restores on `personality: undefined`; T1.2's restore test covers the reload path.
- **`bot_name` is instance-global, the personality is per-browser** (localStorage) — a personality activated on one device renames the assistant everywhere. Mitigated by the explicit checkbox (informed consent) + the restore offer; if this bites, the fix is per-entity display name in `entity_settings/` — DISCOVERY-file it, don't build it speculatively.
- **Scope creep into a skinning engine** — the closed `SHELL_ELEMENTS`/treatment maps are the rail: no arbitrary components, no user-supplied code/CSS beyond the already-sanitized color vars. Any pressure to open them = E6, stop.
- **Open:** should the personality also pin dark/light mode (a brand that only works dark)? Deferred — `theme.tsx` ownership stays untouched this plan; a `preferredMode` hint could be additive later.

---

## Execution log

- [2026-07-28][S1] DONE (the personality registry + identity behaviors). (a) **`web/src/design/personalities.ts`** — `Personality` + a CLOSED, typed `behavior` block (`displayName`, `wordmarkLabel`, `faviconHref`, `personaSnippet`, `uiDensity`, `documentTitle`). Three constraints are the design: colors ride the EXISTING scheme mechanism (a personality names a `baseScheme`, never its own colors — which is what puts every personality's palette inside `schemeContrast.test.ts`'s WCAG sweep automatically); the behavior block is closed (no arbitrary CSS/markup/code, so a future app-contributed entry can be allowlist-validated instead of opening a code path); anything touching SAVED state is proposed, never applied. Three entries: `personalclaw` (the DEFAULT — selecting it IS deactivation, so restore is one code path with no special case) + the two plan-specified placeholders `retro-terminal` and `claw-arcade`. (b) **New `phosphor` scheme** (mono-green CRT) added to `SCHEMES`; it passed the existing 73-assertion contrast sweep on first run — light-mode greens darkened to hold ≥4.5:1 both as a white-text fill and as text on white, same rule as the other schemes. (c) **`web/src/app/personality.tsx`** — applies title/favicon/`data-personality`/wordmark, mounted INSIDE `AppearanceProvider` so colors+density go through `applyScheme`/`setSelect` rather than a parallel mechanism; persists to `localStorage`; tolerates use outside the provider (identity is decoration, never a hard dependency). **Full restore is the load-bearing property**: the pristine title + favicon are captured at MODULE scope (so a remount can't overwrite pristine values with already-personalized ones) and written back on default. (d) **`PersonalityPicker`** in Settings → Design, above the scheme grid (personality is the coarse choice that SETS the scheme, so it reads top-down). The rename offer is a Toggle in a confirm dialog naming the exact config key it would write; declining changes the look and leaves `agent.bot_name` untouched. A colors-only switch skips the dialog entirely — it's instantly reversible, so a confirm would just be a speed bump. (e) **Backend generalization (T1.4, clean break)** — `_PERSONA_THEMES` closed frozenset + `persona_themes()`; `_maybe_inject_persona` now handles any member and `_cached_persona(theme)` replaced `_cached_lumon_persona` (the hardcoded lumon branch is GONE — lumon is entry #1). `chat_handlers` validation imports the SAME set, so accepted values and injectable personas cannot drift. New bundled `persona-retro-terminal.md` + catalog entry. Verified the closed set rejects `persona-lumon`, `../etc/passwd`, and unknown themes (all coerce to `""` = no persona, the pre-existing behavior). 11 structural tests in `personalityA11y.test.ts` (base scheme exists, behavior keys within the closed set, favicons local-only, `persona-<id>` naming, default has no rename + resolves, unknown id falls back).
- [2026-07-28][S1] Gate: `make lint` green (519 files) · `make test` **8345 passed (50.7s)** · web typecheck + **268** vitest + build green. The primitive-adoption ratchet fired TWICE and both were fixed by ADOPTION, never a baseline bump: a raw Save `<button>` → `Button`, and a raw `<input type=checkbox>` → `Toggle` (the kit has no checkbox primitive; Toggle is its binary control). LANDMINE for future sessions: the adoption scanner counts the literal string `<input` in SOURCE TEXT — including inside a comment. My comment explaining why I avoided a raw checkbox tripped the ratchet; reworded.
- [2026-07-28][S1] Validated as-a-user on :10026 — the full activate/restore tour. Activating Retro Terminal flipped ALL FIVE behaviors together (title `TERM://PersonalClaw`, wordmark `TERM://PC`, scheme `phosphor`, density `cli`, `agent.bot_name` = `TERM`); switching back to PersonalClaw restored every one (title, wordmark, `coral`, `comfortable`, and `bot_name` cleared to `""`); switching to Claw Arcade with the rename toggle OFF changed the palette to amber while leaving `bot_name` empty — propose-don't-write proven, not asserted. `persona-retro-terminal` appears in `GET /api/prompt-snippets`. Zero console errors. (The gateway log's 6 tracebacks are "no model provider configured" from my `curl` chat probes on a fresh home — expected, unrelated.)
- [2026-07-28][S1] OWNER REVIEW WANTED (PR-time, not a blocker — plan owner task 3): the consent-dialog sentence, since it writes `agent.bot_name`. Current copy: "Also rename the assistant to **TERM**. This writes `agent.bot_name`, which the assistant uses to refer to itself. Turn it off to keep the name you have." Reverse direction: "Also restore the assistant's default name. This clears `agent.bot_name`, so it goes back to PersonalClaw."
- [2026-07-28][S1] Remaining: **S2** (sound cues via a tiny synth — opt-in, default OFF, silenced under reduced-motion; the shell decorative element; error treatments; and the two proof personalities fleshed out). Owner task 1 (pick real brand identities) is explicitly NOT blocking — the plan states both entries are placeholders and "the registry entry shape is the deliverable".
