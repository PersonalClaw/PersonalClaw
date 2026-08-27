// ── Route manifest — single source of truth for the visual/axe harness ─────
// The nav-reachable routes of the SPA (mirror of the NAV ids in
// src/app/App.tsx). The Playwright visual + axe specs iterate this list so a
// new page is snapshotted the moment it's added here. Hash-routed:
// #/<route>. Keep in sync with App.tsx NAV.

export interface RouteEntry {
  /** hash route segment (no leading #/) — may carry a query string */
  route: string
  /** human label for the snapshot / report */
  label: string
  /** Filesystem-safe artifact name, for the screenshot baseline and the axe
   *  attachment. Defaults to `route`; REQUIRED when `route` carries a query
   *  string, because `?` and `=` have no business in a committed filename. */
  id?: string
  /** routes that need backend data / auth to render meaningfully — the harness
   *  still snapshots their shell (empty/loading state is a valid baseline). */
  needsData?: boolean
}

export const ROUTES: RouteEntry[] = [
  { route: 'dashboard', label: 'Home' },
  { route: 'chat', label: 'Chat', needsData: true },
  { route: 'projects', label: 'Projects', needsData: true },
  { route: 'knowledge', label: 'Knowledge', needsData: true },
  { route: 'tasks', label: 'Tasks', needsData: true },
  { route: 'inbox', label: 'Inbox', needsData: true },
  { route: 'triggers', label: 'Triggers', needsData: true },
  { route: 'files', label: 'Files', needsData: true },
  { route: 'artifacts', label: 'Artifacts', needsData: true },
  { route: 'terminal', label: 'Terminal' },
  { route: 'agents', label: 'Agents', needsData: true },
  { route: 'tools', label: 'Tools', needsData: true },
  { route: 'skills', label: 'Skills', needsData: true },
  // `learning` was missing while sitting in NAV — 18 nav ids vs 17 entries — so the one page
  // this manifest exists to cover got NO axe scan and NO visual baseline. The gap was silent
  // precisely because the manifest is the only thing that would have reported it.
  { route: 'learning', label: 'Learning', needsData: true },
  { route: 'workflows', label: 'Workflows', needsData: true },
  { route: 'prompts', label: 'Prompts', needsData: true },
  { route: 'apps', label: 'Store', needsData: true },
  { route: 'settings', label: 'Settings' },
]

// ── Settings subpages ───────────────────────────────────────────────────────
// `#/settings` renders the bento HOME grid; every panel lives at its own route and
// mounts only when you go there. So scanning `settings` covered 1 of 31 surfaces, and
// the other 30 never rendered under axe at all — three of the five defects found by
// hand in cycle 49 lived here (design's sub-AA preview, security's unscrollable
// denylist, audit's nameless refresh button).
//
// These need NO interaction recipe: each is a plain hash route. Mirror of SUBPAGES in
// src/pages/settings/SettingsPage.tsx — `settingsSubpageCoverage.test.ts` fails if the
// two lists drift, so a new panel cannot ship unscanned.
export const SETTINGS_PANELS = [
  'account', 'design', 'chat', 'providers', 'models', 'search', 'prompts', 'memory', 'evals',
  'agent', 'voice', 'apps', 'inbox', 'documents', 'notifications', 'security', 'devices', 'guardrails',
  'external-access', 'audit',
  'doctor', 'diagnostics', 'tool-output', 'feedback', 'usage', 'routing', 'legibility',
  'ambient', 'companion', 'sources', 'packs', 'archive', 'portability', 'durability', 'updates',
] as const

/** The settings panels as ROUTES, for the axe scan to iterate alongside ROUTES. */
export const SETTINGS_ROUTES: RouteEntry[] = SETTINGS_PANELS.map((id) => ({
  route: `settings/${id}`,
  label: `Settings › ${id}`,
  needsData: true,
}))

// ── Sub-view routes — a nav page's OTHER surfaces (KL-17) ───────────────────
// Some nav routes host more than one surface, selected by a query param rather
// than by their own path. Scanning the nav route only ever renders the DEFAULT
// one, so the alternates were in exactly the blind spot `learning` was in: the
// knowledge graph is `#/knowledge?view=graph` (KnowledgeListPage's `view` param,
// default `library`), and it had never been axe-scanned or snapshotted because
// the harness had no way to name it.
//
// Deliberately a SEPARATE list from ROUTES: `routeManifestParity.test.ts` holds
// ROUTES to an exact mirror of App.tsx's NAV ids, and a query-param view is not
// a nav id. Same distinction SETTINGS_ROUTES already makes.
//
// These are plain hash routes — `useHashRoute` splits the query off the path, so
// no interaction recipe is needed. `graphRouteCoverage.test.ts` fails if the
// route stops resolving to the graph.
export const VIEW_ROUTES: RouteEntry[] = [
  { route: 'knowledge?view=graph', id: 'knowledge-graph', label: 'Knowledge › Graph', needsData: true },
]

// ── Non-nav routable pages — the THIRD axis (PHF-7) ─────────────────────────
// `App.tsx`'s `ROUTABLE` set carries pages with no nav tile. They were ALL outside every
// harness list, so "every authenticated route is axe-scanned" was false for them, and the
// only thing recording that was a prose comment in `routeManifestParity.test.ts` which had
// already drifted (it named six extras while the code had seven).
//
// Owner call (PHF-7): the criterion says EVERY authenticated route, so the test is whether
// the harness CAN reach the page — not whether it has a nav tile. `App.tsx`'s `renderPage`
// switches on the FIRST route segment, so a bare `#/<id>` renders the page component for
// every entry here. The three below need no path parameter, no fixture and no interaction
// recipe, so nothing but the missing list entry was keeping them unscanned:
//   · mission-control — locked dashboard view the server registers as a preset
//     (`views_store._mission_control_preset`), reached from the command palette.
//   · notifications   — attention surface, reached from the header bell.
//   · discover        — store/discovery surface, reached from Apps.
// The ones still exempt are exempt for a REAL reason (see EXEMPT_FROM_THE_HARNESS): they
// need a path parameter to address a specific record (`#/loops/<id>`, `#/code/<id>`,
// `#/app/<name>`), or carry a pending owner taste call that would red the gate on arrival.
//
// Deliberately a SEPARATE list from ROUTES, for the same reason VIEW_ROUTES is:
// `routeManifestParity.test.ts` holds ROUTES to an exact mirror of App.tsx's NAV ids,
// and a non-nav page is not a nav id. Consumed by the axe scan only — `visual.spec.ts`
// iterates ROUTES + VIEW_ROUTES, so adding a page here buys an a11y scan WITHOUT
// minting a visual baseline that would need platform-qualified review.
export const NON_NAV_ROUTES: RouteEntry[] = [
  { route: 'mission-control', label: 'Mission Control', needsData: true },
  { route: 'notifications', label: 'Notifications', needsData: true },
  { route: 'discover', label: 'Discover', needsData: true },
]

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
