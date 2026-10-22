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
  // Library opens its first tab, Knowledge; each tab's own address is in VIEW_ROUTES below.
  { route: 'library', label: 'Library', needsData: true },
  { route: 'inbox', label: 'Inbox', needsData: true },
  // Automations opens its first tab, the one list; the list's old address and the Workflows tab are
  // in VIEW_ROUTES below.
  { route: 'automations', label: 'Automations', needsData: true },
  // Capabilities opens its first tab, Agents; each tab's own address is in VIEW_ROUTES below.
  { route: 'capabilities', label: 'Capabilities', needsData: true },
  { route: 'settings', label: 'Settings' },
]

// ── Settings subpages ───────────────────────────────────────────────────────
// `#/settings` renders the bento HOME grid; every panel lives at its own route and
// mounts only when you go there. So scanning `settings` covered 1 of 31 surfaces, and
// the other 30 never rendered under axe at all — three of the five defects found by
// hand in one audit lived here (design's sub-AA preview, security's unscrollable
// denylist, audit's nameless refresh button).
//
// These need NO interaction recipe: each is a plain hash route. Mirror of SUBPAGES in
// src/pages/settings/SettingsPage.tsx — `settingsSubpageCoverage.test.ts` fails if the
// two lists drift, so a new panel cannot ship unscanned.
export const SETTINGS_PANELS = [
  'account', 'design', 'chat', 'providers', 'models', 'search', 'prompts', 'evals',
  'agent', 'voice', 'apps', 'inbox', 'documents', 'notifications', 'security', 'secrets', 'devices',
  'sender-trust', 'guardrails',
  'external-access', 'audit',
  'system', 'doctor', 'diagnostics', 'tool-output', 'feedback', 'usage', 'routing', 'legibility',
  'workflows', 'loops',
  'ambient', 'companion', 'buddy', 'sources', 'packs', 'archive', 'portability', 'durability', 'updates',
] as const

/** The settings panels as ROUTES, for the axe scan to iterate alongside ROUTES. */
export const SETTINGS_ROUTES: RouteEntry[] = SETTINGS_PANELS.map((id) => ({
  route: `settings/${id}`,
  label: `Settings › ${id}`,
  needsData: true,
}))

// ── Sub-view routes — a nav page's OTHER surfaces ───────────────────────────
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
//
// A destination's tabs that keep addresses of their own are its surfaces too: Capabilities' Agents,
// Skills, Learning, Tools, Prompts and Apps (#3817) are each at the route their page has always
// had, so each keeps the visual baseline and the scans it had as a sidebar row, and so are Library's
// Knowledge, Files and Artifacts (#3815), and Automations' Workflows tab and the list at the
// address it had as Triggers (#3816). Their routes are not nav ids, and
// `routeManifestParity.test.ts` counts this tier among the scanned ones.
export const VIEW_ROUTES: RouteEntry[] = [
  { route: 'agents', id: 'agents', label: 'Capabilities › Agents', needsData: true },
  { route: 'skills', id: 'skills', label: 'Capabilities › Skills', needsData: true },
  { route: 'learning', id: 'learning', label: 'Capabilities › Learning', needsData: true },
  { route: 'tools', id: 'tools', label: 'Capabilities › Tools', needsData: true },
  { route: 'prompts', id: 'prompts', label: 'Capabilities › Prompts', needsData: true },
  { route: 'apps', id: 'apps', label: 'Capabilities › Apps', needsData: true },
  { route: 'knowledge', id: 'knowledge', label: 'Library › Knowledge', needsData: true },
  // All tasks, every project's: a project's own are its Tasks view (#3818), and the sidebar has no
  // Tasks row. It keeps the address, the visual baseline and the scans it had as a row.
  { route: 'tasks', id: 'tasks', label: 'Projects › All tasks', needsData: true },
  // The terminal's full page, the drawer's Open full: the terminal is the drawer on every page
  // (#3826), and the sidebar has no Terminal row. It keeps the address, the visual baseline and the
  // scans it had as a row.
  { route: 'terminal', id: 'terminal', label: 'Terminal (full page)' },
  { route: 'files', id: 'files', label: 'Library › Files', needsData: true },
  { route: 'artifacts', id: 'artifacts', label: 'Library › Artifacts', needsData: true },
  { route: 'triggers', id: 'triggers', label: 'Automations, at #/triggers', needsData: true },
  { route: 'workflows', id: 'workflows', label: 'Automations › Workflows', needsData: true },
  { route: 'knowledge?view=graph', id: 'knowledge-graph', label: 'Knowledge › Graph', needsData: true },
  // The reading experience, reached at `#/knowledge/item/<id>?read=1` — the sharable
  // reader URL `KnowledgeSection` navigates to (its `onOpenReader`). It belongs in VIEW_ROUTES,
  // not the other two tiers, and the reason is `routeManifestParity.test.ts`: ROUTES must mirror
  // NAV exactly (this is not a nav id), and a NON_NAV_ROUTES entry's first segment must be a
  // ROUTABLE-extra — but `knowledge` is a nav id, so that check would red. VIEW_ROUTES is the tier
  // for a nav page's addressable sub-surface, and it is the one that buys BOTH an axe scan
  // (a11y.spec) and a visual baseline (visual.spec), which is what the clause asks for.
  //
  // 🪤 The item id is deliberately NOT a real one, exactly like `app/not-a-real-app` above: the
  // e2e home seeds no knowledge items (playwright.config.ts writes only a user name + the app-UI
  // fixture), so any id resolves to the reader route's DETERMINISTIC missing-item branch
  // (KnowledgeDetailPage: "This knowledge item no longer exists") — a shell-bearing, axe-clean
  // baseline with a real h1 and a named back control. A seeded fixture item would make the
  // snapshot depend on fixture state; the point is a stable surface the harness can always reach.
  { route: 'knowledge/item/not-a-real-item?read=1', id: 'knowledge-reading', label: 'Knowledge › Reading', needsData: true },
]

