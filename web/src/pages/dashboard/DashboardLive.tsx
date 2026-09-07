import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api } from '../../lib/api'
import type {
  PendingApproval, DashboardStatus, InboxItem, SkillProposal,
  Loop, TaskItem, ScheduleRun, NotificationItem, SystemInfo, DiscoverResponse, DoctorReport,
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

/** The slices whose "have we read this yet" answer a consumer actually uses. Deliberately not every
 *  slice: an unread flag nobody reads is the dead-guard shape this file's `*Err` note already
 *  rejects for the same reason. */
export type ReadSlice =
  | 'approvals' | 'inbox' | 'proposals' | 'loops' | 'tasks' | 'notifications' | 'schedule'

/** Nothing read yet — the honest starting position, and the one this file used to lack. */
const NOTHING_READ: Record<ReadSlice, boolean> = {
  approvals: false, inbox: false, proposals: false, loops: false, tasks: false,
  notifications: false, schedule: false,
}

export interface DashboardLiveData {
  approvals: PendingApproval[]
  inbox: InboxItem[]
  proposals: SkillProposal[]
  /** The three triage lanes' own read failures, each distinct from an empty lane. ActionCenter
   *  merges approvals+inbox+proposals into one queue; without these a swallowed lane silently
   *  under-reports (a missing PENDING TOOL APPROVAL reads as "all clear"). Same shape and reason as
   *  `discoverErr`/`doctorErr` below. */
  approvalsErr: unknown
  inboxErr: unknown
  proposalsErr: unknown
  loops: Loop[]
  tasks: TaskItem[]
  /** The three COUNTED lanes' own read failures, each distinct from a lane that is genuinely
   *  empty. `HeroPulse` renders these three as numbers on the app's first screen, and `[]` is
   *  what a swallowed rejection leaves behind — so without these the hero states "0 loops
   *  running · 0 tasks ready · 0 unread" as fact when nothing was read. Same shape and the same
   *  reason as `approvalsErr` above and `discoverErr`/`doctorErr` below; this finishes the
   *  convergence those started, for the slices that have a consumer able to use them.
   *
   *  `schedule`/`status`/`system` deliberately still swallow: no consumer would read the field
   *  yet, and an unread error field is the dead-guard shape the repo's rails already reject.
   *  They are the next step, not an oversight. */
  loopsErr: unknown
  tasksErr: unknown
  notificationsErr: unknown
  schedule: ScheduleRun[]
  /** §1.3's archive split, straight from the server (S165). `didIds` are the fires that DID
   *  something; everything else was held by a gate. Kept beside the rows rather than re-derived in
   *  each widget: `is_inert` is the backend's rule and a second copy would drift the moment a new
   *  `skipped_*` outcome lands. */
  scheduleDidIds: string[]
  scheduleSuppressed: number
  status: DashboardStatus | null
  notifications: NotificationItem[]
  /** Live system metrics (cpu/mem/net/disk/load) from /api/system — P27. Polled on
   *  the fast, visibility-gated cadence so the SystemHealth widget shows live rates. */
  system: SystemInfo | null
  /** The curated Discover tips for the dashboard section + hub (§6), or an empty
   *  feed when everything's been explored / the kill switch is off. Polled on SLOW_POLL. */
  discover: DiscoverResponse | null
  /** The tips read's own failure. Distinct from `discover: null`, which also means "not polled
   *  yet" — and critically distinct from `enabled: false`, because the slot's off-branch names a
   *  SETTING. Without this the consumer cannot tell a dead endpoint from a user's own choice, and
   *  the only sentence it has to say is about the choice. Same shape and same reason as
   *  `doctorErr` below. */
  discoverErr: unknown
  /** The doctor health rollup (PLATFORM-RESILIENCE §1) — cached 30s server-side, so
   *  polled on SLOW_POLL. Powers the SystemHealth widget's one-line health signal. */
  doctor: DoctorReport | null
  /** The probe's own failure. Distinct from `doctor: null`, which also means "not polled yet" —
   *  and critically distinct from a healthy report, because a health surface that goes quiet is
   *  read as "nothing wrong". Consumers must render "unknown", never silence. */
  doctorErr: unknown
  /** Which slices have completed at least one read ATTEMPT — success **or** failure.
   *
   *  🔴 THE FAILED CASE WAS CONVERGED AND THE NOT-YET-READ CASE WAS NEVER MODELLED. Every `*Err`
   *  above starts at `null` and is set to `null` again on success, so `*Err === null` meant BOTH
   *  "this read succeeded" and "this read has never been attempted". Combined with arrays that seed
   *  to `[]`, the dashboard's FIRST FRAME asserted emptiness as fact: `HeroPulse` stated
   *  "0 loops running · 0 approvals waiting · 0 tasks ready · 0 inbox · 0 unread", and four widgets
   *  rendered their empty states — two of them pitching a create flow ("New task", "New trigger") to
   *  someone whose tasks and triggers simply had not arrived yet.
   *
   *  🔑 `HeroPulse` ALREADY CARRIES THE DOCTRINE THIS COMPLETES, in its own words: *"A COUNT IS A
   *  CLAIM, AND `0` IS THE MOST REASSURING ONE THIS STRIP CAN MAKE… `null` means 'not read'."* It
   *  implemented only the FAILED half of "not read". This is the other half.
   *
   *  🪤 AND IT CANNOT BE FOLDED INTO `*Err` — that was the tempting one-line version. Seeding the
   *  err fields with a truthy "unread" sentinel would fix every count for free, because consumers
   *  already write `err ? null : n`. But the two states must render DIFFERENTLY: an unread lane is
   *  neutral and silent, while a FAILED lane earns an error row and a Retry (`ActionCenter` renders
   *  exactly that). A sentinel would make the first frame claim a fault nobody has measured — the
   *  mirror of the bug, and the same mistake `HeroPulse` warns about when it keeps the unknown pill
   *  NEUTRAL rather than styling it as an alarm. */
  read: Readonly<Record<ReadSlice, boolean>>
  /** Re-run ONE triage lane's read — the Retry beside its failure row. Per-lane, so retrying a
   *  failed approvals read doesn't refetch a healthy inbox. */
  retryApprovals: () => void
  retryInbox: () => void
  retryProposals: () => void
  /** Dismiss a Discover tip forever, then refetch the slice so it drops from the
   *  feed (propose-don't-write: this hides, never enables). */
  dismissDiscoverTip: (id: string) => void
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
  // Per-lane read failures for the three triage-queue lanes (ActionCenter merges them into one
  // list). Paired with the arrays above the way `discoverErr`/`doctorErr` pair with their slices:
  // an array left at its last-good value cannot say "this lane failed to load", so a swallowed
  // approvals/inbox/proposals read used to vanish into ActionCenter's "All clear" — hiding, e.g.,
  // a pending TOOL APPROVAL. Tracked so the queue tells a failed lane from an empty one, and retries.
  const [approvalsErr, setApprovalsErr] = useState<unknown>(null)
  const [inboxErr, setInboxErr] = useState<unknown>(null)
  const [proposalsErr, setProposalsErr] = useState<unknown>(null)
  const [loops, setLoops] = useState<Loop[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [schedule, setSchedule] = useState<ScheduleRun[]>([])
  const [scheduleDidIds, setScheduleDidIds] = useState<string[]>([])
  const [scheduleSuppressed, setScheduleSuppressed] = useState(0)
  const [status, setStatus] = useState<DashboardStatus | null>(null)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  // The counted lanes' own failures — see the type's note: `[]` is what a swallowed rejection
  // leaves, and HeroPulse turns these three into numbers on the first screen.
  const [loopsErr, setLoopsErr] = useState<unknown>(null)
  const [tasksErr, setTasksErr] = useState<unknown>(null)
  const [notificationsErr, setNotificationsErr] = useState<unknown>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [discover, setDiscover] = useState<DiscoverResponse | null>(null)
  const [discoverErr, setDiscoverErr] = useState<unknown>(null)
  const [doctor, setDoctor] = useState<DoctorReport | null>(null)
  const [doctorErr, setDoctorErr] = useState<unknown>(null)

  // Individual slice loaders — each swallows errors (a dead endpoint must not
  // blank the whole dashboard) and no-ops if the component has unmounted.
  // The effect sets alive=true on mount and false on cleanup — this handles
  // React strict mode's double-mount cycle correctly (the second mount resets it).
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const guard = <T,>(set: (v: T) => void) => (v: T) => { if (alive.current) set(v) }

  const [read, setRead] = useState<Record<ReadSlice, boolean>>(NOTHING_READ)
  /** Mark a slice as attempted. Called from `.finally()`, which is precisely "the attempt finished"
   *  — one call site per loader covering the success and failure branches together.
   *
   *  🪤 RETURNS THE SAME OBJECT once a slice is already marked. Without that identity check, every
   *  8s poll would hand `read` a fresh object, change the context value, and re-render every
   *  dashboard consumer on every tick — a steady render storm to answer a question that stops
   *  changing after the first load. */
  const markRead = useCallback((s: ReadSlice) => {
    if (!alive.current) return
    setRead((r) => (r[s] ? r : { ...r, [s]: true }))
  }, [])

  // 🔴 The three triage lanes surface their own read failures (like `loadDiscover`/`loadDoctor`
  // below) instead of swallowing. `catch(() => {})` left each array at its last-good value, so a
  // failed read was indistinguishable from an empty lane — and ActionCenter folded that into "All
  // clear — nothing waiting on you", the one sentence that must never appear over a swallowed
  // PENDING TOOL APPROVAL. A later failure keeps the last-good rows (this only assigns on success);
  // the cold case surfaces the error so the lane can say it failed and offer a retry.
  const loadApprovals = useCallback(() => {
    api.approvals().then((d) => { guard(setApprovals)(d); guard(setApprovalsErr)(null) }).catch((e) => guard(setApprovalsErr)(e))
      .finally(() => markRead('approvals'))
  }, [markRead])
  const loadInbox = useCallback(() => {
    api.inboxPending().then((d) => { guard(setInbox)(d); guard(setInboxErr)(null) }).catch((e) => guard(setInboxErr)(e))
      .finally(() => markRead('inbox'))
  }, [markRead])
  const loadProposals = useCallback(() => {
    api.skillProposals().then((d) => { guard(setProposals)(d.proposals); guard(setProposalsErr)(null) }).catch((e) => guard(setProposalsErr)(e))
      .finally(() => markRead('proposals'))
  }, [markRead])
  const loadLoops = useCallback(() => { api.uLoops().then((d) => { guard(setLoops)(d); guard(setLoopsErr)(null) }).catch((e) => guard(setLoopsErr)(e)).finally(() => markRead('loops')) }, [markRead])
  const loadTasks = useCallback(() => { api.readyTasks().then((d) => { guard(setTasks)(d); guard(setTasksErr)(null) }).catch((e) => guard(setTasksErr)(e)).finally(() => markRead('tasks')) }, [markRead])
  // 🔴 Keeps the archive split, which this call discarded (S165). The backend has returned
  // `did_ids`/`suppressed` since S132 and S163 typed them — but the widget still saw only
  // `d.runs`, so a minutely trigger inside quiet hours filled all six visible rows with identical
  // "gate" entries and the ONE fire that ran (at index 7) never appeared. Measured. The user reads
  // that as "nothing has run", which is the exact failure §1.3's split exists to prevent.
  const loadSchedule = useCallback(() => {
    api.triggersHistory(12).then((d) => {
      guard(setSchedule)(d.runs ?? [])
      guard(setScheduleDidIds)(d.did_ids ?? [])
      guard(setScheduleSuppressed)(d.suppressed ?? 0)
    }).catch(() => {}).finally(() => markRead('schedule'))
  }, [markRead])
  const loadStatus = useCallback(() => { api.status().then(guard(setStatus)).catch(() => {}) }, [])
  const loadNotifications = useCallback(() => { api.notifications().then((d) => { guard(setNotifications)(d.notifications ?? []); guard(setNotificationsErr)(null) }).catch((e) => guard(setNotificationsErr)(e)).finally(() => markRead('notifications')) }, [markRead])
  const loadSystem = useCallback(() => { api.system().then(guard(setSystem)).catch(() => {}) }, [])
  // 🔴 `catch(() => {})` left `discover` null, and the dashboard's Discover slot renders
  // "Discover tips are off." for `!discover` — so a dead endpoint (and every millisecond before
  // the first read lands) impersonated a SETTING THE USER NEVER TOUCHED. Measured on an empty home
  // with the read aborted: the slot said "Discover tips are off." while `#/discover`, on the very
  // same rejection, said "Couldn't load your tips" with a Retry. Same story as `loadDoctor` below.
  const loadDiscover = useCallback(() => {
    api.discover().then((d) => { guard(setDiscover)(d); guard(setDiscoverErr)(null) })
      .catch((e) => guard(setDiscoverErr)(e))
  }, [])
  // 🔴 `catch(() => {})` left `doctor` null, and the SystemHealth strip surfaces its health row
  // ONLY when `!doctor.ok` — its own comment says "a healthy system stays quiet". So a failed
  // PROBE impersonated health on the dashboard's health surface. `#/settings/doctor` already says
  // "Couldn't load the doctor report" out loud for exactly this reason; the summary surfaces now
  // get the same fact to work with.
  const loadDoctor = useCallback(() => {
    api.doctor().then((d) => { guard(setDoctor)(d); guard(setDoctorErr)(null) })
      .catch((e) => guard(setDoctorErr)(e))
  }, [])

  // Dismiss persists server-side; on success refetch so the tip drops from the
  // feed (or the "explored everything" empty state shows).
  // The refetch was already gated on success — only the failure was silent, so the tip simply stayed
  // put with nothing said and the click read as doing nothing at all.
  const dismissDiscoverTip = useCallback(async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    loadDiscover()
  }, [loadDiscover])

  const refreshAll = useCallback(() => {
    loadApprovals(); loadInbox(); loadProposals(); loadLoops()
    loadTasks(); loadSchedule(); loadStatus(); loadNotifications(); loadSystem(); loadDiscover(); loadDoctor()
  }, [loadApprovals, loadInbox, loadProposals, loadLoops, loadTasks, loadSchedule, loadStatus, loadNotifications, loadSystem, loadDiscover, loadDoctor])

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
  useVisiblePoll(() => { loadSchedule(); loadStatus(); loadNotifications(); loadDiscover(); loadDoctor() }, SLOW_POLL)

  const value: DashboardLiveData = {
    approvals, inbox, proposals, approvalsErr, inboxErr, proposalsErr,
    loopsErr, tasksErr, notificationsErr, read,
    loops, tasks, schedule, scheduleDidIds, scheduleSuppressed,
    status, notifications, system,
    discover, discoverErr, doctor, doctorErr,
    retryApprovals: loadApprovals, retryInbox: loadInbox, retryProposals: loadProposals,
    dismissDiscoverTip, refreshAll,
  }
  return <DashboardLiveContext.Provider value={value}>{children}</DashboardLiveContext.Provider>
}
