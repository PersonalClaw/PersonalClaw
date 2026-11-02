import type { UiDoc } from './uiDoc'

// Doc object for NavRail. The shell-controlled collapse contract, the persisted
// drag-resize width, and the mobile overlay-drawer mode were all source comments —
// encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'NavRail',
  keywords: ['nav', 'navigation', 'rail', 'sidebar', 'menu', 'collapse', 'resize', 'drawer', 'mobile'],
  description:
    "The app's primary side navigation — a drag-resizable, width-persisted rail. Collapse is CONTROLLED by the shell (the collapse/expand toggle lives in the main area's top-left ShellCornerLeft, not on the rail); collapsed becomes an icon-only 64px rail. A shell of three parts: a top block (the header and the leading destinations) and a bottom block (the sectioned destinations, then the pinned rows), each keeping its own height, with the chat history between them as the one part that scrolls. On mobile it can render as a fixed overlay drawer instead of an in-flow column.",
  props: [
    { name: 'items', description: 'The NavItem list, rendered top→bottom; the leading unsectioned items hold the top block; from the first sectioned item on, every item is in the bottom block, and items flagged pinBottom (e.g. Settings) close it. Neither block scrolls.' },
    { name: 'home', description: "The destination the rail's icon and name open (Home): the header becomes that destination's link, first in the rail's order, named '<wordmark>, <label>' expanded and '<label>' collapsed, and lit (aria-current) while it is the page. Unset, the header only names the app." },
    { name: 'activeId', description: 'The currently-selected item id — drives the active pill, which moves to the new item at once.' },
    { name: 'onSelect', description: 'Fires with the item id when a nav item is clicked.' },
    { name: 'collapsed', description: 'Controlled collapse: true → icon-only 64px rail (drag-resize disabled). Own this state in the shell and drive it from ShellCornerLeft.' },
    { name: 'overlay', description: 'Mobile: render the rail as a fixed OVERLAY drawer (out of layout flow) instead of an in-flow column, so an expanded rail never squeezes the page. Always shows full labels.' },
    { name: 'overlayOpen', description: 'Overlay drawer expanded (slid in). When false the drawer is off-screen (translateX -100%) and no scrim shows.' },
    { name: 'onScrimClick', description: 'Tap the scrim behind the open overlay drawer → close it.' },
    { name: 'material', description: 'The desktop window\'s translucent sidebar material shows behind the rail (app/windowMaterial.ts, useWindowMaterial): the in-flow column is then clear so the material shows through it. The mobile overlay drawer floats over the page, so it keeps var(--color-rail) either way. Default false.' },
    { name: 'threads', description: "The chat history (a ThreadRail), between the top and the bottom block, in the expanded rail and the mobile drawer (the compact path to it): the one part of the rail that scrolls, keeping at least HISTORY_FLOOR of height. The collapsed icon rail shows none, and nothing between its blocks. Omit it and the rail renders only its items." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for NavRail for the app-level side navigation rather than hand-rolling — drag-resize, width persistence, the collapsed icon rail, and the active pill come built in.' },
    { guidance: true, description: 'Collapse is CONTROLLED — own the collapsed state in the shell and drive it from ShellCornerLeft; do not add a second collapse control on the rail itself.' },
    { guidance: true, description: 'On mobile, pass overlay + overlayOpen + onScrimClick so the rail becomes a drawer over a scrim that does not steal layout width.' },
    { guidance: true, description: 'Set pinBottom on a NavItem (e.g. Settings) to put it last in the bottom block, after the sectioned items.' },
    { guidance: false, description: 'Do not filter the rail\'s items to hide destinations: every destination shows for everyone, and a page the sidebar does not carry is reached from the CommandPalette and in-app links (docs/feature-map.md).' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['nav column (background var(--color-rail); clear over the desktop window material); it scrolls as one only when a window is too short for both blocks and the chat history\'s floor', 'the top block (data-rail-top): header (Wordmark expanded / Spark collapsed; with home, the button that is its link) and the leading items, never scrolling', 'the chat history (data-rail-scroll): the threads slot, the one part that scrolls; it keeps the focus ring\'s reach (RING_REACH) clear at its bottom, so a row the focus moves to is scrolled clear of the fold (expanded and drawer only; empty when collapsed)', 'the bottom block (data-rail-pinned): the sectioned items with their section headers and badges, then the pinned items, never scrolling', 'active pill (instant, by colour)', 'right-edge drag-resize handle (expanded)', 'mobile: scrim + fixed overlay drawer'],
}

export default doc
