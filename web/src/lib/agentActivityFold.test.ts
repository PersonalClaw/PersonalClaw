import { describe, it, expect } from 'vitest'
import {
  IDLE_SESSION_LIMIT, MAX_ENTITIES, foldAgentActivity, foldLoops, foldRunning, foldSessions,
  type AgentActivitySources,
} from './useAgentActivity'
import type { ChatSessionSummary, Loop, PendingApproval, RunningRow } from './api'
import { runState } from '../test/runState'

// ── The AgentActivityFeed fold ───────────────────────
//
// The fold is the whole contract: the running list's rows, and the loops and chats behind them,
// collapse onto SIX world states here and nowhere else, so every world (including an
// app-contributed one) agrees on what "stuck" looks like. What runs is the running list's
// (`agentActivityReadsTheRunningList.test.ts` holds each kind and the blocked state); this file
// holds the rest of the fold.
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
  key: 's1', title: 'A chat', messages: 4, run_state: runState(), ...over,
})

const row = (over: Partial<RunningRow> = {}): RunningRow => ({
  work: 'chat:live', kind: 'chat', session: 'live', phase: 'thinking', line: 'Thinking', since: null,
  name: 'Live chat', agent: '', started_at: null, route: '#/chat/live', blocked: false, waiting: [], ...over,
})

const approval = (id: string): PendingApproval => ({
  id, request_id: id, source: 'chat', tool: 'bash', session: 'live', ts: 1,
  session_title: '', agent: '', risk: '', grant_agent: '', source_label: 'chat',
})

const src = (over: Partial<AgentActivitySources> = {}): AgentActivitySources => ({
  running: { running: [], finished: [] }, sessions: [], loops: [], approvals: [], ...over,
})

const SEEDED = src({
  running: {
    running: [
      row(),
      row({ work: 'loop:run', kind: 'loop', session: '', name: 'Ship the thing', route: '#/loops/run' }),
      row({ work: 'subagent:busy', kind: 'subagent', name: 'grep the tree' }),
    ],
    finished: [row({ work: 'subagent:broke', kind: 'subagent', name: 'fetch it', phase: 'error', line: 'Stopped: no' })],
  },
  loops: [loop({ id: 'run' }), loop({ id: 'dead', status: 'failed', name: 'Broke' })],
  sessions: [session({ key: 'quiet' })],
})

describe('the fold produces a feed at all (vacuity floor)', () => {
  it('seeded fixtures yield a NON-ZERO entity count, one per piece of work', () => {
    const { entities, truncated } = foldAgentActivity(SEEDED)
    // 3 live rows + 1 just finished + 1 loop off the list + 1 quiet chat. If this ever reads 0,
    // every assertion below this line is meaningless — which is the point of asserting it.
    expect(entities.length).toBe(6)
    expect(truncated).toBe(0)
    expect(new Set(entities.map((e) => e.kind))).toEqual(new Set(['loop', 'session', 'subagent']))
  })

  it('ids are kind-prefixed, so a loop and a session sharing a raw id stay distinct', () => {
    const { entities } = foldAgentActivity(src({
      running: { running: [row({ work: 'chat:x', session: 'x' }), row({ work: 'subagent:x', kind: 'subagent' })], finished: [] },
      loops: [loop({ id: 'x', status: 'paused' })],
    }))
    expect(entities.map((e) => e.id).sort()).toEqual(['loop:x', 'session:x', 'subagent:x'])
  })
})

describe('a loop off the list collapses onto the world vocabulary', () => {
  const stateOf = (l: Partial<Loop>) => foldLoops([loop(l)])[0].state

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
    expect(foldLoops([loop({ total_cycles: 3, max_cycles: 10 })])[0].progress).toBeCloseTo(0.3)
    expect(foldLoops([loop({ max_cycles: 0 })])[0]).not.toHaveProperty('progress')
    // Clamped: a run that overshot its budget must not draw a 140% arc.
    expect(foldLoops([loop({ total_cycles: 14, max_cycles: 10 })])[0].progress).toBe(1)
  })

  it('a code loop deep-links to #/code, everything else to #/loops', () => {
    expect(foldLoops([loop({ id: 'c', kind: 'code' })])[0].refs.link).toBe('#/code/c')
    expect(foldLoops([loop({ id: 'g', kind: 'goal' })])[0].refs.link).toBe('#/loops/g')
  })
})

describe('what waits on you for a row outranks its phase', () => {
  const stateOf = (r: Partial<RunningRow>, approvals: PendingApproval[] = []) =>
    foldRunning({ running: [row(r)], finished: [] }, approvals, [])[0].state

  it('a running loop with an approval waiting is waiting_approval, though its phase is still running', () => {
    // The loop still says `running` while its tool sits in the queue. Trusting the phase alone
    // would paint "busy" over "one click away from continuing".
    expect(stateOf({ work: 'loop:l1', kind: 'loop', waiting: ['ap-1'] }, [approval('ap-1')])).toBe('waiting_approval')
  })

  it('a wait that is no approval is an agent\'s question: needs input', () => {
    expect(stateOf({ waiting: ['q-1'] }, [approval('ap-1')])).toBe('needs_input')
  })

  it('a running chat reads as its phase says', () => {
    expect(stateOf({ phase: 'input', line: 'Asks: Which branch?' })).toBe('needs_input')
    expect(stateOf({ phase: 'approval', line: 'Needs your OK: shell' })).toBe('waiting_approval')
    expect(stateOf({ phase: 'tool', line: 'Running shell' })).toBe('working')
  })

  it('an approval for other work blocks nobody', () => {
    expect(stateOf({ work: 'loop:l1', kind: 'loop' }, [approval('ap-1')])).toBe('working')
  })

  it("the gateway's blocked is the row's, whatever phase it says", () => {
    // `running_now` marks a row blocked; the world follows its word, not a phase table of its own.
    expect(stateOf({ phase: 'tool', line: 'Held: incident mode is on', blocked: true })).toBe('blocked')
  })

  it('a phase this build does not know is still live work, never idle', () => {
    expect(stateOf({ phase: 'compacting' as RunningRow['phase'] })).toBe('working')
  })
})

