import type { UiDoc } from './uiDoc'

// Doc object for FilterSections — the open filter menu's body, shared by the two filter triggers.
const doc: UiDoc = {
  name: 'FilterSections',
  keywords: ['filter', 'sort', 'sections', 'menu', 'clear', 'done'],
  description:
    "The open filter menu's body: each section's title with its inline Clear, its single-select rows (selected tint, icon, label, count, check), and Done. HeaderFilter and FilterMenu both render it, so the two are one menu.",
  props: [
    { name: 'sections', description: 'The titled single-select groups, as HeaderFilter takes them.' },
    { name: 'close', description: 'Closes the menu; Done calls it.' },
  ],
  bestPractices: [
    { guidance: true, description: "Render it inside the trigger's Popover, passing the Popover's close." },
  ],
  anatomy: ['per-section blocks (uppercase title + inline Clear)', 'option rows (ui/FilterRow)', 'Done button (ghost)'],
}

export default doc
