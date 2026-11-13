import type { UiDoc } from './uiDoc'

// Doc object for FoldRow, the fold at a list's end (the Projects list's "Made for goal loops" and
// "Not in a project").
const doc: UiDoc = {
  name: 'FoldRow',
  keywords: ['fold', 'disclosure', 'collapse', 'expand', 'list end', 'group', 'live count'],
  description:
    'A fold at a list\'s end: a full-width row that opens and closes what it folds, with a chevron, its label, its count in the low ink, and its live counts with their state marks at its end, so nothing live hides behind it. Closed it is outlined on its own; open, one outlined container holds the row and what it folds. Named by its whole line ("Made for goal loops, 3 projects, 1 running") with aria-expanded, and at least 44 px below 1024 px.',
  props: [
    { name: 'label', description: 'What the fold holds ("Made for goal loops").' },
    { name: 'count', description: 'How many it holds, in words ("3 projects"), or "" when there is none to say: a count at 0 is not drawn. Hidden below 768 px, where the name still says it.' },
    { name: 'live', description: 'The live counts at its end, each its words and its state mark\'s tone (a state family token, so it reads as the row marks do).' },
    { name: 'open', description: 'Whether it is open. The caller holds it, so a search can open a fold it matches inside and the owner can still close it.' },
    { name: 'onToggle', description: 'Opens or closes it.' },
    { name: 'children', description: 'What it folds, drawn inside its container while it is open.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Put live counts in `live`, never only in the label, so a closed fold still shows what is running or waiting inside it.' },
    { guidance: false, description: 'Do not use it for a section in the middle of a page: it is the end of a list, outlined to read as set apart from the list\'s own cards.' },
  ],
  anatomy: ['section (outlined; holds the row and, open, what it folds)', 'button (chevron, label, count, live counts; aria-expanded)', 'folded content (open only)'],
}

export default doc
