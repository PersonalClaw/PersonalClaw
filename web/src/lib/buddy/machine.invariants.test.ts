// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { readSource } from '../../test/sourceTree'
import { A1, A2, ON, SITUATION_STATES, T, W1, W2, W3, alert, at, run, type Reducer } from '../../test/buddyMachine'
import type { BuddyInput, Effect } from './inputs'
import { lineOf } from './lines'
import {
  CannotOccur, SITUATIONS, isHidden, reduce, truthOf, viewOf, type BuddyState, type Situation, type Truth,
} from './machine'
import { TIMER_NAMES, TIMINGS, timingFor } from './timing'

// ── The 15 invariants of `docs/design/buddy/state-machine.md`, each a test ──────────────────────
//
// Each invariant is a function of a reducer (or of the part of the machine it is about) returning
// what it found broken, and each is run twice: against the machine, where it finds nothing, and
// against a reducer broken the way the invariant forbids, where it must find the break. v0.3.0 has
// no play layer, so invariants 2 and 3 hold with nothing to drive, and invariant 3's broken-reducer
// control returns with play (#3894).

const situations = SITUATIONS.map((s) => [s, SITUATION_STATES[s]] as const)

/** Every input the owner, the shell, the connection or a timer can send, except an explicit click on
 *  an answer control and ⌘Enter on a focused request in the focused window. */
function everyOtherInput(s: BuddyState): BuddyInput[] {
  return [
    { type: 'start', work: W3 },
    { type: 'phase', id: s.focus ?? 'w1', phase: 'tool', line: 'Running the tests' },
    { type: 'finish', id: s.focus ?? 'w1', how: 'done' },
    { type: 'finish', id: 'w2', how: 'error', reason: 'the site refused' },
    { type: 'alert', alert: A2 },
    { type: 'resolved', id: s.shown ?? 'a9', how: 'elsewhere' },
    { type: 'resolved', id: s.shown ?? 'a9', how: 'expired' },
    { type: 'connection', state: 'lost' }, { type: 'connection', state: 'back' }, { type: 'connection', state: 'restarting' },
    { type: 'listen' }, { type: 'unlisten' },
    { type: 'pointer', at: 'zone' }, { type: 'pointer', at: 'orb' }, { type: 'pointer', at: 'away' },
    { type: 'click' }, { type: 'hits' },
    { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: true }, { type: 'drag', phase: 'end', docked: false },
    { type: 'away' }, { type: 'return' },
    { type: 'key', key: 'Enter', windowFocused: true, onAlert: true },
    { type: 'key', key: 'Escape', windowFocused: true, onAlert: true },
    { type: 'key', key: 'Tab', windowFocused: true, onAlert: true },
    { type: 'key', key: 'ModEnter', windowFocused: false, onAlert: true },
    { type: 'key', key: 'ModEnter', windowFocused: true, onAlert: false },
    { type: 'hide' }, { type: 'show' }, { type: 'enabled', on: false }, { type: 'enabled', on: true },
    { type: 'quietHours', on: true },
    ...TIMER_NAMES.map((name): BuddyInput => ({ type: 'timer', name })),
  ]
}

/** One input's step, or nothing for a cell the matrix says cannot occur. */
function stepOf(reducer: Reducer, s: BuddyState, input: BuddyInput) {
  try {
    return reducer(s, input, T)
  } catch (e) {
    if (e instanceof CannotOccur) return null
    throw e
  }
}

const answersIn = (effects: Effect[]) => effects.filter((e) => e.kind === 'answer')

/** A reducer that does what the real one does, then `also`. */
const broken = (also: (s: BuddyState, input: BuddyInput, step: ReturnType<typeof reduce>) => ReturnType<typeof reduce>): Reducer =>
  (s, input, now) => also(s, input, reduce(s, input, now))

// ── 1. Only an explicit answer sends one ───────────────────────────────────────────────────────

