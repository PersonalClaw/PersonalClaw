import type { UiDoc } from './uiDoc'

// Doc object for HeaderFilter — every choice set of a page that is not its view, in its header.
const doc: UiDoc = {
  name: 'HeaderFilter',
  keywords: ['header', 'filter', 'sort', 'scope', 'status', 'kind', 'owner', 'sections', 'menu', 'topbar', 'cluster'],
  description:
    "The page's filter and sort, as a member of the header's action cluster (HeaderActions): one button holding every choice set of the page that is not its main view switch (scope, status, kind, owner, sort) as the sections of one menu. A header holds at most one HeaderSegmented, the page's view; every other choice set is a section here, whatever its size. Its label says what is set (\"Show: Chats · Active\"), composed from the sections' choices by headerFilterLabel, so a filtered page never looks unfiltered; a page chooses only the word before them. Where the cluster sheds to icons it is its icon, with a dot while any section is set away from its default, its name and tooltip still the whole label. It is never pushed into the … menu.",
  props: [
    { name: 'sections', description: "The titled choice groups ({ title, value, defaultKey, options, onChange, quiet?, said?, multi?, column? }), each single-select or, with `multi`, several choices at once. A `quiet` section is named in the label only once set away from its default; `said` words a choice from its key (\"Sorted by name\")." },
    { name: 'prefix', description: "The word before the choices (default 'Show'); empty where the choices say it themselves, as a sort alone does." },
    { name: 'className', description: "Added to the trigger. It has the compact widths' 44 px hit box (`TOUCH_TARGET`) already." },
    { name: 'phoneSheet', description: "From 320 to 768 px, open as a phone sheet (Popover's phoneSheet): 44 px rows (with `panel`, its boxes and radios at 44 px), one Clear at the top that clears every section in place of each section's 24 px link, and the counting foot `foot` in place of Done. Off by default." },
    { name: 'foot', description: "The phone sheet's counting foot, e.g. \"Show 3,344 items\": what the filter leaves, which the page counts. Pressing it closes the sheet; without it the foot reads Done." },
    { name: 'panel', description: "The panel form (the library's Filter): a 560 px panel of two columns, each section in the column its `column` names, in order, and those that name neither under both, two to a row, scrolling as one in 70vh; choices drawn as check boxes (multi) and radios (single), each still aria-pressed; names as written; no Done (Escape, Tab past the last row, a click outside and the trigger close it). Unset, today's 264 px column with captions, tint, check and Done." },
  ],
  bestPractices: [
    { guidance: true, description: "Lead with the section that says what the list shows (its scope or status), with a default option that reads as a phrase (\"All triggers\", \"Any status\"), and mark the finer sections quiet." },
    { guidance: true, description: "Set each section's defaultKey to the option that means not filtering: the set mark, the dot and the inline Clear measure against it." },
    { guidance: false, description: 'Do not pass a label of your own: the label is composed from the choices, so it cannot say a value the page is not showing.' },
    { guidance: false, description: 'Do not draw a second HeaderSegmented or a Segmented strip for a choice set: it is a section here.' },
  ],
  anatomy: ['tier-aware trigger (sliders icon + label, or icon + set dot)', 'portaled Popover', 'FilterSections: per-section blocks with Clear, Done'],
}

export default doc
