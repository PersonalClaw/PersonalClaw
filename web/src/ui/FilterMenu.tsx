import { useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { SlidersHorizontal, Check, type LucideIcon } from 'lucide-react'
import { Popover } from './Popover'
import { FilterRow } from './FilterRow'
import { Button } from './Button'
import { TextLink } from './TextLink'
import { fvs } from '../design/fontWeight'
import { accentChip } from '../design/accent'
import { cx } from './cx'
import { EDGE } from './SearchField'
import { useInPhoneSheet } from './PhoneSheet'

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
  /** In a header filter's label (`HeaderFilter`): named only while set away from its default. For
   *  a page with more choice sets than its label can read out, the ones whose default says nothing. */
  quiet?: boolean
  /** In a header filter's label: how this section's choice is said, from its key ("Sorted by
   *  name"); the chosen option's own label otherwise. */
  said?: (key: string) => string
  /** Rows after the choices that act rather than choose (Knowledge's New shelf, under its shelves).
   *  Choosing one closes the menu. A `hint` says what it needs, under its label. */
  actions?: { label: string; icon?: LucideIcon; hint?: string; onSelect: () => void }[]
  /** Several choices at once (Chat history's tags, any of which a chat may carry): `value` is the
   *  chosen keys joined by commas, '' for none, and each row adds or removes its own key. */
  multi?: boolean
  /** At most this many choices until the owner opens the rest from a "Show all" row, in place: the
   *  chosen ones, then the ones with the most rows, each in the section's order. Unset, every one. */
  limit?: number
  /** What the choices are, for the "Show all 23 folders" row. */
  limitNoun?: string
  /** In the panel form (`FilterSections`' `panel`): the column the section sits in, in order. A
   *  section that names neither goes under both columns, two to a row. Unread outside the panel. */
  column?: 1 | 2
}

/** The choices a section shows before its Show all row is pressed (`limit`): every chosen one, then
 *  the largest by count, up to the limit, in the section's order. */
export function limitedOptions(s: FilterSectionDef): FilterOption[] {
  if (!s.limit || s.options.length <= s.limit) return s.options
  const chosen = s.options.filter((o) => isChosen(s, o.key))
  const rest = s.options
    .map((o, i) => ({ o, i }))
    .filter(({ o }) => !isChosen(s, o.key))
    .sort((a, b) => (b.o.count ?? 0) - (a.o.count ?? 0) || a.i - b.i)
    .slice(0, Math.max(0, s.limit - chosen.length))
    .map(({ o }) => o)
  const shown = new Set([...chosen, ...rest].map((o) => o.key))
  return s.options.filter((o) => shown.has(o.key))
}

/** A multi section's value with `key` added, or taken out if it was in. */
export function toggledKey(value: string, key: string): string {
  const keys = value.split(',').filter(Boolean)
  return (keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]).join(',')
}

/** Whether a section has `key` chosen. */
function isChosen(s: FilterSectionDef, key: string): boolean {
  return s.multi ? s.value.split(',').includes(key) : s.value === key
}

/** The canonical header "Filter & sort" control: a single 40px pill that opens a
 *  popover holding every list criterion (scope / status / sort / …) as titled
 *  single-select sections. A count badge on the trigger shows how many sections
 *  hold a non-default value, so an active filter is obvious without opening it.
 *  At `size="sm"` the trigger is a 32 px icon button with its edge and a dot while
 *  a section is set, for a list's own toolbar. A section with a `limit` shows that
 *  many choices, the chosen ones first and then the largest, and a "Show all N
 *  <limitNoun>" row that opens the rest in place.
 *
 *  This replaces the older pattern of lining up a filter Segmented + a sort
 *  <select> + a scope dropdown across the header — collapsing N competing widgets
 *  into one, consistent across every page. */
