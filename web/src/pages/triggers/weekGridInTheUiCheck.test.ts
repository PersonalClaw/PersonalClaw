import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The UI quality check keeps the week grid ──────────────────────────────────────────────────
//
// The browser run's home has every trigger switched off (`e2e/fixtures/home/`), so the real week
// projection is empty and `#/triggers?view=week` drew only "No fires this week". Its tree in the
// UI quality check (`e2e/surfaces.spec.ts`) was taken in that state, and the grid left the check
// without a red: its cells as controls, and the shaded cells whose contrast axe reads. The row now
// answers the projection with a fixed week (`STUBBED_READS` there). This holds the committed tree
// to that grid, so a rebaseline that loses it again (a stub that stopped matching the read, say)
// fails here instead of passing as a smaller page.

/** The committed tree: the pinned image's, the only set there is (`-linux`, #3943). */
const TREE = readFileSync(join(process.cwd(), 'e2e/__aria__/triggers-week-linux.aria.yml'), 'utf8')
/** The grid's cells that open a trigger, each named by `cellLabel`: `Mon, <month> # #:# — <its fires>`.
 *  The tree quotes a line whose name holds a `:` (`- 'button "…"': "#"`). */
const CELLS = TREE.split('\n').filter((l) => /^\s*- '?button "(Sun|Mon|Tue|Wed|Thu|Fri|Sat), <month> # #:# — /.test(l))

describe("the UI quality check's week view", () => {
  it('reads the committed tree of the week view (not vacuously green)', () => {
    expect(TREE).toMatch(/- radio "Week" \[checked\]/)
  })

  it('is the grid, not the empty week', () => {
    expect(TREE, 'the empty state is what the run draws with no fire to plot').not.toMatch(/No fires this week/)
    expect(TREE).toMatch(/- table "Scheduled trigger fires by day and hour/)
  })

  it('has a cell in each state the legend names', () => {
    // `<fires>` is the tree's fold of a cell's live fires (`normalizeTree`, e2e/surfaceChecks.ts).
    const states: Record<string, RegExp> = {
      'Will run': /— (<fires>|# fires?: )/,
      'Partly suppressed': /— # fires, # suppressed \(/,
      'Quiet hours': /all suppressed \(quiet hours /,
      'Skip date': /all suppressed \(skip date /,
    }
    for (const [state, label] of Object.entries(states)) {
      expect(CELLS.some((l) => label.test(l)), `a "${state}" cell among the grid's ${CELLS.length}`).toBe(true)
    }
  })
})
