/** The buddy orb's states (#3891): what the state machine's truth shows as, and the pose table's
 *  stills (`buddyRig/poseSet.ts`). A change of state is the agent orb's own change, every dot moving
 *  on the smooth spring (`ui/orb/useSceneMorph.ts`), and done's swirl and bump are its arrival's;
 *  the buddy writes no spring of its own (`docs/design/buddy/motion.md`). */
import type { Truth } from '../../lib/buddy/machine'
import { familyOf, movesOf, stillOf, wordsOf, type BuddyState, type Frame } from '../../buddyRig/poseSet'

export { familyOf, movesOf, stillOf, wordsOf, type BuddyState, type Frame }

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
