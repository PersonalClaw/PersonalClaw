import { type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { MORPH_FAMILY, familySpring } from './vocabulary'

/** "Bud off" spawn — a spawned panel/form emerges FROM its trigger like a liquid
 *  droplet splitting off, instead of appearing from nowhere (§Goal 3 liquid,
 *  §Goal 4 morph-don't-mount). The panel grows from the edge SHARED with the
 *  trigger (`from`: the trigger's side) via a `scaleY` from that origin while its
 *  corner radius relaxes from a fat pill → the settled panel radius — so it reads
 *  as a blob stretching off the button and firming into a surface. Content fades
 *  in over the brief squish, so text never distorts. Wrap in <AnimatePresence> so
 *  it buds back in on close.
 *
 *  Tiers, per the family's shared vocabulary (`vocabulary.ts`, atom FM-4):
 *
 *   • BOLD: the tautest spring in the family — `MORPH_FAMILY.spawn` plus the whole
 *     expressiveness bonus. The bud has the SHORTEST travel of the four primitives
 *     (one edge's worth), so it is the tautest base, and bold makes it tauter still:
 *     quicker off the button and a touch more overshoot as it firms up.
 *   • REFINED: the same squish on a calmer spring — the bonus keeps
 *     `MORPH_FAMILY.floor` of itself. No heavy tier: the squish IS the gesture, so
 *     there is nothing for `exprHeavy()` to drop.
 *   • REDUCED-MOTION: instant — a plain `<div>`, no transform, no `layout`, no
 *     projection node. Self-gated in JS for the reason `Disintegrate` records: the
 *     global CSS rule only kills CSS transitions, and while the root
 *     `MotionConfig reducedMotion="user"` does neutralize framer TRANSFORMS, it
 *     leaves `borderRadius` animating and still installs the projection machinery
 *     `layout` asks for. Delegating to it also made this the ONE family member whose
 *     off-switch you could not assert from the DOM — hence `data-bud`.
 *
 *  Use for "Add X" buttons that reveal an inline form/picker, disclosure buds,
 *  and anywhere a new surface should visibly separate from the control that made
 *  it. The trigger itself stays mounted (the caller keeps rendering it). */
export function Bud({ from = 'bottom', className, children }: {
  /** Which edge is shared with the trigger — the panel grows out of it. A panel
   *  ABOVE a button uses 'bottom' (grows up from the button); one BELOW uses 'top'. */
  from?: 'top' | 'bottom'
  className?: string
  children: ReactNode
}) {
  const reduce = useReducedMotion()
  // Not `layout={!reduce}` on one motion.div: a motion component still installs a
  // projection node and its own style pipeline, and `borderRadius` is not a transform
  // so `reducedMotion="user"` would keep animating it. The reduced-motion end is a
  // plain div so there is nothing left to animate, and `data-bud` makes which branch
  // ran assertable from the DOM — the same contract as `Morph`'s `data-morph` and
  // `LiquidShape`'s `data-liquid-shape`.
  if (reduce) return <div data-bud="instant" className={className}>{children}</div>
  return (
    <motion.div
      data-bud="grown"
      layout
      initial={{ opacity: 0, scaleY: 0.12, borderRadius: 'var(--radius-pill)' }}
      animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
      exit={{ opacity: 0, scaleY: 0.12, borderRadius: 'var(--radius-pill)' }}
      transition={familySpring(MORPH_FAMILY.spawn)}
      style={{ originY: from === 'bottom' ? 1 : 0, overflow: 'hidden' }}
      className={className}
    >
      {children}
    </motion.div>
  )
}
