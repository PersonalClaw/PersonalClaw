import { useId, useMemo, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { SlidersHorizontal, Check, type LucideIcon } from 'lucide-react'
import { Popover } from './Popover'
import { spring, bounce } from './../design/motion'

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
  // Per-FilterMenu base id — the sliding selected-row indicator is namespaced per
  // section (`${baseId}-${section}`) so each section owns one indicator that
  // glides between its rows, without interfering across sections or instances.
  const baseId = useId()

  return (
    <Popover align={align} width={264} placement="bottom"
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="Filter & sort" title="Filter & sort" aria-expanded={open}
          className={`relative inline-flex items-center gap-1.5 h-10 rounded-pill px-4 text-[0.8125rem] transition-colors ${activeCount > 0 || open ? '' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}`}
          style={activeCount > 0 || open ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' } : undefined}>
          <SlidersHorizontal size={14} />
          <span className="hidden sm:inline">{label}</span>
          {/* active-filter count badge pops in on a playful spring (and out) so a
              filter engaging/clearing reads as a little event, not a silent swap */}
          <AnimatePresence>
            {activeCount > 0 && (
              <motion.span
                initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }}
                transition={bounce.playful}
                className="inline-flex items-center justify-center min-w-[1.05rem] h-[1.05rem] px-1 rounded-pill bg-primary text-on-primary text-[0.65rem] tabular-nums" style={{ fontVariationSettings: '"wght" 600' }}>{activeCount}</motion.span>
            )}
          </AnimatePresence>
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-3 max-h-[70vh] overflow-y-auto">
          {sections.map((s) => (
            <Section key={s.title} title={s.title}
              onClear={s.value !== s.defaultKey ? () => s.onChange(s.defaultKey) : undefined}>
              {s.options.map((o) => (
                <Row key={o.key} option={o} selected={s.value === o.key} onClick={() => s.onChange(o.key)} indicatorId={`${baseId}-${s.title}`} />
              ))}
            </Section>
          ))}
          <button type="button" onClick={close} className="mt-1 h-9 rounded-pill bg-surface-high text-on-surface text-[0.8125rem] hover:bg-surface-container transition-colors">Done</button>
        </div>
      )}
    </Popover>
  )
}

function Section({ title, onClear, children }: { title: string; onClear?: () => void; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between px-2 pb-0.5">
        <span className="text-on-surface-low text-[0.65rem] uppercase tracking-wide">{title}</span>
        {onClear && <button type="button" onClick={onClear} className="text-primary text-[0.7rem] hover:underline">Clear</button>}
      </div>
      {children}
    </div>
  )
}

function Row({ option, selected, onClick, indicatorId }: { option: FilterOption; selected: boolean; onClick: () => void; indicatorId: string }) {
  const Icon = option.icon
  return (
    <>
      {option.groupLabel && <div className="mt-1 px-2 pt-1.5 text-on-surface-low text-[0.65rem] uppercase tracking-wide border-t border-on-surface/8">{option.groupLabel}</div>}
      <motion.button type="button" onClick={onClick} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
        className={`relative flex items-center gap-s w-full rounded-md px-2 h-8 text-left transition-colors ${selected ? '' : 'hover:bg-surface-high'}`}>
        {/* liquid selected-row indicator: one shared element per section that SLIDES
            between rows via layoutId (Segmented pattern) instead of the tint
            blink-swapping from row to row when the selection changes. */}
        {selected && (
          <motion.span layoutId={indicatorId} transition={spring.spatialFast}
            className="absolute inset-0 rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' }} />
        )}
        {Icon && <Icon size={14} className="relative shrink-0" style={{ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface-var)' }} />}
        <span className="relative flex-1 min-w-0 truncate text-[0.8125rem]" style={{ color: selected ? 'var(--color-primary)' : 'var(--color-on-surface)', fontVariationSettings: selected ? '"wght" 550' : '"wght" 400' }}>{option.label}</span>
        {typeof option.count === 'number' && option.count > 0 && <span className="relative shrink-0 text-on-surface-low text-[0.7rem] tabular-nums">{option.count}</span>}
        {selected && <Check size={14} className="relative shrink-0 text-primary" />}
      </motion.button>
    </>
  )
}
