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
    { name: 'items', description: 'The NavItem list, rendered top→bottom; items flagged pinBottom (e.g. Settings) sit in the foot of the rail, under its scroll area, and never scroll.' },
    { name: 'activeId', description: 'The currently-selected item id — drives the active pill, which moves to the new item at once.' },
    { name: 'onSelect', description: 'Fires with the item id when a nav item is clicked.' },
    { name: 'collapsed', description: 'Controlled collapse: true → icon-only 64px rail (drag-resize disabled). Own this state in the shell and drive it from ShellCornerLeft.' },
    { name: 'overlay', description: 'Mobile: render the rail as a fixed OVERLAY drawer (out of layout flow) instead of an in-flow column, so an expanded rail never squeezes the page. Always shows full labels.' },
    { name: 'overlayOpen', description: 'Overlay drawer expanded (slid in). When false the drawer is off-screen (translateX -100%) and no scrim shows.' },
    { name: 'onScrimClick', description: 'Tap the scrim behind the open overlay drawer → close it.' },
    { name: 'material', description: 'The desktop window\'s translucent sidebar material shows behind the rail (app/windowMaterial.ts, useWindowMaterial): the in-flow column is then clear so the material shows through it. The mobile overlay drawer floats over the page, so it keeps var(--color-rail) either way. Default false.' },
    { name: 'threads', description: "The projects with their threads (a ThreadRail), rendered after the leading unsectioned items and before the first sectioned one, in the expanded rail and the mobile drawer (the compact path to them). The collapsed icon rail shows none. Omit it and the rail renders only its items." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for NavRail for the app-level side navigation rather than hand-rolling — drag-resize, width persistence, the collapsed icon rail, and the active pill come built in.' },
    { guidance: true, description: 'Collapse is CONTROLLED — own the collapsed state in the shell and drive it from ShellCornerLeft; do not add a second collapse control on the rail itself.' },
    { guidance: true, description: 'On mobile, pass overlay + overlayOpen + onScrimClick so the rail becomes a drawer over a scrim that does not steal layout width.' },
    { guidance: true, description: 'Set pinBottom on a NavItem (e.g. Settings) to pin it to the foot of the rail, out of the scroll area, instead of inline scroll order.' },
    { guidance: false, description: 'Do not filter the rail\'s items to hide destinations: every destination shows for everyone, and a page the sidebar does not carry is reached from the CommandPalette and in-app links (docs/feature-map.md).' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['nav column (background var(--color-rail); clear over the desktop window material)', 'header (Wordmark expanded / Spark collapsed)', 'scroll-order items (with section headers + badges)', 'the threads slot, after the leading unsectioned items (expanded and drawer only)', 'the scroll area (data-rail-scroll): the header, the items and the threads; it keeps the focus ring\'s reach (RING_REACH) clear at its bottom, so a row the focus moves to is scrolled clear of the fold', 'the foot (data-rail-pinned): the bottom-pinned items, under the scroll area, never scrolling', 'active pill (instant, by colour)', 'right-edge drag-resize handle (expanded)', 'mobile: scrim + fixed overlay drawer'],
}

export default doc
