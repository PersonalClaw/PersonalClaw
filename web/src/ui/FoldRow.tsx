import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { cx } from './cx'

/** One live count at a fold's end: its words and its state mark's tone. */
export interface FoldLive {
  text: string
  /** The mark's tone, a state family's token (`var(--color-state-waiting)`, …). */
  tone: string
}

/** ── A fold at a list's end ─────────────────────────────────────────────────────────────────
 *
 *  A full-width row that opens and closes what it folds: a chevron, its label, its count in the
 *  low ink, and its live counts with their state marks at its end, so nothing live hides behind
 *  it. Closed it is outlined on its own; open, one outlined container holds the row and what it
 *  folds. Named by its whole line ("Made for goal loops, 3 projects, 1 running"), with
 *  `aria-expanded`. At least 44 px below 1024 px. */
export function FoldRow({ label, count, live = [], open, onToggle, children }: {
  label: string
  /** "3 projects", or `''` when there is none to say. */
  count: string
  live?: readonly FoldLive[]
  open: boolean
  onToggle: () => void
  /** What it folds, drawn inside its container while it is open. */
  children?: ReactNode
}) {
  const name = [label, count, ...live.map((l) => l.text)].filter(Boolean).join(', ')
  return (
    <section data-fold={open ? 'open' : 'closed'}
      className={cx('flex flex-col rounded-lg shadow-[inset_0_0_0_1px_var(--color-outline-variant)]', open && 'gap-s px-s pb-s')}>
      <button type="button" aria-expanded={open} aria-label={name} onClick={onToggle}
        className={cx('flex min-h-11 w-full min-w-0 items-center gap-s rounded-lg text-left text-on-surface-var transition-colors hover:bg-surface-high focus-visible:-outline-offset-2', open ? 'px-s' : 'px-m')}>
        <ChevronRight aria-hidden size={15} className={cx('shrink-0 transition-transform', open && 'rotate-90')} />
        <span data-type="label-m" className="shrink-0">{label}</span>
        {count && <span data-type="body-s" data-fold-count className="min-w-0 truncate text-on-surface-low max-md:hidden">{count}</span>}
        {live.length > 0 && (
          <span className="ml-auto flex shrink-0 items-center gap-m">
            {live.map((l) => (
              <span key={l.text} data-type="label-s" className="inline-flex items-center gap-xs text-on-surface">
                <span aria-hidden data-mark="dot" className="size-2 rounded-pill border-[1.5px]" style={{ background: l.tone, borderColor: l.tone }} />
                {l.text}
              </span>
            ))}
          </span>
        )}
      </button>
      {open && children}
    </section>
  )
}
