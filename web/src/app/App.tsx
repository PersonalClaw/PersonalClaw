import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { MotionConfig, motion } from 'framer-motion'
import { ease, duration } from '../design/motion'
import { armCueAudio } from '../design/soundCues'
import { installPushCuePlayback } from './pushCuePlayback'
import { Bell, Blocks, BookOpen, Brain, Compass, FileCode, FileText, Files, FolderKanban, Inbox, LayoutDashboard, ListChecks, Loader2, MessageSquare, Radar, Settings, Sparkles, Terminal, Users, Workflow, Wrench, Zap } from 'lucide-react'
import { NavRail, type NavItem } from '../ui/NavRail'
import { ShellCornerLeft, ShellCornerRight } from '../ui/ShellCorners'
import { IncidentBanner } from './IncidentBanner'
import { ChatPage } from '../pages/ChatPage'
import { useIdentity } from './identity'
import { Onboarding } from './Onboarding'
import { peekOnboardingExit, clearOnboardingExit } from './onboarding/exitTo'
import { ProductTour } from './onboarding/ProductTour'
import { useHashRoute } from './useHashRoute'
import { useIsMobile } from './useIsMobile'
import type { RouteProps } from './useQueryState'
import { ErrorBoundary } from './ErrorBoundary'
import { syncAppGenUiComponents } from './appGenUiLayer'
import { api } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { ACTIVE_LOOP_STATUSES } from '../lib/loopStatus'
import { CommandPalette, type Command } from './CommandPalette'
import { TerminalDrawer } from '../pages/terminal/TerminalDrawer'
import { Toaster } from '../ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import { useNativeNotifications } from '../lib/nativeNotifications'
import { DialogHost } from '../ui/dialog/DialogHost'
import { PersonalityShellElement } from './personality'
import { UpdateProgressOverlay } from '../ui/UpdateProgressOverlay'
import { runInTerminal, runInTerminalWhenReady, subscribeTerminal, hasActiveTerminal } from '../pages/terminal/terminalBridge'
import { LoadingStatus } from '../ui/ListScaffold'
import { useQuery } from '../lib/data'
import { resolveAppIcon } from '../pages/apps/appIcon'
import { useWidgetActionLauncher } from '../ui/widget/useWidgetActionBridge'
import { getNavApps, onNavAppsChange } from '../pages/apps/navApps'
import { isDisclosed, undisclosedCount, useNavDisclosure } from './navDisclosure'
import type { AppSummary } from '../lib/api'

