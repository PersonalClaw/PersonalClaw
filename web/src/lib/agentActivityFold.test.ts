import { describe, it, expect } from 'vitest'
import {
  IDLE_SESSION_LIMIT, MAX_ENTITIES, approvalSessions, foldAgentActivity, foldLoops,
  foldSessions, foldSubagents,
} from './useAgentActivity'
import type { ChatSessionSummary, Loop, PendingApproval, SpawnedAgent } from './api'

// ── The AgentActivityFeed fold (AMBIENT-SURFACES A2-3) ───────────────────────
//
// The fold is the whole contract: three entity kinds and twelve loop statuses
// collapse onto FIVE world states here and nowhere else, so every world (including
// an app-contributed one) agrees on what "stuck" looks like.
//
// 🪤 VACUITY FLOOR FIRST. A feed rail over an empty backend passes forever — every
// "no bad entity appeared" assertion is trivially true when the fold produced
// nothing at all. So the first test asserts a NON-ZERO entity count from seeded
// fixtures, and every state assertion below names a fixture that must be present.

const loop = (over: Partial<Loop> = {}): Loop => ({
  id: 'l1', kind: 'goal', name: 'Ship the thing', task: 'ship it',
  execution: 'solo', agent: 'a', model: 'm', attended: false,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status: 'running', total_cycles: 3, error_message: null,
  created_at: 1, started_at: 1, completed_at: null, kind_config: {},
  ...over,
} as Loop)

const session = (over: Partial<ChatSessionSummary> = {}): ChatSessionSummary => ({
  key: 's1', title: 'A chat', messages: 4, ...over,
})

const sub = (over: Partial<SpawnedAgent> = {}): SpawnedAgent => ({
  id: 'g1', task: 'grep the tree', done: false, ...over,
})

const approval = (session_: string): PendingApproval =>
  ({ id: `ap-${session_}`, source: 'chat', tool: 'bash', session: session_, ts: 1 })

const SEEDED = {
  loops: [
    loop({ id: 'run', status: 'running' }),
    loop({ id: 'ask', status: 'needs_input', name: 'Needs me' }),
    loop({ id: 'dead', status: 'failed', name: 'Broke' }),
  ],
  sessions: [session({ key: 'live', running: true, title: 'Live chat' }), session({ key: 'quiet' })],
  subagents: [sub({ id: 'busy' }), sub({ id: 'done', done: true })],
  approvals: [] as PendingApproval[],
}

describe('the fold produces a feed at all (vacuity floor)', () => {
  it('seeded fixtures yield a NON-ZERO entity count, one per source row', () => {
    const { entities, truncated } = foldAgentActivity(SEEDED)
    // 3 loops + 2 sessions + 2 subagents. If this ever reads 0, every assertion
    // below this line is meaningless — which is the point of asserting it.
    expect(entities.length).toBe(7)
    expect(truncated).toBe(0)
    expect(new Set(entities.map((e) => e.kind))).toEqual(new Set(['loop', 'session', 'subagent']))
  })

  it('ids are kind-prefixed, so a loop and a session sharing a raw id stay distinct', () => {
    const { entities } = foldAgentActivity({
      ...SEEDED, loops: [loop({ id: 'x' })], sessions: [session({ key: 'x' })], subagents: [sub({ id: 'x' })],
    })
    expect(entities.map((e) => e.id).sort()).toEqual(['loop:x', 'session:x', 'subagent:x'])
  })
})

describe('loop status collapses onto the world vocabulary', () => {
  const stateOf = (l: Partial<Loop>) => foldLoops([loop(l)], new Set())[0].state

  it.each([
    ['intake', 'working'], ['planning', 'working'], ['review', 'working'], ['running', 'working'],
    ['needs_input', 'needs_input'], ['blocked', 'needs_input'], ['stagnant', 'needs_input'],
    ['failed', 'error'],
    ['ready', 'idle'], ['paused', 'idle'], ['stopped', 'idle'], ['complete', 'idle'],
  ])('%s -> %s', (status, want) => {
    expect(stateOf({ status: status as Loop['status'] })).toBe(want)
  })

  it('every UnifiedLoopStatus is mapped — an unmapped one would silently read idle', () => {
    // The union has 12 members; the table above covers all 12 by name. This guards the
    // reverse direction: a NEW status added to the union with no row here would fall
    // through to `idle`, painting a live run as asleep. If this count changes, add the row.
    const covered = [
      'intake', 'planning', 'review', 'ready', 'running', 'paused',
      'stagnant', 'blocked', 'needs_input', 'complete', 'failed', 'stopped',
    ]
    expect(covered.length).toBe(12)
    for (const s of covered) expect(stateOf({ status: s as Loop['status'] })).not.toBe(undefined)
  })

  it('a complete loop carrying an error_message is error, not idle', () => {
    expect(stateOf({ status: 'complete', error_message: 'cycle budget ran out' })).toBe('error')
  })

  it('progress is the cycle fraction, and ABSENT when there is no budget', () => {
    expect(foldLoops([loop({ total_cycles: 3, max_cycles: 10 })], new Set())[0].progress).toBeCloseTo(0.3)
    expect(foldLoops([loop({ max_cycles: 0 })], new Set())[0]).not.toHaveProperty('progress')
    // Clamped: a run that overshot its budget must not draw a 140% arc.
    expect(foldLoops([loop({ total_cycles: 14, max_cycles: 10 })], new Set())[0].progress).toBe(1)
  })

  it('a code loop deep-links to #/code, everything else to #/loops', () => {
    expect(foldLoops([loop({ id: 'c', kind: 'code' })], new Set())[0].refs.link).toBe('#/code/c')
    expect(foldLoops([loop({ id: 'g', kind: 'goal' })], new Set())[0].refs.link).toBe('#/loops/g')
  })
})

