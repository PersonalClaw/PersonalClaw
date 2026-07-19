// PersonalClaw motion presets for Framer Motion.
// Two families: SPATIAL springs carry visible overshoot (position/scale); EFFECTS
// are critically damped (opacity/color/content). On top, named BOUNCE tiers add
// earned personality — applied with discipline (§3.5: ~3-4 moments), scaled by the
// user-tunable `runtime.bounciness` (0 calm … 1 playful; default playful).

import type { Transition, Variants } from 'framer-motion'

import { runtime } from './runtime'

export const spring = {
  /** default.spatial — gentle settle */
  spatialDefault: { type: 'spring', stiffness: 380, damping: 30, mass: 1 } as Transition,
  /** fast.spatial — snappy with a little overshoot */
  spatialFast: { type: 'spring', stiffness: 800, damping: 34, mass: 1 } as Transition,
  /** slow.spatial — soft, expressive */
  spatialSlow: { type: 'spring', stiffness: 200, damping: 26, mass: 1 } as Transition,
  /** effects — critically damped, no bounce (opacity/color) */
  effects: { duration: 0.2, ease: [0.2, 0, 0, 1] } as Transition,
}

/** A bounce spring whose overshoot scales with `runtime.bounciness` (0 → critically
 *  damped, 1 → the given playful damping). Lets one slider dial the whole app's
 *  personality without touching call sites. Lower damping = more overshoot. */
function bouncy(stiffness: number, dampingAtPlayful: number, calmDamping: number): Transition {
  const b = Math.max(0, Math.min(1, runtime.bounciness))
  // Interpolate damping from calm (high, no overshoot) → playful (low, overshoot).
  const damping = calmDamping + (dampingAtPlayful - calmDamping) * b
  return { type: 'spring', stiffness, damping, mass: 1 }
}

export const bounce = {
  /** light overshoot — press-release, hover settles */
  get subtle(): Transition { return bouncy(520, 26, 40) },
  /** fun overshoot — menu/popover open, success bloom */
  get playful(): Transition { return bouncy(600, 16, 42) },
  /** float-up entrance */
  get lift(): Transition { return bouncy(300, 22, 34) },
  /** large layout shifts settle */
  get settle(): Transition { return bouncy(220, 24, 30) },
}

export const ease = {
  // PersonalClaw curves — smooth, NOT the literal Material M3 values.
  emphasized: [0.22, 0.61, 0.13, 1] as [number, number, number, number],
  emphasizedDecel: [0.08, 0.7, 0.12, 1] as [number, number, number, number],
  emphasizedAccel: [0.34, 0, 0.75, 0.12] as [number, number, number, number],
}

export const duration = { short: 0.1, medium: 0.3, long: 0.5 }

/** Message entrance — rise + fade with emphasized-decelerate (entrances). */
export const messageEnter: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: duration.medium, ease: ease.emphasizedDecel } },
}

/** Menu / sheet entrance — spring scale+fade with an earned playful bounce (one
 *  of the ~3-4 sanctioned bounce moments; scales with the bounciness slider). */
export const overlayEnter: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 4 },
  animate: { opacity: 1, scale: 1, y: 0, transition: bounce.playful },
  exit: { opacity: 0, scale: 0.98, transition: spring.effects },
}

/** Thinking glow — slow opacity/scale pulse. */
export const thinkingPulse: Variants = {
  animate: {
    opacity: [0.45, 0.85, 0.45],
    scale: [1, 1.04, 1],
    transition: { duration: 3.2, ease: 'easeInOut', repeat: Infinity },
  },
}

/** Press feedback for buttons/chips — press in, spring back with a subtle bounce. */
export const pressable = {
  whileTap: { scale: 0.96, transition: spring.spatialFast },
  whileHover: { scale: 1.02, transition: bounce.subtle },
}

// ─────────────────────────────────────────────────────────────────────────
// Component-redesign Slice 0 — researched, tokenized presets shared across the
// per-component sweep. These are aliases/companions to the spring/bounce tiers
// above, named by the technique-selection map (§4) so call sites read intent.
// ─────────────────────────────────────────────────────────────────────────

/** Named spring tiers for the redesign sweep. `gentle` = soft settle (large
 *  surfaces), `snappy` = fast low-overshoot (controls), `bouncy` = earned
 *  personality (scales with the bounciness knob, one of the sanctioned moments). */
export const springs = {
  get gentle(): Transition { return spring.spatialSlow },
  get snappy(): Transition { return spring.spatialFast },
  get bouncy(): Transition { return bounce.playful },
}

/** Stagger a container's children by a fixed step. Use on list/grid entrances so
 *  rows cascade instead of popping in together (§4 choreography). */
export function stagger(step = 0.04, delayChildren = 0): Transition {
  return { staggerChildren: step, delayChildren }
}

/** A list-item entrance variant pair — rise+fade, emphasized-decelerate. Pair
 *  with a parent `variants={{ animate: { transition: stagger() } }}`. */
export const listItemEnter: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: duration.medium, ease: ease.emphasizedDecel } },
}

// ─────────────────────────────────────────────────────────────────────────
// v2 — Expressiveness: the primary intensity dial (runtime.expressiveness,
// 0 refined … 1 bold; default 0.8). EVERY expressive treatment scales through
// `expr()` so one Design-panel knob governs the whole motion/morph/3D language.
// Reduced-motion overrides this to near-static independently (MotionConfig +
// the global CSS rule) — expr() is the *aesthetic* dial, not the a11y switch.
// ─────────────────────────────────────────────────────────────────────────

/** Scale an intensity value by the current expressiveness (0..1). `floor` is the
 *  fraction kept at expressiveness 0 (so refined ≠ dead — e.g. a control still
 *  gets a hint of its move). `expr(x)` → x·(floor + (1-floor)·expressiveness).
 *  Examples: `expr(3, 0.3)` = hover-lift px (keeps 30% when refined); `expr(0.05, 0.4)`
 *  = press-scale bonus; `expr(1.06, 0.5)` = hover scale that halves its *bonus*
 *  when refined. */
export function expr(max: number, floor = 0.35): number {
  const e = Math.max(0, Math.min(1, runtime.expressiveness))
  return max * (floor + (1 - floor) * e)
}

/** True when expressiveness is high enough to warrant a HEAVY effect (gooey
 *  merge, big 3D flip, particle burst). Below the threshold those effects are
 *  skipped entirely (the refined tier shouldn't just shrink them, it drops them).
 *  Default gate 0.5 → heavy effects are on for the bold-leaning default (0.8). */
export function exprHeavy(threshold = 0.5): boolean {
  return runtime.expressiveness >= threshold
}

/** Run a DOM update inside a View Transition when the platform supports it,
 *  else run it synchronously (graceful fallback). Guards
 *  `document.startViewTransition` per §9. Honors reduced-motion (skips the
 *  transition) via the caller passing `reduce`. */
export function viewTransition(update: () => void, reduce = false): void {
  const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown }
  if (reduce || typeof doc.startViewTransition !== 'function') { update(); return }
  doc.startViewTransition(update)
}
