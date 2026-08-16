// PersonalClaw motion presets for Framer Motion.
//
// TWO families, and only two — pick by whether the move carries personality:
//   • `spring` — the raw tiers. Unscaled spatial defaults + `effects`, the
//     critically-damped transition for opacity/color/content (no overshoot ever).
//   • `physics` — the four named PRESETS (snappy/smooth/fluid/playful). Every one
//     is built by `bouncy()`, so every one scales with the user's `--bounciness`
//     slider and collapses to `instant` under `prefers-reduced-motion`.
// Reach for `physics` for anything spatial with character; reach for `spring.effects`
// for a fade. There is deliberately no third set of names — see docs/design/motion.md.

import type { Transition, Variants } from 'framer-motion'

import { runtime } from './runtime'

/** True when the user has asked the platform for less motion.
 *
 *  Read at CALL time, never cached: the presets below are getters, so a mid-session
 *  OS change (or a test flipping the query) is honored on the next animation instead
 *  of whatever the media query happened to say at import. `matchMedia` is guarded for
 *  non-browser callers (SSR, a bare-node test importing this module). */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** The terminal reduced-motion transition: an instant swap with ZERO spring.
 *
 *  `type: 'tween'` is explicit on purpose. Several call sites spread a preset and
 *  override one field (`{ ...physics.fluid, stiffness: 240 }`); without an explicit
 *  type, a leftover `stiffness` would let Framer infer a spring again and the
 *  reduced-motion collapse would leak right back through the spread. */
export const instant: Transition = { type: 'tween', duration: 0 }

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
 *  personality without touching call sites. Lower damping = more overshoot.
 *
 *  This is THE gate for both budget dials that matter here: it is the single place
 *  the bounciness slider enters the spring family, and the single place
 *  `prefers-reduced-motion` zeroes it. Every named preset routes through it, so no
 *  preset can be added that silently escapes either. */
function bouncy(stiffness: number, dampingAtPlayful: number, calmDamping: number): Transition {
  if (prefersReducedMotion()) return instant
  const b = Math.max(0, Math.min(1, runtime.bounciness))
  // Interpolate damping from calm (high, no overshoot) → playful (low, overshoot).
  const damping = calmDamping + (dampingAtPlayful - calmDamping) * b
  return { type: 'spring', stiffness, damping, mass: 1 }
}

/** The named physics presets — the ONE set an author picks from (plan FLUID-MOTION §C1).
 *
 *  All four are `bouncy()` springs, so all four track `--bounciness` and all four go
 *  `instant` under reduced motion. Named by FEEL, not by use case, so the choice is
 *  "how should this move?" rather than "which component am I in?".
 *
 *  Constants are the plan's, verbatim, and they are the taste surface: retuning the
 *  app's whole personality is four numbers here, not a sweep of call sites. */
export const physics = {
  /** quick, minimal overshoot — controls, chevrons, press/hover feedback */
  get snappy(): Transition { return bouncy(520, 30, 40) },
  /** default UI — reach for this when nothing argues for another tier */
  get smooth(): Transition { return bouncy(320, 34, 38) },
  /** liquid, generous settle — large surfaces, layout shifts, morphs */
  get fluid(): Transition { return bouncy(180, 26, 34) },
  /** the most overshoot at bounciness=1 — the ~3-4 earned personality moments */
  get playful(): Transition { return bouncy(420, 14, 34) },
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

/** Menu / sheet entrance — spring scale+fade on the `playful` preset (one of the
 *  ~3-4 sanctioned bounce moments).
 *
 *  `animate` is a FUNCTION, not an object literal, and that is load-bearing. A
 *  module-level `{ transition: physics.playful }` reads the getter ONCE at import
 *  and freezes whatever bounciness (and whatever reduced-motion answer) happened to
 *  be true then — every overlay in the app would ignore the slider for the rest of
 *  the session. Framer resolves a variant function per animation, so the preset is
 *  read fresh each time it opens. */
export const overlayEnter: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 4 },
  animate: () => ({ opacity: 1, scale: 1, y: 0, transition: physics.playful }),
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

/** Stagger a container's children by a fixed step. Use on list/grid entrances so
 *  rows cascade instead of popping in together (§4 choreography). */
export function stagger(step = 0.04, delayChildren = 0): Transition {
  return { staggerChildren: step, delayChildren }
}

/** A list-item entrance variant pair — rise+fade on the `smooth` preset. Pair with
 *  a parent `variants={{ animate: { transition: stagger() } }}`.
 *
 *  A function for the same reason `overlayEnter.animate` is one: an object literal
 *  would freeze the preset at import. `smooth` is critically damped at every slider
 *  position, so a list still arrives without wobble — the discipline lives in the
 *  preset choice, not in a hardcoded tween. */
export const listItemEnter: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: () => ({ opacity: 1, y: 0, transition: physics.smooth }),
}

// ─────────────────────────────────────────────────────────────────────────
// Gesture physics (plan FLUID-MOTION §C1). Direct manipulation is not decoration:
// reduced motion zeroes the transitions a gesture RESOLVES with, and never the
// drag itself — taking the drag away would remove a function, not an effect.
// ─────────────────────────────────────────────────────────────────────────

