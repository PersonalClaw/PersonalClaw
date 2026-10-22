/** The buddy orb's poses and transitions (#3891), as one table.
 *
 *  A pose is #3830's still for a state (`buddyRig/poseSet.ts`: the agent orb's drawing for it, its
 *  dots' density, spread, droop and turn, the whole orb's rise or fall, the badge and the wash), so
 *  the orb the app draws is the one the pose sources record. A transition names the preset that
 *  moves it, one of `design/motion.ts`'s, so the dials and reduced motion reach every one, and the
 *  timing token it is held to (`lib/buddy/timing.ts`): a truth change shows within one `beat`, and
 *  the done flourish plays inside `finishedHold`. No spring is written here
 *  (`docs/design/buddy/motion.md`, the transitions table). */

import type { Transition } from 'framer-motion'
import { physics, spring } from '../../design/motion'
import { familyOf, stillOf, wordsOf, type BuddyState, type Frame } from '../../buddyRig/poseSet'
import type { Truth } from '../../lib/buddy/machine'
import type { TimerName } from '../../lib/buddy/timing'
import type { Pose, PoseKey } from './useOrbPose'

export { familyOf, stillOf, wordsOf, type BuddyState, type Frame }

/** The presets a transition may name: the four physics presets and the effects curve. */
export type Preset = 'snappy' | 'smooth' | 'fluid' | 'playful' | 'effects'

export interface OrbTransition {
  preset: Preset
  /** The timing token the transition is held to. */
  token: TimerName
  /** What it moves, in the transitions table's words. */
  does: string
}

/** The renderer's transitions: what moves the orb from one pose to the next. */
export const ORB_TRANSITIONS = {
  phase: { preset: 'snappy', token: 'beat', does: 'a phase change: the dots take the new phase, the badge pops' },
  alert: { preset: 'snappy', token: 'beat', does: 'an alert arrives: an approval draws the dots in, a question lifts the orb' },
  wash: { preset: 'effects', token: 'beat', does: "the alert's wash rises or falls" },
  error: { preset: 'smooth', token: 'beat', does: 'an error: the dots sink toward the lower edge' },
  done: { preset: 'playful', token: 'finishedHold', does: 'done: the sparse dots swirl once, scaled by expressiveness' },
  listening: { preset: 'snappy', token: 'beat', does: 'listening: the orb takes the listening drawing and the microphone' },
} as const satisfies Record<string, OrbTransition>

export type OrbTransitionName = keyof typeof ORB_TRANSITIONS

/** A preset's transition, read when the move starts, so the dials and reduced motion apply. */
export function presetTransition(preset: Preset): Transition {
  return preset === 'effects' ? spring.effects : physics[preset]
}

/** Which transition moves `key` into `to`'s pose. */
export function transitionNameOf(to: BuddyState, key: PoseKey): OrbTransitionName {
  if (key === 'wash') return 'wash'
  if (to === 'error' || to === 'offline') return 'error'
  if (to === 'done') return 'done'
  if (to === 'approval' || to === 'input') return 'alert'
  if (to === 'listening') return 'listening'
  return 'phase'
}

export function transitionOf(to: BuddyState, key: PoseKey): Transition {
  return presetTransition(ORB_TRANSITIONS[transitionNameOf(to, key)].preset)
}

/** `state`'s pose: its still's values for each pose parameter. */
export function poseOf(state: BuddyState): Pose {
  const { density, spread, sink, lift, swirl, wash } = stillOf(state)
  return { density, spread, sink, lift, swirl, wash }
}

/** The state the orb shows for what the state machine's line says (`lib/buddy/machine.ts`). The
 *  lines the machine adds of its own (Connected again, answered elsewhere, the return summary) show
 *  on the orb that is under them. */
export function orbStateOf(t: Truth, under: BuddyState = 'idle'): BuddyState {
  switch (t.kind) {
    case 'phase': return t.phase
    case 'alert': return t.alert.kind === 'error' ? 'error' : t.alert.kind
    case 'listening': return 'listening'
    case 'done': return 'done'
    case 'restarting': return 'restarting'
    case 'offline': return 'offline'
    case 'idle': return 'idle'
    default: return under
  }
}
