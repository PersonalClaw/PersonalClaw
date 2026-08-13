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
      { name: 'tabIndex', description: 'Roving-tabindex slot for a container that moves focus row to row (`lib/useMenuCursor`): 0 on the cursor row, -1 on the rest, so the popup is ONE tab stop rather than one per row. Pass it from every container that declares role=menu or role=listbox — those roles promise arrow navigation. Omit inside a role-less popover, where a button\'s default 0 is right.' },
      { name: 'disabled', description: 'The row cannot act right now. Rendered as `aria-disabled` (never the `disabled` attribute) so a focus-driven menu can still land on it — a disabled item stays reachable and announced instead of leaving a silent gap in the list — and dimmed at the kit\'s control level (40).' },
      { name: 'role', description: "The ITEM role required by the popup container's role, when it declares one: `option` inside a role=listbox, `menuitem` for an action row inside a role=menu, `menuitemradio` for a pick-one row. Omit inside a role-less popover, where a bare button is correct (28 of the 30 call sites). Passing it also publishes the selected state in that role's vocabulary — `aria-selected` for an option, `aria-checked` for a radio item, and deliberately nothing for a plain menuitem." },
    ],
    bestPractices: [
      { guidance: true, description: 'Use MenuRow for options inside a Popover rather than styling a raw <button> — the press spring, hover icon-nudge, and selected treatment stay consistent across every menu.' },
      { guidance: true, description: "Pass `role` whenever the enclosing popup declares role=menu or role=listbox. Measured before it existed: the collapsed Segmented announced a list box with 0 options and the row context menu a menu with 0 menuitems, because this row rendered a bare button." },
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
