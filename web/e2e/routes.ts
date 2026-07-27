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
  { route: 'workflows', label: 'Workflows', needsData: true },
  { route: 'prompts', label: 'Prompts', needsData: true },
  { route: 'apps', label: 'Store', needsData: true },
  { route: 'settings', label: 'Settings' },
]

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
