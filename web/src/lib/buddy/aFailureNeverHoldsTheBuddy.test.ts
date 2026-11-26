import { describe, expect, it } from 'vitest'
import type { RunningList, RunningRow, WaitingItem, WaitingList } from '../api'
import { A1, SITUATION_STATES, T, W1, W2, alert, at, run } from '../../test/buddyMachine'
import { createBuddyHost, type BuddyHost, type HostDeps, type HostSnapshot } from './host'
import type { Alert } from './inputs'
import { reduce, viewOf } from './machine'
import { TIMINGS } from './timing'

// ── A failure never holds the buddy (the owner's run, 2026-11-25) ──────────────────────────────
//
// Two helpers of a chat's batch failed. Each became an error alert, and the buddy stayed on
// "failed" while the chat went on, through a new chat, and through that chat's own approval,
// which waited unseen behind the error. On a floating orb a click did nothing, so the error's
// Dismiss could not be reached.
//
// Now: a floating orb's click opens the alert's card (its controls with it); an approval or a
// question takes a shown error's place, the error behind it; and an error leaves once its work
// has gone on, after it has shown for `finishedHold`, never before it has shown at all.

const ERROR: Alert = alert('error:w2', { kind: 'error', workId: 'w2', work: 'Nightly digest', brief: 'every tool call it made was refused', route: '', consequence: 0, raisedAt: T - 2_000 })

describe('a click on a floating orb opens its alert card', () => {
  const floatingWith = (shown: Alert) => at({ presence: 'floating', docked: false, live: [W1, W2], focus: 'w1', queue: [shown], shown: shown.id, beneath: 'floating' })

  it('opens the card on an error, where its Open and Dismiss are, and on an approval', () => {
    for (const shown of [ERROR, A1]) {
      const { state } = run(floatingWith(shown), [{ type: 'click' }])
      expect(state.card).toBe('open')
      expect(state.shown).toBe(shown.id)
      expect(viewOf(state).room).toBe('floating')
    }
  })

  it('answers nothing: the click only opens it', () => {
    const { effects } = run(floatingWith(A1), [{ type: 'click' }])
    expect(effects.filter((e) => e.kind === 'answer')).toEqual([])
  })

  it('the card closes as its alert leaves, Dismiss included', () => {
    const { state } = run(floatingWith(ERROR), [{ type: 'click' }, { type: 'resolved', id: ERROR.id, how: 'withdrawn' }])
    expect(state.shown).toBe(null)
    expect(state.card).toBe(null)
  })

  it('a docked orb is unchanged: its alert already shows expanded, with its controls', () => {
    const before = SITUATION_STATES.alert()
    const { state } = run(before, [{ type: 'click' }])
    expect(state).toEqual(before)
  })
})

describe('an approval or a question takes a shown error\'s place', () => {
  const withError = () => at({ presence: 'expanded', live: [W1, W2], focus: 'w1', queue: [ERROR], shown: ERROR.id, beneath: 'compact' })

  it('shows the approval at once, with the error still waiting behind it', () => {
    const { state } = run(withError(), [{ type: 'alert', alert: A1 }])
    expect(state.shown).toBe('a1')
    expect(state.queue.map((a) => a.id)).toContain(ERROR.id)
    expect(viewOf(state).waiting).toBe(1)
  })

  it('the error comes back once the approval is answered', () => {
    const { state } = run(withError(), [
      { type: 'alert', alert: A1 }, { type: 'answer', id: 'a1', choice: { kind: 'allow' } }, { type: 'timer', name: 'alertGap' },
    ])
    expect(state.shown).toBe(ERROR.id)
  })

  it('an error never takes an approval\'s place, and listening holds whatever shows', () => {
    const shownApproval = SITUATION_STATES.alert()
    expect(run(shownApproval, [{ type: 'alert', alert: ERROR }]).state.shown).toBe('a1')
    const listening = { ...withError(), listening: true }
    expect(reduce(listening, { type: 'alert', alert: A1 }, T).state.shown).toBe(ERROR.id)
  })
})

// ── The host: an error leaves once its work has gone on, and never before it has shown ─────────

const T0 = Date.UTC(2026, 10, 26, 2, 52, 0)
const iso = (ms: number) => new Date(ms).toISOString()

