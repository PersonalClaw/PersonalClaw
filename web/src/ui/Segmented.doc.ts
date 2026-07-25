import type { UiDoc } from './uiDoc'

// Doc object for Segmented — the one canonical segmented single-select. Authored:
// keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required
// are DERIVED from Segmented.tsx at build time.
const doc: UiDoc = {
  name: 'Segmented',
  keywords: ['segmented', 'toggle', 'single-select', 'tabs', 'switch', 'filter', 'mode', 'view', 'pill', 'group'],
  description:
    'The ONE canonical segmented single-select for the whole app — a slider-style pill group for every "pick one of N" choice (filters, view switches, mode toggles, tab strips) so they look identical everywhere. The active fill is a shared-layout pill that SLIDES + squishes between options (liquid indicator) rather than each button toggling its own background. Inner height h-8 lines up with Button size="sm"; iconOnly renders square icon buttons. Controlled via value + onChange.',
  props: [
    { name: 'options', description: 'The choices ({ key, label?, tone?, icon?, title? }). A `tone` colors the selected fill semantically (e.g. task status); an `icon` shows a leading glyph.' },
    { name: 'value', description: 'The selected option key (controlled).' },
    { name: 'onChange', description: 'Fires with the newly selected key — on click and on WAI-ARIA arrow/Home/End keyboard nav.' },
    { name: 'iconOnly', description: 'Render square icon buttons (a compact view-switch) instead of icon + label. Requires each option to carry an icon.' },
    { name: 'ariaLabel', description: 'Accessible name for the role="tablist" group.' },
    { name: 'disabled', description: 'Dim + block interaction on the whole group.' },
    { name: 'size', description: "'md' (default) or 'sm' — a compact, low-key strip (shorter, smaller text, muted surface) for inconspicuous secondary controls." },
    { name: 'collapse', description: "Responsive overflow behavior: unset (default) always the inline strip, no measuring; 'scroll' keeps the strip and scrolls it horizontally; 'menu' collapses below the fit threshold to one pill that opens the options in a Popover." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Segmented for every mutually-exclusive "pick one of N" choice rather than hand-rolling toggle buttons — the sliding liquid indicator and roving-tabindex keyboard nav come built in and keep every such control identical.' },
    { guidance: true, description: 'Drive it controlled: pass value and set it in onChange.' },
    { guidance: true, description: "Set collapse='menu' when the strip may outgrow a tight header row — it swaps to a single Popover pill below the fit threshold and re-expands when space returns (no one-way latch)." },
    { guidance: true, description: "Give options a `tone` only for semantic coloring (e.g. status); otherwise the default solid primary fill is the high-contrast choice." },
    { guidance: false, description: 'Do not hardcode colors or px — tones use color-mix over tokens and sizes route through the scale (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['role="tablist" strip (rounded-pill track)', 'per-option motion.button (role="tab", press-scale)', 'liquid active fill (shared layoutId, slides + squishes)', 'off-flow probe + CollapsedSegmented pill → Popover of MenuRows (collapse="menu")'],
}

export default doc
