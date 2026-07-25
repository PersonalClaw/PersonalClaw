import type { UiDoc } from './uiDoc'

// Doc object for Toggle (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// Toggle.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Toggle',
  keywords: ['toggle', 'switch', 'on-off', 'boolean', 'setting', 'checkbox', 'a11y', 'knob'],
  description:
    'The one canonical on/off switch for the whole app — a pill track with a knob that springs across on toggle (bounce-tier settle). Track is primary when on, neutral when off; role="switch" + aria-checked for accessibility. Replaces ~11 hand-rolled inline-styled copies, and can render read-only or purely-decorative forms for display-only or nested-in-a-button cases.',
  props: [
    { name: 'on', description: 'Current state — true drives the primary track and slides the knob across.' },
    { name: 'onChange', description: 'Fires with the next boolean on click. Omit it (with readOnly) for a display-only indicator that renders a non-interactive span.' },
    { name: 'label', description: 'Accessible name (aria-label) for the switch role.' },
    { name: 'disabled', description: 'Dim (40%) + not-allowed; button form only.' },
    { name: 'size', description: "'md' default (h-6 w-10), 'sm' for dense rows (h-5 w-9)." },
    { name: 'readOnly', description: 'Render a display-only indicator (non-interactive span) — pair with omitting onChange so it can sit inside a larger clickable row without nesting buttons.' },
    { name: 'decorative', description: 'Purely visual — drops the switch role/aria entirely (aria-hidden) for a toggle sitting INSIDE an already-labeled clickable control, so it does not surface as a second unnamed switch node.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Toggle for any on/off switch rather than hand-rolling an inline-styled track — spring knob, token track colors, and role="switch"/aria-checked come built in.' },
    { guidance: true, description: 'Pass a label for the a11y name; for a display-only indicator, set readOnly and omit onChange so it renders a non-interactive span.' },
    { guidance: true, description: 'Set decorative ONLY when the switch is nested inside an already-labeled clickable control — it hides the switch from the a11y tree to avoid a duplicate unnamed node.' },
    { guidance: false, description: 'Do not nest an interactive Toggle inside another <button> — use decorative there, and let the wrapping labeled control own the click and a11y state.' },
  ],
  anatomy: ['track (button / span, rounded-pill, primary-on / neutral-off)', 'motion.span knob (springs across on toggle)'],
}

export default doc
