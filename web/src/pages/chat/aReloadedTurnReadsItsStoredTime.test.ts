import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'
import { workedFor, workedMs } from './turnRuns'

// ── After a reload, a turn says the time the gateway measured for it ────────────────────────────
//
// The gateway measures each turn it runs and stores the measurement with the turn's last answer
// row (`meta.turn_telemetry.duration_ms`). A reload reads that, so the folded line says what the
// live page said, and a regenerated answer or a retried turn is timed exactly. The span from the
// turn's first row to its last is only the fallback, for a turn stored without one: an older row,
// or a turn that reported nothing.

const ASKED: HistMsg = { role: 'user', content: 'Why is the nightly export slow?', ts: '2026-10-04T09:00:00+00:00' }
const tool = (id: string, ts?: string): HistMsg => ({
  role: 'tool', content: 'Terminal', ...(ts ? { ts } : {}),
  meta: { tool_call_id: id, input: '{}', kind: 'execute', done: true, output: 'ok' },
})
/** The record the gateway stores with a turn's last answer row, measured at `ms`. */
const stored = (ms: unknown) => ({ turn_telemetry: { duration_ms: ms, line: 'Turn complete: 4 events, 2 tool calls', model: 'scripted-1' } }) as HistMsg['meta']
const answer = (content: string, ts?: string, extra: Partial<HistMsg> = {}): HistMsg => ({ role: 'assistant', content, ...(ts ? { ts } : {}), ...extra })

describe('a reloaded turn reads the time the gateway stored for it', () => {
  it('says the stored time, not the span of its rows', () => {
    // Its rows run from 09:00:02 to 09:00:12, 10 s; the gateway measured 12.1 s.
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      tool('tc2', '2026-10-04T09:00:05+00:00'),
      answer('Found it.', '2026-10-04T09:00:12+00:00', { meta: stored(12_100) }),
    ])
    expect(workedMs(turns[1].runs)).toBe(12_100)
    expect(workedFor(workedMs(turns[1].runs))).toBe('12s')
    // Still named by the row that started the turn, so a live page's run state replaces it.
    expect(turns[1].runs?.map((r) => r.id)).toEqual([ASKED.ts])
  })

  it('times a regenerated answer by the time stored for the regeneration', () => {
    const turns = hydrateTurns([
      ASKED,
      tool('tc9', '2026-10-04T10:00:01+00:00'),
      answer('The export re-reads the table for every page.', '2026-10-04T10:00:07+00:00', {
        variants: [{ content: 'It is slow.', ts: '2026-10-04T09:00:20+00:00' }, { content: 'The export re-reads the table for every page.', ts: '2026-10-04T10:00:07+00:00' }],
        variant_idx: 1,
        meta: stored(7_300),
      }),
    ])
    expect(workedMs(turns[1].runs)).toBe(7_300)
  })

  it('says the sum of the times stored for each turn an answer is shared by', () => {
    // A helper's report starts a turn of its own, and its answer joins the one before it.
    const REPORT: HistMsg = { role: 'subagent', content: 'The helper finished reading the logs.', ts: '2026-10-04T09:30:00+00:00' }
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      answer('A helper is reading the logs.', '2026-10-04T09:00:05+00:00', { meta: stored(5_200) }),
      REPORT,
      tool('tc2', '2026-10-04T09:30:01+00:00'),
      answer('The logs show one slow query.', '2026-10-04T09:30:09+00:00', { meta: stored(9_100) }),
    ])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].runs?.map((r) => r.id)).toEqual([ASKED.ts, REPORT.ts])
    expect(workedMs(turns[1].runs)).toBe(14_300)
  })

  it('keeps a turn she steered as one run, with the time stored on the answer it ended on', () => {
    const STEER: HistMsg = { role: 'user', content: 'Only the export, not the import.', ts: '2026-10-04T09:00:06+00:00', meta: { steered: true } }
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      answer('Looking at both jobs.', '2026-10-04T09:00:04+00:00'),
      STEER,
      tool('tc2', '2026-10-04T09:00:07+00:00'),
      answer('The export re-reads the table.', '2026-10-04T09:00:12+00:00', { meta: stored(12_500) }),
    ])
    expect(turns[1].runs).toBeUndefined()
    expect(turns[3].runs?.map((r) => r.id)).toEqual([ASKED.ts])
    expect(workedMs(turns[3].runs)).toBe(12_500)
  })

  it('reads the stored time even where the rows carry none', () => {
    const turns = hydrateTurns([ASKED, tool('tc1'), answer('Found it.', undefined, { meta: stored(8_000) })])
    expect(workedMs(turns[1].runs)).toBe(8_000)
  })

  it('falls back to the span of the rows for a turn stored without a time', () => {
    for (const meta of [undefined, { turn_telemetry: { line: 'Turn complete: 4 events' } }, stored(null), stored('12100'), stored(-5)]) {
      const turns = hydrateTurns([
        ASKED,
        tool('tc1', '2026-10-04T09:00:02+00:00'),
        answer('Found it.', '2026-10-04T09:00:12+00:00', meta ? { meta } : {}),
      ])
      expect(workedMs(turns[1].runs), JSON.stringify(meta)).toBe(10_000)
    }
  })

  it('falls back turn by turn: a shared answer adds a stored time to a span of rows', () => {
    const REPORT: HistMsg = { role: 'subagent', content: 'The helper finished reading the logs.', ts: '2026-10-04T09:30:00+00:00' }
    const turns = hydrateTurns([
      ASKED,
      tool('tc1', '2026-10-04T09:00:02+00:00'),
      answer('A helper is reading the logs.', '2026-10-04T09:00:05+00:00'),
      REPORT,
      tool('tc2', '2026-10-04T09:30:01+00:00'),
      answer('The logs show one slow query.', '2026-10-04T09:30:09+00:00', { meta: stored(9_100) }),
    ])
    expect(workedMs(turns[1].runs)).toBe(3_000 + 9_100)
  })
})
