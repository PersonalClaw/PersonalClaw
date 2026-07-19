import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { MotionConfig, motion } from 'framer-motion'
import { ease, duration } from '../design/motion'
import {
  MessageSquare, Bell, ListChecks, Zap,
  Inbox, Files, BookOpen, Users, Wrench, Sparkles,
  Workflow, FileText, Settings, Terminal, Loader2, FolderKanban, Blocks,
  LayoutDashboard,
} from 'lucide-react'
import { NavRail, type NavItem } from '../ui/NavRail'
import { ShellCornerLeft, ShellCornerRight } from '../ui/ShellCorners'
import { ChatPage } from '../pages/ChatPage'
import { useIdentity } from './identity'
import { Onboarding } from './Onboarding'
import { useHashRoute } from './useHashRoute'
import { useIsMobile } from './useIsMobile'
import type { RouteProps } from './useQueryState'
import { ErrorBoundary } from './ErrorBoundary'
import { api } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { CommandPalette, type Command } from './CommandPalette'
import { TerminalDrawer } from '../pages/terminal/TerminalDrawer'
import { Toaster } from '../ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import { DialogHost } from '../ui/dialog/DialogHost'
import { UpdateProgressOverlay } from '../ui/UpdateProgressOverlay'
import { runInTerminal, runInTerminalWhenReady, subscribeTerminal, hasActiveTerminal } from '../pages/terminal/terminalBridge'
import { useCachedData } from '../lib/useCachedData'
import { resolveAppIcon } from '../pages/apps/appIcon'
import { getNavApps, onNavAppsChange } from '../pages/apps/navApps'
import type { AppSummary } from '../lib/api'

