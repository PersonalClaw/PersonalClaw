import type { UiDoc } from './uiDoc'

// HeaderActions.tsx exports four related components, so its doc default-exports an
// array — one UiDoc per exported component. The ORDERING TENET (once a source
// comment) becomes machine-readable bestPractices here.
const docs: UiDoc[] = [
  {
    name: 'HeaderActions',
    keywords: ['header', 'actions', 'toolbar', 'overflow', 'responsive', 'topbar', 'controls', 'cluster'],
    description:
      'The one responsive header-controls cluster. Its children degrade together on a single 4-tier ladder (FULL: icon+label → TEXT: label only → ICON: icon only → OVERFLOW: a … menu) as horizontal space shrinks, measured via offscreen probes and a ResizeObserver. In OVERFLOW it keeps the highest-priority controls visible and pushes the rest into an auto-built … menu — no page hand-rolls a header overflow menu.',
    props: [
      { name: 'children', description: 'The header controls (HeaderControl / HeaderSegmented / HeaderModePill / Button), rendered left→right in DOM order.' },
      { name: 'className', description: 'Extra classes on the cluster container (tokens only).' },
    ],
    bestPractices: [
      { guidance: true, description: 'ORDERING TENET: a control that opens a side panel (SidePanel/detail/inspector rail) is the RIGHTMOST child, always.' },
      { guidance: true, description: 'ORDERING TENET: a destructive control (Delete) sits LEFTMOST of the right-edge group. Canonical shape: [Delete] … [other controls] … [open-side-panel].' },
      { guidance: true, description: "Keep the Delete and panel-opener controls priority='low' even though their positions are fixed — priority governs OVERFLOW shedding independently of visual order." },
      { guidance: false, description: 'Do not build a header overflow menu by hand — it falls out of the same child list automatically in the OVERFLOW tier.' },
    ],
    anatomy: ['outer measured container', 'offscreen aria-hidden probe rows (one per tier)', 'visible control row', 'auto … overflow menu (Popover)'],
  },
  {
    name: 'HeaderControl',
    keywords: ['header', 'button', 'control', 'icon', 'action', 'overflow-aware'],
    description:
      'A single overflow-aware control inside HeaderActions. It renders itself at whatever tier the cluster picks (icon+label, label-only, icon-only, or a … menu row) and self-registers its priority and menu descriptor.',
    props: [
      { name: 'icon', description: 'Lucide icon — required to reach the ICON tier.' },
      { name: 'label', description: 'Text label; becomes tooltip + aria-label at the ICON tier.' },
      { name: 'onClick', description: 'Activation handler.' },
      { name: 'variant', description: 'Visual emphasis of the control.' },
      { name: 'active', description: 'Renders the control as currently-on (toggle state).' },
      { name: 'disabled', description: 'Dim + block interaction.' },
      { name: 'danger', description: 'Style as destructive (also flags the overflow menu row).' },
      { name: 'priority', description: "OVERFLOW shedding order: 'primary' stays visible longest, 'low' sheds first." },
      { name: 'hint', description: 'Secondary hint text shown on the overflow menu row.' },
      { name: 'className', description: 'Extra classes (tokens only).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Always pass an icon if the control should survive the ICON tier — a label-only control can only reach TEXT before overflowing.' },
      { guidance: false, description: 'Do not place a HeaderControl outside a HeaderActions cluster — it relies on the cluster context for its tier.' },
    ],
    anatomy: ['tier-aware button (icon / label)', 'overflow menu-row descriptor'],
  },
  {
    name: 'HeaderSegmented',
    keywords: ['header', 'segmented', 'toggle', 'group', 'options', 'switch'],
    description: 'A segmented multi-option toggle sized for the header cluster. Participates in the tier decision but never collapses into the … menu (it has no meaningful single-row form); in OVERFLOW it renders icon-only.',
    props: [
      { name: 'options', description: 'The selectable segments.' },
      { name: 'value', description: 'The currently selected option value.' },
      { name: 'onChange', description: 'Fires with the newly selected value.' },
      { name: 'ariaLabel', description: 'Accessible name for the group.' },
      { name: 'disabled', description: 'Disable the whole group.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use for a small set of mutually-exclusive header modes where all options should stay visible.' },
    ],
    anatomy: ['segmented track', 'per-option buttons', 'sliding active indicator'],
  },
  {
    name: 'HeaderModePill',
    keywords: ['header', 'mode', 'pill', 'dropdown', 'options', 'selector'],
    description: 'A compact pill that opens a menu of modes — the space-efficient alternative to HeaderSegmented when there are more options than fit inline.',
    props: [
      { name: 'options', description: 'The selectable modes.' },
      { name: 'value', description: 'The currently selected mode value.' },
      { name: 'onChange', description: 'Fires with the newly selected value.' },
      { name: 'ariaLabel', description: 'Accessible name for the control.' },
      { name: 'disabled', description: 'Disable the pill.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Prefer over HeaderSegmented when the option set is long or labels are wide.' },
    ],
    anatomy: ['pill trigger (current mode + chevron)', 'Popover menu of options'],
  },
]

export default docs
