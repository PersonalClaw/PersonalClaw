import { describe, expect, it } from 'vitest'
import { currentMarkRange } from './sessionMapRegion'
import { sessionMapEntries } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-5 (the arithmetic half) — the current region as a contiguous entry range ─────────────
//
// `currentMarkRange` is the pure half of "viewport → on-screen colour": given the turn coordinates
// that are on screen, which ENTRIES (user messages) light coral. An entry owns every turn from its
// own message up to the next entry's, so an on-screen reply lights the question it answers. The DOM
// half (the IntersectionObserver that produces those coordinates) is `SessionMapRail.viewport.test.tsx`.
//
// VACUITY FLOOR — three ways a green here would mean nothing:
//
//  1. A BINARY SEARCH IS ONLY CORRECT ON SORTED INPUT, and this one searches `visibleIndex` over the
//     array `sessionMapEntries` produces. So the ordering is asserted against the REAL derivation.
//  2. "THE RANGE IS RIGHT" is satisfiable by a fixture whose answer is the whole array. Every case
//     asserts a strict, non-empty, non-total subset, and the search is checked against an
//     INDEPENDENT linear reference over every possible window.
//  3. THE EMPTY-SET CASE passes with zero new code (it is the on-load state), so it is asserted to be
//     DIFFERENT from every scrolled case, which is what makes the rest of the file load-bearing.

const TS = '2026-09-16T10:00:00.000Z'

/** Agent output first (turn 0 — an agent-initiated session), then `questions` exchanges: a question
 *  at an odd coordinate and a busy reply after it. So the entries start at 1, 3, 5, … and every
 *  entry owns two coordinates. */
function transcript(questions: number): ChatTurn[] {
  const subs: Segment[] = [
    { kind: 'tool', id: 't', tool: 'Terminal', detail: 'npm run build', done: true },
    { kind: 'error', text: 'ValidationException: input is too long.' },
  ]
  const out: ChatTurn[] = [{ role: 'assistant', ts: TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'Good morning.' }] }]
  for (let q = 0; q < questions; q++) {
    out.push({ role: 'user', ts: TS, visibleIndex: 2 * q + 1, segments: [{ kind: 'text', text: `prompt ${q}` }] })
    out.push({ role: 'assistant', ts: TS, visibleIndex: 2 * q + 2, segments: [{ kind: 'text', text: `reply ${q}` }, ...subs] })
  }
  return out
}

/** The answer, computed the slow obvious way — a different algorithm from the one under test: map
 *  every on-screen coordinate to its owner by scanning, then take the extremes. */
function referenceRange(starts: number[], visible: number[]): [number, number] {
  if (!starts.length) return [0, -1]
  if (!visible.length) return [starts.length - 1, starts.length - 1]
  const owners = visible
    .map((c) => { let owner = -1; starts.forEach((s, i) => { if (s <= c) owner = i }); return owner })
  const hi = Math.max(...owners)
  if (hi < 0) return [0, -1]
  return [Math.max(0, Math.min(...owners)), hi]
}

describe('the entry array the search assumes is actually sorted', () => {
  it('visibleIndex strictly increases across sessionMapEntries output', () => {
    const entries = sessionMapEntries(transcript(6))
    expect(entries).toHaveLength(6)
    for (let i = 1; i < entries.length; i++) {
      expect(entries[i].visibleIndex, `entry ${i} did not move forward — the binary search is invalid`)
        .toBeGreaterThan(entries[i - 1].visibleIndex)
    }
  })
})

describe('currentMarkRange — viewport coordinates to a contiguous entry slice', () => {
  const entries = sessionMapEntries(transcript(6))
  const starts = entries.map((e) => e.visibleIndex)
  const LAST_COORD = 12

  it('with NOTHING reported on screen, the newest message is current (the on-load state)', () => {
    const [lo, hi] = currentMarkRange(entries, [])
    expect([lo, hi]).toEqual([entries.length - 1, entries.length - 1])
    expect(lo).toBeGreaterThan(0)
  })

  it('a scrolled window lights the exchanges it shows, and it is not the on-load answer', () => {
    // Question 1 (coordinate 3) and its reply (4) and question 2 (5) are on screen.
    const [lo, hi] = currentMarkRange(entries, [3, 4, 5])
    expect([lo, hi]).toEqual([1, 2])
    expect([lo, hi]).not.toEqual(currentMarkRange(entries, []))
  })

  it('🔑 a reply on screen WITHOUT its question lights the question — and only that one', () => {
    // Deep in the answer to question 3: only coordinate 8 is on screen.
    expect(currentMarkRange(entries, [8])).toEqual([3, 3])
    // The same holds at either end of the transcript.
    expect(currentMarkRange(entries, [2])).toEqual([0, 0])
    expect(currentMarkRange(entries, [LAST_COORD])).toEqual([5, 5])
  })

  it('agrees with an independent linear scan for EVERY window over the transcript', () => {
    let windows = 0
    for (let a = 0; a <= LAST_COORD; a++) {
      for (let b = a; b <= LAST_COORD; b++) {
        const visible = Array.from({ length: b - a + 1 }, (_, k) => a + k)
        expect(currentMarkRange(entries, visible), `window ${a}..${b}`).toEqual(referenceRange(starts, visible))
        windows++
      }
    }
    // A loop that ran zero times would pass silently.
    expect(windows).toBe(91)
  })

  it('a non-contiguous visible set spans its extremes — the region is a range, not a set', () => {
    // Cannot happen for a single scroll container, but the contract has to be total.
    expect(currentMarkRange(entries, [10, 2])).toEqual([0, 4])
  })

  it('agent output from BEFORE the first question owns nothing', () => {
    // Coordinate 0 is the agent's opening line: no user message precedes it, so no entry is lit.
    const [lo, hi] = currentMarkRange(entries, [0])
    expect(hi).toBeLessThan(lo)
    for (let i = 0; i < entries.length; i++) expect(i >= lo && i <= hi).toBe(false)
    // …and once the first question joins it on screen, that question is lit.
    expect(currentMarkRange(entries, [0, 1])).toEqual([0, 0])
  })

  it('returns an EMPTY range (never a crash, never index 0) for a map with no entries', () => {
    expect(currentMarkRange([], [3])).toEqual([0, -1])
    expect(currentMarkRange([], [])).toEqual([0, -1])
  })
})
