import type { UiDoc } from './uiDoc'

// Doc object for SquareIconButton (Platform-Legibility §5). Authored: keywords,
// prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED
// from SquareIconButton.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'SquareIconButton',
  keywords: ['icon', 'button', 'square', 'dense', 'compact', 'toggle', 'danger', 'toolbar', 'list-row'],
  description:
    'The dense square icon button — the compact sibling of the round IconButton. A 28px (size-7) rounded-md hit area with a small glyph, for tight action clusters in list rows, card headers, and content toolbars where the 40px round pill is too large. Idle at ink-low; hover fills surface-high and brightens; the on (selected) state carries the coral tint, and tone="danger" gives a restrained red-on-hover destructive variant.',
  props: [
    { name: 'icon', description: 'Lucide icon component for the static-glyph form; sized by iconSize. Pass this OR children.' },
    { name: 'children', description: 'Glyph content for state-swapping cases (e.g. spinner⇄wifi, a rotating chevron); children size themselves. Pass this OR icon.' },
    { name: 'label', description: 'Accessible name (aria-label) — required; also the default tooltip.' },
    { name: 'title', description: 'Tooltip override — defaults to label. Use when the hover hint should differ from the accessible name (e.g. a disabled button explaining why it is gated).' },
    { name: 'onClick', description: 'Click handler; receives the mouse event. Suppressed while disabled.' },
    { name: 'on', description: 'Selected/toggled — carries the coral tint (primary bg-mix + text-primary) and announces aria-pressed. For a control that REVEALS adjacent content, pass ariaExpanded instead.' },
    { name: 'ariaExpanded', description: 'This button reveals adjacent content, and that content is currently shown — announces aria-expanded and carries the same coral tint as on. Same prop name Button and QuietButton use, deliberately. Passing it suppresses aria-pressed: a disclosure and a toggle answer different questions, and a control claiming both claims one of them falsely. Six of this primitive\u2019s ten state-bearing call sites are disclosures (a config form, a models list, two edit forms, an iteration rail, a menu); four are true toggles (pin, bookmark, word-wrap) and keep on.' },
    { name: 'disabled', description: 'Action currently unavailable: 40% opacity, not-allowed cursor, onClick suppressed — kept distinct from a busy state so it reads as inert rather than a dead-click.' },
    { name: 'disabledReason', description: 'WHY it is unavailable, when disabled is true; appended to the tooltip after an em dash. This button keeps its tab stop (disabled maps to aria-disabled, never the native attribute), so a keyboard user lands on it and would otherwise hear only the label — and being icon-only, it has no visible text to carry the reason either. Omit it when the gate is self-evident or transient; pass it only for the branch it describes when the gate is compound.' },
    { name: 'tone', description: "'neutral' (default) or 'danger'; danger tints the glyph red on hover with no fill (the restrained destructive treatment). Ignored while on." },
    { name: 'iconSize', description: 'Glyph size for the icon form (default 14); the children form sizes itself.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for SquareIconButton in dense action clusters (list rows, card headers, content toolbars) where the 40px round IconButton is too large.' },
    { guidance: true, description: 'Pass exactly one of icon or children — icon for a static glyph, children for a state-swapping glyph (spinner⇄wifi, rotating chevron).' },
    { guidance: true, description: 'Use tone="danger" for delete/remove actions and on for a selected/toggled state; always provide a label since there is no visible text.' },
    { guidance: true, description: 'Ask what the button DOES before choosing the state prop: if clicking it reveals adjacent content, it is a disclosure and wants ariaExpanded; if it flips a state that reveals nothing (pinned, saved, word-wrap), it wants on.' },
    { guidance: false, description: 'Do not pass on and ariaExpanded together — the primitive suppresses aria-pressed when ariaExpanded is present, and a control announcing both states announces one of them falsely.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    { guidance: false, description: 'Do not combine on with tone="danger" — a selected destructive button is not an app pattern, so danger is ignored while on.' },
  ],
  anatomy: ['motion.button (size-7 rounded-md, press spring)', 'icon glyph or children (state tint: idle ink-low / hover / on-or-expanded coral)'],
}

export default doc
