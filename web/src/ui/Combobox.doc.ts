import type { UiDoc } from './uiDoc'

// Doc object for Combobox — the searchable single-select. Authored: keywords,
// prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED
// from Combobox.tsx at build time.
const doc: UiDoc = {
  name: 'Combobox',
  keywords: ['combobox', 'autocomplete', 'select', 'search', 'dropdown', 'picker', 'filter', 'single-select', 'options'],
  description:
    'The searchable single-select for any "pick one from many" field (agents, models, …). Type to filter, arrow keys + Enter to pick; options optionally group by `group`. Redesign-v2: the field MORPHS into the menu as one continuous surface (container-transform) that grows in place and pushes siblings down, rather than mounting a separate menu below the trigger. Controlled via value + onChange.',
  props: [
    { name: 'options', description: 'The choices ({ value, label, group?, description? }); a `group` buckets rows under a first-seen-ordered sub-heading, a `description` renders a muted second line.' },
    { name: 'value', description: 'The selected option value (controlled); `\'\'` shows the placeholder. The X on the collapsed field clears it to `\'\'`.' },
    { name: 'onChange', description: 'Fires with the newly picked value (or `\'\'` when cleared). This is the only way the selection changes — the component is fully controlled.' },
    { name: 'placeholder', description: "Collapsed-field text when nothing is selected (default 'Select…')." },
    { name: 'emptyText', description: "Shown in the open list when the query matches no options (default 'No matches')." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Combobox for any filterable single-select rather than hand-rolling a <select> or a bespoke autocomplete — type-to-filter, keyboard nav, grouping, and the morph animation come built in.' },
    { guidance: true, description: 'Drive it controlled: pass value and update it in onChange; treat `\'\'` as the unselected state.' },
    { guidance: true, description: 'Set `group` on options to bucket a long list into labelled sections; add `description` for a muted second line where the label alone is ambiguous.' },
    { guidance: false, description: 'Do not hardcode colors or px in any wrapping className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    { guidance: false, description: 'Do not reach for Combobox for a short fixed set of mutually-exclusive options — use Segmented (inline) or Select (native) instead.' },
  ],
  anatomy: ['root div (outside-click / focus-leave boundary)', 'THE morphing surface (motion.div layout, field↔menu)', 'collapsed field row (value • clear X • chevron)', 'expanded search header (magnifier + input)', 'grouped option list (liquid layoutId active-row indicator)'],
}

export default doc
