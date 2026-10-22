import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { A1, A2, ON, W1, W2, W3, alert, work } from '../../test/buddyMachine'
import type { BuddyInput, Effect } from './inputs'
import { lineOf } from './lines'
import { reduce, viewOf, type BuddyState } from './machine'
import { TIMER_NAMES, TIMINGS, timingFor, type TimerName } from './timing'

// ── The machine on a clock (#3890): every timing token, the focus rule, the queue, a day replayed ─
//
// A host runs the machine the way the buddy window will: it feeds inputs in at `Date.now()`, starts
// and cancels each named timer the machine asks for at its token's length (`timing.ts`), and feeds
// its expiry back in. Vitest's fake clock drives it, so each token is held to its value.

function host(from: BuddyState = ON) {
  let state = from
  const timers = new Map<TimerName, ReturnType<typeof setTimeout>>()
  const effects: Effect[] = []
  const send = (input: BuddyInput) => {
    const step = reduce(state, input, Date.now())
    state = step.state
    for (const e of step.effects) {
      effects.push(e)
      if (e.kind !== 'timer') continue
      clearTimeout(timers.get(e.name))
      timers.delete(e.name)
      if (e.start) timers.set(e.name, setTimeout(() => { timers.delete(e.name); send({ type: 'timer', name: e.name }) }, timingFor(e.name, false)))
    }
  }
  return {
    send,
    get state() { return state },
    view: () => viewOf(state),
    line: () => { const t = viewOf(state).truth; return t ? lineOf(t) : null },
    effects,
    timers,
  }
}

const startedNow = (w: typeof W1) => ({ ...w, startedAt: Date.now() })

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-21T09:00:00Z')) })
afterEach(() => { vi.useRealTimers() })

