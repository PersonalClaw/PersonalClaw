import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api } from '../../lib/api'
import type {
  PendingApproval, DashboardStatus, InboxItem, SkillProposal,
  Loop, TaskItem, ScheduleRun, NotificationItem, SystemInfo, PowerUpsResponse,
} from '../../lib/api'

// ── Dashboard live feed ────────────────────────────────────────────────────
// ONE shell-level source of truth for the whole dashboard: a single WebSocket
// subscription (precedent: useApprovalToasts / NotificationBell) plus a set of
// visibility-gated polls. Widgets CONSUME this context — no widget opens its own
// socket or poll, so N widgets share one connection and one refresh cadence.
//
// The socket doesn't carry every widget's payload; like NotificationBell it uses
// WS envelopes as a SIGNAL to refetch the relevant slice immediately (so an
// approval or inbox item appears without waiting for the next poll tick), while
// the polls keep slower-moving data (loops, tasks, schedule, status) fresh.

export interface DashboardLiveData {
  approvals: PendingApproval[]
  inbox: InboxItem[]
  proposals: SkillProposal[]
  loops: Loop[]
  tasks: TaskItem[]
  schedule: ScheduleRun[]
  status: DashboardStatus | null
  notifications: NotificationItem[]
  /** Live system metrics (cpu/mem/net/disk/load) from /api/system — P27. Polled on
   *  the fast, visibility-gated cadence so the SystemHealth widget shows live rates. */
  system: SystemInfo | null
  /** The next untouched-capability proposal for the PowerUps widget (§6), or null
   *  when everything's been explored / the kill switch is off. Polled on SLOW_POLL. */
  powerUps: PowerUpsResponse | null
  /** Dismiss a power-up capability forever, then refetch the slice so the next
   *  untouched capability surfaces (propose-don't-write: this hides, never enables). */
  dismissPowerUp: (id: string) => void
  /** Force an immediate refetch of every slice (e.g. after an inline action). */
  refreshAll: () => void
}

const DashboardLiveContext = createContext<DashboardLiveData | null>(null)

/** Consume the shared dashboard live feed. Throws if used outside the provider,
 *  so a widget can never silently render stale/empty data. */
export function useDashboardLive(): DashboardLiveData {
  const ctx = useContext(DashboardLiveContext)
  if (!ctx) throw new Error('useDashboardLive must be used within <DashboardLiveProvider>')
  return ctx
}

// Poll cadences (ms). Pushed-or-fast data refetches on its WS signal AND on a
// short poll as a safety net; slow data polls only.
const FAST_POLL = 8000
const SLOW_POLL = 20000

