import type { UiDoc } from './uiDoc'

// Doc object for FilterMenu — the "Filter & sort" pill the list controls bars carried.
// Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop
// type/required are DERIVED from FilterMenu.tsx at build time.
const doc: UiDoc = {
  name: 'FilterMenu',
  keywords: ['filter', 'sort', 'menu', 'popover', 'scope', 'status', 'sections', 'facets', 'badge', 'header'],
  description:
    'The canonical header "Filter & sort" control: one 40px pill that opens a Popover holding every list criterion (scope / status / sort / …) as titled single-select sections, or at size="sm" a 32 px icon button with its edge and a dot while a section is set. A count badge on the pill trigger reports how many sections hold a non-default value, so an active filter is obvious without opening it. A section with a limit shows that many choices, the chosen ones first and then the largest, and a "Show all N <limitNoun>" row that opens the rest in place, the focus on its first choice. Replaces the old pattern of lining up a filter Segmented + a sort <select> + a scope dropdown across the header — collapsing N competing widgets into one, consistent across every page.',
  props: [
    { name: 'sections', description: 'The titled single-select groups ({ title, value, defaultKey, options, onChange }). Each section owns its selection; a `limit` shows that many choices (the chosen ones, then the largest) until its \"Show all N <limitNoun>\" row is pressed; a value !== defaultKey counts toward the badge and shows an inline Clear.' },
    { name: 'label', description: "Trigger text beside the sliders icon (default 'Filter'); hidden below the sm breakpoint, leaving the icon + badge." },
    { name: 'align', description: "Which trigger edge the popover aligns to, 'left' or 'right' (default 'right' for a right-of-header control)." },
    { name: 'className', description: "Extra classes on the trigger, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) where a page's controls carry it." },
    { name: 'size', description: "'md' (the default: the 40 px pill with its label and count badge) or 'sm': the sliders icon alone, 32 px, on the container with its 1 px edge, and a primary dot while a section is set, for a list's own toolbar (the chat list's search row). At 'sm', `label` is the trigger's whole name and tooltip and should say what is set (headerFilterLabel: \"Sort and filter: Recent activity · Active\")." },
  ],
  bestPractices: [
    { guidance: false, description: "Do not draw it on a page: a page's choice sets are the sections of its header's HeaderFilter, whose label says what is set (app/pageControlsAreInTheHeader.test.ts)." },
    { guidance: true, description: "Set each section's defaultKey to the option that means \"not filtering\" — that's what the active-count badge and the inline Clear measure against." },
    { guidance: true, description: "Use option.groupLabel to draw a sub-heading + divider within a section (e.g. presets vs projects); set option.count to show a tally and option.icon for a leading glyph." },
    { guidance: false, description: 'Do not hardcode colors or px — the active-tint color-mix and every surface route through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['Popover (bottom placement)', 'trigger pill (sliders icon + label + active-count badge, swapped in place), or at size="sm" the 32 px icon button (sliders icon, 1 px edge, set dot)', 'per-section blocks (uppercase title + inline Clear)', 'option rows (selected tint, icon • label • count • check)', 'a "Show all N <limitNoun>" row (a section with a limit)', 'Done button (ghost)'],
}

export default doc
