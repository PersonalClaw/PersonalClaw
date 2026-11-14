import { StaleNotice } from './StaleNotice'

// The list's status (`ListStatus`) and its announcement (`ResultAnnouncement`). The controls bar
// this module was named for, `ListControls`, is gone: every page-level control is in the page's
// header (`app/pageControlsAreInTheHeader.test.ts`).

/** A list's status under the header, now that its controls are in the header: the stale notice
 *  when the rows came from the cache past their freshness, and the announcement of what a search or
 *  filter just did (`ResultAnnouncement`, sr-only). No control sits here: every page-level control
 *  is in the page header's action area. Without `line` it draws nothing visible while the rows are
 *  fresh; with `line` it draws that line, the list's own count in words ("6 projects"). */
export function ListStatus({ results, stale, line }: {
  /** How many rows the current search/filter leaves, what to call them, and whether the user has
   *  narrowed the list, as `ResultAnnouncement` reads them. */
  results: { count: number; noun: string; active: boolean }
  /** `useQuery`'s `stale`, passed straight through. */
  stale?: boolean
  /** The line under the title, visible, in the caption type and the meta ink: what the list holds
   *  ("6 projects"; "2 projects and 4 chats match" while searching). While the list is narrowed
   *  the announcement says these same words, in place of "<count> <noun>"; at rest it stays
   *  empty, as without it. Holds no control. */
  line?: string
}) {
  return (
    <>
      {line !== undefined && (
        <p data-type="caption" data-list-line className="text-on-surface-low">{line}</p>
      )}
      {stale && (
        <div className="mx-auto flex w-full justify-end px-l pt-s">
          <StaleNotice stale what={results.noun} />
        </div>
      )}
      <ResultAnnouncement {...results} says={line} />
    </>
  )
}

/** The sr-only live region that says what a list filter just did — extracted from `ListControls`
 *  so a page whose controls bar is hand-laid can render the SAME idiom instead of a second one.
 *
 *  sr-only on purpose: the count is already visible as the list itself, or as `ListStatus`'s `line`
 *  where a page passes one, so printing it again would duplicate what a sighted user can see. With
 *  `line`, it says the line's own words while the list is narrowed, and stays empty at rest. Always MOUNTED
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
export function ResultAnnouncement({ count, noun, active, singular, empty, partial, says }: {
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
  /** The visible line's own words (`ListStatus`'s `line`), announced in place of the count while
   *  the list is narrowed, so a screen reader hears what a sighted reader sees. */
  says?: string
}) {
  return (
    <div role="status" aria-live="polite" className="sr-only">
      {active
        ? says ?? (count === 0
            ? empty ?? `No matching ${noun}`
            : `${partial ? 'first ' : ''}${count} ${count === 1 ? singular ?? noun.replace(/s$/, '') : noun}`)
        : ''}
    </div>
  )
}
