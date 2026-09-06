import { useState } from 'react'
import { Copy, Check, RotateCcw, GitBranch, Volume2, Square, Pencil, ChevronLeft, ChevronRight, Rewind } from 'lucide-react'
import { unavailableWhen } from '../../ui/unavailable'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'

/** The reveal wrapper the action BUTTONS live in.
 *
 *  This used to be the whole row. It was split so a timestamp can sit in the row and stay visible
 *  while the buttons keep their hover reveal: "Show timestamps" is a setting a reader switches ON to
 *  read times while scanning, and a time that only appears under the pointer does not do that.
 *  `focus-within` stays here rather than on the row, because it is the buttons that take focus. */
const REVEAL = 'flex items-center gap-0.5 translate-y-0.5 opacity-0 transition duration-150'
  + ' group-hover/msg:translate-y-0 group-hover/msg:opacity-100 focus-within:translate-y-0 focus-within:opacity-100'

/** A message's clock time, in the type role the design system reserves for it (`caption` — whose
 *  token comment names "timestamp micro-text" as its purpose).
 *
 *  A real `<time>`, so the instant is machine-readable, and `tabular-nums` so a column of stamps
 *  does not jitter as the digits change. Renders nothing at all when there is no readable stamp:
 *  an absent time is normal (a turn mid-stream has none yet) and must not print a placeholder. */
function Stamp({ ts }: { ts?: string }) {
  const shown = clockTime(ts)
  if (!shown) return null
  const iso = isoStamp(ts)
  // 🪤 NO horizontal padding. The stamp is plain text, not a padded icon button, so any inset here
  // pushes it off the message's own content edge — measured in a browser at 7px adrift from the
  // assistant bubble's left margin, which reads as sloppy rather than as spacing. The gap between the
  // stamp and the buttons comes from the ROW's `gap` instead, where it belongs.
  return (
    <time data-type="caption" className="select-none tabular-nums text-on-surface-low"
      {...(iso ? { dateTime: iso } : {})} title={fullStamp(ts)}>{shown}</time>
  )
}

/** Action bar below an ASSISTANT turn. Copy + Speak always; Regenerate only on
 *  the last turn (it replaces the latest reply); Branch from any turn (CC-7 —
 *  duplicates the conversation up to that point into a new session; branching
 *  from an ANSWER is the common case, "take this analysis two directions"). When
 *  a reply has been regenerated, a ‹ n/N › variant switcher lets the user page
 *  back to a prior answer. Real, wired actions — no decorative thumbs/more.
 *  Reveals on hover of the message (group/msg) and stays while focused.
 *  `speaking` is controlled by the host (real playback state): the Speak button
 *  becomes a Stop toggle while this turn's audio is playing. */
export function AssistantActions({ text, isLast, speaking, canFork = true, variantCount = 0, variantIdx = 0, ts, onCopy, onRegenerate, onFork, onSpeak, onSwitchVariant }: {
  text: string
  isLast: boolean
  /** this turn's source stamp, ALREADY gated on the reader's "Show timestamps" setting by the host —
   *  absent means "do not show one", so this component needs no knowledge of the preference. */
  ts?: string
  speaking?: boolean
  canFork?: boolean  // false on non-persistent (temporary/incognito) sessions — the backend refuses to fork those
  variantCount?: number  // number of regenerated answers on this turn (0/1 → no switcher)
  variantIdx?: number    // active variant (0-based)
  onCopy: () => void
  onRegenerate: () => void
  onFork: () => void
  onSpeak: () => void
  onSwitchVariant?: (index: number) => void
}) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}); onCopy() }
  const hasVariants = variantCount > 1 && !!onSwitchVariant
  return (
    <div className="mt-m flex items-center gap-1.5">
      <Stamp ts={ts} />
      <div className={REVEAL}>
        {hasVariants && <VariantSwitcher count={variantCount} idx={variantIdx} onSwitch={onSwitchVariant!} />}
        <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
        {isLast && <ActBtn icon={RotateCcw} label="Regenerate" onClick={onRegenerate} />}
        {canFork && <ActBtn icon={GitBranch} label="Branch from here" onClick={onFork} />}
        <ActBtn icon={speaking ? Square : Volume2} label={speaking ? 'Stop' : 'Speak'} onClick={onSpeak} active={speaking} />
      </div>
    </div>
  )
}

