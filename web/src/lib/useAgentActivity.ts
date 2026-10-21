import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { useChatSocket, type WsMessage } from './useChatSocket'
import { useVisiblePoll } from './useVisiblePoll'
import { loopRoute } from './loopKind'
import type { ChatSessionSummary, Loop, PendingApproval, RunningList, RunningRow } from './api'
import { phaseWorld, workPhaseOf } from './workPhase'

// ── AgentActivityFeed ────────────────────────────────
// ONE typed read surface describing "what my agents are doing right now", so an
// ambient WORLD can render the live picture without knowing a single endpoint.
// The full contract — including why it exists and what a world may assume — is
// `docs/architecture/agent-activity-feed.md`. The two invariants that matter to
// anyone editing this file:
//
//   1. **Public GETs only.** The fold reads endpoints the dashboard already
//      reads. It adds no route, no query param and no private surface, so an
//      app-contributed world can be handed the same
//      shape with no new permission.
//   2. **WS envelopes are SIGNALS, NEVER PAYLOADS.** `running_changed`, `sessions`,
//      `approval*`, `refresh` and `update_progress` only nudge a debounced REFETCH. Not one
//      field is ever read off an envelope. This is the DashboardLive contract
//      (`pages/dashboard/DashboardLive.tsx`) and it is load-bearing: envelope
//      shapes drift per producer, so a surface that renders them renders lies.
//      `agentActivitySignals.test.tsx` delivers a `running_changed` carrying
//      MISLEADING fields and asserts none of them reach a rendered entity.
//
// What runs is read from ONE list, `GET /api/running` (`running_now`): every kind of live work,
// with a loop's workers and planner already folded into their loop and a queued, retrying or
// model-waiting row marked `blocked`, and the just-finished tail. The chat list and the loop list
// add only the background: chats that are quiet now, and loops that are not running.

/** What kind of thing is working. A world may style these differently but must
 *  handle every one — the fold emits every kind it finds. A `run` is a workflow run, and a
 *  `trigger` and a `webhook` are a fire and a webhook's turn. */
export type AgentActivityKind = 'session' | 'loop' | 'subagent' | 'run' | 'trigger' | 'webhook'

/** The closed state vocabulary. Deliberately SMALL and world-facing: it answers
 *  "is it moving / does it want me / is it stuck / is it asleep", not the 12-member
 *  `UnifiedLoopStatus`. A world switching on it needs no per-kind knowledge. */
export type AgentActivityState =
  | 'working'           // in flight, nobody is needed
  | 'blocked'           // live, and held up: queued for a slot, retrying, or waiting on a busy model
  | 'needs_input'       // stopped ON the user (a question, a block, a stall)
  | 'waiting_approval'  // stopped on a tool-approval decision
  | 'idle'              // alive but not doing anything
  | 'error'             // ended badly

/** Where an entity lives. A world is ambient and need not navigate, but a host
 *  surface that wraps one should be able to without re-deriving route rules. */
export interface AgentActivityRefs {
  /** Hash deep link to the entity's detail surface, or '' when the kind has none
   *  (a spawned subagent has no detail route — pointing at one would 404). */
  link: string
  /** The chat session this entity runs in, when it has one. */
  session?: string
  /** For a subagent, the parent session/run that spawned it. */
  parent?: string
}

export interface AgentActivityEntity {
  /** Unique ACROSS kinds — a loop and a session can share a raw id, so ids are
   *  kind-prefixed (`loop:x`). A world keying a scene node by `id` needs that. */
  id: string
  kind: AgentActivityKind
  state: AgentActivityState
  title: string
  /** 0..1 when the entity can express fractional progress (a loop's cycle budget),
   *  omitted otherwise. A world MUST treat absence as "unknown", not as zero —
   *  a ring drawn empty for an unknown is a lie about the run. */
  progress?: number
  refs: AgentActivityRefs
}

