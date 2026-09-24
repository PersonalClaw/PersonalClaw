import type { UiDoc } from './uiDoc'

// Doc object for PageTitle (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// PageTitle.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'PageTitle',
  keywords: ['heading', 'h1', 'title', 'page', 'destination', 'topbar', 'landmark', 'a11y'],
  description:
    "The current destination's name, rendered as the page's h1. Every surface already showed a visible title as a bare span, so heading navigation — the H key in NVDA/JAWS, the rotor in VoiceOver — found nothing to land on: measured across 20 destinations, 17 had no h1 and 13 had no heading of any level. This is that same title with the tag it should have had. Purely semantic: data-type=\"title-l\" carries the size, line-height and weight, and preflight resets heading margins, so it renders pixel-identically to the span it replaces.",
  props: [
    { name: 'children', description: "The destination's name. May include trailing chrome the title legitimately owns — a count badge, a pending-items summary — since those read as part of the heading." },
    { name: 'className', description: 'Extra classes for the heading element, e.g. the flex row a title-with-badge needs. Tokens only — no raw hex or px.' },
  ],
  bestPractices: [
    { guidance: true, description: "Use it for the destination's own name, in the TopBar left slot, exactly once per page." },
    { guidance: true, description: 'Let a count badge or status summary sit inside it when the number is part of what the page is called ("Inbox 3 pending"); pass the flex classes via className.' },
    { guidance: false, description: 'Do not use it for a section heading inside a page — sections are h2, and skipping from h1 straight to h3 is the defect this primitive exists to stop being possible.' },
    { guidance: false, description: 'Do not give a docked panel or drawer header an h1. A side panel is not the page; claiming the document title for it misdescribes the structure (ChatPage\'s "Chat history" panel is the worked exclusion).' },
    { guidance: false, description: 'Do not render two of these on one route. A page has exactly one name, and a second h1 makes heading navigation ambiguous again.' },
  ],
  anatomy: ['h1[data-type="title-l"] (size/line-height/weight from the type role, margin reset by preflight)'],
}

export default doc
