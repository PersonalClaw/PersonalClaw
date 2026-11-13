import type { OrbState as OrbDrawing } from 'thinking-orbs/engine'
import { ORB_STATE_OF, type OrbBadge, type OrbStateName } from '../lib/orbState'
import { WORK_PHASES, type WorkPhase } from '../lib/workPhase'
import { MOVES } from '../ui/orb/clawRing'

/** The desktop buddy's dot drawings, by phase. The chat's orb draws the claw ring
 *  (`ui/orb/clawRing.ts`); the buddy's orb still draws the `thinking-orbs` package's dots, its
 *  poses built on them (`poses.ts`, `poseSet.ts`), until it moves to the claw ring too. It reads the
 *  same mapping as the chat's orb (`lib/orbState.ts`), so the two show every phase as the same one
 *  of the five states: the dots move exactly where the ring does, in one drawing a state, and take
 *  the same ink and badge. */

/** The tuned drawings the buddy's sizes draw from: the 20 px one and the 64 px one. */
export type TunedDrawing = 20 | 64

export interface OrbLook {
  /** The `thinking-orbs` drawing. */
  drawing: OrbDrawing
  /** Whether it animates: only while its state thinks or works, as the chat's orb does. */
  moves: boolean
  /** Alive while an agent works or waits on the owner; at rest where nothing is alive. */
  ink: 'alive' | 'rest'
  badge?: OrbBadge
}

/** Each state's dots: thinking breathes and working works; ready, waiting on you and done are the
 *  breathing drawing's still frame. */
const DRAWING: Readonly<Record<OrbStateName, OrbDrawing>> = { ready: 'breathing', thinking: 'breathing', working: 'working', waiting: 'breathing', done: 'breathing' }

/** Every phase of the contract, as the buddy's dots show it: its state's drawing, motion, ink and
 *  badge, from the one mapping. */
export const ORB_LOOKS: Readonly<Record<WorkPhase, OrbLook>> = Object.freeze(Object.fromEntries(WORK_PHASES.map((phase) => {
  const { state, ink, badge } = ORB_STATE_OF[phase]
  return [phase, Object.freeze({ drawing: DRAWING[state], moves: MOVES[state], ink, ...(badge ? { badge } : {}) })]
})) as Record<WorkPhase, OrbLook>)