function invariant1(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of situations) {
    const withAlert = name === 'alert' ? state() : { ...state(), queue: [A1], shown: state().dragging || state().listening ? 'a1' : state().shown }
    for (const s of [state(), withAlert]) {
      for (const input of everyOtherInput(s)) {
        const step = stepOf(reducer, s, input)
        if (step && answersIn(step.effects).length) found.push(`${name}: ${JSON.stringify(input)} sent an answer`)
      }
    }
  }
  const shown = SITUATION_STATES.alert()
  const modEnter = (windowFocused: boolean, onAlert: boolean): BuddyInput => ({ type: 'key', key: 'ModEnter', windowFocused, onAlert })
  if (!answersIn(reducer(shown, modEnter(true, true), T).effects).length) found.push('⌘Enter on the focused request in the focused window sent nothing')
  const broad = { ...shown, queue: [{ ...A1, broadReach: true }] }
  const unconfirmed = reducer(broad, { type: 'answer', id: 'a1', choice: { kind: 'allow' } }, T)
  if (answersIn(unconfirmed.effects).length) found.push('a broad-reach approval answered before its confirmation')
  const confirmed = reducer(unconfirmed.state, { type: 'answer', id: 'a1', choice: { kind: 'allow' }, confirmed: true }, T)
  if (!answersIn(confirmed.effects).length) found.push('a confirmed broad-reach approval sent nothing')
  const stale = reducer(shown, { type: 'answer', id: 'a2', choice: { kind: 'allow' } }, T)
  if (answersIn(stale.effects).length) found.push('an answer to a request that is not the shown one was sent')
  return found
}

// ── 2. No play input makes a network request ───────────────────────────────────────────────────

/** The inputs play reacts to once it exists (#3894): hits, the pointer and a drag. */
const PLAY_INPUTS: BuddyInput[] = [
  { type: 'hits' }, { type: 'pointer', at: 'orb' }, { type: 'pointer', at: 'away' },
  { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: false },
]

function invariant2(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of situations) {
    for (const input of PLAY_INPUTS) {
      const step = stepOf(reducer, state(), input)
      const network = step?.effects.filter((e) => e.kind === 'answer' || e.kind === 'reread') ?? []
      if (network.length) found.push(`${name}: ${input.type} asked the network`)
    }
  }
  return found
}

// ── 3. No reaction starts during an alert ──────────────────────────────────────────────────────

function invariant3(reducer: Reducer): string[] {
  const found: string[] = []
  // A drag's release is left out: it opens the carried alert's bubble with the alert's own entrance.
  const reactions: BuddyInput[] = [{ type: 'hits' }, { type: 'click' }, { type: 'pointer', at: 'orb' }, { type: 'pointer', at: 'away' }, { type: 'drag', phase: 'start' }]
  for (const input of reactions) {
    const step = stepOf(reducer, SITUATION_STATES.alert(), input)
    if (step?.effects.some((e) => e.kind === 'beat')) found.push(`${input.type} played a reaction during an alert`)
  }
  return found
}

// ── 4. A truth change ends any reaction, and replaces a cosmetic state at once ─────────────────

/** The cosmetic holds v0.3.0 has: the done beat, the return summary, the floating card, the
 *  "Connected again" line and an alert's leaving line. */
const COSMETIC: [string, () => BuddyState][] = [
  ['the done beat', SITUATION_STATES.finished],
  ['the return summary', () => at({ presence: 'compact', returning: true, summary: { done: 2, failed: 0, started: 0 } })],
  ['the floating card', () => ({ ...SITUATION_STATES.floating(), card: 'open' })],
  ['Connected again', () => ({ ...SITUATION_STATES.compact(), recovered: true })],
  ['a leaving line', () => ({ ...SITUATION_STATES.compact(), leaving: { kind: 'elsewhere', work: 'Release notes' } })],
]

function invariant4(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of COSMETIC) {
    const step = reducer(state(), { type: 'alert', alert: A2 }, T)
    const t = viewOf(step.state).truth
    if (t?.kind !== 'alert') found.push(`${name}: an arriving alert did not replace it (${t?.kind})`)
  }
  return found
}

