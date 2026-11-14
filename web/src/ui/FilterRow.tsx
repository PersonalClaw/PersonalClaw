import { type ReactNode, useId } from 'react'
import { Check, type LucideIcon } from 'lucide-react'
import { withWeight } from '../design/fontWeight'
import { useInPhoneSheet } from './PhoneSheet'

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
 *
 *  `mark` is the panel form's (`HeaderFilter`'s `panel`): a check box (`'box'`, a choice of a multi
 *  section) or a radio (`'radio'`, of a single one) at the row's start, in place of the tint and
 *  the trailing check. It is drawn for the eye only (`aria-hidden`); the row still says whether it
 *  is chosen with `aria-pressed`. Unset, the row is as before.
 */
export function FilterRow({
  label, count, icon: Icon, selected, onClick, pressed, trailing, hint, mark,
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
  /** The panel form's mark at the row's start, in place of the tint (above). */
  mark?: 'box' | 'radio'
}) {
  const hintId = useId()
  // A 44 px row in a phone sheet, 32 px elsewhere.
  const inSheet = useInPhoneSheet()
  // The panel form marks a choice by its box or radio, not a tint.
  const tinted = selected && !mark
  return (
    // With a hint the row is named by its label alone, and the hint is its description.
    <button type="button" onClick={onClick} aria-pressed={pressed} aria-label={hint ? label : undefined} aria-describedby={hint ? hintId : undefined}
      className={`relative flex w-full items-center gap-s rounded-md px-s text-left transition-colors ${inSheet ? 'min-h-11 py-1' : hint ? 'min-h-8 py-1' : 'h-8'} ${tinted ? '' : 'hover:bg-surface-high active:bg-surface-high'}`}>
      {mark && <ChoiceMark mark={mark} on={selected} />}
      {tinted && (
        <span aria-hidden
          className="absolute inset-0 rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }} />
      )}
      {Icon && <Icon size={14} aria-hidden className="relative shrink-0" style={{ color: tinted ? 'var(--color-primary)' : 'var(--color-on-surface-var)' }} />}
      <span className="relative min-w-0 flex-1">
        <span data-type="label-s" className="block truncate"
          style={withWeight({ color: tinted ? 'var(--color-primary)' : 'var(--color-on-surface)' }, tinted ? 550 : 400)}>{label}</span>
        {hint && <span id={hintId} data-type="caption" className="block truncate text-on-surface-low">{hint}</span>}
      </span>
      {typeof count === 'number' && count > 0 && (
        <span data-type="caption" className="relative shrink-0 text-on-surface-low tabular-nums">{count}</span>
      )}
      {trailing}
    </button>
  )
}

/** The panel form's check box or radio, for the eye, drawn as `ChoiceList`'s radio is: its border
 *  is kept under forced colours, and a chosen one is filled (a box with its check, a radio with its
 *  dot). */
function ChoiceMark({ mark, on }: { mark: 'box' | 'radio'; on: boolean }) {
  return (
    <span aria-hidden data-choice-mark={mark} data-on={on || undefined}
      className={`relative inline-flex size-4 shrink-0 items-center justify-center border ${mark === 'box' ? 'rounded-xs' : 'rounded-pill'} ${on ? 'border-primary bg-primary text-on-primary' : 'border-outline'}`}>
      {on && (mark === 'box' ? <Check size={12} strokeWidth={3} /> : <span className="size-1.5 rounded-pill bg-on-primary" />)}
    </span>
  )
}
