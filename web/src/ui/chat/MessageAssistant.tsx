import { motion } from 'framer-motion'
import { fvs } from '../../design/fontWeight'
import { messageEnter } from '../../design/motion'

/** Assistant turn — BUBBLE-LESS: plain text directly on the canvas, full width,
 *  with an action bar below (passed in by the page, which owns the handlers).
 *  The signature NE chat asymmetry. */
export function MessageAssistant({ children, actions }: { children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="group/msg w-full">
      <div
        className="max-w-none text-[1.0625rem] leading-[1.6] text-on-surface"
        style={fvs(400)}
      >
        {children}
      </div>
      {actions}
    </motion.div>
  )
}
