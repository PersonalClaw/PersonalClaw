import { describe, expect, it } from 'vitest'
import { currentMarkRange } from './sessionMapRegion'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment, SubagentCard } from './chatTypes'

// ── SSM-5 (the arithmetic half) — the current region as a contiguous mark range ──────────────
//
// `currentMarkRange` is the pure half of "viewport → accent": given the turn coordinates that are
// on screen, which MARKS light coral. The DOM half (the IntersectionObserver that produces those
// coordinates, and the repaint) is `SessionMapRail.viewport.test.tsx`.
//
// VACUITY FLOOR — three ways a green here would mean nothing:
//
//  1. A BINARY SEARCH IS ONLY CORRECT ON SORTED INPUT, and this one searches `visibleIndex` over
//     the array `sessionMapMarks` produces. If a future mark kind appended out of order the search
//     would silently return a wrong range on real data while any hand-built fixture still passed.
//     So the ordering is asserted against the REAL derivation, subagent marks included (they are
//     appended last and carry the LAST turn's coordinate, which is the one case that could break
//     it).
//  2. "THE RANGE IS RIGHT" is satisfiable by a fixture whose answer is the whole array. Every case
//     below asserts a range that is a strict, non-empty, non-total subset, and the multi-turn case
//     is checked against an INDEPENDENT linear reference over every possible window — so the
//     search is compared to a different implementation, not to my expectation of it.
//  3. THE EMPTY-SET CASE is the one a test that only ever mounts an unscrolled transcript hits, and
//     it is also the SSM-4 behaviour that already shipped — so it is the one case that passes with
//     zero new code. It is asserted here, and then asserted to be DIFFERENT from every scrolled
//     case, which is what makes the rest of the file load-bearing.

const TS = '2026-09-16T10:00:00.000Z'

/** `n` alternating turns, every assistant turn carrying a tool + an error sub-event, so most turns
 *  own several marks and a turn range is never a mark range of the same size. */
function transcript(n: number): ChatTurn[] {
  const subs: Segment[] = [
    { kind: 'tool', id: 't', tool: 'Terminal', detail: 'npm run build', done: true },
    { kind: 'error', text: 'ValidationException: input is too long.' },
  ]
  return Array.from({ length: n }, (_, i) => ({
    role: i % 2 === 0 ? 'user' : 'assistant',
    ts: TS,
    visibleIndex: i,
    segments: i % 2 === 0 ? [{ kind: 'text', text: `prompt ${i}` } as Segment] : [{ kind: 'text', text: `reply ${i}` } as Segment, ...subs],
  }))
}

/** The answer, computed the slow obvious way. Deliberately a different algorithm from the one under
 *  test: a full scan collecting every index whose coordinate is inside the window. */
function referenceRange(coords: number[], visible: number[]): [number, number] {
  const hits = coords.map((c, i) => [c, i] as const)
    .filter(([c]) => c >= Math.min(...visible) && c <= Math.max(...visible))
    .map(([, i]) => i)
  return hits.length ? [hits[0], hits[hits.length - 1]] : [0, -1]
}

describe('the mark array the search assumes is actually sorted', () => {
  it('visibleIndex is non-decreasing across sessionMapMarks output, subagents included', () => {
    const subagents: SubagentCard[] = [
      { id: 's-1', task: 'Investigate the flaky snapshot test', agent: 'general-purpose', done: true },
      { id: 's-2', task: 'Re-run the suite', agent: 'general-purpose', done: false },
    ]
    const marks = sessionMapMarks(transcript(9), subagents)
    expect(marks.length).toBeGreaterThan(9)
    for (let i = 1; i < marks.length; i++) {
      expect(
        marks[i].visibleIndex,
        `mark ${i} (${marks[i].kind}) went backwards — currentMarkRange's binary search is invalid`,
      ).toBeGreaterThanOrEqual(marks[i - 1].visibleIndex)
    }
    // And the appended subagent marks really do sit at the newest coordinate, which is the case
    // that would break the ordering if they were given their own.
    const last = marks[marks.length - 1]
    expect(last.kind).toBe('subagent')
    expect(last.visibleIndex).toBe(8)
  })
})

describe('currentMarkRange — viewport coordinates to a contiguous mark slice', () => {
  const marks = sessionMapMarks(transcript(9))
  const coords = marks.map((m) => m.visibleIndex)

  it('with NOTHING reported on screen, the newest turn is current (the on-load state)', () => {
    const [lo, hi] = currentMarkRange(marks, [])
    // Turn 8 is a user turn: exactly one mark, the last one. A strict, non-total subset.
    expect([lo, hi]).toEqual([marks.length - 1, marks.length - 1])
    expect(lo).toBeGreaterThan(0)
  })

  it('a scrolled window accents THAT window, and it is not the newest-turn answer', () => {
    const [lo, hi] = currentMarkRange(marks, [2, 3])
    const inRange = marks.slice(lo, hi + 1)
    expect(inRange.every((m) => m.visibleIndex === 2 || m.visibleIndex === 3)).toBe(true)
    // Turn 3 is an assistant turn (3 marks) and turn 2 a user turn (1 mark) → 4 marks.
    expect(inRange).toHaveLength(4)
    // The whole point: this is a DIFFERENT answer from the unscrolled one.
    expect([lo, hi]).not.toEqual(currentMarkRange(marks, []))
  })

  it('agrees with an independent linear scan for EVERY window over the transcript', () => {
    let windows = 0
    for (let a = 0; a <= 8; a++) {
      for (let b = a; b <= 8; b++) {
        const visible = Array.from({ length: b - a + 1 }, (_, k) => a + k)
        expect(currentMarkRange(marks, visible), `window ${a}..${b}`).toEqual(referenceRange(coords, visible))
        windows++
      }
    }
    // A loop that ran zero times would pass silently.
    expect(windows).toBe(45)
  })

  it('a non-contiguous visible set spans its extremes — the region is a range, not a set', () => {
    // Turns 1 and 5 on screen with 2-4 scrolled past cannot happen for a single scroll container,
    // but the contract has to be total: the range covers the extremes rather than throwing.
    const [lo, hi] = currentMarkRange(marks, [5, 1])
    expect(marks[lo].visibleIndex).toBe(1)
    expect(marks[hi].visibleIndex).toBe(5)
  })

  it('returns an EMPTY range (never a crash, never index 0) for no marks or an unknown window', () => {
    expect(currentMarkRange([], [3])).toEqual([0, -1])
    // A coordinate the map does not index at all: the honest answer is "nothing is current".
    const [lo, hi] = currentMarkRange(marks, [99])
    expect(hi).toBeLessThan(lo)
    // `i >= lo && i <= hi` must be false for every real index — the reason the empty range is
    // spelled [0, -1] rather than null.
    for (let i = 0; i < marks.length; i++) expect(i >= lo && i <= hi).toBe(false)
  })
})
