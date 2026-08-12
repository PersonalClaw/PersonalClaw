import type { UiDoc } from './uiDoc'

// Doc object for ResultAnnouncement — the sr-only live region that says what a list
// filter just did. Extracted from ListControls so a page with a hand-laid controls bar
// renders the SAME idiom instead of a second copy of it.
const doc: UiDoc = {
  name: 'ResultAnnouncement',
  keywords: ['results', 'count', 'announce', 'live region', 'status', 'aria-live', 'filter', 'search', 'a11y', 'sr-only'],
  description:
    'The sr-only polite live region that announces how many rows a list filter left — "25 items" / "1 item" (singularised) / "No matching items". Typing in a list filter rewrites the page under the user; the sighted cue is the list redrawing, and without this a screen-reader user gets no cue at all (axe reports nothing — a missing announcement is not a rule violation). ListControls renders it for its 13 consumers; this export exists for the pages that lay out their own controls bar and use SearchField directly (tasks, artifacts, files), so one behaviour has one implementation instead of four that drift.',
  props: [
    { name: 'count', description: 'How many rows the current search/filter leaves. Singularised at 1 by stripping the noun\'s trailing "s".' },
    { name: 'noun', description: 'Plural noun for the rows — "tasks", "artifacts", "lines", "items". Pick a word the copy does not already carry: noun="matches" made the zero branch read "No matching matches" on the files search, whose rows are matching LINES (file + line + col + preview), so "lines" is both honest and readable at 1.' },
    { name: 'active', description: 'True only while the user has actually narrowed the list — the surface\'s OWN definition of narrowed, compared against its OWN defaults. `filter !== \'all\'` is true at rest on a surface whose default filter is not "all" (inbox opens on "open", loops on "active"), and the region then announces a count to a user who has done nothing: measured as inbox saying "39 items" at idle.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render it on any list with a search or filter whose controls bar is hand-laid; if the page uses ListControls, pass `results` there instead — the bar already renders this component for you.' },
    { guidance: true, description: 'Keep it MOUNTED at all times (it renders empty when idle). A live region created at the same moment its content appears is not reliably observed by screen readers.' },
    { guidance: true, description: 'Derive `active` from the query or from a filter compared to that surface\'s own default value, so an idle list never announces its own length.' },
    { guidance: false, description: 'Do not hand-roll a second `<div role="status" aria-live="polite" className="sr-only">` on a page — that is the drift this extraction removes; import this component instead.' },
    { guidance: false, description: 'Do not make it visible: the count is already on screen as the list itself, so printing it duplicates what a sighted user can see and adds a shifting element to the bar.' },
  ],
  anatomy: ['div[role=status][aria-live=polite].sr-only', 'count + singularised noun, or "No matching <noun>", or empty while idle'],
}

export default doc
