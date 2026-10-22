import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunningList, RunningRow, WaitingItem, WaitingList } from '../api'
import { createBuddyHost, type BuddyHost, type HostDeps, type HostSnapshot } from './host'
import type { BuddyInput } from './inputs'
import { TIMINGS } from './timing'

// ── The buddy answers only for the owner's explicit answer, on the item's own route (#3832) ──────
//
// The consent test, written first. The host is driven through every input that is not an answer
// control, in every situation the inputs reach, with the clock run past every timer, and no request
// reaches an answer route. Its positive controls: an explicit Allow sends exactly one request, to
// the approval's own route, and an explicit reply to the question's. And the host's own refusals:
// nothing for an alert that is not the one showing, a stale item, a broad-reach Allow before its
// confirmation, a route of the wrong shape, while listening, or offline.

const T0 = Date.UTC(2026, 9, 22, 9, 0, 0)

function row(work: string, over: Partial<RunningRow> = {}): RunningRow {
  return {
    work, kind: 'chat', session: work.replace(/^chat:/, ''), phase: 'thinking', line: 'Thinking', since: null,
    name: `Work ${work}`, agent: '', started_at: new Date(T0).toISOString(), route: '', blocked: false, waiting: [], ...over,
  }
}

function approval(id: string, over: Partial<WaitingItem> = {}, brief: Record<string, unknown> = {}): WaitingItem {
  return {
    id: `approval:${id}`, kind: 'approval', work: 'chat:s1', session: 's1',
    brief: { tool: 'bash', tool_purpose: 'List the files', tool_input: 'ls', confirms: [], ...brief },
    consequence: 'changes', risk: 'safe', asked_at: new Date(T0).toISOString(), age_secs: 0, expires_at: null,
    current: true, interrupt: 'deliver',
    answer: { method: 'POST', path: `/api/approvals/${id}/{answer}`, takes: ['approve', 'reject'], body: {} },
    route: '#/chat/s1', ...over,
  }
}

function question(id: string, over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    id: `question:${id}`, kind: 'question', work: 'chat:s1', session: 's1',
    brief: { questions: [{ question: 'Which branch?', header: '', multiSelect: false, options: [{ label: 'main', description: '' }] }] },
    consequence: 'asks', risk: '', asked_at: new Date(T0 + 1000).toISOString(), age_secs: 0, expires_at: null,
    current: true, interrupt: 'deliver',
    answer: { method: 'POST', path: `/api/chat/sessions/s1/questions/${id}/answer`, takes: ['answers', 'skip'], body: {} },
    route: '#/chat/s1', ...over,
  }
}

/** A gateway, a clock and a scheduler of the test's own. */
function world(running: RunningList, waiting: WaitingList) {
  let now = T0
  let seq = 0
  const timers = new Map<number, { at: number; fire: () => void }>()
  const lists = { running, waiting, listening: false, show: true, window: true, policyFails: false }
  const reads = { running: 0, waiting: 0, policy: 0 }
  const answers: unknown[][] = []
  const deps: HostDeps = {
    running: async () => { reads.running++; return lists.running },
    waiting: async () => { reads.waiting++; return lists.waiting },
    listening: async () => ({ on: lists.listening }),
    policy: async () => {
      reads.policy++
      if (lists.policyFails) throw new Error('the gateway did not answer')
      return { show: lists.show, surfaces: { window: lists.window && lists.show, tray: lists.show, dock: lists.show, notifications: lists.show } }
    },
    resolveApproval: async (...a) => { answers.push(['approval', ...a]); return { ok: true } },
    answerChatQuestion: async (...a) => { answers.push(['question', ...a]); return { ok: true } },
    now: () => now,
    setTimer: (fire, ms) => { const id = ++seq; timers.set(id, { at: now + ms, fire }); return id },
    clearTimer: (id) => { timers.delete(id as number) },
  }
  let last: HostSnapshot | null = null
  const host: BuddyHost = createBuddyHost(deps, (s) => { last = s })
  const settle = async () => { for (let i = 0; i < 6; i++) await Promise.resolve() }
  /** Runs the clock forward, firing every timer that comes due, in order. */
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
  return { host, deps, lists, reads, answers, advance, settle, state: () => last!.machine, pending: () => timers.size }
}

