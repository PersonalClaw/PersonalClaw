import type { UiDoc } from './uiDoc'

// Doc object for Button (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// Button.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Button',
  keywords: ['button', 'cta', 'action', 'submit', 'primary', 'danger', 'loading', 'pill'],
  description:
    'The one shared button for every clickable action. Five variants (primary / tonal / secondary / ghost / danger) across four sizes, pill-shaped by default with an optional squircle corner. Physical press/hover springs and a loading state that swaps the label for a centered spinner without changing width.',
  props: [
    { name: 'children', description: 'The button label (and optional leading icon).' },
    { name: 'variant', description: "Visual emphasis. 'primary' is the page's main action; 'tonal' is a primary-tinted chip CTA; 'secondary'/'ghost' are quieter; 'danger' is destructive." },
    { name: 'size', description: "Height/padding tier. 'xs' for dense in-panel chrome, 'sm'/'md' for most actions, 'lg' for hero CTAs." },
    { name: 'shape', description: "'pill' (default) or 'squircle' for the superellipse corner." },
    { name: 'loading', description: 'Cross-fades the label out for a centered spinner while preserving width — use for in-flight async actions.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
    { name: 'onClick', description: 'Click handler; receives the mouse event.' },
    { name: 'disabled', description: 'Dim + block interaction. `loading` also disables.' },
    { name: 'type', description: "'button' (default) or 'submit' inside a form." },
    { name: 'title', description: 'Native tooltip text.' },
    { name: 'ariaExpanded', description: 'Announce disclosure state to assistive tech — set only when the button folds content (e.g. a "Show N more" archive toggle); omit for plain actions.' },
    { name: 'ariaPressed', description: 'Announce selected state — set when the button is a persistent choice (a selectable list row, a view toggle); omit for plain actions. Use instead of ariaExpanded when the button selects rather than reveals.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Button for any labelled action — never hand-roll a <button> with bespoke classes; the missing xs tier is why pages used to.' },
    { guidance: true, description: "Use variant='danger' for destructive actions and variant='primary' for exactly one main action per view." },
    { guidance: true, description: 'Set `loading` for async actions so the width stays stable and the user sees progress.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    { guidance: false, description: 'Do not use Button for icon-only affordances — use IconButton (or SquareIconButton) instead.' },
  ],
  anatomy: ['motion.button (press/hover spring)', 'pointer-tracked sheen (solid variants, bold intensity)', 'label span (cross-fades under spinner)', 'loading spinner overlay'],
}

export default doc
