import { describe, it, expect } from 'vitest'
import { readingMinutes, readingTimeLabel } from './readingTime'

// KL-16 clause 6: the reading-time estimate is ONE shared helper, so the list row, the item
// metadata and the reader cannot drift to three different numbers for the same body. These lock
// the contract each of those three call sites relies on.
describe('readingMinutes', () => {
  it('estimates whole minutes at the editorial 220 wpm', () => {
    // 440 / 220 = 2 — the value readingView.test.tsx asserts the reader renders.
    expect(readingMinutes(440)).toBe(2)
    expect(readingMinutes(660)).toBe(3)
  })

  it('rounds to the nearest minute', () => {
    expect(readingMinutes(330)).toBe(2) // 1.5 → 2
    expect(readingMinutes(300)).toBe(1) // 1.36 → 1
  })

  it('floors a non-empty body at 1 minute, never "0 min"', () => {
    // A short note still takes a moment; "0 min read" would read as "nothing here".
    expect(readingMinutes(1)).toBe(1)
    expect(readingMinutes(10)).toBe(1) // Math.round(0.045) is 0, floored to 1
  })

  it('is 0 — not 1 — when there is no count to estimate from', () => {
    expect(readingMinutes(0)).toBe(0)
    expect(readingMinutes(undefined)).toBe(0)
    expect(readingMinutes(null)).toBe(0)
  })
})

describe('readingTimeLabel', () => {
  it('is a standalone, self-describing label for a metadata strip', () => {
    expect(readingTimeLabel(440)).toBe('2 min read')
    expect(readingTimeLabel(1)).toBe('1 min read')
  })

  it('is empty when there is no estimate, so a caller drops the element entirely', () => {
    // Not "0 min read": the empty string lets the list row / metadata omit the span.
    expect(readingTimeLabel(0)).toBe('')
    expect(readingTimeLabel(undefined)).toBe('')
    expect(readingTimeLabel(null)).toBe('')
  })
})
