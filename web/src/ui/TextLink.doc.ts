import type { UiDoc } from './uiDoc'

// Doc object for TextLink (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// TextLink.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'TextLink',
  keywords: ['link', 'text', 'inline', 'anchor', 'hyperlink', 'navigation', 'external', 'primary', 'underline'],
  description:
    'The inline text-link idiom — a coral text-primary label that underlines on hover, for in-sentence navigations ("Browse the Store"), quiet inline actions ("Remove from queue", "View all loops"), and the occasional real <a> (external task URLs, memory deep-links). Renders a <button type="button"> by default, or an <a> when href is set; the single source replacing ~16 sites that hand-rolled text-primary hover:underline with drifting sizes and element types.',
  props: [
    { name: 'children', description: 'The link label (and, with icon set, the text beside the glyph).' },
    { name: 'href', description: 'When set, renders a real <a href> instead of a <button>; omit for a click-handler action.' },
    { name: 'external', description: 'For off-app URLs — adds target=_blank + rel="noopener noreferrer". Omit for in-app hash links.' },
    { name: 'onClick', description: 'Activation handler; receives the mouse event (works for both the button and anchor forms).' },
    { name: 'icon', description: 'Optional Lucide glyph; opts the row into inline-flex items-center gap-1 (icon-less links stay bare inline so they flow inside running text without a baseline shift).' },
    { name: 'iconPosition', description: "'leading' (default) places the glyph before the label, 'trailing' after it." },
    { name: 'iconSize', description: 'Glyph size in px (default 13).' },
    { name: 'size', description: "Type scale: 'inherit' (default, take the surrounding paragraph's size for the in-sentence case), 'xs' for dense chrome, 'sm' for standalone links." },
    { name: 'disabled', description: 'Dims to 50% and disables — button form only (an <a> has no disabled state).' },
    { name: 'title', description: 'Native tooltip text.' },
    { name: 'className', description: 'Extra layout (ml-auto, mt-1.5, normal-case) — tokens only, no raw hex/px.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for TextLink for any coral inline link/action rather than hand-rolling text-primary hover:underline — it standardizes size, element type, and icon slot across the app.' },
    { guidance: true, description: 'Pass href for a navigation and add external for off-app URLs (it wires target=_blank + rel="noopener noreferrer"); omit href for a click-handler action, which renders a <button>.' },
    { guidance: true, description: "Leave size='inherit' for links inside running text so they match the paragraph; use xs/sm only for standalone links." },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['<a> (when href) or <button type="button"> (text-primary, hover:underline)', 'optional leading/trailing icon glyph', 'label (children)'],
}

export default doc