// ── 5. Listening wins, and loses no queued alert ───────────────────────────────────────────────

function invariant5(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of situations) {
    const step = stepOf(reducer, state(), { type: 'listen' })
    if (step && viewOf(step.state).truth?.kind !== 'listening') found.push(`${name}: listening did not win`)
  }
  const held = run(SITUATION_STATES.alert(), [
    { type: 'listen' }, { type: 'alert', alert: A2 }, { type: 'finish', id: 'w2', how: 'error', reason: 'x' }, { type: 'unlisten' },
  ], reducer)
  if (answersIn(held.effects).length) found.push('an alert was answered while listening')
  for (const id of ['a1', 'a2', 'error:w2']) if (!held.state.queue.some((a) => a.id === id)) found.push(`${id} was lost while listening`)
  if (viewOf(held.state).truth?.kind !== 'alert') found.push('the held alert did not return when listening ended')
  return found
}

// ── 6. One alert at a time, leaving only by the four endings ───────────────────────────────────

function invariant6(reducer: Reducer): string[] {
  const found: string[] = []
  const pokes: BuddyInput[] = [
    { type: 'alert', alert: A2 }, { type: 'timer', name: 'collapse' }, { type: 'timer', name: 'collapseCountdown' },
    { type: 'timer', name: 'finishedHold' }, { type: 'away' }, { type: 'return' }, { type: 'click' }, { type: 'hits' },
    { type: 'timer', name: 'hoverExpand' }, { type: 'key', key: 'Escape', windowFocused: true, onAlert: true },
    { type: 'pointer', at: 'away' }, { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: true },
  ]
  let s = SITUATION_STATES.alert()
  for (const input of pokes) {
    s = (stepOf(reducer, s, input) ?? { state: s }).state
    if (s.shown !== 'a1') found.push(`${JSON.stringify(input)} took the shown alert away (${s.shown})`)
  }
  if (viewOf(s).waiting !== 1) found.push(`the other alert is not counted as waiting (${viewOf(s).waiting})`)
  for (const how of ['answered', 'elsewhere', 'expired', 'withdrawn'] as const) {
    const left = reducer(SITUATION_STATES.alert(), { type: 'resolved', id: 'a1', how }, T).state
    if (left.shown === 'a1') found.push(`${how} did not end the alert`)
  }
  return found
}

// ── 7. While hidden: no timer, no frame, no pointer sampling ───────────────────────────────────

const HIDDEN: [string, () => BuddyState][] = [
  ['hidden', SITUATION_STATES.hidden],
  ['hidden while away', () => at({ live: [W1, W2], focus: 'w1', away: true })],
  ['hidden by the owner', SITUATION_STATES.muted],
  ['Buddy status off', () => ({ ...SITUATION_STATES.compact(), enabled: false, presence: 'hidden' as const })],
]

function invariant7(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of HIDDEN) {
    for (const input of everyOtherInput(state())) {
      const step = stepOf(reducer, state(), input)
      if (!step || !isHidden(step.state)) continue
      if (step.effects.some((e) => e.kind === 'timer' && e.start)) found.push(`${name}: ${JSON.stringify(input)} started a timer`)
      if (step.state.running.length) found.push(`${name}: ${JSON.stringify(input)} left ${step.state.running} running`)
    }
  }
  return found
}

// ── 8. Focus moves only when the focused work ends or an alert arrives ─────────────────────────