// ── Substate URLs — every other state the app addresses by URL that renders its own surface ───
// Each entry is a value of a page's `useQueryParam` (or a sub-path) that swaps the component the
// route renders: a tab, a view or a mode. The default value of each is the route itself, already
// in a tier above, and `knowledge?view=graph` is in VIEW_ROUTES, which visual.spec.ts also reads.
// A tier of its own so the UI quality check covers every one while visual.spec.ts keeps its
// declared, narrower scope (`routeManifestParity.test.ts`, NARROWER_BY_DESIGN).
//
// LEFT OUT, each for its reason. Every one renders the component of an entry here or above, so on
// the harness's empty home it adds a visit and no new DOM:
//   · filters and sorts over a list: artifacts ?kind (16) ?sort (2), inbox ?filter (3) ?kind (9),
//     triggers ?filter (5), notifications ?filter, tools ?risk (3), prompts ?sort (3),
//     chat/history ?origin (5) ?archived, apps' library and store filters and sorts
//     (?sort ?status ?type ?cap ?entity ?ssort ?stype ?sentity ?stag ?ssrc), usage ?period (2),
//     routing ?uc (2) ?qc (4), and triggers/new?pattern (6), which picks one matcher field;
//   · free text (`?q`, `?include`) and record ids (`?open`, `?peek`, `?panel`, `?item`, `?entity`,
//     `?intent`, `?collection`, `?file`, `?result`, `?active`, `?split`), which address data the
//     harness's home does not hold. A record's own page is EXEMPT_FROM_THE_HARNESS's subject.
//
// One literal line per entry, never a `.map()`: `assert-no-route-skips.mjs` counts `route: '`
// literals per tier to derive how many scans the run owes.
export const SUBSTATE_ROUTES: RouteEntry[] = [
  // The phone companion's view two (#3898): the push link opens the one approval it names, owning
  // the screen. View one is `companion` itself, in NON_NAV_ROUTES; both read the same stubbed list.
  { route: 'companion?approval=ap-0a1b2c3d', id: 'companion-one-work', label: 'Companion › one piece of work', needsData: true },
  // The composer's voice mode (#4963) has no address: it is `composer voice mode` in `OPENERS`.
  { route: 'memory?tab=health', id: 'memory-health', label: 'Library › Memory › health', needsData: true },
  { route: 'memory?tab=recall', id: 'memory-recall', label: 'Library › Memory › recall', needsData: true },
  { route: 'memory?tab=inspect', id: 'memory-inspect', label: 'Library › Memory › inspect', needsData: true },
  { route: 'memory?tab=audit', id: 'memory-audit', label: 'Library › Memory › audit', needsData: true },
  { route: 'memory?tab=settings', id: 'memory-settings', label: 'Library › Memory › settings', needsData: true },
  { route: 'knowledge?view=home', id: 'knowledge-home', label: 'Knowledge › home', needsData: true },
  { route: 'knowledge?view=intents', id: 'knowledge-intents', label: 'Knowledge › intents', needsData: true },
  { route: 'knowledge?view=tags', id: 'knowledge-tags', label: 'Knowledge › tags', needsData: true },
  { route: 'knowledge?view=conflicts', id: 'knowledge-conflicts', label: 'Knowledge › conflicts', needsData: true },
  { route: 'knowledge?view=decisions', id: 'knowledge-decisions', label: 'Knowledge › decisions', needsData: true },
  { route: 'workflows?tab=defs', id: 'workflows-defs', label: 'Automations › Workflows › definitions', needsData: true },
  { route: 'prompts?tab=system', id: 'prompts-system', label: 'Prompts › system', needsData: true },
  { route: 'prompts?tab=bundled', id: 'prompts-bundled', label: 'Prompts › bundled', needsData: true },
  { route: 'prompts?tab=snippets', id: 'prompts-snippets', label: 'Prompts › snippets', needsData: true },
  { route: 'skills?mode=browse', id: 'skills-browse', label: 'Skills › browse', needsData: true },
  { route: 'skills?mode=proposals', id: 'skills-proposals', label: 'Skills › proposals', needsData: true },
  { route: 'apps?view=native', id: 'apps-native', label: 'Capabilities › Apps › native', needsData: true },
  { route: 'apps?view=store', id: 'apps-store', label: 'Capabilities › Store', needsData: true },
  { route: 'triggers?view=week', id: 'triggers-week', label: 'Automations › week', needsData: true },
  { route: 'triggers/new', id: 'triggers-new', label: 'Automations › new › schedule', needsData: true },
  { route: 'triggers/new?kind=lifecycle', id: 'triggers-new-lifecycle', label: 'Automations › new › lifecycle', needsData: true },
  { route: 'triggers/new?kind=event', id: 'triggers-new-event', label: 'Automations › new › event', needsData: true },
  { route: 'triggers/new?kind=run_completed', id: 'triggers-new-run_completed', label: 'Automations › new › run_completed', needsData: true },
  { route: 'chat/history', id: 'chat-history', label: 'Chat › history', needsData: true },
  { route: 'chat/history?view=board', id: 'chat-history-board', label: 'Chat › history › board', needsData: true },
  // Where two pages that left the sidebar went (#3818): the notification feed is a filter of the
  // Inbox, and a project's tasks are its Tasks view. The run's home has no project and its feed holds
  // whatever other specs raised first, so both reads are fixed for these rows (`surfaces.spec.ts`).
  { route: 'inbox?filter=notifications', id: 'inbox-notifications', label: 'Inbox › Notifications', needsData: true },
  { route: 'projects/p-0a1b2c3d?tab=tasks', id: 'project-tasks', label: 'Projects › a project › Tasks', needsData: true },
  // The buddy playground's other two tabs: the scripted day, and the pose sheet the buddy orb's
  // snapshots are taken from (#3892).
  { route: 'buddy/playground?tab=day', id: 'buddy-playground-day', label: 'Buddy › Playground › a day' },
  { route: 'buddy/playground?tab=poses', id: 'buddy-playground-poses', label: 'Buddy › Playground › poses' },
  // The expanded card in each of its states, every alert kind and the running list's own (#3893).
  { route: 'buddy/playground?tab=card', id: 'buddy-playground-card', label: 'Buddy › Playground › card' },
]

