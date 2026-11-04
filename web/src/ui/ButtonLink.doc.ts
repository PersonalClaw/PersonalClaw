import type { UiDoc } from './uiDoc'

// Doc object for ButtonLink. Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// ButtonLink.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'ButtonLink',
  keywords: ['link', 'button', 'anchor', 'navigation', 'cta', 'href', 'tonal', 'pill'],
  description:
    'A link drawn as a button: a real <a href> for a navigation that is a row’s one action ("Answer in Attention"), where a TextLink would be too quiet and a Button would be the wrong element. It wears Button’s own variant, size and shape (buttonLook), so the two never drift. A link opens in a new tab, shows where it goes and reads as a link to assistive tech, which a button that sets the hash in its click handler does not. It has no disabled or loading state, because a link has neither.',
  props: [
    { name: 'children', description: 'The label (and an optional leading icon).' },
    { name: 'href', description: 'Where it goes: an in-app hash route ("#/dashboard?view=attention") or, with external, an off-app URL.' },
    { name: 'external', description: 'For off-app URLs — adds target=_blank + rel="noopener noreferrer". Omit for in-app hash links.' },
    { name: 'variant', description: "Button's variants, painted the same: 'primary' (default) for the page's main action, 'tonal' for a primary-tinted chip CTA in a row, 'secondary'/'ghost' quieter, 'ghost-accent' and 'danger' as Button draws them." },
    { name: 'size', description: "Button's height/padding tiers: 'xs' dense chrome, 'sm'/'md' most actions (md default), 'lg' hero." },
    { name: 'shape', description: "'pill' (default) or 'squircle', as Button." },
    { name: 'title', description: 'Native tooltip text.' },
    { name: 'ariaLabel', description: 'The accessible name, when the visible label alone does not say where the link goes. Omit it when the label does: a second string can drift from the one people read.' },
    { name: 'className', description: 'Extra layout (self-end, shrink-0) — tokens only, no raw hex/px. Not colour: the variant is the colour contract.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use ButtonLink when the action is a navigation that should look like a button — a row’s one way to act on what it shows, opened somewhere else.' },
    { guidance: false, description: 'Do not draw a navigation with Button and an onClick that sets location.hash: it can’t be opened in a new tab, shows no address, and reads as a button to assistive tech.' },
    { guidance: true, description: 'Reach for TextLink instead for a link inside running text, or a quiet standalone link; a page full of button-shaped links has no main action.' },
    { guidance: false, description: 'Do not pass a colour through className; pick the variant. Two colour utilities on one element resolve by stylesheet order, not by the order you wrote them.' },
  ],
  anatomy: ['<a href> wearing Button’s look (variant fill, size tier, pill or squircle corner)', 'label (children)'],
}

export default doc
