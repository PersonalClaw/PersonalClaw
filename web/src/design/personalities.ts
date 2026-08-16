/**
 * PERSONALITIES — a theme that carries behavior, not just colors.
 *
 * Today the app has four disconnected identity knobs: the color scheme, the
 * assistant's name (`agent.bot_name`), the wordmark label, and the favicon. A
 * "personality" ties them into ONE switchable identity, so picking retro-terminal
 * doesn't just recolor the UI — it renames the wordmark, swaps the favicon, and
 * offers to rename the assistant.
 *
 * Three constraints keep this from becoming a theming free-for-all:
 *
 * 1. **Colors ride the EXISTING scheme mechanism, unchanged.** A personality
 *    references a `baseScheme` id from `SCHEMES`; it never carries its own color
 *    values. That means every personality's palette is automatically covered by
 *    `schemeContrast.test.ts` (which iterates SCHEMES), so a personality cannot
 *    smuggle in an inaccessible palette.
 * 2. **The behavior block is a CLOSED, typed set.** No arbitrary CSS, no
 *    injected markup, no per-personality code. Adding a capability means adding a
 *    field here deliberately — which is what lets a future provider seam
 *    (APP-PLATFORM-EVOLUTION) validate app-contributed entries against an
 *    allowlist instead of opening an arbitrary-code path.
 * 3. **Anything that changes SAVED state is proposed, never applied.**
 *    `displayName` is the clear case: renaming the assistant writes
 *    `agent.bot_name`, real config the user may have set deliberately. So it is
 *    offered with a checkbox at activation and restored by offer on deactivation
 *    — propose-don't-write.
 *
 * The two entries below are deliberately PLACEHOLDER identities: the deliverable
 * of this session is the registry shape and the switching behavior, not a final
 * brand set (the plan says so explicitly). Real personalities can be swapped in
 * without touching any of the machinery.
 */

import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

import type { ErrorTreatmentId } from './errorTreatments'
import type { DotPattern, DotShape } from './runtime'
import type { CueName, CuePoint } from './soundCues'

/** The closed set of shell-element ids. A personality may mount ONE registered
 *  decorative component at the App shell — never arbitrary code, never markup it
 *  supplies itself. Adding a member is the deliberate act: `SHELL_ELEMENTS` below
 *  is a total `Record`, so a new id without an entry is a compile error, and an id
 *  that is not a member cannot be written into `behavior.shellElement` at all. */
export type ShellElementId = 'terminal-scanlines'

/** The closed shell-element registry: id → LAZY component.
 *
 *  Lazy is load-bearing, not a nicety. The shell mounts this from `App.tsx`, which
 *  is in the entry chunk — a static import would put every personality's chrome in
 *  the initial bundle of every user, including the (overwhelming) majority running
 *  a standard scheme with no shell element at all. `lazy()` puts each one in its own
 *  chunk that is fetched only when its personality is active. `shellElements.test.tsx`
 *  asserts both halves: every value is a React lazy type, and nothing outside this
 *  file statically imports `ui/personality/`.
 *
 *  Every registered component must satisfy the decorative contract — `aria-hidden`,
 *  `pointer-events-none`, and a `data-shell-element` marker naming its own id — which
 *  the same test enforces by RENDERING each entry rather than by reading its source. */
export const SHELL_ELEMENTS: Record<ShellElementId, LazyExoticComponent<ComponentType>> = {
  'terminal-scanlines': lazy(() =>
    import('../ui/personality/TerminalStrip').then((m) => ({ default: m.TerminalStrip })),
  ),
}

/** The registered component for an id, or `null` for `undefined` and for an id that
 *  is not a member — the runtime half of "closed". Total and non-throwing to match
 *  `getErrorTreatment`: this resolves during a shell render, where a throw would
 *  blank the app over a piece of decoration.
 *
 *  `Object.hasOwn` rather than `map[id] ?? null`, because a plain index reads the
 *  PROTOTYPE CHAIN: `'constructor'` and `'toString'` are not members, but they are
 *  found, so `??` never fires and the caller is handed `Object` to render as a
 *  component. That is not theoretical — the sibling `getErrorTreatment` had the same
 *  shape and `treatmentPaint` threw on the object it returned (fixed alongside this;
 *  see PT-3's execution log). An own-key test is what makes "closed" hold for every
 *  string, not just the plausible ones. */