// ── Non-nav routable pages — the THIRD axis ─────────────────────────────────
// `App.tsx`'s `ROUTABLE` set carries pages with no nav tile. They were ALL outside every
// harness list, so "every authenticated route is axe-scanned" was false for them, and the
// only thing recording that was a prose comment in `routeManifestParity.test.ts` which had
// already drifted (it named six extras while the code had seven).
//
// The criterion says EVERY authenticated route, so the test is whether
// the harness CAN reach the page — not whether it has a nav tile. `App.tsx`'s `renderPage`
// switches on the FIRST route segment, so a bare `#/<id>` renders the page component for
// every entry here. The three below need no path parameter, no fixture and no interaction
// recipe, so nothing but the missing list entry was keeping them unscanned:
//   · mission-control — locked dashboard view the server registers as a preset
//     (`views_store._mission_control_preset`), reached from the command palette.
//   · notifications   — retired to the Inbox's Notifications filter (#3818); the visit opens the
//                       filter through its redirect, which is the address a stored link still has.
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
  // The phone companion: a full-screen page the shell returns early for (`App.tsx`), reached from a
  // push notification or its own address. It lists what waits on you first (#3888).
  { route: 'companion', label: 'Companion', needsData: true },
  // Library's Memory tab (#3815), the page Settings › Memory was, at an address of its own. Scanned
  // as it was as a Settings page; like the Settings pages, it has no visual golden yet.
  { route: 'memory', label: 'Library › Memory', needsData: true },
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
  // The buddy playground (#3892), a full-screen shell route like `#/companion`: its Play tab, with
  // the stage, the controls and the log. It reads nothing from the gateway.
  { route: 'buddy/playground', id: 'buddy-playground', label: 'Buddy › Playground' },
  // The buddy's live presence (#3832), what the buddy window shows: with one piece of work running
  // and the four kinds the waiting list stubs, it shows the first of them expanded, with its card.
  { route: 'buddy', id: 'buddy', label: 'Buddy', needsData: true },
]