/** The spring a dragged element settles with when the finger lets go — snapping
 *  back inside its constraints, or landing after a reorder.
 *
 *  Stiffer than `physics.snappy` on purpose: a return that loses a race with the
 *  hand that threw it reads as lag, not as softness. Same `bouncy()` family, so it
 *  still tracks the slider and still zeroes under reduced motion. */
export function dragSpring(): Transition {
  return bouncy(620, 24, 44)
}

/** How far a dragged element may stretch PAST its constraints (Framer's
 *  `dragElastic`), from `--drag-elastic`.
 *
 *  Deliberately NOT reduced-motion gated and NOT `expr()`-scaled: with elastic 0 and
 *  a zero-width constraint box the element cannot move at all, so a swipe-to-dismiss
 *  would stop being performable. The rubber band is how the gesture answers the
 *  finger; only its release transition is decoration. */
export function dragElastic(): number {
  return Math.max(0, Math.min(1, runtime.dragElastic))
}

/** Verdict + transition for a swipe-to-dismiss gesture, from Framer's
 *  `onDragEnd(_, info)`: pass `info.velocity.x` and `info.offset.x` (or the y pair).
 *
 *  Dismiss on either a fast FLICK (`--swipe-dismiss-velocity`, px/s) or a deliberate
 *  slow HAUL past `--swipe-dismiss-distance` (px) — velocity alone would make a
 *  careful drag all the way across do nothing, which reads as broken. Both thresholds
 *  are user-tunable tokens rather than magic numbers at the call site.
 *
 *  A dismissed element LEAVES on an accelerating curve (it must not overshoot back
 *  into the surface it is leaving); a kept one springs home on `dragSpring()`. */
export function swipeDismiss(velocity: number, offset = 0): { dismiss: boolean; transition: Transition } {
  const dismiss = Math.abs(velocity) >= runtime.swipeVelocity || Math.abs(offset) >= runtime.swipeDistance
  if (prefersReducedMotion()) return { dismiss, transition: instant }
  return {
    dismiss,
    transition: dismiss
      ? { duration: duration.medium, ease: ease.emphasizedAccel }
      : dragSpring(),
  }
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

/** Run a DOM update inside a View Transition when the platform supports it, else
 *  run it synchronously (progressive enhancement, §9).
 *
 *  **`update` is never gated on the transition — it runs exactly once on every
 *  path.** That is the whole contract, because callers pass their *state* change in
 *  here: a transition that swallowed or delayed it would turn decoration into a lost
 *  navigation. The three paths that would otherwise drop it:
 *   1. **No API** — jsdom, and any browser without View Transitions: run it directly.
 *      Note which layer actually carries survival here: the `catch` below would
 *      recover this case too (calling `undefined` throws a `TypeError`), so the
 *      `typeof` guard is a FAST PATH and a statement of intent, not the safety net.
 *      It is here so a third of browsers don't drive normal control flow through an
 *      exception on every navigation.
 *   2. **A throwing `startViewTransition`** — a detached document, an implementation
 *      that refuses: recovered in the `catch`, hence the run-once latch (the throw
 *      may have come *after* the callback was invoked). An error thrown by `update`
 *      itself is re-raised, never recovered — see the comment on the `catch`.
 *   3. **An animation that never settles** — the returned transition object is
 *      deliberately dropped. Awaiting `.finished`/`.ready` before the update is
 *      exactly the bug this function exists to make unwritable, so this function
 *      is not `async` and returns `void`.
 *  The one mode not defended is an implementation that never invokes the callback at
 *  all; the spec requires it to run even when the transition is *skipped* (hidden
 *  document, a second transition starting), so a timer to second-guess that would be
 *  a shim guarding nothing.
 *
 *  Reduced motion is gated HERE and nowhere else, read at CALL time via
 *  `prefersReducedMotion()` — the reduced-motion answer is "no transition at all",
 *  the instant swap. There is deliberately no `reduce` parameter: a caller could
 *  forget it, or pass `false` and quietly overrule the user's OS setting. */
export function viewTransition(update: () => void): void {
  // `lib.dom` types this as always present, which is a lie on any browser without View
  // Transitions — hence the `| undefined` and the runtime check rather than a bare call.
  const start = document.startViewTransition as Document['startViewTransition'] | undefined
  if (prefersReducedMotion() || typeof start !== 'function') { update(); return }
  let ran = false
  let updateFailed = false
  const once = () => {
    if (ran) return
    ran = true
    try { update() } catch (err) { updateFailed = true; throw err }
  }
  try {
    start.call(document, once)
  } catch (err) {
    // Two very different throws surface at the same place, and conflating them is a
    // real defect: `update` failing (a render error travelling out through the
    // callback) must reach the caller, or a broken page goes silently blank. Only a
    // refusal to START the transition is recovered here — and the latch makes that
    // recovery a no-op when the implementation had already invoked the callback.
    if (updateFailed) throw err
    once()
  }
}
