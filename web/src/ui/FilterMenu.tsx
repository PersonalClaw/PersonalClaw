import { useMemo, type ReactNode } from 'react'
import { SlidersHorizontal, Check, type LucideIcon } from 'lucide-react'
import { Popover } from './Popover'
import { FilterRow } from './FilterRow'
import { Button } from './Button'
import { TextLink } from './TextLink'
import { fvs } from '../design/fontWeight'
import { accentChip } from '../design/accent'

/** One selectable choice within a filter section. */
export interface FilterOption {
  key: string
  label: string
  icon?: LucideIcon
  count?: number
  /** A faint divider + sub-heading rendered ABOVE this option (groups options
   *  within a section, e.g. presets vs projects). */
  groupLabel?: string
}

/** A titled, single-select group inside the filter menu. `defaultKey` is the
 *  value considered "not filtering" — selecting anything else counts toward the
 *  active badge and shows an inline Clear. */
export interface FilterSectionDef {
  title: string
  value: string
  defaultKey: string
  options: FilterOption[]
  onChange: (key: string) => void
}

/** The canonical header "Filter & sort" control: a single 40px pill that opens a
 *  popover holding every list criterion (scope / status / sort / …) as titled
 *  single-select sections. A count badge on the trigger shows how many sections
 *  hold a non-default value, so an active filter is obvious without opening it.
 *
 *  This replaces the older pattern of lining up a filter Segmented + a sort
 *  <select> + a scope dropdown across the header — collapsing N competing widgets
 *  into one, consistent across every page. */
export function FilterMenu({ sections, label = 'Filter', align = 'right' }: {
  sections: FilterSectionDef[]
  label?: string
  align?: 'left' | 'right'
}) {
  const activeCount = useMemo(
    () => sections.reduce((n, s) => n + (s.value !== s.defaultKey ? 1 : 0), 0),
    [sections],
  )
  return (
    <Popover align={align} width={264} placement="bottom"
      // 🔴 PORTAL for the same reason as the apps card menu, measured at 430×900 on
      // `#/notifications`: the menu's box was at **x = -202**, so 202px of its 264px width sat off
      // the LEFT edge of the shell — every filter LABEL clipped away, leaving a column of bare counts
      // ("83 ✓ · 2 · 33 · 6 …") with nothing to say what they filtered. `align="right"` anchors it to
      // a trigger that a phone header puts near the left of the remaining space, and a non-portal
      // flyout has no viewport clamp at all. Portal mode is `fixed`, anchored to the trigger rect and
      // clamped into the viewport. Eleven consumers move together, which is why the sweep re-measured
      // all 17 routes afterwards rather than just this one.
      portal
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="Filter & sort" title="Filter & sort" aria-expanded={open}
          data-type="body-s"
          className={`relative inline-flex items-center gap-1.5 h-10 rounded-pill px-4 transition-colors ${activeCount > 0 || open ? '' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}`}
          style={activeCount > 0 || open ? accentChip : undefined}>
          <SlidersHorizontal size={14} />
          <span className="hidden sm:inline">{label}</span>
          {/* The active-filter count. It swaps its number in place: filters are set and cleared
              many times an hour, so the badge does not pop in, out or on each change. */}
          {activeCount > 0 && (
            <span data-type="caption"
              className="inline-flex items-center justify-center min-w-[1.05rem] h-[1.05rem] px-1 rounded-pill bg-primary text-on-primary tabular-nums" style={fvs(600)}>{activeCount}</span>
          )}
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-3 max-h-[70vh] overflow-y-auto">
          {sections.map((s) => (
            <Section key={s.title} title={s.title}
              onClear={s.value !== s.defaultKey ? () => s.onChange(s.defaultKey) : undefined}>
              {s.options.map((o) => (
                <Row key={o.key} option={o} selected={s.value === o.key} onClick={() => s.onChange(o.key)} />
              ))}
            </Section>
          ))}
          <Button variant="ghost" size="sm" onClick={close} className="mt-1 w-full">Done</Button>
        </div>
      )}
    </Popover>
  )
}

function Section({ title, onClear, children }: { title: string; onClear?: () => void; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between px-2 pb-0.5">
        <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">{title}</span>
        {onClear && <TextLink onClick={onClear} size="xs">Clear</TextLink>}
      </div>
      {children}
    </div>
  )
}

function Row({ option, selected, onClick }: { option: FilterOption; selected: boolean; onClick: () => void }) {
  const Icon = option.icon
  return (
    <>
      {option.groupLabel && <div data-type="caption" className="mt-1 px-2 pt-1.5 text-on-surface-low uppercase tracking-wide border-t border-on-surface/8">{option.groupLabel}</div>}
      {/* The row itself is `ui/FilterRow` — the App Store's persistent
          category/source rail renders the same one, because it and this dropdown are one
          control at two viewport widths. The selected-row tint lives there. */}
      <FilterRow label={option.label} count={option.count} icon={Icon} selected={selected}
        onClick={onClick}
        trailing={selected ? <Check size={14} className="relative shrink-0 text-primary" /> : undefined} />
    </>
  )
}
