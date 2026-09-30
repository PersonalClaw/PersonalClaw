import { describe, it, expect } from 'vitest'
import { toLanes } from './attentionLanes'
import type { ActivityInput, LoopInput } from './attentionLanes'

// ── Mission Control's Working lane shows each loop ONCE, by its name ──────────────────────────
//
// Measured on a live install: an Attended Code loop running a per-task worker read twice under
// Working — its own card ("… · running · cycle 3/30", opening the loop) and a second card titled by
// the worker's internal session key (`loop-<id>-<task> · running`), with no link. Only the loop's
// stage-worker key was skipped; its task workers and its planner are sessions of the loop too, and
// the session list already says whose they are (`origin: 'loop'`, `source_id`).

const LOOP = 'a1b2c3d4'

function mkLoop(over: Partial<LoopInput> = {}): LoopInput {
  return {
    id: LOOP, kind: 'code', name: 'Update the README', task: 'update the README',
    status: 'running', total_cycles: 2, max_cycles: 30, started_at: 1_000, session_key: `loop-${LOOP}`,
    ...over,
  }
}

function mkSession(over: Partial<ActivityInput> = {}): ActivityInput {
  return { key: 'chat-1-1790000000', title: 'Trip planning', running: true, stopping: false, pending_approval: false, ...over }
}

/** A session of the loop, as `GET /api/chat/sessions` lists it. */
function workerOf(key: string, over: Partial<ActivityInput> = {}): ActivityInput {
  return mkSession({ key, title: key, origin: 'loop', source_id: LOOP, source_label: 'Update the README', ...over })
}

const noCardIsTitledByAKey = (titles: string[]) =>
  expect(titles.filter((t) => /^(loop|chat)-/.test(t)), 'a card is titled by an internal session key').toEqual([])

describe('the Working lane', () => {
  it("a running loop's task worker is part of the loop's one card", () => {
    const working = toLanes([], [], [
      workerOf(`loop-${LOOP}`),
      workerOf(`loop-${LOOP}-t-9f8e7d6c`),
    ], [mkLoop()]).working

    expect(working.map((c) => c.key)).toEqual([`loop:${LOOP}`])
    expect(working[0].title).toBe('Update the README')
    expect(working[0].refs).toEqual({ link: `#/code/${LOOP}` })
    noCardIsTitledByAKey(working.map((c) => c.title))
  })

  it('a loop that is planning is carded once, by its name, while its planner runs', () => {
    const working = toLanes([], [], [workerOf(`loop-plan-${LOOP}`)], [mkLoop({ status: 'planning' })]).working

    expect(working.map((c) => [c.key, c.title, c.subtitle])).toEqual([
      [`loop:${LOOP}`, 'Update the README', 'planning'],
    ])
    expect(working[0].refs).toEqual({ link: `#/code/${LOOP}` })
  })

  it("a paused loop's worker still winding down is the loop, stopping", () => {
    const working = toLanes([], [], [
      workerOf(`loop-${LOOP}-t-9f8e7d6c`, { running: false, stopping: true }),
      workerOf(`loop-${LOOP}-t-1a2b3c4d`, { running: false, stopping: true }),
    ], [mkLoop({ status: 'paused' })]).working

    expect(working.map((c) => [c.key, c.title, c.subtitle])).toEqual([
      [`loop:${LOOP}`, 'Update the README', 'stopping'],
    ])
  })

  it('a session whose loop the snapshot lacks is named by its label, never by its key', () => {
    const working = toLanes([], [], [workerOf(`loop-${LOOP}-t-9f8e7d6c`)], []).working
    expect(working.map((c) => c.title)).toEqual(['Update the README'])
  })

  it('a running chat with no title yet is named as every surface names it, not by its key', () => {
    const working = toLanes([], [], [
      mkSession({ key: 'chat-7-1790000123', title: 'chat-7-1790000123', prompt_preview: 'What changed in the parser?' }),
      mkSession({ key: 'chat-8-1790000456', title: '' }),
    ], []).working

    expect(working.map((c) => c.title).sort()).toEqual(['Untitled chat', 'What changed in the parser?'])
    noCardIsTitledByAKey(working.map((c) => c.title))
  })

  it("a wire without the session's origin still folds the stage worker into its loop", () => {
    const working = toLanes([], [], [mkSession({ key: `loop-${LOOP}`, title: `loop-${LOOP}` })], [mkLoop()]).working
    expect(working.map((c) => c.key)).toEqual([`loop:${LOOP}`])
  })
})
