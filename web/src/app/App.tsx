import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { MotionConfig, motion } from 'framer-motion'
import { ease, duration } from '../design/motion'
import { armCueAudio } from '../design/soundCues'
import { installPushCuePlayback } from './pushCuePlayback'
import { Bell, Blocks, BookOpen, Brain, Compass, Database, FileCode, FileText, Files, FolderKanban, Inbox, LayoutDashboard, Library, ListChecks, Loader2, MessageSquare, Plus, Puzzle, Radar, Repeat, Settings, Smartphone, Sparkles, Store, Terminal, Users, Workflow, Wrench, Zap } from 'lucide-react'
import { NavRail, type NavItem } from '../ui/NavRail'
import { ShellThreadRail, threadNumberRoute } from './threadRail'
import { ShellCornerLeft, ShellCornerRight } from '../ui/ShellCorners'
import { IncidentBanner } from './IncidentBanner'
import { DisconnectedBanner } from './DisconnectedBanner'
import { GatewayConnectionProvider } from './gatewayConnection'
import { StatusLine } from './StatusLine'
import { ModelWaits } from '../ui/ModelWaitNotice'
import { SignedOutScreen } from './SignedOutScreen'
import { useSignedOut } from '../lib/signedOut'
import { ChatPage } from '../pages/ChatPage'
import { useIdentity } from './identity'
import { Onboarding } from './Onboarding'
import { peekOnboardingExit, clearOnboardingExit, setOnboardingExit } from './onboarding/exitTo'
import { opensDuringSetup } from './onboarding/openDuringSetup'
import { onSetupRerun } from './onboarding/rerun'
import { ProductTour } from './onboarding/ProductTour'
import { useHashRoute } from './useHashRoute'
import { linkStatePatch, showsNewThread, withLinkState } from './linkState'
import { useIsMobile } from './useIsMobile'
import { useWindowMaterial } from './windowMaterial'
import type { RouteProps } from './useQueryState'
import { ErrorBoundary, WidgetBoundary } from './ErrorBoundary'
import { syncAppGenUiComponents } from './appGenUiLayer'
import { api } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { ACTIVE_LOOP_STATUSES } from '../lib/loopStatus'
import { CommandPalette, keymapCommands, type Command } from './CommandPalette'
import { THREAD_NUMBERS, useBoundRunner, useKeyBinding, type CommandId } from './keymap'
import { nextNeedsYouRoute } from './globalKeys'
import { notify } from './appSdk'
import { TerminalDrawer, fullTerminalRoute } from '../pages/terminal/TerminalDrawer'
import { Toaster } from '../ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import { useNativeNotifications } from '../lib/nativeNotifications'
import { useNotificationToasts } from '../lib/notificationToasts'
import { DialogHost } from '../ui/dialog/DialogHost'
import { PersonalityShellElement } from './personality'
import { UpdateProgressOverlay } from '../ui/UpdateProgressOverlay'
import { runInTerminal, runInTerminalWhenReady, subscribeTerminal, hasActiveTerminal } from '../pages/terminal/terminalBridge'
import { LoadError, LoadingStatus } from '../ui/ListScaffold'
import { useQuery } from '../lib/data'
import { resolveAppIcon } from '../pages/apps/appIcon'
import { useWidgetActionLauncher } from '../ui/widget/useWidgetActionBridge'
import { getNavApps, onNavAppsChange } from '../pages/apps/navApps'
import { isDisclosed, undisclosedCount, useNavDisclosure } from './navDisclosure'
import { ViewModesProvider, useViewModeState } from './viewModes'
import type { AppSummary } from '../lib/api'
import { CapabilitiesSection } from '../pages/capabilities/CapabilitiesSection'
import { isCapabilityRoute } from '../pages/capabilities/capabilityTabs'
import { LibrarySection } from '../pages/library/LibrarySection'
import { isLibraryRoute } from '../pages/library/libraryTabs'

