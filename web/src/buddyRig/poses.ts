/** The poses both rig candidates draw (#3887), as one table, so the spike compares two ways of
 *  drawing the same poses (`docs/design/buddy/character.md`, "The poses").
 *
 *  A pose is what the orb's dots and the whole orb do for a state, on top of the agent orb's own
 *  look for its phase (`ORB_LOOKS`): how dense the dots are, how tightly they are drawn in, how far
 *  they crowd toward the lower edge, how far the whole orb lifts or sinks, the done flourish's swirl,
 *  and the alert's wash. Every amplitude goes through `expr()`, and every move is one of the app's
 *  presets, picked by the transitions table (`docs/design/buddy/motion.md`), so the two dials and
 *  reduced motion reach every one of them and no spring is written here. */

import type { Transition } from 'framer-motion'
import type { OrbState } from 'thinking-orbs'
import { expr, physics, spring } from '../design/motion'
import { WORK_PHASE_LOOKS, phaseLabel, type PhaseFamily, type PhaseTone } from '../lib/workPhase'
import { ORB_LOOKS, type AgentOrbSize } from '../ui/AgentOrb'

/** The phases the spike plays: each of the four families, and two of the active family's drawings. */
export type RigPhase = 'idle' | 'thinking' | 'tool' | 'approval' | 'input' | 'error' | 'done'
export const RIG_PHASES: readonly RigPhase[] = ['idle', 'thinking', 'tool', 'approval', 'input', 'error', 'done']

/** The badge's marks the spike draws: the working mark, the three alert marks and the check. */
export type BadgeMark = 'working' | 'approval' | 'input' | 'error' | 'done'
export const BADGE_GLYPHS: Record<BadgeMark, string> = { working: '•••', approval: '!', input: '?', error: '×', done: '✓' }

export interface RigLook {
  family: PhaseFamily
  /** The agent orb's drawing for the phase. */
  drawing: OrbState
  /** Whether the dots move: only in the active family, as the agent orb's do. */
  moves: boolean
  ink: 'alive' | 'rest'
  badge: BadgeMark | null
  /** The badge's and the wash's tone. */
  tone: PhaseTone
  label: string
}

const BADGES: Record<RigPhase, BadgeMark | null> = {
  idle: null, thinking: 'working', tool: 'working', approval: 'approval', input: 'input', error: 'error', done: 'done',
}

/** The agent orb's look for `phase`, with the badge the buddy adds. */
export function rigLook(phase: RigPhase): RigLook {
  const orb = ORB_LOOKS[phase]
  const { family, tone } = WORK_PHASE_LOOKS[phase]
  return { family, drawing: orb.drawing, moves: orb.moves, ink: orb.ink, badge: BADGES[phase], tone, label: phaseLabel(phase) }
}

/** What a pose does to the dots and the whole orb. Every value is a plain number a preset moves. */
export interface Pose {
  /** The share of the drawing's dots shown: 1 full, less for quiet's sparse orb. */
  density: number
  /** How far the dots sit from the centre: 1 as drawn, less when an approval draws them in. */
  spread: number
  /** 0 to 1: how far the dots crowd toward the lower edge, as an error's droop. */
  sink: number
  /** The whole orb's rise (negative) or fall, as a share of its size: a question lifts it. */
  lift: number
  /** The done flourish: how far the dots have turned about the centre, in turns. */
  swirl: number
  /** 0 to 1: the alert's wash, rising from the orb's lower edge in the alert's tone. */
  wash: number
}
export type PoseKey = keyof Pose
export const POSE_KEYS: readonly PoseKey[] = ['density', 'spread', 'sink', 'lift', 'swirl', 'wash']

/** Quiet's dots: sparse, in the neutral. */
const SPARSE = 0.45

/** `phase`'s pose at the current expressiveness. A function, read when the pose is entered, so the
 *  dial is honoured at every change. */
export function poseOf(phase: RigPhase): Pose {
  const pose: Pose = { density: 1, spread: 1, sink: 0, lift: 0, swirl: 0, wash: 0 }
  switch (phase) {
    case 'idle': return { ...pose, density: SPARSE }
    case 'thinking':
    case 'tool': return pose
    case 'approval': return { ...pose, spread: 1 - expr(0.26, 0.35), wash: 1 }
    case 'input': return { ...pose, lift: -expr(0.12, 0.35), wash: 1 }
    case 'error': return { ...pose, sink: expr(0.7, 0.35), wash: 1 }
    case 'done': return { ...pose, density: SPARSE, swirl: expr(1, 0.35) }
  }
}

/** The transition that moves `key` into `to`'s pose, from the transitions table: the alert's wash
 *  on the effects curve; an error's droop on `smooth`; done's flourish on `playful`; every other
 *  phase change, an alert's hold or raise among them, on `snappy`. */
export function poseTransition(to: RigPhase, key: PoseKey): Transition {
  if (key === 'wash') return spring.effects
  if (to === 'error') return physics.smooth
  if (to === 'done') return physics.playful
  return physics.snappy
}

/** The focused card: it opens on `playful`, a slight overshoot, and closes on the effects curve. */
export function cardTransition(open: boolean): Transition {
  return open ? physics.playful : spring.effects
}

/** One step of the loop the measurement plays: a phase to enter, or the focused card. */
export type RigStep = { phase: RigPhase } | { card: boolean }

/** Into each of the four families and back to quiet, then the focused card open and closed. */
export const RIG_LOOP: readonly RigStep[] = [
  { phase: 'thinking' }, { phase: 'tool' }, { phase: 'approval' }, { phase: 'input' },
  { phase: 'error' }, { phase: 'done' }, { phase: 'idle' }, { card: true }, { card: false },
]

/** The buddy's sizes (`docs/design/buddy/character.md`, "Constraints"). */
export type BuddySize = 18 | 24 | 48 | 96
export const BUDDY_SIZES: readonly BuddySize[] = [18, 24, 48, 96]

/** Which of the agent orb's two tuned drawings a buddy size shares: 18 and 24 px draw from the
 *  20 px drawing's dot counts and radii, 48 and 96 px from the 64 px drawing's, each at its own size
 *  (`docs/design/buddy/decisions/rig.md`, "Sizes"). The tray's stills and the presence poses' own
 *  sizes (16 to 22 px, and 56) follow the same line. */
export function tunedDrawingFor(size: number): AgentOrbSize {
  return size < 32 ? 20 : 64
}

/** The most frames a second the buddy paints its dots at. The agent orb in chat draws every frame
 *  the display does; the buddy, on screen for as long as the app runs, paints at most this many,
 *  which takes its loop from about 11% of a core to about the animating CPU bar's 5%
 *  (`docs/design/buddy/decisions/rig.md`). */
export const BUDDY_FPS = 30
