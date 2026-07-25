import type { UiDoc } from './uiDoc'

// Doc object for FilterMenu — the canonical "Filter & sort" header control.
// Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop
// type/required are DERIVED from FilterMenu.tsx at build time.
const doc: UiDoc = {
  name: 'FilterMenu',
  keywords: ['filter', 'sort', 'menu', 'popover', 'scope', 'status', 'sections', 'facets', 'badge', 'header'],
  description:
    'The canonical header "Filter & sort" control: one 40px pill that opens a Popover holding every list criterion (scope / status / sort / …) as titled single-select sections. A count badge on the trigger reports how many sections hold a non-default value, so an active filter is obvious without opening it. Replaces the old pattern of lining up a filter Segmented + a sort <select> + a scope dropdown across the header — collapsing N competing widgets into one, consistent across every page.',
  props: [
    { name: 'sections', description: 'The titled single-select groups ({ title, value, defaultKey, options, onChange }). Each section owns its selection; a value !== defaultKey counts toward the badge and shows an inline Clear.' },
    { name: 'label', description: "Trigger text beside the sliders icon (default 'Filter'); hidden below the sm breakpoint, leaving the icon + badge." },
    { name: 'align', description: "Which trigger edge the popover aligns to, 'left' or 'right' (default 'right' for a right-of-header control)." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for FilterMenu for all list criteria instead of scattering a filter Segmented, a sort <select>, and a scope dropdown across the header — one pill, one active-count badge, consistent everywhere.' },
    { guidance: true, description: "Set each section's defaultKey to the option that means \"not filtering\" — that's what the active-count badge and the inline Clear measure against." },
    { guidance: true, description: 'Model each criterion as its own section with its own onChange; the menu handles the sliding selected-row indicator, counts, and Clear per section.' },
    { guidance: true, description: "Use option.groupLabel to draw a sub-heading + divider within a section (e.g. presets vs projects); set option.count to show a tally and option.icon for a leading glyph." },
    { guidance: false, description: 'Do not hardcode colors or px — the active-tint color-mix and every surface route through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['Popover (bottom placement)', 'trigger pill (sliders icon + label + spring-pop active-count badge)', 'per-section blocks (uppercase title + inline Clear)', 'option rows (liquid layoutId selected-indicator, icon • label • count • check)', 'Done button (ghost)'],
}

export default doc
