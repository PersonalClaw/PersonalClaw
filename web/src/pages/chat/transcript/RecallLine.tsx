import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, ChevronRight } from 'lucide-react'
import { spring } from '../../../design/motion'
import { Button } from '../../../ui/Button'
import { TextLink } from '../../../ui/TextLink'
import type { ActivitySegment } from '../chatTypes'

/** Where "Review what's recalled" lands: Memory's recall view, as the composer's context
 *  disclosure opens it. */
export const REVIEW_RECALL_HREF = '#/memory?tab=recall'

/** What a turn recalled, said as a line of the turn where it was recalled (#5876): above the text it
 *  fed, or between the steps it came between, live and after a reload (`hydrateTurns` puts the
 *  reply's `context_fed` at the head of the turn, where the gateway says it). It is no step of the
 *  turn's work and no row of its footer.
 *
 *  Bare, as the turn's other activity lines, with its details one tap away: how much was recalled,
 *  what it is, and where to review it. A turn that read none of your memory
 *  (`context_without_memory`, `memory_reads.fed`) says "No memory recalled" on its line, gives the
 *  gateway's own reason in its details, and offers no review: there is nothing of yours to review. */
export function RecallLine({ seg }: { seg: ActivitySegment }) {
  const [open, setOpen] = useState(false)
  const noMemory = seg.activityKind === 'context_without_memory'
  const chars = seg.text.match(/([\d,]+)\s*chars/)?.[1] ?? ''
  const said = noMemory ? 'No memory recalled' : `Recalled context${chars ? ` · ${chars} chars` : ''}`
  return (
    <div data-turn-recall="" className="my-1">
      {/* Pulled left by its own padding, so its words line up with the turn's other lines. */}
      <Button variant="ghost" size="xs" className="-ml-m" onClick={() => setOpen((v) => !v)} ariaExpanded={open}
        title={open ? 'Hide what was recalled' : 'What was recalled for this turn'}>
        {/* The line's quiet ink is its own: `ghost` sets the button's. */}
        <span className="flex items-center gap-1.5 text-on-surface-low">
          <Brain size={12} className="shrink-0 opacity-70" aria-hidden />
          <span>{said}</span>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60" aria-hidden>
            <ChevronRight size={11} />
          </motion.span>
        </span>
      </Button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div data-type="caption" className="mt-1 ml-1.5 border-l border-outline-variant/40 pl-3 text-on-surface-low">
              {noMemory
                ? `${seg.text}.`
                : <>Recalled from your saved memories, learned lessons, earlier conversation, and episodic history, and put before your message.{' '}
                  <TextLink href={REVIEW_RECALL_HREF}>Review what’s recalled</TextLink></>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
