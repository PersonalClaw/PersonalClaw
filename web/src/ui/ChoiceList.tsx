import { useId, type Ref } from 'react'
import { Check } from 'lucide-react'
import { cx } from './cx'

/** One choice in a {@link ChoiceList}: what it is called, the line that says what it does, and, for
 *  one that cannot be picked now, why. */
export interface Choice {
  key: string
  label: string
  hint?: string
  /** A choice that cannot be picked now. It stays in the list, skipped by the arrows, and its hint
   *  says why. */
  disabled?: boolean
}

/** A vertical pick-one list: a `radiogroup` named by its heading, each row a `radio` named by its
 *  label alone (`aria-labelledby`, so the hint is not read twice) and described by its hint.
 *
 *  One tab stop for the group, on the checked row, even one that cannot be picked now, or on the
 *  first row that can be picked when none is. The arrows move AND select, as a radiogroup's do
 *  (`Segmented`), with Home and End; a disabled row is skipped (from one, the arrows go to the
 *  nearest row that can be picked in their direction) and says why in its hint, at full contrast.
 *  A row is at least 40 px tall, and a long label or hint wraps anywhere rather than widen it.
 *
 *  `checkedRef` reaches the row that holds the tab stop, so a window can open with focus on it. */
export function ChoiceList({ labelledBy, options, value, onChange, checkedRef }: {
  /** The id of the heading that names the group. */
  labelledBy: string
  options: Choice[]
  value: string
  onChange: (key: string) => void
  checkedRef?: Ref<HTMLButtonElement>
}) {
  const id = useId()
  const live = options.filter((o) => !o.disabled)
  const stop = options.some((o) => o.key === value) ? value : live[0]?.key
  const move = (e: React.KeyboardEvent<HTMLButtonElement>, key: string) => {
    const at = live.findIndex((o) => o.key === key)
    // From a row that cannot be picked (a click, or the tab stop, put the focus there): the nearest
    // live row in the arrow's direction, wrapping.
    const from = options.findIndex((o) => o.key === key)
    const below = live.findIndex((o) => options.indexOf(o) > from)
    const above = live.map((o) => options.indexOf(o)).filter((i) => i < from).length - 1
    let next = -1
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = at >= 0 ? (at + 1) % live.length : (below >= 0 ? below : 0)
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = at >= 0 ? (at - 1 + live.length) % live.length : (above >= 0 ? above : live.length - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = live.length - 1
    else return
    e.preventDefault()
    const o = live[next]
    if (!o) return
    onChange(o.key)
    const group = e.currentTarget.closest('[role="radiogroup"]')
    group?.querySelector<HTMLElement>(`[data-choice="${CSS.escape(o.key)}"]`)?.focus()
  }
  return (
    <div role="radiogroup" aria-labelledby={labelledBy} className="flex flex-col">
      {options.map((o, i) => {
        const on = o.key === value
        const labelId = `${id}-l${i}`
        const hintId = o.hint ? `${id}-h${i}` : undefined
        return (
          <button key={o.key} type="button" role="radio" aria-checked={on} data-choice={o.key}
            aria-disabled={o.disabled || undefined} aria-labelledby={labelId} aria-describedby={hintId}
            ref={o.key === stop ? checkedRef : undefined}
            tabIndex={o.key === stop ? 0 : -1}
            onKeyDown={(e) => move(e, o.key)}
            onClick={o.disabled ? undefined : () => onChange(o.key)}
            className={cx(
              'flex min-h-10 w-full items-start gap-s rounded-md px-s py-1.5 text-left transition-colors',
              o.disabled ? 'cursor-not-allowed' : 'hover:bg-surface-high',
              on && !o.disabled && 'bg-surface-container',
            )}>
            <span aria-hidden="true" className={cx(
              'mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-pill border',
              on ? 'border-primary bg-primary text-on-primary' : 'border-outline',
              o.disabled && 'opacity-40',
            )}>
              {on && <Check size={12} strokeWidth={3} />}
            </span>
            <span className="flex min-w-0 flex-col">
              <span id={labelId} data-type="body-s" className={cx('text-on-surface [overflow-wrap:anywhere]', o.disabled && 'opacity-40')}>{o.label}</span>
              {o.hint && <span id={hintId} data-type="caption" className="text-on-surface-low [overflow-wrap:anywhere]">{o.hint}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}
