import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { bounce, expr } from '../../design/motion'

/** "Bud off" spawn — a spawned panel/form emerges FROM its trigger like a liquid
 *  droplet splitting off, instead of appearing from nowhere (§Goal 3 liquid,
 *  §Goal 4 morph-don't-mount). The panel grows from the edge SHARED with the
 *  trigger (`from`: the trigger's side) via a `scaleY` from that origin while its
 *  corner radius relaxes from a fat pill → the settled panel radius — so it reads
 *  as a blob stretching off the button and firming into a surface. Content fades
 *  in over the brief squish, so text never distorts. Wrap in <AnimatePresence> so
 *  it buds back in on close. Overshoot scales via the bounciness+expressiveness
 *  knobs; reduced-motion degrades the transform to a fade (global MotionConfig).
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
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scaleY: 0.12, borderRadius: 'var(--radius-pill)' }}
      animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
      exit={{ opacity: 0, scaleY: 0.12, borderRadius: 'var(--radius-pill)' }}
      // A touch more overshoot when expressiveness is bold; settles calmly when refined.
      transition={{ ...bounce.settle, stiffness: 260 - expr(70, 0.4) }}
      style={{ originY: from === 'bottom' ? 1 : 0, overflow: 'hidden' }}
      className={className}
    >
      {children}
    </motion.div>
  )
}
