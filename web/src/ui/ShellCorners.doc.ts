import type { UiDoc } from './uiDoc'

// ShellCorners.tsx exports the two persistent app-shell corner regions, so its doc
// default-exports an array. The CSS-var publication contract (--shell-corner-*) that
// page TopBars pad against was a source comment — encoded here.
const docs: UiDoc[] = [
  {
    name: 'ShellCornerLeft',
    keywords: ['shell', 'corner', 'nav', 'collapse', 'toggle', 'sidebar', 'chrome', 'pull-tab'],
    description:
      "The app shell's LEFT corner — a pull-tab hugging the nav rail's top-right edge that carries the nav collapse/expand toggle, floating above page content. Publishes its measured width to the CSS var `--shell-corner-l` so page TopBars pad to clear it.",
    props: [
      { name: 'collapsed', description: 'Current rail collapsed state — chooses the glyph (PanelLeftOpen vs PanelLeftClose) and the aria/title copy.' },
      { name: 'onToggle', description: 'Fires to flip the rail between collapsed and expanded.' },
    ],
    bestPractices: [
      { guidance: true, description: "This is the single home for the nav collapse toggle — NavRail is collapse-CONTROLLED, so drive NavRail's `collapsed` from the same state you pass here." },
      { guidance: false, description: 'Do not drop or hardcode the `--shell-corner-l` width it publishes — page TopBars reserve exactly that much left padding via that var to avoid sliding under the tab.' },
    ],
    anatomy: ['absolutely-positioned pull-tab (flush left, rounded right edge, attached to the rail)', 'toggle button', 'AnimatePresence glyph morph (PanelLeftClose / PanelLeftOpen)'],
  },
  {
    name: 'ShellCornerRight',
    keywords: ['shell', 'corner', 'terminal', 'theme', 'notifications', 'width', 'system', 'chrome'],
    description:
      "The app shell's RIGHT corner — the screen's top-right control cluster (terminal-drawer toggle • content-width pill • notification bell • theme • system health dot) rendered as native shell chrome above any page header. Publishes its measured width to `--shell-corner-r` and height to `--shell-corner-rh`.",
    props: [
      { name: 'terminalOpen', description: 'Whether the terminal drawer is open — drives the toggle glyph highlight + aria/title copy.' },
      { name: 'onToggleTerminal', description: 'Fires to open/close the terminal drawer (⌘`).' },
      { name: 'navigate', description: 'Router navigation function, passed straight through to the embedded NotificationBell.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for this as the app-level native control cluster (terminal / width / notifications / theme / system) — do not scatter these controls into individual page TopBars.' },
      { guidance: false, description: 'Do not drop or hardcode the `--shell-corner-r` / `--shell-corner-rh` vars it publishes — TopBar pads its right edge and SidePanel starts below the corner band using them, so overriding them causes overlap.' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['absolutely-positioned top-right cluster (rounded inner corner, backdrop-blur)', 'terminal toggle', 'WidthPill (desktop only — dropped on mobile)', 'NotificationBell', 'ThemeControl', 'SystemWidget connectivity dot'],
  },
]

export default docs
