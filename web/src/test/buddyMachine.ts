// The buddy state machine's test fixtures (#3890): the work, the alerts and the situations the
// matrix, invariant, state and replay tests build on, and one way to run inputs through a reducer.

import type { Alert, BuddyInput, Effect, Work } from '../lib/buddy/inputs'
import { INITIAL, reduce, type BuddyState, type Situation, type Step } from '../lib/buddy/machine'

/** The tests' clock: inputs land at `T`, and the work started a little before. */
export const T = 10_000_000

export const work = (id: string, name: string, over: Partial<Work> = {}): Work =>
  ({ id, name, phase: 'thinking', line: '', startedAt: T - 60_000, ...over })

export const W1 = work('w1', 'Release notes', { startedAt: T - 120_000 })
export const W2 = work('w2', 'Nightly digest', { phase: 'tool', line: 'Reading the inbox', startedAt: T - 60_000 })
export const W3 = work('w3', 'Tidy downloads', { startedAt: T })

export const alert = (id: string, over: Partial<Alert> = {}): Alert => ({
  id, kind: 'approval', workId: 'w1', work: 'Release notes', brief: 'git push', route: `/api/approvals/${id}/answer`,
  consequence: 2, raisedAt: T - 5_000, ...over,
})

export const A1 = alert('a1')
export const A2 = alert('a2', { workId: 'w2', work: 'Nightly digest', brief: 'send_email', consequence: 1, raisedAt: T - 1_000 })

/** Buddy status on, and nothing else. */
export const ON: BuddyState = { ...INITIAL, enabled: true }
export const at = (over: Partial<BuddyState>): BuddyState => ({ ...ON, ...over })

const busy = { live: [W1, W2], focus: 'w1' } as const

/** One state for each situation of the matrix, as its tests build it. */
export const SITUATION_STATES: Readonly<Record<Situation, () => BuddyState>> = {
  hidden: () => ON,
  peek: () => at({ presence: 'peek', live: [W1], focus: 'w1' }),
  compact: () => at({ presence: 'compact', ...busy }),
  expanded: () => at({ presence: 'expanded', ...busy, running: ['collapse'] }),
  alert: () => at({ presence: 'expanded', ...busy, queue: [A1], shown: 'a1', beneath: 'compact' }),
  finished: () => at({
    presence: 'compact', live: [W2], focus: 'w1', running: ['finishedHold'],
    finished: { work: W1, count: 1, at: T - 1_000 },
  }),
  floating: () => at({ presence: 'floating', docked: false, ...busy }),
  sleeping: () => at({ presence: 'sleeping', docked: false }),
  listening: () => at({ presence: 'compact', ...busy, listening: true }),
  dragging: () => at({ presence: 'compact', ...busy, dragging: true }),
  muted: () => at({ presence: 'compact', ...busy, muted: true }),
  offline: () => at({ presence: 'compact', live: [W1], focus: 'w1', connection: 'offline', lostAt: T - 10_000 }),
}

export type Reducer = (state: BuddyState, input: BuddyInput, now: number) => Step

/** `inputs` through `reducer` from `state`, each a millisecond after the last: every step, the
 *  final state and every effect asked for on the way. */
export function run(state: BuddyState, inputs: readonly BuddyInput[], reducer: Reducer = reduce, now = T) {
  const steps: Step[] = []
  let s = state
  inputs.forEach((input, i) => {
    const step = reducer(s, input, now + i)
    steps.push(step)
    s = step.state
  })
  const effects: Effect[] = steps.flatMap((step) => step.effects)
  return { state: s, steps, effects }
}