function invariant8(reducer: Reducer): string[] {
  const found: string[] = []
  const busy = run(ON, [
    { type: 'start', work: W1 }, { type: 'timer', name: 'showDelay' }, { type: 'start', work: W2 },
    { type: 'phase', id: 'w2', phase: 'tool', line: 'x' }, { type: 'start', work: W3 }, { type: 'phase', id: 'w3', phase: 'searching', line: '' },
  ], reducer)
  if (busy.state.focus !== 'w1') found.push(`focus jumped to ${busy.state.focus} while w1 still runs`)
  const alerted = reducer(busy.state, { type: 'alert', alert: A2 }, T)
  if (viewOf(alerted.state).truth?.kind !== 'alert' || (truthOf(alerted.state) as Extract<Truth, { kind: 'alert' }>).alert.workId !== 'w2') {
    found.push("an alert did not bring its work into focus")
  }
  const ended = run(busy.state, [{ type: 'finish', id: 'w1', how: 'done' }, { type: 'timer', name: 'finishedHold' }], reducer)
  if (ended.state.focus !== 'w2') found.push(`when w1 ended, focus went to ${ended.state.focus}, not the earliest live work`)
  return found
}

// ── 9. Presence never changes the words ────────────────────────────────────────────────────────

type TruthReader = (s: BuddyState) => Truth | null

function invariant9(read: TruthReader): string[] {
  const found: string[] = []
  const rooms = ['peek', 'compact', 'expanded', 'floating'] as const
  const truths: [string, BuddyState][] = [
    ['working', SITUATION_STATES.compact()],
    ['an alert', SITUATION_STATES.alert()],
    ['offline', SITUATION_STATES.offline()],
  ]
  for (const [name, s] of truths) {
    const lines = rooms.map((presence) => {
      const t = read({ ...s, presence, docked: presence !== 'floating' })
      return t ? lineOf(t) : ''
    })
    if (new Set(lines).size !== 1) found.push(`${name}: the words change with the room: ${JSON.stringify(lines)}`)
  }
  return found
}

// ── 10. Every situation has a reduced-motion rendering ─────────────────────────────────────────

const POSES = join(process.cwd(), '..', 'docs', 'design', 'buddy', 'poses')

function invariant10(timing: typeof timingFor): string[] {
  const found: string[] = []
  for (const name of TIMER_NAMES) {
    if (timing(name, true) !== timing(name, false)) found.push(`${name} changes length under reduced motion: it changes how, never when`)
  }
  for (const [name, state] of situations) {
    const view = viewOf(state())
    if (!view.shows) continue
    let still = ''
    try {
      still = readSource(join(POSES, `${view.family}-${view.family === 'quiet' ? 'idle' : view.family === 'active' ? 'thinking' : view.family === 'waiting' ? 'approval' : 'error'}.svg`))
    } catch {
      still = ''
    }
    if (!still.includes('data-frame="still"')) found.push(`${name}: its family ${view.family} has no reduced-motion still`)
  }
  return found
}

// ── 11. Nothing shows as running past offlineGrace once the connection is lost ─────────────────

function invariant11(reducer: Reducer): string[] {
  const found: string[] = []
  for (const name of ['peek', 'compact', 'expanded', 'floating', 'finished'] as const) {
    const lost = run(SITUATION_STATES[name](), [{ type: 'connection', state: 'lost' }, { type: 'timer', name: 'offlineGrace' }], reducer)
    const t = viewOf(lost.state).truth
    if (t?.kind === 'phase') found.push(`${name}: ${t.work.name} still shows as ${t.phase} past offlineGrace`)
  }
  return found
}

// ── 12. The badge and the wash show the truth at once, in every situation ──────────────────────

function invariant12(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of situations) {
    const step = stepOf(reducer, state(), { type: 'alert', alert: alert('a7', { consequence: 9, workId: 'w2' }) })
    if (!step) continue
    const view = viewOf(step.state)
    if (!view.shows) continue
    const shownNow = view.truth?.kind === 'alert' || view.truth?.kind === 'listening' || view.waiting > viewOf(state()).waiting
    if (!shownNow) found.push(`${name}: the alert's badge waits (${view.truth?.kind})`)
  }
  return found
}

// ── 13. Error outranks done ────────────────────────────────────────────────────────────────────