/** The routes above that the shell renders full-screen, without its NavRail (`SHELL_ROUTES` in
 *  App.tsx), by their first segment. Each page marks its root `data-full-screen="<segment>"`,
 *  which `gotoRoute` waits for in place of the rail. The shell renders one only once the owner
 *  is onboarded, so the mark proves what the rail does: the harness reached the route, not the
 *  onboarding screen. */
export const FULL_SCREEN_ROUTES: readonly string[] = ['buddy', 'companion']

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

// ── The agent orb's states — one element, photographed per variant ─────────
// Settings → Design shows every phase of the orb at 20 and 64 px on request
// (`pages/settings/AgentOrbStates.tsx`), and `visual.spec.ts` photographs that gallery alone, in
// each theme with motion and under reduced motion. Each shot's `key` is its baseline's name, as a
// route's `<id>-<theme>` is, so `visualBaselineCoverage.test.ts` holds the goldens to this list
// too. Not a route scan: the axe and walkthrough sweeps reach the page through SETTINGS_ROUTES.
export const ORB_STATE_SHOTS = THEMES.flatMap((theme) => [
  { key: `agent-orb-states-${theme}`, theme, reducedMotion: 'no-preference' as const },
  { key: `agent-orb-states-${theme}-reduced-motion`, theme, reducedMotion: 'reduce' as const },
])

// ── Reduced motion — the surfaces the motion audit stripped, under the preference ──────────────
// `docs/design/motion.md` §10 takes the motion off the app's high-frequency actions, and §3 says what
// reduced motion leaves of the rest: nothing that moves. `visual.spec.ts` photographs each surface the
// audit touched that the suite already shoots, and the command palette and a menu open, with
// `prefers-reduced-motion: reduce` in force, in both themes. A golden of its own rather than the motion
// shot's: reduced motion also changes the picture (a `.glass` surface drops its blur), so the two
// frames differ by design. `reducedMotionSnapshots.test.ts` holds this list to the audit's strip table,
// and `visualBaselineCoverage.test.ts` holds each `key` to a committed golden.
export type ReducedMotionOpen = 'command palette' | 'composer menu'

export interface ReducedMotionShot {
  /** The surface, as `reducedMotionSnapshots.test.ts` names it. */
  id: string
  /** The golden's name: `<id>-<theme>-reduced-motion`. */
  key: string
  theme: Theme
  /** The hash route the shot loads, with no leading `#/`. */
  route: string
  /** What the shot opens once the route is at rest, if anything. */
  open?: ReducedMotionOpen
}

const REDUCED_MOTION_SURFACES: { id: string; route: string; open?: ReducedMotionOpen }[] = [
  { id: 'dashboard', route: 'dashboard' },
  { id: 'chat', route: 'chat' },
  { id: 'projects', route: 'projects' },
  { id: 'tasks', route: 'tasks' },
  { id: 'apps', route: 'apps' },
  { id: 'settings', route: 'settings' },
  { id: 'command-palette', route: 'chat', open: 'command palette' },
  // The composer's + menu: a `ui/Popover` menu, the primitive most of the app's menus are built on.
  { id: 'composer-menu', route: 'chat', open: 'composer menu' },
]

export const REDUCED_MOTION_SHOTS: ReducedMotionShot[] = THEMES.flatMap((theme) =>
  REDUCED_MOTION_SURFACES.map((s) => ({ ...s, theme, key: `${s.id}-${theme}-reduced-motion` })),
)