describe('every timing token holds its value', () => {
  it('showDelay: new work shows only after it, and work that ends inside it never shows', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay - 1)
    expect(h.view().shows, 'shown before showDelay').toBe(false)
    vi.advanceTimersByTime(1)
    expect(h.view().shows).toBe(true)

    const fast = host()
    fast.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay / 2)
    fast.send({ type: 'finish', id: 'w1', how: 'done' })
    vi.advanceTimersByTime(TIMINGS.finishedHold * 2)
    expect(fast.view().shows, 'fast work flashed').toBe(false)
    expect(fast.timers.size, 'a timer is left running for work that never showed').toBe(0)
  })

  it('minHold: work that has shown stays drawn at least minHold, held by its done beat', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'finish', id: 'w1', how: 'done' })
    vi.advanceTimersByTime(TIMINGS.minHold)
    expect(h.view().shows).toBe(true)
    expect(TIMINGS.finishedHold).toBeGreaterThanOrEqual(TIMINGS.minHold)
  })

  it('finishedHold: the done line holds, then folds away', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay + 2_000)
    h.send({ type: 'finish', id: 'w1', how: 'done' })
    expect(h.line()).toBe('Release notes is done in 2s')
    vi.advanceTimersByTime(TIMINGS.finishedHold - 1)
    expect(h.view().truth?.kind).toBe('done')
    vi.advanceTimersByTime(1)
    expect(h.view().shows, 'nothing live: it folds away').toBe(false)
  })

  it('offlineGrace: a lost connection shows as offline only after it, and a short reconnect never flashes', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'connection', state: 'lost' })
    vi.advanceTimersByTime(TIMINGS.offlineGrace - 1)
    expect(h.view().truth?.kind, 'offline before offlineGrace').toBe('phase')
    h.send({ type: 'connection', state: 'back' })
    vi.advanceTimersByTime(TIMINGS.offlineGrace)
    expect(h.view().truth?.kind, 'a short reconnect flashed').toBe('phase')
    h.send({ type: 'connection', state: 'lost' })
    vi.advanceTimersByTime(TIMINGS.offlineGrace)
    expect(h.view().truth?.kind).toBe('offline')
  })

  it('hoverExpand, collapse and collapseCountdown: resting opens the card, which folds after both', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'pointer', at: 'orb' })
    vi.advanceTimersByTime(TIMINGS.hoverExpand - 1)
    expect(h.view().room).toBe('compact')
    vi.advanceTimersByTime(1)
    expect(h.view().room).toBe('expanded')
    h.send({ type: 'pointer', at: 'away' })
    vi.advanceTimersByTime(TIMINGS.collapse)
    expect(h.view().countdown, 'the countdown line shows').toBe(true)
    vi.advanceTimersByTime(TIMINGS.collapseCountdown - 1)
    expect(h.view().room).toBe('expanded')
    vi.advanceTimersByTime(1)
    expect(h.view().room).toBe('compact')
  })

  it('peekLeave: the peek folds this long after the pointer leaves the top-edge zone', () => {
    const h = host()
    h.send({ type: 'pointer', at: 'zone' })
    expect(h.view().room).toBe('peek')
    h.send({ type: 'pointer', at: 'away' })
    vi.advanceTimersByTime(TIMINGS.peekLeave - 1)
    expect(h.view().room).toBe('peek')
    vi.advanceTimersByTime(1)
    expect(h.view().shows).toBe(false)
  })

  it('alertGap: after an answer the next alert enters this long after', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    h.send({ type: 'start', work: startedNow(W2) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'alert', alert: A1 })
    h.send({ type: 'alert', alert: A2 })
    h.send({ type: 'answer', id: 'a1', choice: { kind: 'allow' } })
    vi.advanceTimersByTime(TIMINGS.alertGap - 1)
    expect(h.state.shown, 'the next alert entered inside alertGap').toBe(null)
    vi.advanceTimersByTime(1)
    expect(h.state.shown).toBe('a2')
  })

  it('beat: an alert answered elsewhere leaves with its line for one beat', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'alert', alert: A1 })
    h.send({ type: 'resolved', id: 'a1', how: 'elsewhere' })
    expect(h.line()).toBe('Answered in PersonalClaw')
    vi.advanceTimersByTime(TIMINGS.beat)
    expect(h.line()).toBe('Release notes: thinking')
  })

  it('sleep: a floating orb with nothing live sleeps after it', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'drag', phase: 'start' })
    h.send({ type: 'drag', phase: 'end', docked: false })
    h.send({ type: 'finish', id: 'w1', how: 'done' })
    vi.advanceTimersByTime(TIMINGS.sleep - 1)
    expect(h.view().situation).toBe('floating')
    vi.advanceTimersByTime(1)
    expect(h.view().situation).toBe('sleeping')
  })

  it('names a test for every timer the machine runs', () => {
    const tested = ['showDelay', 'minHold', 'finishedHold', 'offlineGrace', 'hoverExpand', 'collapse', 'collapseCountdown', 'peekLeave', 'alertGap', 'beat', 'sleep']
    expect([...TIMER_NAMES].sort()).toEqual(tested.sort())
  })
})

describe('the focus rule, the queue and the return summary', () => {
  it('focus stays on the focused work while it runs, and moves to the earliest live work when it ends', () => {
    const h = host()
    h.send({ type: 'start', work: work('w1', 'Release notes', { startedAt: Date.now() }) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'start', work: work('w2', 'Nightly digest', { startedAt: Date.now() }) })
    h.send({ type: 'start', work: work('w3', 'Tidy downloads', { startedAt: Date.now() + 1 }) })
    h.send({ type: 'phase', id: 'w3', phase: 'tool', line: 'Moving files' })
    expect(h.state.focus).toBe('w1')
    expect(h.view().inSight).toBe(2)
    h.send({ type: 'finish', id: 'w1', how: 'done' })
    vi.advanceTimersByTime(TIMINGS.finishedHold)
    expect(h.state.focus).toBe('w2')
  })

  it('alerts show one at a time in the waiting list order, consequence then age, with the others counted', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    const older = alert('old', { consequence: 1, raisedAt: Date.now() - 9_000 })
    const graver = alert('grave', { consequence: 3, raisedAt: Date.now() })
    const newer = alert('new', { consequence: 1, raisedAt: Date.now() })
    h.send({ type: 'alert', alert: newer })
    h.send({ type: 'alert', alert: older })
    h.send({ type: 'alert', alert: graver })
    expect(h.state.shown, 'the shown alert stays').toBe('new')
    expect(h.view().waiting).toBe(2)
    const order: string[] = []
    for (let i = 0; i < 3; i++) {
      order.push(h.state.shown!)
      h.send({ type: 'answer', id: h.state.shown!, choice: { kind: 'deny' } })
      vi.advanceTimersByTime(TIMINGS.alertGap)
    }
    expect(order).toEqual(['new', 'grave', 'old'])
  })

  it('the return summary says what happened while the owner was away, for finishedHold', () => {
    const h = host()
    h.send({ type: 'start', work: startedNow(W1) })
    vi.advanceTimersByTime(TIMINGS.showDelay)
    h.send({ type: 'away' })
    h.send({ type: 'finish', id: 'w1', how: 'done' })
    h.send({ type: 'start', work: startedNow(W3) })
    expect(h.view().shows).toBe(false)
    h.send({ type: 'return' })
    expect(h.line()).toBe('While you were away: 1 done, 0 need you')
    vi.advanceTimersByTime(TIMINGS.finishedHold)
    expect(h.line()).toBe('Tidy downloads: thinking')
  })
})

