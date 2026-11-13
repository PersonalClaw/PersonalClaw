import type { UiDoc } from './uiDoc'

// Doc object for ListStatus — what a list says under its header, once its controls are in it.
const doc: UiDoc = {
  name: 'ListStatus',
  keywords: ['list', 'status', 'results', 'count', 'announce', 'stale', 'live region', 'a11y'],
  description:
    "A list's status under its header, now that every page-level control is in the header: the stale notice when the rows came from the cache past their freshness, and the sr-only announcement of how many rows a search or filter left (ResultAnnouncement). Without `line` it draws nothing visible while the rows are fresh; with `line` it draws that one line, what the list holds. It holds no control.",
  props: [
    { name: 'results', description: "How many rows the current search or filter leaves ({ count, noun, active }). Derive `active` from the user's query against the filter's own default, never hardcode it, or the list announces its length at rest." },
    { name: 'stale', description: "`useQuery`'s `stale`, passed straight through: the rows on screen are past their freshness window." },
    { name: 'line', description: 'Opt-in. The line under the title, visible, in the caption type and the meta ink: what the list holds, in words ("6 projects"; "2 projects and 4 chats match" while searching). While the list is narrowed (`results.active`) the announcement says these same words in place of "<count> <noun>", and at rest it stays empty, as without it. Without `line` the primitive draws what it always drew, so no other page moves.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render it first in the page body of any page whose header holds a HeaderSearch or HeaderFilter, counting the array the body renders.' },
    { guidance: true, description: "Pass `line` where a page draws its list's count under its title, as the Projects list does, and build its words from the same rows the body renders, so the line, the announcement and the list agree." },
    { guidance: false, description: 'Do not put a control on the line: every page-level control is in the header.' },
  ],
  anatomy: ['line (caption, meta ink; only with `line`)', 'StaleNotice (right-aligned, only when stale)', 'ResultAnnouncement (sr-only role=status; says the line while narrowed)'],
}

export default doc
