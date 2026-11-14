import type { UiDoc } from './uiDoc'

// Doc object for FilterSections — the open filter menu's body, shared by the two filter triggers.
const doc: UiDoc = {
  name: 'FilterSections',
  keywords: ['filter', 'sort', 'sections', 'menu', 'clear', 'done'],
  description:
    "The open filter menu's body: each section's title with its inline Clear, its rows (single-select or, with `multi`, several at once; selected tint, icon, label, count, check), and Done. HeaderFilter and FilterMenu both render it, so the two are one menu. With `panel`, the panel form: two columns, check boxes and radios, names as written, no Done. In a phone sheet, one Clear at its top and the counting foot in place of Done.",
  props: [
    { name: 'sections', description: 'The titled choice groups, as HeaderFilter takes them, each single-select or, with `multi`, several at once; `column` places one in the panel form.' },
    { name: 'close', description: 'Closes the menu; Done, and a phone sheet\'s counting foot, call it.' },
    { name: 'panel', description: "The panel form (HeaderFilter's panel): each section in the column its `column` names (1 or 2), in order, and those that name neither under both, two to a row, scrolling as one; a multi section's choices draw a check box and a single section's a radio (FilterRow's mark), each still aria-pressed; names as written, not in capitals; no Done. Unset, the menu is as before." },
    { name: 'foot', description: "In a phone sheet, the counting foot in place of Done (\"Show 3,344 items\"), which the page counts; pressing it closes the sheet. Unread outside a sheet; without it the foot reads Done." },
  ],
  bestPractices: [
    { guidance: true, description: "Render it inside the trigger's Popover, passing the Popover's close." },
  ],
  anatomy: ['per-section blocks (uppercase title + inline Clear)', 'option rows (ui/FilterRow)', 'Done button (ghost)'],
}

export default doc
