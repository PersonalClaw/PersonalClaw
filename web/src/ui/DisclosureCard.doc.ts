import type { UiDoc } from './uiDoc'

// Doc object for DisclosureCard. The "two byte-identical copies, and the duplication duplicated a
// clipped-focus-ring defect" history is a source comment on the component — encoded here as
// machine-readable data. Prop type/required are DERIVED at build time.
const doc: UiDoc = {
  name: 'DisclosureCard',
  keywords: ['collapse', 'collapsible', 'disclosure', 'expand', 'accordion', 'settings', 'card', 'use-case', 'binding'],
  description:
    'A collapsible settings card: an always-visible header summarising what is currently bound (accent icon chip, label, one-line subtitle, right-hand count pill) over a body that discloses the controls which change it. The header is a real button wired to the body with aria-expanded + aria-controls; open state is internal and uncontrolled.',
  props: [
    { name: 'icon', description: "The binding's glyph. Rendered in a 28px chip that carries the accent when something is bound." },
    { name: 'label', description: "The binding's name — the header's primary line." },
    { name: 'subtitle', description: 'One-line summary of what is bound. A node, not a string, so callers can render an italic "nothing bound" fallback rather than empty text.' },
    { name: 'active', description: 'Whether something IS bound. Drives the icon chip accent, which is what makes a scan of the list show the gaps.' },
    { name: 'count', description: 'How many options this card can offer. The pill is suppressed at 0 — a card with nothing to offer says so in its body instead.' },
    { name: 'countLabel', description: "Noun for the count pill; defaults to 'available'." },
    { name: 'children', description: "The disclosed body. Rendered in the card's own flex column with its gap and padding, so callers pass plain children and never re-state the layout." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for DisclosureCard for any "summarise a binding, disclose its options" row in settings — ModelsPanel and SearchPanel each hand-rolled this exact shell; this is the single source.' },
    { guidance: true, description: 'Pass a node for subtitle when the unbound state needs its own voice (an italic "none — falls back to General" reads as a state, where empty text reads as a bug).' },
    { guidance: true, description: 'Let the card own its open state. Both current callers kept an `open` flag nothing outside the shell read, which is exactly the state that belongs to the primitive.' },
    { guidance: false, description: 'Do not re-declare the body layout at the call site. The card already applies the flex column, gap, top border and padding; a nested wrapper double-pads it.' },
    { guidance: false, description: 'Do not remove focus-visible:-outline-offset-2 from the header. The header fills an overflow-hidden card, so the global rail\'s outward outline-offset draws entirely outside the clip and NOTHING paints — both original copies shipped with no visible focus indicator (WCAG 2.4.7).' },
    { guidance: false, description: 'Do not add a controlled open/onOpenChange pair until a caller needs one. Speculative API on a primitive extracted to remove duplication re-introduces the divergence it was extracted to end.' },
  ],
  anatomy: [
    'div (overflow-hidden rounded-lg surface-container card — the clip that makes the header\'s negative outline-offset necessary)',
    'button (disclosure header; aria-expanded + aria-controls → the body)',
    'ChevronRight (rotates 90deg and takes the accent when open)',
    'span (28px icon chip; accentChip when active, quiet surface fill when not)',
    'div (label-s name over a caption subtitle)',
    'span (count pill; suppressed at 0)',
    'div (the disclosed body — bordered, padded flex column carrying children)',
  ],
}

export default doc
