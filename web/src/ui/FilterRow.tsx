import { type ReactNode, useId } from 'react'
import { type LucideIcon } from 'lucide-react'
import { withWeight } from '../design/fontWeight'

/** ONE selectable filter row — `icon · label · count`, with a tint marking the selected
 *  one. The tint moves to a new row at once: a filter changes many times an hour, so it no
 *  longer slides between rows, and a press shows by the hover colour, with no scale.
 *
 *  `ui/FilterMenu`'s section rows render it, the header filter's among them. It lives in `ui/`
 *  because the `design/primitiveAdoption` ratchet counts a page-level hand-rolled button as
 *  bespoke chrome.
 *
 *  `pressed` says which row is chosen. The rows pass it: the trailing check is drawn for the eye
 *  and hidden from a screen reader (`aria-hidden`), so a tint and a check alone would tell a
 *  screen-reader user nothing.
 */
export function FilterRow({
  label, count, icon: Icon, selected, onClick, pressed, trailing, hint,
}: {
  label: string
  /** Rendered only when > 0 — a bare "0" beside a label reads as a broken count. */
  count?: number
  icon?: LucideIcon
  selected: boolean
  onClick: () => void
  /** Whether the row is the chosen one, for a screen reader (`aria-pressed`). */
  pressed?: boolean
  /** Trailing adornment (a section row's check mark, `aria-hidden`). */
  trailing?: ReactNode
  /** What the row needs to act, under its label (a Repeatable list's Reset: every task done
   *  first), read as its description. */
  hint?: string
}) {
  const hintId = useId()
  return (
    // With a hint the row is named by its label alone, and the hint is its description.
    <button type="button" onClick={onClick} aria-pressed={pressed} aria-label={hint ? label : undefined} aria-describedby={hint ? hintId : undefined}
      className={`relative flex w-full items-center gap-s rounded-md px-s text-left transition-colors ${hint ? 'min-h-8 py-1' : 'h-8'} ${selected ? '' : 'hover:bg-surface-high active:bg-surface-high'}`}>
      {selected && (
        <span aria-hidden
          className="absolute inset-0 rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }} />
      )}
      {Icon && <Icon size={14} aria-hidden className="relative shrink-0" style={{ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface-var)' }} />}
      <span className="relative min-w-0 flex-1">
        <span data-type="label-s" className="block truncate"
          style={withWeight({ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface)' }, selected ? 550 : 400)}>{label}</span>
        {hint && <span id={hintId} data-type="caption" className="block truncate text-on-surface-low">{hint}</span>}
      </span>
      {typeof count === 'number' && count > 0 && (
        <span data-type="caption" className="relative shrink-0 text-on-surface-low tabular-nums">{count}</span>
      )}
      {trailing}
    </button>
  )
}