function invariant13(reducer: Reducer): string[] {
  const found: string[] = []
  const failedDuringDone = reducer(SITUATION_STATES.finished(), { type: 'finish', id: 'w2', how: 'error', reason: 'x' }, T)
  if (viewOf(failedDuringDone.state).family !== 'blocked') found.push('a failure during the done beat did not show')
  const errorShown = run(SITUATION_STATES.compact(), [
    { type: 'finish', id: 'w2', how: 'error', reason: 'x' }, { type: 'finish', id: 'w1', how: 'done' },
  ], reducer)
  if (viewOf(errorShown.state).family !== 'blocked') found.push('done masked the error')
  return found
}

// ── 14. A truth change during an entrance, a hold or an exit shows at once ─────────────────────

const HOLDS: [string, () => BuddyState][] = [
  ['the done hold', SITUATION_STATES.finished],
  ['an alert leaving', () => ({ ...SITUATION_STATES.compact(), leaving: { kind: 'elsewhere', work: 'x' }, running: ['beat'] })],
  ['the gap between alerts', () => ({ ...SITUATION_STATES.compact(), gap: true, running: ['alertGap'] })],
  ['the countdown', () => ({ ...SITUATION_STATES.expanded(), countdown: true, running: ['collapseCountdown'] })],
  ['the return summary', () => at({ presence: 'compact', live: [W1], focus: 'w1', returning: true, summary: { done: 1, failed: 0, started: 0 } })],
]

function invariant14(reducer: Reducer): string[] {
  const found: string[] = []
  for (const [name, state] of HOLDS) {
    const failed = reducer(state(), { type: 'finish', id: state().live[0]?.id ?? 'w2', how: 'error', reason: 'x' }, T)
    if (viewOf(failed.state).family !== 'blocked' && !failed.state.queue.some((a) => a.kind === 'error')) {
      found.push(`${name}: the failure was dropped`)
    }
    const listen = reducer(state(), { type: 'listen' }, T)
    if (viewOf(listen.state).truth?.kind !== 'listening') found.push(`${name}: listening waited for it`)
  }
  return found
}

// ── 15. Every shipping cell of the matrix has a test ───────────────────────────────────────────

const MATRIX = join(process.cwd(), '..', 'docs', 'design', 'buddy', 'interruption-matrix.json')
const MATRIX_TEST = join(process.cwd(), 'src', 'lib', 'buddy', 'machine.matrix.test.ts')

function invariant15(cells: { cells: Record<string, Record<string, string>>; later: [string, string][] }, testSource: string): string[] {
  const later = new Set(cells.later.map(([s, e]) => `${s}/${e}`))
  return Object.entries(cells.cells)
    .flatMap(([s, row]) => Object.keys(row).map((e) => `${s}/${e}`))
    .filter((key) => !later.has(key) && !testSource.includes(`'${key}':`))
    .map((key) => `${key} has no test`)
}

// ── The tests ──────────────────────────────────────────────────────────────────────────────────

