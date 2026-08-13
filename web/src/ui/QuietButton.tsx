import type { ReactNode } from 'react'
import { cx } from './cx'

/** The quiet compact inline action in a content-viewer toolbar — a dimmed
 *  ink-low label + leading glyph, medium radius, 28px tall; hover fills to
 *  surface-high and brightens the ink. Deliberately smaller, dimmer, and
 *  square-cornered vs the pill ghost {@link Button} (h-7/text-[0.75rem]/
 *  text-on-surface-low, not h-8+/pill/text-on-surface) so it recedes into a
 *  header action row rather than reading as a CTA. Four toolbar actions —
 *  ArtifactViewer's "Source file" + "Download", FileViewer's "Artifact",
 *  LoopCockpitPage's findings-log "Download" — rendered this exact markup
 *  inline; this is the single source. Children are the leading glyph + label
 *  (they carry the accessible name); `title` adds the supplementary tooltip
 *  three of the sites pass. */
export function QuietButton({ children, onClick, onDoubleClick, title, ariaExpanded, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  onDoubleClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  title?: string
  /** When this quiet action is a DISCLOSURE, its open state — same prop name `Button` already uses, so
   *  the two siblings answer the question the same way rather than each inventing a spelling.
   *
   *  Six of its call sites are disclosures and every one was silent: ChatPage's "View / Hide", the
   *  artifact viewer's "Compare versions / Close compare", and `WorkflowRunDetail`'s four panel toggles
   *  (workspace, outbox, introspect, steer). Each swaps its own label, which tells a user what the NEXT
   *  click does — but not whether the panel is open right now, which is what `aria-expanded` carries.
   *  Omit it for a plain quiet action (Download, Source file) and no state is claimed. */
  ariaExpanded?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      title={title}
      aria-expanded={ariaExpanded}
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem]',
        'text-on-surface-low hover:bg-surface-high hover:text-on-surface',
        className,
      )}
    >
      {children}
    </button>
  )
}
