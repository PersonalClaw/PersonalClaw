import { useState } from 'react'
import { Copy, Check, RotateCcw, GitBranch, Volume2, Square, Pencil, ChevronLeft, ChevronRight } from 'lucide-react'

/** Action bar below an ASSISTANT turn. Copy + Speak always; Regenerate only on
 *  the last turn (it replaces the latest reply); Fork from any turn (branches a
 *  new session at that point). When a reply has been regenerated, a ‹ n/N ›
 *  variant switcher lets the user page back to a prior answer. Real, wired
 *  actions — no decorative thumbs/more. Reveals on hover of the message
 *  (group/msg) and stays while focused. `speaking` is controlled by the host
 *  (real playback state): the Speak button becomes a Stop toggle while this
 *  turn's audio is playing. */
export function AssistantActions({ text, isLast, speaking, canFork = true, variantCount = 0, variantIdx = 0, onCopy, onRegenerate, onFork, onSpeak, onSwitchVariant }: {
  text: string
  isLast: boolean
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
    <div className="mt-m flex items-center gap-0.5 translate-y-0.5 opacity-0 transition duration-150 group-hover/msg:translate-y-0 group-hover/msg:opacity-100 focus-within:translate-y-0 focus-within:opacity-100">
      {hasVariants && <VariantSwitcher count={variantCount} idx={variantIdx} onSwitch={onSwitchVariant!} />}
      <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
      {isLast && <ActBtn icon={RotateCcw} label="Regenerate" onClick={onRegenerate} />}
      {canFork && <ActBtn icon={GitBranch} label="Fork from here" onClick={onFork} />}
      <ActBtn icon={speaking ? Square : Volume2} label={speaking ? 'Stop' : 'Speak'} onClick={onSpeak} active={speaking} />
    </div>
  )
}

/** ‹ 2/3 › pager over an assistant turn's regenerated answer variants. Prev/next
 *  wrap-clamp at the ends (disabled, not looping) and call onSwitch with the new
 *  index; the backend swaps the active answer and echoes it over the WS. */
function VariantSwitcher({ count, idx, onSwitch }: { count: number; idx: number; onSwitch: (index: number) => void }) {
  const atStart = idx <= 0
  const atEnd = idx >= count - 1
  return (
    <div className="inline-flex items-center gap-0.5 rounded-md pr-1 text-on-surface-low" title={`Answer ${idx + 1} of ${count}`}>
      <button type="button" onClick={() => !atStart && onSwitch(idx - 1)} disabled={atStart}
        aria-label="Previous answer" title="Previous answer"
        className="inline-flex h-8 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high hover:text-on-surface disabled:opacity-30 disabled:hover:bg-transparent disabled:cursor-default">
        <ChevronLeft size={14} />
      </button>
      <span className="min-w-[2.1rem] select-none text-center text-[0.7rem] tabular-nums" aria-live="polite">{idx + 1}/{count}</span>
      <button type="button" onClick={() => !atEnd && onSwitch(idx + 1)} disabled={atEnd}
        aria-label="Next answer" title="Next answer"
        className="inline-flex h-8 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high hover:text-on-surface disabled:opacity-30 disabled:hover:bg-transparent disabled:cursor-default">
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

/** Action bar below a USER turn — Copy, Edit & resend (re-runs from that point),
 *  Fork. Right-aligned to sit under the bubble. `canFork` hides Fork on a
 *  non-persistent session (the backend refuses to fork temporary/incognito). */
export function UserActions({ text, canFork = true, onEdit, onFork }: { text: string; canFork?: boolean; onEdit: () => void; onFork: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }
  return (
    <div className="mt-1.5 flex items-center justify-end gap-0.5 translate-y-0.5 opacity-0 transition duration-150 group-hover/msg:translate-y-0 group-hover/msg:opacity-100 focus-within:translate-y-0 focus-within:opacity-100">
      <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
      <ActBtn icon={Pencil} label="Edit & resend" onClick={onEdit} />
      {canFork && <ActBtn icon={GitBranch} label="Fork from here" onClick={onFork} />}
    </div>
  )
}

function ActBtn({ icon: Icon, label, onClick, done, active }: { icon: typeof Copy; label: string; onClick: () => void; done?: boolean; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={label} aria-label={label}
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-on-surface-low text-[0.75rem] transition-colors hover:bg-surface-high hover:text-on-surface"
      style={done ? { color: 'var(--color-ok)' } : active ? { color: 'var(--color-primary)' } : undefined}>
      <Icon size={14} className={active ? 'fill-current' : ''} />
    </button>
  )
}
