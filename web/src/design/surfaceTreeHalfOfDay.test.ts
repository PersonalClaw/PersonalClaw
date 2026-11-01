import { describe, expect, it } from 'vitest'
import { normalizeTree } from '../../e2e/surfaceChecks'

// ── The surface matrix's tree baseline does not hang on the hour a run starts ────────────────────
//
// A clock time the page prints (`toLocaleString` with `hour: '2-digit'`, as Settings › devices does
// for a session's expiry) has its digits folded by `normalizeTree`, but its half of the day is a
// word, and it flips with the hour the run happens to start. The same page then read `… #:# AM` on
// one run and `… #:# PM` on the next, and the tree step failed on a change nothing made.

describe("the matrix's accessibility tree folds a clock time's half of the day", () => {
  it('reads a morning and an evening expiry as the same line', () => {
    const am = '  - text: Signed in 3m ago · session expires Nov 9, 09:15 AM'
    const pm = '  - text: Signed in 3m ago · session expires Nov 9, 09:15 PM'
    expect(normalizeTree(am)).toBe(normalizeTree(pm))
  })

  it('still names that a time is there', () => {
    expect(normalizeTree('  - text: session expires Nov 9, 09:15 PM')).toBe('  - text: session expires <month> #, #:# <am/pm>')
  })

  it('leaves the word in running text alone', () => {
    // Control: only a half of day that follows a clock time is folded, not every capital AM or PM.
    expect(normalizeTree('  - text: PM notes from the AM standup')).toBe('  - text: PM notes from the AM standup')
  })
})
