import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { type LucideIcon } from 'lucide-react'
import { withWeight } from '../design/fontWeight'
import { spring } from '../design/motion'

/** ONE selectable filter row — `icon · label · count`, with a sliding tint marking the
 *  selected one.
 *
 *  Extracted (PEP-3) because a second surface needed it. `ui/FilterMenu`'s dropdown rows
 *  and the App Store's persistent category/source rail are the SAME control at two
 *  viewport widths — the rail owns the two dimensions when it is on screen and the
 *  dropdown carries them when it is not — so they cannot be two hand-laid buttons that
 *  merely look alike today. Both render this.
 *
 *  It lives in `ui/` for a second reason the `design/primitiveAdoption` ratchet makes
 *  concrete: a page-level hand-rolled button element is bespoke chrome, and copying this row
 *  into `pages/apps/` raised that count. Extraction moves the button into the primitive
 *  layer instead of asking the ratchet for slack.
 *
 *  `pressed` is opt-in. The dropdown rows sit inside a popover whose selection is already
 *  announced by the trailing check, so they leave it undefined; the rail's rows are
 *  toggle buttons whose only selection signal IS `aria-pressed`, so a tint alone would tell
 *  a screen-reader user nothing.
 */
export function FilterRow({
  label, count, icon: Icon, selected, indicatorId, onClick, pressed, trailing,
}: {
  label: string
  /** Rendered only when > 0 — a bare "0" beside a label reads as a broken count. */
  count?: number
  icon?: LucideIcon
  selected: boolean
  /** `layoutId` for the shared selected-row indicator: one per GROUP, so the tint glides
   *  from row to row within that group instead of blink-swapping. Omit for a lone row. */
  indicatorId?: string
  onClick: () => void
  /** Set only where the row is a toggle button rather than a popover row. */
  pressed?: boolean
  /** Trailing adornment (the dropdown's check mark). */
  trailing?: ReactNode
}) {
  return (
    <motion.button type="button" onClick={onClick} aria-pressed={pressed}
      whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
      className={`relative flex h-8 w-full items-center gap-s rounded-md px-2 text-left transition-colors ${selected ? '' : 'hover:bg-surface-high'}`}>
      {selected && indicatorId && (
        <motion.span layoutId={indicatorId} transition={spring.spatialFast} aria-hidden
          className="absolute inset-0 rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }} />
      )}
      {Icon && <Icon size={14} aria-hidden className="relative shrink-0" style={{ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface-var)' }} />}
      <span className="relative min-w-0 flex-1 truncate text-[0.8125rem]"
        style={withWeight({ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface)' }, selected ? 550 : 400)}>{label}</span>
      {typeof count === 'number' && count > 0 && (
        <span className="relative shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{count}</span>
      )}
      {trailing}
    </motion.button>
  )
}
