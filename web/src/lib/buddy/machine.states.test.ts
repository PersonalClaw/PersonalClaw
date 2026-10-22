import { describe, expect, it } from 'vitest'
import { clockTime } from '../epoch'
import { A1, ON, T, W1, W2, run, type Reducer } from '../../test/buddyMachine'
import type { BuddyInput } from './inputs'
import { lineOf } from './lines'
import { reduce, viewOf, type BuddyState } from './machine'

// ── The nine shipping states, each with what it shows (#3890's approved update) ────────────────
//
// "The shipping machine handles quiet, active, waiting, blocked, hidden, focused, interrupted,
// failed, and recovered states." Each is driven from a cold start through the inputs that reach it,
// and asserts its family and its exact line on the open card (nothing at all while hidden), and the
// system states their copy.md lines. The control: a reducer that reads every phase as one family
// fails the suite.

const workStarts: BuddyInput[] = [{ type: 'start', work: W1 }, { type: 'timer', name: 'showDelay' }]

interface Shown { family: string | null; line: string | null }

function shown(inputs: BuddyInput[], reducer: Reducer, from: BuddyState = ON): Shown {
  const view = viewOf(run(from, inputs, reducer).state)
  return { family: view.family, line: view.truth ? lineOf(view.truth) : null }
}

/** Each shipping state: how it is reached, and what it shows. */
const STATES: [string, BuddyInput[], Shown][] = [
  ['quiet', [...workStarts, { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: false }, { type: 'finish', id: 'w1', how: 'done' }],
    { family: 'quiet', line: 'Nothing running' }],
  ['active', workStarts, { family: 'active', line: 'Release notes: thinking' }],
  ['waiting', [...workStarts, { type: 'alert', alert: A1 }], { family: 'waiting', line: 'Release notes needs your OK: git push' }],
  ['blocked', [...workStarts, { type: 'connection', state: 'lost' }, { type: 'timer', name: 'offlineGrace' }],
    { family: 'blocked', line: `Can't reach PersonalClaw since ${clockTime(T + 2)}` }],
  ['hidden', [], { family: null, line: null }],
  ['focused', [...workStarts, { type: 'start', work: W2 }, { type: 'phase', id: 'w2', phase: 'searching', line: 'the web' }],
    { family: 'active', line: 'Release notes: thinking' }],
  ['interrupted', [...workStarts, { type: 'phase', id: 'w1', phase: 'tool', line: 'Running the tests' }, { type: 'alert', alert: A1 },
    { type: 'answer', id: 'a1', choice: { kind: 'allow' } }], { family: 'active', line: 'Release notes: Running the tests' }],
  ['failed', [...workStarts, { type: 'finish', id: 'w1', how: 'error', reason: 'the site refused' }],
    { family: 'blocked', line: 'Release notes stopped: the site refused' }],
  ['recovered', [...workStarts, { type: 'connection', state: 'lost' }, { type: 'timer', name: 'offlineGrace' }, { type: 'connection', state: 'back' }],
    { family: 'active', line: 'Connected again' }],
]

/** A reducer under which every phase reads as one family: every piece of work thinks, and every
 *  alert is an approval. */
const oneFamily: Reducer = (s, input, now) => {
  const step = reduce(s, input, now)
  return {
    ...step,
    state: {
      ...step.state,
      live: step.state.live.map((w) => ({ ...w, phase: 'thinking' as const })),
      queue: step.state.queue.map((a) => ({ ...a, kind: 'approval' as const })),
    },
  }
}

describe('the shipping states', () => {
  it.each(STATES)('%s shows its family and its exact line', (_name, inputs, expected) => {
    expect(shown(inputs, reduce)).toEqual(expected)
  })

  it('an interruption shows the alert first, in its own family and words', () => {
    expect(shown([...workStarts, { type: 'alert', alert: A1 }], reduce)).toEqual({ family: 'waiting', line: 'Release notes needs your OK: git push' })
  })

  it('a higher-priority truth replaces a cosmetic state on the same input', () => {
    const done = run(ON, [...workStarts, { type: 'finish', id: 'w1', how: 'done' }], reduce)
    expect(viewOf(done.state).truth?.kind, 'the done beat is showing').toBe('done')
    const step = reduce(done.state, { type: 'alert', alert: A1 }, T + 9)
    expect(viewOf(step.state).family, 'the alert replaced it in that step').toBe('waiting')
  })

  it('fail against a reducer that reads every phase as one family', () => {
    const wrong = STATES.filter(([, inputs, expected]) => JSON.stringify(shown(inputs, oneFamily)) !== JSON.stringify(expected)).map(([name]) => name)
    expect(wrong, 'the states the one-family reducer gets wrong').toEqual(expect.arrayContaining(['failed']))
    expect(wrong.length).toBeGreaterThan(0)
  })
})

describe('the system states', () => {
  it('restarting shows the active family with "PersonalClaw is restarting"', () => {
    expect(shown([...workStarts, { type: 'connection', state: 'restarting' }], reduce)).toEqual({ family: 'active', line: 'PersonalClaw is restarting' })
  })

  it("offline shows the blocked family with \"Can't reach PersonalClaw since <time>\", and recovery \"Connected again\"", () => {
    const offline = [...workStarts, { type: 'connection', state: 'lost' }, { type: 'timer', name: 'offlineGrace' }] as BuddyInput[]
    expect(shown(offline, reduce).line).toBe(`Can't reach PersonalClaw since ${clockTime(T + 2)}`)
    expect(shown([...offline, { type: 'connection', state: 'back' }], reduce).line).toBe('Connected again')
  })

  it('an expired alert leaves with "<work>: the request expired"', () => {
    expect(shown([...workStarts, { type: 'alert', alert: A1 }, { type: 'resolved', id: 'a1', how: 'expired' }], reduce))
      .toEqual({ family: 'quiet', line: 'Release notes: the request expired' })
  })
})