export function getShellElement(id: string | undefined): LazyExoticComponent<ComponentType> | null {
  if (!id || !Object.hasOwn(SHELL_ELEMENTS, id)) return null
  return SHELL_ELEMENTS[id as ShellElementId]
}

/** Motion/backdrop dials a personality presets at activation.
 *
 *  Each key names an EXISTING token in `tokenRegistry.ts` (see
 *  `PERSONALITY_DIAL_TOKENS`), so a preset is applied by writing that token through
 *  the appearance store — the same call the slider in Settings → Appearance makes.
 *  Nothing here writes `design/runtime.ts` directly, which matters twice: the
 *  appearance store's own bridge stays the single writer of the canvas runtime, and
 *  the preset lands in the user's saved overrides, so they can dial it straight back
 *  afterwards and it sticks. A personality SEEDS these; it does not own them.
 *
 *  Deliberately four dials, not "any token". An identity gets to arrive with a
 *  motion temperament and a dot glyph; it does not get to resize the type, recolor
 *  outside its `baseScheme`, or reach the layout. */
export interface PersonalityDials {
  /** `--expressiveness`: the primary motion-intensity dial (0 refined … 1 bold). */
  expressiveness?: number
  /** `--bounciness`: spring overshoot (0 calm … 1 playful). */
  bounciness?: number
  /** `--dot-shape`: the glyph the halftone backdrop paints. */
  dotShape?: DotShape
  /** `--dot-pattern`: the lattice the dots sit on. */
  dotPattern?: DotPattern
}

/** dial → the token varName it writes. One declaration, so the provider that applies
 *  a preset and the rail that proves every dial names a REAL token read the same map
 *  (a dial pointing at a token that no longer exists would silently do nothing). */
export const PERSONALITY_DIAL_TOKENS: Record<keyof PersonalityDials, string> = {
  expressiveness: '--expressiveness',
  bounciness: '--bounciness',
  dotShape: '--dot-shape',
  dotPattern: '--dot-pattern',
}

/** Assistant-name + prompt-persona + chrome behaviors a personality may carry.
 *  Every field is optional: a personality that only recolors is valid. */
export interface PersonalityBehavior {
  /** Assistant name OFFERED at activation (writes `agent.bot_name` on consent). */
  displayName?: string
  /** Wordmark text in the shell (the `Wordmark` label prop). */
  wordmarkLabel?: string
  /** Favicon path — bundled assets under `web/public/` only, never a remote URL. */
  faviconHref?: string
  /** Bundled prompt-snippet id (`persona-<id>`); the backend validates it against
   *  its own closed set, so a bad value here degrades to "no persona". */
  personaSnippet?: string
  /** Maps to the existing `--ui-density` select — no new mechanism. */
  uiDensity?: 'comfortable' | 'dense' | 'cli'
  /** Browser tab title. */
  documentTitle?: string
  /** One decorative component mounted at the App shell — an id from the closed
   *  `SHELL_ELEMENTS` map, never a component reference and never markup. The map's
   *  contract (aria-hidden, pointer-events-none, static under reduced motion) is
   *  what keeps this a purely visual layer: a shell element cannot be reached by a
   *  pointer or by assistive tech, so it can add atmosphere but not content, not a
   *  control, and not a second reading order. Omitted = nothing mounts. */
  shellElement?: ShellElementId
  /** Skin for the two error surfaces (ErrorBoundary fallback, IncidentBanner) —
   *  an id from the closed `ERROR_TREATMENTS` map. Presentation only: the shape
   *  of a treatment has no room for copy, actions or roles, so a personality
   *  cannot reword or disarm a failure. Omitted = today's treatment, unchanged. */
  errorTreatment?: ErrorTreatmentId
  /** Re-voice one or more of the three cue POINTS with another registered cue from
   *  `design/soundCues.ts`. Both sides are closed: the key must be a cue point and
   *  the value a registered voice, so an identity can change what a moment sounds
   *  like but cannot add a moment, cannot author a tone, and cannot make sound the
   *  master toggle hasn't allowed — every cue still plays through `playCue`, which
   *  owns the toggle, reduced-motion and hidden-tab gates. Omitted = the default
   *  voices; the master toggle is OFF by default, so an identity that declares a
   *  voice is still silent until the user asks for sound. */
  soundCues?: Partial<Record<CuePoint, CueName>>
  /** Motion/backdrop dials this identity arrives with, seeded into the user's own
   *  appearance overrides at activation (see `PersonalityDials`). Omitted = the
   *  token defaults, which is what makes a standard scheme's motion untouched. */
  dials?: PersonalityDials
}

