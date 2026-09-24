import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { useChatSocket, type WsMessage } from './useChatSocket'
import { useVisiblePoll } from './useVisiblePoll'
import type { ChatSessionSummary, Loop, PendingApproval, SpawnedAgent } from './api'

// ── AgentActivityFeed (AMBIENT-SURFACES A2-3) ────────────────────────────────
// ONE typed read surface describing "what my agents are doing right now", so an
// ambient WORLD can render the live picture without knowing a single endpoint.
// The full contract — including why it exists and what a world may assume — is
// `docs/architecture/agent-activity-feed.md`. The two invariants that matter to
// anyone editing this file:
//
//   1. **Public GETs only.** The fold reads endpoints the dashboard already
//      reads. It adds no route, no query param and no private surface, so an
//      app-contributed world (APP-PLATFORM-EVOLUTION) can be handed the same
//      shape with no new permission.
//   2. **WS envelopes are SIGNALS, NEVER PAYLOADS.** `chat_status`, `sessions`,
//      `subagent*` and `update_progress` only nudge a debounced REFETCH. Not one
//      field is ever read off an envelope. This is the DashboardLive contract
//      (`pages/dashboard/DashboardLive.tsx`) and it is load-bearing: envelope
//      shapes drift per producer, so a surface that renders them renders lies.
//      `agentActivitySignals.test.tsx` delivers a `chat_status` carrying
//      MISLEADING fields and asserts none of them reach a rendered entity.

/** What kind of thing is working. A world may style these differently but must
 *  handle all three — the fold emits every kind it finds. */
export type AgentActivityKind = 'session' | 'loop' | 'subagent'

/** The closed state vocabulary. Deliberately SMALL and world-facing: it answers
 *  "is it moving / does it want me / is it stuck / is it asleep", not the 12-member
 *  `UnifiedLoopStatus`. A world switching on it needs no per-kind knowledge. */
export type AgentActivityState =
  | 'working'           // in flight, nobody is needed
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
  needs_input: 0, waiting_approval: 1, error: 2, working: 3, idle: 4,
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

/** Session keys with a pending tool approval. `PendingApproval.session` is the join
 *  key both loops (`Loop.session_key`) and chat sessions (`key`) share, which is why
 *  `waiting_approval` is reachable at all — see the doc's "four sources, not three". */
export function approvalSessions(approvals: PendingApproval[]): Set<string> {
  return new Set(approvals.map((a) => a.session).filter(Boolean))
}

export function foldLoops(loops: Loop[], blocked: Set<string>): AgentActivityEntity[] {
  return loops.map((l) => {
    // An attributed approval WINS over the loop's own status: the loop still reports
    // `running` while its tool call sits in the approval queue, so trusting status
    // alone would paint a run as busy when it is actually waiting on one click.
    const awaiting = !!l.session_key && blocked.has(l.session_key)
    // A `complete` loop carrying an error_message ended early, not well — the same
    // honesty `loopStatusMeta`'s synthetic `ended_early` tone exists for.
    const ended = l.status === 'complete' && !!l.error_message
    const state: AgentActivityState = awaiting
      ? 'waiting_approval'
      : ended ? 'error' : (LOOP_STATE[l.status] ?? 'idle')
    const total = l.max_cycles > 0 ? Math.min(1, Math.max(0, l.total_cycles / l.max_cycles)) : undefined
    return {
      id: `loop:${l.id}`,
      kind: 'loop' as const,
      state,
      title: label(l.name || l.task, 'Loop'),
      ...(total === undefined ? {} : { progress: total }),
      refs: {
        link: `#/${l.kind === 'code' ? 'code' : 'loops'}/${l.id}`,
        ...(l.session_key ? { session: l.session_key } : {}),
      },
    }
  })
}

