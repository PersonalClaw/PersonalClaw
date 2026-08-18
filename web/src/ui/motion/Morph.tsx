import { type ReactNode } from 'react'
import { motion, useReducedMotion, type Transition } from 'framer-motion'
import { physics, expr } from '../../design/motion'

/** The morph's constants, in ONE named place (plan FLUID-MOTION, owner task 1: the
 *  feel is taste-driven, ~30 min a session dialing numbers — so the numbers must be
 *  findable, not spread across call sites). Nothing here is settled; it is the dial.
 *
 *  `stiffness` is the floor a refined morph flies at; `stiffnessBonus` is what the
 *  expressiveness knob adds on top, and `floor` is the fraction of that bonus kept at
 *  expressiveness 0. Base+bonus lands near `ComposerStage`'s hero→dock flight (the
 *  app's existing signature morph) on purpose — the two should read as one gesture. */
export const MORPH = { stiffness: 190, stiffnessBonus: 70, floor: 0.4 }

/** The transition every `<Morph>` flies on. Exported because a caller occasionally has
 *  to hand the same curve to a sibling animation so the two don't drift apart.
 *
 *  The `type` check is not defensive noise — it closes the spread hazard `motion.ts`
 *  documents on `instant`: `{ ...physics.fluid, stiffness: N }` would let Framer infer
 *  a spring again from the leftover `stiffness`, so under reduced motion the collapsed
 *  preset is returned UNTOUCHED rather than spread. */
export function morphTransition(): Transition {
  const base = physics.fluid
  if (base.type !== 'spring') return base
  return { ...base, stiffness: MORPH.stiffness + expr(MORPH.stiffnessBonus, MORPH.floor) }
}

/** Shared-element morph (§Goal 4 "morph, don't mount"): two boxes in DIFFERENT parts of
 *  the tree that are the SAME object to the user — a library card and the page it opens —
 *  declare one `id`, and Framer flies the second out of the first's position and size
 *  instead of cross-cutting one away and popping the other in.
 *
 *  Give BOTH ends the same `id` and mount only one at a time. The morph is what Framer
 *  does when an element with a `layoutId` leaves in the same commit another with that id
 *  arrives — so the two ends must swap in ONE render (a state/sub-route flip both
 *  directions), not across an await. A target that mounts a tick later, after its fetch
 *  resolves, gets no morph and no error: it just appears. `ComposerStage` (hero → dock)
 *  and `FilterMenu`'s indicator are the same mechanism hand-rolled; this is it named.
 *
 *  **`layoutId` only — deliberately no `layout`.** `layout` would additionally animate
 *  each end's own size changes, which on a list means every filter, scroll and hover
 *  re-measures every card for an animation nobody asked for. Without it Framer measures
 *  only when a shared transition actually starts, which is what keeps a big grid off the
 *  layout path. Reach for `Expandable` when the thing genuinely grows in place.
 *
 *  **REDUCED MOTION drops the shared element entirely** — a plain `<div>`, no `layoutId`,
 *  so the swap is instant rather than quick, and the projection machinery never runs at
 *  all. Self-gated in JS because the global CSS rule only kills CSS transitions
 *  (`Disintegrate`'s note); a `duration: 0` transition would still project a frame. */
export function Morph({ id, className, style, children }: {
  /** The shared identity — the SAME string on both ends. Scope it to the entity
   *  (`artifact-<slug>`), never to the surface, or two lists morph into each other. */
  id: string
  className?: string
  style?: React.CSSProperties
  children: ReactNode
}) {
  const reduce = useReducedMotion()
  // Not `layoutId={reduce ? undefined : id}` on one motion.div: a motion component still
  // installs a projection node and its own style pipeline. The reduced-motion end is a
  // plain div so there is nothing left to animate, and `data-morph` makes which branch
  // ran assertable from the DOM.
  if (reduce) return <div data-morph="none" className={className} style={style}>{children}</div>
  return (
    <motion.div data-morph="shared" layoutId={id} transition={morphTransition()} className={className} style={style}>
      {children}
    </motion.div>
  )
}
