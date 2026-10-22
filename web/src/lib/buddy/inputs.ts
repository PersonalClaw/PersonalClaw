// ── What the buddy's state machine hears, and what it asks its host to do ──────────────────────
//
// `docs/design/buddy/state-machine.md`, "Inputs": the machine is a pure function of its state and
// one input at a time, and every input comes from exactly one source. The truth inputs are levels,
// not one-shot events: what the work is doing now and what is waiting now, re-read on every
// doorbell, so a change that arrives while the orb is busy is shown the moment it can be.
//
// The shapes follow their contracts, not their transport: a piece of live work is a running list
// row's fields (`RunningRow`, #3889, phases from #3833's `WORK_PHASES`), and an alert is a waiting
// list item's (#3888). The host turns its frames into these; the tests feed them directly.

import type { WorkPhase } from '../workPhase'
import type { TimerName } from './timing'

/** One piece of live work, from the phase contract. */
export interface Work {
  id: string
  /** A chat's title, a loop's goal, a run's name. */
  name: string
  phase: WorkPhase
  /** What it is doing, in the gateway's words for its phase. */
  line: string
  /** When it started, in the machine's clock (ms). */
  startedAt: number
  /** While it hands off, how many helpers it hands off to. */
  helpers?: number
}

export type AlertKind = 'approval' | 'input' | 'error'

/** One thing waiting on the owner, from the "waiting on you" list. */
export interface Alert {
  id: string
  kind: AlertKind
  /** The work it belongs to: its id, and its name as the line says it. */
  workId: string
  work: string
  /** The approval's tool, the question, or the error's reason. */
  brief: string
  /** The item's own answer route: the only place an answer goes. */
  route: string
  /** The waiting list's order: the greater consequence first, then the older. */
  consequence: number
  raisedAt: number
  /** A delete, an overwrite outside the workspace, a send to other people, or spending: it answers
   *  only after its confirmation. */
  broadReach?: boolean
}

/** The answers an alert takes: Allow and Deny for an approval, an option or the text for a question. */
export type AnswerChoice =
  | { kind: 'allow' } | { kind: 'deny' } | { kind: 'option'; option: string } | { kind: 'send'; text: string }

export type BuddyInput =
  // The phase contract (#3833), per piece of live work.
  | { type: 'start'; work: Work }
  | { type: 'phase'; id: string; phase: WorkPhase; line: string }
  | { type: 'finish'; id: string; how: 'done' | 'error' | 'stopped' | 'declined'; reason?: string }
  // The waiting list (#3888).
  | { type: 'alert'; alert: Alert }
  | { type: 'resolved'; id: string; how: 'answered' | 'elsewhere' | 'expired' | 'withdrawn' }
  // The connection.
  | { type: 'connection'; state: 'lost' | 'back' | 'restarting' }
  // Push-to-talk, every capture source.
  | { type: 'listen' } | { type: 'unlisten' }
  // The desktop shell.
  | { type: 'pointer'; at: 'zone' | 'orb' | 'away' }
  | { type: 'click' } | { type: 'hits' }
  | { type: 'drag'; phase: 'start' | 'end'; docked?: boolean }
  | { type: 'away' } | { type: 'return' }
  // The owner's controls. An answer comes only from an explicit click on its control, or a key.
  | { type: 'answer'; id: string; choice: AnswerChoice; confirmed?: boolean }
  | { type: 'key'; key: 'ModEnter' | 'Enter' | 'Escape' | 'Tab'; windowFocused: boolean; onAlert: boolean }
  | { type: 'hide' } | { type: 'show' }
  | { type: 'enabled'; on: boolean }
  | { type: 'quietHours'; on: boolean }
  // Timers, which run only while the buddy is visible.
  | { type: 'timer'; name: TimerName }

/** The short moves the renderer plays for a change. Cosmetic: none of them acts. */
export type Beat = 'phase' | 'alertEntrance' | 'nod' | 'shake' | 'done' | 'wake' | 'countPop' | 'leave' | 'listen'

/** What the machine asks its host to do. The answer is the one effect that reaches the network, on
 *  the item's own route, and only for an explicit answer (invariant 1). */
export type Effect =
  | { kind: 'timer'; name: TimerName; start: boolean }
  | { kind: 'answer'; id: string; route: string; choice: AnswerChoice }
  | { kind: 'reread' }
  | { kind: 'beat'; beat: Beat }
  /** A production build's answer to a matrix cell that cannot occur: nothing changed, and the
   *  host logs it. A development build and the tests throw `CannotOccur` instead. */
  | { kind: 'ignored'; situation: string; event: string }
