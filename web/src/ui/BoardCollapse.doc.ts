import type { UiDoc } from './uiDoc'

// BoardCollapse.tsx exports the shared kanban column-collapse mechanism. The two
// documented components (the header collapse button + the collapsed rail) get one
// UiDoc each. The "rails stay collapsed during a drag / the rail is the drop target"
// and "template never changes mid-drag" contracts were source comments — encoded here.
const docs: UiDoc[] = [
  {
    name: 'CollapseColumnButton',
    keywords: ['collapse', 'column', 'kanban', 'board', 'header', 'chevron', 'rail'],
    description:
      'The header affordance that manually collapses an expanded kanban column into a slim vertical rail (chat-history tag board + Tasks status board). Always visible but quiet — a brand-new affordance hidden behind hover would be undiscoverable. Stops event propagation so collapsing does not also trigger the column header.',
    props: [
      { name: 'onCollapse', description: 'Called when the chevron is pressed to collapse the column; the click is stopPropagation-guarded so it never bubbles to the header.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Pair with useBoardCollapse — wire onCollapse to its `toggle` so the collapse becomes a persisted user override layered over the derived (empty-column) default.' },
      { guidance: true, description: 'Keep it always visible, not hover-gated — a discoverable but quiet chevron is the intended affordance.' },
      { guidance: false, description: 'Do not restructure the grid template on collapse mid-drag — the board template must be a pure function of data + stored preference (restructuring during dragstart makes Chrome cancel the native drag).' },
    ],
    anatomy: ['button (quiet, always-visible)', 'ChevronsRightLeft icon'],
  },
  {
    name: 'CollapsedBoardColumn',
    keywords: ['collapsed', 'column', 'rail', 'kanban', 'board', 'drop-target', 'expand', 'vertical'],
    description:
      'The slim vertical rail a collapsed kanban column renders as: the column icon, its item count, and a rotated label. The WHOLE rail is a live drop target — spread the same drag/drop handlers (and dragover highlight) the expanded column uses onto it via `...rest`; it highlights (never expands) while a card hovers. Clicking re-expands the column.',
    props: [
      { name: 'count', description: 'Item count shown in the rail; an auto-collapsed column re-expands on the render after a drop lands because its count went ≥1.' },
      { name: 'icon', description: 'The column icon (a Lucide icon), tinted by `tone`.' },
      { name: 'label', description: 'Column name, rendered rotated (vertical writing mode) down the rail.' },
      { name: 'onExpand', description: 'Click-to-expand handler (the manual collapse toggle). When set, the rail becomes a role="button" cursor-pointer; when omitted, the rail is inert.' },
      { name: 'tone', description: 'Accent color for the icon + label (tag color / status tone); falls back to on-surface-low.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Spread the SAME drag/drop handlers (and dragover highlight style) the expanded column uses onto the rail via the passthrough props — the rail itself is the drop target and must highlight, never expand, while a card hovers.' },
      { guidance: true, description: 'Pass onExpand so clicking the rail re-expands the column; omit it only for a rail that must stay inert.' },
      { guidance: false, description: 'Do not expand the rail on dragover — an auto-collapsed column expands naturally after a drop (count ≥1); expanding mid-drag would restructure the grid and cancel the native drag.' },
    ],
    anatomy: ['div rail (drop target, click-to-expand)', 'column icon (toned)', 'count (tabular-nums)', 'rotated label (vertical-rl)'],
  },
]

export default docs
