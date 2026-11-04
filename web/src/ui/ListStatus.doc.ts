import type { UiDoc } from './uiDoc'

// Doc object for ListStatus — what a list says under its header, once its controls are in it.
const doc: UiDoc = {
  name: 'ListStatus',
  keywords: ['list', 'status', 'results', 'count', 'announce', 'stale', 'live region', 'a11y'],
  description:
    "A list's status under its header, now that every page-level control is in the header: the stale notice when the rows came from the cache past their freshness, and the sr-only announcement of how many rows a search or filter left (ResultAnnouncement). It draws nothing visible while the rows are fresh, and holds no control.",
  props: [
    { name: 'results', description: "How many rows the current search or filter leaves ({ count, noun, active }). Derive `active` from the user's query against the filter's own default, never hardcode it, or the list announces its length at rest." },
    { name: 'stale', description: "`useQuery`'s `stale`, passed straight through: the rows on screen are past their freshness window." },
  ],
  bestPractices: [
    { guidance: true, description: 'Render it first in the page body of any page whose header holds a HeaderSearch or HeaderFilter, counting the array the body renders.' },
  ],
  anatomy: ['StaleNotice (right-aligned, only when stale)', 'ResultAnnouncement (sr-only role=status)'],
}

export default doc
