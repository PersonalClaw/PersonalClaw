/** The buddy's sizes and frame rate (`docs/design/buddy/character.md`, "Constraints"). What a pose
 *  draws is the agent orb's halftone state for what the buddy shows (`ui/buddy/buddyHalftone.ts`),
 *  moved as a whole by its pose (`poseSet.ts`). */

import { ORB_FPS } from '../ui/orbDots'

/** The buddy's sizes (`docs/design/buddy/character.md`, "Constraints"). */
export type BuddySize = 18 | 24 | 48 | 96
export const BUDDY_SIZES: readonly BuddySize[] = [18, 24, 48, 96]

/** The most frames a second the buddy paints its dots at: the agent orb's, since both draw through
 *  one loop (`ui/orbDots.ts`). The buddy, on screen for as long as the app runs, paints at most this
 *  many, which takes its loop from about 11% of a core to about the animating CPU bar's 5%
 *  (`docs/design/buddy/decisions/rig.md`). */
export const BUDDY_FPS = ORB_FPS
