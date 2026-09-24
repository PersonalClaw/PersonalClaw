import type { UiDoc } from './uiDoc'

// Doc object for Slider (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// Slider.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Slider',
  keywords: ['slider', 'range', 'scale', 'dial', 'number', 'bounded', 'input', 'accent'],
  description:
    'The one canonical bounded-range slider — a native range input wearing the design-system accent. Use it for any "pick a number on a scale" control (the grill\'s slider question, a granularity dial) so they look identical everywhere instead of each call-site hand-rolling a raw range element the primitive-adoption ratchet would flag. Deliberately thin: the native control already gives keyboard + a11y; this owns the accent, the min/max/step contract, and the accessible name.',
  props: [
    { name: 'value', description: 'Current numeric position on the scale.' },
    { name: 'onChange', description: 'Fires with the next number as the thumb moves.' },
    { name: 'min', description: 'Lower bound (default 0).' },
    { name: 'max', description: 'Upper bound (default 10).' },
    { name: 'step', description: 'Increment between stops (default 1).' },
    { name: 'ariaLabel', description: 'Accessible name — a bare slider has none of its own, so pass one (or wrap in a Field that labels it).' },
    { name: 'disabled', description: 'Dim (40%) + not-allowed cursor; the track stops responding.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Slider for any bounded numeric pick rather than a raw <input type="range"> — the accent and a11y name come built in and the ratchet stays green.' },
    { guidance: true, description: 'Pair it with NumberField when the exact value matters, so the user can both drag and type.' },
    { guidance: false, description: 'Do not use it for an unbounded or open-ended number — that is NumberField\'s job; a slider implies a known min/max.' },
  ],
  anatomy: ['native range input (accent-primary track/thumb, full width)'],
}

export default doc
