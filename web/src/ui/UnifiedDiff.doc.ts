import type { UiDoc } from './uiDoc'

// Doc object for UnifiedDiff — the shared patch renderer (LV-5). Extracted from the code
// cockpit's inline commit view so the skill-refinement approval surface renders a patch the
// same way, with one marker→token map instead of two.
const doc: UiDoc = {
  name: 'UnifiedDiff',
  keywords: ['diff', 'patch', 'unified', 'hunk', 'commit', 'refinement', 'change', 'review'],
  description:
    "A unified-diff patch rendered as plain text, one <div> per line, colored by leading marker: added lines ok, removed lines danger, @@ hunk headers primary, and the ---/+++/index file headers muted so they do not read as an add or a remove. Used by the code cockpit's commit view and by the skill-refinement approval surface. Renders text content ONLY — never markdown, never HTML — because a patch can come from a model proposal or a turn transcript and an approval surface has to be safe to look at before you approve it.",
  props: [
    { name: 'patch', type: 'string', required: true, description: 'The unified diff. Empty lines keep a non-breaking space so a blank context line does not collapse and shift the alignment of every line after it.' },
    { name: 'label', type: 'string', required: false, description: "The accessible name for the scroll region (default 'Diff'). Pass something specific when two patches can appear on one page — two regions both named 'Diff' are an ambiguous name, not a name." },
    { name: 'className', type: 'string', required: false, description: 'Overrides the default <pre> classes. Keep overflow-x-auto and a mono font; the box is focusable so a keyboard user can scroll it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Give it a specific label when the surface can show more than one patch — the proposal surface names the skill it is changing.' },
    { guidance: true, description: 'Feed it the diff a backend COMPUTED, not one a model wrote in prose: the whole value of showing a patch on an approval surface is that it is the change, not a description of it.' },
    { guidance: false, description: 'Do not pass it through a markdown renderer, and do not inject it as raw HTML — patch text is untrusted, and escaping is the only reason this is safe to render.' },
    { guidance: false, description: 'Do not use it for a side-by-side comparison of two whole files — that is DiffView (Monaco), which needs a workspace path.' },
  ],
  anatomy: ['focusable, horizontally scrollable <pre> with an accessible name', 'one <div> per patch line, colored by its leading marker'],
}

export default doc
