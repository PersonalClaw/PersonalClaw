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
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for QuietButton for a secondary inline toolbar action that should recede in a header action row — not hand-rolled markup (this is the single source for those four toolbar buttons).' },
    { guidance: true, description: 'Put the glyph + label in children — they are the accessible name; add title only for a supplementary tooltip.' },
    { guidance: false, description: 'Do not use QuietButton for a primary or standalone CTA — reach for Button (it is intentionally quieter and square-cornered so it reads as secondary).' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['button (h-7 rounded-md, ink-low → surface-high hover)', 'leading glyph + label (children)'],
}

export default doc