export interface AgentActivityFeed {
  entities: AgentActivityEntity[]
  /** How many entities the fold dropped to stay under `MAX_ENTITIES`. A world that
   *  silently renders 64 of 300 is lying about the scale of what is running. */
  truncated: number
  /** The fold's OWN failure. Distinct from `entities: []`, which also means "nothing
   *  is running" — and a world that renders a calm empty scene while every fetch is
   *  502ing is the worst outcome available. Consumers MUST say "unknown", never go
   *  quiet. (Same rule as DashboardLive's `doctorErr`.) */
  error: unknown
  /** True until the first fold settles, so a world can hold its empty state back. */
  loading: boolean
  refresh: () => void
}

/** Scene budget. A canvas of 300 dots is mush, and the fold is a read model for a
 *  WORLD, not an audit log. Over the cap the least salient entities drop and
 *  `truncated` carries the count. */
export const MAX_ENTITIES = 64

/** Idle chat sessions worth showing. Every session ever opened is "idle", so an
 *  unbounded fold would bury the four things actually running under 200 asleep
 *  ones. Running and approval-blocked sessions are ALWAYS kept (see `foldSessions`);
 *  this caps only the ambient background of recent-but-quiet ones. */
export const IDLE_SESSION_LIMIT = 8

/** Salience order — what a user should see first when the scene is over budget,
 *  and the paint order a world should follow. Actionable before merely alive. */
const SALIENCE: Record<AgentActivityState, number> = {
  needs_input: 0, waiting_approval: 1, error: 2, blocked: 3, working: 4, idle: 5,
}

/** Loop status → world state. The 12-member `UnifiedLoopStatus` collapses here and
 *  NOWHERE else, so every world agrees on what "stuck" looks like.
 *
 *  `blocked` and `stagnant` land on `needs_input` on purpose: the dashboard's
 *  ActiveWork widget already treats both as "in flight or awaiting them"
 *  (`widgets/ActiveWork.tsx` ACTIVE set), and a stalled run that renders as calm
 *  `working` is the exact failure an ambient surface exists to prevent. */
const LOOP_STATE: Record<string, AgentActivityState> = {
  intake: 'working', planning: 'working', review: 'working', running: 'working',
  needs_input: 'needs_input', blocked: 'needs_input', stagnant: 'needs_input',
  failed: 'error',
  ready: 'idle', paused: 'idle', stopped: 'idle', complete: 'idle',
}

/** Trim a title to something a canvas label can hold without becoming a paragraph. */
function label(s: string | undefined, fallback: string): string {
  const t = (s ?? '').replace(/\s+/g, ' ').trim()
  return t ? t.slice(0, 80) : fallback
}

/** A row's kind as the world names it: a workflow run is a `run`, a chat a `session`. */
const ROW_KIND: Record<RunningRow['kind'], AgentActivityKind> = {
  chat: 'session', loop: 'loop', workflow: 'run', subagent: 'subagent', trigger: 'trigger', webhook: 'webhook',
}

/** A row's entity id: kind-prefixed, as every entity's is, so a chat keeps its `session:` id. */
function rowId(row: RunningRow): string {
  const ident = row.work.slice(row.work.indexOf(':') + 1)
  return `${ROW_KIND[row.kind] ?? row.kind}:${ident}`
}

/** A row's world state. What waits on you for it comes first, as the gateway's own count reads it:
 *  an approval, else a question. Then the gateway's `blocked`, then its phase (`workPhase.ts`).
 *  A live row in a phase this build does not know is still live work, never idle. */
function rowState(row: RunningRow, approvals: ReadonlySet<string>): AgentActivityState {
  const waiting = Array.isArray(row.waiting) ? row.waiting : []
  if (waiting.some((id) => approvals.has(id))) return 'waiting_approval'
  if (waiting.length > 0) return 'needs_input'
  if (row.blocked) return 'blocked'
  const phase = workPhaseOf(row.phase)
  return phase ? phaseWorld(phase) : 'working'
}

