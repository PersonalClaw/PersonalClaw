import { describe, it, expect } from 'vitest'
import { toLanes } from './attentionLanes'
import type { ActivityInput, LoopInput } from './attentionLanes'

// ── Mission Control's Working lane lists every RUNNING loop, whatever backs it ─────────────────
//
// Measured 2026-09-25: a General loop was working while this lane said "Nothing is running right
// now". A General loop is a workflow run (PP-16) and its stages are subagents, so no chat session
// carries it — and chat sessions were the lane's only source. `GET /api/loops` lists run-backed
// loops beside the loops-table rows, so the listing is the lane's second source.
//
// 🪤 A loops-table loop's worker IS a chat session, so a naive second source counts that loop twice.
// One worker must be one card — the loop's, which names it and opens its cockpit.

function mkLoop(over: Partial<LoopInput> = {}): LoopInput {
  return {
    id: 'r1', kind: 'general', name: 'Weekly checklist', task: 'write a checklist',
    status: 'running', total_cycles: 1, max_cycles: 30, started_at: 1_000, run_id: 'r1',
    ...over,
  }
}

function mkSession(over: Partial<ActivityInput> = {}): ActivityInput {
  return { key: 'chat-1', title: 'a chat', running: true, stopping: false, pending_approval: false, ...over }
}

describe('the Working lane', () => {
  it('cards a running run-backed loop, linked to its run page', () => {
    const working = toLanes([], [], [], [mkLoop()]).working
    expect(working.map((c) => c.key)).toEqual(['loop:r1'])
    const card = working[0]
    expect(card.origin).toBe('loop')
    expect(card.title).toBe('Weekly checklist')
    // The open cycle counts (`shownCycle`), matching every other loop surface.
    expect(card.subtitle).toBe('running · cycle 2/30')
    expect(card.refs).toEqual({ link: '#/workflows/runs/r1' })
  })

  it('does not card a loop that is parked — paused is not working', () => {
    const lanes = toLanes([], [], [], [
      mkLoop({ id: 'p', run_id: 'p', status: 'paused' }),
      mkLoop({ id: 'n', run_id: 'n', status: 'needs_input' }),
      mkLoop({ id: 'c', run_id: 'c', status: 'complete' }),
    ])
    expect(lanes.working).toEqual([])
  })

  it("a loops-table loop's worker session is ONE card — the loop's", () => {
    const goal = mkLoop({ id: 'g1', kind: 'goal', run_id: undefined, session_key: 'loop-g1' })
    const working = toLanes([], [], [mkSession({ key: 'loop-g1', title: 'Untitled chat' }), mkSession()], [goal]).working
    expect(working.map((c) => c.key).sort()).toEqual(['loop:g1', 'session:chat-1'])
    expect(working.find((c) => c.key === 'loop:g1')!.refs).toEqual({ link: '#/loops/g1' })
  })

  it('still cards running chat sessions when no loop is given — the two-source call is unchanged', () => {
    expect(toLanes([], [], [mkSession()]).working.map((c) => c.key)).toEqual(['session:chat-1'])
  })

  it('a malformed loop row is skipped, never thrown on', () => {
    const junk = [null, 'x', { id: '' }, mkLoop()] as unknown as LoopInput[]
    expect(() => toLanes([], [], [], junk)).not.toThrow()
    expect(toLanes([], [], [], junk).working.map((c) => c.key)).toEqual(['loop:r1'])
  })
})
