import type { UiDoc } from './uiDoc'

// Doc object for NavRail. The shell-controlled collapse contract, the persisted
// drag-resize width, and the mobile overlay-drawer mode were all source comments —
// encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'NavRail',
  keywords: ['nav', 'navigation', 'rail', 'sidebar', 'menu', 'collapse', 'resize', 'drawer', 'mobile'],
  description:
    "The app's primary side navigation — a drag-resizable, width-persisted rail. Collapse is CONTROLLED by the shell (the collapse/expand toggle lives in the main area's top-left ShellCornerLeft, not on the rail); collapsed becomes an icon-only 64px rail. On mobile it can render as a fixed overlay drawer instead of an in-flow column.",
  props: [
    { name: 'items', description: 'The NavItem list, rendered top→bottom; items flagged pinBottom (e.g. Settings) are pushed to the bottom past a flex spacer.' },
    { name: 'activeId', description: 'The currently-selected item id — drives the shared-layout active pill.' },
    { name: 'onSelect', description: 'Fires with the item id when a nav item is clicked.' },
    { name: 'collapsed', description: 'Controlled collapse: true → icon-only 64px rail (drag-resize disabled). Own this state in the shell and drive it from ShellCornerLeft.' },
    { name: 'overlay', description: 'Mobile: render the rail as a fixed OVERLAY drawer (out of layout flow) instead of an in-flow column, so an expanded rail never squeezes the page. Always shows full labels.' },
    { name: 'overlayOpen', description: 'Overlay drawer expanded (slid in). When false the drawer is off-screen (translateX -100%) and no scrim shows.' },
    { name: 'onScrimClick', description: 'Tap the scrim behind the open overlay drawer → close it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for NavRail for the app-level side navigation rather than hand-rolling — drag-resize, width persistence, the collapsed icon rail, and the sliding active pill come built in.' },
    { guidance: true, description: 'Collapse is CONTROLLED — own the collapsed state in the shell and drive it from ShellCornerLeft; do not add a second collapse control on the rail itself.' },
    { guidance: true, description: 'On mobile, pass overlay + overlayOpen + onScrimClick so the rail becomes a drawer over a scrim that does not steal layout width.' },
    { guidance: true, description: 'Set pinBottom on a NavItem (e.g. Settings) to pin it to the bottom of the rail instead of inline scroll order.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['nav column (background var(--color-rail))', 'header (Wordmark expanded / Spark collapsed)', 'scroll-order items (with section headers + badges)', 'flex spacer', 'bottom-pinned items', 'shared-layout active pill (layoutId slide)', 'right-edge drag-resize handle (expanded)', 'mobile: scrim + fixed overlay drawer'],
}

export default doc
