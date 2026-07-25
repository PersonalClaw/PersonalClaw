import type { UiDoc } from './uiDoc'

// Doc object for AddItemButton. Its "quiet, rectangular, not the pill CTA" intent
// and the five duplicate inline copies it replaced were source comments — encoded
// here as machine-readable data. Prop type/required are DERIVED at build time.
const doc: UiDoc = {
  name: 'AddItemButton',
  keywords: ['add', 'row', 'append', 'list', 'editor', 'variable', 'step', 'plus'],
  description:
    'The quiet "add another row" affordance beneath an editable list (workflow steps, prompt/snippet variables). A surface-container fill with a medium radius, 36px tall, ink-var label with a leading glyph, lifting to surface-high on hover. Deliberately rectangular and understated so it aligns with the rounded-md list rows it sits under — not the pill CTA Button.',
  props: [
    { name: 'children', description: 'The leading icon + label (e.g. a Plus glyph and "Add step").' },
    { name: 'className', description: "Extra classes (tokens only). Pass 'self-start' where the button must not stretch in its flex column." },
    { name: 'onClick', description: 'Click handler that appends the new row; receives the mouse event.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for AddItemButton for any "add a row" control under an editable list — five editors hand-rolled this exact markup inline; this is the single source.' },
    { guidance: true, description: "Pass className='self-start' inside a flex column so the button hugs its content instead of stretching full width." },
    { guidance: false, description: 'Do not use Button (the pill CTA) for adding list rows — this understated rectangular fill deliberately matches the rounded-md surface-container rows it sits beneath.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['button (surface-container fill, rounded-md, 36px tall)', 'children (leading icon + label)'],
}

export default doc
