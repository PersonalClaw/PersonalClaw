import type { UiDoc } from './uiDoc'

// Doc object for Eyebrow — the canonical caption-tier micro-label. Encodes the
// Weight-First rule (web/DESIGN.md §3/§6) as machine-readable Do/Don't so an
// app-building agent reaches for the role, not the uppercase-tracked drift it
// replaces (audit CD-02).
const doc: UiDoc = {
  name: 'Eyebrow',
  keywords: ['eyebrow', 'label', 'section', 'caption', 'kicker', 'overline', 'meta', 'chip', 'micro-label', 'weight-first'],
  description:
    "The one canonical caption-tier micro-label — the small grey label that heads a section, tags a chip, or captions a row. Renders the `caption` type role (0.75rem / wght 470) in sentence case per the Weight-First rule (web/DESIGN.md §3/§6); it replaces the uppercase-with-tracking eyebrow treatment that had drifted across the app (audit CD-02).",
  props: [
    { name: 'children', description: 'The label text (sentence case — never shouted in caps).' },
    { name: 'as', description: "Element to render: a block 'div'/'p' section label, an inline 'span' for a chip label or an eyebrow sharing a flex row with a value, or a semantic 'h2'/'h3' — a section heading whose visual treatment is the caption-tier label (the outline keeps its level). Defaults to 'div'." },
    { name: 'tone', description: "Ink tone: 'muted' (default, the section-label grey), or 'info'/'primary' for the semantic eyebrows (a queued nudge, an active marker). Exactly one color class is emitted so it never races a className color." },
    { name: 'id', description: "DOM id for the rendered element, so a caption-tier label can be an accessible-name target — a labelless control names itself by pointing at this id via aria-labelledby (the canonical Field label uses this)." },
    { name: 'className', description: 'Layout/spacing utilities for this instance (margins, a flex row, a chip fill). Never uppercase/tracking-* — that is the drift this replaces.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Eyebrow for every section eyebrow, chip label, or meta tag; it carries the weight-step, sentence-case caption treatment in one place.' },
    { guidance: true, description: 'Keep the label sentence case ("Done when", "Action plan") — emphasis is the weight step the caption role already applies.' },
    { guidance: false, description: "Do not hand-roll uppercase-with-tracking eyebrows; they are the exact Weight-First Don't (DESIGN.md §6) and are held down by the eyebrowWeightRole ratchet." },
    { guidance: false, description: 'Do not pass a `text-*` color in className to recolor it — use the tone prop, so only one ink utility is emitted (cx is a plain joiner and would let two colors race on stylesheet order).' },
  ],
  anatomy: ['Tag (div | span | p) with data-type="caption"', 'tone ink class (text-on-surface-low | text-info | text-primary)', 'children (the sentence-case label)'],
}

export default doc
