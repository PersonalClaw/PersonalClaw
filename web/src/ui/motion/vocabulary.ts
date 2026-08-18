// The morph family's ONE timing vocabulary (plan FLUID-MOTION, atom FM-4).
//
// Four primitives — `Morph` (a card flies to the page it opens), `LiquidShape` (a
// silhouette changes composure), `Bud` (a panel separates from its trigger) and
// `Disintegrate` (a row dissolves) — are all the same gesture at different scales:
// something the user already sees BECOMES something else, rather than one thing
// crossfading out under another. They shipped one atom at a time (FM-1…FM-3), and
// each arrived with its own numbers: two of them hand-rolled the same
// `expr(70, 0.4)` stiffness bonus with OPPOSITE signs, and the fourth rode a raw
// Material curve the design system explicitly disowns. That is four dialects of one
// idea, which is exactly how a motion language stops reading as a language.
//
// So the timing lives HERE, once, and the primitives contain no timing arithmetic at
// all. Two consequences worth stating, because they are the whole point:
//
//   • **One sign convention.** BOLD always means a TAUTER spring — which under this
//     app's fixed-damping `bouncy()` family means both quicker AND more overshoot.
//     Before this atom, dialing expressiveness up made a `Morph` tauter and a `Bud`
//     *slacker*; the two primitives disagreed about what the user's knob means.
//   • **Travel picks the base, the knob picks the bonus.** Bases are ordered by how
//     far the thing travels (a cross-page flight is softest, a bud off a button is
//     tautest); the bonus the knob adds is ONE number for the whole family.
//
// This module composes `design/motion` — it does not restate it. `physics.fluid` is
// still the family's spring, `ease`/`duration` still own the curves and lengths, and
// the reduced-motion collapse still happens in exactly one place (`bouncy()`). There
// is deliberately no third preset family here; see docs/design/motion.md §5b.

import type { Transition } from 'framer-motion'

import { duration, ease, expr, physics, spring } from '../../design/motion'

/** The family's tunables, in ONE named place. Nothing here is settled — this is the
 *  taste surface the plan's owner task 1 budgets ~30 min a session dialing, and
 *  `LiquidShape`'s `TUNING` / `Disintegrate`'s tier list are the amplitude halves of
 *  the same idea. Retuning the whole family's feel is these five numbers. */
export const MORPH_FAMILY = {
  /** `Morph` — a library card flying to the page it opens. The LONGEST travel in the
   *  family, so the softest base: a full-width flight on a taut spring reads as a
   *  snap, not as one object becoming another. Base+bonus lands near
   *  `ComposerStage`'s hero→dock flight (the app's pre-existing signature morph) on
   *  purpose — the two should read as one gesture. */
  flight: 190,
  /** `LiquidShape` — a silhouette changing composure IN PLACE. No travel, but the
   *  whole outline is in motion, so it sits between the two: quicker than a flight,
   *  slower than something popping off a button. */
  state: 200,
  /** `Bud` — a panel separating from the control that made it. The SHORTEST travel
   *  (one edge's worth), so the tautest base: a bud that takes as long as a page
   *  flight reads as lag on a button press. */
  spawn: 240,
  /** What BOLD adds on top of a base stiffness. ONE number for the family — the two
   *  primitives that hand-rolled it before this atom both chose 70, which is the
   *  clearest possible evidence it was always one constant wearing two hats. */
  stiffnessBonus: 70,
  /** The fraction of `stiffnessBonus` kept at expressiveness 0. Refined is a CALMER
   *  spring, not a dead one — and it is deliberately not 0, because zeroing it here
   *  would make the aesthetic dial duplicate the accessibility switch. */
  floor: 0.4,
  /** The share of a family TWEEN's length the refined tier runs at. The refined tier
   *  drops the heavy fragmentation entirely (`exprHeavy`'s contract), and a dissolve
   *  with nothing left to fragment should not linger for the same beat. */
  refinedScale: 0.7,
}

/** The spring EVERY morph-family primitive flies on, given its base stiffness.
 *
 *  `physics.fluid` is the family's preset (liquid, generous settle — the tier
 *  `motion.ts` nominates for "large surfaces, layout shifts, morphs"), so the family
 *  inherits its bounciness scaling and its reduced-motion collapse for free.
 *
 *  The `type` check is not defensive noise — it closes the spread hazard `motion.ts`
 *  documents on `instant`: `{ ...physics.fluid, stiffness: N }` would let Framer
 *  infer a spring again from the leftover `stiffness`, so under reduced motion the
 *  collapsed preset is returned UNTOUCHED rather than spread. Every family member
 *  routes through here, so no member can be added that escapes either the collapse
 *  or the sign convention. */
export function familySpring(base: number): Transition {
  const preset = physics.fluid
  if (preset.type !== 'spring') return preset
  return { ...preset, stiffness: base + expr(MORPH_FAMILY.stiffnessBonus, MORPH_FAMILY.floor) }
}

/** The family's DISSOLVE tween — the one non-spring transition in the vocabulary,
 *  for the member that must not overshoot at all (`Disintegrate`: a spring settling
 *  on `filter` undershoots below its target and `blur()` rejects negatives).
 *
 *  `ease.emphasized` rather than the Material standard curve this used to hardcode.
 *  The house curve commits FAST and settles long, which is the right shape twice
 *  over: a delete must feel answered the instant it is clicked, and the gap the row
 *  leaves must close gently. A slow-start curve got both backwards. */
export function familyTween(heavy: boolean): Transition {
  return {
    duration: duration.medium * (heavy ? 1 : MORPH_FAMILY.refinedScale),
    ease: ease.emphasized,
  }
}

/** The family's OPACITY/settle transition — a tinted wash fading in, a dissolve
 *  reversing. `spring.effects` is the app's critically-damped fade, so a family
 *  member never invents a duration for "something is fading".
 *
 *  Deliberately NOT reduced-motion gated, for the reason `motion.ts` gives the
 *  `spring` tier: opacity is the one thing `MotionConfig reducedMotion="user"`
 *  keeps, and every family member already self-gates its own SHAPE — under reduced
 *  motion `Disintegrate` renders no motion tree at all, so there is no fade left to
 *  gate. A second gate here would guard nothing and imply the first one is optional. */
export function familyFade(): Transition {
  return spring.effects
}
