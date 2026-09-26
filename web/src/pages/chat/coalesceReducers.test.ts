import { describe, it, expect } from 'vitest'
import { applyCoalescedFlush, insertActivity, TextRunOwnership, type SegmentsUpdate } from './coalesceReducers'
import type { Segment } from './chatTypes'

// Regression suite for the chat stream coalescer's segment attribution — the exact
// logic behind K42 (mid-stream activity duplicated the reply), K44 (turn N+1 absorbed
// turn N's answer) and K45 (edit-resend glued the new answer onto the old). These
// lived inline in ChatPage and were untested, which is how they shipped. Locking them.

const text = (t: string): Segment => ({ kind: 'text', text: t })
const activity = (t: string): Segment => ({ kind: 'activity', text: t, activityKind: 'context' })
const tool = (): Segment => ({ kind: 'tool', id: 't1', tool: 'x', done: false } as Segment)

describe('applyCoalescedFlush — replace vs push', () => {
  it('first flush of a fresh run PUSHES a text segment', () => {
    expect(applyCoalescedFlush([], 'Hel', false)).toEqual([text('Hel')])
  })

  it('subsequent flushes REPLACE the owned trailing text in place (no growth in count)', () => {
    let segs = applyCoalescedFlush([], 'Hel', false)
    segs = applyCoalescedFlush(segs, 'Hello', true)
    segs = applyCoalescedFlush(segs, 'Hello world', true)
    expect(segs).toEqual([text('Hello world')]) // ONE segment, replaced each frame
  })

  it('K44/K45: a flush that does not own the tail (fresh send/turn) PUSHES beside prior text — never replaces it', () => {
    // Prior turn left an owned text run; a new send releases it. The next flush must OPEN A
    // NEW segment, not overwrite/absorb the prior turn's answer.
    const prior = [text('Turn 1 full answer.')]
    const segs = applyCoalescedFlush(prior, 'Turn 2', false)
    expect(segs).toEqual([text('Turn 1 full answer.'), text('Turn 2')])
    expect(segs[0]).toEqual(text('Turn 1 full answer.')) // prior answer intact, not glued/absorbed
  })

  it('K42: after an activity segment interleaves, an owning flush does NOT duplicate — it replaces the tail only when the tail is text', () => {
    // Simulate: text run owned, then activity inserted BEFORE it (via insertActivity),
    // so the tail is STILL the text run → flush replaces in place (no duplicate push).
    let segs: Segment[] = [text('The answer so far')]
    segs = insertActivity(segs, 'recalled context', 'context', true) // K42 insert
    // tail is still the text run
    expect(segs[segs.length - 1]).toEqual(text('The answer so far'))
    segs = applyCoalescedFlush(segs, 'The answer so far, extended', true)
    // exactly one text segment (replaced), plus the one activity — NOT two text blocks
    expect(segs.filter((s) => s.kind === 'text')).toHaveLength(1)
    expect(segs.filter((s) => s.kind === 'activity')).toHaveLength(1)
    expect(segs[segs.length - 1]).toEqual(text('The answer so far, extended'))
  })
})

describe('insertActivity — K42 ordering discipline', () => {
  it('inserts BEFORE the active coalesced text run (keeps text as the tail)', () => {
    const segs = insertActivity([text('streaming answer')], 'recalled context', 'context', true)
    expect(segs).toEqual([activity('recalled context'), text('streaming answer')])
    expect(segs[segs.length - 1].kind).toBe('text') // tail stays text → next flush replaces in place
  })

  it('appends at the end when no run is live (turn done — no active run to protect)', () => {
    const segs = insertActivity([text('final answer')], 'telemetry', 'context', false)
    expect(segs).toEqual([text('final answer'), activity('telemetry')])
  })

  it('de-dupes an identical adjacent activity line (returns same array by identity)', () => {
    const start: Segment[] = [activity('recalled context'), text('answer')]
    const out = insertActivity(start, 'recalled context', 'context', true)
    expect(out).toBe(start) // no-op — the neighbor at the insert point is the same line
  })

  it('tool cards win — activity is dropped when a tool segment is present', () => {
    const start: Segment[] = [tool()]
    expect(insertActivity(start, 'recalled context', 'context', true)).toBe(start)
  })
})

describe('TextRunOwnership — every decision is taken at dispatch', () => {
  // React applies a queued updater whenever it next renders, which can be after later WS
  // callbacks have moved ownership. Each case below queues updaters, releases the run, and
  // only THEN applies them — the interleaving a shared render batch produces.
  const applyAll = (segs: Segment[], queued: SegmentsUpdate[]) => queued.reduce((s, u) => u(s), segs)

  it('a flush queued before the release still replaces the tail it owned (#3513)', () => {
    const run = new TextRunOwnership()
    const painted = applyAll([], [run.flush('partial')])
    const queued = [run.flush('complete answer')]  // chat_done: seal() emits the terminal flush…
    run.release()                                   // …then endTextRun releases, synchronously
    expect(applyAll(painted, queued)).toEqual([text('complete answer')])
  })

  it('an activity line queued before the release still lands ABOVE the live text', () => {
    // The day56b s14 doubling: the stats line and chat_done shared a render batch.
    const run = new TextRunOwnership()
    const painted = applyAll([], [run.flush('the whole answer')])
    const queued = [run.activity('Turn complete', 'stats'), run.flush('the whole answer')]
    run.release()
    expect(applyAll(painted, queued)).toEqual([
      { kind: 'activity', text: 'Turn complete', activityKind: 'stats' },
      text('the whole answer'),
    ])
  })

  it('an activity line dispatched AFTER the release lands below the finished answer', () => {
    const run = new TextRunOwnership()
    const painted = applyAll([], [run.flush('the whole answer')])
    run.release()
    expect(applyAll(painted, [run.activity('learned 1', 'learned')])).toEqual([
      text('the whole answer'),
      { kind: 'activity', text: 'learned 1', activityKind: 'learned' },
    ])
  })

  it('adopt() makes a hydrated trailing partial the live run, so the next flush extends it', () => {
    const run = new TextRunOwnership()
    run.adopt()
    expect(applyAll([text('Half an ans')], [run.flush('Half an answer, then the rest.')]))
      .toEqual([text('Half an answer, then the rest.')])
  })

  it('carries the activity origin onto the line it inserted', () => {
    const run = new TextRunOwnership()
    const [line] = applyAll([], [run.activity('Learned: X', 'learned', 'facet')])
    expect(line).toMatchObject({ kind: 'activity', origin: 'facet' })
  })
})