// Heavier / less-frequently-first-viewed pages are code-split so the initial
// chat view loads lean (Monaco, graph SVG, settings panels, etc. load on demand).
const LoopsSection = lazy(() => import('../pages/loops/LoopsSection').then((m) => ({ default: m.LoopsSection })))
const CodeSection = lazy(() => import('../pages/code/CodeSection').then((m) => ({ default: m.CodeSection })))
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const AgentsSection = lazy(() => import('../pages/agents/AgentsSection').then((m) => ({ default: m.AgentsSection })))
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage').then((m) => ({ default: m.NotificationsPage })))
const TriggersSection = lazy(() => import('../pages/triggers/TriggersSection').then((m) => ({ default: m.TriggersSection })))
const TasksSection = lazy(() => import('../pages/tasks/TasksSection').then((m) => ({ default: m.TasksSection })))
const ProjectsSection = lazy(() => import('../pages/projects/ProjectsSection').then((m) => ({ default: m.ProjectsSection })))
const WorkflowsSection = lazy(() => import('../pages/workflows/WorkflowsSection').then((m) => ({ default: m.WorkflowsSection })))
const PromptsSection = lazy(() => import('../pages/prompts/PromptsSection').then((m) => ({ default: m.PromptsSection })))
const SkillsPage = lazy(() => import('../pages/skills/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const ToolsPage = lazy(() => import('../pages/tools/ToolsPage').then((m) => ({ default: m.ToolsPage })))
const KnowledgeSection = lazy(() => import('../pages/knowledge/KnowledgeSection').then((m) => ({ default: m.KnowledgeSection })))
const LoopSection = lazy(() => import('../pages/loop/LoopSection').then((m) => ({ default: m.LoopSection })))
const InboxPage = lazy(() => import('../pages/inbox/InboxPage').then((m) => ({ default: m.InboxPage })))
const FilesSection = lazy(() => import('../pages/files/FilesSection').then((m) => ({ default: m.FilesSection })))
const AppsSection = lazy(() => import('../pages/apps/AppsSection').then((m) => ({ default: m.AppsSection })))
const AppHostPage = lazy(() => import('../pages/apps/AppHostPage').then((m) => ({ default: m.AppHostPage })))
const TerminalPage = lazy(() => import('../pages/terminal/TerminalPage').then((m) => ({ default: m.TerminalPage })))
const DashboardPage = lazy(() => import('../pages/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })))

const NAV: NavItem[] = [
  // Primary group (no section header): the Dashboard is the home, then Chat,
  // then Projects, then Knowledge.
  { id: 'dashboard', label: 'Home', icon: LayoutDashboard },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'projects', label: 'Projects', icon: FolderKanban },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
  // Platform group (was "Workspace") — Tasks + Triggers moved in here.
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'inbox', label: 'Inbox', icon: Inbox, section: 'Platform' },
  { id: 'triggers', label: 'Triggers', icon: Zap, section: 'Platform' },
  { id: 'files', label: 'Files', icon: Files, section: 'Platform' },
  { id: 'terminal', label: 'Terminal', icon: Terminal, section: 'Platform' },
  // Capabilities group (was "Build").
  { id: 'agents', label: 'Agents', icon: Users, section: 'Capabilities' },
  { id: 'tools', label: 'Tools', icon: Wrench, section: 'Capabilities' },
  { id: 'skills', label: 'Skills', icon: Sparkles, section: 'Capabilities' },
  { id: 'workflows', label: 'Workflows', icon: Workflow, section: 'Capabilities' },
  { id: 'prompts', label: 'Prompts', icon: FileText, section: 'Capabilities' },
  // Apps group: the Store (browse/install) + each installed app's contributed UI
  // pages are injected here dynamically at render (see appNavItems).
  { id: 'apps', label: 'Store', icon: Blocks, section: 'Apps' },
  // Settings is pinned to the very bottom of the rail (NavRail honors pinBottom).
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]
// Routes reachable without a dedicated nav item. The Loop feature no longer has a
// dedicated nav tile — loops are launched from within Projects (and surfaced as
// chat-session widgets) — but its detail/history/planning sub-routes
// (#/loop, #/loops/<id>, #/code/<id>, …) stay reachable.
const ROUTABLE = new Set([...NAV.map((n) => n.id), 'notifications', 'loop', 'loops', 'code', 'app'])

function PageFallback() {
  return <div className="flex h-full items-center justify-center"><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
}

function renderPage(active: string, r: RouteProps) {
  // Every page receives the full RouteProps bundle so its sub-pages, tabs,
  // filters, search, and open-panel state can all be URL-addressable.
  switch (active) {
    case 'dashboard': return <DashboardPage {...r} />
    case 'chat': return <ChatPage {...r} />
    case 'loop': return <LoopSection {...r} />
    case 'loops': return <LoopsSection {...r} />
    case 'code': return <CodeSection {...r} />
    case 'notifications': return <NotificationsPage {...r} />
    case 'triggers': return <TriggersSection {...r} />
    case 'tasks': return <TasksSection {...r} />
    case 'projects': return <ProjectsSection {...r} />
    case 'knowledge': return <KnowledgeSection {...r} />
    case 'inbox': return <InboxPage {...r} />
    case 'files': return <FilesSection {...r} />
    case 'terminal': return <TerminalPage {...r} />
    case 'workflows': return <WorkflowsSection {...r} />
    case 'prompts': return <PromptsSection {...r} />
    case 'skills': return <SkillsPage {...r} />
    case 'tools': return <ToolsPage {...r} />
    case 'agents': return <AgentsSection {...r} />
    case 'apps': return <AppsSection {...r} />
    case 'app': return <AppHostPage {...r} />
    case 'settings': return <SettingsPage {...r} />
    default: return <div className="flex h-full items-center justify-center text-on-surface-low" data-type="headline-s">{NAV.find((n) => n.id === active)?.label} — coming soon</div>
  }
}

const NAV_COLLAPSED_KEY = 'nav-collapsed'

/** App root — wraps the whole shell in `MotionConfig reducedMotion="user"` so
 *  every framer-motion animation automatically swaps transform/layout motion for
 *  a fade (or nothing) when the OS "Reduce Motion" preference is on. This is the
 *  system-wide accessibility fallback for the component-redesign motion sweep;
 *  the global CSS `prefers-reduced-motion` rule (tokens.css) covers CSS
 *  transitions, this covers JS-driven motion. */
export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <AppInner />
    </MotionConfig>
  )
}

