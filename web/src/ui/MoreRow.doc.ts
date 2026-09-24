import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'MoreRow',
  keywords: ['more', 'truncated', 'cap', 'residue', 'overflow', 'hidden', 'list', 'count', 'ellipsis'],
  description:
    'The line a capped list owes the label above it. When a section header states a full count ("Relations · 47") but the list renders a bounded slice of it, this states the difference ("… 17 more") so the list is not read as all of it. Renders nothing when nothing is hidden, so callers pass their numbers unconditionally instead of repeating the comparison.',
  props: [
    { name: 'total', description: 'How many items exist — the number the surrounding label states.' },
    { name: 'shown', description: 'How many are rendered: the cap actually applied by the caller\'s `.slice(0, n)`. Must match it, or the residue is wrong.' },
    { name: 'noun', description: 'Plural word for what is hidden, when "… 6 more" alone would not say. Beneath a stacked list the subject is obvious from what sits above it; beneath a TABLE it is not, because rows and columns are very different facts about the data being read.' },
    { name: 'className', description: 'Layout-only override for a caller whose list is a chip row rather than stacked rows (e.g. `px-1`).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render it whenever a list is capped under a label that states the total — the mismatch between a header promising N and a list showing fewer is the defect this exists for, not the cap itself.' },
    { guidance: true, description: 'Pass the same literal you sliced with. `shown` duplicates the cap, and a mismatch states a confidently wrong number; cappedListDisclosed.test.tsx pairs every MoreRow with its own list and asserts the two agree.' },
    { guidance: false, description: 'Do not write the sentence inline. Three sites each spelled their own version ("…{n} more", "… {n} more", "+{n} more") before this component existed, which is how one sentence became three.' },
    { guidance: false, description: 'Do not use it where a control can reveal the rest — an expanding "+N more" button is a disclosure the user can act on, and replacing it with a static row removes a feature.' },
  ],
  anatomy: ['muted 0.75rem row (returns null when total <= shown)'],
}

const partialCount: UiDoc = {
  name: 'PartialCount',
  keywords: ['partial', 'showing', 'of', 'thinned', 'server cap', 'scope', 'count', 'canvas', 'total'],
  description:
    'How much of the thing a view is showing, when the cap came from the SERVER rather than the caller\'s own slice — "768 of 1,540 relations". Its sibling MoreRow names the residue of a slice the caller wrote and vanishes when nothing is hidden; this states the scope positively and always renders, because a canvas has no rows to stop short of and the count itself is the fact. Use `of="more"` where the total is genuinely unknowable (a search that stopped at its match limit never counted the rest).',
  props: [
    { name: 'shown', description: 'How many the view has in hand — the number it drew, listed or plotted. Read it from the payload, never from a literal: the server owns the cap.' },
    { name: 'of', description: 'How many exist, from the same payload. `"more"` when the total is unknowable, which renders "first 500 matches" rather than inventing a denominator. A value at or below `shown` means the view is complete and the caveat drops.' },
    { name: 'noun', description: 'Plural word for what is counted. Required, unlike MoreRow\'s: this sentence is often the only count on the surface.' },
    { name: 'singular', description: 'The singular, for a count that can legitimately be 1. Agreement follows the number the noun belongs to — the total in "1 of 1,540 relations", the shown count everywhere else.' },
    { name: 'className', description: 'Layout-only override — a segment inside an overlay pill inherits the pill\'s colour rather than setting its own.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for it whenever a payload carries both a partial array and its own true total (`thinning.edges_total`, `counts.selected`, `truncated`). ui/serverCapDisclosed.test.ts sweeps the gateway handlers for exactly those signals and reds when one reaches a surface that says nothing.' },
    { guidance: true, description: 'Take both numbers from the payload. A hard-coded `shown` states the cap the client THINKS applies, which silently becomes wrong the day the server\'s default moves.' },
    { guidance: false, description: 'Do not use it for a slice the caller applied itself — that is MoreRow, whose "… N more" reads correctly under a list whose label already states the total.' },
    { guidance: false, description: 'Do not make it disappear when the view is complete. Inside a summary line a vanishing segment leaves a `·` separating nothing (ui/danglingSeparator.test.ts).' },
  ],
  anatomy: ['inline caption span, tabular-nums, inherits its parent\'s ink'],
}

export default [doc, partialCount]
