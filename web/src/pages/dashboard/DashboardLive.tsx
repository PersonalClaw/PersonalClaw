import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { refreshKinds, useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { api } from '../../lib/api'
import type { TaskItem, ScheduleRun, DiscoverResponse } from '../../lib/api'

// ── Home's Overview live feed ──────────────────────────────────────────────
// ONE source of truth for the Overview's bands (`HomeOverview`): a single WebSocket subscription
// plus one visibility-gated poll. The bands CONSUME this context — no band opens its own socket or
// poll, so they share one connection and one refresh cadence.
//
// The socket doesn't carry the bands' payloads; it is a SIGNAL to refetch the slice a frame names
// immediately, while the poll keeps the slower-moving data (tasks, recent activity, Discover)
// fresh. What waits on you and what runs are the attention stack's (`Home.tsx`), which reads them
// itself.

/** The slices whose "have we read this yet" answer a consumer actually uses. Deliberately not every
 *  slice: an unread flag nobody reads is the dead-guard shape the `*Err` note below rejects for the
 *  same reason. */
export type ReadSlice = 'tasks' | 'schedule'

const NOTHING_READ: Record<ReadSlice, boolean> = { tasks: false, schedule: false }

export interface DashboardLiveData {
  tasks: TaskItem[]
  /** The tasks read's own failure, distinct from a list that is genuinely empty. */
  tasksErr: unknown
  schedule: ScheduleRun[]
  /** The archive split, straight from the server. `didIds` are the fires that DID
   *  something; everything else was held by a gate. Kept beside the rows rather than re-derived in
   *  each widget: `is_inert` is the backend's rule and a second copy would drift the moment a new
   *  `skipped_*` outcome lands. */
  scheduleDidIds: string[]
  scheduleSuppressed: number
  /** The curated Discover tips for the Overview's section + the hub, or an empty
   *  feed when everything's been explored / the kill switch is off. Polled on SLOW_POLL. */
  discover: DiscoverResponse | null
  /** The tips read's own failure. Distinct from `discover: null`, which also means "not polled
   *  yet" — and critically distinct from `enabled: false`, because the slot's off-branch names a
   *  SETTING. Without this the consumer cannot tell a dead endpoint from a user's own choice, and
   *  the only sentence it has to say is about the choice. Same shape and same reason as
   *  `doctorErr` in `settings/SystemLive`. */
  discoverErr: unknown
  /** Which slices have completed at least one read ATTEMPT — success **or** failure.
   *
   *  🔴 THE NOT-YET-READ CASE HAD TO BE MODELLED. Every `*Err` starts at `null` and is set to `null`
   *  again on success, so `*Err === null` meant BOTH "this read succeeded" and "this read has never
   *  been attempted". Combined with arrays that seed to `[]`, the first frame asserted emptiness as
   *  fact: bands rendered their empty states, pitching a create flow ("New task", "New trigger") to
   *  someone whose tasks and triggers simply had not arrived yet. A count is a claim, and `0` is the
   *  most reassuring one a band can make.
   *
   *  🪤 AND IT CANNOT BE FOLDED INTO `*Err`. Seeding the err fields with a truthy "unread" sentinel
   *  would make the first frame claim a fault nobody has measured: an unread slice is neutral and
   *  silent, while a FAILED one earns an error row and a Retry. */
  read: Readonly<Record<ReadSlice, boolean>>
  /** Dismiss a Discover tip forever, then refetch the slice so it drops from the
   *  feed (propose-don't-write: this hides, never enables). */
  dismissDiscoverTip: (id: string) => void
  /** Force an immediate refetch of every slice (e.g. after an inline action). */
  refreshAll: () => void
}

const DashboardLiveContext = createContext<DashboardLiveData | null>(null)

/** Consume the shared live feed. Throws if used outside the provider,
 *  so a band can never silently render stale/empty data. */
export function useDashboardLive(): DashboardLiveData {
  const ctx = useContext(DashboardLiveContext)
  if (!ctx) throw new Error('useDashboardLive must be used within <DashboardLiveProvider>')
  return ctx
}

const SLOW_POLL = 20000

export function DashboardLiveProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [tasksErr, setTasksErr] = useState<unknown>(null)
  const [schedule, setSchedule] = useState<ScheduleRun[]>([])
  const [scheduleDidIds, setScheduleDidIds] = useState<string[]>([])
  const [scheduleSuppressed, setScheduleSuppressed] = useState(0)
  const [discover, setDiscover] = useState<DiscoverResponse | null>(null)
  const [discoverErr, setDiscoverErr] = useState<unknown>(null)

  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const guard = <T,>(set: (v: T) => void) => (v: T) => { if (alive.current) set(v) }

  const [read, setRead] = useState<Record<ReadSlice, boolean>>(NOTHING_READ)
  /** Mark a slice as attempted. Called from `.finally()`, which is precisely "the attempt finished"
   *  — one call site per loader covering the success and failure branches together.
   *
   *  🪤 RETURNS THE SAME OBJECT once a slice is already marked. Without that identity check, every
   *  poll would hand `read` a fresh object, change the context value, and re-render every consumer
   *  on every tick — a steady render storm to answer a question that stops changing after the
   *  first load. */
  const markRead = useCallback((s: ReadSlice) => {
    if (!alive.current) return
    setRead((r) => (r[s] ? r : { ...r, [s]: true }))
  }, [])

  const loadTasks = useCallback(() => { api.readyTasks().then((d) => { guard(setTasks)(d); guard(setTasksErr)(null) }).catch((e) => guard(setTasksErr)(e)).finally(() => markRead('tasks')) }, [markRead])
  const loadSchedule = useCallback(() => {
    api.triggersHistory(12).then((d) => {
      guard(setSchedule)(d.runs ?? [])
      guard(setScheduleDidIds)(d.did_ids ?? [])
      guard(setScheduleSuppressed)(d.suppressed ?? 0)
    }).catch(() => {}).finally(() => markRead('schedule'))
  }, [markRead])
  const loadDiscover = useCallback(() => {
    api.discover().then((d) => { guard(setDiscover)(d); guard(setDiscoverErr)(null) })
      .catch((e) => guard(setDiscoverErr)(e))
  }, [])
  const dismissDiscoverTip = useCallback(async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    loadDiscover()
  }, [loadDiscover])

  const refreshAll = useCallback(() => {
    loadTasks(); loadSchedule(); loadDiscover()
  }, [loadTasks, loadSchedule, loadDiscover])

  // A burst of frames for one slice reads it once: each slice waits 150 ms for the rest of its burst.
  const soonTimers = useRef<Map<string, number>>(new Map())
  const soon = useCallback((slice: string, load: () => void) => {
    if (soonTimers.current.has(slice)) return
    soonTimers.current.set(slice, window.setTimeout(() => { soonTimers.current.delete(slice); load() }, 150))
  }, [])
  useEffect(() => () => { for (const timer of soonTimers.current.values()) clearTimeout(timer) }, [])

  const onMessage = useCallback((m: WsMessage) => {
    if (m.type !== 'refresh') return
    const kinds = refreshKinds(m)
    if (kinds.includes('tasks')) soon('tasks', loadTasks)
    if (kinds.includes('crons') || kinds.includes('cron_history')) soon('schedule', loadSchedule)
  }, [soon, loadTasks, loadSchedule])

  useChatSocket(
    onMessage,
    refreshAll,                              // reopened after a drop → catch up on everything
  )

  useEffect(() => { refreshAll() }, [refreshAll])
  useVisiblePoll(refreshAll, SLOW_POLL, { immediate: false })

  const value: DashboardLiveData = {
    tasks, tasksErr, read, schedule, scheduleDidIds, scheduleSuppressed,
    discover, discoverErr,
    dismissDiscoverTip, refreshAll,
  }
  return <DashboardLiveContext.Provider value={value}>{children}</DashboardLiveContext.Provider>
}
