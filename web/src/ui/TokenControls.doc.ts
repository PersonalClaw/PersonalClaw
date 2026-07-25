import type { UiDoc } from './uiDoc'

// TokenControls.tsx exports the three Design-panel token-row editors (color / scalar
// / select), so its doc default-exports an array. Each row edits a design token LIVE
// via useAppearance and shares the spin-to-default ResetButton.
const docs: UiDoc[] = [
  {
    name: 'ColorControl',
    keywords: ['token', 'color', 'swatch', 'hex', 'design', 'appearance', 'row', 'reset'],
    description:
      'A single color-token row for the Design panel — a swatch (native color input) plus a validated hex field for the CURRENT mode (dark/light), with a reset. Editing applies live to the token.',
    props: [
      { name: 'token', description: 'The ColorToken (varName + label) this row edits.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use ColorControl for editing a color design token in the Design panel rather than a bespoke picker — it applies live per current mode and wires reset-to-default for free.' },
      { guidance: true, description: 'It edits the token for the CURRENT mode only (dark vs light) — switch mode to edit the other.' },
      { guidance: false, description: 'Do not hardcode raw hex into chrome — this control is how a color token itself is set; everything else consumes the token.' },
    ],
    anatomy: ['swatch (native color input overlay)', 'label', 'hex text field (#rrggbb validated)', 'ResetButton (RotateCcw spins a full turn)'],
  },
  {
    name: 'ScalarControl',
    keywords: ['token', 'scalar', 'slider', 'range', 'number', 'unit', 'design', 'reset'],
    description:
      'A single scalar-token row for the Design panel — label + formatted value + range slider + reset. Formats the value by the token\'s declared unit (px/% → integer, a specific unit → 1-decimal, unitless → a "1.00×" multiplier). Editing applies live.',
    props: [
      { name: 'token', description: 'The ScalarToken (varName, label, min/max/step, optional unit) this row edits.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use ScalarControl for a numeric design token (spacing, radius, durations, multipliers) in the Design panel — it respects the token min/max/step and its unit formatting.' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['label', 'formatted value readout (tabular-nums)', 'range slider (accent-primary)', 'ResetButton'],
  },
  {
    name: 'SelectControl',
    keywords: ['token', 'select', 'segmented', 'options', 'enum', 'design', 'pill', 'reset'],
    description:
      'A single select-token row for the Design panel — label + a segmented pill group of the token\'s options + reset, with a liquid active pill that slides between options (layoutId) instead of the fill jumping. Editing applies live.',
    props: [
      { name: 'token', description: 'The SelectToken (varName, label, options) this row edits.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use SelectControl for an enumerated design token (a small set of named options) in the Design panel.' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['label', 'segmented pill group (per-option buttons)', 'sliding active pill (layoutId)', 'ResetButton'],
  },
]

export default docs
