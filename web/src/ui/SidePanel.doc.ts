import type { UiDoc } from './uiDoc'

// Doc object for SidePanel. The three-mode contract, the urlKey close-clears-the-key
// standard, and the fillHeight below-TopBar rule were all source comments — encoded
// here as machine-readable data.
const doc: UiDoc = {
  name: 'SidePanel',
  keywords: ['panel', 'side', 'drawer', 'detail', 'inspector', 'docked', 'expand', 'rail', 'overlay', 'right'],
  description:
    'The one right-docked side panel used across pages for detail/inspector views. Three modes from two controls: docked (in-flow, pushes the main content narrower, left edge drags to resize), expanded (one button unfurls it to a full-viewport overlay via a right-anchored clip wipe, or delegates to a dedicated full page via onExpand), and close. The header (title + expand + close) is pinned; only the body scrolls.',
  props: [
    { name: 'title', description: 'Panel heading, shown in the pinned header.' },
    { name: 'icon', description: 'Optional leading icon beside the title.' },
    { name: 'onClose', description: 'Called when the panel dismisses (X, Escape, or parent). Required.' },
    { name: 'urlKey', description: "Optional { key, setQuery } URL-sync: closing the panel drops `?key` from the URL, so a page that opens via `?open=<id>` gets the close-clears-the-key half for free." },
    { name: 'storeKey', description: "localStorage key the docked width persists under (default 'sidepanel-w'); give co-existing panels distinct keys." },
    { name: 'fillHeight', description: 'Set when docking BELOW a page TopBar — skips the shell-corner top offset so the panel reaches the viewport bottom instead of double-counting it.' },
    { name: 'onExpand', description: 'When the content has a dedicated full-page home, pass navigation here and expand goes THERE instead of unfurling the in-place overlay.' },
    { name: 'children', description: 'The scrolling panel body.' },
  ],
  bestPractices: [
    { guidance: true, description: 'When URL-bound, pass urlKey — it standardizes the "Back/Escape closes the panel and clears its query key" contract so the URL and open state can never diverge.' },
    { guidance: true, description: 'Inside a WorkbenchLayout (docked below a TopBar), always set fillHeight — otherwise the panel stops short of the viewport bottom.' },
    { guidance: true, description: 'Reach for SidePanel for any right-edge detail/inspector rail rather than hand-rolling a drawer — resize, width persistence, expand/collapse, and Escape-to-close come built in.' },
    { guidance: false, description: 'Do not manage the panel width yourself — pass a distinct storeKey and let the drag handle persist it.' },
    { guidance: false, description: 'Do not omit onExpand when the content has a dedicated page — the in-place overlay then shadows a better full-page home.' },
  ],
  anatomy: ['motion.div docked column (left-edge drag-resize handle)', 'pinned header (title • expand/collapse • close)', 'scrolling body', 'full-viewport overlay (portaled to <body>, right-anchored clip wipe) when expanded'],
}

export default doc