const LIVE: RunningList = { running: [row('chat:s1')], finished: [] }
const WAITING: WaitingList = { waiting: [approval('ap-1'), question('q1')], unreadable: [] } as WaitingList

/** Every input that is not an answer control, as the shell, the socket and the owner send them. */
const NOT_AN_ANSWER: BuddyInput[] = [
  { type: 'pointer', at: 'zone' }, { type: 'pointer', at: 'orb' }, { type: 'pointer', at: 'away' },
  { type: 'click' }, { type: 'hits' },
  { type: 'key', key: 'Enter', windowFocused: true, onAlert: true },
  { type: 'key', key: 'Escape', windowFocused: true, onAlert: true },
  { type: 'key', key: 'Tab', windowFocused: true, onAlert: true },
  { type: 'key', key: 'ModEnter', windowFocused: false, onAlert: true },
  { type: 'key', key: 'ModEnter', windowFocused: true, onAlert: false },
  { type: 'listen' }, { type: 'unlisten' },
  { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: false }, { type: 'drag', phase: 'start' }, { type: 'drag', phase: 'end', docked: true },
  { type: 'away' }, { type: 'return' },
  { type: 'connection', state: 'lost' }, { type: 'connection', state: 'back' }, { type: 'connection', state: 'restarting' }, { type: 'connection', state: 'back' },
  { type: 'quietHours', on: true }, { type: 'quietHours', on: false },
  { type: 'hide' }, { type: 'show' },
  { type: 'enabled', on: false }, { type: 'enabled', on: true },
]

