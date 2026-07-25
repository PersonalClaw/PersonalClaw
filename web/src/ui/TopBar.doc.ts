import type { UiDoc } from './uiDoc'

// TopBar.tsx exports the page top bar plus the ThemeControl that lives in the shell
// corner, so its doc default-exports an array. The shell-corner clearance contract
// and the keepCornerPadding / contentAligned rules were all source comments.
const docs: UiDoc[] = [
  {
    name: 'TopBar',
    keywords: ['topbar', 'header', 'page', 'title', 'actions', 'chrome', 'corner', 'shell'],
    description:
      "The app top bar — sparse chrome with a left slot for context (model pill / page title) and a right slot for actions. It pads BOTH ends to clear the floating shell corners (collapse toggle left, control cluster right) so its content lays out only in the space between them and never slides under either. Theme + width controls are NOT here — they live in the shell corners.",
    props: [
      { name: 'left', description: 'Left slot for context (model pill / page title / breadcrumb). Flexes and truncates so the actions never crush it.' },
      { name: 'right', description: 'Right slot for actions. Content-sized (shrink-0) so a wide action set keeps its full size.' },
      { name: 'keepCornerPadding', description: 'Keep the right corner padding even when a docked panel is open. Set on pages where a SidePanel docks BELOW this bar (e.g. the loop cockpit) so the actions still clear the floating shell corner instead of sliding under it.' },
      { name: 'contentAligned', description: 'Center the header inner row to `--content-width` (the SAME column the body uses) so a header carrying body-level controls lines up with the content below and tracks the width toggle; corner gaps are kept as MIN padding so it still clears the shell corners at the "full" preset.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for TopBar for any page header rather than hand-rolling one — it owns the shell-corner clearance so actions never slide under the floating corners.' },
      { guidance: true, description: 'On a WorkbenchLayout page whose SidePanel docks below the bar, set keepCornerPadding so the right actions keep clearing the shell corner.' },
      { guidance: true, description: 'Set contentAligned when the header carries body-level controls (breadcrumb + title/actions) so they line up with the content column and track the content-width toggle.' },
      { guidance: false, description: 'Do not put theme/width controls in the TopBar (they belong in the shell corners) or list search/filter/sort here (put those in a WorkbenchLayout `controls` bar).' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['header row (h-14, both-ends shell-corner padding)', 'left slot (flex + truncate, data-header-left)', 'right slot (shrink-0 actions)', 'contentAligned variant: inner row centered to --content-width'],
  },
  {
    name: 'ThemeControl',
    keywords: ['theme', 'dark', 'light', 'system', 'toggle', 'appearance', 'mode', 'shell'],
    description:
      'Cycles the theme dark → light → system (follow OS). The icon reflects the chosen preference (Moon / Sun / Monitor) and the tooltip names the next state. Rendered in the shell corner cluster; takes no props (reads/writes the theme store).',
    props: [],
    bestPractices: [
      { guidance: true, description: 'Reach for ThemeControl in the shell corner cluster rather than building a bespoke theme toggle — it owns the dark→light→system cycle and the theme store.' },
      { guidance: false, description: "Do not duplicate it per page — it's shell chrome, mounted once in ShellCornerRight." },
    ],
    anatomy: ['IconButton (mode-reflecting icon, next-state tooltip)'],
  },
]

export default docs