function row(work: string, over: Partial<RunningRow> = {}): RunningRow {
  return {
    work, kind: 'chat', session: 'dashboard:chat-7', phase: 'thinking', line: 'Thinking', since: null,
    name: 'Buddy orb tour', agent: '', started_at: iso(T0), route: '#/chat/chat-7', blocked: false, waiting: [], project_id: '', loop_kind: '', ...over,
  }
}
const helper = (over: Partial<RunningRow> = {}) => row('subagent:20239e5d', { kind: 'subagent', session: 'chat-7', name: 'Read orb checklist', ...over })
const failed = helper({ phase: 'error', line: 'every tool call it made was refused', started_at: null })

function approval(id: string, askedAt: number): WaitingItem {
  return {
    id: `approval:${id}`, kind: 'approval', work: 'chat:dashboard:chat-8', session: 'chat-8',
    brief: { tool: 'subagent_run', tool_purpose: 'A batch of three', tool_input: '{}', confirms: [] },
    consequence: 'changes', risk: 'caution', asked_at: iso(askedAt), age_secs: 0, expires_at: null,
    current: true, interrupt: 'deliver', project_id: '', loop_kind: '',
    answer: { method: 'POST', path: `/api/approvals/${id}/{answer}`, takes: ['approve', 'reject'], body: {} },
    route: '#/chat/chat-8',
  }
}

function world() {
  let now = T0
  let seq = 0
  const timers = new Map<number, { at: number; fire: () => void }>()
  const lists: { running: RunningList; waiting: WaitingList } = {
    running: { running: [helper()], finished: [] },
    waiting: { waiting: [], unreadable: [] } as WaitingList,
  }
  const deps: HostDeps = {
    running: async () => lists.running,
    waiting: async () => lists.waiting,
    listening: async () => ({ on: false }),
    policy: async () => ({ show: true, surfaces: { window: true, tray: true, dock: true, notifications: true } }),
    resolveApproval: async () => ({ ok: true }),
    answerChatQuestion: async () => ({ ok: true }),
    now: () => now,
    setTimer: (fire, ms) => { const id = ++seq; timers.set(id, { at: now + ms, fire }); return id },
    clearTimer: (id) => { timers.delete(id as number) },
  }
  let last: HostSnapshot | null = null
  const host: BuddyHost = createBuddyHost(deps, (s) => { last = s })
  const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
  const advance = async (ms: number) => {
    const until = now + ms
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
      if (!due) break
      timers.delete(due[0])
      now = due[1].at
      due[1].fire()
      await settle()
    }
    now = until
  }
  const ring = async () => { host.ring(); await settle() }
  return { host, lists, advance, ring, now: () => now, machine: () => last!.machine }
}

/** The helper failed: it left the running list, and its ending says so. */
async function helperFails(w: ReturnType<typeof world>, also: Partial<RunningList> = {}) {
  w.lists.running = { running: [], finished: [failed], ...also }
  await w.ring()
}

