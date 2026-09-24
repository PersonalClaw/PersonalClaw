import type { UiDoc } from './uiDoc'

// Doc object for Toggle (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// Toggle.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Toggle',
  keywords: ['toggle', 'switch', 'on-off', 'boolean', 'setting', 'checkbox', 'a11y', 'knob'],
  description:
    'The one canonical on/off switch for the whole app — a pill track with a knob that springs across on toggle (bounce-tier settle). Track is primary when on, neutral when off; role="switch" + aria-checked for accessibility. Replaces ~11 hand-rolled inline-styled copies, and can render read-only or purely-decorative forms for display-only or nested-in-a-button cases. It also claims the hint published by the surrounding Row/Field as its accessible DESCRIPTION, which is what makes the sentence beside a settings switch reach a screen reader: measured across all 34 #/settings/* subpages, 58 of the 61 rendered switches sit inside a wrapper publishing a hint id and 0 carried any aria-describedby, so every one of those visible sentences was sighted-only; after, 53 of the 58 resolve to the hint text and the remaining 5 are soft-off and keep their reason. axe cannot see that — a paragraph beside a switch is valid HTML with no rule to violate.',
  props: [
    { name: 'on', description: 'Current state — true drives the primary track and slides the knob across.' },
    { name: 'onChange', description: 'Fires with the next boolean on click. Omit it (with readOnly) for a display-only indicator that renders a non-interactive span.' },
    { name: 'label', description: 'Accessible name (aria-label) for the switch role.' },
    { name: 'disabled', description: 'Dim (40%) + not-allowed; button form only.' },
    { name: 'disabledReason', description: 'WHY the switch is unavailable, when disabled is true. Given a reason it stays REACHABLE — aria-disabled instead of the native attribute, plus the reason as its title and the click suppressed — because a natively disabled switch leaves the tab order and a keyboard user tabs past it without learning it exists. Omit it for transient unavailability (in flight, still loading), where the stronger native attribute is right. Passing it also SUPPRESSES the row-hint description, because aria-describedby outranks title and would otherwise delete the reason from what a screen reader announces; the blocking fact wins, and the hint returns by itself once the precondition is cleared.' },
    { name: 'size', description: "'md' default (h-6 w-10), 'sm' for dense rows (h-5 w-9)." },
    { name: 'readOnly', description: 'Render a display-only indicator (non-interactive span) — pair with omitting onChange so it can sit inside a larger clickable row without nesting buttons.' },
    { name: 'decorative', description: 'Purely visual — drops the switch role/aria entirely (aria-hidden) for a toggle sitting INSIDE an already-labeled clickable control, so it does not surface as a second unnamed switch node.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Toggle for any on/off switch rather than hand-rolling an inline-styled track — spring knob, token track colors, and role="switch"/aria-checked come built in.' },
    { guidance: true, description: 'Pass a label for the a11y name; for a display-only indicator, set readOnly and omit onChange so it renders a non-interactive span.' },
    { guidance: true, description: 'Set decorative ONLY when the switch is nested inside an already-labeled clickable control — it hides the switch from the a11y tree to avoid a duplicate unnamed node.' },
    { guidance: true, description: 'When the switch is off-limits because of a PRECONDITION the user can fix (no password set, no model bound, a parent feature off), pass disabledReason so the control keeps its tab stop and says what would unlock it — the same contract Button carries.' },
    { guidance: false, description: 'Do not nest an interactive Toggle inside another <button> — use decorative there, and let the wrapping labeled control own the click and a11y state.' },
    { guidance: false, description: 'Do not pass disabledReason for an in-flight or still-loading state: re-clicking a switch mid-save is exactly what the native attribute prevents, and "not known yet" is not something the user can act on.' },
  ],
  anatomy: ['track (button / span, rounded-pill, primary-on / neutral-off)', 'motion.span knob (springs across on toggle)'],
}

export default doc
