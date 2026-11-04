import type { UiDoc } from './uiDoc'

// Doc object for ListControls — the list controls bar a page drew under its header, before every
// page-level control moved into the header's action area.
const doc: UiDoc = {
  name: 'ListControls',
  keywords: ['list', 'controls', 'search', 'filter', 'sort', 'toolbar', 'bar', 'segmented', 'page'],
  description:
    "A list controls bar — a search box, an optional single-select filter strip, and optional extra controls (sort, chips) — that pages drew under their TopBar. No page draws it now: every page-level control lives in the header's action area (a HeaderSearch, the one HeaderSegmented, a HeaderFilter), and the list's status under the header is a ListStatus. Renders nothing when empty.",
  props: [
    { name: 'search', description: 'Optional search-box config ({ value, onChange, placeholder?, label?, autoFocus? }); omit for a filter-only bar. A stable input name is derived from the label/placeholder.' },
    { name: 'results', description: "How many rows the current search/filter leaves ({ count, noun, active }), announced politely in an sr-only role=status region. Typing in a list filter rewrites the page under the user, and NOTHING told a screen-reader user what happened — measured on six surfaces (knowledge, prompts, triggers, apps, skills, projects): filtering 26 rows to 25, or to a \"No matching items\" empty state, produced ZERO live regions every time. Renders \"25 items\" / \"1 item\" (singularised) / \"No matching items\", and stays silent while `active` is false so an idle list never announces its own length. Same pattern as FindBar's match counter." },
    { name: 'stale', description: "The rows on screen came from the cache and are past their freshness window — `useQuery`'s `stale`, passed straight through. Renders the shared StaleNotice next to the count, because a claim about the data belongs beside the count of it. The noun is taken from `results.noun`, which the surface has already declared; without `results` there is no noun and the label is suppressed rather than guessed. Pass `stale`, never `loading` (false once anything is cached, so the label would never fire) and never `revalidating` (true on every mount, so it would label a genuinely fresh paint)." },
    { name: 'filter', description: 'Optional single-select filter strip ({ value, onChange, options, ariaLabel? }) for status/kind/scope — NOT a view switch. Renders as a Segmented.' },
    { name: 'children', description: 'Extra controls (sort dropdown, filter chips) rendered after search + filter.' },
  ],
  bestPractices: [
    { guidance: false, description: "Do not draw it on a page: put the search in a HeaderSearch and every choice set in the header's HeaderFilter, and announce the result count with a ListStatus (app/pageControlsAreInTheHeader.test.ts)." },
    { guidance: true, description: 'Where `results` is passed, derive `active` from the query (`!!q.trim()`), never hardcode it true, or the list announces its length on every mount.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['bottom-bordered shrink-0 container', 'full-width row', 'SearchField (flex-1)', 'Segmented filter strip', 'extra children (sort / chips)'],
}

export default doc