describe('an error leaves once its work has gone on, after it has shown', () => {
  it('the chat running a turn begun after the failure takes the error away, once it has shown for finishedHold', async () => {
    const w = world()
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    expect(w.machine().shown).toBe('error:subagent:20239e5d')
    await w.advance(1_000)
    w.lists.running = { running: [row('chat:dashboard:chat-7', { started_at: iso(w.now()) })], finished: [failed] }
    await w.ring()
    expect(w.machine().shown, 'it stays for its hold').toBe('error:subagent:20239e5d')
    await w.advance(TIMINGS.finishedHold)
    expect(w.machine().queue.some((a) => a.kind === 'error')).toBe(false)
  })

  it('the chat ending a turn it was running when the helper failed takes the error away too', async () => {
    const w = world()
    w.lists.running = { running: [row('chat:dashboard:chat-7'), helper()], finished: [] }
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w, { running: [row('chat:dashboard:chat-7')] })
    expect(w.machine().shown).toBe('error:subagent:20239e5d')
    w.lists.running = { running: [], finished: [failed, row('chat:dashboard:chat-7', { phase: 'done', started_at: null })] }
    await w.ring()
    await w.advance(TIMINGS.finishedHold)
    expect(w.machine().queue.some((a) => a.kind === 'error')).toBe(false)
  })

  it('an error whose work has not gone on stays, whatever time passes: no timer dismisses it', async () => {
    const w = world()
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    await w.advance(60 * 60_000)
    await w.ring()
    expect(w.machine().shown).toBe('error:subagent:20239e5d')
  })

  it("a chat turn begun before the failure, still running, does not take the error away", async () => {
    const w = world()
    w.lists.running = { running: [row('chat:dashboard:chat-7'), helper()], finished: [] }
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w, { running: [row('chat:dashboard:chat-7')] })
    await w.advance(TIMINGS.finishedHold * 4)
    await w.ring()
    expect(w.machine().shown).toBe('error:subagent:20239e5d')
  })

  it('an error that never showed is not dropped when its chat goes on: it shows once, then leaves', async () => {
    const w = world()
    w.lists.waiting = { waiting: [approval('batch-8', T0)], unreadable: [] } as WaitingList
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    expect(w.machine().shown, 'the approval shows; the error waits behind it').toBe('approval:batch-8')
    await w.advance(1_000)
    w.lists.running = { running: [row('chat:dashboard:chat-7', { started_at: iso(w.now()) })], finished: [failed] }
    await w.ring()
    await w.advance(TIMINGS.finishedHold * 3)
    expect(w.machine().queue.map((a) => a.id), 'never shown, so still waiting').toContain('error:subagent:20239e5d')
    w.lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    await w.ring()
    await w.advance(TIMINGS.alertGap)
    expect(w.machine().shown, 'its turn: it shows').toBe('error:subagent:20239e5d')
    await w.ring()
    expect(w.machine().shown, 'not yet: it has only just shown').toBe('error:subagent:20239e5d')
    await w.advance(TIMINGS.finishedHold)
    expect(w.machine().queue.some((a) => a.kind === 'error')).toBe(false)
  })

  it('a hide right after the error shows starts its hold over: it stays a full finishedHold once on screen again', async () => {
    const w = world()
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    await w.advance(200)
    w.host.send({ type: 'hide' })
    w.lists.running = { running: [row('chat:dashboard:chat-7', { started_at: iso(w.now()) })], finished: [failed] }
    await w.ring()
    await w.advance(TIMINGS.finishedHold * 2)
    w.host.send({ type: 'show' })
    await w.ring()
    expect(w.machine().shown, 'on screen again: its hold starts now').toBe('error:subagent:20239e5d')
    await w.advance(TIMINGS.finishedHold - 100)
    await w.ring()
    expect(w.machine().shown).toBe('error:subagent:20239e5d')
    await w.advance(200)
    expect(w.machine().queue.some((a) => a.kind === 'error')).toBe(false)
  })

  it('an error sent back behind an approval starts its hold over when it shows again', async () => {
    const w = world()
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    await w.advance(200)
    w.lists.waiting = { waiting: [approval('batch-8', w.now())], unreadable: [] } as WaitingList
    w.lists.running = { running: [row('chat:dashboard:chat-7', { started_at: iso(w.now()) })], finished: [failed] }
    await w.ring()
    expect(w.machine().shown, 'the approval took its place').toBe('approval:batch-8')
    await w.advance(TIMINGS.finishedHold * 2)
    w.lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    await w.ring()
    await w.advance(TIMINGS.alertGap)
    expect(w.machine().shown, 'back on screen').toBe('error:subagent:20239e5d')
    await w.ring()
    await w.advance(TIMINGS.finishedHold - 100)
    await w.ring()
    expect(w.machine().shown, 'a full hold, counted from its return').toBe('error:subagent:20239e5d')
    await w.advance(200)
    expect(w.machine().queue.some((a) => a.kind === 'error')).toBe(false)
  })

  it("a new chat's approval shows over a shown error at once", async () => {
    const w = world()
    await w.ring()
    await w.advance(TIMINGS.showDelay)
    await helperFails(w)
    w.lists.waiting = { waiting: [approval('batch-8', w.now())], unreadable: [] } as WaitingList
    await w.ring()
    expect(w.machine().shown).toBe('approval:batch-8')
  })
})

describe('an error leaves without an "answered elsewhere" line', () => {
  it('its Dismiss, or its work going on, ends it with no line of its own', () => {
    const shownError = at({ presence: 'expanded', live: [W1], focus: 'w1', queue: [ERROR], shown: ERROR.id, beneath: 'compact' })
    const { state } = run(shownError, [{ type: 'resolved', id: ERROR.id, how: 'withdrawn' }])
    expect(state.shown).toBe(null)
    expect(state.leaving).toBe(null)
    const approvalElsewhere = run(SITUATION_STATES.alert(), [{ type: 'resolved', id: 'a1', how: 'elsewhere' }]).state
    expect(approvalElsewhere.leaving?.kind).toBe('elsewhere')
  })
})