export function FilterMenu({ sections, label = 'Filter', align = 'right', className, size = 'md' }: {
  sections: FilterSectionDef[]
  /** The word beside the icon; at `size="sm"`, the trigger's whole name and tooltip, which should say
   *  what is set ("Sort and filter: Recent activity · Active", `headerFilterLabel`). */
  label?: string
  align?: 'left' | 'right'
  /** Added to the trigger, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`). */
  className?: string
  /** `sm`: the icon alone, 32 px, on the container with its edge, and a dot while a section is set,
   *  for a list's own toolbar (the chat list's search row). */
  size?: 'sm' | 'md'
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
      trigger={(open, toggle) => size === 'sm' ? (
        <button type="button" onClick={toggle} aria-label={label} title={label} aria-expanded={open}
          data-filter-menu={activeCount > 0 ? 'set' : 'default'}
          className={cx(
            'relative inline-flex size-8 shrink-0 items-center justify-center rounded-md transition-colors duration-(--duration-short) hover:bg-surface-high hover:text-on-surface',
            EDGE,
            open ? 'bg-surface-high text-on-surface' : 'bg-surface-container text-on-surface-var',
            className,
          )}>
          <SlidersHorizontal size={16} strokeWidth={2} aria-hidden />
          {activeCount > 0 && <span aria-hidden data-filter-set className="absolute right-1 top-1 size-1.5 rounded-pill bg-primary" />}
        </button>
      ) : (
        <button type="button" onClick={toggle} aria-label="Filter & sort" title="Filter & sort" aria-expanded={open}
          data-type="body-s"
          className={`relative inline-flex items-center gap-1.5 h-10 rounded-pill px-4 transition-colors ${activeCount > 0 || open ? '' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}${className ? ` ${className}` : ''}`}
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
      {(close) => <FilterSections sections={sections} close={close} />}
    </Popover>
  )
}

/** The open menu's body: each section's choices, with its Clear, and Done. Shared by `FilterMenu`
 *  and the header's `HeaderFilter`, so the two are one menu.
 *
 *  `panel` is the panel form (the library's Filter, `HeaderFilter`'s `panel`): the sections in two
 *  columns, each in the column it names (`column`), in order, and those that name neither under
 *  both, two to a row, all scrolling as one; a multi section's choices draw a check box and a single
 *  section's a radio (`FilterRow`'s `mark`), in place of the tint and the trailing check, each still
 *  `aria-pressed` (a Show all row and an action row draw none); sections named as written, not in
 *  capitals; and no Done, since Escape, Tab past the last row, a click outside and the trigger close
 *  it, as any portaled `Popover`. Unset, the menu is as before. */
export function FilterSections({ sections, close, panel = false, foot }: {
  sections: FilterSectionDef[]
  close: () => void
  panel?: boolean
  /** In a phone sheet, the counting foot in place of Done ("Show 3,344 items"): what the filter
   *  leaves, which the page counts. Pressing it closes the sheet. Unread outside a sheet. */
  foot?: string
}) {
  // In a phone sheet (`HeaderFilter`'s `phoneSheet`): one Clear at the top, which clears every
  // section, in place of each section's own 24 px link, and the counting foot in place of Done.
  const inSheet = useInPhoneSheet()
  const anySet = sections.some((s) => s.value !== s.defaultKey)
  // The sections whose Show all row was pressed, by title: for as long as the menu is open.
  const [whole, setWhole] = useState<ReadonlySet<string>>(() => new Set())
  // The choices a limited section has shown, by title: a choice shown stays shown until the menu
  // closes, so unticking one that was shown for being chosen neither moves it nor drops the focus.
  const seen = useRef(new Map<string, Set<string>>())
  // A section whose Show all row was just pressed: the focus goes to its first choice, since the
  // row it was on is gone.
  const opened = useRef<string | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const title = opened.current
    if (title === null) return
    opened.current = null
    const section = [...(rootRef.current?.querySelectorAll<HTMLElement>('[data-section]') ?? [])].find((el) => el.dataset.section === title)
    section?.querySelector<HTMLElement>('button[aria-pressed]')?.focus()
  }, [whole])
  const shownOf = (s: FilterSectionDef): FilterOption[] => {
    if (whole.has(s.title) || !s.limit || s.options.length <= s.limit) return s.options
    const keep = new Set([...(seen.current.get(s.title) ?? []), ...limitedOptions(s).map((o) => o.key)])
    seen.current.set(s.title, keep)
    return s.options.filter((o) => keep.has(o.key))
  }
  const section = (s: FilterSectionDef) => {
        const options = shownOf(s)
        return (
        <Section key={s.title} title={s.title} asWritten={panel}
          onClear={!inSheet && s.value !== s.defaultKey ? () => s.onChange(s.defaultKey) : undefined}>
          {options.map((o) => (
            <Row key={o.key} option={o} selected={isChosen(s, o.key)} mark={panel ? (s.multi ? 'box' : 'radio') : undefined}
              onClick={() => s.onChange(s.multi ? toggledKey(s.value, o.key) : o.key)} />
          ))}
          {options.length < s.options.length && (
            <FilterRow label={`Show all ${s.options.length} ${s.limitNoun ?? 'choices'}`} selected={false}
              onClick={() => { opened.current = s.title; setWhole((prev) => new Set([...prev, s.title])) }} />
          )}
          {s.actions?.map((a) => (
            <FilterRow key={a.label} label={a.label} icon={a.icon} hint={a.hint} selected={false} onClick={() => { close(); a.onSelect() }} />
          ))}
        </Section>
        )
  }
  const clearAll = inSheet && anySet && (
    <div className="flex justify-end">
      <Button variant="ghost" size="sm" className="min-h-11" onClick={() => { for (const s of sections) if (s.value !== s.defaultKey) s.onChange(s.defaultKey) }}>Clear</Button>
    </div>
  )
  const end = inSheet
    ? <Button variant="primary" size="sm" onClick={close} className="mt-1 min-h-11 w-full">{foot || 'Done'}</Button>
    : <Button variant="ghost" size="sm" onClick={close} className="mt-1 w-full">Done</Button>
  if (panel && !inSheet) {
    const inColumn = (c: 1 | 2) => sections.filter((s) => s.column === c)
    const under = sections.filter((s) => s.column !== 1 && s.column !== 2)
    return (
      <div ref={rootRef} data-filter-panel className="flex flex-col gap-3 max-h-[70vh] overflow-y-auto overscroll-contain">
        <div className="grid grid-cols-2 gap-x-m gap-y-3">
          <div data-filter-column="1" className="flex min-w-0 flex-col gap-3">{inColumn(1).map(section)}</div>
          <div data-filter-column="2" className="flex min-w-0 flex-col gap-3">{inColumn(2).map(section)}</div>
        </div>
        {under.length > 0 && <div data-filter-under className="grid grid-cols-2 gap-x-m gap-y-3">{under.map(section)}</div>}
      </div>
    )
  }
  return (
    <div ref={rootRef} className={inSheet ? 'flex flex-col gap-3' : 'flex flex-col gap-3 max-h-[70vh] overflow-y-auto overscroll-contain'}>
      {clearAll}
      {sections.map(section)}
      {end}
    </div>
  )
}

