/** The buddy's poses (`docs/design/buddy/character.md`, "The poses"): what a pose does to the
 *  claw ring's strokes and the whole orb, on top of the agent orb's own form for the state
 *  (`lib/orbState.ts`): how thin quiet draws the strokes, how far an approval draws them in, how far
 *  an error sags them, the whole orb's rise or fall, done's turn, and the alert's wash. Every
 *  amplitude goes through `expr()`, and every move is one of the app's presets, picked by the
 *  transitions table (`docs/design/buddy/motion.md`), so the two dials and reduced motion reach
 *  every one of them and no spring is written here. */

import { expr } from '../design/motion'
import { ORB_FPS } from '../ui/orbDots'
import type { Pose } from '../ui/buddy/useOrbPose'

/** The phases with a pose of their own: each of the four families, and the active family's two
 *  states. Every other phase takes its family's. */
export type RigPhase = 'idle' | 'thinking' | 'tool' | 'approval' | 'input' | 'error' | 'done'
export const RIG_PHASES: readonly RigPhase[] = ['idle', 'thinking', 'tool', 'approval', 'input', 'error', 'done']

/** What a pose does to the strokes and the whole orb, every value a plain number a preset moves:
 *  quiet's thinness (1 full, less for quiet's thin strokes), how far the strokes sit from the
 *  centre (less when an approval draws them in), how far they sag (an error's droop), the whole
 *  orb's rise (negative, a question) or fall as a share of its size, done's turn, and the alert's
 *  wash rising from the lower edge. The buddy orb's own (`ui/buddy/useOrbPose.ts`); what each does
 *  to the strokes is `ui/buddy/buddyRing.ts`'s `posedForm`. */
export { POSE_KEYS, type Pose, type PoseKey } from '../ui/buddy/useOrbPose'

/** Quiet's strokes: thin, in the neutral. */
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

/** The buddy's sizes (`docs/design/buddy/character.md`, "Constraints"). */
export type BuddySize = 18 | 24 | 48 | 96
export const BUDDY_SIZES: readonly BuddySize[] = [18, 24, 48, 96]

/** The most frames a second the buddy paints its strokes at: the agent orb's, since both draw through
 *  one loop (`ui/orbDots.ts`). The buddy, on screen for as long as the app runs, paints at most this
 *  many, which takes its loop from about 11% of a core to about the animating CPU bar's 5%
 *  (`docs/design/buddy/decisions/rig.md`). */
export const BUDDY_FPS = ORB_FPS
