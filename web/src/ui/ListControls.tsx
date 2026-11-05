import { StaleNotice } from './StaleNotice'

// The list's status (`ListStatus`) and its announcement (`ResultAnnouncement`). The controls bar
// this module was named for, `ListControls`, is gone: every page-level control is in the page's
// header (`app/pageControlsAreInTheHeader.test.ts`).

/** A list's status under the header, now that its controls are in the header: the stale notice
 *  when the rows came from the cache past their freshness, and the announcement of what a search or
 *  filter just did (`ResultAnnouncement`, sr-only). No control sits here: every page-level control
 *  is in the page header's action area. It draws nothing visible while the rows are fresh. */
export function ListStatus({ results, stale }: {
  /** How many rows the current search/filter leaves, what to call them, and whether the user has
   *  narrowed the list, as `ResultAnnouncement` reads them. */
  results: { count: number; noun: string; active: boolean }
  /** `useQuery`'s `stale`, passed straight through. */
  stale?: boolean
}) {
  return (
    <>
      {stale && (
        <div className="mx-auto flex w-full justify-end px-l pt-s">
          <StaleNotice stale what={results.noun} />
        </div>
      )}
      <ResultAnnouncement {...results} />
    </>
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
 *
 *  🪤 THE ANNOUNCED NOUN MUST BE THE VISIBLE NOUN. A surface whose on-screen counter says
 *  "17 matches" while this region says "17 lines" gives a screen-reader user different vocabulary
 *  than a sighted user discussing the same screen. When the visible noun breaks the defaults —
 *  "matches" composes "No matching matches", "1 matche" — override `empty`/`singular` rather than
 *  announcing a different word.
 */
export function ResultAnnouncement({ count, noun, active, singular, empty, partial }: {
  /** How many rows the current search/filter leaves. */
  count: number
  /** Plural noun for the rows ("tasks", "artifacts", "matches") — singularised at count 1. */
  noun: string
  /** True only while the user has actually narrowed the list. */
  active: boolean
  /** Count-1 form, when stripping the trailing "s" is wrong ("matches" → "match"). */
  singular?: string
  /** Zero-count sentence, when `No matching ${noun}` reads badly ("No matching matches"). */
  empty?: string
  /** The count is a CAP, not the answer — the search stopped early. Announces "first 500 matches",
   *  the same word `ui/MoreRow`'s `PartialCount` paints on screen, because the noun rule above
   *  applies to the caveat too: the files search said "first 500 matches" visually while announcing
   *  a flat "500 matches", which is the same lie in the channel nobody was looking at. */
  partial?: boolean
}) {
  return (
    <div role="status" aria-live="polite" className="sr-only">
      {active
        ? (count === 0
            ? empty ?? `No matching ${noun}`
            : `${partial ? 'first ' : ''}${count} ${count === 1 ? singular ?? noun.replace(/s$/, '') : noun}`)
        : ''}
    </div>
  )
}
