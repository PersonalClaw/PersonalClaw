import type { UiDoc } from './uiDoc'

// Doc object for QuietButton (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// QuietButton.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'QuietButton',
  keywords: ['button', 'quiet', 'toolbar', 'inline', 'compact', 'minimal', 'action', 'ghost'],
  description:
    'The quiet, compact inline action in a content-viewer toolbar — a dimmed ink-low label + leading glyph, medium radius, 28px tall, hovering to a surface-high fill with brightened ink. Deliberately smaller, dimmer, and square-cornered vs the pill ghost Button so it recedes into a header action row rather than reading as a CTA (the ArtifactViewer/FileViewer/findings-log toolbar actions).',
  props: [
    { name: 'children', description: 'The leading glyph + label; they carry the accessible name (there is no separate label prop).' },
    { name: 'onClick', description: 'Click handler; receives the button mouse event.' },
    { name: 'onDoubleClick', description: 'Optional double-click handler (e.g. a chip whose single click fills and double click sends).' },
    { name: 'title', description: 'Supplementary native tooltip (three of the consuming sites pass one).' },
    { name: 'ariaExpanded', description: 'When this quiet action is a DISCLOSURE, its open state — the same prop name Button uses, so the two siblings answer the question the same way instead of each inventing a spelling. Six call sites are disclosures and every one was silent: ChatPage\'s View/Hide, the artifact viewer\'s Compare versions/Close compare, and WorkflowRunDetail\'s four panel toggles (workspace, outbox, introspect, steer). Each swaps its own label, which tells a user what the NEXT click does but not whether the panel is open right now — which is what aria-expanded carries. Omit it for a plain quiet action (Download, Source file) so no state is claimed.' },
    { name: 'disabled', description: 'The action is currently unavailable: 40% opacity, cursor-not-allowed, onClick/onDoubleClick suppressed, press feedback dropped. Mapped to aria-disabled and NEVER the native attribute (the same trade SquareIconButton and Button make), so the control keeps its tab stop and a keyboard user can reach it and hear why. This tier had no disabled state at all until now, and the measured consequence was OutboxPanel\'s "Choose files": it forwards to a file-picker input that is itself disabled={dropBusy}, so for the whole hand-over window the button stayed lit and clickable while every click was a silent no-op.' },
    { name: 'disabledReason', description: 'WHY it is unavailable, when disabled is true. Rides title (appended after an em dash, after any caller title), matching SquareIconButton.disabledReason and Button.disabledReason — an sr-only span inside the button would be concatenated into the accessible name, so the action would stop being findable by its own name. Omit it when the gate is self-evident; a compound gate should pass it only for the branch it actually describes.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for QuietButton for a secondary inline toolbar action that should recede in a header action row — not hand-rolled markup (this is the single source for those four toolbar buttons).' },
    { guidance: true, description: 'Put the glyph + label in children — they are the accessible name; add title only for a supplementary tooltip.' },
    { guidance: true, description: 'Pass ariaExpanded when the button reveals or hides adjacent content, bound to the same flag that gates it — a label that flips (View/Hide) tells a user what the next click does, not what the current state is.' },
    { guidance: false, description: 'Do not pass ariaExpanded on a plain quiet action (Download, Source file): aria-expanded="false" would announce a collapsed state to a control that discloses nothing.' },
    { guidance: true, description: 'Pass disabled (plus a disabledReason) whenever the thing the button forwards to is itself gated — a label that swaps to "Handing over…" says what is happening, not that the control is inert, and a lit button whose click does nothing is a dead click nobody is told about.' },
    { guidance: false, description: 'Do not pass disabledReason without disabled, and do not use it for a transient gate that is already self-evident: it rides title, so a permanent tooltip explaining a condition that is not currently true is noise.' },
    { guidance: false, description: 'Do not use QuietButton for a primary or standalone CTA — reach for Button (it is intentionally quieter and square-cornered so it reads as secondary).' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: [
    'motion.button (h-7 rounded-md, ink-low → surface-high hover, expressiveness-scaled press spring on the fast spatial preset)',
    'leading glyph + label (children)',
  ],
}

export default doc
