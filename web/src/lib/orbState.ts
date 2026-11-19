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

/** The halftone orb's twenty states (`docs/design/agent-orb.md`, "The twenty states"): the sphere at
 *  rest and thinking, five with a mark on a plate beside it, and a scene of its own for each kind of
 *  work and each voice stage. */
export const ORB_SCENES = [
  'ready', 'thinking', 'queued', 'planning', 'searching', 'reading', 'editing', 'running', 'delegating',
  'answering', 'retrying', 'approval', 'question', 'listening', 'recording', 'transcribing', 'speaking',
  'done', 'error', 'offline',
] as const
export type OrbScene = (typeof ORB_SCENES)[number]

/** What drives the orb that is not a phase of the contract, each one typed so no page reaches a
 *  state through a string: a push-to-talk capture, a piece of voice being turned into text, a reply
 *  read aloud, and the gateway out of reach. */
export const ORB_STAGES = ['recording', 'transcribing', 'speaking', 'offline'] as const
export type OrbStage = (typeof ORB_STAGES)[number]

/** Every phase of the contract to the state the halftone orb draws it as, total by its type. A
 *  queued turn and one held behind other calls on a local model both wait behind another's turn;
 *  any tool runs, as a command does, until the gateway says what a call does; and a reply streaming
 *  in is the orb answering. */
export const ORB_SCENE_OF: Readonly<Record<WorkPhase, OrbScene>> = Object.freeze({
  idle: 'ready',
  listening: 'listening',
  queued: 'queued',
  waiting_model: 'queued',
  thinking: 'thinking',
  planning: 'planning',
  searching: 'searching',
  tool: 'running',
  delegating: 'delegating',
  streaming: 'answering',
  retrying: 'retrying',
  approval: 'approval',
  input: 'question',
  done: 'done',
  error: 'error',
})

/** The words for a stage, as the phase contract's labels are for a phase. */
export const ORB_STAGE_LABELS: Readonly<Record<OrbStage, string>> = Object.freeze({
  recording: 'Recording', transcribing: 'Turning that into text', speaking: 'Reading the reply aloud', offline: 'Offline',
})

/** The orb's state for what drives it: a stage wins over the phase, since a page names one only
 *  while it holds. */
export function orbSceneOf(phase: WorkPhase, stage?: OrbStage | null): OrbScene {
  return stage ?? ORB_SCENE_OF[phase]
}

/** The states whose drawing moves: every state but rest, the five with a plate (done moves once,
 *  on arrival), so a still state asks for no frame once it has settled. */
export const SCENE_MOVES: Readonly<Record<OrbScene, boolean>> = Object.freeze(
  Object.fromEntries(ORB_SCENES.map((s) => [s, !['ready', 'approval', 'question', 'done', 'error', 'offline'].includes(s)])) as Record<OrbScene, boolean>,
)

/** A row's still mark (12 px): only the sphere's own forms, since a scene does not hold at that
 *  size, and a row knows its pip, not its phase, so each pip and each of Home's agent row states is
 *  drawn as the form that says it. */
export const MARK_SCENE_OF: Readonly<Record<OrbStateName, OrbScene>> = Object.freeze({
  ready: 'ready', thinking: 'thinking', working: 'thinking', waiting: 'approval', done: 'done',
})
/** A mark's state for an orb state and its badge: a failure is the error mark, a question the
 *  question mark. */
export function markSceneOf(look: Pick<OrbState, 'state' | 'badge'>): OrbScene {
  if (look.badge === 'error') return 'error'
  if (look.badge === 'input') return 'question'
  return MARK_SCENE_OF[look.state]
}

/** A 12 px orb's state: the sphere's own form for what drives it, as a row's mark is (the scenes are
 *  drawn for 64 and 20 px). A live agent is the thinking sphere, a listening one too, a wait or a
 *  stop its plate, and the gateway away the offline mark. */
export function markSceneOfPhase(phase: WorkPhase, stage?: OrbStage | null): OrbScene {
  if (stage) return stage === 'offline' ? 'offline' : 'thinking'
  const look = ORB_STATE_OF[phase]
  if (look.state === 'ready') return look.ink === 'alive' ? 'thinking' : 'ready'
  return markSceneOf(look)
}

/** The orb's state for a wire value, or `null` for anything that is not a phase of the contract:
 *  a status line's words, an orb state's name or a stray value never reach the orb. */
export function orbStateOfWire(value: unknown): OrbState | null {
  const phase = workPhaseOf(value)
  return phase ? ORB_STATE_OF[phase] : null
}

/** A sidebar row's mark, from its pip (`lib/useThreads.ts`). The rail knows only that a row's work
 *  runs, waits on the owner, failed or finished unread, not which phase it is in, so each pip is
 *  the state that says it, drawn as its sphere form (`markSceneOf`): a running row thinking, a
 *  waiting one needs your OK, a failed one the error mark with its × and an unread one done. The
 *  mark is still at every setting, and the row's name and tooltip say the state in words. */
export const ORB_STATE_OF_PIP: Readonly<Record<ThreadPip, OrbState>> = Object.freeze({
  running: as('working', 'alive'),
  waiting: as('waiting', 'alive'),
  failed: as('done', 'rest', { badge: 'error' }),
  unread: as('done', 'rest'),
})

/** Home's agent rows (`pages/dashboard/agents/agentDayModel.ts`'s row states), as the orb's still
 *  mark (`markSceneOf`): an agent at work or held up thinks, one that waits on the owner needs your
 *  OK, one that failed or a restart cut off is the error mark with its ×, and an idle one rests. */
export const ORB_STATE_OF_AGENT_ROW: Readonly<Record<'working' | 'blocked' | 'waiting' | 'failed' | 'interrupted' | 'idle', OrbState>> = Object.freeze({
  working: as('working', 'alive'),
  blocked: as('thinking', 'alive'),
  waiting: as('waiting', 'alive'),
  failed: as('done', 'rest', { badge: 'error' }),
  interrupted: as('done', 'rest', { badge: 'error' }),
  idle: as('ready', 'rest'),
})