describe('the buddy sends no answer for anything but an explicit answer', () => {
  beforeEach(() => vi.useRealTimers())
  afterEach(() => vi.restoreAllMocks())

  it('every other input, in every situation they reach, with every timer run out, sends nothing', async () => {
    const w = world(LIVE, WAITING)
    w.host.ring()
    await w.settle()
    expect(w.state().shown, 'the approval shows').toBe('approval:ap-1')
    const situations = new Set<string>()
    for (let round = 0; round < 3; round++) {
      for (const input of NOT_AN_ANSWER) {
        // Buddy status arrives as the policy says it, read on the ring below.
        if (input.type === 'enabled') w.lists.show = input.on
        w.host.send(input)
        w.host.hold(round % 2 === 0)
        w.host.ring()
        await w.settle()
        situations.add(`${w.state().presence}/${w.state().connection}/${w.state().shown ?? '-'}`)
        await w.advance(TIMINGS.hoverExpand)
      }
      await w.advance(TIMINGS.sleep + TIMINGS.collapse + TIMINGS.collapseCountdown)
    }
    // Docked and floating, hidden, a connection lost and a restart, with the alert showing.
    const reached = [...situations].join(' | ')
    for (const where of ['expanded/up/approval', 'floating/', 'hidden/', '/lost/', '/restarting/']) expect(reached, where).toContain(where)
    expect(w.answers).toEqual([])
  })

  it('an explicit Allow sends exactly one request, to the approval its own route names', async () => {
    const w = world(LIVE, WAITING)
    w.host.ring()
    await w.settle()
    expect(await w.host.answer('approval:ap-1', { kind: 'allow' }, { confirmed: true })).toBe(true)
    expect(w.answers).toEqual([['approval', 'ap-1', 'approve']])
  })

  it('an explicit Deny sends the reject on the same route', async () => {
    const w = world(LIVE, WAITING)
    w.host.ring()
    await w.settle()
    expect(await w.host.answer('approval:ap-1', { kind: 'deny' })).toBe(true)
    expect(w.answers).toEqual([['approval', 'ap-1', 'reject']])
  })

  it("a reply to the question goes to the chat and the question its route names, as the chat's card sends it", async () => {
    const w = world(LIVE, { waiting: [question('q1')], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    const reply = { answers: [{ selected: [0], other: '' }] }
    expect(await w.host.answer('question:q1', { kind: 'send', text: '' }, { reply })).toBe(true)
    expect(w.answers).toEqual([['question', 's1', 'q1', reply]])
  })

  it('nothing is sent for an alert that is not the one showing', async () => {
    const w = world(LIVE, WAITING)
    w.host.ring()
    await w.settle()
    expect(await w.host.answer('question:q1', { kind: 'send', text: '' }, { reply: { skip: true } })).toBe(false)
    expect(w.answers).toEqual([])
  })

  it('a broad-reach Allow sends nothing before its confirmation, and one request after it', async () => {
    const w = world(LIVE, { waiting: [approval('ap-2', {}, { confirms: ['deletes'] })], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(w.state().queue[0].broadReach).toBe(true)
    expect(await w.host.answer('approval:ap-2', { kind: 'allow' })).toBe(false)
    expect(w.state().confirming).toBe('approval:ap-2')
    expect(w.answers).toEqual([])
    expect(await w.host.answer('approval:ap-2', { kind: 'allow' }, { confirmed: true })).toBe(true)
    expect(w.answers).toEqual([['approval', 'ap-2', 'approve']])
  })

  it('a row that names no confirmation fails closed: its Allow asks first', async () => {
    const w = world(LIVE, { waiting: [approval('ap-3', { risk: 'caution' }, { confirms: undefined })], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(await w.host.answer('approval:ap-3', { kind: 'allow' })).toBe(false)
    expect(w.answers).toEqual([])
  })

  it('a stale item sends nothing and leaves as expired', async () => {
    const w = world(LIVE, { waiting: [approval('ap-4', { expires_at: new Date(T0 + 30_000).toISOString() })], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    await w.advance(31_000)
    expect(await w.host.answer('approval:ap-4', { kind: 'allow' }, { confirmed: true })).toBe(false)
    expect(w.answers).toEqual([])
    expect(w.state().leaving).toEqual({ kind: 'expired', work: 'Work chat:s1' })
  })

  it('an item whose route is not its kind\'s shape never becomes an alert', async () => {
    const odd = approval('ap-5', { answer: { method: 'POST', path: '/api/somewhere/else', takes: [], body: {} } })
    const w = world(LIVE, { waiting: [odd], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(w.state().queue).toEqual([])
    expect(await w.host.answer('approval:ap-5', { kind: 'allow' }, { confirmed: true })).toBe(false)
    expect(w.answers).toEqual([])
  })

  it('listening holds the alert and sends nothing; so does offline', async () => {
    const w = world(LIVE, WAITING)
    w.host.ring()
    await w.settle()
    w.host.send({ type: 'listen' })
    expect(await w.host.answer('approval:ap-1', { kind: 'allow' }, { confirmed: true })).toBe(false)
    w.host.send({ type: 'unlisten' })
    w.host.send({ type: 'connection', state: 'lost' })
    await w.advance(TIMINGS.offlineGrace)
    expect(w.state().connection).toBe('offline')
    expect(await w.host.answer('approval:ap-1', { kind: 'allow' }, { confirmed: true })).toBe(false)
    expect(w.answers).toEqual([])
  })
})

describe('the doorbell, levels and timers', () => {
  it('a burst of rings is one read of each list, and of the policy', async () => {
    const w = world(LIVE, WAITING)
    for (let i = 0; i < 10; i++) w.host.ring()
    await w.settle()
    expect(w.reads).toEqual({ running: 1, waiting: 1, policy: 1 })
  })

  it("Buddy status, as the policy says it: off, nothing shows and the alert waits; on again, it shows", async () => {
    const w = world(LIVE, WAITING)
    w.lists.show = false
    w.host.ring()
    await w.settle()
    expect(w.state().enabled).toBe(false)
    expect(w.state().presence).toBe('hidden')
    expect(w.state().shown).toBeNull()
    expect(w.state().queue.map((a) => a.id), 'the alert waits').toContain('approval:ap-1')
    w.lists.show = true
    w.host.ring()
    await w.settle()
    expect(w.state().enabled).toBe(true)
    expect(w.state().shown).toBe('approval:ap-1')
    expect(w.answers).toEqual([])
  })

  it("the window's own surface off is off too, whatever Buddy status says", async () => {
    const w = world(LIVE, WAITING)
    w.lists.window = false
    w.host.ring()
    await w.settle()
    expect(w.state().enabled).toBe(false)
    expect(w.state().presence).toBe('hidden')
  })

  it('a policy read that fails keeps what the last one said, and the lists are still read', async () => {
    const w = world(LIVE, WAITING)
    w.lists.show = false
    w.host.ring()
    await w.settle()
    w.lists.show = true
    w.lists.policyFails = true
    w.host.ring()
    await w.settle()
    expect(w.state().enabled, 'still off, as the last answer said').toBe(false)
    expect(w.reads.waiting).toBe(2)
  })

  it('listening is a level too: read on the ring, it holds the alert, and its end lets it go', async () => {
    const w = world(LIVE, WAITING)
    w.lists.listening = true
    w.host.ring()
    await w.settle()
    expect(w.state().listening).toBe(true)
    expect(await w.host.answer('approval:ap-1', { kind: 'allow' }, { confirmed: true })).toBe(false)
    w.lists.listening = false
    w.host.ring()
    await w.settle()
    expect(w.state().listening).toBe(false)
    expect(w.state().shown).toBe('approval:ap-1')
    expect(w.answers).toEqual([])
  })

  it('an item the list no longer has leaves as answered elsewhere', async () => {
    const w = world(LIVE, { waiting: [approval('ap-1')], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    w.lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    w.host.ring()
    await w.settle()
    expect(w.state().shown).toBeNull()
    expect(w.state().leaving).toEqual({ kind: 'elsewhere', work: 'Work chat:s1' })
  })

  it('alerts show one at a time, in the list\'s order', async () => {
    const w = world(LIVE, { waiting: [question('q1'), approval('ap-1', { consequence: 'destructive' })], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(w.state().queue.map((a) => a.id)).toEqual(['approval:ap-1', 'question:q1'])
    expect(w.state().shown).toBe('approval:ap-1')
  })

  it('work that ends in an error raises its alert; work that left otherwise ends quietly', async () => {
    const w = world({ running: [row('chat:s1'), row('chat:s2')], finished: [] }, { waiting: [], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    w.lists.running = { running: [], finished: [row('chat:s1', { phase: 'error', line: 'The model refused' }), row('chat:s2', { phase: 'done' })] }
    w.host.ring()
    await w.settle()
    expect(w.state().live).toEqual([])
    expect(w.state().queue.map((a) => [a.kind, a.brief])).toEqual([['error', 'The model refused']])
  })

  it('an item the gateway says may not interrupt you arrives as a quiet peek', async () => {
    const w = world(LIVE, { waiting: [approval('ap-1', { interrupt: 'quiet' })], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(w.state().quietHours).toBe(true)
    expect(w.state().presence).toBe('peek')
  })

  it('over a full-screen app window an alert arrives as a quiet peek; leaving it lifts the quiet', async () => {
    const w = world(LIVE, { waiting: [], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    w.host.fullScreen(true)
    expect(w.state().quietHours).toBe(true)
    w.lists.waiting = { waiting: [approval('ap-1')], unreadable: [] } as WaitingList
    w.host.ring()
    await w.settle()
    expect(w.state().presence).toBe('peek')
    w.host.fullScreen(false)
    expect(w.state().quietHours, 'a deliver item, no full screen: not quiet').toBe(false)
    expect(w.answers).toEqual([])
  })

  it('hidden, the host holds no timer', async () => {
    const w = world({ running: [], finished: [] }, { waiting: [], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    expect(w.state().presence).toBe('hidden')
    expect(w.pending()).toBe(0)
    w.host.send({ type: 'hide' })
    expect(w.pending()).toBe(0)
  })

  it('holding the card stops its fold, and letting go starts the fold over', async () => {
    const w = world(LIVE, { waiting: [], unreadable: [] } as WaitingList)
    w.host.ring()
    await w.settle()
    await w.advance(TIMINGS.showDelay)
    w.host.send({ type: 'click' })
    expect(w.state().presence).toBe('expanded')
    w.host.hold(true)
    await w.advance(TIMINGS.collapse * 2)
    expect(w.state().presence, 'held, it stays open').toBe('expanded')
    expect(w.state().countdown).toBe(false)
    w.host.hold(false)
    await w.advance(TIMINGS.collapse)
    expect(w.state().countdown, 'let go, the fold runs again from its start').toBe(true)
  })
})
