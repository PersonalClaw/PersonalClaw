import { describe, expect, it } from 'vitest'
import { toLanes, type ApprovalInput, type AttentionInput, type Lane } from './attentionLanes'
import { toThreads, type Thread, type ThreadChat, type ThreadLoop, type ThreadSources } from './useThreads'
import { runState, runningState } from '../test/runState'

// ── A thread's pip is the lane Home gives its work, never a second derivation (#3814) ──────────
//
// "Pips track the same signals as Home's lanes": waiting on you is Needs approval or Your turn,
// running is Working. Each case below asks `toLanes` for the lane of the same snapshot and checks
// the thread's pip against it, so the two can't drift apart. Failed is the one pip no lane holds:
// a loop whose status is `failed`, shown only while nothing of it waits or runs.

function chat(over: Partial<ThreadChat> = {}): ThreadChat {
  return { key: 'chat-1', title: 'Plan the garden', run_state: runState(), origin: 'manual', project_id: 'p-garden', ...over }
}

function loop(over: Partial<ThreadLoop> = {}): ThreadLoop {
  return {
    id: 'a1b2c3d4', kind: 'goal', name: 'Find a seed supplier', task: 'find a seed supplier', status: 'running',
    total_cycles: 1, max_cycles: 10, started_at: 1_000, session_key: 'loop-a1b2c3d4', project_id: 'p-garden',
    ...over,
  }
}

function approval(over: Partial<ApprovalInput> = {}): ApprovalInput {
  return { id: 'req-1', request_id: 'req-1', tool: 'Bash', session: 'chat-1', ts: 1_000, risk: '', grant_agent: '', source_label: 'chat', ...over }
}

function item(over: Partial<AttentionInput> = {}): AttentionInput {
  return {
    id: 'i1', item_kind: 'agent_request', status: 'pending', created_at: 1_000, message: 'Which bed gets the roses?',
    sender_name: 'PersonalClaw', channel_name: '', ...over,
  }
}

function sources(over: Partial<ThreadSources> = {}): ThreadSources {
  return { sessions: [], loops: [], items: [], approvals: [], ...over }
}

function thread(src: ThreadSources, key: string): Thread {
  const found = toThreads(src).flatMap((g) => g.threads).find((t) => t.key === key)
  expect(found, `no thread ${key}`).toBeDefined()
  return found!
}

/** The lanes Home puts this snapshot's cards in, by card key. */
function lanesOf(src: ThreadSources): Record<string, Lane> {
  const lanes = toLanes(src.items, src.approvals, src.sessions, src.loops)
  return Object.fromEntries(Object.values(lanes).flat().map((c) => [c.key, c.lane]))
}

describe("a thread's pip", () => {
  it('a chat with a pending approval is waiting on you, though its turn is running', () => {
    const src = sources({ sessions: [chat({ run_state: runningState({ phase: 'approval' }) })], approvals: [approval()] })
    expect(lanesOf(src)).toEqual({ 'approval:req-1': 'needs-approval', 'session:chat-1': 'working' })
    expect(thread(src, 'chat:chat-1').pip).toBe('waiting')
  })

  it('a chat whose agent asked you something is waiting on you', () => {
    const src = sources({ sessions: [chat()], items: [item({ refs: { session: 'chat-1', question: 'q1' } })] })
    expect(lanesOf(src)).toEqual({ 'inbox:i1': 'your-turn' })
    expect(thread(src, 'chat:chat-1').pip).toBe('waiting')
  })

  it('a chat whose turn is running is running', () => {
    const src = sources({ sessions: [chat({ run_state: runningState() })] })
    expect(lanesOf(src)).toEqual({ 'session:chat-1': 'working' })
    expect(thread(src, 'chat:chat-1').pip).toBe('running')
  })

  it('a running loop is running', () => {
    const src = sources({ loops: [loop()] })
    expect(lanesOf(src)).toEqual({ 'loop:a1b2c3d4': 'working' })
    expect(thread(src, 'loop:a1b2c3d4').pip).toBe('running')
  })

  it('a failed loop is failed, and no lane holds it', () => {
    const src = sources({ loops: [loop({ status: 'failed' })] })
    expect(lanesOf(src)).toEqual({})
    expect(thread(src, 'loop:a1b2c3d4').pip).toBe('failed')
  })

  it("a loop's worker asking an approval makes the loop wait on you, not a thread of its own", () => {
    const src = sources({
      sessions: [chat({ key: 'loop-a1b2c3d4-t-1', title: 'Untitled chat', origin: 'loop', source_id: 'a1b2c3d4', run_state: runningState({ phase: 'approval' }) })],
      loops: [loop()],
      approvals: [approval({ session: 'loop-a1b2c3d4-t-1', source_label: 'loop “Find a seed supplier”' })],
    })
    expect(lanesOf(src)).toEqual({ 'approval:req-1': 'needs-approval', 'loop:a1b2c3d4': 'working' })
    const threads = toThreads(src).flatMap((g) => g.threads)
    expect(threads.map((t) => [t.key, t.pip])).toEqual([['loop:a1b2c3d4', 'waiting']])
  })

  it('a loop waiting on your answer is waiting on you, and that outranks its failure', () => {
    const asks = item({ item_kind: 'needs_input', refs: { loop: 'a1b2c3d4' } })
    for (const status of ['needs_input', 'failed'] as const) {
      const src = sources({ loops: [loop({ status })], items: [asks] })
      expect(lanesOf(src)).toEqual({ 'inbox:i1': 'your-turn' })
      expect(thread(src, 'loop:a1b2c3d4').pip, status).toBe('waiting')
    }
  })

  it('an idle chat and a finished loop show no pip', () => {
    const src = sources({ sessions: [chat()], loops: [loop({ status: 'complete' })] })
    expect(lanesOf(src)).toEqual({})
    expect(toThreads(src).flatMap((g) => g.threads).map((t) => [t.key, t.pip])).toEqual([
      ['chat:chat-1', null],
      ['loop:a1b2c3d4', null],
    ])
  })

  it("work that is no thread's (another chat's ask, a workflow run's step) marks no thread", () => {
    const src = sources({
      sessions: [chat()],
      approvals: [approval({ session: 'workflow:9f9f9f9f:review' }), approval({ id: 'req-2', session: 'chat-gone' })],
      items: [item({ refs: { session: 'chat-gone' } })],
    })
    expect(Object.values(lanesOf(src)).sort()).toEqual(['needs-approval', 'needs-approval', 'your-turn'])
    expect(thread(src, 'chat:chat-1').pip).toBeNull()
  })
})
