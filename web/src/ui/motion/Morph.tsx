import { type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { MORPH_FAMILY, familySpring } from './vocabulary'

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
 *  Tiers, per the family's shared vocabulary (`vocabulary.ts`, atom FM-4):
 *
 *   • BOLD: the tautest flight the family allows — `MORPH_FAMILY.flight` plus the whole
 *     expressiveness bonus, so it is both quicker and overshoots more.
 *   • REFINED: the same flight on a calmer spring — the bonus keeps `MORPH_FAMILY.floor`
 *     of itself rather than zeroing, because refined is composed, not dead. There is no
 *     heavy tier here: a morph is the gesture, not an effect layered on one, so there is
 *     nothing for `exprHeavy()` to drop.
 *   • REDUCED-MOTION: **the shared element is dropped entirely** — a plain `<div>`, no
 *     `layoutId`, so the swap is instant rather than quick and the projection machinery
 *     never runs at all. Self-gated in JS because the global CSS rule only kills CSS
 *     transitions (`Disintegrate`'s note); a `duration: 0` transition would still
 *     project a frame. */
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
    <motion.div data-morph="shared" layoutId={id} transition={familySpring(MORPH_FAMILY.flight)}
      className={className} style={style}>
      {children}
    </motion.div>
  )
}
