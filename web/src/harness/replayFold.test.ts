import { describe, it, expect } from 'vitest'
import {
  replayChat,
  replayRun,
  adjacentDuplicateTextCount,
  type ChatStep,
} from './replayFold'

// Event-trace replay regression (FE side).
//
// These feed recorded chat/run traces through the SAME pure folds the live UI uses. The
// load-bearing test catches a re-introduced duplicate answer: a trace that a CORRECT
// coalescer collapses to one text segment. We prove the healthy fold does so (0 adjacent
// duplicates) AND that an answer-rendered-twice mis-drive (a flush that pushes instead of replacing,
// modeled by a spurious release of the text run) produces the duplicate the metric catches —
// so a regression is caught by replay, not only by a hand-written unit test.

// ── happy-path-chat: streamed reply coalesces to one segment ──────────────────

const HAPPY_PATH: ChatStep[] = [
  { kind: 'flush', text: 'The' },
  { kind: 'flush', text: 'The answer' },
  { kind: 'flush', text: 'The answer is 42.' },
]

describe('replayChat — happy path', () => {
  it('coalesces a growing streamed reply into a single text segment', () => {
    const { segs } = replayChat(HAPPY_PATH)
    const texts = segs.filter((s) => s.kind === 'text')
    expect(texts).toHaveLength(1)
    expect(texts[0]).toMatchObject({ kind: 'text', text: 'The answer is 42.' })
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })
})

// ── history-overlap-guard: activity preamble stays above the answer ────────────

const WITH_ACTIVITY: ChatStep[] = [
  { kind: 'flush', text: 'Answer' },
  { kind: 'activity', text: 'recalled 3 memories', activityKind: 'memory' },
  { kind: 'flush', text: 'Answer complete.' },
]

describe('replayChat — activity insertion', () => {
  it('inserts the activity BEFORE the active text run so the flush replaces in place', () => {
    const { segs } = replayChat(WITH_ACTIVITY)
    // Order: activity preamble, then the single (replaced) text run — no duplicate.
    expect(segs.map((s) => s.kind)).toEqual(['activity', 'text'])
    const text = segs.find((s) => s.kind === 'text')
    expect(text).toMatchObject({ text: 'Answer complete.' })
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })
})

// ── the duplicate-answer regression proof ─────────────────────────────────────

describe('replayChat — duplicate-answer detection', () => {
  it('a correct coalescer produces zero adjacent duplicate text segments', () => {
    const { segs } = replayChat(HAPPY_PATH)
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })

  it('catches the answer-rendered-twice signature when a boundary wrongly splits a continuing run', () => {
    // The mis-drive: a spurious boundary mid-stream resets coalescing, so the
    // next flush PUSHES a new segment instead of replacing — the "answer rendered twice"
    // shape. Replay surfaces it as an adjacent duplicate the metric flags.
    const DOUBLED_TRACE: ChatStep[] = [
      { kind: 'flush', text: 'The answer is 42.' },
      { kind: 'boundary' }, // spurious reset (the bug) — no real tool/approval happened
      { kind: 'flush', text: 'The answer is 42.' },
    ]
    const { segs } = replayChat(DOUBLED_TRACE)
    expect(adjacentDuplicateTextCount(segs)).toBe(1)
  })
})

// ── run-stream replay: lifecycle folds to terminal flags ──────────────────────

describe('replayRun — lifecycle fold', () => {
  it('a gate failure then a passing re-run clears the banner', () => {
    const flags = replayRun([
      { event: 'gate_check', data: { ok: false, label: 'lint', command: 'make lint', output: 'x' } },
      { event: 'gate_check', data: { ok: true } },
    ])
    expect(flags.gate).toBeNull()
  })

  it('a stall then a cycle verdict clears the stall + judge-degraded flags', () => {
    const flags = replayRun([
      { event: 'judge_error' },
      { event: 'stage_stalled', data: { stage: 'build', findings: 2 } },
      { event: 'cycle_verdict' },
    ])
    expect(flags.stall).toBeNull()
    expect(flags.judgeDegraded).toBe(false)
  })

  it('deleted is terminal and sticks', () => {
    const flags = replayRun([{ event: 'deleted' }, { event: 'new_finding' }])
    expect(flags.deleted).toBe(true)
  })
})
