import type { UiDoc } from './uiDoc'

// Doc object for HeaderFilter — every choice set of a page that is not its view, in its header.
const doc: UiDoc = {
  name: 'HeaderFilter',
  keywords: ['header', 'filter', 'sort', 'scope', 'status', 'kind', 'owner', 'sections', 'menu', 'topbar', 'cluster'],
  description:
    "The page's filter and sort, as a member of the header's action cluster (HeaderActions): one button holding every choice set of the page that is not its main view switch (scope, status, kind, owner, sort) as the sections of one menu. A header holds at most one HeaderSegmented, the page's view; every other choice set is a section here, whatever its size. Its label says what is set (\"Show: Chats · Active\"), composed from the sections' choices by headerFilterLabel, so a filtered page never looks unfiltered; a page chooses only the word before them. Where the cluster sheds to icons it is its icon, with a dot while any section is set away from its default, its name and tooltip still the whole label. It is never pushed into the … menu.",
  props: [
    { name: 'sections', description: "The titled single-select groups ({ title, value, defaultKey, options, onChange, quiet?, said? }). A `quiet` section is named in the label only once set away from its default; `said` words a choice from its key (\"Sorted by name\")." },
    { name: 'prefix', description: "The word before the choices (default 'Show'); empty where the choices say it themselves, as a sort alone does." },
    { name: 'className', description: "Added to the trigger, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`)." },
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
