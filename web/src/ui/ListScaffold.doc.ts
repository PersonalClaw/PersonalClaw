import type { UiDoc } from './uiDoc'

// ListScaffold.tsx exports eight related components (the shared list-page kit +
// its uniform loading / empty states), so its doc default-exports an array — one
// UiDoc per exported component. Authored: keywords, prose, per-prop descriptions,
// Do/Don't, anatomy. Prop type/required are DERIVED from the source at build time.
const docs: UiDoc[] = [
  {
    name: 'ListScaffold',
    keywords: ['list', 'scaffold', 'page', 'shell', 'topbar', 'destination', 'entity', 'skeleton', 'layout'],
    description:
      'The shared shell for workspace/build list PAGES (design Tenet 2: a list is a destination page, not a cramped panel). A sparse TopBar with the title on the left and an action slot on the right, above a centered scrolling column at the customizable content width. Pair with the sibling skeleton + EmptyState pieces so every entity surface reads as one family.',
    props: [
      { name: 'title', description: 'Page title, rendered title-l in the TopBar left slot.' },
      { name: 'right', description: 'Fills the TopBar action slot (the page primary action / view switches).' },
      { name: 'children', description: 'The centered page body (the list itself).' },
      { name: 'bodyClassName', description: "Overrides the default body wrapper classes (default 'mx-auto px-l py-2xl'); the content-width max is always applied." },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for ListScaffold for any entity/list destination page rather than hand-rolling a TopBar + centered column — it keeps every list surface in one visual family.' },
      { guidance: true, description: 'Use the sibling ListSkeleton / FormSkeleton / CardGridSkeleton for first load and EmptyState for the empty case so loading and empty states match across pages.' },
      { guidance: false, description: 'Do not hardcode colors or px in className / bodyClassName — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['flex-col full-height container', 'TopBar (title left • right action slot)', 'scrolling region', 'centered content-width body'],
  },
  {
    name: 'LoadError',
    keywords: ['error', 'load', 'failed', 'fetch', 'retry', 'alert', 'empty', 'first-load'],
    description:
      "First-load FAILURE for a list/collection surface — the sibling of EmptyState. A failed fetch and a genuinely empty collection are different facts, and a surface that branches only on `data === undefined` conflates them: the user is told \"you have none\" when the truth is \"we could not load it\", with no way to retry and nothing announced. `useQuery` returns an `error` for exactly this (measured: 3 of 106 call sites read it). Renders as role=alert, so a load failure interrupts — EmptyState deliberately does not, because \"you have none\" is a normal answer.",
    props: [
      { name: 'what', description: 'The thing that failed to load, lowercase — fills "Couldn\'t load your <what>" and the fallback body copy.' },
      { name: 'error', description: "The rejection from useQuery; its `message` is shown when present, so the server's own words reach the user instead of a generic apology." },
      { name: 'onRetry', description: 'Re-runs the fetch (typically `invalidateKeys(key); refresh()`). Omit only if the surface genuinely cannot retry — the button disappears.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Branch on the error FIRST: `data === undefined` is also true for the loading and empty branches, so an error test placed after them never runs.' },
      { guidance: true, description: 'Pass `onRetry` wherever the fetch can be re-run — an error with no recovery leaves the user stuck on a dead surface.' },
      { guidance: false, description: 'Do not use it for a form-submit or action failure — that is InlineError, which sits inline near the control rather than replacing the whole surface.' },
    ],
    anatomy: ['role=alert centered column', 'aria-hidden AlertTriangle', 'headline-s "Couldn\'t load your <what>"', "error message or reassurance line", 'optional Retry Button'],
  },
  {
    name: 'ListRow',
    keywords: ['list', 'row', 'card', 'item', 'clickable', 'hover', 'accent', 'motion'],
    description:
      'The animated row wrapper used inside a list body — staggered rise+fade in, and, when clickable, a physical hover-lift + press so rows feel like liftable cards rather than flat strips. Exit collapses its height so removals animate. Consistent across every list page.',
    props: [
      { name: 'accent', description: 'Optional left-edge accent color bar (a 3px rule); pass a token-backed color.' },
      { name: 'children', description: 'The row content (leading icon, title, meta, trailing controls).' },
      { name: 'index', description: 'Row position — staggers the enter animation (capped) so a list cascades in.' },
      { name: 'label', description: "What the row IS, for assistive tech — normally the entity's title. A clickable row is a button, and without this its accessible name is computed from the whole subtree (measured up to 2001 characters for one inbox row). Required in practice for every clickable row; ignored on a static one, which is not a button." },
      { name: 'onClick', description: 'Makes the row interactive — enables the hover-lift + press-scale and a pointer cursor. Omit for a static row.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Pass `label` whenever you pass `onClick` — the row announces that one short name instead of reading its entire content as the button name. The body stays readable underneath as ordinary text.' },
      { guidance: true, description: 'Pass `index` from the list map so rows cascade in on a staggered spring rather than popping in together.' },
      { guidance: true, description: 'Wrap the list in AnimatePresence so a removed ListRow plays its height-collapse exit instead of vanishing.' },
      { guidance: false, description: "Do not add hover/press styling by hand — pass onClick and the lift/press springs (expr-scaled) are applied automatically only for clickable rows." },
    ],
    anatomy: ['motion.div (staggered enter, hover-lift + press when clickable, height-collapse exit)', 'optional left accent bar', 'row content'],
  },
  {
    name: 'ListSkeleton',
    keywords: ['skeleton', 'loading', 'placeholder', 'list', 'shimmer', 'rows', 'first-load'],
    description:
      "The default first-load state for list pages — N shimmering placeholder rows shaped like ListRow (matching padding + leading-icon), so the swap to real data is calm rather than a jarring pop. Gate a list body on it while a cache-miss fetch is in flight.",
    props: [
      { name: 'what', description: 'What is loading, for the sr-only announcement — "tasks", "notification settings". Omit for a bare "Loading…". Measured before it existed: the region was marked `role=status aria-busy` with an `aria-label`, and announced NOTHING at any point in a cold load, because a live region is announced by its content and the content was styled divs.' },
      { name: 'rows', description: 'How many placeholder rows to render (default 6).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use ListSkeleton (not a bare "Loading…") as the first-load gate on list pages so the page appears instantly in its final shape.' },
      { guidance: true, description: "Set `rows` near the expected count so the skeleton's height roughly matches the loaded list and the swap doesn't jump." },
    ],
    anatomy: ['aria-busy container', 'per-row: leading Skeleton square + two Skeleton text lines'],
  },
  {
    name: 'Skeleton',
    keywords: ['skeleton', 'placeholder', 'shimmer', 'loading', 'block', 'shape'],
    description:
      'A single shimmering placeholder block — the primitive the other skeletons compose. Use it to render the SHAPE of content while a (cache-miss) fetch is in flight so the page appears instantly instead of a bare "Loading…". `className` controls its size/shape.',
    props: [
      { name: 'className', description: 'Sizing/shape classes (height, width, rounding) — the shimmer + base rounding are built in.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Compose Skeleton blocks to mirror the real content layout so the swap to loaded data is calm; prefer the ready-made ListSkeleton / FormSkeleton / CardGridSkeleton when one fits.' },
      { guidance: false, description: 'Do not set colors via className — the shimmer surface comes from the `skeleton` token utility; className is for size/shape only (no raw hex/px).' },
    ],
    anatomy: ['aria-hidden shimmer div (skeleton utility + rounding)'],
  },
  {
    name: 'CardGridSkeleton',
    keywords: ['skeleton', 'loading', 'placeholder', 'grid', 'cards', 'dashboard', 'stats', 'hub'],
    description:
      'A first-load placeholder for a stat/hub panel — a title block plus a grid of N stat cards. Use it as the loading gate on read-only dashboard-style panels (Overview, Security).',
    props: [
      { name: 'what', description: 'What is loading, for the sr-only announcement — "tasks", "notification settings". Omit for a bare "Loading…". Measured before it existed: the region was marked `role=status aria-busy` with an `aria-label`, and announced NOTHING at any point in a cold load, because a live region is announced by its content and the content was styled divs.' },
      { name: 'cards', description: 'How many stat-card placeholders to render (default 4).' },
      { name: 'cols', description: 'Grid column count (default 2).' },
      { name: 'title', description: 'Whether to render the leading title block (default true); pass false when the panel has no header.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Match `cards` / `cols` to the real dashboard grid so the skeleton mirrors its final layout.' },
      { guidance: true, description: 'Reach for CardGridSkeleton on stat/hub panels rather than a generic spinner so the loaded grid resolves in place.' },
    ],
    anatomy: ['aria-busy container', 'optional title block (two Skeleton lines)', 'card grid: per-card icon + label + value + subline Skeletons'],
  },
  {
    name: 'FormSkeleton',
    keywords: ['skeleton', 'loading', 'placeholder', 'form', 'settings', 'config', 'sections', 'panel'],
    description:
      'A first-load placeholder for a settings FORM panel — a title block plus N sections, each a heading and a few label/control rows, shaped like the Section/Row chrome so the swap to the real form is calm. Use it as the loading gate on config panels fetched via useQuery (Chat, Voice, Inbox, Notifications, Agent defaults…).',
    props: [
      { name: 'what', description: 'What is loading, for the sr-only announcement — "tasks", "notification settings". Omit for a bare "Loading…". Measured before it existed: the region was marked `role=status aria-busy` with an `aria-label`, and announced NOTHING at any point in a cold load, because a live region is announced by its content and the content was styled divs.' },
      { name: 'rows', description: 'Label/control rows per section (default 3).' },
      { name: 'sections', description: 'How many section blocks to render (default 2).' },
      { name: 'title', description: 'Whether to render the leading title block (default true); pass false when the panel has no header.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use FormSkeleton as the loading gate on config/settings panels so the form resolves in place rather than after a spinner.' },
      { guidance: true, description: 'Set `sections` / `rows` near the real form shape so the placeholder height roughly matches the loaded panel.' },
    ],
    anatomy: ['aria-busy container', 'optional title block', 'per-section: heading Skeleton + rows (label lines + trailing control Skeleton)'],
  },
  {
    name: 'EmptyState',
    keywords: ['empty', 'state', 'placeholder', 'zero', 'cta', 'blank', 'onboarding', 'claw'],
    description:
      'The uniform empty state for a list/panel — a centered icon (the Spark claw mark by default, or a supplied Lucide icon in a tinted tile), a headline, an optional subline, and an optional call-to-action Button. Use it wherever a collection is empty so every zero state reads the same.',
    props: [
      { name: 'action', description: 'Optional CTA ({ label, onClick, icon? }) rendered as a Button below the text.' },
      { name: 'hint', description: 'Optional subline explaining the empty state or how to fill it.' },
      { name: 'icon', description: 'Optional Lucide icon shown in a tinted tile; omit to fall back to the Spark claw mark.' },
      { name: 'title', description: 'The empty-state headline.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for EmptyState for any empty collection rather than hand-rolling centered text, so zero states stay consistent across pages.' },
      { guidance: true, description: 'Give an `action` when there is an obvious next step (create the first item) so the empty state is a launchpad, not a dead end.' },
    ],
    anatomy: ['centered column', 'icon tile (Lucide) or Spark claw mark', 'headline + optional hint', 'optional CTA Button'],
  },
  {
    name: 'Loading',
    keywords: ['loading', 'placeholder', 'text', 'pending', 'inline'],
    description:
      'A minimal inline "Loading…" text line, dimmed to on-surface-low. A last-resort placeholder for small transient waits; for list/form/dashboard first loads prefer the shaped skeletons instead.',
    props: [
      { name: 'what', description: 'What is loading — renders the visible text "Loading <what>…". Omit for a bare "Loading…". The component is a `role=status aria-busy` live region, so this text is both what a sighted user reads and what everyone hears; measured before that role existed: "Loading…" was on screen for 2.8s on #/workflows with nothing announced.' },],
    bestPractices: [
      { guidance: true, description: 'Use Loading only for tiny inline waits; for a page or panel first load reach for ListSkeleton / FormSkeleton / CardGridSkeleton so content resolves in its final shape.' },
    ],
    anatomy: ['dimmed "Loading…" text span'],
  },
  {
    name: 'LoadingStatus',
    keywords: ['loading', 'skeleton', 'announce', 'live region', 'status', 'screen reader'],
    description:
      'The sr-only text that makes a skeleton audible. A `role="status" aria-busy="true"` region is announced by its CONTENT changing, so a skeleton built from styled divs announces nothing however well it is marked up — measured on a cold, throttled load of #/tasks and #/knowledge: the region was on screen, correctly named by aria-label, and silent from the first frame to the moment data arrived. Render this inside any busy region; it replaces the aria-label rather than joining it, because `status` takes no name from its content and a second hard-coded string would drift from the one people hear.',
    props: [
      { name: 'what', description: 'What is loading — renders "Loading <what>…". Omit for a bare "Loading…".' },
    ],
    bestPractices: [
      { guidance: true, description: 'Put it inside every region that sets aria-busy. The rail in ui/loadingAnnounced.test.tsx fails a busy region that renders no announcement.' },
      { guidance: false, description: 'Do not add an aria-label alongside it — one region, one string, so the announced text cannot drift from the name.' },
    ],
    anatomy: ['sr-only span with the "Loading …" text'],
  },
]

export default docs
