import { describe, it, expect } from 'vitest'
import { packRows, columnsFor } from './packRows'

// The Settings overview is a grid of row bands: each group's cards flow in source order, and a card
// spans one or two columns. Rows must end even at every width (#5897): the owner's complaint was
// ragged column bottoms, and a row that stops short of the right edge is the same raggedness one
// row at a time. `packRows` is the one place a span is decided, so it is pinned here as a pure
// function, over every set of cards a search can leave on screen.

/** The rows `spans` lay out into at `columns`, in order. */
function rows(spans: number[], columns: number): number[][] {
  const out: number[][] = []
  let row: number[] = []
  let used = 0
  for (const s of spans) {
    if (used + s > columns) { out.push(row); row = []; used = 0 }
    row.push(s)
    used += s
    if (used === columns) { out.push(row); row = []; used = 0 }
  }
  if (row.length) out.push(row)
  return out
}

/** Every subset of `xs`, order kept (a search hides cards; it never reorders them). */
function subsets<T>(xs: T[]): T[][] {
  const out: T[][] = []
  for (let mask = 0; mask < 1 << xs.length; mask++) out.push(xs.filter((_, i) => mask & (1 << i)))
  return out
}

describe('packRows: every row of a group ends at the grid edge', () => {
  const groups = [
    [2, 1, 1, 1, 1],
    [2, 1, 1, 2, 2, 1],
    [1, 1, 1, 1, 1, 1],
    [2, 2, 2],
    [1],
    [2],
    [1, 2, 1, 2, 1, 1, 2],
  ]

  for (const columns of [1, 2, 3]) {
    it(`at ${columns} column${columns === 1 ? '' : 's'}, over every subset a search can leave`, () => {
      let checked = 0
      for (const group of groups) {
        for (const visible of subsets(group)) {
          if (!visible.length) continue
          const packed = packRows(visible, columns)
          expect(packed.length, 'one span per card, none dropped or added').toBe(visible.length)
          packed.forEach((s, i) => {
            expect(s, 'a span never passes the column count').toBeLessThanOrEqual(columns)
            expect(s, 'a card never narrows below what it asked for (capped at the columns)').toBeGreaterThanOrEqual(Math.min(visible[i], columns))
          })
          for (const row of rows(packed, columns)) {
            expect(row.reduce((a, b) => a + b, 0), `a row of ${JSON.stringify(packed)} stops short`).toBe(columns)
          }
          checked++
        }
      }
      // Vacuity: the subsets above number in the hundreds; a loop that ran over nothing proves nothing.
      expect(checked).toBeGreaterThan(150)
    })
  }

  it('widens only where a row would stop short, and leaves a row that fills alone', () => {
    expect(packRows([2, 1, 1, 1, 1], 3)).toEqual([2, 1, 1, 1, 1])
    expect(packRows([2, 1, 1, 1, 1], 2)).toEqual([2, 1, 1, 1, 1])
    // A two-span card after a lone one-span card: the one-span card takes its row.
    expect(packRows([1, 2], 2)).toEqual([2, 2])
    // The last row of a group: its last card fills the rest.
    expect(packRows([1, 1, 1, 1], 3)).toEqual([1, 1, 1, 3])
    expect(packRows([2, 1, 1], 2)).toEqual([2, 1, 1])
    expect(packRows([2, 1, 1, 1], 2)).toEqual([2, 1, 1, 2])
    // One column: everything spans one.
    expect(packRows([2, 1, 2], 1)).toEqual([1, 1, 1])
    expect(packRows([], 3)).toEqual([])
  })
})

describe('columnsFor: the column count follows the space the grid has, not the device', () => {
  it('is 1 under 640 px, 2 under 960 px, and 3 from there', () => {
    expect(columnsFor(0)).toBe(1)
    expect(columnsFor(342)).toBe(1)
    expect(columnsFor(639)).toBe(1)
    expect(columnsFor(640)).toBe(2)
    expect(columnsFor(766)).toBe(2)
    expect(columnsFor(959)).toBe(2)
    expect(columnsFor(960)).toBe(3)
    expect(columnsFor(1200)).toBe(3)
    expect(columnsFor(2400)).toBe(3)
  })
})
