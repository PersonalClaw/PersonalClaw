import { type ReactNode } from 'react'
import { Segmented, type SegOption } from './Segmented'
import { SearchField } from './SearchField'

/** The canonical on-PAGE controls bar for a list section — search + an optional
 *  filter segmented + optional extra controls (sort, chips), pinned just below the
 *  TopBar and centered to the content width. List controls belong here, on the
 *  page, NOT in the header (the header keeps only structural view-switches + the
 *  primary action). Mirrors the chat-history layout so every list page reads the
 *  same. Render via WorkbenchLayout's `controls` slot, or inline above a body. */
export function ListControls({
  search, filter, results, children,
}: {
  /** Optional search box config — omit for a filter-only bar. */
  search?: { value: string; onChange: (v: string) => void; placeholder?: string; label?: string; autoFocus?: boolean }
  /** Optional single-select filter strip (status / kind / scope — NOT a view switch). */
  filter?: { value: string; onChange: (v: string) => void; options: SegOption[]; ariaLabel?: string }
  /** How many rows the current search/filter leaves, and what to call them.
   *
   *  Typing in a list filter changes the page under the user, and NOTHING told a
   *  screen-reader user what happened: measured on 6 surfaces (knowledge, prompts,
   *  triggers, apps, skills, projects) — filtering 26 rows down to 25, or to a
   *  "No matching items" empty state, produced **zero live regions** every time. The
   *  sighted cue is the list redrawing; without an announcement there is no cue at all.
   *
   *  Announced politely (a result count is an update, not an interruption) and only
   *  while a query/filter is actually narrowing — an idle list announcing its own length
   *  on mount would be noise. `FindBar`'s match counter is the same pattern; this brings
   *  the 13 `ListControls` consumers onto it. */
  results?: { count: number; noun: string; active: boolean }
  /** Extra controls (sort dropdown, filter chips) rendered after search + filter. */
  children?: ReactNode
}) {
  if (!search && !filter && !children) return null
  return (
    <div className="shrink-0 border-b border-outline-variant/30">
      <div className="mx-auto flex w-full flex-wrap items-center gap-s px-l py-m" style={{ maxWidth: 'var(--content-width)' }}>
        {search && (
          <div className="min-w-[12rem] flex-1">
            <SearchField value={search.value} onChange={search.onChange}
              placeholder={search.placeholder ?? 'Search'} ariaLabel={search.label ?? search.placeholder ?? 'Search'}
              name={`search-${(search.label ?? search.placeholder ?? 'list').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`}
              autoFocus={search.autoFocus} />
          </div>
        )}
        {filter && (
          <Segmented ariaLabel={filter.ariaLabel ?? 'Filter'} value={filter.value} onChange={filter.onChange} options={filter.options} />
        )}
        {children}
      </div>
      {/* sr-only: the count is already visible as the list itself, so printing it would
          duplicate what a sighted user can see and add a shifting element to the bar.
          Always MOUNTED (rendered empty when idle) — a live region created at the same
          moment its content appears is not reliably observed. */}
      <div role="status" aria-live="polite" className="sr-only">
        {results?.active
          ? (results.count === 0
              ? `No matching ${results.noun}`
              : `${results.count} ${results.count === 1 ? results.noun.replace(/s$/, '') : results.noun}`)
          : ''}
      </div>
    </div>
  )
}
