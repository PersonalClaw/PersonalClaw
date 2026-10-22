import type { RunState } from './api'
import type { AgentActivityState } from './useAgentActivity'

// ── ONE phase vocabulary ────────────────────────────────────────────────────────────────────────
//
// What an agent is doing reaches the web as one phase of a closed set: a chat's `run_state` and the
// `work_phase` frames after it, which the gateway sends from what it measures
// (`src/personalclaw/work_phase.py`, `dashboard/chat_phase.py`). This file is the one table of how
// each phase looks, and every surface reads it here:
//
//   · `tone` — the status tone a phase is painted in. Live work is the scheme's primary (coral),
//     never a colour of its own; waiting on the owner's yes is warn and on her answer info; a finish
//     is ok and a failure danger; nothing running carries no accent. Listening keeps warn, which
//     matches the system's own microphone dot.
//   · `family` — which of the four shared families it reads as: active, waiting, blocked, quiet.
//     Each surface's own looks are variants inside them.
//   · `lineKey` — the copy row the gateway writes its line from. The line holds the state only
//     ("Searching your knowledge"); a surface adds the work's name.
//   · `world` — the state an ambient world shows it as (`useAgentActivity`'s six): live work held
//     up (queued for a slot, retrying, waiting on a busy model) is `blocked`, apart from working, as
//     the running list marks it (`running_now.BLOCKED_PHASES`).
//   · `label` — the word the chat says for it.
//
// Colour and motion never carry a phase alone: a surface shows its label or its line beside them.
// `tests/test_work_phase_vocabulary.py` pins this table to the gateway's in both directions, and
// `workPhase.test.ts` holds every surface to it.

export const WORK_PHASES = [
  'queued', 'thinking', 'searching', 'tool', 'planning', 'delegating', 'streaming', 'listening',
  'approval', 'input', 'done', 'error', 'retrying', 'waiting_model', 'idle',
] as const

export type WorkPhase = (typeof WORK_PHASES)[number]

export type PhaseFamily = 'quiet' | 'active' | 'waiting' | 'blocked'

/** A status tone (`ui/StatusPill`'s): `primary` is coral, the one live accent. */
export type PhaseTone = 'primary' | 'warn' | 'info' | 'ok' | 'danger' | 'neutral'

export interface PhaseLook {
  tone: PhaseTone
  family: PhaseFamily
  lineKey: string
  world: AgentActivityState
  label: string
}

export const WORK_PHASE_LOOKS: Record<WorkPhase, PhaseLook> = {
  queued: { tone: 'primary', family: 'active', lineKey: 'queued', world: 'blocked', label: 'Waiting to start' },
  thinking: { tone: 'primary', family: 'active', lineKey: 'thinking', world: 'working', label: 'Thinking' },
  searching: { tone: 'primary', family: 'active', lineKey: 'searching', world: 'working', label: 'Searching' },
  tool: { tone: 'primary', family: 'active', lineKey: 'tool', world: 'working', label: 'Working' },
  planning: { tone: 'primary', family: 'active', lineKey: 'planning', world: 'working', label: 'Planning' },
  delegating: { tone: 'primary', family: 'active', lineKey: 'delegating', world: 'working', label: 'Handing off' },
  streaming: { tone: 'primary', family: 'active', lineKey: 'streaming', world: 'working', label: 'Writing' },
  listening: { tone: 'warn', family: 'active', lineKey: 'listening', world: 'working', label: 'Listening' },
  approval: { tone: 'warn', family: 'waiting', lineKey: 'approval', world: 'waiting_approval', label: 'Needs your OK' },
  input: { tone: 'info', family: 'waiting', lineKey: 'question', world: 'needs_input', label: 'Waiting for your answer' },
  done: { tone: 'ok', family: 'quiet', lineKey: 'done', world: 'idle', label: 'Done' },
  error: { tone: 'danger', family: 'blocked', lineKey: 'error', world: 'error', label: 'Stopped' },
  retrying: { tone: 'primary', family: 'active', lineKey: 'retrying', world: 'blocked', label: 'Retrying' },
  waiting_model: { tone: 'primary', family: 'active', lineKey: 'waiting_model', world: 'blocked', label: 'Waiting for the model' },
  idle: { tone: 'neutral', family: 'quiet', lineKey: 'idle', world: 'idle', label: 'Nothing running' },
}

/** The gateway's own states, by the family each reads as. */
export const SYSTEM_STATE_FAMILIES: Record<'idle' | 'restarting' | 'offline', PhaseFamily> = {
  idle: 'quiet',
  restarting: 'active',
  offline: 'blocked',
}

const KNOWN: ReadonlySet<string> = new Set(WORK_PHASES)

/** A wire value read as a phase, or `null` for one that is not: never a guess at a nearby one. */
export function workPhaseOf(value: unknown): WorkPhase | null {
  return typeof value === 'string' && KNOWN.has(value) ? (value as WorkPhase) : null
}

export const phaseFamily = (phase: WorkPhase): PhaseFamily => WORK_PHASE_LOOKS[phase].family
export const phaseWorld = (phase: WorkPhase): AgentActivityState => WORK_PHASE_LOOKS[phase].world
export const phaseLabel = (phase: WorkPhase): string => WORK_PHASE_LOOKS[phase].label

// ── One tone per phase, on every surface (#3833) ────────────────────────────────────────────────
//
// A surface paints what an agent is doing in its phase's tone and in no tone of its own: Agent
// World, a loop's and a run's status, the Inbox's and the bell's needs-you rows, the Projects
// board and the microphone chip. Each reads it here, in the form it paints with.

