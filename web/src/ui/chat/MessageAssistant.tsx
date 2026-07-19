import { motion } from 'framer-motion'
import { messageEnter } from '../../design/motion'

/** Assistant turn — BUBBLE-LESS: plain text directly on the canvas, full width,
 *  with an action bar below (passed in by the page, which owns the handlers).
 *  The signature NE chat asymmetry. */
export function MessageAssistant({ children, actions }: { children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="group/msg w-full">
      <div
        className="max-w-none text-[1rem] leading-[1.6] text-on-surface"
        style={{ fontVariationSettings: '"wght" 400' }}
      >
        {children}
      </div>
      {actions}
    </motion.div>
  )
}