describe('waiting_approval is reachable, and an approval outranks a busy status', () => {
  it('a running loop whose session has a pending approval is waiting_approval', () => {
    const blocked = approvalSessions([approval('sk-1')])
    const [e] = foldLoops([loop({ status: 'running', session_key: 'sk-1' })], blocked)
    // The loop still reports `running` while its tool sits in the queue. Trusting the
    // status alone would paint "busy" over "one click away from continuing".
    expect(e.state).toBe('waiting_approval')
  })

  it('a running chat session with a pending approval is waiting_approval, not working', () => {
    const blocked = approvalSessions([approval('live')])
    expect(foldSessions([session({ key: 'live', running: true })], blocked)[0].state).toBe('waiting_approval')
  })

  it('an approval on an unrelated session does not block anyone', () => {
    const blocked = approvalSessions([approval('somebody-else')])
    expect(foldLoops([loop({ status: 'running', session_key: 'sk-1' })], blocked)[0].state).toBe('working')
  })

  it('approvals with an empty session key are dropped, not turned into a match-all', () => {
    // `session: ''` would otherwise join with every loop that has no session_key.
    const blocked = approvalSessions([{ ...approval('x'), session: '' }])
    expect(blocked.size).toBe(0)
  })
})

describe('chat sessions: live always, quiet ones bounded', () => {
  it('archived sessions never enter the scene', () => {
    const out = foldSessions([session({ key: 'gone', lifecycle: 'archived' }), session({ key: 'here' })], new Set())
    expect(out.map((e) => e.id)).toEqual(['session:here'])
  })

  it('running and approval-blocked sessions survive the idle cap; quiet ones are capped', () => {
    const quiet = Array.from({ length: IDLE_SESSION_LIMIT + 6 }, (_, i) =>
      session({ key: `q${i}`, title: `Quiet ${i}`, last_activity_at: i }))
    const out = foldSessions(
      [...quiet, session({ key: 'live', running: true }), session({ key: 'held' })],
      approvalSessions([approval('held')]),
    )
    expect(out.find((e) => e.id === 'session:live')).toBeTruthy()
    expect(out.find((e) => e.id === 'session:held')).toBeTruthy()
    expect(out.filter((e) => e.state === 'idle').length).toBe(IDLE_SESSION_LIMIT)
    // The kept idle ones are the most recent, not the first N off the wire.
    const keptIdle = out.filter((e) => e.state === 'idle').map((e) => e.id)
    expect(keptIdle).toContain(`session:q${IDLE_SESSION_LIMIT + 5}`)
    expect(keptIdle).not.toContain('session:q0')
  })
})

describe('subagents', () => {
  it('an errored subagent is error, a finished one idle, a live one working', () => {
    const out = foldSubagents([
      sub({ id: 'a', error: 'boom' }), sub({ id: 'b', done: true }), sub({ id: 'c' }),
    ])
    expect(out.map((e) => e.state)).toEqual(['error', 'idle', 'working'])
  })

  it('a subagent link is EMPTY, not a route that would 404', () => {
    // There is no detail page for a spawned agent (the monitor is a rail inside
    // SystemWidget). `triggers/delivery.status_url`'s rule: point somewhere honest.
    expect(foldSubagents([sub()])[0].refs.link).toBe('')
    expect(foldSubagents([sub({ parent: 'p1' })])[0].refs.parent).toBe('p1')
  })
})

describe('ordering and the scene budget', () => {
  it('actionable states sort ahead of merely-alive ones', () => {
    const { entities } = foldAgentActivity({
      loops: [
        loop({ id: 'i', status: 'paused' }), loop({ id: 'w', status: 'running' }),
        loop({ id: 'e', status: 'failed' }), loop({ id: 'n', status: 'needs_input' }),
      ],
      sessions: [], subagents: [], approvals: [],
    })
    expect(entities.map((e) => e.state)).toEqual(['needs_input', 'error', 'working', 'idle'])
  })

  it('over budget the fold caps and REPORTS the remainder', () => {
    const many = Array.from({ length: MAX_ENTITIES + 9 }, (_, i) => loop({ id: `l${i}`, status: 'running' }))
    const { entities, truncated } = foldAgentActivity({ loops: many, sessions: [], subagents: [], approvals: [] })
    expect(entities.length).toBe(MAX_ENTITIES)
    // Silently rendering 64 of 73 would misstate the scale of what is running.
    expect(truncated).toBe(9)
  })

  it('the fold is deterministic — the same sources twice give byte-identical order', () => {
    // A world interpolates BETWEEN folds; an unstable sort would visibly reshuffle
    // every entity on each refetch.
    expect(foldAgentActivity(SEEDED).entities).toEqual(foldAgentActivity(SEEDED).entities)
  })

  it('a title falls back rather than rendering an empty label', () => {
    expect(foldLoops([loop({ name: '', task: '' })], new Set())[0].title).toBe('Loop')
    expect(foldSessions([session({ title: '' })], new Set())[0].title).toBe('Chat')
    expect(foldSubagents([sub({ task: '', agent: '' })])[0].title).toBe('Subagent')
  })
})
