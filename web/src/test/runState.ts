import type { RunState } from '../lib/api'
import { idleRunState } from '../lib/workPhase'

// A chat's run state as the gateway serves it (`run_state` on the chat list and a chat's detail, and
// the payload of a `work_phase` frame), for a test to hand a page.

/** When the turns these build started. */
export const TURN_STARTED = '2026-01-01T09:00:00+00:00'

/** A chat with nothing running: idle, as a chat that never ran here is served. */
export function runState(over: Partial<RunState> = {}): RunState {
  return { ...idleRunState(over.session ?? 's'), ...over }
}

/** A chat whose turn is running, thinking unless told otherwise. */
export function runningState(over: Partial<RunState> = {}): RunState {
  return runState({
    phase: 'thinking',
    line: 'Thinking',
    since: TURN_STARTED,
    turn: { message_id: TURN_STARTED, started_at: TURN_STARTED },
    seq: 1,
    ...over,
  })
}

/** A chat whose last turn ended *outcome*, with nothing running now. */
export function endedState(outcome: NonNullable<RunState['last_outcome']>, over: Partial<RunState> = {}): RunState {
  return runState({ since: TURN_STARTED, last_outcome: outcome, seq: 2, ...over })
}