export function foldSessions(sessions: ChatSessionSummary[], blocked: Set<string>): AgentActivityEntity[] {
  // Archived sessions left the active list by the user's own decision (SESSION-
  // MANAGEMENT S2); resurrecting them in an ambient scene would undo that gesture.
  const live = sessions.filter((s) => s.lifecycle !== 'archived')
  const kept: ChatSessionSummary[] = []
  const idle: ChatSessionSummary[] = []
  for (const s of live) {
    if (s.running || blocked.has(s.key)) kept.push(s)
    else idle.push(s)
  }
  idle.sort((a, b) => (b.last_activity_at ?? 0) - (a.last_activity_at ?? 0))
  return [...kept, ...idle.slice(0, IDLE_SESSION_LIMIT)].map((s) => ({
    id: `session:${s.key}`,
    kind: 'session' as const,
    // Approval first for the same reason as loops: a streaming session parked on an
    // approval reports `running: true`.
    state: blocked.has(s.key) ? 'waiting_approval' : s.running ? 'working' : 'idle',
    title: label(s.title, 'Chat'),
    refs: { link: `#/chat/${encodeURIComponent(s.key)}`, session: s.key },
  }))
}

export function foldSubagents(agents: SpawnedAgent[]): AgentActivityEntity[] {
  return agents.map((a) => ({
    id: `subagent:${a.id}`,
    kind: 'subagent' as const,
    state: a.error ? 'error' : a.done ? 'idle' : 'working',
    title: label(a.task || a.agent, 'Subagent'),
    // No detail route exists for a spawned agent (the monitor is a rail inside
    // SystemWidget, not a page), so the link is honestly empty rather than a 404.
    refs: { link: '', ...(a.parent ? { parent: a.parent } : {}) },
  }))
}

export interface AgentActivitySources {
  loops: Loop[]
  sessions: ChatSessionSummary[]
  subagents: SpawnedAgent[]
  approvals: PendingApproval[]
}

/** THE fold. Pure — no fetch, no hooks, no DOM — so a world's state→visual mapping
 *  can be tested against seeded fixtures without a gateway (and so the vacuity floor
 *  in `agentActivityFold.test.ts` can assert a NON-ZERO entity count from them). */
export function foldAgentActivity(src: AgentActivitySources): { entities: AgentActivityEntity[]; truncated: number } {
  const blocked = approvalSessions(src.approvals)
  const all = [
    ...foldLoops(src.loops, blocked),
    ...foldSessions(src.sessions, blocked),
    ...foldSubagents(src.subagents),
  ]
  // Stable: salience, then title, then id. A world interpolates BETWEEN folds, so a
  // fold whose order wobbled would make every entity swap places on each refetch.
  all.sort((a, b) =>
    SALIENCE[a.state] - SALIENCE[b.state] || a.title.localeCompare(b.title) || a.id.localeCompare(b.id))
  return { entities: all.slice(0, MAX_ENTITIES), truncated: Math.max(0, all.length - MAX_ENTITIES) }
}

const FAST_POLL = 10_000
const SIGNAL_DEBOUNCE = 600

/** Live agent activity, folded from four public GETs and refreshed by the existing
 *  WS envelopes AS SIGNALS ONLY. A world consumes this and nothing else — see the
 *  contract doc. Safe to call from more than one host: each call opens its own
 *  socket, so prefer one host per view. */
export function useAgentActivity(): AgentActivityFeed {
  const [sources, setSources] = useState<AgentActivitySources | null>(null)
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const load = useCallback(() => {
    Promise.all([
      api.uLoops(), api.chatSessions(), api.spawnedAgents(), api.approvals(),
    ]).then(([loops, sessions, subagents, approvals]) => {
      if (!alive.current) return
      setSources({ loops, sessions, subagents, approvals })
      setError(null)
    }).catch((e) => { if (alive.current) setError(e) })
  }, [])

  // Coalesce the signal storm. `chat_status`/`sessions` fire on every turn lifecycle
  // change — many per second while streaming — so a refetch per envelope would be a
  // request storm. Debounced exactly like DashboardLive's `refreshWork`.
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
      t === 'chat_status' || t === 'sessions' || t === 'update_progress' ||
      t.startsWith('subagent') || t === 'approval' || t === 'approval_resolved'
    ) signal()
  }, [signal])

  useChatSocket(onMessage, load)  // reopened after a drop → full catch-up refetch
  useVisiblePoll(load, FAST_POLL)

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