/** The running list's rows, each one entity: live work first, then the just-finished tail, whose
 *  phase is how it ended (a failure paints its error, a finish rests). A loop's row carries the
 *  loop's cycle budget as its progress. */
export function foldRunning(
  list: RunningList,
  approvals: PendingApproval[],
  loops: Loop[],
): AgentActivityEntity[] {
  const asked = new Set(approvals.map((a) => a.id))
  const loopsById = new Map(loops.map((l) => [l.id, l]))
  const out: AgentActivityEntity[] = []
  const seen = new Set<string>()
  for (const row of [...(list.running ?? []), ...(list.finished ?? [])]) {
    if (row === null || typeof row !== 'object' || typeof row.work !== 'string') continue
    const id = rowId(row)
    if (seen.has(id)) continue
    seen.add(id)
    // A run-backed loop's row is its run's (`workflow:<run>`), and the loop list names it by its run.
    const l = row.kind === 'loop' ? loopsById.get(row.work.slice(row.work.indexOf(':') + 1)) : undefined
    const progress = l && l.max_cycles > 0 ? Math.min(1, Math.max(0, l.total_cycles / l.max_cycles)) : undefined
    out.push({
      id,
      kind: ROW_KIND[row.kind] ?? 'session',
      state: rowState(row, asked),
      title: label(row.name, 'Work'),
      ...(progress === undefined ? {} : { progress }),
      refs: { link: row.route || '', ...(row.session ? { session: row.session } : {}) },
    })
  }
  return out
}

export function foldLoops(loops: Loop[]): AgentActivityEntity[] {
  return loops.map((l) => {
    // A `complete` loop carrying an error_message ended early, not well — the same
    // honesty `loopStatusMeta`'s synthetic `ended_early` tone exists for.
    const ended = l.status === 'complete' && !!l.error_message
    const state: AgentActivityState = ended ? 'error' : (LOOP_STATE[l.status] ?? 'idle')
    const total = l.max_cycles > 0 ? Math.min(1, Math.max(0, l.total_cycles / l.max_cycles)) : undefined
    return {
      id: `loop:${l.id}`,
      kind: 'loop' as const,
      state,
      title: label(l.name || l.task, 'Loop'),
      ...(total === undefined ? {} : { progress: total }),
      refs: {
        // `loopRoute`: a run-backed loop (`run_id`) opens on its run page, a code loop in Code.
        link: `#/${loopRoute(l)}`,
        ...(l.session_key ? { session: l.session_key } : {}),
      },
    }
  })
}

/** The quiet chats: what is running is the list's, and a loop's worker or planner is its loop, never
 *  a chat of its own (the chat list marks them `origin: 'loop'`). */
export function foldSessions(sessions: ChatSessionSummary[]): AgentActivityEntity[] {
  // Archived sessions left the active list by the user's own decision (SESSION-
  // MANAGEMENT S2); resurrecting them in an ambient scene would undo that gesture.
  const quiet = sessions.filter((s) => s.lifecycle !== 'archived' && s.origin !== 'loop')
  quiet.sort((a, b) => (b.last_activity_at ?? 0) - (a.last_activity_at ?? 0))
  return quiet.slice(0, IDLE_SESSION_LIMIT).map((s) => ({
    id: `session:${s.key}`,
    kind: 'session' as const,
    state: 'idle' as const,
    title: label(s.title, 'Chat'),
    refs: { link: `#/chat/${encodeURIComponent(s.key)}`, session: s.key },
  }))
}

export interface AgentActivitySources {
  /** `GET /api/running`: what runs, and what just finished. */
  running: RunningList
  /** `GET /api/chat/sessions`: the quiet chats behind it. */
  sessions: ChatSessionSummary[]
  /** `GET /api/loops`: the loops that are not running, and a running loop's cycle budget. */
  loops: Loop[]
  /** `GET /api/approvals`: which of a row's waits are approvals. */
  approvals: PendingApproval[]
}