export interface Personality {
  id: string
  label: string
  /** One-line description shown in the picker. */
  hint: string
  /** MUST be an id in `SCHEMES` — colors ride the existing scheme mechanism. */
  baseScheme: string
  behavior: PersonalityBehavior
}

/** The default identity: PersonalClaw itself. Selecting it is what "off" means,
 *  so deactivation is just activating this — one code path, no special case. */
export const DEFAULT_PERSONALITY = 'personalclaw'

export const PERSONALITIES: Personality[] = [
  {
    id: DEFAULT_PERSONALITY,
    label: 'PersonalClaw',
    hint: 'The default identity — coral, the claw wordmark, no persona.',
    baseScheme: 'coral',
    behavior: {
      wordmarkLabel: 'PersonalClaw',
      documentTitle: 'PersonalClaw',
      faviconHref: '/favicon.svg',
    },
  },
  {
    // Placeholder identity #1 (plan: "the registry entry shape is the deliverable").
    // Every field in the closed block is exercised by one of the two proofs, so the
    // registry shape is demonstrated rather than described — this one carries the
    // persona snippet and the shell element; the arcade carries neither.
    id: 'retro-terminal',
    label: 'Retro Terminal',
    hint: 'Mono-green phosphor, dense CLI spacing, a scanline haze, and a terse operator voice.',
    baseScheme: 'phosphor',
    behavior: {
      displayName: 'TERM',
      wordmarkLabel: 'TERM://PC',
      documentTitle: 'TERM://PersonalClaw',
      faviconHref: '/favicon.svg',
      personaSnippet: 'persona-retro-terminal',
      uiDensity: 'cli',
      shellElement: 'terminal-scanlines',
      errorTreatment: 'terminal-frame',
      // The ASCII BEL: what a terminal actually does when a job wants you.
      soundCues: { approval_needed: 'terminal_bell' },
      // A CRT is orderly and unhurried: square dots on a straight lattice, and the
      // motion language pulled down to its refined end — no overshoot at all, so
      // panels arrive flat instead of springing.
      dials: { expressiveness: 0.25, bounciness: 0, dotShape: 'square', dotPattern: 'grid' },
    },
  },
  {
    // Placeholder identity #2 — deliberately reuses an existing scheme, proving a
    // personality needs no new palette to be a distinct identity. Its whole
    // character is carried by the dials, the cue voice and the error skin.
    id: 'claw-arcade',
    label: 'Claw Arcade',
    hint: 'Amber cabinet glow, sparkle dots, bouncy motion, and a playful, high-energy voice.',
    baseScheme: 'amber',
    behavior: {
      displayName: 'CLAW-1',
      wordmarkLabel: 'CLAW ARCADE',
      documentTitle: 'CLAW ARCADE',
      faviconHref: '/favicon.svg',
      uiDensity: 'comfortable',
      errorTreatment: 'arcade-panel',
      // A finished turn is a credit accepted.
      soundCues: { turn_complete: 'coin_blip' },
      // The opposite temperament to the terminal's, on the same four dials: motion
      // at full tilt, and a sparkle glyph on an offset lattice so the backdrop reads
      // as an attract screen rather than a graph.
      dials: { expressiveness: 1, bounciness: 1, dotShape: 'sparkle', dotPattern: 'diamond' },
    },
  },
]

export function getPersonality(id: string | undefined): Personality | undefined {
  return PERSONALITIES.find((p) => p.id === id)
}

/** The personality to treat as active for an id that no longer exists (a saved
 *  override from a removed entry) — never leave the shell in a half-applied state. */
export function resolvePersonality(id: string | undefined): Personality {
  return getPersonality(id) ?? getPersonality(DEFAULT_PERSONALITY) ?? PERSONALITIES[0]
}