describe('the invariants hold', () => {
  it('1. only an explicit answer, or ⌘Enter on the focused request, sends one; a broad reach confirms first', () => {
    expect(invariant1(reduce)).toEqual([])
    expect(invariant1(broken((s, input, step) => (input.type === 'key' && input.key === 'Enter' && s.shown
      ? { ...step, effects: [...step.effects, { kind: 'answer', id: s.shown, route: '/x', choice: { kind: 'allow' } }] } : step))))
      .not.toEqual([])
  })

  it('2. no play input makes a network request', () => {
    expect(invariant2(reduce)).toEqual([])
    expect(invariant2(broken((_s, input, step) => (input.type === 'hits' ? { ...step, effects: [...step.effects, { kind: 'reread' }] } : step))))
      .not.toEqual([])
  })

  it('3. no reaction starts during an alert (its broken-reducer control returns with play, #3894)', () => {
    expect(invariant3(reduce)).toEqual([])
  })

  it('4. a truth change replaces a cosmetic state on the same input', () => {
    expect(invariant4(reduce)).toEqual([])
    expect(invariant4(broken((s, input, step) => (input.type === 'alert' && s.finished ? { ...step, state: { ...s, queue: step.state.queue } } : step))))
      .not.toEqual([])
  })

  it('5. listening wins, and when it ends no queued alert was lost or answered', () => {
    expect(invariant5(reduce)).toEqual([])
    expect(invariant5(broken((s, input, step) => (input.type === 'alert' && s.listening ? { ...step, state: s } : step))))
      .not.toEqual([])
  })

  it('6. one alert shows at a time, and it leaves only by being answered, answered elsewhere, expiring or withdrawn', () => {
    expect(invariant6(reduce)).toEqual([])
    expect(invariant6(broken((s, input, step) => (input.type === 'timer' && input.name === 'collapse' && s.shown
      ? { ...step, state: { ...step.state, shown: null } } : step))))
      .not.toEqual([])
  })

  it('7. while hidden, no timer runs and none starts', () => {
    expect(invariant7(reduce)).toEqual([])
    expect(invariant7((s, input, now) => {
      const step = reduce(s, input, now)
      return input.type === 'connection' && isHidden(step.state)
        ? { ...step, effects: [...step.effects, { kind: 'timer', name: 'offlineGrace', start: true }] } : step
    })).not.toEqual([])
  })

  it('8. focus moves only when the focused work ends or an alert arrives', () => {
    expect(invariant8(reduce)).toEqual([])
    expect(invariant8(broken((_s, input, step) => (input.type === 'start' ? { ...step, state: { ...step.state, focus: input.work.id } } : step))))
      .not.toEqual([])
  })

  it('9. presence never changes the words', () => {
    expect(invariant9(truthOf)).toEqual([])
    expect(invariant9((s) => (s.presence === 'floating' ? { kind: 'idle' } : truthOf(s)))).not.toEqual([])
  })

  it('10. every situation has a reduced-motion still, and no wait changes length under reduced motion', () => {
    expect(invariant10(timingFor)).toEqual([])
    expect(invariant10((name, reduced) => (reduced ? 0 : TIMINGS[name]))).not.toEqual([])
  })

  it('11. once the connection is lost, nothing shows as running past offlineGrace', () => {
    expect(invariant11(reduce)).toEqual([])
    expect(invariant11(broken((s, input, step) => (input.type === 'timer' && input.name === 'offlineGrace' ? { ...step, state: s } : step))))
      .not.toEqual([])
  })

  it('12. the badge and the wash show the truth in the step it changes, in every situation', () => {
    expect(invariant12(reduce)).toEqual([])
    expect(invariant12(broken((s, input, step) => (input.type === 'alert' && !s.shown && !s.listening
      ? { ...step, state: { ...step.state, shown: null, queue: s.queue } } : step))))
      .not.toEqual([])
  })

  it('13. an error outranks done, and done never masks it', () => {
    expect(invariant13(reduce)).toEqual([])
    expect(invariant13(broken((_s, input, step) => (input.type === 'finish' && input.how === 'done'
      ? { ...step, state: { ...step.state, shown: null, queue: [] } } : step))))
      .not.toEqual([])
  })

  it('14. a truth change during a hold is shown at once, and none is dropped', () => {
    expect(invariant14(reduce)).toEqual([])
    expect(invariant14(broken((s, input, step) => ((s.leaving || s.gap) && (input.type === 'listen' || input.type === 'finish') ? { ...step, state: s } : step))))
      .not.toEqual([])
  })

  it('15. every shipping cell of the interruption matrix has a test', () => {
    const cells = JSON.parse(readSource(MATRIX))
    const source = readSource(MATRIX_TEST)
    expect(invariant15(cells, source)).toEqual([])
    const grown = { ...cells, cells: { ...cells.cells, compact: { ...cells.cells.compact, wink: 'compact winks' } } }
    expect(invariant15(grown, source)).toEqual(['compact/wink has no test'])
  })
})

describe('the situations every invariant drives', () => {
  it('are every situation the machine has', () => {
    expect(situations.map(([s]) => s)).toEqual([...SITUATIONS] as Situation[])
  })
})
