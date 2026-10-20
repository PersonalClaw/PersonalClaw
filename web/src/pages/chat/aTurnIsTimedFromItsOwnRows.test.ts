import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'

// ── After a reload, a turn is timed from the rows it wrote ──────────────────────────────────────
//
// A page that heard a turn end has the gateway's own start and end for it (`run_state`). A reload
// has only the rows, so each run of a turn is timed from the first row it wrote to its last, and is
// named by the row that started it, the name the gateway gives the running turn
// (`run_state.turn.message_id`), so the live times replace the rows' for the same run.
//
// Its first row, not the row that started it: a regenerated answer, or a failed turn tried again,
// keeps the question's row and its old time, and from there it would count the hours between.

const ASKED: HistMsg = { role: 'user', content: 'Why is the nightly export slow?', ts: '2026-10-04T09:00:00+00:00' }
const tool = (id: string, ts?: string): HistMsg => ({
  role: 'tool', content: 'Terminal', ...(ts ? { ts } : {}),
  meta: { tool_call_id: id, input: '{}', kind: 'execute', done: true, output: 'ok' },
})
const answer = (content: string, ts?: string, extra: Partial<HistMsg> = {}): HistMsg => ({ role: 'assistant', content, ...(ts ? { ts } : {}), ...extra })

describe('a reloaded turn is timed from its own rows', () => {
  it('runs from the first row the turn wrote to its last, named by the row that started it', () => {
    const turns = hydrateTurns([ASKED, tool('tc1', '2026-10-04T09:00:02+00:00'), tool('tc2', '2026-10-04T09:00:05+00:00'), answer('Found it.', '2026-10-04T09:00:12+00:00')])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].runs).toEqual([{ id: ASKED.ts, start: '2026-10-04T09:00:02+00:00', end: '2026-10-04T09:00:12+00:00' }])
    expect(turns[0].runs).toBeUndefined()
  })

  it('times a regenerated answer from its own first step, not from the question asked an hour before', () => {
    const turns = hydrateTurns([
      ASKED,
      tool('tc9', '2026-10-04T10:00:01+00:00'),
      answer('The export re-reads the table for every page.', '2026-10-04T10:00:07+00:00', {
        variants: [{ content: 'It is slow.', ts: '2026-10-04T09:00:20+00:00' }, { content: 'The export re-reads the table for every page.', ts: '2026-10-04T10:00:07+00:00' }],
        variant_idx: 1,
      }),
    ])
    expect(turns[1].runs).toEqual([{ id: ASKED.ts, start: '2026-10-04T10:00:01+00:00', end: '2026-10-04T10:00:07+00:00' }])
  })

  it('counts each turn an answer is shared by on its own, not the wait between them', () => {
    // A helper's report starts a turn of its own, and its answer joins the one before it.
    const REPORT: HistMsg = { role: 'subagent', content: 'The helper finished reading the logs.', ts: '2026-10-04T09:30:00+00:00' }
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      answer('A helper is reading the logs.', '2026-10-04T09:00:05+00:00'),
      REPORT,
      tool('tc2', '2026-10-04T09:30:01+00:00'),
      answer('The logs show one slow query.', '2026-10-04T09:30:09+00:00'),
    ])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].runs).toEqual([
      { id: ASKED.ts, start: '2026-10-04T09:00:02+00:00', end: '2026-10-04T09:00:05+00:00' },
      { id: REPORT.ts, start: '2026-10-04T09:30:01+00:00', end: '2026-10-04T09:30:09+00:00' },
    ])
  })

  it('keeps a turn she steered as one run, on the answer it ended on, as the gateway times it', () => {
    const STEER: HistMsg = { role: 'user', content: 'Only the export, not the import.', ts: '2026-10-04T09:00:06+00:00', meta: { steered: true } }
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      answer('Looking at both jobs.', '2026-10-04T09:00:04+00:00'),
      STEER,
      tool('tc2', '2026-10-04T09:00:07+00:00'),
      answer('The export re-reads the table.', '2026-10-04T09:00:12+00:00'),
    ])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(turns[1].runs).toBeUndefined()
    expect(turns[3].runs).toEqual([{ id: ASKED.ts, start: '2026-10-04T09:00:02+00:00', end: '2026-10-04T09:00:12+00:00' }])
  })

  it('times no run whose rows carry no time', () => {
    const turns = hydrateTurns([ASKED, tool('tc1'), answer('Found it.')])
    expect(turns[1].runs).toBeUndefined()
  })

  it('names a run by its first row when the history starts inside it', () => {
    const turns = hydrateTurns([tool('tc1', '2026-10-04T09:00:02+00:00'), answer('Found it.', '2026-10-04T09:00:12+00:00')])
    expect(turns[0].runs).toEqual([{ id: '2026-10-04T09:00:02+00:00', start: '2026-10-04T09:00:02+00:00', end: '2026-10-04T09:00:12+00:00' }])
  })
})
