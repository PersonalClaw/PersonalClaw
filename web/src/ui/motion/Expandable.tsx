import { type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { spring } from '../../design/motion'

/** In-place expand/collapse via Motion `layout` — the SAME element grows to reveal
 *  its detail instead of mounting a detached surface (§Goal 4: "morph, don't
 *  mount"). The header is always visible; the body animates open/closed with a
 *  height+opacity morph. Reduced-motion is honored globally via the root
 *  MotionConfig (the layout animation degrades to an instant swap).
 *
 *  Use where the expanded thing is conceptually the same object (settings
 *  sections, a row that reveals detail). For a truly detached/transient surface
 *  (menu/tooltip/toast), mount under AnimatePresence instead — do NOT force this. */
export function Expandable({
  open, header, children, className,
}: {
  open: boolean
  header: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <motion.div layout transition={spring.spatialDefault} className={className} style={{ overflow: 'hidden' }}>
      {header}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={spring.spatialDefault}
            style={{ overflow: 'hidden' }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
