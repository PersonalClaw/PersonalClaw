import type { ReactNode } from 'react'
import { cx } from './cx'

/** A block-level clickable CARD/TILE — the kit's home for "the whole card is one
 *  click target" (library grids, gallery tiles). Distinct from {@link Button}
 *  (an inline CTA pill) and {@link QuietButton} (a compact toolbar action): a
 *  TileButton is a bordered container whose CHILDREN are the content (preview,
 *  title rows); it owns only the card chrome — border, radius, hover, the
 *  active ring — and the accessible button semantics. `active` marks selection. */
export function TileButton({ children, onClick, active, title, ariaLabel, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  active?: boolean
  title?: string
  /** The accessible name, for a tile whose CONTENT is a document rather than a label.
   *
   *  Measured in Chrome's computed accessibility tree on `#/artifacts`: five artifact tiles whose
   *  names were **438-695 characters** of the rendered markdown preview — heading markers, `**`
   *  emphasis and blockquote `>` included — because a button with content takes its name from that
   *  content, and `title` loses to it. Nothing in the source looks wrong, which is why only an AX-tree
   *  read finds it. Pass the thing the tile IS. */
  ariaLabel?: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      // `active` is the selection, and until now it was PAINT ONLY: a border colour. Measured in the
      // AX tree on `#/settings/design`, 3 of 89 buttons exposed any state — the Mode row — while the
      // selected personality card announced exactly like the other two. `aria-pressed` is this app's
      // idiom for "one of N is chosen" (the Mode row 20 lines away, `WidthPill`, bento's `SegToggle`);
      // it is omitted entirely for a tile that is not selectable, because `active` is optional.
      aria-pressed={active}
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