describe('chat sessions: the quiet ones behind the list, bounded', () => {
  it('archived sessions never enter the scene', () => {
    const out = foldSessions([session({ key: 'gone', lifecycle: 'archived' }), session({ key: 'here' })])
    expect(out.map((e) => e.id)).toEqual(['session:here'])
  })

  it("a loop's worker or planner is its loop, never a chat node", () => {
    const out = foldSessions([
      session({ key: 'loop-0a1b2c3d', origin: 'loop', source_id: '0a1b2c3d' }),
      session({ key: 'loop-plan-0a1b2c3d', origin: 'loop', source_id: '0a1b2c3d' }),
      session({ key: 'here' }),
    ])
    expect(out.map((e) => e.id)).toEqual(['session:here'])
  })

  it('quiet ones are capped, keeping the most recent', () => {
    const quiet = Array.from({ length: IDLE_SESSION_LIMIT + 6 }, (_, i) =>
      session({ key: `q${i}`, title: `Quiet ${i}`, last_activity_at: i }))
    const out = foldSessions(quiet)
    expect(out.length).toBe(IDLE_SESSION_LIMIT)
    expect(out.every((e) => e.state === 'idle')).toBe(true)
    // The kept ones are the most recent, not the first N off the wire.
    expect(out.map((e) => e.id)).toContain(`session:q${IDLE_SESSION_LIMIT + 5}`)
    expect(out.map((e) => e.id)).not.toContain('session:q0')
  })

  it('a running chat is the list\'s row, and survives the cap whatever the quiet ones', () => {
    const quiet = Array.from({ length: IDLE_SESSION_LIMIT + 6 }, (_, i) => session({ key: `q${i}`, last_activity_at: i }))
    const { entities } = foldAgentActivity(src({
      running: { running: [row()], finished: [] }, sessions: [...quiet, session({ key: 'live', title: 'Live chat' })],
    }))
    expect(entities.filter((e) => e.id === 'session:live').map((e) => e.state)).toEqual(['working'])
    expect(entities.filter((e) => e.state === 'idle').length).toBe(IDLE_SESSION_LIMIT)
  })
})

describe('helpers', () => {
  it('a just-finished helper that failed is error, one that finished rests, a live one works', () => {
    const out = foldRunning({
      running: [row({ work: 'subagent:c', kind: 'subagent' })],
      finished: [
        row({ work: 'subagent:a', kind: 'subagent', phase: 'error', line: 'Stopped: boom' }),
        row({ work: 'subagent:b', kind: 'subagent', phase: 'done', line: 'Done in 3 seconds' }),
      ],
    }, [], [])
    expect(out.map((e) => [e.id, e.state])).toEqual([['subagent:c', 'working'], ['subagent:a', 'error'], ['subagent:b', 'idle']])
  })

  it("a helper opens where the list says it is answered, its parent's chat, or nowhere", () => {
    const out = foldRunning({
      running: [
        row({ work: 'subagent:a', kind: 'subagent', route: '#/chat/live' }),
        row({ work: 'subagent:b', kind: 'subagent', session: '', route: '' }),
      ],
      finished: [],
    }, [], [])
    expect(out.map((e) => e.refs.link)).toEqual(['#/chat/live', ''])
  })
})

describe('ordering and the scene budget', () => {
  it('actionable states sort ahead of merely-alive ones, and blocked ahead of working', () => {
    const { entities } = foldAgentActivity(src({
      running: {
        running: [
          row({ work: 'subagent:w', kind: 'subagent' }),
          row({ work: 'subagent:b', kind: 'subagent', phase: 'queued', blocked: true }),
        ],
        finished: [],
      },
      loops: [loop({ id: 'i', status: 'paused' }), loop({ id: 'e', status: 'failed' }), loop({ id: 'n', status: 'needs_input' })],
    }))
    expect(entities.map((e) => e.state)).toEqual(['needs_input', 'error', 'blocked', 'working', 'idle'])
  })

  it('over budget the fold caps and REPORTS the remainder', () => {
    const many = Array.from({ length: MAX_ENTITIES + 9 }, (_, i) => row({ work: `subagent:h${i}`, kind: 'subagent' }))
    const { entities, truncated } = foldAgentActivity(src({ running: { running: many, finished: [] } }))
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
    expect(foldLoops([loop({ name: '', task: '' })])[0].title).toBe('Loop')
    expect(foldSessions([session({ title: '' })])[0].title).toBe('Chat')
    expect(foldRunning({ running: [row({ name: '' })], finished: [] }, [], [])[0].title).toBe('Work')
  })
})