describe('a recorded day replays', () => {
  /** A scripted day: each input at its offset from 09:00, in ms. */
  const DAY: [number, BuddyInput][] = [
    [0, { type: 'enabled', on: true }],
    [1_000, { type: 'start', work: work('chat', 'Release notes', { startedAt: 1_000 }) }],
    [2_000, { type: 'phase', id: 'chat', phase: 'tool', line: 'Reading the changelog' }],
    [3_000, { type: 'start', work: work('loop', 'Nightly digest', { startedAt: 3_000 }) }],
    [9_000, { type: 'alert', alert: alert('push', { workId: 'chat', work: 'Release notes', raisedAt: 9_000 }) }],
    [9_500, { type: 'alert', alert: alert('mail', { workId: 'loop', work: 'Nightly digest', brief: 'send_email', consequence: 1, raisedAt: 9_500 }) }],
    [15_000, { type: 'answer', id: 'push', choice: { kind: 'allow' } }],
    [20_000, { type: 'listen' }],
    [24_000, { type: 'unlisten' }],
    [30_000, { type: 'resolved', id: 'mail', how: 'elsewhere' }],
    [40_000, { type: 'connection', state: 'lost' }],
    [52_000, { type: 'connection', state: 'back' }],
    [60_000, { type: 'finish', id: 'loop', how: 'error', reason: 'the feed was down' }],
    [70_000, { type: 'finish', id: 'chat', how: 'done' }],
    [80_000, { type: 'away' }],
    [400_000, { type: 'return' }],
  ]

  function replay() {
    vi.setSystemTime(new Date('2026-10-21T09:00:00Z'))
    const h = host({ ...ON, enabled: false })
    const seen: string[] = []
    let at = 0
    for (const [offset, input] of DAY) {
      vi.advanceTimersByTime(offset - at)
      at = offset
      h.send(input)
      seen.push(`${offset} ${h.view().situation} ${h.line() ?? '-'}`)
    }
    vi.advanceTimersByTime(TIMINGS.finishedHold)
    seen.push(`end ${h.view().situation} ${h.line() ?? '-'}`)
    return { seen, answers: h.effects.filter((e) => e.kind === 'answer') }
  }

  it('through the same reducer, to the same views and the same one answer', () => {
    const first = replay()
    const second = replay()
    expect(second).toEqual(first)
    expect(first.answers).toEqual([{ kind: 'answer', id: 'push', route: '/api/approvals/push/answer', choice: { kind: 'allow' } }])
    expect(first.seen).toContain('9000 alert Release notes needs your OK: git push')
    expect(first.seen).toContain('20000 listening Listening')
    expect(first.seen.find((l) => l.startsWith('30000')), 'the second alert leaves, and the card the alerts opened folds').toBe('30000 compact Answered in PersonalClaw')
    expect(first.seen.find((l) => l.startsWith('52000'))).toBe('52000 compact Connected again')
    expect(first.seen.find((l) => l.startsWith('60000'))).toBe('60000 alert Nightly digest stopped: the feed was down')
  })
})