// Heavier / less-frequently-first-viewed pages are code-split so the initial
// chat view loads lean (Monaco, graph SVG, settings panels, etc. load on demand).
const LoopsSection = lazy(() => import('../pages/loops/LoopsSection').then((m) => ({ default: m.LoopsSection })))
const CodeSection = lazy(() => import('../pages/code/CodeSection').then((m) => ({ default: m.CodeSection })))
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const AgentsSection = lazy(() => import('../pages/agents/AgentsSection').then((m) => ({ default: m.AgentsSection })))
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
const MemoryPage = lazy(() => import('../pages/library/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const AppsSection = lazy(() => import('../pages/apps/AppsSection').then((m) => ({ default: m.AppsSection })))
const AppHostPage = lazy(() => import('../pages/apps/AppHostPage').then((m) => ({ default: m.AppHostPage })))
const TerminalPage = lazy(() => import('../pages/terminal/TerminalPage').then((m) => ({ default: m.TerminalPage })))
const DashboardPage = lazy(() => import('../pages/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const MissionControl = lazy(() => import('../pages/dashboard/MissionControl').then((m) => ({ default: m.MissionControl })))
const DiscoverPage = lazy(() => import('../pages/discover/DiscoverPage').then((m) => ({ default: m.DiscoverPage })))
const CompanionPage = lazy(() => import('../pages/companion/CompanionPage').then((m) => ({ default: m.CompanionPage })))

const NAV: NavItem[] = [
  // Primary group (no section header): the Dashboard is the home, then Chat,
  // then Projects, then Library. Library is one destination (#3815): Knowledge, Memory, Files and
  // Artifacts are its tabs, each at its own address (ROUTABLE below).
  { id: 'dashboard', label: 'Home', icon: LayoutDashboard },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'projects', label: 'Projects', icon: FolderKanban },
  { id: 'library', label: 'Library', icon: Library },
  // Platform group (was "Workspace") — Tasks + Triggers moved in here.
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'inbox', label: 'Inbox', icon: Inbox, section: 'Platform' },
  { id: 'triggers', label: 'Triggers', icon: Zap, section: 'Platform' },
  { id: 'terminal', label: 'Terminal', icon: Terminal, section: 'Platform' },
  // Capabilities is one destination (#3817): Agents, Skills, Learning, Tools, Prompts, Apps and the
  // Store are its tabs, each still at its own address (ROUTABLE below). It has no section of its
  // own: a "Capabilities" header over a "Capabilities" row groups nothing. Workflows keeps its row
  // beside it until its own move (#3816).
  { id: 'capabilities', label: 'Capabilities', icon: Puzzle, section: 'Platform' },
  { id: 'workflows', label: 'Workflows', icon: Workflow, section: 'Platform' },
  // The app pages the owner pinned (`navApps`) follow, under their own Apps header: an app's own
  // page is a working surface, not configuration (see appNavItems).
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
// The six pages that are Capabilities' tabs keep their own addresses (`capabilityTabs.ts`), and so
// do Library's four (`libraryTabs.ts`).
const ROUTABLE = new Set([...NAV.map((n) => n.id), 'agents', 'skills', 'learning', 'tools', 'prompts', 'apps', 'knowledge', 'memory', 'files', 'artifacts', 'notifications', 'discover', 'loop', 'loops', 'code', 'app', 'mission-control'])
/** The routes the shell renders ITSELF, full-screen, from an early return above the nav switch —
 *  `#/onboarding` and `#/companion`. Deliberately outside `NAV`/`ROUTABLE` (neither is a rail
 *  destination) and every bit as real as the ones inside it.
 *
 *  🔴 The URL corrector MUST consult this as well as `ROUTABLE` (#3506). Testing `ROUTABLE` alone
 *  made it treat both as typos, which measured as two dead ends: `#/companion` — the PWA
 *  `start_url` — silently settling on `#/dashboard`, and EVERY onboarding exit deep-link landing
 *  on the dashboard, because the correction fires on the handoff commit while `route` is still the
 *  stale `'onboarding'`. */
const SHELL_ROUTES = new Set(['onboarding', 'companion'])
/** The phone's own surface, as a row of the rail's DRAWER — the phone form only (`railItems`).
 *  `#/companion` is a shell route rendered full-screen, not a desktop destination, so it stays out
 *  of `NAV`; without this row a phone that left it for the full dashboard (its footer's "Open the
 *  full dashboard") could come back only by typing its address. Pinned beside Settings: it is
 *  where the device is, not a section of the product. */
const COMPANION_RAIL_ITEM: NavItem = { id: 'companion', label: 'Companion', icon: Smartphone, pinBottom: true }
/** Is there anything the shell can render for this hash — by the nav switch or by an early
 *  return? The corrector's question, and the reason it is not `ROUTABLE.has`. */
const renderable = (route: string): boolean => ROUTABLE.has(route) || SHELL_ROUTES.has(route)

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
    case 'discover': return <DiscoverPage {...r} />
    case 'triggers': return <TriggersSection {...r} />
    case 'tasks': return <TasksSection {...r} />
    case 'projects': return <ProjectsSection {...r} />
    case 'knowledge': return <KnowledgeSection {...r} />
    // Library's own address opens its first tab. After the tab's own case, so the route tables the
    // censuses read place each page on its tab's route.
    case 'library': return <KnowledgeSection {...r} />
    case 'memory': return <MemoryPage {...r} />
    case 'inbox': return <InboxPage {...r} />
    // Notifications are a filter of the Inbox (#3818). `#/notifications` and its keys are retired to
    // it (`routeRedirects.ts`), so the router never hands this case that address, only a deeper path
    // no link wrote. The route stays routable for the links that still name it, `personalclaw://`
    // and a native notification's tap among them, which the redirect then carries.
    case 'notifications': return <InboxPage {...r} />
    case 'files': return <FilesSection {...r} />
    case 'artifacts': return <ArtifactsSection {...r} />
    case 'terminal': return <TerminalPage {...r} />
    case 'prompts': return <PromptsSection {...r} />
    case 'workflows': return <WorkflowsSection {...r} />
    case 'skills': return <SkillsPage {...r} />
    case 'learning': return <LearningPage {...r} />
    case 'tools': return <ToolsPage {...r} />
    case 'agents': return <AgentsSection {...r} />
    // Capabilities' own address opens its first tab. After the tab's own case, so the route tables
    // the censuses read place each page on its tab's route.
    case 'capabilities': return <AgentsSection {...r} />
    case 'apps': return <AppsSection {...r} />
    case 'app': return <AppHostPage {...r} />
    case 'settings': return <SettingsPage {...r} />
    // Unreachable by construction: every ROUTABLE member has a case above, and an UNKNOWN route
    // never arrives here because `rendered` clamps it (and the effect then corrects the URL).
    // `routeCasesCoverRoutable.test.ts` is what keeps that true.
    //
    // It said "<label> — coming soon", which was wrong twice over: for an unknown route the label
    // resolved to `undefined` (rendering a bare " — coming soon"), and for a ROUTABLE route with a
    // missing case it told the user a feature was PLANNED when the truth is a missing branch. The
    // switch still needs a fallback for its return type, so it says something true instead.
    default: return <div className="flex h-full items-center justify-center text-on-surface-low" data-type="headline-s">This view isn’t available.</div>
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
  // A tab whose session ended shows ONE signed-out screen, with the gateway's sentence, in place of
  // the whole shell — which unmounts every panel, poll and socket that could only be refused.
  const signedOut = useSignedOut()
  return (
    <MotionConfig reducedMotion="user">
      {signedOut ? <SignedOutScreen notice={signedOut} /> : <AppInner />}
    </MotionConfig>
  )
}

function AppInner() {
  // The Dashboard is the home: the app lands on the at-a-glance home (nav #0).
  const { route, sub, navEpoch, navigate, query: addressQuery, setQuery } = useHashRoute('dashboard')
  // A link's own words for chat state (`prompt`, `pane`) read as the chat's keys from the first
  // render, so a new thread mounts with its draft; the effect then writes them into the address, in
  // place. A new thread reads its draft only as it mounts, so when the link reaches a window that
  // was already showing one, the rewrite remounts it (as `navigate` does) and its composer takes
  // the draft the address now names, in place of the one it held.
  const linkPatch = linkStatePatch(route, sub, addressQuery)
  const query = linkPatch ? withLinkState(addressQuery, linkPatch) : addressQuery
  const linkPatchKey = linkPatch ? JSON.stringify(linkPatch) : ''
  const onNewThread = showsNewThread(route, sub)
  const wasOnNewThread = useRef(false)
  useEffect(() => {
    if (linkPatch) setQuery(linkPatch, { replace: true, remount: 'seed' in linkPatch && wasOnNewThread.current })
  }, [route, sub, linkPatchKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { wasOnNewThread.current = onNewThread })
  // Out-of-context approval nudges: toast when a tool-approval (e.g. a subagent's)
  // is raised for a chat the user isn't currently viewing. The active chat key is
  // `sub` on the chat route (excluding the new/history list routes AND the room
  // sub-route — `chat/room/<id>` addresses an Agent Room, not a session key, so
  // treating it as one would suppress every approval toast while a room is open by
  // claiming a session named "room/<id>" is on screen).
  const activeChatSession = route === 'chat' && sub && sub !== 'new' && sub !== 'history' && !sub.startsWith('room/') && sub !== 'room' ? sub : ''
  useApprovalToasts(activeChatSession)
  // The `native` notification target. Mounted in the shell because it must
  // hold for every route, not just the notifications page: the gateway decides which notes
  // are native and this relays them to the Electron shell, which raises the OS banner and
  // hands back the route on a tap. A no-op in a browser tab — the bell is the fallback.
  useNativeNotifications(navigate)
  // The `immediate` mode's toast (#343). Mounted beside the native relay because it is the same
  // shape of thing — the gateway has already decided the note interrupts, and this is the surface
  // that acts on it — and because the matrix's promise ("Notify = a toast") has to hold on every
  // route, not only where a notification list happens to be mounted.
  useNotificationToasts()
  // Sound cues need their AudioContext built inside a real user gesture, and the
  // three cue points (turn settled, approval requested, error toast) are none of
  // them. So the shell arms a one-shot primer here and the next click/keypress
  // builds the single context. Does nothing while the toggle is off (Settings →
  // Design → Personality), which is the default.
  useEffect(() => { armCueAudio() }, [])
  // A mobile push arrives silent/vibrate (the SW cannot play audio); an open client plays the
  // per-kind voice it hands back. Mounted in the shell so any open tab
  // can voice it, not just the notifications page. A no-op where service workers are unavailable.
  useEffect(() => installPushCuePlayback(), [])
  const { onboarded, status: identityStatus, error: identityError, retry: retryIdentity } = useIdentity()
  const [navCollapsed, setNavCollapsed] = useState(() => localStorage.getItem(NAV_COLLAPSED_KEY) === '1')
  useEffect(() => { localStorage.setItem(NAV_COLLAPSED_KEY, navCollapsed ? '1' : '0') }, [navCollapsed])
  // Mobile: the rail defaults COLLAPSED and expands as an overlay DRAWER (it must not
  // squeeze the page like the in-flow desktop rail). The shell toggle opens/closes the
  // drawer; picking a nav target closes it again. `mobileNavOpen` is the drawer state
  // (never persisted — always starts closed on a fresh mobile load).
  const isMobile = useIsMobile()
  // The desktop window's sidebar material: the shell root and the rail go clear over it, and the
  // work surface (`<main>`) keeps the canvas, so only the sidebar is translucent.
  const material = useWindowMaterial()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  // Effective collapse: on mobile the rail is collapsed unless the drawer is open;
  // on desktop it follows the persisted user preference.
  const railCollapsed = isMobile ? !mobileNavOpen : navCollapsed
  // Focus mode and reading mode (`viewModes.tsx`). Focus mode hides the rail on a desktop; a phone's
  // rail is a drawer that costs the page nothing, so there it only closes the side pane. While the
  // rail is hidden, the sidebar's control is the way out of focus mode, and the rail comes back as
  // it was.
  const viewModes = useViewModeState()
  const { focus, setFocus, setReading } = viewModes
  const railHidden = focus && !isMobile
  const toggleNav = () => { if (isMobile) setMobileNavOpen((v) => !v); else if (railHidden) setFocus(false); else setNavCollapsed((v) => !v) }
  // Reading mode belongs to the chat on screen, so a move to another page or chat ends it, and its
  // key and palette row are there only while a chat is.
  useEffect(() => { setReading(false) }, [route, sub, setReading])
  const readsAChat = activeChatSession !== ''
  const toggleFocus = () => setFocus((v) => !v)
  const toggleReading = () => setReading((v) => !v)
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
  // General or Design loop is counted too, not just goal+code. Every 30s: an ambient count
  // is not a live view, and at 8s this was one of THREE readers of `/api/loops` on Home.
  const [activeLoops, setActiveLoops] = useState(0)
  useVisiblePoll(() => {
    api.uLoops().then((ls) => setActiveLoops(ls.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length)).catch(() => {})
  }, 30_000)

  // Installed apps → the Apps nav section. Apps do NOT auto-register: the user
  // opts each one in from its detail panel ("Show in navigation"), persisted via
  // navApps. Only enabled, UI-bearing, user-pinned apps get a nav target
  // (id `app/<name>`) beneath the Store tile. Re-read live on pin changes.
  const { data: installedApps } = useQuery<AppSummary[]>(
    // 🔴 THE POISONER, and the reason this fix spans four files. This hook lives in the SHELL, so it
    // runs on every route before any page mounts. `useQuery` caches by KEY, and `.catch(() => [])`
    // made a failed fetch RESOLVE with an empty list, which the hook then persisted — so
    // `sessionStorage['cache:apps']` read `"[]"` and every other consumer of `'apps'` saw a successful
    // empty result. With all `/api/apps*` calls at 500, `#/apps` still rendered "No apps
    // installed" even after that surface itself stopped swallowing, because its `data` was `[]` and not
    // `undefined`. The badge below already tolerates `undefined` (it is the pre-fetch state).
    'apps', () => api.apps(), { persist: true },
  )
  // The L1 genui component layer: an enabled app that declares
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
    // An app the gateway does not run here (`refused`) is off as far as the shell is concerned.
    .filter((a) => a.enabled && !a.refused && a.hasUI && (a.uiPages?.length ?? 0) > 0 && navAppSet.includes(a.name))
    .map((a) => ({
      id: `app/${a.name}`,
      label: a.uiPages[0].label || a.displayName,
      icon: resolveAppIcon(a.uiPages[0].icon || a.icon),
      section: 'Apps',
    }))

  // Contributed-app SDK events: launch a chat, badge an app's nav tile.
  const [appBadges, setAppBadges] = useState<Record<string, number>>({})
  useEffect(() => {
    const onLaunch = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      const qs = new URLSearchParams()
      if (d.prompt) qs.set('seed', d.prompt)
      if (d.agent) qs.set('agent', d.agent)
      const q = qs.toString()
      // A staged session keeps its ?seed too (investigate pre-fills the
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
  // The terminal drawer, on every page: ⌘J or ⌘`, the corner's toggle and the palette.
  const [termDrawer, setTermDrawer] = useState(false)
  // a command queued by "Run in terminal" while no terminal was live yet — sent
  // once a session registers (the drawer opens, its TerminalView registers).
  const pendingRun = useRef<string | null>(null)
  useEffect(() => {
    // "Run in terminal" from a chat code block: open the drawer, queue the cmd.
    const onRun = (e: Event) => {
      const cmd = (e as CustomEvent).detail?.command
      if (typeof cmd !== 'string' || !cmd) return
      if (hasActiveTerminal() && runInTerminal(cmd)) return
      pendingRun.current = cmd
      setTermDrawer(true)
    }
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
    return () => { window.removeEventListener('ne:run-in-terminal', onRun as EventListener); unsub() }
  }, [])

  // A deliberate re-run of first-run setup, asked for from inside the shell (Settings → Account).
  //
  // The guard below is the single owner of where a user goes with respect to this flow, and it
  // decides from `onboarded` — a variable that cannot express "already set up, asked to do it
  // again". So the request is a SECOND INPUT to the same guard rather than a navigation racing it,
  // and the flow's `onFinished` withdrawing the request is the state change that lets the guard put
  // the user back. Before this, the only re-entry door CLEARED the operator's name to force the
  // guard's hand (`clearName`), which is a destructive way to reach a read-only screen.
  const [setupRerun, setSetupRerun] = useState(false)
  // `[]` deps on purpose: the callback only ever sets state, so the subscription never needs to be
  // torn down and rebuilt as `navigate`'s identity churns each render.
  useEffect(() => onSetupRerun(() => setSetupRerun(true)), [])

  // ── ONE effect decides where the browser must be. ────────────────────────────────────────────
  //
  // Onboarding is a real route (#/onboarding/<step>), full-screen, no NavRail: this redirects TO it
  // while setup is WANTED and AWAY from it once it is not. "Wanted" is `!onboarded || setupRerun`,
  // not `!onboarded` — a deliberate re-run is an onboarded user who belongs in the flow, so the
  // request is a second input to this guard rather than a navigation racing it. The exit branch
  // honours a destination the flow asked for: a try-one card's outcome link and its failure
  // path's Settings deep-link both need to LEAVE the flow and land somewhere specific. They cannot
  // navigate there themselves — the redirect branch would pull them straight back, and navigating
  // after committing the name races this effect. So the flow hands the destination over and this one
  // navigation resolves it. Absent (the ordinary finish), the dashboard default is unchanged.
  //
  // The REDIRECT branch hands a destination over in the other direction too. A fresh home pulls
  // every route here, so a user who asked for `#/settings/providers` got a different screen with
  // nothing to say why — indistinguishable from the app swallowing the click. Recording the route
  // they asked for turns the redirect into a deferral the flow names and then keeps. What it records
  // is the address, query and all, as the router hands it over (a retired one already moved to where
  // it opens now): a page keeps its view in the query, so the path alone landed on another view.
  // `#/notifications` opens as `inbox?filter=notifications`, and kept as `inbox` it landed on the
  // Inbox's items, not the feed.
  //
  // `peek` never consumes, because THIS EFFECT IS RE-ENTRANT: `navigate` sets `location.hash` and
  // `route` only catches up on the browser's async `hashchange`, so the exit branch can run again
  // with a stale `route === 'onboarding'`. A consuming read made the second run resolve to the
  // default and overwrite the first run's correct hash — measured live, landing on `#/dashboard`
  // instead of `#/settings/providers`. Peeking makes every run resolve identically; the destination
  // is dropped in the last branch, once the route has provably left onboarding.
  //
  // 🔴 The unknown-hash correction (#306) is the LAST branch of this same effect, not a second
  // effect, and that is the fix for #3506. As two effects it could only be ordered by a gate, and
  // every gate tried described the wrong window: `loaded && onboarded` fired the corrector on the
  // handoff commit, and tightening it to `|| route === 'onboarding'` still left the pair ordered by
  // a predicate over two states that change on different ticks. At the instant setup stops being
  // wanted, `route` is STILL the stale `'onboarding'`, so both effects ran on one commit — the guard
  // navigated to the handed-over destination and the corrector `replace`d it with the dashboard.
  // Measured from both ends: a fresh home asked for `#/settings/providers`, the flow named the
  // deferral on screen, and the landing was `#/dashboard` — one synchronous
  // `history.replaceState('#/dashboard')` beating the guard's asynchronous `hashchange`. Precedence
  // now holds by construction rather than by predicate: while there is a route decision to make,
  // correction cannot run, because it is downstream of `return`.
  //
  // `replace: true` on that branch is load-bearing: a push would leave the bogus hash in history,
  // so Back would return the user to the broken URL they were just rescued from — and each Back
  // press would re-run this effect, bouncing them forward again.
  //
  // 🔴 Nothing is decided until the stored identity has been READ. Waiting for the read to SETTLE
  // was not enough: a failed read settled as "no name", this effect sent the user to setup, and a
  // first-run Skip there wrote the fallback name over the real one. A failed read is `failed`, and
  // the retry screen below owns the page until a read succeeds.
  const addressSearch = new URLSearchParams(addressQuery).toString()
  useEffect(() => {
    if (identityStatus !== 'ready') return
    const wantsSetup = !onboarded || setupRerun
    if (wantsSetup) {
      // 🔑 THE PAGES THAT CUT SOMETHING OFF ARE NEVER HELD (`onboarding/openDuringSetup`): signing a
      // device out or suspending unattended work runs on nothing setup asks, and cannot wait for it.
      // No redirect, and no destination recorded, since nothing was deferred; one recorded earlier
      // stays pending, because a visit here is not the end of setup.
      if (opensDuringSetup(route, sub)) return
      if (route !== 'onboarding') {
        if (ROUTABLE.has(route) && route !== 'dashboard') {
          const path = [route, sub].filter(Boolean).join('/')
          setOnboardingExit(addressSearch ? `${path}?${addressSearch}` : path)
        }
        // 🔴 REPLACE FOR A FIRST RUN, PUSH FOR A RE-RUN. A push here made the browser's Back button a
        // bounce: Back left `#/onboarding`, this effect fired on the new route and pushed
        // `#/onboarding` straight back on top. Measured on a fresh home — Back flashed `#/dashboard`,
        // returned to `#/onboarding`, and `history.length` stayed pinned at 3, so the user could not
        // leave and could not go back. Replacing leaves the flow's own step pushes as the only in-flow
        // history, so Back walks the steps and Back from step 1 exits the app. A deliberate re-run is
        // the opposite case: the user came from a real page, so pushing is what lets Back return them.
        navigate('onboarding', { replace: !onboarded })
      }
      // Setup owns the route, so NOTHING below may run — least of all `clearOnboardingExit()`.
      // `exitTo` sets the destination and THEN commits the name, so this effect can run in between
      // with setup still wanted; clearing here would drop the destination before the branch that
      // reads it ever ran.
      return
    }
    if (route === 'onboarding') { navigate(peekOnboardingExit() || 'dashboard'); return }
    // `renderable`, not `ROUTABLE.has` — `#/companion` is rendered by an early return below and is
    // the PWA's `start_url`, so correcting it away made an installed app open on the wrong surface.
    if (route && !renderable(route)) { navigate('dashboard', { replace: true }); return }
    clearOnboardingExit()
  }, [identityStatus, onboarded, setupRerun, route, sub, addressSearch, navigate])

  // ── Progressive disclosure over the rail ──
  // Read synchronously from localStorage on mount: no probe, so no flash of the wrong rail,
  // and no record at all resolves to `expert` — the marker for an install that was onboarded
  // before this shipped (see app/navDisclosure.ts).
  const { mode: navMode, pinned: navPinned, setMode: setNavMode, pin: pinNav } = useNavDisclosure()

  // The REAL route to render (loops/code keep their own sections for detail/history/
  // planning sub-routes; only the BARE route was folded into the #/loop composer).
  // An unknown route falls back to the home dashboard FOR THIS RENDER, and the route effect above
  // then corrects the URL (#306). The clamp alone left `#/nonsense` in the address bar with the
  // dashboard underneath it — so a stale bookmark, a typo, or a link from an older version all
  // looked like the dashboard had simply moved there, with nothing to tell the user which.
  //
  // `ROUTABLE.has`, deliberately NOT `renderable`: this picks a case for the nav switch, and a
  // SHELL_ROUTES member never reaches the switch — it returns early below. Widening it here would
  // hand the switch a route with no case.
  const rendered = ROUTABLE.has(route) ? route : 'dashboard'
  // The nav-HIGHLIGHT route: loops launch from within Projects, so a bare #/loop
  // composer or a #/loops/<id> / #/code/<id> deep-link lights the Projects tile.
  // Highlight-only — must NOT change which page renders.
  const active = (rendered === 'loop' || rendered === 'loops' || rendered === 'code') ? 'projects'
    : rendered === 'app' ? `app/${(sub ?? '').split('/')[0]}`  // light the specific app tile
      : isCapabilityRoute(rendered) ? 'capabilities'  // each tab lights its destination
        : isLibraryRoute(rendered) ? 'library'
          : rendered
  // The destination drawn: a tab of Capabilities is Capabilities, and a tab of Library is Library,
  // so moving between a destination's tabs keeps its strip (and the focus on it) where it is, and
  // only the page under it changes.
  const surface = isCapabilityRoute(rendered) ? 'capabilities' : isLibraryRoute(rendered) ? 'library' : rendered
  // Whether the sidebar lights a thread, the one the address opens (`threadRail.tsx`). While one is
  // lit, the destination its route belongs to (Chat, Projects) is not lit beside it: one row says
  // where you are. A hook, so it sits above the early returns (React #310).
  const [threadLit, setThreadLit] = useState(false)

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

  // The keymap's global rows (`keymap.ts`), each run where the thing it moves lives. Hooks, so
  // they sit above every early return (React #310). ⌘⌥A reads the lists fresh when pressed, so it
  // follows what Home shows now; ⌘1 to ⌘9 open the rows the sidebar shows (`threadNumberRoute`),
  // read fresh while no rail shows them.
  const toggleDrawer = () => setTermDrawer((v) => !v)
  const goNeedsYou = () => {
    Promise.all([api.inboxOpen(), api.approvals()])
      .then(([items, approvals]) => {
        const to = nextNeedsYouRoute(items, approvals)
        if (to) navigate(to)
        else notify('Nothing is waiting on you.', 'info')
      })
      .catch(() => notify("Couldn't read what is waiting on you.", 'error'))
  }
  const goThread = (n: number) => {
    threadNumberRoute(n)
      .then((to) => {
        if (to) onNavSelect(to)
        else notify(`There is no thread ${n} in the sidebar.`, 'info')
      })
      .catch(() => notify("Couldn't read your threads.", 'error'))
  }
  // Persist the embed flag at mount-time so in-page navigation (which strips the
  // query param from the hash) doesn't lose it. MUST be before any early return so
  // hook order is stable across renders (React #310).
  const embedRef = useRef(query.embed === '1')
  if (query.embed === '1') embedRef.current = true
  // ⌘⇧E's runner is the open chat's, which shows and hides its working folder (`chat/panes`); the
  // palette runs it while a chat is open and lists it dimmed elsewhere.
  const chatTree = useBoundRunner('workspace-tree')
  const keyRunners: Partial<Record<CommandId, () => void>> = {
    sidebar: toggleNav,
    drawer: toggleDrawer,
    // The two modes change the shell, which an embedded page has none of; reading mode folds a
    // chat's composer, so it runs only where a chat is on screen.
    ...(embedRef.current ? {} : { 'focus-mode': toggleFocus, ...(readsAChat ? { 'reading-mode': toggleReading } : {}) }),
    'workspace-tree': chatTree,
    'next-needs-you': goNeedsYou,
    'new-chat': () => navigate('chat/new'),
    ...Object.fromEntries(THREAD_NUMBERS.map((n) => [`chat-${n}`, () => goThread(n)])),
  }
  useKeyBinding('sidebar', () => keyRunners.sidebar?.())
  useKeyBinding('drawer', () => keyRunners.drawer?.())
  useKeyBinding('focus-mode', keyRunners['focus-mode'])
  useKeyBinding('reading-mode', keyRunners['reading-mode'])
  useKeyBinding('next-needs-you', () => keyRunners['next-needs-you']?.())
  useKeyBinding('new-chat', () => keyRunners['new-chat']?.())
  useKeyBinding('chat-1', () => goThread(1))
  useKeyBinding('chat-2', () => goThread(2))
  useKeyBinding('chat-3', () => goThread(3))
  useKeyBinding('chat-4', () => goThread(4))
  useKeyBinding('chat-5', () => goThread(5))
  useKeyBinding('chat-6', () => goThread(6))
  useKeyBinding('chat-7', () => goThread(7))
  useKeyBinding('chat-8', () => goThread(8))
  useKeyBinding('chat-9', () => goThread(9))


  // Wait for the server identity before deciding — don't flash onboarding. `PageFallback` rather
  // than a bare spinner, so a Retry below is followed by an announced "Loading", not by silence.
  if (identityStatus === 'loading') return <div className="h-full" style={{ background: 'var(--color-canvas)' }}><PageFallback /></div>
  // 🔴 A FAILED READ OF WHO YOU ARE GETS A RETRY — NEITHER SETUP NOR THE APP. Either would be a guess
  // about a home nobody has read, and setup's guess is destructive: its first-run Skip commits a
  // name over whatever is stored. No toast host here, because nothing on this screen writes.
  if (identityStatus === 'failed') {
    return (
      <div className="grid h-full place-items-center overflow-y-auto px-l" style={{ background: 'var(--color-canvas)' }}>
        <LoadError what="account" error={identityError} onRetry={retryIdentity} />
      </div>
    )
  }
  // A page setup never holds (`onboarding/openDuringSetup`), while setup holds every other one: on a
  // home whose setup is not done, and during a deliberate re-run, which holds them the same way.
  // Full-screen like the flow, with no rail: every rail destination but these would only lead back
  // into setup, so the page itself names the way back and the other pages like it (`duringSetup`).
  // It carries the incident banner, since one of these pages is where that switch is turned on, and
  // the toast and dialog hosts: every control here asks before it acts and reports how it went, and a
  // confirmation with no host to render it never resolves, which leaves the control doing nothing.
  // No shell corners are drawn, so the page header keeps none of their room (as in embed mode).
  if ((!onboarded || setupRerun) && opensDuringSetup(route, sub)) {
    return (
      <div className="flex h-full flex-col" style={{
        background: 'var(--color-canvas)',
        '--shell-corner-l': '0px',
        '--shell-corner-lh': '0px',
        '--shell-corner-r': '0px',
        '--shell-corner-rh': '0px',
      } as React.CSSProperties}>
        <IncidentBanner />
        <main className="min-h-0 flex-1">
          <ErrorBoundary resetKey={sub}>
            <Suspense fallback={<PageFallback />}>
              <SettingsPage sub={sub} navigate={navigate} navEpoch={navEpoch} query={query} setQuery={setQuery} duringSetup />
            </Suspense>
          </ErrorBoundary>
        </main>
        <Toaster />
        <DialogHost />
      </div>
    )
  }

  // `sub` is the step's slug, `navigate` makes each step a history entry, and `onFinished` withdraws
  // a re-run request so the guard above — still the only navigator — moves the user out.
  //
  // 🔴 THE TOAST HOST COMES WITH IT. This branch returned the flow ALONE, so `<Toaster />` was not
  // mounted anywhere on the first screen of the product and every `notify()` inside onboarding fired
  // into nothing — a reported failure with no surface to report on. Measured after fixing the
  // erased-name dead end: with the gateway killed, the flow correctly kept the user on their step
  // with their draft, and the explanation it raised was still invisible. Two existing callers were
  // silently inert the same way (the done screen's auto-update switch, and its Store-source link's
  // failure path). Same shape as `#/companion` below, for the same reason.
  if (route === 'onboarding' || !onboarded) {
    return (
      <>
        {/* The first screen of the product gets the same route boundary as every other page: a
            step that throws shows "This page hit an error" with Retry, never an empty body. */}
        <ErrorBoundary resetKey={sub}>
          <Onboarding sub={sub} navigate={navigate} deferred={peekOnboardingExit()} onFinished={() => setSetupRerun(false)} />
        </ErrorBoundary>
        <Toaster />
      </>
    )
  }

  // `#/companion` — the phone companion, full-screen with NO NavRail:
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
        '--shell-corner-lh': '0px',
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
  // Installed apps with an available update badge the Capabilities tile, where the Apps tab is, with
  // a count, so an out-of-date app is visible from any page (computed from the same /api/apps
  // read the Library uses — no extra poll). Summed with any SDK-set per-app badges.
  const updatesCount = (installedApps ?? []).filter((a) => a.updateAvailable).length
  const appBadgeTotal = Object.values(appBadges).reduce((a, b) => a + b, 0) + updatesCount
  // Build the rail: static NAV with badges, then splice the dynamic app UI tiles in after the
  // destinations, above Settings (so they sit contiguous under their Apps section header). A
  // per-app badge (set via the SDK setNavBadge) lights its own tile.
  const navItems: NavItem[] = []
  for (const n of NAV) {
    // `badgeLabel` says what the number counts. Supplied ONLY where this shell actually knows the
    // unit: the Projects badge is the active-LOOP count (it read as "1 project" beside five), and
    // the Store badge is app updates — but only while nothing else is summed into it, since an
    // SDK-set per-app badge's unit is app-defined and the shell must not invent one for it.
    if (n.id === 'projects' && activeLoops > 0) {
      navItems.push({ ...n, badge: String(activeLoops), badgeLabel: `${activeLoops} active loop${activeLoops === 1 ? '' : 's'}` })
    } else if (n.id === 'capabilities' && appBadgeTotal > 0) {
      navItems.push({
        ...n,
        badge: String(appBadgeTotal),
        badgeLabel: appBadgeTotal === updatesCount
          ? `${updatesCount} app update${updatesCount === 1 ? '' : 's'} available`
          : undefined,
      })
    } else navItems.push(n)
  }
  const bottom = navItems.findIndex((n) => n.pinBottom)
  navItems.splice(bottom < 0 ? navItems.length : bottom, 0, ...appNavItems.map((ai) => {
    const badge = appBadges[ai.id.slice('app/'.length)]
    return badge ? { ...ai, badge: String(badge) } : ai
  }))
  // Which rail rows SHOW. Disclosure hides rows; it never removes a route (`rendered` above is
  // untouched by it) and never removes a command below.
  const disclosedItems = navItems.filter((n) => isDisclosed(n.id, navMode, navPinned))
  // The phone form's drawer also carries the companion, ahead of Settings (`COMPANION_RAIL_ITEM`).
  const railItems = isMobile
    ? [...disclosedItems.filter((n) => !n.pinBottom), COMPANION_RAIL_ITEM, ...disclosedItems.filter((n) => n.pinBottom)]
    : disclosedItems
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
    // A filter of the Inbox, not a page of its own (#3818).
    { id: 'go:notifications', label: 'Notifications', hint: 'Go to', icon: Bell, keywords: 'alerts feed inbox', run: () => navigate('inbox?filter=notifications') },
    { id: 'go:discover', label: 'Discover', hint: 'Go to', icon: Compass, keywords: 'tips tour learn features guide', run: () => navigate('discover') },
    // Capabilities' tabs, each a row of its own: they left the sidebar for the one Capabilities row.
    { id: 'go:agents', label: 'Agents', hint: 'Go to', icon: Users, keywords: 'Capabilities', run: () => navigate('agents') },
    { id: 'go:skills', label: 'Skills', hint: 'Go to', icon: Sparkles, keywords: 'Capabilities', run: () => navigate('skills') },
    { id: 'go:learning', label: 'Learning', hint: 'Go to', icon: Brain, keywords: 'Capabilities proposals lessons evals', run: () => navigate('learning') },
    { id: 'go:tools', label: 'Tools', hint: 'Go to', icon: Wrench, keywords: 'Capabilities tool servers mcp', run: () => navigate('tools') },
    { id: 'go:prompts', label: 'Prompts', hint: 'Go to', icon: FileText, keywords: 'Capabilities', run: () => navigate('prompts') },
    { id: 'go:apps', label: 'Apps', hint: 'Go to', icon: Blocks, keywords: 'Capabilities library installed', run: () => navigate('apps') },
    { id: 'go:store', label: 'Store', hint: 'Go to', icon: Store, keywords: 'Capabilities apps install browse', run: () => navigate('apps?view=store') },
    { id: 'act:add-tool-server', label: 'Add a tool server', hint: 'Action', icon: Plus, keywords: 'Capabilities tools mcp server connect', run: () => navigate('tools?add=1') },
    // Library's tabs, each a row of its own: they left the sidebar (and Settings) for the one Library row.
    { id: 'go:knowledge', label: 'Knowledge', hint: 'Go to', icon: BookOpen, keywords: 'Library notes documents sources reports graph', run: () => navigate('knowledge') },
    { id: 'go:memory', label: 'Memory', hint: 'Go to', icon: Database, keywords: 'Library facts episodes lessons recall audit remember', run: () => navigate('memory') },
    { id: 'go:files', label: 'Files', hint: 'Go to', icon: Files, keywords: 'Library workspace home outbox explorer', run: () => navigate('files') },
    { id: 'go:artifacts', label: 'Artifacts', hint: 'Go to', icon: FileCode, keywords: 'Library made reports outputs', run: () => navigate('artifacts') },
    // The loop composer has no rail tile (loops start from Projects), so this row is its way in.
    { id: 'act:start-loop', label: 'Start a loop', hint: 'Action', icon: Repeat, keywords: 'loop goal code design autonomous plan run new', run: () => navigate('loop') },
    { id: 'act:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: () => navigate('settings') },
    // Every keyboard shortcut, from the keymap: the drawer, the sidebar, focus and reading mode, the
    // threads, what needs you, and the keys a focused control handles, listed with why they don't run
    // from here.
    ...keymapCommands(keyRunners),
  ]
  return (
    // One reading of the gateway connection for the status line, its banner and the System status
    // card under it.
    <GatewayConnectionProvider>
      <div className="flex h-full flex-col" style={{ background: material ? 'transparent' : 'var(--color-canvas)' }}>
        <div className="flex min-h-0 flex-1">
          {/* The rail sits in a boundary, as each corner control does in ShellCorners: one that throws
              leaves a notice in its place, and the page beside it and ⌘K keep working. */}
          {!railHidden && (
            <WidgetBoundary what="the navigation" compact className="m-s self-start">
              <NavRail items={railItems} activeId={threadLit ? '' : active} onSelect={onNavSelect} collapsed={railCollapsed}
                overlay={isMobile} overlayOpen={isMobile && mobileNavOpen} onScrimClick={() => setMobileNavOpen(false)} material={material}
                threads={(
                  // Its own boundary: threads that throw leave a notice in their place, and the
                  // destinations around them keep working.
                  <WidgetBoundary what="your threads" className="mx-s">
                    <ShellThreadRail route={route} sub={sub} onOpen={onNavSelect} onLit={setThreadLit} />
                  </WidgetBoundary>
                )}
                disclosure={{
                  expanded: navMode === 'expert',
                  moreCount,
                  onToggle: () => setNavMode(navMode === 'expert' ? 'starter' : 'expert'),
                }} />
            </WidgetBoundary>
          )}
          <main className="relative flex-1 min-w-0" style={material ? { background: 'var(--color-canvas)' } : undefined}>
            {/* App-shell corner regions — float above page content, not a header row */}
            <ShellCornerLeft collapsed={railCollapsed} onToggle={toggleNav} focusMode={railHidden} />
            <ShellCornerRight terminalOpen={termDrawer} onToggleTerminal={() => setTermDrawer((v) => !v)} navigate={navigate} />
            {/* Guardrails incident banner — spans the content area on every page while
                incident mode is active. Renders nothing otherwise. */}
            <IncidentBanner />
            {/* A request a page is waiting for while the local model it needs is busy: why, and what
                happens next. Over the page, below the corner band (TopBar's own clearance), rather than
                in its flow, so nothing moves under you for the few seconds it shows. A chat's own
                waits show in that chat. A lost connection's banner shows here too, for the same reason:
                a restart is usually back in seconds. */}
            <div className="pointer-events-none absolute inset-x-0 z-30 flex flex-col gap-s px-l"
              style={{ top: 'max(var(--shell-corner-lh, 52px), var(--shell-corner-rh, 44px))' }}>
              <DisconnectedBanner className="mx-auto w-full max-w-[36rem]" />
              <ModelWaits session="" floating className="mx-auto w-full max-w-[36rem]" />
            </div>
            <ErrorBoundary resetKey={rendered}>
              {/* Route cross-fade (global choreography): the new page fades in on
                  each route change — keyed on the destination drawn (`surface`: a tab of
                  Capabilities is Capabilities, a tab of Library is Library) so switching sections reads
                  as a continuous transition, not a hard cut. It no longer rises: a rail
                  click is the most frequent action in the app, and a page climbing 6px
                  each time is motion that explains nothing. Enter-only (no exit-wait)
                  keeps navigation instant; MotionConfig at the root swaps this for no
                  motion under Reduce Motion.

                  🔴 The code-split boundary sits INSIDE this element, never around it. Around it, a
                  lazy component anywhere in a page that suspends after the page painted made React
                  hide THIS element (`display: none !important`) and detach its ref. On the reveal,
                  framer-motion re-mounts it and jumps every value back to `initial` so the entrance
                  can replay — but the replay runs from its effect after a RENDER, and a Suspense
                  reveal re-renders nothing here. So the page came back pinned at opacity 0 (and,
                  while it still rose, translateY(6px)) until something re-rendered the shell: the blank Files page. In
                  here, a suspension hides only the page, and this element is never hidden at all. */}
              <motion.div
                key={surface}
                className="h-full"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: duration.medium, ease: ease.emphasizedDecel }}
              >
                <Suspense fallback={<PageFallback />}>
                  <ViewModesProvider value={viewModes}>
                    {surface === 'capabilities'
                      ? (
                        // The destination's tab strip over the page the address names.
                        <CapabilitiesSection route={rendered} query={query} navigate={navigate} fallback={<PageFallback />}>
                          {renderPage(rendered, { sub, navigate, navEpoch, query, setQuery })}
                        </CapabilitiesSection>
                      )
                      : surface === 'library'
                        ? (
                          <LibrarySection route={rendered} navigate={navigate} fallback={<PageFallback />}>
                            {renderPage(rendered, { sub, navigate, navEpoch, query, setQuery })}
                          </LibrarySection>
                        )
                        : renderPage(rendered, { sub, navigate, navEpoch, query, setQuery })}
                  </ViewModesProvider>
                </Suspense>
              </motion.div>
            </ErrorBoundary>
          </main>
        </div>
        {/* Ambient state at the bottom of the window, on every page. Focus mode hides it while the
            gateway answers. */}
        <StatusLine focusMode={focus} />
        <CommandPalette commands={commands} navigate={navigate} />
        {/* The replayable product tour — shell-level because its stops
            are shell surfaces (the rail, chat, inbox, the home approvals band, settings) and
            it walks between them. Renders NOTHING until the onboarding done screen or the
            Discover hub's "Replay the tour" card asks for it. */}
        <ProductTour route={rendered} navigate={navigate} />
        {/* The active personality's decorative shell element.
            Renders NOTHING for the default identity and for every standard scheme;
            under a personality that declares one it mounts an aria-hidden,
            pointer-events-none overlay from the closed SHELL_ELEMENTS registry. */}
        <PersonalityShellElement />
        <Toaster />
        <DialogHost />
        {/* Self-update step progression (WS `update_progress`) — shell-level so the
            modal appears from ANY page while an update pipeline runs. */}
        <UpdateProgressOverlay />
        <TerminalDrawer open={termDrawer} onClose={() => setTermDrawer(false)}
          onOpenFull={(session) => { setTermDrawer(false); navigate(fullTerminalRoute(session)) }} />
      </div>
    </GatewayConnectionProvider>
  )
}