/** ‹ 2/3 › pager over an assistant turn's regenerated answer variants. Prev/next
 *  wrap-clamp at the ends (unavailable, not looping) and call onSwitch with the new
 *  index; the backend swaps the active answer and echoes it over the WS.
 *
 *  The end arrows stay REACHABLE (`aria-disabled`, not the native attribute) and name
 *  the limit. A native `disabled` arrow is removed from the tab order, so paging to the
 *  last answer with the keyboard destroys the user's own focus: the button they just
 *  pressed vanishes from the order and focus falls to <body>, leaving them to tab in
 *  from the top of the document to reach anything. */
function VariantSwitcher({ count, idx, onSwitch }: { count: number; idx: number; onSwitch: (index: number) => void }) {
  const atStart = idx <= 0
  const atEnd = idx >= count - 1
  // `disabled:` variants no longer fire once the native attribute is gone — the dim and the
  // suppressed hover have to be restated on `aria-disabled:` or the clamped arrow reads as live.
  const arrow = 'inline-flex h-8 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high hover:text-on-surface aria-disabled:opacity-40 aria-disabled:hover:bg-transparent aria-disabled:hover:text-on-surface-low aria-disabled:cursor-default'
  return (
    <div className="inline-flex items-center gap-0.5 rounded-md pr-1 text-on-surface-low" title={`Answer ${idx + 1} of ${count}`}>
      <button type="button" onClick={() => !atStart && onSwitch(idx - 1)}
        aria-label="Previous answer" className={arrow}
        {...unavailableWhen(atStart, 'Already at the first answer', { title: 'Previous answer' })}>
        <ChevronLeft size={14} />
      </button>
      <span data-type="caption" className="min-w-[2.1rem] select-none text-center tabular-nums" aria-live="polite">{idx + 1}/{count}</span>
      <button type="button" onClick={() => !atEnd && onSwitch(idx + 1)}
        aria-label="Next answer" className={arrow}
        {...unavailableWhen(atEnd, 'Already at the last answer', { title: 'Next answer' })}>
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

/** Action bar below a USER turn — Copy, Edit & resend, Rewind, Branch. Right-aligned
 *  to sit under the bubble. `canFork` hides Branch on a non-persistent session (the
 *  backend refuses to fork temporary/incognito). Rewind is offered only on
 *  NON-last user turns (`canRewind`): editing an earlier turn replays from there
 *  and keeps the discarded tail in history (fork-and-swap); the last turn uses
 *  plain Edit & resend (nothing to retain). */
export function UserActions({ text, canFork = true, canRewind = false, ts, onEdit, onRewind, onFork }: { text: string; canFork?: boolean; canRewind?: boolean; ts?: string; onEdit: () => void; onRewind?: () => void; onFork: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }
  return (
    // Right-aligned to match the user bubble, so the stamp trails the buttons here and leads them on
    // an assistant turn — both end up on the same edge as the message they belong to.
    <div className="mt-1.5 flex items-center justify-end gap-1.5">
      <div className={REVEAL}>
        <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
        <ActBtn icon={Pencil} label="Edit & resend" onClick={onEdit} />
        {canRewind && onRewind && <ActBtn icon={Rewind} label="Rewind to here" onClick={onRewind} />}
        {canFork && <ActBtn icon={GitBranch} label="Branch from here" onClick={onFork} />}
      </div>
      <Stamp ts={ts} />
    </div>
  )
}

function ActBtn({ icon: Icon, label, onClick, done, active }: { icon: typeof Copy; label: string; onClick: () => void; done?: boolean; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={label} aria-label={label}
      data-type="caption"
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
      style={done ? { color: 'var(--color-ok)' } : active ? { color: 'var(--color-primary)' } : undefined}>
      <Icon size={14} className={active ? 'fill-current' : ''} />
    </button>
  )
}
