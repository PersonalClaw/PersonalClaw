import type { UiDoc } from './uiDoc'

// Popover.tsx exports two components (MenuRow + Popover), so its doc default-
// exports an array — one UiDoc per exported component. The portal / openSignal /
// single-layer-Escape contracts that were source comments become bestPractices.
const docs: UiDoc[] = [
  {
    name: 'MenuRow',
    keywords: ['menu', 'row', 'item', 'option', 'list', 'popover', 'selected'],
    description:
      'A single row inside a popover menu/list — a left-aligned icon + label (+ optional hint) that presses on tap and nudges its icon on hover. The shared building block for options rendered inside a Popover (e.g. a collapsed Segmented menu); restrained motion because menus are dense.',
    props: [
      { name: 'icon', description: 'Optional leading glyph; shifts right slightly on row hover.' },
      { name: 'label', description: 'The row text (bumps to a heavier weight when selected).' },
      { name: 'hint', description: 'Optional muted second line beneath the label.' },
      { name: 'selected', description: 'Marks the row as the current choice — heavier label weight + a trailing primary dot.' },
      { name: 'onClick', description: 'Activation handler for the row.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use MenuRow for options inside a Popover rather than styling a raw <button> — the press spring, hover icon-nudge, and selected treatment stay consistent across every menu.' },
      { guidance: false, description: 'Do not hardcode colors or px — tone, surface, and radius route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['motion.button (press spring, group hover)', 'leading icon span (hover-nudge)', 'label + optional hint', 'trailing selected dot'],
  },
  {
    name: 'Popover',
    keywords: ['popover', 'flyout', 'menu', 'anchored', 'dropdown', 'portal', 'overlay', 'trigger'],
    description:
      'The anchored flyout — a glass surface with a spring entrance that a trigger toggles open. Opens ABOVE the trigger by default (composers sit low); pass placement="bottom" for a top-anchored control. Restores focus to the trigger on Escape/selection, and consumes the Escape keydown so it closes a single layer (not also a parent SidePanel). Pass `portal` when the trigger lives inside an overflow-clipping/scrolling container so the flyout renders to <body>, viewport-clamped, and closes on any scroll.',
    props: [
      { name: 'trigger', description: 'Render-prop for the anchor: `(open, toggle) => ReactNode`. Wire your button to `toggle` and reflect `open` (e.g. aria-expanded, a chevron).' },
      { name: 'children', description: 'Render-prop for the flyout contents: `(close) => ReactNode`. Call `close` from a chosen item / Done button.' },
      { name: 'align', description: "Which trigger edge the flyout aligns to, 'left' (default) or 'right'." },
      { name: 'openSignal', description: 'Monotonic counter — each increment forces the popover open (mount / 0 ignored). Lets a host open it programmatically (e.g. a "/model" slash command) without making it fully controlled.' },
      { name: 'placement', description: "'top' (default, opens above) or 'bottom' (opens below) — use 'bottom' for a top-bar trigger." },
      { name: 'portal', description: 'Render the flyout via a <body> portal with position:fixed, anchored to the trigger rect, viewport-clamped, closing on any scroll. Set it when the trigger sits inside an overflow-clipping/scrolling container (e.g. a kanban column); default off leaves existing consumers untouched.' },
      { name: 'width', description: 'Fixed flyout width in px (min 200); omit to let it size to content within the min.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Set `portal` when the trigger lives inside a scrolling or overflow-clipping container — otherwise the inline flyout gets clipped or drifts off its anchor.' },
      { guidance: true, description: "Pass placement='bottom' for top-anchored triggers (top-bar controls); leave the default 'top' for low-sitting triggers like a composer." },
      { guidance: true, description: 'Drive programmatic opens with openSignal (increment it) rather than lifting the whole open state — the popover stays self-managed for outside-click/Escape.' },
      { guidance: true, description: 'Reach for Popover for any anchored flyout/menu rather than hand-rolling positioning — single-layer Escape, focus restore, outside-click, and viewport clamping come built in.' },
      { guidance: false, description: 'Do not hardcode colors or px in the contents — use tokens (the glass surface, radius, and shadow already do; the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['relative anchor wrapper (renders trigger)', 'AnimatePresence flyout (glass surface, spring overlayEnter)', 'children(close) contents', 'body portal + portalPos clamp (portal mode)'],
  },
]

export default docs