export function DashboardLiveProvider({ children }: { children: ReactNode }) {
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [inbox, setInbox] = useState<InboxItem[]>([])
  const [proposals, setProposals] = useState<SkillProposal[]>([])
  const [loops, setLoops] = useState<Loop[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [schedule, setSchedule] = useState<ScheduleRun[]>([])
  const [status, setStatus] = useState<DashboardStatus | null>(null)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [powerUps, setPowerUps] = useState<PowerUpsResponse | null>(null)

  // Individual slice loaders — each swallows errors (a dead endpoint must not
  // blank the whole dashboard) and no-ops if the component has unmounted.
  // The effect sets alive=true on mount and false on cleanup — this handles
  // React strict mode's double-mount cycle correctly (the second mount resets it).
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const guard = <T,>(set: (v: T) => void) => (v: T) => { if (alive.current) set(v) }

  const loadApprovals = useCallback(() => { api.approvals().then(guard(setApprovals)).catch(() => {}) }, [])
  const loadInbox = useCallback(() => { api.inboxPending().then(guard(setInbox)).catch(() => {}) }, [])
  const loadProposals = useCallback(() => { api.skillProposals().then(guard(setProposals)).catch(() => {}) }, [])
  const loadLoops = useCallback(() => { api.uLoops().then(guard(setLoops)).catch(() => {}) }, [])
  const loadTasks = useCallback(() => { api.readyTasks().then(guard(setTasks)).catch(() => {}) }, [])
  const loadSchedule = useCallback(() => { api.triggersHistory(12).then((d) => guard(setSchedule)(d.runs ?? [])).catch(() => {}) }, [])
  const loadStatus = useCallback(() => { api.status().then(guard(setStatus)).catch(() => {}) }, [])
  const loadNotifications = useCallback(() => { api.notifications().then((d) => guard(setNotifications)(d.notifications ?? [])).catch(() => {}) }, [])
  const loadSystem = useCallback(() => { api.system().then(guard(setSystem)).catch(() => {}) }, [])
  const loadPowerUps = useCallback(() => { api.powerUps().then(guard(setPowerUps)).catch(() => {}) }, [])

  // Dismiss persists server-side; on success refetch so the next untouched
  // capability takes its place (or the "explored everything" empty state shows).
  const dismissPowerUp = useCallback((id: string) => {
    api.dismissPowerUp(id).then(() => loadPowerUps()).catch(() => {})
  }, [loadPowerUps])

  const refreshAll = useCallback(() => {
    loadApprovals(); loadInbox(); loadProposals(); loadLoops()
    loadTasks(); loadSchedule(); loadStatus(); loadNotifications(); loadSystem(); loadPowerUps()
  }, [loadApprovals, loadInbox, loadProposals, loadLoops, loadTasks, loadSchedule, loadStatus, loadNotifications, loadSystem, loadPowerUps])

  // Coalesce high-frequency work/status signals. `chat_status`/`sessions` fire on
  // every turn lifecycle change — during active streaming that's many events/sec —
  // and `update_progress` steps through the self-update pipeline (its step DISPLAY
  // is the shell-level UpdateProgressOverlay; here it's only a status-refetch nudge).
  // Debounce the loops+status refetch so a burst collapses into one call (~600ms
  // after it settles) instead of a storm.
  const workDebounce = useRef<number | undefined>(undefined)
  const refreshWork = useCallback(() => {
    if (workDebounce.current) clearTimeout(workDebounce.current)
    workDebounce.current = window.setTimeout(() => { loadLoops(); loadStatus() }, 600)
  }, [loadLoops, loadStatus])
  useEffect(() => () => { if (workDebounce.current) clearTimeout(workDebounce.current) }, [])

  // ONE socket for the whole dashboard. Envelopes are refetch SIGNALS: route each
  // type to the slice it affects so a change lands immediately, not next poll.
  const onMessage = useCallback((m: WsMessage) => {
    const t = m.type
    if (t === 'approval') loadApprovals()
    else if (t.startsWith('inbox')) loadInbox()
    else if (t.startsWith('notification')) loadNotifications()
    // Loop / run progress + session lifecycle nudges refresh the work + status views
    // (debounced — these can fire rapidly during streaming).
    else if (t === 'update_progress' || t === 'chat_status' || t === 'sessions' || t.startsWith('subagent')) {
      refreshWork()
    }
  }, [loadApprovals, loadInbox, loadNotifications, refreshWork])

  useChatSocket(
    onMessage,
    refreshAll,                              // reopened after a drop → catch up on everything
    // NOTE: no onStatus callback — the WS is used for push-signal routing only.
    // Gateway connectivity is surfaced by the shell's SystemWidget dot (a live
    // /api/system poll), so the dashboard feed doesn't track link state itself.
  )

  // Initial load once, then visibility-gated polls (pause when the tab is hidden).
  useEffect(() => { refreshAll() }, [refreshAll])
  useVisiblePoll(() => { loadApprovals(); loadInbox(); loadProposals(); loadLoops(); loadTasks(); loadSystem() }, FAST_POLL)
  useVisiblePoll(() => { loadSchedule(); loadStatus(); loadNotifications(); loadPowerUps() }, SLOW_POLL)

  const value: DashboardLiveData = {
    approvals, inbox, proposals, loops, tasks, schedule, status, notifications, system,
    powerUps, dismissPowerUp, refreshAll,
  }
  return <DashboardLiveContext.Provider value={value}>{children}</DashboardLiveContext.Provider>
}
