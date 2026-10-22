/** A rig candidate's pose (#3887): the buddy orb's pose (`ui/buddy/useOrbPose.ts`) on the rig's
 *  table, each phase's pose moved into by the transition the table names (`poses.ts`). The done
 *  flourish turns on from wherever the dots are, so it never unwinds: every other phase leaves the
 *  turn where it is. `dials` is any value that changes when the motion dials do, so a pose re-reads
 *  its amplitudes and its presets when the owner moves one. */

import { useOrbPose, type PoseHandle } from '../ui/buddy/useOrbPose'
import { poseOf, poseTransition, type RigPhase } from './poses'

export type { PoseHandle, PoseValues } from '../ui/buddy/useOrbPose'

export function usePose(phase: RigPhase, dials: string, fps?: number | null): PoseHandle {
  return useOrbPose(`${phase}|${dials}`, {
    targetOf: (key, now) => (key === 'swirl' ? now + poseOf(phase).swirl : poseOf(phase)[key]),
    transitionOf: (key) => poseTransition(phase, key),
  }, fps)
}
