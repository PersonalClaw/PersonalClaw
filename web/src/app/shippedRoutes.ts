/** Every address the dashboard has shipped, and where each one came from (#3819).
 *
 *  An address is a hash route as the address bar holds it after `#/`: `<route>/<sub...>?<query>`
 *  (`useHashRoute`). Each entry is one the app has handed out, or still hands out: a row of the
 *  router's own tables, a command palette row, a link the desktop shell or a notification opens, a
 *  link the gateway writes into an Inbox item, a notification or a reply, and the sub-routes and
 *  substates each section documents in its header comment. A link someone kept (a bookmark, an old
 *  transcript, a copied URL) has to keep opening, so an entry is never dropped when its page moves:
 *  it gains a `lands` naming where it opens now.
 *
 *  What holds this list to its sources, and the app to this list:
 *
 *    · `everyShippedRouteResolves.test.tsx` opens every entry in the real shell, fresh, as a copied
 *      URL is opened, and asserts it settles on `lands` (the entry itself when it has none) and
 *      renders a page: not the dashboard the URL corrector falls back to, not the switch's "isn't
 *      available", not an error, not nothing;
 *    · `everyProducedLinkIsAShippedRoute.test.ts` reads each producer that can be read from here
 *      (the router's tables, the palette's rows, the `personalclaw://` parser, the menu-bar item,
 *      the notification and approval routes, the e2e route manifest) and fails on an address that
 *      producer makes and this list lacks, and on an entry naming a producer that does not make it.
 *
 *  The router's top-level routes and Settings' pages carry `since`: the first release tag whose
 *  `web/src/app/App.tsx` (`NAV`, `ROUTABLE`, `SHELL_ROUTES`) or `SettingsPage.tsx` (`SUBPAGES`)
 *  had them, read with `git show <tag>:<file>`. A tag cut later adds its new routes here.
 *
 *  A parameter is a sample (`SAMPLE`), shaped like the real thing, never a record any home holds,
 *  so an entry opens its page's own state for an address whose record is missing. Pure data: no
 *  DOM, no imports, so a test that only reads it runs without one.
 */

/** The release tags, oldest first. */
export const RELEASES = ['v0.1.0', 'v0.1.1', 'v0.1.2', 'v0.1.3', 'v0.2.0', 'v0.2.1', 'v0.2.2', 'v0.2.3'] as const
export type Release = (typeof RELEASES)[number]

/** Where an address came from. */
export type RouteSource =
  /** The router's tables, `web/src/app/App.tsx` `NAV`, `ROUTABLE` and `SHELL_ROUTES`, at a release
   *  tag or on main. Top-level routes only. */
  | 'router'
  /** A command palette row (`commands` in `App.tsx`, the keymap's rows among them). */
  | 'palette'
  /** A `personalclaw://` link, as `desktop/deepLinks.js` turns it into a hash. */
  | 'deep-link'
  /** The menu-bar item and Quick Capture, `desktop/trayPresence.js` `DEEP_LINKS`. */
  | 'tray'
  /** A native notification's tap, `lib/nativeNotifications.ts` `NOTIFICATION_SOURCE_ROUTES`. */
  | 'notification'
  /** Where an approval is answered, `app/approvalDestination.ts`. */
  | 'approval'
  /** The menu-bar companion app: it opens a loop's page, and an approval on the phone's page. */
  | 'companion-app'
  /** A link the gateway writes into a notification, an Inbox item, an agent's reply or a paired
   *  phone's way in. */
  | 'gateway'
  /** A section's header comment, a guide under `docs/`, or the e2e route manifest. */
  | 'documented'

export interface ShippedRoute {
  /** The address after `#/`, its parameters percent-encoded as the router writes them. */
  readonly route: string
  /** Every producer known to make it. */
  readonly from: readonly RouteSource[]
  /** The first release that had it, where the tags were read (see the header). */
  readonly since?: Release
  /** Where the shell settles when it opens this address, when that is not the address itself: an
   *  in-app redirect, or a link's own words rewritten into the ones the page reads. */
  readonly lands?: string
  /** Opened on a home whose first-run setup is not done. Every other entry is opened signed in,
   *  set up, on an empty home. */
  readonly home?: 'new'
}