/** Each tone's colour token. `neutral` is low-emphasis on-surface, never an attention hue. */
const TONE_TOKEN: Record<PhaseTone, string> = {
  primary: '--color-primary', warn: '--color-warn', info: '--color-info', ok: '--color-ok', danger: '--color-danger',
  neutral: '--color-on-surface-low',
}
/** Each tone as a text class. */
const TONE_TEXT: Record<PhaseTone, string> = {
  primary: 'text-primary', warn: 'text-warning', info: 'text-info', ok: 'text-success', danger: 'text-danger',
  neutral: 'text-on-surface-low',
}
/** A phase's colour token (`--color-…`), for a canvas or an inline style that names one. */
export const phaseToken = (phase: WorkPhase): string => TONE_TOKEN[WORK_PHASE_LOOKS[phase].tone]
/** A phase's colour, as a CSS value. */
export const phaseColor = (phase: WorkPhase): string => `var(${phaseToken(phase)})`
/** A phase's accent: its colour, or `''` for a phase with no tone (nothing running, paused). */
export const phaseAccent = (phase: WorkPhase): string => (WORK_PHASE_LOOKS[phase].tone === 'neutral' ? '' : phaseColor(phase))
/** A phase's tone as a text class. */
export const phaseText = (phase: WorkPhase): string => TONE_TEXT[WORK_PHASE_LOOKS[phase].tone]

/** The phase each ambient world state stands for, which its tone is read from: the world of each
 *  is the state itself (`world` above). Live work held up is painted as queued work is. */
export const WORLD_PHASE: Record<AgentActivityState, WorkPhase> = {
  working: 'thinking', waiting_approval: 'approval', needs_input: 'input', error: 'error', blocked: 'queued', idle: 'idle',
}

/** The phase each workflow run status reads as, the gateway's own (`work_phase.py`,
 *  `RUN_STATUS_PHASES`): a cancel and a decline are done, a failure and an escalation errors.
 *  `tests/test_work_phase_vocabulary.py` pins it to the gateway's in both directions. A loop's are
 *  beside its words, in `loopStatus.ts`, the one loop-status registry. */
export const RUN_STATUS_PHASES: Readonly<Record<string, WorkPhase>> = {
  draft: 'queued',
  running: 'thinking',
  paused: 'idle',
  needs_input: 'input',
  complete: 'done',
  failed: 'error',
  cancelled: 'done',
  escalated: 'error',
  declined: 'done',
}

/** The run state the gateway serves a chat that has never run (`chat_phase.idle_payload`): what a
 *  page holds for a chat it has just made, before the gateway has said anything of it. */
export function idleRunState(session: string): RunState {
  return {
    work: `chat:${session}`, session, kind: 'chat', phase: 'idle', line: '', since: null, turn: null,
    last_step_at: null, stopping: false, steerable: false, last_outcome: null, queued_messages: 0, seq: 0,
    retry: null, queue: null,
  }
}

/** Whether a `work_phase` frame is a chat's own run state. The gateway sends one frame type for every
 *  kind of work (`dashboard/work_phases.py`): a helper's names the chat it works for in `session`, as
 *  its other frames do, and a loop's or a workflow run's names no chat. Neither is the chat's turn,
 *  so a page reads a frame as its chat's run state only when this says it is one: a chat's own, or
 *  one that names no other kind. */
export function isAChatsPhase(frame: { kind?: unknown; [field: string]: unknown } | undefined | null): boolean {
  return !!frame && (frame.kind === undefined || frame.kind === 'chat')
}

/** A `work_phase` frame read as what its turn is doing now: the phase and its line while the turn
 *  runs, `null` once it has ended (its last frame says how) or for a frame that names no phase. */
export function livePhaseOf(frame: { phase?: unknown; line?: unknown; turn?: unknown } | undefined): { phase: WorkPhase; line: string } | null {
  const phase = workPhaseOf(frame?.phase)
  return phase && frame?.turn ? { phase, line: String(frame.line ?? '') } : null
}

/** The phases of a step the agent takes: its call says them on its own card, so a feed that shows
 *  the card need not say them again. */
export const isStepPhase = (phase: WorkPhase): boolean => phase === 'tool' || phase === 'searching'

/** What a feed of a turn's steps gains from a `work_phase` frame, given what the turn last said
 *  (`said`, `''` for nothing yet): its line, once per change of phase or line while the turn runs.
 *  Nothing for the gateway saying the same phase again (it does every fifteen seconds), for a turn
 *  that has ended, for a phase the feed shows its own way (`shownElsewhere`: by default a step's,
 *  whose card says it), or for the thinking (planning, in plan mode) the gateway says after every
 *  step's result, which would put a row after each card. `said` comes back for the next frame. */
export function phaseFeedLine(
  frame: { phase?: unknown; line?: unknown; turn?: unknown } | undefined,
  said: string,
  shownElsewhere: (phase: WorkPhase) => boolean = isStepPhase,
): { said: string; line: string | null } {
  const live = livePhaseOf(frame)
  const now = live ? `${live.phase} ${live.line}` : ''
  if (now === said || !live || !live.line || shownElsewhere(live.phase)) return { said: now, line: null }
  const before = workPhaseOf(said.slice(0, said.indexOf(' ')))
  const afterAStep = before !== null && isStepPhase(before) && (live.phase === 'thinking' || live.phase === 'planning')
  return { said: now, line: afterAStep ? null : live.line }
}
