import { workPhaseOf, type WorkPhase } from './workPhase'
import type { ThreadPip } from './useThreads'

/** The orb's five states (#6117, `docs/design/agent-orb.md`, "Phase to state"): what the agent is
 *  doing, as one form with its own motion. Ready at rest, thinking before it answers or acts,
 *  working while a tool or a helper runs, waiting on you while it needs the owner, and done. */
export const ORB_STATES = ['ready', 'thinking', 'working', 'waiting', 'done'] as const
export type OrbStateName = (typeof ORB_STATES)[number]

/** Where the owner is needed, or the turn failed: the badge on the orb's upper edge. */
export type OrbBadge = 'approval' | 'input' | 'error'

export interface OrbState {
  /** Which of the five states the orb draws. */
  state: OrbStateName
  /** Alive while an agent works, listens or waits on the owner; at rest where nothing is alive. */
  ink: 'alive' | 'rest'
  /** The wait or the failure, by its glyph: ! for an approval, ? for a question, × for a failure. */
  badge?: OrbBadge
  /** Whether arriving here plays the one success bloom: done does; a failure closes to done's form
   *  with its × and never blooms. */
  bloom: boolean
}

const as = (state: OrbStateName, ink: OrbState['ink'], extra: Partial<Pick<OrbState, 'badge' | 'bloom'>> = {}): OrbState =>
  Object.freeze({ state, ink, bloom: false, ...extra })

/** The one map from the gateway's run state to the orb: every phase of the contract to one state,
 *  total by its type, so a phase the contract adds is a type error here until it is placed. The orb,
 *  the buddy's orb and a list's small mark all read it, and nothing else maps a phase. */
export const ORB_STATE_OF: Readonly<Record<WorkPhase, OrbState>> = Object.freeze({
  idle: as('ready', 'rest'),
  // Voice capture: the agent attends while the owner speaks, so it holds ready's still form in the
  // alive ink, and the words say Listening.
  listening: as('ready', 'alive'),
  queued: as('thinking', 'alive'),
  thinking: as('thinking', 'alive'),
  planning: as('thinking', 'alive'),
  streaming: as('thinking', 'alive'),
  waiting_model: as('thinking', 'alive'),
  // A retry or a fail-over of the model call, pending until it starts: a held-up model call.
  retrying: as('thinking', 'alive'),
  tool: as('working', 'alive'),
  searching: as('working', 'alive'),
  delegating: as('working', 'alive'),
  approval: as('waiting', 'alive', { badge: 'approval' }),
  input: as('waiting', 'alive', { badge: 'input' }),
  done: as('done', 'rest', { bloom: true }),
  error: as('done', 'rest', { badge: 'error' }),
})

/** The orb's state for a wire value, or `null` for anything that is not a phase of the contract:
 *  a status line's words, an orb state's name or a stray value never reach the orb. */
export function orbStateOfWire(value: unknown): OrbState | null {
  const phase = workPhaseOf(value)
  return phase ? ORB_STATE_OF[phase] : null
}

/** A sidebar row's mark, from its pip (`lib/useThreads.ts`). The rail knows only that a row's work
 *  runs, waits on the owner, failed or finished unread, not which phase it is in, so each pip is
 *  drawn as the state that says it: a running row as working's two rings, a waiting one as the
 *  clasp, and a failed or unread one as done's mark, the failure with its ×. The mark is still at
 *  every setting, and the row's name and tooltip say the state in words. */
export const ORB_STATE_OF_PIP: Readonly<Record<ThreadPip, OrbState>> = Object.freeze({
  running: as('working', 'alive'),
  waiting: as('waiting', 'alive'),
  failed: as('done', 'rest', { badge: 'error' }),
  unread: as('done', 'rest'),
})

/** Home's agent rows (`pages/dashboard/agents/agentDayModel.ts`'s row states), as the orb's still
 *  mark: an agent at work is working's rings, one that waits on the owner the clasp, one that failed
 *  done's mark with its ×, and an idle one the open ring. */
export const ORB_STATE_OF_AGENT_ROW: Readonly<Record<'working' | 'waiting' | 'failed' | 'idle', OrbState>> = Object.freeze({
  working: as('working', 'alive'),
  waiting: as('waiting', 'alive'),
  failed: as('done', 'rest', { badge: 'error' }),
  idle: as('ready', 'rest'),
})