// Heavier / less-frequently-first-viewed pages are code-split so the initial
// chat view loads lean (Monaco, graph SVG, settings panels, etc. load on demand).
const LoopsSection = lazy(() => import('../pages/loops/LoopsSection').then((m) => ({ default: m.LoopsSection })))
const CodeSection = lazy(() => import('../pages/code/CodeSection').then((m) => ({ default: m.CodeSection })))
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const AgentsSection = lazy(() => import('../pages/agents/AgentsSection').then((m) => ({ default: m.AgentsSection })))
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage').then((m) => ({ default: m.NotificationsPage })))
const TriggersSection = lazy(() => import('../pages/triggers/TriggersSection').then((m) => ({ default: m.TriggersSection })))
const LearningPage = lazy(() => import('../pages/learning/LearningPage').then((m) => ({ default: m.LearningPage })))
const TasksSection = lazy(() => import('../pages/tasks/TasksSection').then((m) => ({ default: m.TasksSection })))
const ProjectsSection = lazy(() => import('../pages/projects/ProjectsSection').then((m) => ({ default: m.ProjectsSection })))
const PromptsSection = lazy(() => import('../pages/prompts/PromptsSection').then((m) => ({ default: m.PromptsSection })))
const WorkflowsSection = lazy(() => import('../pages/workflows/WorkflowsSection').then((m) => ({ default: m.WorkflowsSection })))
const SkillsPage = lazy(() => import('../pages/skills/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const ToolsPage = lazy(() => import('../pages/tools/ToolsPage').then((m) => ({ default: m.ToolsPage })))
const KnowledgeSection = lazy(() => import('../pages/knowledge/KnowledgeSection').then((m) => ({ default: m.KnowledgeSection })))
const LoopSection = lazy(() => import('../pages/loop/LoopSection').then((m) => ({ default: m.LoopSection })))
const InboxPage = lazy(() => import('../pages/inbox/InboxPage').then((m) => ({ default: m.InboxPage })))
const FilesSection = lazy(() => import('../pages/files/FilesSection').then((m) => ({ default: m.FilesSection })))
const ArtifactsSection = lazy(() => import('../pages/artifacts/ArtifactsSection').then((m) => ({ default: m.ArtifactsSection })))
const AppsSection = lazy(() => import('../pages/apps/AppsSection').then((m) => ({ default: m.AppsSection })))
const AppHostPage = lazy(() => import('../pages/apps/AppHostPage').then((m) => ({ default: m.AppHostPage })))
const TerminalPage = lazy(() => import('../pages/terminal/TerminalPage').then((m) => ({ default: m.TerminalPage })))
const DashboardPage = lazy(() => import('../pages/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const MissionControl = lazy(() => import('../pages/dashboard/MissionControl').then((m) => ({ default: m.MissionControl })))
const DiscoverPage = lazy(() => import('../pages/discover/DiscoverPage').then((m) => ({ default: m.DiscoverPage })))
const CompanionPage = lazy(() => import('../pages/companion/CompanionPage').then((m) => ({ default: m.CompanionPage })))

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
  { id: 'artifacts', label: 'Artifacts', icon: FileCode, section: 'Platform' },
  { id: 'terminal', label: 'Terminal', icon: Terminal, section: 'Platform' },
  // Capabilities group (was "Build").
  { id: 'agents', label: 'Agents', icon: Users, section: 'Capabilities' },
  { id: 'tools', label: 'Tools', icon: Wrench, section: 'Capabilities' },
  { id: 'skills', label: 'Skills', icon: Sparkles, section: 'Capabilities' },
  { id: 'learning', label: 'Learning', icon: Brain, section: 'Capabilities' },
  { id: 'prompts', label: 'Prompts', icon: FileText, section: 'Capabilities' },
  { id: 'workflows', label: 'Workflows', icon: Workflow, section: 'Capabilities' },
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
// `mission-control` is routable but NOT in `NAV`: it is a locked DASHBOARD VIEW the server
// registers (`views_store._mission_control_preset`), not a static rail destination. The registry
// declares it `nav_pinned` with an icon, and NOTHING on the frontend reads either field yet — so
// until a rail is built from `/api/dashboard/views`, the command palette below is how a user
// reaches it. That is the palette's stated job (see its comment: the always-open door to a
// surface the rail is holding back), not a workaround.
const ROUTABLE = new Set([...NAV.map((n) => n.id), 'notifications', 'discover', 'loop', 'loops', 'code', 'app', 'mission-control'])

/** The Suspense fallback for every code-split route, so it is what a user sees on EVERY
 *  navigation whose chunk is not cached yet.
 *
 *  It used to be a bare spinning icon: no role, no name, no text. Measured on a cold load of
 *  `#/terminal` — parent `flex h-full items-center justify-center` with `role=(none)`, the icon
 *  with `aria-label=(none)`, and the only live region on the page the toast host's EMPTY
 *  `sr-only` status. So the announcement was nothing, on every route.
 *
 *  Fixed the way the tree already does it (`ListSkeleton`): `role="status" aria-busy="true"` on
 *  the region plus `LoadingStatus`, whose sr-only text is what actually gets announced — an
 *  `aria-label` on a live region is a NAME, not an announcement, which is the trap
 *  `LoadingStatus`' own doc records. Nothing visible changes. */
function PageFallback() {
  return (
    <div role="status" aria-busy="true" className="flex h-full items-center justify-center">
      <LoadingStatus />
      <Loader2 size={22} className="animate-spin text-on-surface-low" />
    </div>
  )
}

function renderPage(active: string, r: RouteProps) {
  // Every page receives the full RouteProps bundle so its sub-pages, tabs,
  // filters, search, and open-panel state can all be URL-addressable.
  switch (active) {
    case 'dashboard': return <DashboardPage {...r} />
    case 'mission-control': return <MissionControl />
    case 'chat': return <ChatPage {...r} />
    case 'loop': return <LoopSection {...r} />
    case 'loops': return <LoopsSection {...r} />
    case 'code': return <CodeSection {...r} />
    case 'notifications': return <NotificationsPage {...r} />
    case 'discover': return <DiscoverPage {...r} />
    case 'triggers': return <TriggersSection {...r} />
    case 'tasks': return <TasksSection {...r} />
    case 'projects': return <ProjectsSection {...r} />
    case 'knowledge': return <KnowledgeSection {...r} />
    case 'inbox': return <InboxPage {...r} />
    case 'files': return <FilesSection {...r} />
    case 'artifacts': return <ArtifactsSection {...r} />
    case 'terminal': return <TerminalPage {...r} />
    case 'prompts': return <PromptsSection {...r} />
    case 'workflows': return <WorkflowsSection {...r} />
    case 'skills': return <SkillsPage {...r} />
    case 'learning': return <LearningPage />
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
  // Plan-42's `native` notification target (DC-5). Mounted in the shell because it must
  // hold for every route, not just the notifications page: the gateway decides which notes
  // are native and this relays them to the Electron shell, which raises the OS banner and
  // hands back the route on a tap. A no-op in a browser tab — the bell is the fallback.
  useNativeNotifications(navigate)
  // Sound cues need their AudioContext built inside a real user gesture, and the
  // three cue points (turn settled, approval requested, error toast) are none of
  // them. So the shell arms a one-shot primer here and the next click/keypress
  // builds the single context. Does nothing while the toggle is off (Settings →
  // Design → Personality), which is the default.
  useEffect(() => { armCueAudio() }, [])
  // A mobile push arrives silent/vibrate (the SW cannot play audio); an open client plays the
  // per-kind voice it hands back (MOBILE-COMPANION MC-6). Mounted in the shell so any open tab
  // can voice it, not just the notifications page. A no-op where service workers are unavailable.
  useEffect(() => installPushCuePlayback(), [])
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
  // Escape closes the overlay drawer. The scrim tap was its ONLY dismissal, which reads as
  // touch-only-so-no-keyboard — but `useIsMobile` is a `max-width: 768px` MEDIA QUERY, so a narrow
  // DESKTOP window gets the drawer and a real keyboard. Measured at 700px before this: the drawer
  // stayed open (`aria-hidden="false"`) after Escape, with no keyboard way out.
  //
  // `stopPropagation` keeps Escape single-layer, matching `ui/Popover`'s documented contract — the
  // drawer is the outermost layer here, so consuming the key stops one press also closing a panel
  // underneath it.
  useEffect(() => {
    if (!mobileNavOpen) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setMobileNavOpen(false)
    }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [mobileNavOpen])

  // Ambient count of loops actively working in the background — surfaced as a badge
  // on the one Loop nav tile so autonomous runs are visible from any page (the whole
  // point of a loop is it runs while you're elsewhere). ONE poll over ALL kinds
  // (general/goal/code/design) — the union of every kind's active states — so a running
  // General or Design loop is counted too, not just goal+code.
  const [activeLoops, setActiveLoops] = useState(0)
  useVisiblePoll(() => {
    api.uLoops().then((ls) => setActiveLoops(ls.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length)).catch(() => {})
  }, 8000)

  // Installed apps → the Apps nav section. Apps do NOT auto-register: the user
  // opts each one in from its detail panel ("Show in navigation"), persisted via
  // navApps. Only enabled, UI-bearing, user-pinned apps get a nav target
  // (id `app/<name>`) beneath the Store tile. Re-read live on pin changes.
  const { data: installedApps } = useQuery<AppSummary[]>(
    // 🔴 THE POISONER, and the reason this fix spans four files. This hook lives in the SHELL, so it
    // runs on every route before any page mounts. `useQuery` caches by KEY, and `.catch(() => [])`
    // made a failed fetch RESOLVE with an empty list, which the hook then persisted — so
    // `sessionStorage['cache:apps']` read `"[]"` and every other consumer of `'apps'` saw a successful
    // empty result. Measured: with all `/api/apps*` calls at 500, `#/apps` still rendered "No apps
    // installed" even after that surface itself stopped swallowing, because its `data` was `[]` and not
    // `undefined`. The badge below already tolerates `undefined` (it is the pre-fetch state).
    'apps', () => api.apps(), { persist: true },
  )
  // The L1 genui component layer (AMBIENT-SURFACES §5.1): an enabled app that declares
  // `generative-component` + a components module contributes to the HOST registry here,
  // at the shell — so a chat-born widget can name its components without the user having
  // opened that app, and DISABLING the app removes them on the same pass. Refused
  // wholesale in safe mode (the loader checks `maxSurfaceLayer`).
  useEffect(() => {
    if (!installedApps) return
    void syncAppGenUiComponents(installedApps)
  }, [installedApps])
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
      // A staged session keeps its ?seed too (plan 60: investigate pre-fills the
      // composer with an editable opening question — never auto-sent).
      navigate(d.session ? `chat/${encodeURIComponent(d.session)}${q ? `?${q}` : ''}` : `chat/new${q ? `?${q}` : ''}`)
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
  // Widget actions raised OUTSIDE a chat (artifact-library preview, dashboard tile
  // band) route through the same `ne:launch-chat` above. Registered at the shell so
  // every widget host inherits routing; a mounted chat page claims the bridge ahead
  // of this fallback, keeping a chat-born action in its own conversation.
  useWidgetActionLauncher()
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
  //
  // The exit branch honours a destination the flow asked for (OU-3): a try-one card's
  // outcome link and its failure path's Settings deep-link both need to LEAVE the flow
  // and land somewhere specific. They cannot navigate there themselves — the `!onboarded`
  // branch above would pull them straight back, and navigating after committing the name
  // races this effect. So the flow hands the destination over and this one navigation
  // resolves it. Absent (the ordinary finish), the dashboard default is unchanged.
  //
  // `peek` never consumes, because THIS EFFECT IS RE-ENTRANT: `navigate` sets `location.hash`
  // and `route` only catches up on the browser's async `hashchange`, so the exit branch can run
  // again with a stale `route === 'onboarding'`. A consuming read made the second run resolve to
  // the default and overwrite the first run's correct hash — measured live, landing on
  // `#/dashboard` instead of `#/settings/providers`. Peeking makes every run resolve identically;
  // the destination is dropped in the third branch, once the route has provably left onboarding.
  useEffect(() => {
    if (!loaded) return
    if (!onboarded && route !== 'onboarding') navigate('onboarding')
    else if (onboarded && route === 'onboarding') navigate(peekOnboardingExit() || 'dashboard')
    else if (onboarded) clearOnboardingExit()
  }, [loaded, onboarded, route, navigate])

  // ── Progressive disclosure over the rail (ONBOARDING-UX C4) ──
  // Read synchronously from localStorage on mount: no probe, so no flash of the wrong rail,
  // and no record at all resolves to `expert` — the marker for an install that was onboarded
  // before this shipped (see app/navDisclosure.ts).
  const { mode: navMode, pinned: navPinned, setMode: setNavMode, pin: pinNav } = useNavDisclosure()

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

  // Auto-pin on visit. Reaching a surface is the strongest signal it belongs in your rail, so
  // a visit to one the starter rail is holding back REVEALS it permanently. This is what makes
  // hiding safe: a deep link, a CommandPalette "Go to", a Discover tip and an in-app link all
  // render their surface whatever the mode is, and the rail catches up by itself. Nothing is
  // ever reachable ONLY through the rail.
  //
  // Starter mode only, deliberately: in expert mode nothing is hidden, so there is nothing to
  // reveal — and pinning every surface you browsed while expanded would silently empty out
  // "Show fewer" and make the toggle a one-way door.
  //
  // Placed above the early returns (with `rendered`/`active`) so hook order is stable —
  // an effect after them would be conditional (React #310).
  useEffect(() => {
    if (navMode !== 'starter') return
    if (isDisclosed(active, navMode, navPinned)) return
    pinNav(active)
  }, [active, navMode, navPinned, pinNav])

  // Persist the embed flag at mount-time so in-page navigation (which strips the
  // query param from the hash) doesn't lose it. MUST be before any early return so
  // hook order is stable across renders (React #310).
  const embedRef = useRef(query.embed === '1')
  if (query.embed === '1') embedRef.current = true

  // Wait for the server identity before deciding — don't flash onboarding.
  if (!loaded) return <div className="grid h-full place-items-center" style={{ background: 'var(--color-canvas)' }}><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
  if (route === 'onboarding' || !onboarded) return <Onboarding />

  // `#/companion` — the phone companion (MOBILE-COMPANION S2), full-screen with NO NavRail:
  // the whole viewport belongs to the approval waiting on the owner. Same shape as
  // `#/onboarding` above, and deliberately NOT in `ROUTABLE`/`NAV` — it is a deep-link and a
  // PWA start_url, not a desktop nav destination (adding it to NAV would also demand an
  // e2e route-manifest entry per routeManifestParity). Not gated on `useIsMobile`: that is a
  // max-width media query, not a touch test, so gating would make the route undebuggable
  // from a desktop browser without making it any more correct for a phone.
  if (route === 'companion') {
    return (
      <>
        <ErrorBoundary resetKey="companion">
          <Suspense fallback={<PageFallback />}>
            <CompanionPage sub={sub} navigate={navigate} navEpoch={navEpoch} query={query} setQuery={setQuery} />
          </Suspense>
        </ErrorBoundary>
        {/* Outside the boundary: a resolve failure is announced through the toast host, so it
            must survive a page-level crash rather than being replaced by the fallback. */}
        <Toaster />
      </>
    )
  }

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

  // Ambient active-loop count badges the Projects tile (loops live under projects now).
  // APE-7: installed apps with an available update badge the Store nav tile with a count,
  // so an out-of-date app is visible from any page (computed from the same /api/apps
  // read the Library uses — no extra poll). Summed with any SDK-set per-app badges.
  const updatesCount = (installedApps ?? []).filter((a) => a.updateAvailable).length
  const appBadgeTotal = Object.values(appBadges).reduce((a, b) => a + b, 0) + updatesCount
  // Build the rail: static NAV with badges, then splice the dynamic app UI tiles
  // in right after the Store tile (so they sit contiguous under the Apps section
  // header). A per-app badge (set via the SDK setNavBadge) lights its own tile.
  const navItems: NavItem[] = []
  for (const n of NAV) {
    // `badgeLabel` says what the number counts. Supplied ONLY where this shell actually knows the
    // unit: the Projects badge is the active-LOOP count (it read as "1 project" beside five), and
    // the Store badge is app updates — but only while nothing else is summed into it, since an
    // SDK-set per-app badge's unit is app-defined and the shell must not invent one for it.
    if (n.id === 'projects' && activeLoops > 0) {
      navItems.push({ ...n, badge: String(activeLoops), badgeLabel: `${activeLoops} active loop${activeLoops === 1 ? '' : 's'}` })
    } else if (n.id === 'apps' && appBadgeTotal > 0) {
      navItems.push({
        ...n,
        badge: String(appBadgeTotal),
        badgeLabel: appBadgeTotal === updatesCount
          ? `${updatesCount} app update${updatesCount === 1 ? '' : 's'} available`
          : undefined,
      })
    } else navItems.push(n)
    if (n.id === 'apps') {
      for (const ai of appNavItems) {
        const badge = appBadges[ai.id.slice('app/'.length)]
        navItems.push(badge ? { ...ai, badge: String(badge) } : ai)
      }
    }
  }
  // Which rail rows SHOW. Disclosure hides rows; it never removes a route (`rendered` above is
  // untouched by it) and never removes a command below.
  const disclosedItems = navItems.filter((n) => isDisclosed(n.id, navMode, navPinned))
  // What "Everything" would reveal — counted against starter whatever the current mode is, so
  // the expanded rail can say what collapsing costs.
  const moreCount = undisclosedCount(navItems.map((n) => n.id), navPinned)

  // command palette (⌘K): every nav destination as a "Go to" + global actions.
  // Built from the FULL `NAV`, never from `disclosedItems` — the palette is the always-open
  // door to a surface the starter rail is holding back, and visiting through it pins the
  // surface (the auto-pin effect above). Filtering it here would make disclosure a gate.
  const commands: Command[] = [
    ...NAV.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: n.section ?? '', run: () => navigate(n.id) })),
    // pinned app tiles are nav destinations too — same "Go to" contract
    ...appNavItems.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: 'app', run: () => navigate(n.id) })),
    { id: 'go:mission-control', label: 'Mission Control', hint: 'Go to', icon: Radar, keywords: 'attention lanes approvals needs approval your turn working idle', run: () => navigate('mission-control') },
    { id: 'go:notifications', label: 'Notifications', hint: 'Go to', icon: Bell, keywords: 'alerts feed', run: () => navigate('notifications') },
    { id: 'go:discover', label: 'Discover', hint: 'Go to', icon: Compass, keywords: 'tips tour learn features guide', run: () => navigate('discover') },
    { id: 'act:terminal-drawer', label: 'Toggle terminal drawer', hint: 'Action · ⌘`', icon: Terminal, keywords: 'shell pty console', run: () => setTermDrawer((v) => !v) },
    { id: 'act:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: () => navigate('settings') },
  ]
  return (
    <div className="flex h-full" style={{ background: 'var(--color-canvas)' }}>
      <NavRail items={disclosedItems} activeId={active} onSelect={onNavSelect} collapsed={railCollapsed}
        overlay={isMobile} overlayOpen={isMobile && mobileNavOpen} onScrimClick={() => setMobileNavOpen(false)}
        disclosure={{
          expanded: navMode === 'expert',
          moreCount,
          onToggle: () => setNavMode(navMode === 'expert' ? 'starter' : 'expert'),
        }} />
      <main className="relative flex-1 min-w-0">
        {/* App-shell corner regions — float above page content, not a header row */}
        <ShellCornerLeft collapsed={railCollapsed} onToggle={toggleNav} />
        <ShellCornerRight terminalOpen={termDrawer} onToggleTerminal={() => setTermDrawer((v) => !v)} navigate={navigate} />
        {/* Guardrails incident banner — spans the content area on every page while
            incident mode is active (§1.3). Renders nothing otherwise. */}
        <IncidentBanner />
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
      {/* The replayable product tour (ONBOARDING-UX T5.1) — shell-level because its stops
          are shell surfaces (the rail, chat, inbox, the home approvals band, settings) and
          it walks between them. Renders NOTHING until the onboarding done screen or the
          Discover hub's "Replay the tour" card asks for it. */}
      <ProductTour route={rendered} navigate={navigate} />
      {/* The active personality's decorative shell element (PERSONALITY-THEMES §S2).
          Renders NOTHING for the default identity and for every standard scheme;
          under a personality that declares one it mounts an aria-hidden,
          pointer-events-none overlay from the closed SHELL_ELEMENTS registry. */}
      <PersonalityShellElement />
      <Toaster />
      <DialogHost />
      {/* Self-update step progression (WS `update_progress`) — shell-level so the
          modal appears from ANY page while an update pipeline runs. */}
      <UpdateProgressOverlay />
      <TerminalDrawer open={termDrawer} onClose={() => setTermDrawer(false)} onOpenFull={() => { setTermDrawer(false); navigate('terminal') }} />
    </div>
  )
}
