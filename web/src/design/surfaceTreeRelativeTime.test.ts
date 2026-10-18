import { describe, expect, it } from 'vitest'
import { normalizeTree } from '../../e2e/surfaceChecks'

// ── The surface matrix's tree baseline does not hang on how long ago a job ran ───────────────────
//
// Settings › durability prints each backup job's last run as "just now" for its first 90 seconds and
// as "# min ago" after that (`relativeTime` in `DurabilityPanel.tsx`). The tree folded "just now" and
// the short forms ("#m ago") to `<when>`, but not "# min ago", so the page read one line or the other
// depending on how far into the run it was visited, and the tree step failed on a change nothing made.

describe("the matrix's accessibility tree folds a relative time in each spelling the app prints", () => {
  it('reads a job that ran just now and one that ran minutes ago as the same line', () => {
    const early = '  - text: "Last run of each job: Incremental export just now Nightly snapshot just now"'
    const later = '  - text: "Last run of each job: Incremental export 3 min ago Nightly snapshot 12 min ago"'
    expect(normalizeTree(later)).toBe(normalizeTree(early))
  })

  it.each(['just now', '3m ago', '3 min ago', '1 minute ago', '1 hour ago', '4 days ago', '2 months ago'])(
    'reads "%s" as <when>',
    (when) => {
      expect(normalizeTree(`  - text: "Restore drill ${when} Run now:"`)).toBe('  - text: "Restore drill <when> Run now:"')
    },
  )

  it('leaves a length of time that is not a time ago alone', () => {
    // Control: only a time ago folds, not every number of minutes the page names.
    expect(normalizeTree('  - text: Keeps 3 min of history')).toBe('  - text: Keeps # min of history')
  })
})
