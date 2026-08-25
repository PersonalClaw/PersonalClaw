import type { UiDoc } from './uiDoc'

// Doc object for FilterRow — the one selectable filter row shared by FilterMenu's
// dropdown sections and the App Store's persistent category/source rail (PEP-3).
// Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop
// type/required are DERIVED from FilterRow.tsx at build time.
const doc: UiDoc = {
  name: 'FilterRow',
  keywords: ['filter', 'row', 'option', 'facet', 'rail', 'category', 'source', 'count', 'selected', 'toggle', 'aria-pressed'],
  description:
    'One selectable filter row — icon • label • count — with a sliding layoutId tint marking the selected one. Extracted so a filter dimension can be presented two ways without becoming two components: FilterMenu renders it as a dropdown section row, and the App Store rail renders it as a persistent toggle button, which is what makes "one control at two viewport widths" structural instead of a resemblance someone has to keep up. Being in ui/ also keeps a page from hand-rolling the same button, which the primitive-adoption ratchet counts as new bespoke chrome.',
  props: [
    { name: 'label', description: 'The row text. It is the accessible name — never add a visually-hidden span inside the row, which would append text the sighted user cannot see.' },
    { name: 'count', description: 'A tally rendered right-aligned, and only when > 0: a bare "0" beside a label reads as a broken count rather than an empty facet.' },
    { name: 'icon', description: 'Optional leading lucide glyph; tinted to primary while selected and to on-surface-var otherwise.' },
    { name: 'selected', description: 'Whether this row is the chosen one — drives the tint, the weight bump and the icon/label color.' },
    { name: 'indicatorId', description: 'layoutId for the shared selected-row indicator: ONE per group, so the tint glides row to row inside that group instead of blink-swapping. Omit for a lone row.' },
    { name: 'onClick', description: 'Select this row.' },
    { name: 'pressed', description: 'Opt-in aria-pressed, for a row that is a toggle button rather than a popover row. Set it wherever the tint is the ONLY selection signal — a background color is invisible to a screen-reader user. Leave it undefined inside a popover whose trailing check already announces the choice.' },
    { name: 'trailing', description: 'Trailing adornment, e.g. the dropdown\'s check mark.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render FilterRow for any "pick one of these facets" row rather than laying out an icon/label/count button by hand — it carries the tint, the weight bump, the count treatment and the press affordance.' },
    { guidance: true, description: 'Give every row in one group the same indicatorId so the selected tint slides between them; use a different id per group.' },
    { guidance: true, description: 'Set pressed whenever the row is a standalone toggle (a persistent rail), so the selection reaches the accessibility tree and not only the pixels.' },
    { guidance: false, description: 'Do not use pressed together with a container role="listbox" — an option announces selection via aria-selected, and a row cannot honestly be both.' },
    { guidance: false, description: 'Do not add an arrow-key cursor over a list of these without also declaring a container role and a roving tabindex; a cursor over independently-tabbable buttons is neither APG pattern.' },
    { guidance: false, description: 'Do not hardcode colors or px — the active tint is a color-mix over --color-primary and every surface routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['motion.button (h-8, rounded-md, whileTap)', 'layoutId selected-indicator (12% primary tint)', 'leading icon (optional)', 'truncating label', 'tabular-nums count (when > 0)', 'trailing slot'],
}

export default doc
