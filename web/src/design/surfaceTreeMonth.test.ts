import { describe, expect, it } from 'vitest'
import { normalizeTree } from '../../e2e/surfaceChecks'

// ── The surface matrix's tree baseline does not hang on the month a run falls in ─────────────────
//
// Settings › devices prints a session's expiry, thirty days out, as `Nov 30, 09:15 PM`. Its digits
// and its half of the day were folded, but its month is a word, so a baseline taken in October
// read `session expires Nov #, …` and every run from Nov 1 read `Dec #, …`: the tree step went red
// on main with nothing changed.

describe("the matrix's accessibility tree folds a date's month", () => {
  it('reads an expiry at the end of one month and the start of the next as the same line', () => {
    const november = '  - text: Signed in 3m ago · session expires Nov 30, 09:15 PM'
    const december = '  - text: Signed in 3m ago · session expires Dec 1, 09:15 PM'
    expect(normalizeTree(december)).toBe(normalizeTree(november))
  })

  it('still names that a date is there', () => {
    expect(normalizeTree('  - text: session expires Dec 1, 09:15 PM')).toBe('  - text: session expires <month> #, #:# <am/pm>')
    expect(normalizeTree('  - text: Nov 30 – Dec 6 9 fires this week')).toBe('  - text: <month> # – <month> # # fires this week')
  })

  it('leaves the word in running text alone', () => {
    // Control: only a month a day's number follows is folded, not a sentence that opens with "May".
    expect(normalizeTree('  - paragraph: May I archive last year’s mail?')).toBe('  - paragraph: May I archive last year’s mail?')
  })
})
