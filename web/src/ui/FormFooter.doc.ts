import type { UiDoc } from './uiDoc'

// Doc object for FormFooter — the sticky Cancel/Save row at the bottom of a detail
// pane's edit form. The "bleeds to pane edges, stays visible while the form scrolls,
// single source for every *Detail edit form" intent was a source comment.
const doc: UiDoc = {
  name: 'FormFooter',
  keywords: ['footer', 'form', 'sticky', 'actions', 'save', 'cancel', 'edit', 'bar'],
  description:
    'The sticky edit-mode action bar — the right-aligned Cancel/Save row pinned to the bottom of a detail pane\'s edit form. Bleeds to the pane edges (`-mx-l`), sits on a translucent surface with a hairline top border, and stays visible while the form scrolls. The buttons and their handlers are the caller\'s children; the bar also owns the last action\'s failure (`error`), so it is shown beside the action that produced it.',
  props: [
    { name: 'children', description: 'The action buttons (typically Cancel + Save Button); rendered right-aligned with gap.' },
    { name: 'className', description: 'Extra classes on the sticky bar (tokens only).' },
    { name: 'error', description: "The last action's failure (a string; omit or '' for none). Rendered IN the sticky bar, on its own row directly above the buttons, as an announced alert that takes focus when it appears or changes — so a refused save is on screen whatever the form's scroll position." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for FormFooter for any detail-pane edit form\'s action row — every *Detail edit form (Task, Schedule, Lifecycle, Workflow, Agent, Prompt, Snippet) rendered this exact wrapper inline; this is the single source.' },
    { guidance: true, description: 'Put the primary Save as a variant="primary" Button and Cancel as a quieter variant, passed as children — the footer only owns the sticky, edge-bleeding, right-aligned layout.' },
    { guidance: false, description: 'Do not hand-roll a sticky bottom action bar with bespoke -mx / border / translucent classes — that duplication is what this consolidates.' },
    { guidance: true, description: "Pass a save failure as `error`, never as a FieldError above the footer: that position is the END of the scrolling form, which put a refused task save 803px below the fold with focus on <body> — the user saw nothing happen." },
  ],
  anatomy: ['sticky bottom div (edge-bleed -mx-l, translucent surface, hairline top border)', 'optional full-row alert (the action\'s failure; focused when it appears)', 'right-aligned children (action buttons)'],
}

export default doc
