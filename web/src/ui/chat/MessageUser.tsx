import { useId, useState } from 'react'
import { fvs, withWeight } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { Sparkles, ChevronRight, ChevronDown } from 'lucide-react'
import { messageEnter, spring } from '../../design/motion'
import { MessageBody, type TurnPaste } from '../../pages/chat/PasteChip'

/** Entrance for the JUST-SENT bubble: it travels UP from near the composer into
 *  its transcript slot (rise + slight grow), in concert with the glow that splits
 *  off the composer. A spring gives it weight + a soft settle. Older user bubbles
 *  use the quiet default `messageEnter`. */
/*  A FUNCTION, not an object literal, because `spring.spatialSlow` is a getter that reads
 *  `prefers-reduced-motion` at access time (FM-7). Held as a module-scope literal it would
 *  resolve the gate ONCE at import and freeze that answer for the session, so a user who
 *  turns reduced motion on mid-session would keep the 120px spring travel — the same trap
 *  `overlayEnter.exit` carried in `design/motion.ts` until this atom made it a function. */
const travelEnter = () => ({
  initial: { opacity: 0, y: 120, scale: 0.94 },
  animate: { opacity: 1, y: 0, scale: 1, transition: spring.spatialSlow },
})

/** A user message past either bound opens COLLAPSED behind "Show full message".
 *
 *  Measured: a 60,014-character paste sent as typed text (the composer only chips a paste it
 *  sees as a paste event) rendered as a ~21,000px bubble, and the turn's error landed below
 *  it — out of sight, reachable only through "Jump to latest". The message is the user's own
 *  words, so it is never truncated, only folded: the whole text stays in the DOM (copy, select
 *  and screen readers get all of it) and one click shows it. The bounds sit well above
 *  anything typed as a normal message — the paste chip already folds a real paste at 4
 *  lines / 320 characters, so this catches only what reached the transcript unchipped. */
export const LONG_MESSAGE_LINES = 16
export const LONG_MESSAGE_CHARS = 1500

export function isLongUserMessage(text: string): boolean {
  return text.length > LONG_MESSAGE_CHARS || text.split('\n').length > LONG_MESSAGE_LINES
}

/** User turn — right-aligned contained bubble (40px radius, surface-container,
 *  max-width 452px). The ONLY bubbled side in NE chat. Content renders as
 *  markdown (same renderer as assistant turns), with first/last-child margins
 *  collapsed so a one-line message sits snug. `fromComposer` makes the newest
 *  sent bubble travel up from the composer (Stage 3 glow-travel). */
export function MessageUser({ children, fromComposer = false, onFileClick, pastes, optimized, onExpand }: {
  children: string; fromComposer?: boolean; onFileClick?: (path: string) => void; pastes?: TurnPaste[]; optimized?: string
  /** Called when the reader unfolds a long message — a decision to read it, which the host
   *  uses to stop following a turn that is still arriving below it. */
  onExpand?: () => void
}) {
  const long = isLongUserMessage(children)
  const [expanded, setExpanded] = useState(false)
  const bodyId = useId()
  const folded = long && !expanded
  const lines = children.split('\n').length
  return (
    <motion.div variants={fromComposer ? travelEnter() : messageEnter} initial="initial" animate="animate" className="flex justify-end">
      <div
        className="bg-surface-container text-on-surface [&_>div>*:first-child]:mt-0 [&_>div>*:last-child]:mb-0"
        style={withWeight({
          borderRadius: 'calc(40px * var(--radius-scale))',
          padding: '20px 28px',
          // fits content, growing up to 80% of the column before wrapping (was a
          // hard 452px cap that truncated wide content like code/long lines).
          maxWidth: '80%',
          fontSize: '1.0625rem',
          lineHeight: 1.5,
        }, 400)}
      >
        <div id={bodyId} data-user-message-folded={folded || undefined}
          className={folded ? 'max-h-[16rem] overflow-hidden' : undefined}
          style={folded ? { maskImage: 'linear-gradient(to bottom, black 72%, transparent)', WebkitMaskImage: 'linear-gradient(to bottom, black 72%, transparent)' } : undefined}>
          <MessageBody text={children} pastes={pastes} onFileClick={onFileClick} />
        </div>
        {long && (
          <button type="button" onClick={() => { if (!expanded) onExpand?.(); setExpanded(!expanded) }} aria-expanded={expanded} aria-controls={bodyId}
            data-type="caption"
            className="mt-2 flex items-center gap-1 text-on-surface-low hover:text-on-surface-var transition-colors"
            style={fvs(500)}>
            <ChevronDown size={13} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            {expanded
              ? 'Show less'
              : `Show full message · ${lines > 1 ? `${lines.toLocaleString()} lines` : `${children.length.toLocaleString()} characters`}`}
          </button>
        )}
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
        data-type="caption"
        className="flex items-center gap-1 text-on-surface-low hover:text-on-surface-var transition-colors"
        style={fvs(500)}>
        <ChevronRight size={13} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        <Sparkles size={12} className="shrink-0" />
        {open ? 'Optimized prompt sent to the model' : 'Sent an optimized version'}
      </button>
      {open && (
        <div data-type="body-m" className="mt-2 rounded-lg bg-surface/60 px-3 py-2 [&_>*:first-child]:mt-0 [&_>*:last-child]:mb-0">
          <MessageBody text={optimized} onFileClick={onFileClick} />
        </div>
      )}
    </div>
  )
}
