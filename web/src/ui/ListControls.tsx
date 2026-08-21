import { type ReactNode } from 'react'
import { Segmented, type SegOption } from './Segmented'
import { StaleNotice } from './StaleNotice'
import { SearchField } from './SearchField'

/** The canonical on-PAGE controls bar for a list section — search + an optional
 *  filter segmented + optional extra controls (sort, chips), pinned just below the
 *  TopBar and centered to the content width. List controls belong here, on the
 *  page, NOT in the header (the header keeps only structural view-switches + the
 *  primary action). Mirrors the chat-history layout so every list page reads the
 *  same. Render via WorkbenchLayout's `controls` slot, or inline above a body. */
export function ListControls({
  search, filter, results, stale, children,
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
  /** The rows on screen came from the cache and are past their freshness window — pass
   *  `useQuery`'s `stale` straight through. Renders the shared `StaleNotice` in this bar, next to
   *  the count, which is where a claim about the data belongs.
   *
   *  🔑 THE NOUN IS ALREADY DECLARED, in `results.noun`. Same reasoning as the loading-noun rail:
   *  a surface that says its rows are "triggers" when it COUNTS them has already told us what to
   *  call them while they are being re-read, so nothing here is invented. Needs `results` for
   *  that reason; without it there is no noun and the label is suppressed rather than guessed. */
  stale?: boolean
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
        {results && <StaleNotice stale={!!stale} what={results.noun} className="ml-auto" />}
      </div>
      <ResultAnnouncement {...(results ?? { count: 0, noun: '', active: false })} />
    </div>
  )
}

/** The sr-only live region that says what a list filter just did — extracted from `ListControls`
 *  so a page whose controls bar is hand-laid can render the SAME idiom instead of a second one.
 *
 *  sr-only on purpose: the count is already visible as the list itself, so printing it would
 *  duplicate what a sighted user can see and add a shifting element to the bar. Always MOUNTED
 *  (rendered empty when idle) — a live region created at the same moment its content appears is
 *  not reliably observed.
 *
 *  🪤 `active` MUST BE THE SURFACE'S OWN DEFINITION OF NARROWED, compared against its own defaults.
 *  A flag like `filter !== 'all'` is true at rest on a surface whose default filter is not `all`,
 *  and the region then announces "39 items" to a user who has done nothing.
 */
export function ResultAnnouncement({ count, noun, active }: {
  /** How many rows the current search/filter leaves. */
  count: number
  /** Plural noun for the rows ("tasks", "artifacts", "matches") — singularised at count 1. */
  noun: string
  /** True only while the user has actually narrowed the list. */
  active: boolean
}) {
  return (
    <div role="status" aria-live="polite" className="sr-only">
      {active
        ? (count === 0
            ? `No matching ${noun}`
            : `${count} ${count === 1 ? noun.replace(/s$/, '') : noun}`)
        : ''}
    </div>
  )
}
