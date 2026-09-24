import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearReadingPosition, getReadingPosition, readingPositions, setReadingPosition,
} from './readingPosition'

// ── The resume-point store (`KL-8`) ─────────────────────────────────────────────────────────
//
// A convenience cache, so every path through it has to degrade to "resume at the top" instead of
// throwing inside a render — including the ones a user can cause by hand (a corrupted value) and
// the one a long-lived library causes on its own (unbounded growth). The reader's end-to-end
// behaviour lives in readingResume.test.tsx; this file pins the store's own edges.

const KEY = 'knowledge-reading-positions'

beforeEach(() => localStorage.clear())

describe('a resume point round-trips', () => {
  it('keeps a mid-article fraction and hands it back', () => {
    setReadingPosition('a', 0.42)
    expect(getReadingPosition('a')?.pct).toBeCloseTo(0.42, 5)
    expect(getReadingPosition('a')!.ts).toBeGreaterThan(0)
  })

  it('refuses the two ends, because neither is a place to resume', () => {
    setReadingPosition('top', 0.001)
    setReadingPosition('done', 1)
    expect(getReadingPosition('top')).toBeNull()
    expect(getReadingPosition('done')).toBeNull()
  })

  it('DELETES an existing point when the reader returns to the top or finishes', () => {
    setReadingPosition('a', 0.5)
    setReadingPosition('a', 0.999)
    expect(getReadingPosition('a'), 'finishing clears the point rather than parking at the end').toBeNull()

    setReadingPosition('b', 0.5)
    setReadingPosition('b', 0)
    expect(getReadingPosition('b'), 'scrolling back to the top is not a resume point').toBeNull()
  })

  it('clears one item without touching its neighbours', () => {
    setReadingPosition('a', 0.3)
    setReadingPosition('b', 0.6)
    clearReadingPosition('a')
    expect(getReadingPosition('a')).toBeNull()
    expect(getReadingPosition('b')?.pct).toBeCloseTo(0.6, 5)
  })
})

describe('it degrades rather than throwing', () => {
  it('reads a corrupted store as "no saved positions"', () => {
    localStorage.setItem(KEY, '{not json at all')
    expect(readingPositions()).toEqual({})
    expect(getReadingPosition('a')).toBeNull()
    // And a write over the wreckage still works — the store self-heals on the next scroll.
    setReadingPosition('a', 0.5)
    expect(getReadingPosition('a')?.pct).toBeCloseTo(0.5, 5)
  })

  it('drops entries of the wrong shape instead of handing a shelf a NaN', () => {
    localStorage.setItem(KEY, JSON.stringify({
      good: { pct: 0.5, ts: 1 }, bad: { pct: 'half', ts: 1 }, worse: null, array: [1, 2],
    }))
    expect(Object.keys(readingPositions())).toEqual(['good'])
  })

  it('ignores a JSON array, which is valid JSON and not a position map', () => {
    localStorage.setItem(KEY, JSON.stringify([{ pct: 0.5, ts: 1 }]))
    expect(readingPositions()).toEqual({})
  })
})

describe('it stays bounded', () => {
  it('keeps the 200 most recently read and drops the rest ON DISK, not just in memory', () => {
    // 🪤 The trim has to happen on the WRITE. A map trimmed only when read grows forever in
    // localStorage, which is the failure this test exists to catch — so it asserts the stored
    // bytes, not the returned object.
    const many: Record<string, { pct: number; ts: number }> = {}
    for (let i = 0; i < 250; i++) many[`k${i}`] = { pct: 0.5, ts: i }
    localStorage.setItem(KEY, JSON.stringify(many))

    setReadingPosition('newest', 0.5)

    const stored = JSON.parse(localStorage.getItem(KEY)!) as Record<string, unknown>
    expect(Object.keys(stored)).toHaveLength(200)
    expect(stored.newest, 'the point just written must survive its own trim').toBeTruthy()
    expect(stored.k249, 'the most recent of the old entries stays').toBeTruthy()
    expect(stored.k0, 'the least recently read falls off first').toBeUndefined()
  })
})
