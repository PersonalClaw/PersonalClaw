import type { UiDoc } from './uiDoc'

// HeaderActions.tsx exports five related components, so its doc default-exports an
// array — one UiDoc per exported component. The ORDERING TENET (once a source
// comment) becomes machine-readable bestPractices here.
const docs: UiDoc[] = [
  {
    name: 'HeaderActions',
    keywords: ['header', 'actions', 'toolbar', 'overflow', 'responsive', 'topbar', 'controls', 'cluster'],
    description:
      'The one responsive header-controls cluster. Its children degrade together on a single 4-tier ladder (FULL: icon+label → TEXT: label only → ICON: icon only → OVERFLOW: a … menu) as horizontal space shrinks, measured via offscreen probes and a ResizeObserver. In OVERFLOW it keeps the highest-priority controls visible and pushes the rest into an auto-built … menu — no page hand-rolls a header overflow menu. It leaves the title slot what that slot cannot shed (every shrink-0 control and gap beside the title) plus a floor for the title itself, and when the band between the shell corners cannot hold that beside the narrowest the cluster can get (its never-overflow controls plus the …), it asks its TopBar to lay the row out below the corners across the full width.',
    props: [
      { name: 'children', description: 'The header controls (HeaderControl / HeaderMenu / HeaderSegmented / HeaderModePill / Button), rendered left→right in DOM order.' },
      { name: 'className', description: 'Extra classes on the cluster container (tokens only).' },
      { name: 'overflowClassName', description: "Extra classes on the `…` trigger, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) where the page's header controls carry it." },
    ],
    bestPractices: [
      { guidance: true, description: 'ORDERING TENET: a control that opens a side panel (SidePanel/detail/inspector rail) is the RIGHTMOST child, always.' },
      { guidance: true, description: 'ORDERING TENET: a destructive control (Delete) sits LEFTMOST of the right-edge group. Canonical shape: [Delete] … [other controls] … [open-side-panel].' },
      { guidance: true, description: "Keep the Delete and panel-opener controls priority='low' even though their positions are fixed — priority governs OVERFLOW shedding independently of visual order." },
      { guidance: false, description: 'Do not build a header overflow menu by hand — it falls out of the same child list automatically in the OVERFLOW tier, and a HeaderControl marked inOverflow is a row of it at every tier.' },
      { guidance: false, description: 'Do not put chips or other facts about the page beside the title: they cannot shrink, so they either overlap the cluster or starve the title. Put them in TopBar `below`, which wraps; put an action (copy link) in the cluster as a HeaderControl, which sheds into the … menu.' },
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
      { name: 'title', description: 'The tooltip, when the control says more than its label. Defaults to the label; the accessible name stays the label.' },
      { name: 'onClick', description: 'Activation handler.' },
      { name: 'variant', description: 'Visual emphasis of the control.' },
      { name: 'active', description: 'Renders the control as currently-on (toggle state); announces aria-pressed.' },
      { name: 'ariaExpanded', description: 'This control shows/hides an adjacent region — announces aria-expanded and suppresses aria-pressed, since a control cannot honestly claim both.' },
      { name: 'disabled', description: 'Dim + block interaction.' },
      { name: 'disabledReason', description: "Why it is unavailable. Given one, a disabled control stays reachable: aria-disabled with the reason in its tooltip (Button's rule), and the reason as the … menu row's hint." },
      { name: 'danger', description: 'Style as destructive (also flags the overflow menu row).' },
      { name: 'priority', description: "OVERFLOW shedding order: 'primary' stays visible longest, 'low' sheds first." },
      { name: 'hint', description: 'Secondary hint text shown on the overflow menu row.' },
      { name: 'className', description: 'Extra classes (tokens only).' },
      { name: 'inOverflow', description: 'Always a row of the … menu, at every tier, never a button on the row: an action kept one click away without spending a slot (a thread\'s Save as starter). Its presence keeps the … on the row.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Always pass an icon if the control should survive the ICON tier — a label-only control can only reach TEXT before overflowing.' },
      { guidance: true, description: 'A header holds at most three always-visible actions (web/REDESIGN.md §3 rule 4); mark the rest inOverflow.' },
      { guidance: false, description: 'Do not place a HeaderControl outside a HeaderActions cluster — it relies on the cluster context for its tier.' },
    ],
    anatomy: ['tier-aware button (icon / label)', 'overflow menu-row descriptor'],
  },
  {
    name: 'HeaderMenu',
    keywords: ['header', 'menu', 'share', 'actions', 'dropdown'],
    description: 'A header control that opens a short menu of actions (a thread\'s Share: Copy chat link and its hand-offs). It takes the cluster\'s ladder like HeaderControl, and when the row sheds it into the … its rows go there under their own names, since a menu row cannot open a second menu. The menu is a role=menu of menuitems with the shared cursor: it opens on its first row, the arrows move, Enter picks, Escape or Tab closes it with focus back on the control.',
    props: [
      { name: 'icon', description: 'Lucide icon, shown beside the label and alone at the ICON tier.' },
      { name: 'label', description: 'The control\'s name and the menu\'s.' },
      { name: 'items', description: 'The menu\'s rows: a label, an optional icon and hint, and what the row does.' },
      { name: 'priority', description: "OVERFLOW shedding order, as HeaderControl's." },
    ],
    bestPractices: [
      { guidance: true, description: 'Use for a few actions of one kind that would otherwise each take a header slot.' },
    ],
    anatomy: ['tier-aware trigger button', 'portaled Popover with a role=menu of rows'],
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
      { name: 'disabledReason', description: 'Why a disabled pill is unavailable. Keeps it focusable (aria-disabled) with the reason as its title, instead of dropping it from the tab order.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Prefer over HeaderSegmented when the option set is long or labels are wide.' },
      { guidance: true, description: 'Give a disabled pill a disabledReason whenever the reason is not obvious from the page.' },
    ],
    anatomy: ['pill trigger (current mode + chevron)', 'Popover menu of options'],
  },
]

export default docs
