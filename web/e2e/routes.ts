// ── Route manifest — single source of truth for the visual/axe harness ─────
// The nav-reachable routes of the SPA (mirror of the NAV ids in
// src/app/App.tsx). The Playwright visual + axe specs iterate this list so a
// new page is snapshotted the moment it's added here. Hash-routed:
// #/<route>. Keep in sync with App.tsx NAV.

export interface RouteEntry {
  /** hash route segment (no leading #/) */
  route: string
  /** human label for the snapshot / report */
  label: string
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
  'account', 'design', 'chat', 'providers', 'models', 'search', 'prompts', 'memory',
  'agent', 'voice', 'apps', 'inbox', 'notifications', 'security', 'guardrails', 'audit',
  'doctor', 'diagnostics', 'tool-output', 'feedback', 'usage', 'routing', 'legibility',
  'ambient', 'companion', 'sources', 'packs', 'archive', 'portability', 'durability', 'updates',
] as const

/** The settings panels as ROUTES, for the axe scan to iterate alongside ROUTES. */
export const SETTINGS_ROUTES: RouteEntry[] = SETTINGS_PANELS.map((id) => ({
  route: `settings/${id}`,
  label: `Settings › ${id}`,
  needsData: true,
}))

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
