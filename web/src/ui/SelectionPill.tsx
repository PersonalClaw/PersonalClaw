import { forwardRef } from 'react'
import type { LucideIcon } from 'lucide-react'

/** Floating action pill anchored at a text selection inside a scrolling
 *  transcript/preview — the small "Quote"/"Comment" affordance that pops above a
 *  highlighted passage. The parent owns selection detection and positioning
 *  (content-relative x/y within its scroll root) and forwards a ref so it can
 *  exclude clicks on the pill from its own mouseup/mousedown selection handlers.
 *  ``preventDefault`` + ``stopPropagation`` fire before ``onPress`` so the browser
 *  selection survives the click that acts on it. */
export const SelectionPill = forwardRef<HTMLButtonElement, {
  icon: LucideIcon
  label: string
  x: number
  y: number
  onPress: () => void
}>(function SelectionPill({ icon: Icon, label, x, y, onPress }, ref) {
  return (
    <button ref={ref} type="button"
      onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); onPress() }}
      className="absolute z-30 -translate-x-1/2 -translate-y-full inline-flex items-center gap-1.5 rounded-pill bg-surface-highest px-3 h-8 text-on-surface text-[0.8125rem] shadow-lg ring-1 ring-outline-variant/50 hover:bg-surface-high"
      style={{ left: x, top: y }}>
      <Icon size={13} className="text-primary" /> {label}
    </button>
  )
})

export interface SelectionAction { icon: LucideIcon; label: string; onPress: () => void }

/** Multi-action floating toolbar variant of {@link SelectionPill} — same anchoring
 *  contract (content-relative x/y, ref-forwarded so the parent excludes its own
 *  clicks, mousedown preventDefault+stopPropagation so the selection survives), but
 *  carries 2+ actions (e.g. Quote + Copy) as segmented buttons in one pill. Use it
 *  where a selection affords more than one verb; use SelectionPill for a single one. */
export const SelectionToolbar = forwardRef<HTMLDivElement, {
  actions: SelectionAction[]
  x: number
  y: number
}>(function SelectionToolbar({ actions, x, y }, ref) {
  return (
    <div ref={ref}
      className="absolute z-30 -translate-x-1/2 -translate-y-full inline-flex items-center rounded-pill bg-surface-highest h-8 shadow-lg ring-1 ring-outline-variant/50 overflow-hidden"
      style={{ left: x, top: y }}>
      {actions.map((a, i) => (
        <button key={a.label} type="button"
          onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); a.onPress() }}
          className={`inline-flex items-center gap-1.5 px-3 h-8 text-on-surface text-[0.8125rem] hover:bg-surface-high ${i > 0 ? 'border-l border-outline-variant/50' : ''}`}>
          <a.icon size={13} className="text-primary" /> {a.label}
        </button>
      ))}
    </div>
  )
})
