import type { UiDoc } from './uiDoc'

// Doc object for WorkbenchLayout — the standard list-with-detail page skeleton.
const doc: UiDoc = {
  name: 'WorkbenchLayout',
  keywords: ['layout', 'workbench', 'list', 'detail', 'page', 'skeleton', 'scaffold', 'topbar', 'panel', 'scroll'],
  description:
    'The standard list-with-detail page skeleton. A full-width TopBar stays pinned on top; the scrollable body and an optional right-docked SidePanel sit in a row BELOW it. The panel pushes only the body, never the header — so opening it can never shove the header actions off the right edge.',
  props: [
    { name: 'topBar', description: "The page's <TopBar keepCornerPadding …>; keep the corner padding so its actions clear the floating shell corner." },
    { name: 'controls', description: 'Optional pinned controls bar (search / filter / sort) rendered between the TopBar and the scrolling body — list controls that stay visible as the list scrolls. Pass a <ListControls>.' },
    { name: 'panel', description: 'Already gated + wrapped detail panel, e.g. `open && <SidePanel fillHeight … />`. Docks below the TopBar.' },
    { name: 'scroll', description: 'When true (default) the layout owns the body vertical scroll; set false for a body that manages its own height (e.g. a Kanban shell).' },
    { name: 'children', description: 'The centered page content (the list/board).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pair with a fillHeight SidePanel as the `panel` prop — it docks below the TopBar, so without fillHeight it stops short of the viewport bottom.' },
    { guidance: true, description: 'Gate the panel node yourself (`open && <SidePanel …/>`) and pass it as `panel`; the layout wraps it in AnimatePresence for enter/exit.' },
    { guidance: true, description: 'Put list search/filter/sort in `controls` (a <ListControls>), not in the TopBar — they belong to the page and should scroll-pin, not crowd the header.' },
    { guidance: false, description: 'Do not set scroll for a body that owns its own scroll region (Kanban columns) — pass scroll={false} instead, or you get nested scrollbars.' },
  ],
  anatomy: ['pinned TopBar', 'row below: [ optional ListControls + scrolling body ] + [ AnimatePresence-wrapped SidePanel ]'],
}

export default doc
