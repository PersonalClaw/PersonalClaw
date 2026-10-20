import { useId, useState, type ReactNode } from 'react'
import { fvs, withWeight } from '../../design/fontWeight'
import { Sparkles, ChevronRight, ChevronDown, FileText, CornerDownLeft } from 'lucide-react'
import { MessageBody, type TurnPaste } from '../../pages/chat/PasteChip'
import type { RanPrompt } from '../../pages/chat/chatTypes'

/** A user message past either bound opens COLLAPSED behind "Show full message".
 *
 *  A 60,014-character paste sent as typed text (the composer only chips a paste it
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
 *  collapsed so a one-line message sits snug. The bubble arrives with no entrance: it
 *  used to travel 120px up from the composer on a spring (the newest) or rise and fade in
 *  (the rest), and a message the owner just sent is not news to her, so it is simply there.
 *  `ranPrompt` is the saved prompt the message ran: the bubble keeps what she typed, and the prompt's text sits folded under it, labelled as the
 *  prompt's. `steered` says she sent it into the answer while it was being written, which took it
 *  as it went: the answer above it goes on below it. */
export function MessageUser({ children, onFileClick, pastes, optimized, ranPrompt, steered = false, onExpand }: {
  children: string; onFileClick?: (path: string) => void; pastes?: TurnPaste[]; optimized?: string
  ranPrompt?: RanPrompt
  steered?: boolean
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
    <div className="flex justify-end">
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
        {steered && (
          <p data-type="caption" className="mt-s flex items-center gap-xs text-on-surface-low" style={fvs(500)}>
            <CornerDownLeft size={12} className="shrink-0" aria-hidden />
            Steered into the answer
          </p>
        )}
        {ranPrompt && (
          <SentTextDisclosure text={ranPrompt.text} onFileClick={onFileClick}
            icon={<FileText size={12} className="shrink-0" />}
            closedLabel={`Ran the prompt @${ranPrompt.name}`}
            openLabel={`Text of the prompt @${ranPrompt.name}, as the agent received it`} />
        )}
        {optimized && (
          <SentTextDisclosure text={optimized} onFileClick={onFileClick}
            icon={<Sparkles size={12} className="shrink-0" />}
            closedLabel="Sent an optimized version"
            openLabel="Optimized prompt sent to the model" />
        )}
      </div>
    </div>
  )
}

/** A section folded under a user bubble holding text the model received that she did not type:
 *  the optimized version of her prompt, or the text of a saved prompt she ran. The bubble keeps
 *  what she typed as primary, and this says what the extra text is. Closed by default. */
function SentTextDisclosure({ text, icon, closedLabel, openLabel, onFileClick }: {
  text: string; icon: ReactNode; closedLabel: string; openLabel: string; onFileClick?: (path: string) => void
}) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()
  return (
    <div className="mt-2.5 border-t border-outline-variant/40 pt-2">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={bodyId}
        data-type="caption"
        className="flex items-center gap-xs text-on-surface-low hover:text-on-surface-var transition-colors"
        style={fvs(500)}>
        <ChevronRight size={13} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        {icon}
        {open ? openLabel : closedLabel}
      </button>
      {open && (
        <div id={bodyId} data-type="body-m" className="mt-s rounded-lg bg-surface/60 px-m py-s [&_>*:first-child]:mt-0 [&_>*:last-child]:mb-0">
          <MessageBody text={text} onFileClick={onFileClick} />
        </div>
      )}
    </div>
  )
}
