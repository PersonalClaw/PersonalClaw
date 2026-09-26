import type { UiDoc } from './uiDoc'

// TopBar.tsx exports the page top bar plus the ThemeControl that lives in the shell
// corner, so its doc default-exports an array. The shell-corner clearance contract
// and the keepCornerPadding / contentAligned rules were all source comments.
const docs: UiDoc[] = [
  {
    name: 'TopBar',
    keywords: ['topbar', 'header', 'page', 'title', 'actions', 'chrome', 'corner', 'shell'],
    description:
      "The app top bar — sparse chrome with a left slot for context (model pill / page title), a right slot for actions, and an optional context line below. It pads BOTH ends to clear the floating shell corners (collapse toggle left, control cluster right) so its row lays out only in the space between them and never slides under either — and when that band is too narrow for the row (a phone), its HeaderActions cluster moves the row below both corners, across the full width. Theme + width controls are NOT here — they live in the shell corners.",
    props: [
      { name: 'left', description: 'Left slot for context (model pill / page title / breadcrumb). Flexes and truncates so the actions never crush it.' },
      { name: 'right', description: 'Right slot for actions. Content-sized (shrink-0) so a wide action set keeps its full size.' },
      { name: 'below', description: 'A context line under the row: chips that describe the page (who started it, where it came from, what it cost). It wraps onto more lines rather than competing with the actions for the row. Omit it (undefined) when there is nothing to say, so the bar keeps its single row.' },
      { name: 'keepCornerPadding', description: 'Keep the right corner padding even when a docked panel is open. Set on pages where a SidePanel docks BELOW this bar (e.g. the loop cockpit) so the actions still clear the floating shell corner instead of sliding under it.' },
      { name: 'contentAligned', description: 'Center the header inner row to `--content-width` (the SAME column the body uses) so a header carrying body-level controls lines up with the content below and tracks the width toggle; corner gaps are kept as MIN padding so it still clears the shell corners at the "full" preset.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for TopBar for any page header rather than hand-rolling one — it owns the shell-corner clearance so actions never slide under the floating corners.' },
      { guidance: true, description: 'On a WorkbenchLayout page whose SidePanel docks below the bar, set keepCornerPadding so the right actions keep clearing the shell corner.' },
      { guidance: true, description: 'Set contentAligned when the header carries body-level controls (breadcrumb + title/actions) so they line up with the content column and track the content-width toggle.' },
      { guidance: true, description: 'Put facts about the page in `below`, not beside the title: a chip in the row cannot shrink, so it overlaps the actions or starves the title, while a chip below can only wrap. Truncate a long chip and keep its full text in its title and accessible name.' },
      { guidance: false, description: 'Do not put theme/width controls in the TopBar (they belong in the shell corners) or list search/filter/sort here (put those in a WorkbenchLayout `controls` bar).' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['header (both-ends shell-corner padding; stacked: below the corners, page-edge padding)', 'band probe (data-top-bar-band: the width between the corners)', 'row (h-14)', 'left slot (flex + truncate, data-header-left)', 'right slot (shrink-0 actions)', 'context line (data-header-below, wraps)', 'contentAligned variant: inner row centered to --content-width'],
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
