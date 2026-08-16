/**
 * ERROR TREATMENTS — a personality's skin for the two error surfaces.
 *
 * PERSONALITY-THEMES §S2 (T2.3). A personality may nudge how a failure LOOKS —
 * a terminal frame, an arcade panel — without touching what a failure SAYS or
 * what a user can DO about it. Three rules make that safe rather than aspirational:
 *
 * 1. **Skin only.** A treatment carries presentation and nothing else: two class
 *    strings and three colour tokens. There is no slot for copy, no slot for an
 *    action, and no slot for a role — so a treatment structurally cannot change
 *    the message, the buttons, or the semantics of an error surface. The guard is
 *    the type, not a review note.
 * 2. **Opt-in per personality.** The default identity declares no treatment, so a
 *    standard scheme renders the error surfaces byte-identically to a build with
 *    none of this code (asserted against frozen pre-change markup in
 *    `app/errorTreatmentSkin.test.tsx`).
 * 3. **AA by construction.** Colours are named as design tokens, never literals,
 *    and every triple is measured against `tokens.css` in BOTH modes by
 *    `errorTreatments.test.ts`. An unreadable error surface is worse than an ugly
 *    one, so the contrast bar is enforced where the treatment is declared.
 *
 * Colour arrives as an inline `style` (both surfaces already paint that way)
 * rather than as a `bg-*`/`text-*` class, deliberately: two colour utilities on
 * one element resolve by stylesheet order, not by class order, so an appended
 * class is not reliably the winner. Inline always wins — the treatment's colour
 * is the colour that renders.
 */

/** The closed set of treatment ids. Adding a member is the deliberate act; the
 *  registry below and the AA sweep both fail on a member without an entry. */
export type ErrorTreatmentId = 'terminal-frame' | 'arcade-panel'

export interface ErrorTreatment {
  id: ErrorTreatmentId
  /** Human label for tests and any future picker. NEVER rendered as error copy. */
  label: string
  /** Utility classes appended to the error surface's outer element. */
  surfaceClass: string
  /** Utility classes for the alert glyph — REPLACES the base ink class, because
   *  two colour utilities on one element are resolved by stylesheet order. */
  iconClass: string
  /** Tokens this treatment paints. `bg`/`ink` are the surface pair; `icon` is the
   *  glyph ink. Every pairing is asserted ≥ 4.5:1 in dark AND light. */
  paint: { bg: string; ink: string; icon: string }
}

export const ERROR_TREATMENTS: Record<ErrorTreatmentId, ErrorTreatment> = {
  // Retro Terminal: a hard-edged frame, mono type, wide tracking — a machine
  // reporting a fault rather than an app apologising for one.
  'terminal-frame': {
    id: 'terminal-frame',
    label: 'Terminal frame',
    surfaceClass: 'font-mono tracking-wide rounded-none border border-danger',
    iconClass: 'text-danger',
    paint: { bg: '--color-surface-container', ink: '--color-on-surface', icon: '--color-danger' },
  },
  // Claw Arcade: a dashed cabinet panel with a heavier frame — playful, still
  // unmistakably an error (the frame and glyph stay on the danger token).
  'arcade-panel': {
    id: 'arcade-panel',
    label: 'Arcade panel',
    surfaceClass: 'rounded-xl border-2 border-dashed border-danger',
    iconClass: 'text-danger',
    paint: { bg: '--color-surface-high', ink: '--color-on-surface', icon: '--color-danger' },
  },
}

/** The treatment for an id, or `null` for `undefined` and for an id that no
 *  longer exists. Total and non-throwing on purpose: this is called while an
 *  error surface is already rendering, and a throw there replaces one broken page
 *  with a blank app.
 *
 *  `Object.hasOwn`, not `map[id] ?? null`: a plain index reads the PROTOTYPE CHAIN,
 *  so `getErrorTreatment('constructor')` used to return the `Object` constructor
 *  instead of `null` — and `treatmentPaint` then threw `Cannot read properties of
 *  undefined (reading 'bg')`, on the one render path this function's own contract
 *  says must never throw. Found while closing the same hole in `getShellElement`
 *  (PT-3); the two registries now resolve identically. */
export function getErrorTreatment(id: string | undefined): ErrorTreatment | null {
  if (!id || !Object.hasOwn(ERROR_TREATMENTS, id)) return null
  return ERROR_TREATMENTS[id as ErrorTreatmentId]
}

/** The inline colour declarations for a treatment — `null` when there is none, so
 *  a spread of the result leaves the base style untouched. */
export function treatmentPaint(t: ErrorTreatment | null): { background: string; color: string } | null {
  if (!t) return null
  return { background: `var(${t.paint.bg})`, color: `var(${t.paint.ink})` }
}
