import { useState } from 'react'
import { motion } from 'framer-motion'
import { Sparkles, ChevronRight } from 'lucide-react'
import { messageEnter, spring } from '../../design/motion'
import { MessageBody, type TurnPaste } from '../../pages/chat/PasteChip'

/** Entrance for the JUST-SENT bubble: it travels UP from near the composer into
 *  its transcript slot (rise + slight grow), in concert with the glow that splits
 *  off the composer. A spring gives it weight + a soft settle. Older user bubbles
 *  use the quiet default `messageEnter`. */
const travelEnter = {
  initial: { opacity: 0, y: 120, scale: 0.94 },
  animate: { opacity: 1, y: 0, scale: 1, transition: spring.spatialSlow },
}

/** User turn — right-aligned contained bubble (40px radius, surface-container,
 *  max-width 452px). The ONLY bubbled side in NE chat. Content renders as
 *  markdown (same renderer as assistant turns), with first/last-child margins
 *  collapsed so a one-line message sits snug. `fromComposer` makes the newest
 *  sent bubble travel up from the composer (Stage 3 glow-travel). */
export function MessageUser({ children, fromComposer = false, onFileClick, pastes, optimized }: { children: string; fromComposer?: boolean; onFileClick?: (path: string) => void; pastes?: TurnPaste[]; optimized?: string }) {
  return (
    <motion.div variants={fromComposer ? travelEnter : messageEnter} initial="initial" animate="animate" className="flex justify-end">
      <div
        className="bg-surface-container text-on-surface [&_>div>*:first-child]:mt-0 [&_>div>*:last-child]:mb-0"
        style={{
          borderRadius: 'calc(40px * var(--radius-scale))',
          padding: '20px 28px',
          // fits content, growing up to 80% of the column before wrapping (was a
          // hard 452px cap that truncated wide content like code/long lines).
          maxWidth: '80%',
          fontSize: '16px',
          lineHeight: 1.5,
          fontVariationSettings: '"wght" 400',
        }}
      >
        <MessageBody text={children} pastes={pastes} onFileClick={onFileClick} />
        {optimized && <OptimizedDisclosure optimized={optimized} onFileClick={onFileClick} />}
      </div>
    </motion.div>
  )
}

/** Collapsed "optimized" section shown under a user bubble whose prompt was
 *  optimized before sending: the bubble shows the ORIGINAL text; this reveals the
 *  optimized version the model actually received. Closed by default. */
function OptimizedDisclosure({ optimized, onFileClick }: { optimized: string; onFileClick?: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2.5 border-t border-outline-variant/40 pt-2">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex items-center gap-1 text-[0.75rem] text-on-surface-low hover:text-on-surface-var transition-colors"
        style={{ fontVariationSettings: '"wght" 500' }}>
        <ChevronRight size={13} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        <Sparkles size={12} className="shrink-0" />
        {open ? 'Optimized prompt sent to the model' : 'Sent an optimized version'}
      </button>
      {open && (
        <div className="mt-2 rounded-lg bg-surface/60 px-3 py-2 text-[0.9375rem] [&_>*:first-child]:mt-0 [&_>*:last-child]:mb-0">
          <MessageBody text={optimized} onFileClick={onFileClick} />
        </div>
      )}
    </div>
  )
}
