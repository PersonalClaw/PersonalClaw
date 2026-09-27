import { describe, it, expect } from 'vitest'
import { toLanes } from './attentionLanes'
import type { LoopInput, RunInput } from './attentionLanes'

// ── Mission Control's Working lane lists every running workflow RUN, not only loops (F-32) ────
//
// The lane read chat sessions and `GET /api/loops`, and a run started from Workflows (a template,
// a trigger's Run workflow, a project's work) is neither: no chat session carries it, and the loop
// listing holds only the runs started AS loops. So a digest run could be working for an hour while
// this lane said "Nothing is running right now." `GET /api/workflows/runs?status=running` is the
// third source.
//
// 🪤 A run-backed loop is a workflow run too, and the loop listing already cards it — by its loop
// name, opening its cockpit. One piece of work, one card: the loop's.

function mkRun(over: Partial<RunInput> = {}): RunInput {
  return {
    id: 'run-1', workflow_name: 'paper-ingest', title: '', status: 'running',
    started_at: '2026-09-26T09:00:00Z', created_at: '2026-09-26T08:59:58Z', parent_run_id: '', ...over,
  }
}

function mkLoop(over: Partial<LoopInput> = {}): LoopInput {
  return {
    id: 'loop-run', kind: 'general', name: 'Weekly checklist', task: 'write a checklist',
    status: 'running', total_cycles: 1, max_cycles: 30, started_at: 1_000, run_id: 'loop-run', ...over,
  }
}

describe('the Working lane', () => {
  it('🔑 cards a running workflow run that is not a loop, linked to its run page', () => {
    const working = toLanes([], [], [], [], [mkRun()]).working
    expect(working.map((c) => c.key)).toEqual(['run:run-1'])
    const card = working[0]
    expect(card.origin).toBe('run')
    expect(card.title).toBe('paper-ingest')
    expect(card.subtitle).toBe('running')
    expect(card.refs).toEqual({ link: '#/workflows/runs/run-1' })
    expect(card.at).toBe(Date.parse('2026-09-26T09:00:00Z') / 1000)
  })

  it('names a run by its own title when it has one', () => {
    const [card] = toLanes([], [], [], [], [mkRun({ title: 'Ingest the attention paper' })]).working
    expect(card.title).toBe('Ingest the attention paper')
  })

  it('says a run that has not started yet is waiting for a slot, not working', () => {
    const [card] = toLanes([], [], [], [], [mkRun({ started_at: null })]).working
    expect(card.subtitle).toBe('waiting for a slot')
    expect(card.at).toBe(Date.parse('2026-09-26T08:59:58Z') / 1000)
  })

  it('🔑 a run that backs a loop is ONE card — the loop’s — whatever the loop’s status', () => {
    const lanes = toLanes([], [], [], [mkLoop()], [mkRun({ id: 'loop-run' }), mkRun({ id: 'run-2' })])
    expect(lanes.working.map((c) => c.key).sort()).toEqual(['loop:loop-run', 'run:run-2'])
    const parked = toLanes([], [], [], [mkLoop({ status: 'paused' })], [mkRun({ id: 'loop-run' })])
    expect(parked.working).toEqual([])
  })

  it('a sub-run is part of the run that spawned it, so it is not a card of its own', () => {
    const lanes = toLanes([], [], [], [], [mkRun(), mkRun({ id: 'child', parent_run_id: 'run-1' })])
    expect(lanes.working.map((c) => c.key)).toEqual(['run:run-1'])
  })

  it('only running runs are working — paused, parked and finished ones are not', () => {
    const lanes = toLanes([], [], [], [], [
      mkRun({ id: 'p', status: 'paused' }),
      mkRun({ id: 'n', status: 'needs_input' }),
      mkRun({ id: 'c', status: 'complete' }),
    ])
    expect(lanes.working).toEqual([])
  })

  it('a malformed run row is skipped, never thrown on', () => {
    const junk = [null, 'x', { id: '' }, mkRun()] as unknown as RunInput[]
    expect(() => toLanes([], [], [], [], junk)).not.toThrow()
    expect(toLanes([], [], [], [], junk).working.map((c) => c.key)).toEqual(['run:run-1'])
  })

  it('the four-source call is unchanged', () => {
    expect(toLanes([], [], [], [mkLoop()]).working.map((c) => c.key)).toEqual(['loop:loop-run'])
  })
})
