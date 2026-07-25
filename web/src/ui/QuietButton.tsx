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
export function QuietButton({ children, onClick, title, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  title?: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
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
