import type { UiDoc } from './uiDoc'

// Doc object for Segmented — the one canonical segmented single-select. Authored:
// keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required
// are DERIVED from Segmented.tsx at build time.
const doc: UiDoc = {
  name: 'Segmented',
  keywords: ['segmented', 'toggle', 'single-select', 'tabs', 'switch', 'filter', 'mode', 'view', 'pill', 'group'],
  description:
    'The ONE canonical segmented single-select for the whole app — a slider-style pill group for every "pick one of N" choice (filters, view switches, mode toggles, tab strips) so they look identical everywhere. The active fill moves to the picked option at once, by colour: a tab or option change is frequent, so it neither slides between options nor scales on press. Inner height h-8 lines up with Button size="sm"; iconOnly renders square icon buttons. Controlled via value + onChange.',
  props: [
    { name: 'options', description: 'The choices ({ key, label?, tone?, icon?, title? }). A `tone` colors the selected fill semantically (e.g. task status); an `icon` shows a leading glyph.' },
    { name: 'value', description: 'The selected option key (controlled).' },
    { name: 'onChange', description: 'Fires with the newly selected key — on click and on WAI-ARIA arrow/Home/End keyboard nav.' },
    { name: 'iconOnly', description: 'Render square icon buttons (a compact view-switch) instead of icon + label. Requires each option to carry an icon.' },
    { name: 'ariaLabel', description: 'Accessible name for the role="radiogroup". Omit it inside a Field — the group claims the Field\'s visible label automatically; an explicit ariaLabel wins.' },
    { name: 'disabled', description: 'Dim + block interaction on the whole group.' },
    { name: 'size', description: "'md' (default) or 'sm' — a compact, low-key strip (shorter, smaller text, muted surface) for inconspicuous secondary controls." },
    { name: 'collapse', description: "Responsive overflow behavior: unset (default) always the inline strip, no measuring; 'scroll' keeps the strip and scrolls it horizontally; 'menu' collapses below the fit threshold to one pill that opens the options in a Popover; 'wrap' lets the options wrap onto further rows inside the container so every one stays visible (a form field in a narrow panel)." },
    { name: 'collapsedClassName', description: "Extra classes on the folded pill (collapse='menu'), e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) the shell's destination tabs give it. The strip's own options keep their size." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Segmented for every mutually-exclusive "pick one of N" choice rather than hand-rolling toggle buttons — the sliding liquid indicator and roving-tabindex keyboard nav come built in and keep every such control identical.' },
    { guidance: false, description: 'Do not use it for a strip that reveals a PANEL. It announces itself as a radiogroup, which is the truth for a field whose value is one of N and nothing is revealed. A real tabbed interface needs role="tab" with an aria-controls\'d role="tabpanel" — hand-roll that, as ChatActivityPanel does.' },
    { guidance: true, description: 'Drive it controlled: pass value and set it in onChange.' },
    { guidance: true, description: "Set collapse='menu' when the strip may outgrow a tight header row — it swaps to a single Popover pill below the fit threshold and re-expands when space returns (no one-way latch)." },
    { guidance: true, description: "Set collapse='wrap' for a FORM field that can land in a narrow panel (the task form's Status and Priority in the 420px side panel): the options wrap onto a second row instead of spilling past the panel and scrolling the whole form sideways." },
    { guidance: true, description: "Give options a `tone` only for semantic coloring (e.g. status); otherwise the default solid primary fill is the high-contrast choice." },
    { guidance: false, description: 'Do not hardcode colors or px — tones use color-mix over tokens and sizes route through the scale (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['role="radiogroup" strip (rounded-pill track)', 'per-option button (role="radio" + aria-checked)', 'active fill (instant, by colour)', 'off-flow probe + CollapsedSegmented pill → Popover of MenuRows (collapse="menu")', 'flex-wrap track with a one-row radius (collapse="wrap")'],
}

export default doc