/** THE fold. Pure — no fetch, no hooks, no DOM — so a world's state→visual mapping
 *  can be tested against seeded fixtures without a gateway (and so the vacuity floor
 *  in `agentActivityFold.test.ts` can assert a NON-ZERO entity count from them). */
export function foldAgentActivity(src: AgentActivitySources): { entities: AgentActivityEntity[]; truncated: number } {
  const live = foldRunning(src.running ?? { running: [], finished: [] }, src.approvals ?? [], src.loops ?? [])
  const held = new Set(live.map((e) => e.id))
  const all = [
    ...live,
    ...foldLoops((src.loops ?? []).filter((l) => !held.has(`loop:${l.id}`))),
    ...foldSessions((src.sessions ?? []).filter((s) => !held.has(`session:${s.key}`))),
  ]
  // Stable: salience, then title, then id. A world interpolates BETWEEN folds, so a
  // fold whose order wobbled would make every entity swap places on each refetch.
  all.sort((a, b) =>
    SALIENCE[a.state] - SALIENCE[b.state] || a.title.localeCompare(b.title) || a.id.localeCompare(b.id))
  return { entities: all.slice(0, MAX_ENTITIES), truncated: Math.max(0, all.length - MAX_ENTITIES) }
}

/** A safety net, not the feed: every source this folds already signals its own changes (below),
 *  so the timer only has to cover a frame the socket missed. It re-read all four every 10s. */
const SAFETY_POLL = 60_000
const SIGNAL_DEBOUNCE = 600

/** Live agent activity, folded from the running list and three public GETs behind it, and
 *  refreshed by the existing WS envelopes AS SIGNALS ONLY. A world consumes this and nothing else — see the
 *  contract doc. Safe to call from more than one host: each call opens its own
 *  socket, so prefer one host per view. */
export function useAgentActivity(): AgentActivityFeed {
  const [sources, setSources] = useState<AgentActivitySources | null>(null)
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const load = useCallback(() => {
    Promise.all([
      api.running(), api.chatSessions(), api.uLoops(), api.approvals(),
    ]).then(([running, sessions, loops, approvals]) => {
      if (!alive.current) return
      setSources({ running, sessions, loops, approvals })
      setError(null)
    }).catch((e) => { if (alive.current) setError(e) })
  }, [])

  // Coalesce the signal storm. The gateway already rings `running_changed` at most once per
  // 150 ms window and never for a liveness re-send, but `sessions` fires on every turn lifecycle
  // change, so a burst still collapses into one re-read. Debounced exactly like DashboardLive's
  // `refreshWork`.
  const debounce = useRef<number | undefined>(undefined)
  const signal = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(load, SIGNAL_DEBOUNCE)
  }, [load])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])

  const onMessage = useCallback((m: WsMessage) => {
    const t = m.type
    // The envelope's TYPE is the only thing read. `m.data` is deliberately never
    // touched here — that is the whole contract. Do not "optimize" a field out of it.
    if (
      t === 'running_changed' || t === 'sessions' || t === 'update_progress' ||
      t === 'approval' || t === 'approval_resolved' ||
      // A loop's lifecycle rides the gateway's `refresh` hint. Signalled on the TYPE alone, per
      // the contract above: the hint is rare, so an unrelated kind costs one coalesced re-read.
      t === 'refresh'
    ) signal()
  }, [signal])

  useChatSocket(onMessage, load)  // reopened after a drop → full catch-up refetch
  useVisiblePoll(load, SAFETY_POLL)

  const folded = useMemo(
    () => sources ? foldAgentActivity(sources) : { entities: [], truncated: 0 },
    [sources],
  )

  return {
    entities: folded.entities,
    truncated: folded.truncated,
    error,
    loading: sources === null && error === null,
    refresh: load,
  }
}
