import { motion } from 'framer-motion'
import { ShieldCheck } from 'lucide-react'
import { spring } from '../../design/motion'
import { QuietButton } from '../../ui/QuietButton'

/** "Check this work" chip (HARNESS-CRAFT §3.3) — offered under the last assistant turn
 *  after a completion turn that did real multi-step work (3+ tool calls + completion
 *  language, decided server-side and pushed over `chat_check_work_offer`).
 *
 *  It only OFFERS. Clicking sends "check your work" as an ordinary message, which
 *  triggers the bundled `check-work` skill — so the cost and latency of verification are
 *  always spent on a user's click, never automatically. Visual sibling of FollowupChips,
 *  deliberately distinct (icon + accent border) because it means "verify", not "next". */
export function CheckWorkChip({ label, onRun }: { label: string; onRun: () => void }) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialFast}
      className="mt-m inline-flex items-center overflow-hidden rounded-pill border border-primary/30 bg-surface-container hover:border-primary/60">
      <QuietButton onClick={onRun} title="Re-derive and run checks against what this turn claimed"
        className="rounded-none hover:bg-surface-high">
        <ShieldCheck size={13} aria-hidden className="mr-1.5 shrink-0 text-primary" />
        {label}
      </QuietButton>
    </motion.span>
  )
}
