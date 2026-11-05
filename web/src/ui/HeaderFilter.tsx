import { SlidersHorizontal } from 'lucide-react'
import { cx } from './cx'
import { Popover } from './Popover'
import { FilterSections, type FilterSectionDef } from './FilterMenu'
import { accentChip } from '../design/accent'
import { fvs } from '../design/fontWeight'
import { useHeaderChild } from './HeaderActions'
import { TOUCH_TARGET } from './Toggle'

/** What a header filter says it is set to: its prefix, then each section's choice, in order
 *  ("Show: Chats · Active"). Every section set away from its default is named, so a filtered page
 *  never reads as unfiltered; a `quiet` section is left out only while at its default. The words
 *  come from the choice itself (its option's label, or the section's `said` of its key), so the
 *  label cannot say a value the page is not showing. An empty prefix leaves just the choices; with
 *  nothing named, the page is unfiltered and the label is "Filter". */
export function headerFilterLabel(sections: FilterSectionDef[], prefix = 'Show'): string {
  const chosen = sections
    .filter((s) => !s.quiet || s.value !== s.defaultKey)
    .map((s) => s.said?.(s.value) ?? s.options.find((o) => o.key === s.value)?.label ?? s.value)
    .filter(Boolean)
  // Nothing named: every section is quiet and at its default, so the page is unfiltered.
  if (chosen.length === 0) return 'Filter'
  return prefix ? `${prefix}: ${chosen.join(' · ')}` : chosen.join(' · ')
}

/** How many sections hold something other than their default: what the shed button's dot says. */
export function headerFilterSet(sections: FilterSectionDef[]): number {
  return sections.reduce((n, s) => n + (s.value !== s.defaultKey ? 1 : 0), 0)
}

/** The page's filter and sort, as a member of the header's action cluster (`HeaderActions`): one
 *  button holding every choice set of the page that is not its main view switch (scope, status,
 *  kind, owner, sort), as the sections of one menu. A header holds at most one `HeaderSegmented`,
 *  the page's view; every other choice set is a section here, whatever its size.
 *
 *  Its label says what is set, every section's choice (`headerFilterLabel`), so a filtered page
 *  never looks unfiltered. Where the cluster sheds to icons it is its icon, with a dot while any
 *  section holds something other than its default; its accessible name and tooltip still say the
 *  whole label. It is never pushed into the `…` menu, since a menu row cannot open this one, and
 *  a filter the page is under must stay in sight. */
export function HeaderFilter({ sections, prefix = 'Show', className }: {
  sections: FilterSectionDef[]
  /** Added to the trigger. It has the compact widths' 44 px hit box (`TOUCH_TARGET`) already. */
  className?: string
  /** The word before the choices: "Show", or nothing where the choices say it themselves (a sort
   *  alone, "Sorted by name"). */
  prefix?: string
}) {
  const label = headerFilterLabel(sections, prefix)
  const set = headerFilterSet(sections)
  const { tier, mark } = useHeaderChild({ priority: 'default', canIcon: true, neverOverflow: true, menu: { label, icon: SlidersHorizontal } })
  const iconOnly = tier === 'icon' || tier === 'overflow'
  return mark(
    <Popover portal placement="bottom" align="right" width={264}
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-haspopup="dialog" aria-expanded={open} aria-label={label} title={label}
          data-type="label-s" data-header-filter={set > 0 ? 'set' : 'default'}
          className={cx(
            'relative inline-flex h-10 shrink-0 items-center gap-1.5 rounded-pill transition-colors duration-(--duration-short)',
            // The compact widths' 44 px hit box, by default: a header filter is tapped there.
            TOUCH_TARGET,
            iconOnly ? 'w-10 justify-center' : 'max-w-[18rem] px-l',
            set > 0 || open ? '' : 'text-on-surface hover:bg-surface-high',
            className,
          )}
          style={{ ...fvs(470), ...(set > 0 || open ? accentChip : {}) }}>
          <SlidersHorizontal size={16} className="shrink-0" />
          {!iconOnly && <span className="min-w-0 truncate whitespace-nowrap">{label}</span>}
          {iconOnly && set > 0 && <span aria-hidden data-header-set className="absolute right-2 top-2 size-1.5 rounded-pill bg-primary" />}
        </button>
      )}>
      {(close) => <FilterSections sections={sections} close={close} />}
    </Popover>
  )
}
