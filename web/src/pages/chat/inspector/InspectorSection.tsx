import { useId, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ChevronUp } from 'lucide-react'
import { fvs } from '../../../design/fontWeight'
import { IconButton } from '../../../ui/IconButton'

/** One section of the session inspector: a disclosure whose header says the section's name and,
 *  while it is folded, a one-line summary of what it holds; and, where the column can be reordered
 *  (`onMove`; not on a phone, where the inspector is a drawer), a pair of controls that move it up
 *  and down. Whether it is open, and where it sits, are the inspector's to keep (`inspectorLayout`).
 *  Opening a section reads what it shows and runs nothing. */
export function InspectorSection({ title, summary, open, onToggle, onMove, first, last, children }: {
  title: string
  /** What the folded section holds, in a line ("2 running · 3 finished"). */
  summary: ReactNode
  open: boolean
  onToggle: () => void
  onMove?: (by: -1 | 1) => void
  first: boolean
  last: boolean
  children: ReactNode
}) {
  const id = useId()
  const bodyId = `inspector-${id}`
  return (
    <section aria-labelledby={`${bodyId}-title`} className="border-b border-outline-variant/40 last:border-b-0">
      {/* `pr-s`: the rail clips at its edge, and the last control's focus ring needs its 4 px. */}
      <div className="group flex items-center gap-xs pr-s">
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={bodyId}
          className="flex min-h-11 min-w-0 flex-1 items-center gap-s px-m py-s text-left transition-colors duration-(--duration-short) hover:bg-surface-high focus-visible:-outline-offset-2">
          {open
            ? <ChevronDown size={14} aria-hidden className="shrink-0 text-primary" />
            : <ChevronRight size={14} aria-hidden className="shrink-0 text-on-surface-low" />}
          <span className="min-w-0 flex-1">
            <span id={`${bodyId}-title`} data-type="label-s" className="block text-on-surface" style={fvs(500)}>{title}</span>
            {!open && <span data-type="caption" className="block truncate text-on-surface-low">{summary}</span>}
          </span>
        </button>
        {/* Moving a section is a choice about the column, so it stays out of the way until the
            header is hovered or focused, and is always there for the keyboard. */}
        {onMove && <span className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <IconButton icon={ChevronUp} label={`Move ${title} up`} size={28} iconSize={14} onClick={() => onMove(-1)}
            disabled={first} disabledReason={`${title} is already first`} />
          <IconButton icon={ChevronDown} label={`Move ${title} down`} size={28} iconSize={14} onClick={() => onMove(1)}
            disabled={last} disabledReason={`${title} is already last`} />
        </span>}
      </div>
      {open && <div id={bodyId} className="px-m pb-m">{children}</div>}
    </section>
  )
}