/** One choice set of the menu: a group named by its title, so a screen reader says which set a
 *  row is in ("Type", "Shelf") as the old chip rows' groups did. */
function Section({ title, onClear, children, asWritten = false }: { title: string; onClear?: () => void; children: ReactNode; asWritten?: boolean }) {
  const titleId = useId()
  return (
    <div role="group" aria-labelledby={titleId} data-section={title} className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between px-2 pb-0.5">
        <span id={titleId} data-type="caption" className={asWritten ? 'text-on-surface-var' : 'text-on-surface-low uppercase tracking-wide'}>{title}</span>
        {onClear && <TextLink onClick={onClear} size="xs">Clear</TextLink>}
      </div>
      {children}
    </div>
  )
}

function Row({ option, selected, onClick, mark }: { option: FilterOption; selected: boolean; onClick: () => void; mark?: 'box' | 'radio' }) {
  const Icon = option.icon
  return (
    <>
      {option.groupLabel && <div data-type="caption" className="mt-1 px-2 pt-1.5 text-on-surface-low uppercase tracking-wide border-t border-on-surface/8">{option.groupLabel}</div>}
      {/* The row itself is `ui/FilterRow`, where the selected-row tint lives. `pressed` says
          which row is chosen: the check is drawn for the eye (`aria-hidden`), and a screen reader
          heard the same label for the chosen row and the rest (the chips these rows replaced
          announced it). */}
      <FilterRow label={option.label} count={option.count} icon={Icon} selected={selected} pressed={selected}
        onClick={onClick} mark={mark}
        trailing={selected && !mark ? <Check size={14} aria-hidden className="relative shrink-0 text-primary" /> : undefined} />
    </>
  )
}
