import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'MoreRow',
  keywords: ['more', 'truncated', 'cap', 'residue', 'overflow', 'hidden', 'list', 'count', 'ellipsis'],
  description:
    'The line a capped list owes the label above it. When a section header states a full count ("Relations · 47") but the list renders a bounded slice of it, this states the difference ("… 17 more") so the list is not read as all of it. Renders nothing when nothing is hidden, so callers pass their numbers unconditionally instead of repeating the comparison.',
  props: [
    { name: 'total', description: 'How many items exist — the number the surrounding label states.' },
    { name: 'shown', description: 'How many are rendered: the cap actually applied by the caller\'s `.slice(0, n)`. Must match it, or the residue is wrong.' },
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

export default doc