function AppInner() {
  // The Dashboard is the home: the app lands on the at-a-glance home (nav #0).
  const { route, sub, navEpoch, navigate, query, setQuery } = useHashRoute('dashboard')
  // Out-of-context approval nudges: toast when a tool-approval (e.g. a subagent's)
  // is raised for a chat the user isn't currently viewing. The active chat key is
  // `sub` on the chat route (excluding the new/history list routes).
  const activeChatSession = route === 'chat' && sub && sub !== 'new' && sub !== 'history' ? sub : ''
  useApprovalToasts(activeChatSession)
  const { onboarded, loaded } = useIdentity()
  const [navCollapsed, setNavCollapsed] = useState(() => localStorage.getItem(NAV_COLLAPSED_KEY) === '1')
  useEffect(() => { localStorage.setItem(NAV_COLLAPSED_KEY, navCollapsed ? '1' : '0') }, [navCollapsed])
  // Mobile: the rail defaults COLLAPSED and expands as an overlay DRAWER (it must not
  // squeeze the page like the in-flow desktop rail). The shell toggle opens/closes the
  // drawer; picking a nav target closes it again. `mobileNavOpen` is the drawer state
  // (never persisted — always starts closed on a fresh mobile load).
  const isMobile = useIsMobile()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  // Effective collapse: on mobile the rail is collapsed unless the drawer is open;
  // on desktop it follows the persisted user preference.
  const railCollapsed = isMobile ? !mobileNavOpen : navCollapsed
  const toggleNav = () => { if (isMobile) setMobileNavOpen((v) => !v); else setNavCollapsed((v) => !v) }
  // A nav selection on mobile closes the overlay drawer (tap-through-to-collapse).
  const onNavSelect = (id: string) => { navigate(id); if (isMobile) setMobileNavOpen(false) }
  // Close the drawer if the viewport grows back to desktop while it was open.
  useEffect(() => { if (!isMobile) setMobileNavOpen(false) }, [isMobile])

  // Ambient count of loops actively working in the background — surfaced as a badge
  // on the one Loop nav tile so autonomous runs are visible from any page (the whole
  // point of a loop is it runs while you're elsewhere). ONE poll over ALL kinds
  // (general/goal/code/design) — the union of every kind's active states — so a running
  // General or Design loop is counted too, not just goal+code.
  const [activeLoops, setActiveLoops] = useState(0)
  const ACTIVE_LOOP_STATES = new Set(['running', 'paused', 'stagnant', 'blocked', 'needs_input'])
  useVisiblePoll(() => {
    api.uLoops().then((ls) => setActiveLoops(ls.filter((l) => ACTIVE_LOOP_STATES.has(l.status)).length)).catch(() => {})
  }, 8000)

  // Installed apps → the Apps nav section. Apps do NOT auto-register: the user
  // opts each one in from its detail panel ("Show in navigation"), persisted via
  // navApps. Only enabled, UI-bearing, user-pinned apps get a nav target
  // (id `app/<name>`) beneath the Store tile. Re-read live on pin changes.
  const { data: installedApps } = useCachedData<AppSummary[]>(
    'apps', () => api.apps().catch(() => [] as AppSummary[]), { persist: true },
  )
  const [navAppSet, setNavAppSet] = useState<string[]>(() => getNavApps())
  useEffect(() => onNavAppsChange(() => setNavAppSet(getNavApps())), [])
  const appNavItems: NavItem[] = (installedApps ?? [])
    .filter((a) => a.enabled && a.hasUI && (a.uiPages?.length ?? 0) > 0 && navAppSet.includes(a.name))
    .map((a) => ({
      id: `app/${a.name}`,
      label: a.uiPages[0].label || a.displayName,
      icon: resolveAppIcon(a.uiPages[0].icon || a.icon),
      section: 'Apps',
    }))

  // Contributed-app SDK events (A6/A8): launch a chat, badge an app's nav tile.
  const [appBadges, setAppBadges] = useState<Record<string, number>>({})
  useEffect(() => {
    const onLaunch = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      const qs = new URLSearchParams()
      if (d.prompt) qs.set('seed', d.prompt)
      if (d.agent) qs.set('agent', d.agent)
      const q = qs.toString()
      navigate(d.session ? `chat/${encodeURIComponent(d.session)}` : `chat/new${q ? `?${q}` : ''}`)
    }
    const onBadge = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      if (!d.app) return
      setAppBadges((prev) => {
        const next = { ...prev }
        if (d.count == null || d.count === 0) delete next[d.app]
        else next[d.app] = d.count
        return next
      })
    }
    window.addEventListener('ne:launch-chat', onLaunch as EventListener)
    window.addEventListener('ne:nav-badge', onBadge as EventListener)
    return () => {
      window.removeEventListener('ne:launch-chat', onLaunch as EventListener)
      window.removeEventListener('ne:nav-badge', onBadge as EventListener)
    }
  }, [navigate])
  // quick terminal drawer (reachable from any page) — toggled by ⌘` / ⌘K.
  const [termDrawer, setTermDrawer] = useState(false)
  // a command queued by "Run in terminal" while no terminal was live yet — sent
  // once a session registers (the drawer opens, its TerminalView registers).
  const pendingRun = useRef<string | null>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '`' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setTermDrawer((v) => !v) }
    }
    // "Run in terminal" from a chat code block: open the drawer, queue the cmd.
    const onRun = (e: Event) => {
      const cmd = (e as CustomEvent).detail?.command
      if (typeof cmd !== 'string' || !cmd) return
      if (hasActiveTerminal() && runInTerminal(cmd)) return
      pendingRun.current = cmd
      setTermDrawer(true)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('ne:run-in-terminal', onRun as EventListener)
    // flush the queued command once a terminal session becomes available. The
    // registration callback fires while the terminal's WebSocket is still
    // CONNECTING, so a fixed-delay one-shot send raced the socket open and could
    // silently drop the command — retry until the send actually succeeds.
    const unsub = subscribeTerminal(() => {
      if (pendingRun.current && hasActiveTerminal()) {
        const cmd = pendingRun.current
        pendingRun.current = null  // claim it NOW so a 2nd callback can't re-run it
        runInTerminalWhenReady(cmd)
      }
    })
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('ne:run-in-terminal', onRun as EventListener); unsub() }
  }, [])

  // Onboarding is a real route (#/onboarding), full-screen, no NavRail. A guard
  // redirects TO it when there's no name and AWAY from it once onboarded.
  useEffect(() => {
    if (!loaded) return
    if (!onboarded && route !== 'onboarding') navigate('onboarding')
    else if (onboarded && route === 'onboarding') navigate('dashboard')
  }, [loaded, onboarded, route, navigate])

  // Persist the embed flag at mount-time so in-page navigation (which strips the
  // query param from the hash) doesn't lose it. MUST be before any early return so
  // hook order is stable across renders (React #310).
  const embedRef = useRef(query.embed === '1')
  if (query.embed === '1') embedRef.current = true

  // Wait for the server identity before deciding — don't flash onboarding.
  if (!loaded) return <div className="grid h-full place-items-center" style={{ background: 'var(--color-canvas)' }}><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
  if (route === 'onboarding' || !onboarded) return <Onboarding />

  // Embed mode (`?embed=1`): render ONLY the page content — no NavRail, no shell
  // corners — so an app's ChatEmbed gets just the chat surface, not a nested copy
  // of the whole PersonalClaw shell. Used by the SDK ChatEmbed iframe.
  if (embedRef.current) {
    const embedRoute = ROUTABLE.has(route) ? route : 'chat'
    // Keep `embed=1` in the URL across in-embed navigation. navigate() replaces
    // the whole hash (path+query), so a plain navigate drops the flag — the ref
    // above keeps the CURRENT document in embed mode, but a reload (notably the
    // ErrorBoundary's stale-chunk auto-reload after a self-update) re-parses the
    // URL and would nest the full shell inside the host iframe. With the flag
    // kept in the URL, reload and copy-link reconstruct embed mode; the ref stays
    // as the same-document latch for raw-navigate paths (e.g. ne:launch-chat).
    const embedNavigate: typeof navigate = (path, opts) => {
      const [p, q = ''] = path.replace(/^#?\/?/, '').split('?')
      const usp = new URLSearchParams(q)
      usp.set('embed', '1')
      navigate(`${p}?${usp.toString()}`, opts)
    }
    return (
      <div className="h-full" style={{
        background: 'var(--color-canvas)',
        // Zero the shell-corner vars — no corners render in embed mode, so
        // TopBar's padding (which reads these with a non-zero fallback) must
        // collapse to avoid dead space on both sides of the page header.
        '--shell-corner-l': '0px',
        '--shell-corner-r': '0px',
        '--shell-corner-rh': '0px',
      } as React.CSSProperties}>
        <ErrorBoundary resetKey={embedRoute}>
          <Suspense fallback={<PageFallback />}>
            {renderPage(embedRoute, { sub, navigate: embedNavigate, navEpoch, query, setQuery })}
          </Suspense>
        </ErrorBoundary>
        {/* Imperative hosts the embedded page still depends on: confirm dialogs
            (delete flows) and ne:toast surfaces would otherwise silently no-op. */}
        <Toaster />
        <DialogHost />
      </div>
    )
  }

  // The REAL route to render (loops/code keep their own sections for detail/history/
  // planning sub-routes; only the BARE route was folded into the #/loop composer).
  // An unknown route falls back to the home dashboard.
  const rendered = ROUTABLE.has(route) ? route : 'dashboard'
  // The nav-HIGHLIGHT route: loops launch from within Projects, so a bare #/loop
  // composer or a #/loops/<id> / #/code/<id> deep-link lights the Projects tile.
  // Highlight-only — must NOT change which page renders.
  const active = (rendered === 'loop' || rendered === 'loops' || rendered === 'code') ? 'projects'
    : rendered === 'app' ? `app/${(sub ?? '').split('/')[0]}`  // light the specific app tile
    : rendered
  // Ambient active-loop count badges the Projects tile (loops live under projects now).
  const appBadgeTotal = Object.values(appBadges).reduce((a, b) => a + b, 0)
  // Build the rail: static NAV with badges, then splice the dynamic app UI tiles
  // in right after the Store tile (so they sit contiguous under the Apps section
  // header). A per-app badge (set via the SDK setNavBadge) lights its own tile.
  const navItems: NavItem[] = []
  for (const n of NAV) {
    if (n.id === 'projects' && activeLoops > 0) navItems.push({ ...n, badge: String(activeLoops) })
    else if (n.id === 'apps' && appBadgeTotal > 0) navItems.push({ ...n, badge: String(appBadgeTotal) })
    else navItems.push(n)
    if (n.id === 'apps') {
      for (const ai of appNavItems) {
        const badge = appBadges[ai.id.slice('app/'.length)]
        navItems.push(badge ? { ...ai, badge: String(badge) } : ai)
      }
    }
  }
  // command palette (⌘K): every nav destination as a "Go to" + global actions
  const commands: Command[] = [
    ...NAV.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: n.section ?? '', run: () => navigate(n.id) })),
    // pinned app tiles are nav destinations too — same "Go to" contract
    ...appNavItems.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: 'app', run: () => navigate(n.id) })),
    { id: 'go:notifications', label: 'Notifications', hint: 'Go to', icon: Bell, keywords: 'alerts feed', run: () => navigate('notifications') },
    { id: 'act:terminal-drawer', label: 'Toggle terminal drawer', hint: 'Action · ⌘`', icon: Terminal, keywords: 'shell pty console', run: () => setTermDrawer((v) => !v) },
    { id: 'act:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: () => navigate('settings') },
  ]
  return (
    <div className="flex h-full" style={{ background: 'var(--color-canvas)' }}>
      <NavRail items={navItems} activeId={active} onSelect={onNavSelect} collapsed={railCollapsed}
        overlay={isMobile} overlayOpen={isMobile && mobileNavOpen} onScrimClick={() => setMobileNavOpen(false)} />
      <main className="relative flex-1 min-w-0">
        {/* App-shell corner regions — float above page content, not a header row */}
        <ShellCornerLeft collapsed={railCollapsed} onToggle={toggleNav} />
        <ShellCornerRight terminalOpen={termDrawer} onToggleTerminal={() => setTermDrawer((v) => !v)} navigate={navigate} />
        <ErrorBoundary resetKey={rendered}>
          <Suspense fallback={<PageFallback />}>
            {/* Route cross-fade (Slice 5 global choreography): the new page fades+
                rises in on each route change — keyed on `rendered` so switching
                sections reads as a continuous transition, not a hard cut.
                Enter-only (no exit-wait) keeps navigation instant; MotionConfig
                at the root swaps this for no motion under Reduce Motion. */}
            <motion.div
              key={rendered}
              className="h-full"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: duration.medium, ease: ease.emphasizedDecel }}
            >
              {renderPage(rendered, { sub, navigate, navEpoch, query, setQuery })}
            </motion.div>
          </Suspense>
        </ErrorBoundary>
      </main>
      <CommandPalette commands={commands} />
      <Toaster />
      <DialogHost />
      {/* Self-update step progression (WS `update_progress`) — shell-level so the
          modal appears from ANY page while an update pipeline runs. */}
      <UpdateProgressOverlay />
      <TerminalDrawer open={termDrawer} onClose={() => setTermDrawer(false)} onOpenFull={() => { setTermDrawer(false); navigate('terminal') }} />
    </div>
  )
}
