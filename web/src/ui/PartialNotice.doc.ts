import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'PartialNotice',
  keywords: ['partial', 'truncated', 'incomplete', 'window', 'paging', 'total', 'disclosure', 'status'],
  description:
    'The line a surface owes its reader when a SUCCESSFUL read returned only part of the data. The fourth load-state beside loading / stale / error: "Showing 10,000 of 12,431 tasks — dependency links to tasks outside this window are not drawn." A polite role="status" (a working screen stating a true fact, not bad news), self-gating on `complete` so a caller passes what its loader returned.',
  props: [
    { name: 'complete', description: 'Straight from the loader (e.g. `api.allTasks`). Renders nothing when true, so the call site cannot forget the `&&`.' },
    { name: 'shown', description: 'How many rows the caller actually holds.' },
    { name: 'total', description: 'How many exist — the number the bounded read could not reach.' },
    { name: 'what', description: 'The rows as a lowercase plural noun, copied from the same surface\'s LoadError and skeleton rather than invented, so one surface speaks one vocabulary in all four states.' },
    { name: 'detail', description: 'What is wrong BEYOND the missing rows, in this surface\'s own terms — a graph not drawing an edge, a picker not offering a candidate. Optional: a surface that only lists rows has nothing further to disclose.' },
    { name: 'className', description: 'Layout-only override for placement within the caller\'s header or toolbar.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use it wherever a bounded read feeds DERIVED structure — a dependency graph, a prerequisite picker, a dependents list. Those resolve an id against the rows at hand and read a row they do not hold as absent, so truncation makes them confidently wrong rather than merely short.' },
    { guidance: true, description: 'Fill `detail` with the derived consequence for that surface. "Showing 50 of 300" tells a reader rows are missing; it does not tell them the graph beside it is drawing unblocked nodes that are blocked.' },
    { guidance: false, description: 'Do not use it for a list that HAS all its rows and renders a slice — that is `MoreRow`, whose only casualty is the hidden rows themselves.' },
    { guidance: false, description: 'Do not make it an alert. The read succeeded and the screen is usable; `LoadError` interrupts because a failed read changes what the screen means, and this does not.' },
  ],
  anatomy: ['muted body-s status row carrying [data-partial] (returns null when complete)'],
}

export default doc
