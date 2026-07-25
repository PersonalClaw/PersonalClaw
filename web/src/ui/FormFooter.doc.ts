import type { UiDoc } from './uiDoc'

// Doc object for FormFooter — the sticky Cancel/Save row at the bottom of a detail
// pane's edit form. The "bleeds to pane edges, stays visible while the form scrolls,
// single source for every *Detail edit form" intent was a source comment.
const doc: UiDoc = {
  name: 'FormFooter',
  keywords: ['footer', 'form', 'sticky', 'actions', 'save', 'cancel', 'edit', 'bar'],
  description:
    'The sticky edit-mode action bar — the right-aligned Cancel/Save row pinned to the bottom of a detail pane\'s edit form. Bleeds to the pane edges (`-mx-l`), sits on a translucent surface with a hairline top border, and stays visible while the form scrolls. Pure chrome — the buttons and their handlers are the caller\'s children.',
  props: [
    { name: 'children', description: 'The action buttons (typically Cancel + Save Button); rendered right-aligned with gap.' },
    { name: 'className', description: 'Extra classes on the sticky bar (tokens only).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for FormFooter for any detail-pane edit form\'s action row — every *Detail edit form (Task, Schedule, Lifecycle, Workflow, Agent, Prompt, Snippet) rendered this exact wrapper inline; this is the single source.' },
    { guidance: true, description: 'Put the primary Save as a variant="primary" Button and Cancel as a quieter variant, passed as children — the footer only owns the sticky, edge-bleeding, right-aligned layout.' },
    { guidance: false, description: 'Do not hand-roll a sticky bottom action bar with bespoke -mx / border / translucent classes — that duplication is what this consolidates.' },
  ],
  anatomy: ['sticky bottom div (edge-bleed -mx-l, translucent surface, hairline top border)', 'right-aligned children (action buttons)'],
}

export default doc
