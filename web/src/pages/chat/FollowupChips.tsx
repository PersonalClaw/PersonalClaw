import { motion } from 'framer-motion'
import { CornerDownLeft } from 'lucide-react'
import { spring } from '../../design/motion'
import { QuietButton } from '../../ui/QuietButton'
import { IconButton } from '../../ui/IconButton'

/** What the host's always-mounted live region says when chips arrive (CC-6).
 *
 *  The chips land 1-3s AFTER the reply finishes, from a WebSocket event — a purely
 *  visual arrival. `role="group" aria-label` names them once a user reaches them, but
 *  nothing told a screen-reader user they had appeared at all, so the only way to find
 *  them was to re-walk the transcript on the off-chance. This is the sentence that gets
 *  announced; the region itself lives in the host (ChatPage) and is mounted from first
 *  render, because a live region that appears together with its content is not
 *  reliably announced.
 *
 *  Empty string when there are none — including on dismissal, so the region CLEARS
 *  rather than leaving a stale claim that suggestions are still there. */
export function followupAnnouncement(count: number): string {
  if (count <= 0) return ''
  return `${count} follow-up suggestion${count === 1 ? '' : 's'} available`
}

/** Follow-up chips (CHAT-CRAFT S3) — 2-3 suggested next messages rendered under the
 *  last assistant turn's actions after a reply completes. Click fills the composer;
 *  the small send glyph (or double-click) sends immediately. The host dismisses them
 *  on any user activity (typing 3+ chars, sending, switching session, a new stream),
 *  so they never block or shift the composer. Visual sibling of the hero
 *  SuggestionChips; each chip eases in on a small per-item delay (motion tiers).
 *  Built from primitives: a QuietButton label half + an IconButton send half. */
export function FollowupChips({ items, onPick, onSend }: {
  items: string[]
  onPick: (text: string) => void
  onSend: (text: string) => void
}) {
  if (!items.length) return null
  // role="group": a bare <div> cannot take aria-label (prohibited — the name is
  // discarded), so the chips announced as loose buttons with no indication of what they
  // were. A named group says "Suggested follow-ups" before its contents.
  return (
    <div role="group" className="mt-m flex flex-wrap items-center gap-1.5" aria-label="Suggested follow-ups">
      {items.map((s, i) => (
        <motion.span key={`${i}-${s}`}
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialFast, delay: 0.04 * i }}
          className="inline-flex items-center overflow-hidden rounded-pill border border-outline-variant/50 bg-surface-container hover:border-primary/40">
          <QuietButton onClick={() => onPick(s)} onDoubleClick={() => onSend(s)}
            title="Click to edit · double-click to send" className="max-w-[22rem] truncate rounded-none hover:bg-surface-high">
            {s}
          </QuietButton>
          <IconButton icon={CornerDownLeft} label={`Send: ${s}`} onClick={() => onSend(s)} size={28} iconSize={13}
            className="shrink-0 border-l border-outline-variant/40 hover:text-primary" />
        </motion.span>
      ))}
    </div>
  )
}