/** The sample parameters. */
export const SAMPLE = {
  chat: 'chat-1-1792387100',
  room: 'team-review',
  project: 'p-0a1b2c3d',
  loop: 'a17c3f92',
  code: '950626ff',
  knowledge: 'k-0a1b2c3d',
  artifact: 'report-1',
  run: '11b9a34c',
  node: 'synthesize',
  workflow: 'daily-digest',
  trigger: 'tr-0a1b2c3d',
  task: 'ta-0a1b2c3d',
  app: 'an-app',
  approval: 'ap-0a1b2c3d',
  prompt: 'greeting',
  preset: 'morning-brief',
  agent: 'writer',
  commit: '0a1b2c3',
  server: 'files',
} as const

const S = SAMPLE

export const SHIPPED_ROUTES: readonly ShippedRoute[] = [
  // ── The router's top-level routes ─────────────────────────────────────────────────────────────
  { route: 'dashboard', from: ['router', 'palette', 'deep-link', 'tray', 'documented'], since: 'v0.1.0' },
  { route: 'chat', from: ['router', 'palette', 'deep-link', 'tray', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'projects', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'knowledge', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'tasks', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'inbox', from: ['router', 'palette', 'deep-link', 'tray', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'triggers', from: ['router', 'palette', 'deep-link', 'notification', 'gateway', 'documented'], since: 'v0.1.0' },
  { route: 'files', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'artifacts', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.3' },
  { route: 'terminal', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'agents', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'tools', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'skills', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'learning', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.2.0' },
  { route: 'prompts', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'workflows', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'apps', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'settings', from: ['router', 'palette', 'deep-link', 'tray', 'documented'], since: 'v0.1.0' },
  { route: 'notifications', from: ['router', 'palette', 'deep-link', 'notification', 'documented'], since: 'v0.1.0' },
  { route: 'discover', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.1.2' },
  { route: 'mission-control', from: ['router', 'palette', 'deep-link', 'documented'], since: 'v0.2.0' },
  { route: 'loop', from: ['router', 'deep-link', 'documented'], since: 'v0.1.0' },
  // The bare loops and code routes fold into the one loop composer (`LoopsSection`, `CodeSection`).
  { route: 'loops', from: ['router', 'deep-link', 'notification', 'documented'], since: 'v0.1.0', lands: 'loop' },
  { route: 'code', from: ['router', 'deep-link', 'gateway'], since: 'v0.1.0', lands: 'loop?kind=code' },
  // The host of an app's page (`AppHostPage`). Bare, it names no app and says so.
  { route: 'app', from: ['router', 'deep-link'], since: 'v0.1.0' },
  // The shell's own full-screen routes. Setup is opened on a home that has not done it; on one that
  // has, `#/onboarding` leaves for Home.
  { route: 'onboarding', from: ['router', 'documented'], since: 'v0.1.0', home: 'new', lands: 'onboarding/name' },
  { route: 'onboarding', from: ['router', 'documented'], since: 'v0.1.0', lands: 'dashboard' },
  { route: 'companion', from: ['router', 'gateway', 'documented'], since: 'v0.2.0' },

  // ── Home ──────────────────────────────────────────────────────────────────────────────────────
  { route: 'dashboard?safe=1', from: ['documented'] },

  // ── Chat ──────────────────────────────────────────────────────────────────────────────────────
  { route: 'chat/new', from: ['palette', 'documented'] },
  { route: 'chat/history', from: ['documented'] },
  { route: 'chat/history?view=board', from: ['documented'] },
  { route: 'chat/history?origin=room', from: ['documented'] },
  { route: `chat/${S.chat}`, from: ['palette', 'approval', 'gateway', 'documented'] },
  // A search result opens its chat with the find bar seeded; the bar takes the term and the address
  // drops it (`useTranscript`).
  { route: `chat/${S.chat}?find=report`, from: ['palette', 'gateway'], lands: `chat/${S.chat}` },
  { route: `chat/room/${S.room}`, from: ['approval', 'documented'] },
  // No room named: the list of rooms, in place (`RoomsRedirect`).
  { route: 'chat/room', from: ['documented'], lands: 'chat/history?origin=room' },
  // A link's own words for a chat's state (`linkState.ts`): a new thread's draft rides as `seed`,
  // and the pane it names opens as that pane's own flag.
  { route: 'chat/new?prompt=draft+this', from: ['deep-link'], lands: 'chat/new?seed=draft+this' },
  { route: 'chat/new?pane=history', from: ['deep-link'], lands: 'chat/new?history=1' },
  { route: `chat/new?project=${S.project}`, from: ['deep-link'] },
  { route: 'chat/new?mode=plan', from: ['deep-link'] },
  { route: `chat/${S.chat}?pane=activity&file=src%2Fapp.py`, from: ['deep-link', 'documented'], lands: `chat/${S.chat}?file=src%2Fapp.py&activity=1` },

  // ── Projects ──────────────────────────────────────────────────────────────────────────────────
  { route: `projects/${S.project}`, from: ['documented'] },

  // ── Knowledge ─────────────────────────────────────────────────────────────────────────────────
  { route: 'knowledge/new', from: ['documented'] },
  { route: `knowledge/item/${S.knowledge}`, from: ['gateway', 'documented'] },
  { route: `knowledge/item/${S.knowledge}?read=1`, from: ['documented'] },
  { route: `knowledge/item/${S.knowledge}?loc=p3`, from: ['gateway'] },
  { route: 'knowledge/sources', from: ['documented'] },
  { route: 'knowledge/sources/new', from: ['documented'] },
  { route: 'knowledge/reports', from: ['documented'] },
  { route: 'knowledge?view=graph', from: ['documented'] },
  { route: 'knowledge?view=home', from: ['documented'] },
  { route: 'knowledge?view=intents', from: ['documented'] },
  { route: 'knowledge?view=tags', from: ['documented'] },
  { route: 'knowledge?view=conflicts', from: ['documented'] },
  { route: 'knowledge?view=decisions', from: ['documented'] },

  // ── Tasks ─────────────────────────────────────────────────────────────────────────────────────
  { route: 'tasks/new', from: ['documented'] },
  { route: 'tasks?view=board', from: ['documented'] },
  { route: `tasks?view=board&scope=${S.project}`, from: ['documented'] },
  { route: `tasks?open=${S.task}`, from: ['gateway', 'documented'] },

  // ── Inbox and notifications ───────────────────────────────────────────────────────────────────
  { route: 'inbox?capture=1', from: ['tray', 'documented'] },
  { route: 'notifications?filter=unread', from: ['documented'] },

  // ── Triggers ──────────────────────────────────────────────────────────────────────────────────
  { route: 'triggers/new', from: ['documented'] },
  { route: 'triggers/new?kind=lifecycle', from: ['documented'] },
  { route: 'triggers/new?kind=event', from: ['documented'] },
  { route: 'triggers/new?kind=run_completed', from: ['documented'] },
  { route: `triggers/new?kind=schedule&preset=${S.preset}`, from: ['documented'] },
  { route: 'triggers?view=week', from: ['documented'] },
  { route: `triggers?open=${S.trigger}`, from: ['gateway', 'documented'] },

  // ── Files and artifacts ───────────────────────────────────────────────────────────────────────
  // An artifact's link from before artifacts had their own page, kept in old transcripts.
  { route: `files/${S.artifact}`, from: ['documented'], lands: `artifacts/${S.artifact}` },
  { route: 'files?file=src%2Fapp.py', from: ['documented'] },
  { route: `artifacts/${S.artifact}`, from: ['deep-link', 'documented'] },
  { route: `artifacts/${S.artifact}?v=2`, from: ['documented'] },

  // ── Capabilities ──────────────────────────────────────────────────────────────────────────────
  { route: 'agents/new', from: ['documented'] },
  { route: `agents?open=native%3A${S.agent}`, from: ['documented'] },
  { route: `tools?q=${S.server}`, from: ['gateway'] },
  { route: 'skills?mode=browse', from: ['deep-link', 'documented'] },
  { route: 'skills?mode=proposals', from: ['deep-link', 'gateway', 'documented'] },
  { route: 'prompts/new?kind=user', from: ['documented'] },
  { route: `prompts/view?kind=user&name=${S.prompt}`, from: ['documented'] },
  { route: 'prompts?tab=system', from: ['documented'] },
  { route: 'prompts?tab=bundled', from: ['documented'] },
  { route: 'prompts?tab=snippets', from: ['documented'] },
  { route: 'workflows?tab=defs', from: ['documented'] },
  { route: `workflows/runs/${S.run}`, from: ['gateway', 'documented'] },
  { route: `workflows/runs/${S.run}?node=${S.node}`, from: ['approval', 'documented'] },
  { route: `workflows/defs/${S.workflow}`, from: ['documented'] },
  { route: `workflows/defs/${S.workflow}/edit`, from: ['documented'] },
  { route: `workflows/defs/${S.workflow}/edit?from=2`, from: ['documented'] },

  // ── Apps ──────────────────────────────────────────────────────────────────────────────────────
  { route: 'apps?view=store', from: ['documented'] },
  { route: 'apps?view=native', from: ['documented'] },
  { route: 'apps?view=store&stag=search', from: ['documented'] },
  { route: 'apps?view=store&stag=ocr', from: ['gateway'] },
  { route: `app/${S.app}`, from: ['palette', 'documented'] },

  // ── Loops and code ────────────────────────────────────────────────────────────────────────────
  { route: 'loop?kind=code', from: ['documented'] },
  { route: 'loop?mode=attended', from: ['deep-link', 'documented'] },
  { route: 'loops/history', from: ['documented'] },
  { route: `loops/${S.loop}`, from: ['tray', 'approval', 'companion-app', 'gateway', 'documented'] },
  { route: 'code/history', from: ['documented'] },
  { route: `code/${S.code}`, from: ['deep-link', 'gateway', 'documented'] },
  // A cockpit's center and its open task, as a `personalclaw://` link names them.
  { route: `code/${S.code}?file=src%2Fapp.py`, from: ['deep-link'] },
  { route: `code/${S.code}?pane=changes&commit=${S.commit}`, from: ['deep-link'] },
  { route: `code/${S.code}?pane=changes&diff=src%2Fapp.py&task=${S.task}`, from: ['deep-link', 'documented'] },

  // ── The phone's page ──────────────────────────────────────────────────────────────────────────
  { route: `companion?approval=${S.approval}`, from: ['companion-app', 'gateway', 'documented'] },

  // ── Setup ─────────────────────────────────────────────────────────────────────────────────────
  { route: 'onboarding/name', from: ['documented'], home: 'new' },

  // ── Settings: the home, then every page by its id ─────────────────────────────────────────────
  // Vocabulary merged into Speech & Transcription, before the first tag; its link opens that section.
  { route: 'settings/vocabulary', from: ['documented'], lands: 'settings/voice?section=vocabulary' },
  { route: 'settings/voice?section=vocabulary', from: ['documented'] },
  { route: 'settings/account', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/agent', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/ambient', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/apps', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/archive', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/audit', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/chat', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/companion', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/design', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/devices', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/diagnostics', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/doctor', from: ['documented'], since: 'v0.1.2' },
  { route: 'settings/documents', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/durability', from: ['gateway', 'documented'], since: 'v0.1.3' },
  { route: 'settings/evals', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/external-access', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/feedback', from: ['documented'], since: 'v0.1.3' },
  { route: 'settings/guardrails', from: ['documented'], since: 'v0.1.2' },
  { route: 'settings/inbox', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/legibility', from: ['documented'], since: 'v0.1.2' },
  { route: 'settings/loops', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/memory', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/models', from: ['gateway', 'documented'], since: 'v0.1.0' },
  { route: 'settings/notifications', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/packs', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/portability', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/prompts', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/providers', from: ['deep-link', 'documented'], since: 'v0.1.0' },
  { route: 'settings/routing', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/search', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/secrets', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/security', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/sender-trust', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/sources', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/system', from: ['documented'], since: 'v0.2.2' },
  { route: 'settings/tool-output', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/updates', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/usage', from: ['documented'], since: 'v0.2.0' },
  { route: 'settings/voice', from: ['documented'], since: 'v0.1.0' },
  { route: 'settings/workflows', from: ['documented'], since: 'v0.2.0' },
  // Memory's tabs, each its own surface.
  { route: 'settings/memory?tab=health', from: ['documented'] },
  { route: 'settings/memory?tab=recall', from: ['documented'] },
  { route: 'settings/memory?tab=inspect', from: ['documented'] },
  { route: 'settings/memory?tab=audit', from: ['documented'] },
  { route: 'settings/memory?tab=settings', from: ['documented'] },
]
