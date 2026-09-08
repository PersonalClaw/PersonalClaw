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
  'agent', 'voice', 'apps', 'inbox', 'documents', 'notifications', 'security', 'secrets', 'devices',
  'sender-trust', 'guardrails',
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
// need a path parameter to address a specific record (`#/loops/<id>`, `#/code/<id>`), or
// carry a pending owner taste call that would red the gate on arrival.
//
// 🔑 `#/app/<name>` USED TO BE ON THAT LIST, AND IT WAS HALF WRONG. The exemption read "needs
// an installed app name; one route per app", which is true of an app's OWN UI and not true of
// the SHELL that hosts it. `AppHostPage` renders three surfaces before any app code runs — a
// 404 EmptyState (`"<name>" isn't installed`, with a focusable "Open the Store" action), a
// retryable LoadError for any other failure, and a spinner — and a name that is not installed
// reaches the first one deterministically, with no fixture, no path record and no interaction
// recipe. So a whole page's worth of chrome sat outside every scan because the harder half of
// the same route was genuinely blocked. **An exemption that covers two things and is true of
// one of them is a gap with a reason attached.** `app/<not-installed>` is in the list below.
//
// ✅ AND THE APP-AUTHORED HALF IS NOW COVERED TOO — the fixture was the whole missing piece. The
// note here used to say app-AUTHORED UI was unreachable because it "needs an installed app WITH
// its bundle built, i.e. a fixture that seeds the e2e home AND has the apps repo present". The
// second clause was the mistaken one: nothing requires the app to be a REAL one. `playwright.
// config.ts` installs `e2e/fixtures/app-ui` into the e2e home at gateway boot — manifest,
// `installed.json` and a hand-written ESM entry — so `app/e2e-ui-fixture` mounts app-authored DOM
// into the host document and every route-driven check sweeps it unchanged.
//
// 🪤 Deliberately NOT a copy of a real app. Depending on the apps repo would make this gate's
// coverage depend on a sibling checkout being present, which is exactly what made
// `test_apps_import_boundary` a silent no-op for its entire life (core 1777). An in-repo fixture
// is always there, so the sweep cannot quietly stop happening.
//
// 🪤 A CLEAN FIXTURE CANNOT PROVE IT WAS SWEPT. Both detectors return `[]` on a healthy tree, so
// this route entry alone is indistinguishable from not scanning at all — and if the fixture failed
// to install, the route would render the SHELL's 404 EmptyState, which is already covered above,
// and still pass. Two things close that: the config fails the BOOT when the fixture is missing,
// and `appSurface.spec.ts` asserts the app-authored DOM mounted and its control takes focus.
//
// Still genuinely out of reach: the native menu-bar companion, which renders no web surface at all.
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
  // The app-hosting SHELL, reached with a name that is deliberately not installed. 🪤 The name
  // matters: anything the e2e home might plausibly have would make this route's rendering depend
  // on fixture state, and the point is a DETERMINISTIC 404 branch. `needsData` because the
  // EmptyState only appears once `/api/apps/{name}` has answered.
  { route: 'app/not-a-real-app', id: 'app-host-not-installed', label: 'App host › not installed', needsData: true },
  // App-AUTHORED DOM, mounted into the HOST document by the app's own ESM bundle. The fixture is
  // installed into the e2e home by playwright.config.ts; the boot fails if it is not there, because
  // an absent app would silently fall back to the shell's 404 branch on the line above.
  { route: 'app/e2e-ui-fixture', id: 'app-host-contributed-ui', label: 'App host › contributed UI', needsData: true },
]

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
