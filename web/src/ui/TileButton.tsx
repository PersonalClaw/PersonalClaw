import type { ReactNode } from 'react'
import { cx } from './cx'

/** A block-level clickable CARD/TILE — the kit's home for "the whole card is one
 *  click target" (library grids, gallery tiles). Distinct from {@link Button}
 *  (an inline CTA pill) and {@link QuietButton} (a compact toolbar action): a
 *  TileButton is a bordered container whose CHILDREN are the content (preview,
 *  title rows); it owns only the card chrome — border, radius, hover, the
 *  active ring — and the accessible button semantics. `active` marks selection. */
export function TileButton({ children, onClick, active, title, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  active?: boolean
  title?: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cx(
        'group flex flex-col overflow-hidden rounded-xl border text-left transition-colors',
        active ? 'border-primary/60' : 'border-outline-variant/40 hover:border-outline-variant',
        'bg-surface-container focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/50',
        className,
      )}
    >
      {children}
    </button>
  )
}
